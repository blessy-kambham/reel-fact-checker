"""Access control for a shared deployment: an optional app password with a signed session cookie,
per-client report limits and login throttling. Standard library only.

Local development needs none of it: with no APP_PASSWORD the app behaves as before. In production
(ENVIRONMENT=production) live research refuses to start until APP_PASSWORD and SESSION_SECRET are set.
"""
import hashlib
import hmac
import os
import time
from collections import deque

COOKIE = 'rfc_session'
SESSION_SECONDS = 7 * 24 * 3600
LOGIN_ATTEMPTS, LOGIN_WINDOW = 5, 600
MIN_SECRET_CHARS = 32


def production() -> bool:
    return os.getenv('ENVIRONMENT', '').strip().lower() == 'production'


def password() -> str:
    return os.getenv('APP_PASSWORD', '')


def secret() -> str:
    return os.getenv('SESSION_SECRET', '')


def auth_required() -> bool:
    return bool(password()) or production()


def configuration_problem() -> str | None:
    """A message when access control is misconfigured for this environment, else None."""
    if production() and not password():
        return 'Set APP_PASSWORD before offering live research in production.'
    if auth_required() and len(secret()) < MIN_SECRET_CHARS:
        return f'Set SESSION_SECRET to a random value of at least {MIN_SECRET_CHARS} characters.'
    return None


def _sign(issued: str) -> str:
    return hmac.new(secret().encode(), f'{issued}.{hashlib.sha256(password().encode()).hexdigest()}'.encode(),
                    hashlib.sha256).hexdigest()


def new_session(now: float | None = None) -> str:
    issued = str(int(now if now is not None else time.time()))
    return f'{issued}.{_sign(issued)}'


def valid_session(token: str | None, now: float | None = None) -> bool:
    """Sessions expire after a week and become invalid when the password or secret changes."""
    if not token or configuration_problem():
        return False
    issued, _, signature = token.partition('.')
    if not issued.isdigit() or not hmac.compare_digest(signature, _sign(issued)):
        return False
    age = (now if now is not None else time.time()) - int(issued)
    return 0 <= age <= SESSION_SECONDS


def password_matches(attempt: str) -> bool:
    return bool(password()) and hmac.compare_digest(attempt.encode(), password().encode())


class SlidingWindow:
    """At most `limit` events per `seconds` per key, kept in memory (one process)."""
    def __init__(self, limit: int, seconds: int):
        self.limit, self.seconds, self.events = limit, seconds, {}

    def allow(self, key: str, now: float | None = None) -> bool:
        now = now if now is not None else time.monotonic()
        window = self.events.setdefault(key, deque())
        while window and now - window[0] >= self.seconds:
            window.popleft()
        if len(window) >= self.limit:
            return False
        window.append(now)
        return True


def reports_per_hour() -> int | None:
    value = os.getenv('REPORTS_PER_HOUR', '').strip() or '10'
    return int(value) if value.isdigit() and int(value) > 0 else None


def client_key(request) -> str:
    """The signed-in session if any, otherwise the client address (forwarded only when trusted)."""
    session = request.cookies.get(COOKIE)
    if session and valid_session(session):
        return 'session:' + session.rpartition('.')[2][:16]
    address = request.client.host if request.client else 'unknown'
    if os.getenv('TRUST_PROXY_HEADERS', '').lower() == 'true':
        forwarded = request.headers.get('x-forwarded-for', '').split(',')[0].strip()
        address = forwarded or address
    return 'ip:' + address
