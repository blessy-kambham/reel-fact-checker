"""Local fact-checking API. Live calls are explicitly opt-in."""
import asyncio
import os
from pathlib import Path
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from schemas import ClaimRequest, Report
from services.demo import demo_report
from services.pipeline import run_pipeline
from services.providers import Providers, ProviderFailure, missing_settings

load_dotenv(Path(__file__).with_name('.env'))

@asynccontextmanager
async def lifespan(app):
    app.state.run_lock = asyncio.Lock()
    yield

app = FastAPI(title='Reel Fact-Checker', version='0.2.0', lifespan=lifespan)
app.add_middleware(CORSMiddleware,
    allow_origins=[v.strip() for v in os.getenv('CORS_ORIGINS', 'http://localhost:5173,http://127.0.0.1:5173').split(',') if v.strip()],
    allow_methods=['GET', 'POST'], allow_headers=['Content-Type'])

@app.get('/health')
def health():
    return {'status': 'ok'}

@app.get('/config')
def config():
    missing = missing_settings()
    enabled = os.getenv('ENABLE_LIVE_RESEARCH', '').lower() == 'true'
    return {'live_ready': enabled and not missing, 'live_enabled': enabled,
            'missing_settings': missing, 'max_claims': 3,
            'message': 'Live research is ready.' if enabled and not missing else 'Free demo is available. Live research is not configured or enabled.'}

@app.get('/demo', response_model=Report)
def demo():
    return demo_report()

@app.post('/fact-check', response_model=Report)
async def fact_check(request: ClaimRequest):
    if not config()['live_ready']:
        raise HTTPException(503, 'Live research is off. Try the free demo. Configure backend/.env when you are ready for provider setup.')
    if app.state.run_lock.locked():
        raise HTTPException(429, 'Another report is running. Try again when it finishes.')
    async with app.state.run_lock:
        provider = Providers()
        try:
            return await asyncio.wait_for(run_pipeline(request.claim, provider), timeout=330)
        except asyncio.TimeoutError:
            raise HTTPException(504, 'The report timed out. Try a shorter, more specific claim.')
        except ProviderFailure as exc:
            raise HTTPException(502, str(exc))
        finally:
            await provider.close()
