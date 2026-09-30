"""Local report history: storage, API and failure handling. Offline."""
import sqlite3
import pytest
from fastapi.testclient import TestClient
import main
from services.demo import demo_report
from services.history import History


def live_report(text='A saved claim', created_at='2026-09-30T12:00:00+00:00'):
    report = demo_report()
    claim = report.claims[0]
    evidence = [c.model_copy(update={'evidence_id': f'E{i + 1}', 'verified': True}) for i, c in enumerate(claim.evidence)]
    claim = claim.model_copy(update={'evidence': evidence, 'verdict_evidence_ids': ['E2'], 'verdict_state': 'issued'})
    return report.model_copy(update={'mode': 'live', 'submitted_text': text, 'created_at': created_at, 'claims': [claim]})


def test_round_trip_keeps_the_full_report(tmp_path):
    store, report = History(tmp_path / 'h.sqlite3'), live_report()
    store.save(report)
    assert store.get(report.id) == report


def test_rows_record_claims_sources_and_verdict_evidence(tmp_path):
    store, report = History(tmp_path / 'h.sqlite3'), live_report()
    store.save(report)
    db = sqlite3.connect(tmp_path / 'h.sqlite3')
    assert db.execute('SELECT verdict, verdict_state FROM claims').fetchall() == [('MISLEADING', 'issued')]
    rows = db.execute('SELECT evidence_id, accepted, used_for_verdict, title FROM citations ORDER BY evidence_id').fetchall()
    assert [(r[0], r[1], r[2]) for r in rows] == [('E1', 1, 0), ('E2', 1, 1)]
    assert all(r[3] for r in rows)


def test_recent_is_newest_first_with_short_previews(tmp_path):
    store = History(tmp_path / 'h.sqlite3')
    store.save(live_report('old', '2026-09-29T12:00:00+00:00'))
    store.save(live_report('x' * 500, '2026-09-30T12:00:00+00:00'))
    items = store.recent()
    assert [len(i['preview']) for i in items] == [200, 3]
    assert items[0]['claims'][0]['verdict'] == 'MISLEADING'
    assert len(store.recent(limit=1)) == 1


def test_saving_twice_does_not_duplicate_rows(tmp_path):
    store, report = History(tmp_path / 'h.sqlite3'), live_report()
    store.save(report)
    store.save(report)
    db = sqlite3.connect(tmp_path / 'h.sqlite3')
    assert db.execute('SELECT COUNT(*) FROM claims').fetchone()[0] == 1
    assert db.execute('SELECT COUNT(*) FROM citations').fetchone()[0] == 2


def test_delete_removes_report_and_its_rows(tmp_path):
    store, report = History(tmp_path / 'h.sqlite3'), live_report()
    store.save(report)
    assert store.delete(report.id) and not store.delete(report.id)
    db = sqlite3.connect(tmp_path / 'h.sqlite3')
    assert db.execute('SELECT COUNT(*) FROM citations').fetchone()[0] == 0
    assert store.get(report.id) is None


@pytest.fixture
def client(monkeypatch, tmp_path):
    for key in ('OPENAI_API_KEY', 'TAVILY_API_KEY'):
        monkeypatch.setenv(key, 'offline-test')
    monkeypatch.setenv('OPENAI_MODEL', 'gpt-4.1-mini')
    monkeypatch.setenv('ENABLE_LIVE_RESEARCH', 'true')
    monkeypatch.setattr(main, 'DATA_DIR', tmp_path)
    class Stub:
        async def close(self):
            pass
    monkeypatch.setattr(main, 'Providers', Stub)
    with TestClient(main.app) as client:
        yield client


def test_live_reports_are_saved_and_listed(client, monkeypatch):
    async def pipeline(text, provider):
        return live_report(text)
    monkeypatch.setattr(main, 'run_pipeline', pipeline)
    report = client.post('/fact-check', json={'claim': 'Saved through the API'}).json()
    listing = client.get('/history').json()['reports']
    assert [r['id'] for r in listing] == [report['id']]
    assert client.get(f"/history/{report['id']}").json() == report


def test_demo_reports_are_not_saved(client):
    client.get('/demo')
    assert client.get('/history').json() == {'reports': []}


def test_history_save_failure_still_returns_the_report(client, monkeypatch):
    async def pipeline(text, provider):
        return live_report(text)
    def broken(self, report):
        raise sqlite3.OperationalError('disk full')
    monkeypatch.setattr(main, 'run_pipeline', pipeline)
    monkeypatch.setattr(History, 'save', broken)
    response = client.post('/fact-check', json={'claim': 'Unsaved'})
    assert response.status_code == 200
    assert 'This report could not be saved to history.' in response.json()['limitations']
    assert 'disk full' not in response.text


def test_history_routes_reject_unknown_and_malformed_ids(client):
    assert client.get('/history/not-a-uuid').status_code == 404
    assert client.get('/history/00000000-0000-4000-8000-000000000000').status_code == 404
    assert client.delete('/history/../../etc').status_code == 404
    assert client.get('/history?limit=0').status_code == 422


def test_delete_route(client, monkeypatch):
    async def pipeline(text, provider):
        return live_report(text)
    monkeypatch.setattr(main, 'run_pipeline', pipeline)
    report_id = client.post('/fact-check', json={'claim': 'Delete me'}).json()['id']
    assert client.delete(f'/history/{report_id}').json() == {'deleted': report_id}
    assert client.get(f'/history/{report_id}').status_code == 404
