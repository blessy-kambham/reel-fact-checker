"""Local fact-checking API. Live calls are explicitly opt-in."""
import asyncio
import os
import sqlite3
import tempfile
from uuid import UUID
from pathlib import Path
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from fastapi.middleware.cors import CORSMiddleware
from schemas import ArticleRequest, ClaimRequest, Report
from decimal import Decimal, InvalidOperation
from services.budget import PRICES, BudgetExceeded, daily_budget
from services.article import ArticleUnavailable, run_article_pipeline
from services.demo import demo_report
from services.history import History
from services import access
from services import media
from services.media import MediaRejected, MediaToolMissing
from services.transcribe import LocalWhisper, TranscriptionUnavailable
from services.video import MAX_CAPTION_CHARS, run_video_pipeline
from services.pipeline import run_pipeline
from services.providers import Providers, ProviderFailure, missing_settings

load_dotenv(Path(__file__).with_name('.env'))
STARTED_AT = datetime.now(timezone.utc).isoformat(timespec='seconds')
# Names only, never values: lets a deployment be diagnosed from /config.
DIAGNOSED_SETTINGS = ('OPENAI_API_KEY', 'OPENAI_MODEL', 'TAVILY_API_KEY', 'ENABLE_LIVE_RESEARCH', 'APP_PASSWORD', 'SESSION_SECRET')


def setting_states():
    return {name: 'absent' if name not in os.environ else 'set' if os.environ[name].strip() else 'empty'
            for name in DIAGNOSED_SETTINGS}
DATA_DIR = Path(os.getenv('DATA_DIR') or Path(__file__).with_name('data'))
DEFAULT_DAILY_USD, DEFAULT_DAILY_SEARCHES = '0.50', 40


def spending_limits():
    """(max USD, max searches) per UTC day, or None when the configuration is invalid."""
    try:
        usd = Decimal(os.getenv('DAILY_BUDGET_USD', '').strip() or DEFAULT_DAILY_USD)
        searches = int(os.getenv('DAILY_SEARCH_LIMIT', '').strip() or DEFAULT_DAILY_SEARCHES)
    except (InvalidOperation, ValueError):
        return None
    return (usd, searches) if usd > 0 and searches >= 0 else None


VIDEO_SUFFIXES = {'.mp4', '.mov', '.m4v', '.webm'}
_transcriber = None


def transcriber():
    global _transcriber
    if _transcriber is None:
        _transcriber = LocalWhisper()
    return _transcriber


def video_status():
    if not media.tools_available():
        return False, 'Video checks need ffmpeg. On a Mac: brew install ffmpeg, then restart the backend.'
    if not LocalWhisper.installed():
        return False, 'Video checks need local transcription. Run: pip install -r requirements-video.txt, then restart the backend.'
    return True, 'Video checks are available.'


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    password: str = Field(min_length=1, max_length=200)


def require_access(request: Request):
    """Paid research and saved reports need a session whenever access control is on."""
    if not access.auth_required():
        return
    problem = access.configuration_problem()
    if problem:
        raise HTTPException(503, problem)
    if not access.valid_session(request.cookies.get(access.COOKIE)):
        raise HTTPException(401, 'Sign in to use live research and saved reports.')


def report_limiter():
    limit = access.reports_per_hour()
    limiters = app.state.report_limiters
    if limit not in limiters:
        limiters[limit] = access.SlidingWindow(limit, 3600) if limit else None
    return limiters[limit]


def history():
    return History(DATA_DIR / 'history.sqlite3')


def todays_budget():
    usd, searches = spending_limits()
    return daily_budget(DATA_DIR, usd, searches, os.environ['OPENAI_MODEL'])

@asynccontextmanager
async def lifespan(app):
    app.state.run_lock = asyncio.Lock()
    app.state.report_limiters = {}
    app.state.login_limiter = access.SlidingWindow(access.LOGIN_ATTEMPTS, access.LOGIN_WINDOW)
    yield

app = FastAPI(title='Reel Fact-Checker', version='0.2.0', lifespan=lifespan)
app.add_middleware(CORSMiddleware,
    allow_origins=[v.strip() for v in os.getenv('CORS_ORIGINS', 'http://localhost:5173,http://127.0.0.1:5173').split(',') if v.strip()],
    allow_methods=['GET', 'POST', 'DELETE'], allow_headers=['Content-Type'])

@app.get('/health')
def health():
    return {'status': 'ok'}

@app.get('/config')
def config(request: Request):
    missing = missing_settings()
    enabled = os.getenv('ENABLE_LIVE_RESEARCH', '').strip().lower() == 'true'
    ready, message, spending = enabled and not missing, 'Live research is ready.', None
    if not ready:
        message = 'Free demo is available. Live research is not configured or enabled.'
    elif os.environ['OPENAI_MODEL'] not in PRICES:
        ready, message = False, f'Spending limits are only priced for: {", ".join(PRICES)}. Set OPENAI_MODEL to one of them.'
    elif access.configuration_problem():
        ready, message = False, access.configuration_problem()
    elif access.reports_per_hour() is None:
        ready, message = False, 'REPORTS_PER_HOUR must be a positive whole number.'
    elif spending_limits() is None:
        ready, message = False, 'DAILY_BUDGET_USD and DAILY_SEARCH_LIMIT must be a positive amount and a whole number.'
    else:
        spending = todays_budget().status()  # Numbers only; never configuration values or keys.
        if spending['stopped']:
            message = "Today's spending limit has been reached. Live research resumes after midnight UTC."
    video_ready, video_message = video_status()
    return {'live_ready': ready, 'live_enabled': enabled, 'missing_settings': missing, 'max_claims': 3,
            'spending': spending, 'message': message, 'video_ready': ready and video_ready, 'video_message': video_message,
            'auth': {'required': access.auth_required(), 'signed_in': access.valid_session(request.cookies.get(access.COOKIE))},
            'reports_per_hour': access.reports_per_hour(),
            'started_at': STARTED_AT, 'setting_states': setting_states()}

@app.get('/demo', response_model=Report)
def demo():
    return demo_report()

@app.post('/login')
def login(body: LoginRequest, request: Request, response: Response):
    if not access.auth_required():
        return {'signed_in': True}
    problem = access.configuration_problem()
    if problem:
        raise HTTPException(503, problem)
    if not app.state.login_limiter.allow(access.client_key(request)):
        raise HTTPException(429, 'Too many sign-in attempts. Wait a few minutes and try again.')
    if not access.password_matches(body.password):
        raise HTTPException(401, 'Incorrect password.')
    response.set_cookie(access.COOKIE, access.new_session(), max_age=access.SESSION_SECONDS, httponly=True,
                        samesite='strict', secure=os.getenv('COOKIE_SECURE', '').lower() == 'true', path='/')
    return {'signed_in': True}


@app.post('/logout')
def logout(response: Response):
    response.delete_cookie(access.COOKIE, path='/')
    return {'signed_in': False}


@app.post('/fact-check', response_model=Report, dependencies=[Depends(require_access)])
async def fact_check(body: ClaimRequest, request: Request):
    return await run_live(request, lambda provider: run_pipeline(body.claim, provider))


@app.post('/fact-check-article', response_model=Report, dependencies=[Depends(require_access)])
async def fact_check_article(body: ArticleRequest, request: Request):
    return await run_live(request, lambda provider: run_article_pipeline(body.url, provider))


@app.middleware('http')
async def limit_video_uploads(request: Request, call_next):
    # Refuse oversized or unsized uploads before any of the body is read.
    if request.url.path == '/fact-check-video' and request.method == 'POST':
        if access.auth_required() and not access.valid_session(request.cookies.get(access.COOKIE)):
            return JSONResponse(status_code=401, content={'detail': 'Sign in to use live research and saved reports.'})
        length = request.headers.get('content-length')
        if not length or not length.isdigit():
            return JSONResponse(status_code=411, content={'detail': 'Uploads must declare their size.'})
        if int(length) > media.MAX_VIDEO_BYTES + 1024 * 1024:
            return JSONResponse(status_code=413, content={'detail': f'Videos must be at most {media.MAX_VIDEO_BYTES // (1024 * 1024)} MB.'})
    return await call_next(request)


@app.post('/fact-check-video', response_model=Report, dependencies=[Depends(require_access)])
async def fact_check_video(request: Request, file: UploadFile = File(...), caption: str = Form('', max_length=MAX_CAPTION_CHARS)):
    name = Path(file.filename or 'video').name[:120]
    if Path(name).suffix.lower() not in VIDEO_SUFFIXES:
        raise HTTPException(422, 'Upload an MP4, MOV or WebM video.')
    ready, message = video_status()
    if not ready:
        raise HTTPException(503, message)
    with tempfile.TemporaryDirectory(prefix='reel-upload-') as work:
        path = Path(work) / ('upload' + Path(name).suffix.lower())
        size = 0
        with path.open('wb') as target:
            while chunk := await file.read(1 << 20):
                size += len(chunk)
                if size > media.MAX_VIDEO_BYTES:
                    raise HTTPException(413, f'Videos must be at most {media.MAX_VIDEO_BYTES // (1024 * 1024)} MB.')
                target.write(chunk)
        # The upload is deleted when this block ends, whatever happens.
        return await run_live(request, lambda provider: run_video_pipeline(path, name, caption, provider, transcriber()), timeout=600)


async def run_live(request, make_report, timeout=330):
    """Shared guards for every live report: configuration, daily spending, per-client limit, one run at a time,
    timeout, history."""
    status = config(request)
    if not status['live_ready']:
        raise HTTPException(503, 'Live research is off. Try the free demo. Configure backend/.env when you are ready for provider setup.'
                            if not status['live_enabled'] or status['missing_settings'] else status['message'])
    if status['spending']['stopped']:
        raise HTTPException(429, status['message'])
    limiter = report_limiter()
    if limiter and not limiter.allow(access.client_key(request)):
        raise HTTPException(429, f'You can run {access.reports_per_hour()} reports per hour. Try again later.')
    if app.state.run_lock.locked():
        raise HTTPException(429, 'Another report is running. Try again when it finishes.')
    async with app.state.run_lock:
        provider = Providers()
        provider.spending = todays_budget()
        try:
            report = await asyncio.wait_for(make_report(provider), timeout=timeout)
        except asyncio.TimeoutError:
            raise HTTPException(504, 'The report timed out. Try a shorter, more specific claim.')
        except (ArticleUnavailable, MediaRejected) as exc:
            raise HTTPException(422, str(exc))
        except (TranscriptionUnavailable, MediaToolMissing) as exc:
            raise HTTPException(503, str(exc))
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


@app.get('/history', dependencies=[Depends(require_access)])
def list_history(limit: int = Query(20, ge=1, le=100)):
    return {'reports': history().recent(limit)}


@app.get('/history/{id}', response_model=Report, dependencies=[Depends(require_access)])
def get_history(id: str):
    report = history().get(report_id(id))
    if report is None:
        raise HTTPException(404, 'Report not found.')
    return report


@app.delete('/history/{id}', dependencies=[Depends(require_access)])
def delete_history(id: str):
    if not history().delete(report_id(id)):
        raise HTTPException(404, 'Report not found.')
    return {'deleted': id}


SECURITY_HEADERS = {
    'X-Content-Type-Options': 'nosniff',
    'X-Frame-Options': 'DENY',
    'Referrer-Policy': 'no-referrer',
    'Content-Security-Policy': "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
                               "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
}


@app.middleware('http')
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    return response


def mount_frontend(target, directory):
    """Serve the built website from the API server (production). API routes keep priority."""
    if directory and (Path(directory) / 'index.html').is_file():
        target.mount('/', StaticFiles(directory=directory, html=True), name='frontend')
        return True
    return False


mount_frontend(app, os.getenv('FRONTEND_DIST'))
