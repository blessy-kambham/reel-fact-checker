"""The same article or video link, submitted again within the cache period, is answered from the saved report. Offline."""
import sqlite3
import pytest
from fastapi.testclient import TestClient
import main
from services.history import History, reusable
from tests.test_history import live_report

ARTICLE = 'https://news.example.org/story'
REEL = 'https://www.instagram.com/reel/abc/'


@pytest.fixture
def client(monkeypatch, tmp_path):
    for key in ('OPENAI_API_KEY', 'TAVILY_API_KEY'):
        monkeypatch.setenv(key, 'offline-test')
    monkeypatch.setenv('OPENAI_MODEL', 'gpt-4.1-mini')
    monkeypatch.setenv('ENABLE_LIVE_RESEARCH', 'true')
    monkeypatch.setenv('ALLOW_VIDEO_LINKS', 'true')
    monkeypatch.delenv('LINK_CACHE_HOURS', raising=False)
    monkeypatch.setattr(main, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(main, 'video_status', lambda: (True, 'ready'))
    monkeypatch.setattr(main.video_link, 'installed', lambda: True)

    class Stub:
        async def close(self):
            pass
    monkeypatch.setattr(main, 'Providers', Stub)
    runs = []

    async def article(url, provider):
        runs.append(('article', url))
        return live_report(url).model_copy(update={'input_type': 'article', 'source_url': url, 'created_at': main.datetime.now(main.timezone.utc).isoformat()})

    async def video(url, caption, provider, transcriber):
        runs.append(('video', url, caption))
        return live_report(f'Video link: {url}').model_copy(update={
            'input_type': 'video', 'source_url': url, 'created_at': main.datetime.now(main.timezone.utc).isoformat()})
    monkeypatch.setattr(main, 'run_article_pipeline', article)
    monkeypatch.setattr(main, 'run_video_link_pipeline', video)
    with TestClient(main.app) as client:
        client.runs = runs
        yield client


def test_the_same_article_link_is_answered_from_the_saved_report(client):
    first = client.post('/fact-check-article', json={'url': ARTICLE}).json()
    again = client.post('/fact-check-article', json={'url': ARTICLE + '/#comments'}).json()
    assert len(client.runs) == 1
    assert again['id'] == first['id'] and again['shown_again'] is True and first['shown_again'] is False
    assert client.get('/history').json()['reports'][0]['id'] == first['id'] and len(client.get('/history').json()['reports']) == 1


def test_a_repeated_link_costs_nothing_so_the_limits_do_not_apply(client, monkeypatch):
    monkeypatch.setenv('REPORTS_PER_HOUR', '1')
    assert client.post('/fact-check-article', json={'url': ARTICLE}).status_code == 200
    assert client.post('/fact-check-article', json={'url': ARTICLE}).status_code == 200
    assert client.post('/fact-check-article', json={'url': ARTICLE + '-other'}).status_code == 429


def test_a_video_link_is_reused_only_with_the_same_typed_caption(client):
    client.post('/fact-check-video-link', json={'url': REEL})
    assert client.post('/fact-check-video-link', json={'url': REEL + '?igsh=tracking'}).json()['shown_again'] is True
    assert client.post('/fact-check-video-link', json={'url': REEL, 'caption': 'A caption I typed'}).json()['shown_again'] is False
    assert [run[2] for run in client.runs] == ['', 'A caption I typed']


def test_an_article_and_a_video_never_share_a_report(client):
    client.post('/fact-check-article', json={'url': REEL})
    assert client.post('/fact-check-video-link', json={'url': REEL}).json()['shown_again'] is False


def test_old_reports_and_a_zero_setting_are_researched_again(client, monkeypatch):
    client.post('/fact-check-article', json={'url': ARTICLE})
    monkeypatch.setenv('LINK_CACHE_HOURS', '0')
    client.post('/fact-check-article', json={'url': ARTICLE})
    assert len(client.runs) == 2
    monkeypatch.setenv('LINK_CACHE_HOURS', 'not a number')   # falls back to 24 hours
    assert client.post('/fact-check-article', json={'url': ARTICLE}).json()['shown_again'] is True


def test_a_report_cut_short_is_not_shown_again(client, monkeypatch):
    async def cut_short(url, provider):
        client.runs.append(('article', url))
        report = live_report(url)
        claim = report.claims[0].model_copy(update={'status': 'incomplete', 'verdict_state': 'withheld',
                                                    'withheld_reason': 'search_failed', 'verdict': 'UNVERIFIABLE'})
        return report.model_copy(update={'claims': [claim], 'created_at': main.datetime.now(main.timezone.utc).isoformat()})
    monkeypatch.setattr(main, 'run_article_pipeline', cut_short)
    client.post('/fact-check-article', json={'url': ARTICLE})
    client.post('/fact-check-article', json={'url': ARTICLE})
    assert len(client.runs) == 2


def test_an_unreadable_history_means_research_runs(client, monkeypatch):
    def broken(self, key, since):
        raise sqlite3.OperationalError('locked')
    monkeypatch.setattr(History, 'find_link', broken)
    assert client.post('/fact-check-article', json={'url': ARTICLE}).status_code == 200
    assert len(client.runs) == 1


def test_only_complete_live_reports_are_reusable():
    report = live_report()
    assert reusable(report)
    assert not reusable(report.model_copy(update={'mode': 'demo'}))
    assert not reusable(report.model_copy(update={'coverage_status': 'incomplete'}))
    assert not reusable(report.model_copy(update={'claims': [report.claims[0].model_copy(update={'status': 'incomplete'})]}))
    assert reusable(report.model_copy(update={'claims': []}))   # nothing to check is a finished answer too


def test_histories_from_before_the_cache_gain_the_column(tmp_path):
    path = tmp_path / 'old.sqlite3'
    db = sqlite3.connect(path)
    db.execute('CREATE TABLE reports (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, mode TEXT NOT NULL, submitted_text TEXT NOT NULL, '
               'intent TEXT NOT NULL, coverage_status TEXT NOT NULL, model_calls INTEGER NOT NULL DEFAULT 0, '
               'search_calls INTEGER NOT NULL DEFAULT 0, report_json TEXT NOT NULL)')
    db.commit()
    db.close()
    store, report = History(path), live_report()
    store.save(report, 'key')
    assert store.find_link('key', '2000-01-01') == report
    assert store.find_link('key', '2999-01-01') is None and store.find_link('other', '2000-01-01') is None


def test_article_links_differing_only_in_path_or_query_case_are_different_pages(client):
    client.post('/fact-check-article', json={'url': 'https://bit.ly/3AbCdE'})
    assert client.post('/fact-check-article', json={'url': 'https://bit.ly/3abcde'}).json()['shown_again'] is False
    assert client.post('/fact-check-article', json={'url': 'HTTPS://BIT.LY/3AbCdE/#top'}).json()['shown_again'] is True
    assert len(client.runs) == 2
