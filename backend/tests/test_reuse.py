"""A claim that was already checked is recognised and its result shown again, without new research. Offline."""
import asyncio
import sqlite3
import pytest
from fastapi.testclient import TestClient
import main
from agents import orchestrator
from agents.orchestrator import claim_key, research_all
from schemas import AtomicClaim, Report
from services.history import History
from tests.test_pipeline import CLAIM, FakeProvider, fake_fetch

LONG = 'The fictional sensor measured twelve units during the test in the one room that was sampled last year.'


def test_the_key_ignores_spacing_case_and_quote_style_but_not_the_words():
    same = AtomicClaim(text="  the SENSOR   measured 12 units. ", context='fictional test')
    assert claim_key(CLAIM) == claim_key(same) and len(claim_key(CLAIM)) == 32
    assert claim_key(CLAIM) != claim_key(AtomicClaim(text='The sensor measured 13 units.', context='Fictional test'))
    assert claim_key(CLAIM) != claim_key(AtomicClaim(text=CLAIM.text, context='A different submission'))


def test_the_key_records_whether_copies_of_the_checked_text_were_kept_out():
    # Short markers exclude nothing, so they do not change the question; a long one does.
    assert claim_key(CLAIM, ['short text']) == claim_key(CLAIM)
    assert claim_key(CLAIM, [LONG]) != claim_key(CLAIM)


def remembered(provider, results):
    """A `recall` that knows the results of an earlier run, the way the app's history does."""
    by_key = {r.claim_key: r for r in results}
    asked = []
    def recall(key):
        asked.append(key)
        return (by_key[key], 'earlier-report', '2026-10-05T09:30:00+00:00') if key in by_key else None
    provider.recall = recall
    return asked


def test_a_claim_checked_before_is_shown_again_without_new_research():
    [first] = asyncio.run(research_all([CLAIM], FakeProvider(), fake_fetch))
    assert first.verdict == 'TRUE' and first.claim_key == claim_key(CLAIM) and first.reused_from is None
    provider = FakeProvider()
    remembered(provider, [first])
    [again] = asyncio.run(research_all([CLAIM], provider, fake_fetch))
    assert provider.queries == [] and provider.verifier_calls == 0
    assert again.verdict == 'TRUE' and again.evidence == first.evidence and again.confidence == first.confidence
    assert again.reused_from == 'earlier-report' and again.first_checked_at == '2026-10-05T09:30:00+00:00'
    assert again.limitations[0] == 'This claim was checked on 2026-10-05. That result is shown again; no new research was done.'
    assert again.agent_steps[0] == 'Orchestrator: Recognised a claim already checked on 2026-10-05 and reused its result.'


def test_only_the_repeated_claim_is_reused():
    other = AtomicClaim(text='The sample was limited to one room.', context='Fictional test')
    [first] = asyncio.run(research_all([CLAIM], FakeProvider(), fake_fetch))
    provider = FakeProvider()
    remembered(provider, [first])
    again, fresh = asyncio.run(research_all([CLAIM, other], provider, fake_fetch))
    assert again.reused_from == 'earlier-report' and fresh.reused_from is None and len(provider.queries) == 2


def test_nothing_is_reused_when_pages_are_excluded_for_this_submission():
    """An article's own page may not be evidence for its claims, and an earlier result may rest on it."""
    [first] = asyncio.run(research_all([CLAIM], FakeProvider(), fake_fetch))
    provider = FakeProvider()
    asked = remembered(provider, [first])
    [fresh] = asyncio.run(research_all([CLAIM], provider, fake_fetch, exclude=frozenset({'https://example.com/article'})))
    assert asked == [] and len(provider.queries) == 2 and fresh.reused_from is None and fresh.claim_key is None


def test_without_a_history_every_claim_is_researched():
    provider = FakeProvider()
    asyncio.run(research_all([CLAIM], provider, fake_fetch))
    asyncio.run(research_all([CLAIM], provider, fake_fetch))
    assert len(provider.queries) == 4


# ---- the history side ---------------------------------------------------------------------------

def report_with(result, created_at='2026-10-05T09:30:00+00:00', report_id='r1'):
    return Report(id=report_id, mode='live', submitted_text=result.claim, created_at=created_at, intent='FACTUAL', note='',
                  claims=[result], limitations=[], usage={})


@pytest.fixture
def checked():
    return asyncio.run(research_all([CLAIM], FakeProvider(), fake_fetch))[0]


def test_history_finds_the_result_of_an_identical_claim(tmp_path, checked):
    store = History(tmp_path / 'h.sqlite3')
    store.save(report_with(checked))
    found, report_id, created_at = store.find_claim(checked.claim_key, '2026-10-01T00:00:00+00:00')
    assert found == checked and report_id == 'r1' and created_at == '2026-10-05T09:30:00+00:00'
    assert store.find_claim('0' * 32, '2026-10-01T00:00:00+00:00') is None


def test_history_returns_the_newest_and_nothing_older_than_the_period(tmp_path, checked):
    store = History(tmp_path / 'h.sqlite3')
    store.save(report_with(checked, '2026-09-20T09:00:00+00:00', 'old'))
    assert store.find_claim(checked.claim_key, '2026-09-29T00:00:00+00:00') is None
    store.save(report_with(checked, '2026-10-02T09:00:00+00:00', 'newer'))
    store.save(report_with(checked, '2026-10-04T09:00:00+00:00', 'newest'))
    assert store.find_claim(checked.claim_key, '2026-09-29T00:00:00+00:00')[1] == 'newest'


def test_a_withheld_or_incomplete_result_is_researched_again(tmp_path, checked):
    store = History(tmp_path / 'h.sqlite3')
    store.save(report_with(checked.model_copy(update={'verdict_state': 'withheld', 'verdict': 'UNVERIFIABLE'}), report_id='w'))
    store.save(report_with(checked.model_copy(update={'status': 'incomplete'}), report_id='i'))
    assert store.find_claim(checked.claim_key, '2026-10-01T00:00:00+00:00') is None


def test_an_answer_of_unverifiable_is_researched_again(tmp_path, checked):
    store = History(tmp_path / 'h.sqlite3')
    store.save(report_with(checked.model_copy(update={'verdict': 'UNVERIFIABLE'})))
    assert store.find_claim(checked.claim_key, '2026-10-01T00:00:00+00:00') is None


def test_the_reused_result_carries_the_claim_as_worded_this_time():
    [first] = asyncio.run(research_all([CLAIM], FakeProvider(), fake_fetch))
    provider = FakeProvider()
    remembered(provider, [first])
    reworded = AtomicClaim(text='the sensor  measured 12 UNITS.', context=CLAIM.context)
    [again] = asyncio.run(research_all([reworded], provider, fake_fetch))
    assert again.reused_from == 'earlier-report' and again.claim == 'the sensor  measured 12 UNITS.'


def test_a_reused_result_is_not_itself_a_source_for_reuse(tmp_path, checked):
    """Otherwise each reuse would renew the result and it would never be researched again."""
    store = History(tmp_path / 'h.sqlite3')
    store.save(report_with(orchestrator.reused(checked, 'r0', '2026-09-01T00:00:00+00:00')))
    assert store.find_claim(checked.claim_key, '2026-10-01T00:00:00+00:00') is None


def test_deleting_a_report_forgets_its_claims(tmp_path, checked):
    store = History(tmp_path / 'h.sqlite3')
    store.save(report_with(checked))
    store.delete('r1')
    assert store.find_claim(checked.claim_key, '2026-10-01T00:00:00+00:00') is None


def test_a_history_file_from_before_reuse_still_opens(tmp_path, checked):
    path = tmp_path / 'h.sqlite3'
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE reports (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, mode TEXT NOT NULL, submitted_text TEXT NOT NULL,
            intent TEXT NOT NULL, coverage_status TEXT NOT NULL, model_calls INTEGER NOT NULL DEFAULT 0,
            search_calls INTEGER NOT NULL DEFAULT 0, report_json TEXT NOT NULL);
        CREATE TABLE claims (report_id TEXT NOT NULL REFERENCES reports(id) ON DELETE CASCADE, position INTEGER NOT NULL,
            claim TEXT NOT NULL, verdict TEXT NOT NULL, verdict_state TEXT NOT NULL, withheld_reason TEXT, status TEXT NOT NULL,
            PRIMARY KEY (report_id, position));
        INSERT INTO reports VALUES ('before', '2026-10-01T00:00:00+00:00', 'live', 'old', 'FACTUAL', 'passed', 0, 0, '{}');
        INSERT INTO claims VALUES ('before', 0, 'An old claim', 'TRUE', 'issued', NULL, 'complete');
    """)
    db.commit()
    db.close()
    # Several requests may open an old history at once: each tries to add the column, and none may fail.
    import threading
    errors = []
    def open_it():
        try:
            History(path).recent()
        except Exception as exc:
            errors.append(exc)
    threads = [threading.Thread(target=open_it) for _ in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    store = History(path)
    assert store.find_claim(checked.claim_key, '2026-09-01T00:00:00+00:00') is None   # old rows have no key
    store.save(report_with(checked))
    assert store.find_claim(checked.claim_key, '2026-10-01T00:00:00+00:00')[1] == 'r1'
    assert [item['id'] for item in store.recent()] == ['r1', 'before']


# ---- through the API ----------------------------------------------------------------------------

@pytest.fixture
def client(monkeypatch, tmp_path):
    for key in ('OPENAI_API_KEY', 'TAVILY_API_KEY'):
        monkeypatch.setenv(key, 'offline-test')
    monkeypatch.setenv('OPENAI_MODEL', 'gpt-4.1-mini')
    monkeypatch.setenv('ENABLE_LIVE_RESEARCH', 'true')
    monkeypatch.delenv('REUSE_DAYS', raising=False)
    monkeypatch.setattr(main, 'DATA_DIR', tmp_path)
    made = []
    class Provider(FakeProvider):
        def __init__(self):
            super().__init__()
            made.append(self)
        async def close(self):
            pass
    async def pipeline(text, provider):
        return await orchestrator.run_pipeline(text, provider, fake_fetch)
    monkeypatch.setattr(main, 'Providers', Provider)
    monkeypatch.setattr(main, 'run_pipeline', pipeline)
    with TestClient(main.app) as client:
        client.made = made
        yield client


def check(client):
    response = client.post('/fact-check', json={'claim': CLAIM.text})
    assert response.status_code == 200
    return response.json(), client.made[-1]


def test_the_app_researches_a_claim_once_and_then_shows_the_saved_result(client):
    first, provider = check(client)
    assert first['claims'][0]['reused_from'] is None and len(provider.queries) == 2
    second, provider = check(client)
    assert provider.queries == [] and second['id'] != first['id']
    assert second['claims'][0]['reused_from'] == first['id'] and second['claims'][0]['verdict'] == 'TRUE'
    # The third check still goes back to the original, not to the copy shown the second time.
    third, provider = check(client)
    assert provider.queries == [] and third['claims'][0]['reused_from'] == first['id']


def test_deleting_the_original_report_makes_the_app_research_the_claim_again(client):
    first, _ = check(client)
    assert client.delete(f"/history/{first['id']}").status_code == 200
    again, provider = check(client)
    assert again['claims'][0]['reused_from'] is None and len(provider.queries) == 2


def test_reuse_can_be_turned_off(client, monkeypatch):
    check(client)
    monkeypatch.setenv('REUSE_DAYS', '0')
    again, provider = check(client)
    assert again['claims'][0]['reused_from'] is None and len(provider.queries) == 2
    monkeypatch.setenv('REUSE_DAYS', 'not a number')
    assert main.reuse_days() == 7


def test_a_result_older_than_the_period_is_not_reused(client, monkeypatch):
    first, _ = check(client)
    store = main.history()
    report = store.get(first['id'])
    store.save(report.model_copy(update={'created_at': '2026-01-01T00:00:00+00:00'}))
    again, provider = check(client)
    assert again['claims'][0]['reused_from'] is None and len(provider.queries) == 2
