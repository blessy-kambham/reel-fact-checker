import pytest
from fastapi.testclient import TestClient
from main import app

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv('ENABLE_LIVE_RESEARCH', 'false')
    with TestClient(app) as client:
        yield client

def test_health(client):
    assert client.get('/health').json() == {'status': 'ok'}

def test_live_disabled(client):
    assert client.post('/fact-check', json={'claim': 'Example claim'}).status_code == 503

def test_config_hides_secrets(client, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'secret-test-value')
    response = client.get('/config')
    assert 'secret-test-value' not in response.text
    assert response.json()['live_ready'] is False

def test_demo_is_offline(client):
    report = client.get('/demo').json()
    assert report['mode'] == 'demo'
    assert report['usage']['model_calls'] == report['usage']['search_calls'] == 0
    assert all(not c['verified'] and c['url'] is None for r in report['claims'] for c in r['evidence'])

@pytest.mark.parametrize('payload', [{}, {'claim': ' '}, {'claim': 'x' * 5001}, {'claim': 42}, {'claim': None}, {'claim': 'test', 'verdict': 'TRUE'}])
def test_invalid_claim(client, payload):
    assert client.post('/fact-check', json=payload).status_code == 422

def test_cors(client):
    response = client.options('/fact-check', headers={'Origin': 'http://localhost:5173', 'Access-Control-Request-Method': 'POST'})
    assert response.headers['access-control-allow-origin'] == 'http://localhost:5173'
    assert 'access-control-allow-origin' not in client.get('/health', headers={'Origin': 'https://example.com'}).headers
