"""Offline browser error checks. Run python -m evaluation.replay on port 8765.
No provider is constructed. Submit 'provider failure' or 'validation error'.
Other input replays a recorded local report with an explicit replay note.
"""
import asyncio
import json
import os
from pathlib import Path
os.environ['CORS_ORIGINS'] = 'http://127.0.0.1:5174'
from fastapi import HTTPException
import main
from schemas import Report
from services.providers import ProviderFailure

class OfflineProvider:
    async def close(self):
        pass

async def replay(text, provider):
    await asyncio.sleep(0.5)
    if text == 'provider failure':
        raise ProviderFailure('Offline test: simulated provider failure. No API request was made.')
    if text == 'validation error':
        raise HTTPException(422, 'Offline test: simulated validation error. No API request was made.')
    files = sorted(Path(__file__).with_name('results').glob('live-*.json'))
    if not files:
        raise ProviderFailure('No local report exists to replay.')
    report = Report.model_validate(json.loads(files[-1].read_text())['response'])
    report.note = 'OFFLINE REPLAY of a saved report; no fresh research was performed.'
    return report

if __name__ == '__main__':
    import uvicorn
    os.environ['ENABLE_LIVE_RESEARCH'] = 'true'
    main.Providers = OfflineProvider
    main.run_pipeline = replay
    uvicorn.run(main.app, host='127.0.0.1', port=8765)
