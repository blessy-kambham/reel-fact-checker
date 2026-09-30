"""Local fact-checking API. Live calls are explicitly opt-in."""
import asyncio
import os
import sqlite3
from uuid import UUID
from pathlib import Path
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from schemas import ArticleRequest, ClaimRequest, Report
from decimal import Decimal, InvalidOperation
from services.budget import PRICES, BudgetExceeded, daily_budget
from services.article import ArticleUnavailable, run_article_pipeline
from services.demo import demo_report
from services.history import History
from services.pipeline import run_pipeline
from services.providers import Providers, ProviderFailure, missing_settings

load_dotenv(Path(__file__).with_name('.env'))
DATA_DIR = Path(__file__).with_name('data')
DEFAULT_DAILY_USD, DEFAULT_DAILY_SEARCHES = '0.50', 40


def spending_limits():
    """(max USD, max searches) per UTC day, or None when the configuration is invalid."""
    try:
        usd = Decimal(os.getenv('DAILY_BUDGET_USD', '').strip() or DEFAULT_DAILY_USD)
        searches = int(os.getenv('DAILY_SEARCH_LIMIT', '').strip() or DEFAULT_DAILY_SEARCHES)
    except (InvalidOperation, ValueError):
        return None
    return (usd, searches) if usd > 0 and searches >= 0 else None


def history():
    return History(DATA_DIR / 'history.sqlite3')


def todays_budget():
    usd, searches = spending_limits()
    return daily_budget(DATA_DIR, usd, searches, os.environ['OPENAI_MODEL'])

@asynccontextmanager
async def lifespan(app):
    app.state.run_lock = asyncio.Lock()
    yield

app = FastAPI(title='Reel Fact-Checker', version='0.2.0', lifespan=lifespan)
app.add_middleware(CORSMiddleware,
    allow_origins=[v.strip() for v in os.getenv('CORS_ORIGINS', 'http://localhost:5173,http://127.0.0.1:5173').split(',') if v.strip()],
    allow_methods=['GET', 'POST', 'DELETE'], allow_headers=['Content-Type'])

@app.get('/health')
def health():
    return {'status': 'ok'}

@app.get('/config')
def config():
    missing = missing_settings()
    enabled = os.getenv('ENABLE_LIVE_RESEARCH', '').lower() == 'true'
    ready, message, spending = enabled and not missing, 'Live research is ready.', None
    if not ready:
        message = 'Free demo is available. Live research is not configured or enabled.'
    elif os.environ['OPENAI_MODEL'] not in PRICES:
        ready, message = False, f'Spending limits are only priced for: {", ".join(PRICES)}. Set OPENAI_MODEL to one of them.'
    elif spending_limits() is None:
        ready, message = False, 'DAILY_BUDGET_USD and DAILY_SEARCH_LIMIT must be a positive amount and a whole number.'
    else:
        spending = todays_budget().status()  # Numbers only; never configuration values or keys.
        if spending['stopped']:
            message = "Today's spending limit has been reached. Live research resumes after midnight UTC."
    return {'live_ready': ready, 'live_enabled': enabled, 'missing_settings': missing, 'max_claims': 3,
            'spending': spending, 'message': message}

@app.get('/demo', response_model=Report)
def demo():
    return demo_report()

@app.post('/fact-check', response_model=Report)
async def fact_check(request: ClaimRequest):
    return await run_live(lambda provider: run_pipeline(request.claim, provider))


@app.post('/fact-check-article', response_model=Report)
async def fact_check_article(request: ArticleRequest):
    return await run_live(lambda provider: run_article_pipeline(request.url, provider))


async def run_live(make_report):
    """Shared guards for every live report: configuration, daily spending, one run at a time, timeout, history."""
    status = config()
    if not status['live_ready']:
        raise HTTPException(503, 'Live research is off. Try the free demo. Configure backend/.env when you are ready for provider setup.'
                            if not status['live_enabled'] or status['missing_settings'] else status['message'])
    if status['spending']['stopped']:
        raise HTTPException(429, status['message'])
    if app.state.run_lock.locked():
        raise HTTPException(429, 'Another report is running. Try again when it finishes.')
    async with app.state.run_lock:
        provider = Providers()
        provider.spending = todays_budget()
        try:
            report = await asyncio.wait_for(make_report(provider), timeout=330)
        except asyncio.TimeoutError:
            raise HTTPException(504, 'The report timed out. Try a shorter, more specific claim.')
        except ArticleUnavailable as exc:
            raise HTTPException(422, str(exc))
        except BudgetExceeded:
            raise HTTPException(429, "Today's spending limit was reached before the report could start. Live research resumes after midnight UTC.")
        except ProviderFailure as exc:
            raise HTTPException(502, str(exc))
        finally:
            await provider.close()
    try:
        history().save(report)
    except sqlite3.Error:
        report = report.model_copy(update={'limitations': report.limitations + ['This report could not be saved to history.']})
    return report


def report_id(value: str) -> str:
    try:
        return str(UUID(value))
    except ValueError:
        raise HTTPException(404, 'Report not found.')


@app.get('/history')
def list_history(limit: int = Query(20, ge=1, le=100)):
    return {'reports': history().recent(limit)}


@app.get('/history/{id}', response_model=Report)
def get_history(id: str):
    report = history().get(report_id(id))
    if report is None:
        raise HTTPException(404, 'Report not found.')
    return report


@app.delete('/history/{id}')
def delete_history(id: str):
    if not history().delete(report_id(id)):
        raise HTTPException(404, 'Report not found.')
    return {'deleted': id}
