"""App-level daily spending cap. Offline: no request here reaches OpenAI or Tavily."""
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
import main
from schemas import Extraction
from services.budget import Budget, BudgetExceeded, daily_budget
from services.pipeline import run_pipeline
from tools.providers import Providers
from tests.test_pipeline import CLAIM, FakeProvider, fake_fetch


class FakeResponses:
    def __init__(self, input_tokens=1000, output_tokens=200):
        self.calls = 0
        self.tokens = input_tokens, output_tokens
    async def parse(self, **kwargs):
        self.calls += 1
        parsed = Extraction(intent='OPINION', claims=[], omitted_claims=False, note='')
        return SimpleNamespace(output_parsed=parsed, usage=SimpleNamespace(input_tokens=self.tokens[0], output_tokens=self.tokens[1]))


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'offline-test')
    monkeypatch.setenv('OPENAI_MODEL', 'gpt-4.1-mini')
    provider = Providers()
    provider.client = SimpleNamespace(responses=FakeResponses())
    return provider


def test_model_calls_reserve_then_reconcile_to_actual_cost(provider, tmp_path):
    provider.spending = Budget(tmp_path / 'day.json', max_usd='0.50')
    asyncio.run(provider.structured(Extraction, 'extract', 'text'))
    assert provider.spending.reserved == Decimal(1000) * Decimal('0.0000004') + Decimal(200) * Decimal('0.0000016')
    assert Budget(tmp_path / 'day.json').reserved == provider.spending.reserved


def test_exhausted_budget_refuses_before_any_request(provider):
    provider.spending = Budget(max_usd='0.000001')
    with pytest.raises(BudgetExceeded):
        asyncio.run(provider.structured(Extraction, 'extract', 'text'))
    assert provider.client.responses.calls == 0 and provider.spending.stopped


def test_search_limit_refuses_before_any_request(provider, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError('no HTTP client may be created')
    monkeypatch.setattr('tools.providers.httpx.AsyncClient', no_network)
    provider.spending = Budget(search_limit=0)
    with pytest.raises(BudgetExceeded):
        asyncio.run(provider.search('query'))
    assert provider.usage['search_calls'] == 0


def test_without_spending_guard_providers_behave_as_before(provider):
    assert provider.spending is None
    asyncio.run(provider.structured(Extraction, 'extract', 'text'))
    assert provider.client.responses.calls == 1


def test_daily_ledgers_are_per_utc_day_and_never_raise_within_a_day(tmp_path):
    day1 = datetime(2026, 9, 30, 23, 0, tzinfo=timezone.utc)
    budget = daily_budget(tmp_path, '0.50', 40, 'gpt-4.1-mini', now=day1)
    budget.reserve_search()
    raised = daily_budget(tmp_path, '5.00', 400, 'gpt-4.1-mini', now=day1)
    assert raised.max_usd == Decimal('0.50') and raised.search_limit == 40 and raised.searches == 1
    next_day = daily_budget(tmp_path, '0.50', 40, 'gpt-4.1-mini', now=datetime(2026, 10, 1, 0, 5, tzinfo=timezone.utc))
    assert next_day.searches == 0 and next_day.reserved == 0


def test_unpriced_models_are_rejected():
    with pytest.raises(KeyError):
        Budget(model='unpriced-model')


def test_limit_reached_mid_report_is_a_named_withheld_verdict():
    class Capped(FakeProvider):
        async def search(self, query):
            raise BudgetExceeded('Search limit reached; no search was made.')
    report = asyncio.run(run_pipeline(CLAIM.text, Capped(), fake_fetch))
    assert report.claims[0].withheld_reason == 'spending_limit'


@pytest.fixture
def live_client(monkeypatch, tmp_path):
    for key in ('OPENAI_API_KEY', 'TAVILY_API_KEY'):
        monkeypatch.setenv(key, 'secret-must-not-appear')
    monkeypatch.setenv('OPENAI_MODEL', 'gpt-4.1-mini')
    monkeypatch.setenv('ENABLE_LIVE_RESEARCH', 'true')
    monkeypatch.delenv('DAILY_BUDGET_USD', raising=False)
    monkeypatch.delenv('DAILY_SEARCH_LIMIT', raising=False)
    monkeypatch.setattr(main, 'DATA_DIR', tmp_path)
    with TestClient(main.app) as client:
        yield client


def test_config_reports_spending_as_numbers_only(live_client):
    response = live_client.get('/config')
    body = response.json()
    assert body['live_ready'] and 'secret-must-not-appear' not in response.text
    assert body['spending'] == {'spent_usd': 0.0, 'limit_usd': 0.5, 'searches': 0, 'search_limit': 40, 'stopped': False}


def test_request_is_refused_once_the_daily_limit_is_reached(live_client, monkeypatch):
    main.todays_budget().stop()
    called = []
    async def pipeline(*args):
        called.append(True)
    monkeypatch.setattr(main, 'run_pipeline', pipeline)
    response = live_client.post('/fact-check', json={'claim': 'Example'})
    assert response.status_code == 429 and 'spending limit' in response.json()['detail']
    assert not called


def test_limit_hit_during_extraction_is_a_clear_429(live_client, monkeypatch):
    async def pipeline(text, provider):
        assert provider.spending is not None
        raise BudgetExceeded('Spending budget exhausted; no further model call was made.')
    monkeypatch.setattr(main, 'run_pipeline', pipeline)
    response = live_client.post('/fact-check', json={'claim': 'Example'})
    assert response.status_code == 429 and 'midnight UTC' in response.json()['detail']


@pytest.mark.parametrize('usd, searches', [('abc', '40'), ('0', '40'), ('0.5', 'many'), ('0.5', '-1')])
def test_invalid_limits_disable_live_research_with_a_clear_message(live_client, monkeypatch, usd, searches):
    monkeypatch.setenv('DAILY_BUDGET_USD', usd)
    monkeypatch.setenv('DAILY_SEARCH_LIMIT', searches)
    body = live_client.get('/config').json()
    assert body['live_ready'] is False and 'DAILY_BUDGET_USD' in body['message']
    assert live_client.post('/fact-check', json={'claim': 'Example'}).status_code == 503


def test_unpriced_model_disables_live_research(live_client, monkeypatch):
    monkeypatch.setenv('OPENAI_MODEL', 'some-other-model')
    body = live_client.get('/config').json()
    assert body['live_ready'] is False and 'gpt-4.1-mini' in body['message']


def test_reservation_is_conservative_but_not_four_times_too_high():
    from services.budget import BYTES_PER_TOKEN
    page = 'The quick brown fox jumps over the lazy dog. ' * 400  # About 18,000 characters, like a fetched page.
    budget = Budget(max_usd='1.00')
    cost = budget.reserve('instructions', page, Extraction)
    realistic_tokens = len(page) / 4
    assert cost >= Decimal(realistic_tokens) * budget.input_price  # Still covers real input usage.
    assert BYTES_PER_TOKEN == 3 and cost < Decimal('0.02')
