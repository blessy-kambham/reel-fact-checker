"""Persistent spending guard shared by the app and the validation runner.

Every model call reserves a conservative cost before it is made; successful calls reconcile the
reservation to actual token usage, failed calls keep it. A refusal stops the ledger for good.
"""
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from services.providers import ProviderFailure

# USD per token (input, output). Reservations are only meaningful for models listed here.
PRICES = {'gpt-4.1-mini': (Decimal('0.0000004'), Decimal('0.0000016'))}
MAX_OUTPUT_TOKENS = 3000
BYTES_PER_TOKEN = 3
OVERHEAD_TOKENS = 2000


class BudgetExceeded(ProviderFailure):
    """A spending or search limit refused a call before it was made."""


class Budget:
    def __init__(self, ledger=None, search_limit=12, max_usd=Decimal('0.10'), model='gpt-4.1-mini'):
        self.ledger = ledger
        self.reserved = Decimal('0')
        self.searches = 0
        self.search_limit = search_limit
        self.max_usd = Decimal(str(max_usd))
        self.stopped = False
        self.metadata = {}
        self.input_price, self.output_price = PRICES[model]
        if ledger and ledger.exists():
            state = json.loads(ledger.read_text())
            self.reserved = Decimal(state['reserved'])
            self.searches = state['searches']
            # Limits persisted in a ledger can only be lowered, never raised.
            self.search_limit = min(search_limit, state.get('search_limit', search_limit))
            self.max_usd = min(self.max_usd, Decimal(state.get('max_usd', str(self.max_usd))))
            self.stopped = state.get('stopped', False)
            self.metadata = state.get('metadata', {})

    def save(self):
        if self.ledger:
            self.ledger.parent.mkdir(parents=True, exist_ok=True)
            temp = self.ledger.with_suffix('.tmp')
            temp.write_text(json.dumps({'reserved':str(self.reserved),'searches':self.searches,'search_limit':self.search_limit,
                                        'max_usd':str(self.max_usd),'stopped':self.stopped,'metadata':self.metadata}))
            temp.replace(self.ledger)

    def ensure_active(self):
        if self.stopped:
            raise BudgetExceeded('Spending budget stopped; no further external call was made.')

    def stop(self):
        self.stopped = True
        self.save()

    def reserve_search(self):
        self.ensure_active()
        if self.searches >= self.search_limit:
            self.stop()
            raise BudgetExceeded('Search limit reached; no search was made.')
        self.searches += 1
        self.save()

    def reserve(self, instructions, data, schema):
        self.ensure_active()
        # About one token per three bytes of ASCII-escaped JSON (English averages about four), plus
        # protocol overhead. Conservative, not exact. Failed calls retain their reservation.
        size = len(json.dumps([instructions, data, schema.model_json_schema()], ensure_ascii=True).encode())
        tokens = -(-size // BYTES_PER_TOKEN) + OVERHEAD_TOKENS
        cost = Decimal(tokens) * self.input_price + Decimal(MAX_OUTPUT_TOKENS) * self.output_price
        if self.reserved + cost > self.max_usd:
            self.stop()
            raise BudgetExceeded('Spending budget exhausted; no further model call was made.')
        self.reserved += cost
        self.save()
        return cost

    def reconcile(self, reservation, input_tokens, output_tokens):
        actual = Decimal(input_tokens) * self.input_price + Decimal(output_tokens) * self.output_price
        if actual > 0:
            self.reserved += actual - reservation
            self.save()
        return actual

    def status(self):
        return {'spent_usd': float(self.reserved), 'limit_usd': float(self.max_usd),
                'searches': self.searches, 'search_limit': self.search_limit, 'stopped': self.stopped}


def daily_budget(directory: Path, max_usd, max_searches, model, now=None):
    """The app's ledger for the current UTC day. A new day starts a new ledger; a day's limits never rise."""
    day = (now or datetime.now(timezone.utc)).strftime('%Y-%m-%d')
    return Budget(Path(directory) / f'usage-{day}.json', search_limit=max_searches, max_usd=max_usd, model=model)
