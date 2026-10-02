"""Access control, report limits, security headers and production static serving. Offline."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
import main
from services import access
from tests.test_history import live_report

PASSWORD, SECRET = 'correct horse battery staple', 's' * 40


@pytest.fixture
def env(monkeypatch, tmp_path):
    for key in ('OPENAI_API_KEY', 'TAVILY_API_KEY'):
        monkeypatch.setenv(key, 'offline-test')
    monkeypatch.setenv('OPENAI_MODEL', 'gpt-4.1-mini')
    monkeypatch.setenv('ENABLE_LIVE_RESEARCH', 'true')
    for key in ('APP_PASSWORD', 'SESSION_SECRET', 'ENVIRONMENT', 'REPORTS_PER_HOUR', 'TRUST_PROXY_HEADERS', 'COOKIE_SECURE'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(main, 'DATA_DIR', tmp_path)
    class Stub:
        async def close(self):
            pass
    monkeypatch.setattr(main, 'Providers', Stub)
    async def pipeline(text, provider):
        return live_report(text)
    monkeypatch.setattr(main, 'run_pipeline', pipeline)
    return monkeypatch


def client():
    return TestClient(main.app)


@pytest.fixture
def protected(env):
    env.setenv('APP_PASSWORD', PASSWORD)
    env.setenv('SESSION_SECRET', SECRET)
    return env


def test_without_a_password_local_use_is_unchanged(env):
    with client() as c:
        assert c.get('/config').json()['auth'] == {'required': False, 'signed_in': False}
        assert c.post('/fact-check', json={'claim': 'x'}).status_code == 200


def test_password_without_a_strong_secret_disables_live_research(env):
    env.setenv('APP_PASSWORD', PASSWORD)
    env.setenv('SESSION_SECRET', 'short')
    with client() as c:
        body = c.get('/config').json()
        assert body['live_ready'] is False and 'SESSION_SECRET' in body['message']
        assert c.post('/fact-check', json={'claim': 'x'}).status_code == 503


def test_production_requires_a_password(env):
    env.setenv('ENVIRONMENT', 'production')
    env.setenv('SESSION_SECRET', SECRET)
    with client() as c:
        body = c.get('/config').json()
        assert body['live_ready'] is False and 'APP_PASSWORD' in body['message']


def test_sign_in_unlocks_research_and_history_and_sign_out_locks_them(protected):
    with client() as c:
        assert c.post('/fact-check', json={'claim': 'x'}).status_code == 401
        assert c.get('/history').status_code == 401
        assert c.post('/login', json={'password': 'wrong'}).status_code == 401
        response = c.post('/login', json={'password': PASSWORD})
        cookie = response.headers['set-cookie'].lower()
        assert response.status_code == 200 and 'httponly' in cookie and 'samesite=strict' in cookie
        assert c.get('/config').json()['auth'] == {'required': True, 'signed_in': True}
        report = c.post('/fact-check', json={'claim': 'x'}).json()
        assert c.get(f"/history/{report['id']}").status_code == 200
        c.post('/logout')
        assert c.post('/fact-check', json={'claim': 'x'}).status_code == 401


def test_config_never_reveals_the_password_or_secret(protected):
    with client() as c:
        text = c.get('/config').text
        assert PASSWORD not in text and SECRET not in text


def test_sessions_resist_tampering_expiry_and_password_changes(protected):
    token = access.new_session(now=1_000_000)
    assert access.valid_session(token, now=1_000_100)
    assert not access.valid_session(token, now=1_000_000 + access.SESSION_SECONDS + 1)
    issued, signature = token.split('.')
    assert not access.valid_session(f'{int(issued) + 1}.{signature}', now=1_000_100)
    assert not access.valid_session('garbage', now=1_000_100) and not access.valid_session(None)
    protected.setenv('APP_PASSWORD', 'a new password')
    assert not access.valid_session(token, now=1_000_100)


def test_sign_in_attempts_are_throttled(protected):
    with client() as c:
        codes = [c.post('/login', json={'password': 'wrong'}).status_code for _ in range(access.LOGIN_ATTEMPTS)]
        assert codes == [401] * access.LOGIN_ATTEMPTS
        assert c.post('/login', json={'password': PASSWORD}).status_code == 429


def test_reports_are_limited_per_client(env):
    env.setenv('REPORTS_PER_HOUR', '2')
    with client() as c:
        assert [c.post('/fact-check', json={'claim': 'x'}).status_code for _ in range(3)] == [200, 200, 429]


def test_forwarded_addresses_count_only_when_trusted(env):
    env.setenv('REPORTS_PER_HOUR', '1')
    with client() as c:
        first = c.post('/fact-check', json={'claim': 'x'}, headers={'X-Forwarded-For': '203.0.113.1'})
        second = c.post('/fact-check', json={'claim': 'x'}, headers={'X-Forwarded-For': '203.0.113.2'})
        assert (first.status_code, second.status_code) == (200, 429)  # Untrusted header ignored.
    env.setenv('TRUST_PROXY_HEADERS', 'true')
    with client() as c:
        codes = [c.post('/fact-check', json={'claim': 'x'}, headers={'X-Forwarded-For': ip}).status_code
                 for ip in ('203.0.113.1', '203.0.113.2')]
        assert codes == [200, 200]


def test_invalid_report_limit_disables_live_research(env):
    env.setenv('REPORTS_PER_HOUR', 'lots')
    with client() as c:
        assert c.get('/config').json()['live_ready'] is False


def test_video_uploads_need_a_session_before_the_body_is_read(protected):
    with client() as c:
        response = c.post('/fact-check-video', files={'file': ('clip.mp4', b'x' * 1024, 'video/mp4')})
        assert response.status_code == 401


def test_security_headers_are_set(env):
    with client() as c:
        headers = c.get('/health').headers
        assert headers['x-content-type-options'] == 'nosniff' and headers['x-frame-options'] == 'DENY'
        assert "frame-ancestors 'none'" in headers['content-security-policy']


def test_built_frontend_is_served_without_shadowing_the_api(tmp_path):
    (tmp_path / 'index.html').write_text('<!doctype html><title>app</title>')
    (tmp_path / 'assets').mkdir()
    (tmp_path / 'assets' / 'app.js').write_text('console.log(1)')
    app = FastAPI()
    @app.get('/health')
    def health():
        return {'status': 'ok'}
    assert main.mount_frontend(app, str(tmp_path))
    with TestClient(app) as c:
        assert c.get('/').text.startswith('<!doctype html>')
        assert c.get('/assets/app.js').status_code == 200
        assert c.get('/health').json() == {'status': 'ok'}
        assert c.get('/../../backend/.env').status_code == 404
    assert not main.mount_frontend(FastAPI(), str(tmp_path / 'missing'))
    assert not main.mount_frontend(FastAPI(), None)


def test_config_reports_setting_states_without_values(protected):
    protected.setenv('OPENAI_MODEL', '   ')
    protected.delenv('TAVILY_API_KEY')
    protected.setenv('ENABLE_LIVE_RESEARCH', ' TRUE ')
    with client() as c:
        response = c.get('/config')
        body = response.json()
        assert body['setting_states'] == {'OPENAI_API_KEY': 'set', 'OPENAI_MODEL': 'empty', 'TAVILY_API_KEY': 'absent',
                                          'ENABLE_LIVE_RESEARCH': 'set', 'APP_PASSWORD': 'set', 'SESSION_SECRET': 'set'}
        assert body['live_enabled'] is True and body['started_at'].endswith('+00:00')
        assert PASSWORD not in response.text and SECRET not in response.text and 'offline-test' not in response.text
