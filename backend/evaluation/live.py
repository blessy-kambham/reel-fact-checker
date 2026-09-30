"""Local validation runner for the real research routes (/fact-check and /fact-check-article).

  python -m evaluation.live
      Offline preparation (the default): fingerprints the exact code, copies it beside the traces,
      reports configuration by name only and lists allowances. Makes no network or paid calls.

  python -m evaluation.live --allow-paid --allowance NAME --max-usd 0.10 --max-searches 8
      Starts the paid validation server under a named allowance. An allowance is tied to the code
      fingerprint it was opened with, persists across restarts, can never be raised, and is refused
      if the code has changed. A new approval needs a new NAME; old ledgers are never reset.

Generated traces stay local. Never enable this server on a public interface.
"""
import argparse
import asyncio
import json
import os
import re
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from openai import AsyncOpenAI
from evaluation import snapshot
from services.budget import Budget
from services.providers import Providers, ProviderFailure

RESULTS = Path(__file__).with_name('results')
ALLOWANCES = 'allowances'
# Sanity ceilings for a single approval; the approved values are passed explicitly and usually far lower.
MAX_USD_CEILING = Decimal('1.00')
MAX_SEARCH_CEILING = 50
VALIDATION_MODEL = 'gpt-4.1-mini'
# Every route that runs paid research must be traced; a test checks this against the app's routes.
LIVE_PATHS = frozenset({'/fact-check', '/fact-check-article', '/fact-check-video'})

class AuditProvider(Providers):
    def __init__(self, budget, trace):
        self.client = AsyncOpenAI(api_key=os.environ['OPENAI_API_KEY'], timeout=40, max_retries=0)
        self.usage = dict(input_tokens=0, output_tokens=0, model_calls=0, search_calls=0)
        self.budget, self.trace = budget, trace
        self.model_lock = asyncio.Lock()

    async def structured(self, schema, instructions, data):
        # Serialize validation model calls so usage deltas cannot overlap.
        async with self.model_lock:
            return await self._structured(schema, instructions, data)

    async def _structured(self, schema, instructions, data):
        try:
            reservation = self.budget.reserve(instructions, data, schema)
        except ProviderFailure:
            self.trace.setdefault('blocked', []).append({'stage': schema.__name__, 'reason': 'budget_stopped'})
            raise
        before = dict(self.usage)
        record = {'stage': schema.__name__, 'input': json.loads(data) if schema.__name__ != 'Extraction' else data}
        self.trace['model'].append(record)
        try:
            result = await super().structured(schema, instructions, data)
            record['output'] = result.model_dump()
            self.budget.reconcile(reservation, self.usage['input_tokens'] - before['input_tokens'],
                                  self.usage['output_tokens'] - before['output_tokens'])
            return result
        except ProviderFailure:
            record['error'] = 'model_request_failed'
            raise

    async def read_images(self, schema, instructions, images):
        from services.budget import IMAGE_TOKENS
        async with self.model_lock:
            try:
                reservation = self.budget.reserve(instructions, '', schema, extra_input_tokens=IMAGE_TOKENS * len(images))
            except ProviderFailure:
                self.trace.setdefault('blocked', []).append({'stage': schema.__name__, 'reason': 'budget_stopped'})
                raise
            before = dict(self.usage)
            record = {'stage': schema.__name__, 'input': {'images': len(images)}}
            self.trace['model'].append(record)
            try:
                result = await super().read_images(schema, instructions, images)
                record['output'] = result.model_dump()
                self.budget.reconcile(reservation, self.usage['input_tokens'] - before['input_tokens'],
                                      self.usage['output_tokens'] - before['output_tokens'])
                return result
            except ProviderFailure:
                record['error'] = 'model_request_failed'
                raise

    async def search(self, query):
        try:
            self.budget.reserve_search()
        except ProviderFailure:
            self.trace.setdefault('blocked', []).append({'stage': 'search', 'reason': 'budget_stopped'})
            raise
        record = {'query': query}
        self.trace['searches'].append(record)
        try:
            hits = await super().search(query)
            record['urls'] = [hit['url'] for hit in hits]
            return hits
        except ProviderFailure:
            record['error'] = 'search_request_failed'
            raise


def open_allowance(name, fingerprint, max_usd, max_searches, results=RESULTS):
    """Open or resume a named allowance. Refuses changed code; never raises a persisted limit."""
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,39}', name or ''):
        raise ValueError('Allowance names use 1-40 lowercase letters, digits and hyphens.')
    max_usd = Decimal(str(max_usd))
    if not Decimal('0') < max_usd <= MAX_USD_CEILING:
        raise ValueError(f'--max-usd must be above 0 and at most {MAX_USD_CEILING}.')
    if not 0 <= max_searches <= MAX_SEARCH_CEILING:
        raise ValueError(f'--max-searches must be between 0 and {MAX_SEARCH_CEILING}.')
    ledger = results / ALLOWANCES / f'{name}.json'
    existed = ledger.exists()
    budget = Budget(ledger, search_limit=max_searches, max_usd=max_usd)
    if existed:
        opened_with = budget.metadata.get('code_fingerprint')
        if opened_with != fingerprint:
            raise RuntimeError(f'Allowance {name!r} was opened for different code. It will not be reused; '
                               'ask for a new approval and pass a new --allowance name.')
    else:
        budget.metadata = {'name': name, 'code_fingerprint': fingerprint,
                           'opened_at': datetime.now(timezone.utc).isoformat(),
                           'approved_max_usd': str(max_usd), 'approved_max_searches': max_searches}
        budget.save()
    return budget


def allowance_status(results=RESULTS):
    rows = []
    for path in sorted((results / ALLOWANCES).glob('*.json')):
        state = json.loads(path.read_text())
        rows.append({'name': path.stem, 'code_fingerprint': state.get('metadata', {}).get('code_fingerprint', '')[:16],
                     'reserved_usd': state['reserved'], 'max_usd': state.get('max_usd'),
                     'searches': state['searches'], 'max_searches': state.get('search_limit'), 'stopped': state.get('stopped', False)})
    legacy = results / 'budget.json'
    if legacy.exists():
        state = json.loads(legacy.read_text())
        rows.append({'name': '(legacy budget.json, not used)', 'code_fingerprint': None, 'reserved_usd': state['reserved'],
                     'max_usd': '0.10', 'searches': state['searches'], 'max_searches': state.get('search_limit'), 'stopped': state.get('stopped', False)})
    return rows


def prepare(results=RESULTS, root=snapshot.ROOT):
    """Offline preparation: no provider objects, network requests or paid calls."""
    import main  # Loads the ignored .env into this process only; values are never reported.
    info = snapshot.capture(results, root)
    model = os.environ.get('OPENAI_MODEL', '')
    return {'code_fingerprint': info['fingerprint'], 'git_head': info['git_head'], 'file_count': info['file_count'],
            'uncommitted_files': info['uncommitted_files'], 'untracked_files': info['untracked_files'],
            'snapshot_dir': info['snapshot_dir'],
            'missing_settings': main.missing_settings(),
            'model_supported': model == VALIDATION_MODEL,
            'allowances': allowance_status(results),
            'calls_per_claim': 'Per claim: at most 2 searches and 14 model calls (1 analysis, 2 per selected citation '
                               'for relation and attribution with up to 6 citations, 1 verdict). Per report: 1-2 more '
                               'model calls for extraction and coverage.'}


def create_app(allowance, max_usd, max_searches):
    os.environ['CORS_ORIGINS'] = 'http://127.0.0.1:5174'
    import main
    if os.environ.get('OPENAI_MODEL') != VALIDATION_MODEL:
        raise RuntimeError(f'Validation pricing is only configured for {VALIDATION_MODEL}.')
    if main.missing_settings():
        raise RuntimeError('Required provider settings are missing.')
    # Snapshot and allowance are settled before any provider exists.
    code = snapshot.capture(RESULTS)
    budget = open_allowance(allowance, code['fingerprint'], max_usd, max_searches)
    if budget.stopped:
        raise RuntimeError(f'Allowance {allowance!r} is stopped. Ask for a new approval and use a new name.')
    os.environ['ENABLE_LIVE_RESEARCH'] = 'true'  # process only; .env stays unchanged
    active = {'trace': None}
    main.Providers = lambda: AuditProvider(budget, active['trace'])

    @main.app.middleware('http')
    async def capture(request, call_next):
        if request.url.path not in LIVE_PATHS or request.method != 'POST':
            return await call_next(request)
        if active['trace'] is not None:
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=429, content={'detail': 'Validation request already running.'})
        if request.url.path == '/fact-check-video':
            payload = {'claim': 'video upload'}  # Multipart body: never parsed or stored by the trace.
        else:
            try:
                payload = await request.json()
            except (ValueError, UnicodeDecodeError):
                return await call_next(request)
            if not isinstance(payload, dict):
                return await call_next(request)
        trace = {'submitted_text': payload.get('claim') or payload.get('url'), 'route': request.url.path, 'allowance': allowance, 'code_fingerprint': code['fingerprint'],
                 'git_head': code['git_head'], 'snapshot_dir': code['snapshot_dir'],
                 'model': [], 'searches': [], 'confidence': None,
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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--allow-paid', action='store_true', help='Start the paid validation server.')
    parser.add_argument('--allowance', help='Name of a newly approved allowance, or one being resumed for identical code.')
    parser.add_argument('--max-usd', type=Decimal, help='Approved model spending cap for this allowance.')
    parser.add_argument('--max-searches', type=int, help='Approved Tavily search cap for this allowance.')
    args = parser.parse_args(argv)
    if not args.allow_paid:
        print(json.dumps(prepare(), indent=2))
        print('Offline preparation only; no network or paid calls were made.')
        return
    if not args.allowance or args.max_usd is None or args.max_searches is None:
        parser.error('--allow-paid requires --allowance, --max-usd and --max-searches from an explicit approval.')
    import uvicorn
    uvicorn.run(create_app(args.allowance, args.max_usd, args.max_searches), host='127.0.0.1', port=8765)

if __name__ == '__main__':
    main()
