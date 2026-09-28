"""Opt-in local validation server: python -m evaluation.live --allow-paid.
Uses the real /fact-check route with a shared conservative $0.10 model allowance.
Generated traces stay local. Never enable this server on a public interface.
"""
import argparse
import json
import os
import time
from decimal import Decimal
from pathlib import Path

from openai import AsyncOpenAI
from services.providers import Providers, ProviderFailure

RESULTS = Path(__file__).with_name('results')

class Budget:
    def __init__(self, ledger=None):
        self.ledger = ledger
        self.reserved = Decimal('0')
        self.searches = 0
        if ledger and ledger.exists():
            state = json.loads(ledger.read_text())
            self.reserved = Decimal(state['reserved'])
            self.searches = state['searches']

    def save(self):
        if self.ledger:
            self.ledger.parent.mkdir(exist_ok=True)
            temp = self.ledger.with_suffix('.tmp')
            temp.write_text(json.dumps({'reserved':str(self.reserved),'searches':self.searches}))
            temp.replace(self.ledger)

    def reserve(self, instructions, data, schema):
        # Conservative byte bound plus protocol overhead. Failed calls retain their reservation.
        tokens = len(json.dumps([instructions, data, schema.model_json_schema()], ensure_ascii=True).encode()) + 10000
        cost = Decimal(tokens) * Decimal('0.0000004') + Decimal(3000) * Decimal('0.0000016')
        if self.reserved + cost > Decimal('0.10'):
            raise ProviderFailure('Validation budget exhausted; no further model call was made.')
        self.reserved += cost
        self.save()
        return cost

class AuditProvider(Providers):
    def __init__(self, budget, trace):
        self.client = AsyncOpenAI(api_key=os.environ['OPENAI_API_KEY'], timeout=40, max_retries=0)
        self.usage = dict(input_tokens=0, output_tokens=0, model_calls=0, search_calls=0)
        self.budget, self.trace = budget, trace

    async def structured(self, schema, instructions, data):
        reservation = self.budget.reserve(instructions, data, schema)
        before = dict(self.usage)
        record = {'stage': schema.__name__, 'input': json.loads(data) if schema.__name__ != 'Extraction' else data}
        self.trace['model'].append(record)
        try:
            result = await super().structured(schema, instructions, data)
            record['output'] = result.model_dump()
            actual = Decimal(self.usage['input_tokens'] - before['input_tokens']) * Decimal('0.0000004') + Decimal(self.usage['output_tokens'] - before['output_tokens']) * Decimal('0.0000016')
            if actual > 0:
                self.budget.reserved += actual - reservation
                self.budget.save()
            return result
        except ProviderFailure:
            record['error'] = 'model_request_failed'
            raise

    async def search(self, query):
        if self.budget.searches >= 12:
            raise ProviderFailure('Validation search limit reached; no search was made.')
        self.budget.searches += 1
        self.budget.save()
        record = {'query': query}
        self.trace['searches'].append(record)
        try:
            hits = await super().search(query)
            record['urls'] = [hit['url'] for hit in hits]
            return hits
        except ProviderFailure:
            record['error'] = 'search_request_failed'
            raise


def create_app():
    os.environ['CORS_ORIGINS'] = 'http://127.0.0.1:5174'
    import main
    if os.environ.get('OPENAI_MODEL') != 'gpt-4.1-mini':
        raise RuntimeError('Validation pricing is only configured for gpt-4.1-mini.')
    if main.missing_settings():
        raise RuntimeError('Required provider settings are missing.')
    os.environ['ENABLE_LIVE_RESEARCH'] = 'true'  # process only; .env stays unchanged
    budget = Budget(RESULTS / 'budget.json')
    # Resume the same local allowance across server restarts; never silently reset it.
    prior = sorted(RESULTS.glob('live-*.json'))
    if prior and not budget.ledger.exists():
        previous = json.loads(prior[-1].read_text())
        budget.reserved = Decimal(str(previous['reserved_openai_usd']))
        budget.searches = previous['total_tavily_calls']
        budget.save()
    active = {'trace': None}
    main.Providers = lambda: AuditProvider(budget, active['trace'])

    @main.app.middleware('http')
    async def capture(request, call_next):
        if request.url.path != '/fact-check' or request.method != 'POST':
            return await call_next(request)
        if active['trace'] is not None:
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=429, content={'detail': 'Validation request already running.'})
        try:
            payload = await request.json()
        except (ValueError, UnicodeDecodeError):
            return await call_next(request)
        if not isinstance(payload, dict):
            return await call_next(request)
        trace = {'submitted_text': payload.get('claim'), 'model': [], 'searches': [], 'confidence': None,
                 'confidence_note': 'Not calibrated; no percentage generated.'}
        active['trace'] = trace
        start = time.monotonic()
        try:
            response = await call_next(request)
            body = b''.join([part async for part in response.body_iterator])
            trace['http_status'] = response.status_code
            trace['response'] = json.loads(body)
            from starlette.responses import Response
            return Response(body, status_code=response.status_code, headers=dict(response.headers), media_type=response.media_type)
        finally:
            trace['latency_seconds'] = round(time.monotonic() - start, 3)
            trace['reserved_openai_usd'] = float(budget.reserved)
            trace['total_tavily_calls'] = budget.searches
            RESULTS.mkdir(exist_ok=True)
            (RESULTS / f'live-{time.time_ns()}.json').write_text(json.dumps(trace, indent=2))
            active['trace'] = None
    return main.app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-paid', action='store_true')
    args = parser.parse_args()
    if not args.allow_paid:
        parser.error('--allow-paid is required; maximum reserved model cost $0.10 and 12 Tavily calls per persisted local allowance.')
    import uvicorn
    uvicorn.run(create_app(), host='127.0.0.1', port=8765)

if __name__ == '__main__':
    main()
