import json
import asyncio
from decimal import Decimal
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
import main
from evaluation.live import Budget
from evaluation.summarize import summarize
from services.providers import ProviderFailure
from services.demo import demo_report
from schemas import Extraction


def test_budget_refuses_oversized_request():
    budget = Budget()
    with pytest.raises(ProviderFailure, match='budget'):
        budget.reserve('test', 'x' * 300000, Extraction)
    assert budget.reserved == 0


def test_budget_never_exceeds_allowance():
    budget = Budget()
    with pytest.raises(ProviderFailure):
        for _ in range(100):
            budget.reserve('test', 'claim', Extraction)
    assert 0 < budget.reserved <= Decimal('0.10')


@pytest.fixture
def enabled_client(monkeypatch, tmp_path):
    for key in ('OPENAI_API_KEY', 'TAVILY_API_KEY'):
        monkeypatch.setenv(key, 'offline-test')
    monkeypatch.setenv('OPENAI_MODEL', 'gpt-4.1-mini')
    monkeypatch.setattr(main, 'DATA_DIR', tmp_path)
    monkeypatch.setenv('ENABLE_LIVE_RESEARCH', 'true')
    class Stub:
        closed = False
        async def close(self):
            self.closed = True
    provider = Stub()
    monkeypatch.setattr(main, 'Providers', lambda: provider)
    with TestClient(main.app) as client:
        yield client, provider


def test_live_route_returns_report_and_closes_provider(enabled_client, monkeypatch):
    client, provider = enabled_client
    async def pipeline(text, provider):
        return demo_report().model_copy(update={'mode':'live','submitted_text':text})
    monkeypatch.setattr(main, 'run_pipeline', pipeline)
    response = client.post('/fact-check', json={'claim':'  Example  '})
    assert response.status_code == 200
    assert response.json()['submitted_text'] == 'Example'
    assert provider.closed


def test_live_provider_failure_is_actionable(enabled_client, monkeypatch):
    client, provider = enabled_client
    async def pipeline(*args):
        raise ProviderFailure('Model unavailable for this test.')
    monkeypatch.setattr(main, 'run_pipeline', pipeline)
    response = client.post('/fact-check', json={'claim':'Example'})
    assert response.status_code == 502
    assert response.json()['detail'] == 'Model unavailable for this test.'
    assert provider.closed


def test_live_timeout_closes_provider(enabled_client, monkeypatch):
    client, provider = enabled_client
    async def pipeline(*args):
        raise asyncio.TimeoutError()
    monkeypatch.setattr(main, 'run_pipeline', pipeline)
    assert client.post('/fact-check', json={'claim':'Example'}).status_code == 504
    assert provider.closed


def test_summary_does_not_count_absent_results_as_success():
    summary = summarize([{'http_status':502,'response':{'detail':'failed'},'model':[{'error':'failed'}]}], [])
    assert summary['incomplete_cases'] == 1
    assert summary['verdict_agreement'] is None
    assert summary['citation_acceptance_rate'] is None
    assert summary['provider_failures'] == 1


def test_real_dataset_has_expected_coverage():
    cases = json.loads((Path(__file__).parents[1]/'evaluation/real_cases.json').read_text())
    assert 10 <= len(cases) <= 15
    assert len({c['id'] for c in cases}) == len(cases)
    assert {'TRUE','FALSE','PARTIALLY TRUE','MISLEADING','OUTDATED','UNVERIFIABLE'} <= {c['reference_verdict'] for c in cases}
    assert all(c['rationale'] and (c['reference_sources'] or c['reference_verdict']=='UNVERIFIABLE') for c in cases)


def test_rejection_metrics_separate_unavailable_checks():
    claim = {'verdict':'UNVERIFIABLE','status':'incomplete','evidence':[],
        'rejected_citations':[{'verification_code':'check_unavailable','verification':'Unavailable','quote':'q'},
                              {'verification_code':'quote_not_found','verification':'Absent','quote':'q'}]}
    trace = {'http_status':200,'model':[{'stage':'Extraction','output':{'claims':[{}]}}],
             'response':{'submitted_text':'x','claims':[claim]}}
    summary = summarize([trace], [{'claim':'x','reference_verdict':'TRUE'}])
    assert summary['unsupported_citation_count'] == 1
    assert summary['extraction_failures'] == 0
    assert summary['verdict_agreement'] == 0
    assert summary['human_reviewed_unsupported_accepted_citations'] is None


@pytest.mark.parametrize('quote',['Water freezes at 0\u000b0C.', 'Water freezes...under standard pressure.'])
def test_altered_quotes_remain_rejected(quote):
    import asyncio
    from schemas import EvidenceDraft, Source
    from services.pipeline import verify_citation
    class NoModel:
        async def structured(self, *args):
            raise AssertionError('Bad quote must fail before calling a model')
    source = Source(id='S1',title='Fixture',url='https://example.org',text='Water freezes at 0°C. This applies under standard pressure.',retrieved_at='2026-09-27')
    draft = EvidenceDraft(source_id='S1',quote=quote,statement='Test',stance='FOR')
    result = asyncio.run(verify_citation(draft, {'S1':source}, NoModel()))
    assert result.verification_code == 'quote_not_found'


def test_budget_reservation_survives_restart(tmp_path):
    ledger = tmp_path / 'budget.json'
    first = Budget(ledger)
    first.reserve('test', 'claim', Extraction)
    first.searches = 12
    first.save()
    resumed = Budget(ledger)
    assert resumed.reserved == first.reserved
    assert resumed.searches == 12


def test_budget_stop_is_persistent_and_blocks_smaller_calls_and_searches(tmp_path):
    path = tmp_path / 'budget.json'
    budget = Budget(path, search_limit=8)
    with pytest.raises(ProviderFailure):
        budget.reserve('test', 'x' * 300000, Extraction)
    resumed = Budget(path)
    assert resumed.stopped and resumed.search_limit == 8
    with pytest.raises(ProviderFailure):
        resumed.reserve('test', 'small', Extraction)
    with pytest.raises(ProviderFailure):
        resumed.reserve_search()
    assert resumed.searches == 0 and resumed.reserved == 0


def test_search_cap_stops_model_calls():
    budget = Budget(search_limit=1)
    budget.reserve_search()
    with pytest.raises(ProviderFailure):
        budget.reserve_search()
    with pytest.raises(ProviderFailure):
        budget.reserve('test', 'claim', Extraction)
    assert budget.searches == 1


def test_summary_records_coverage_and_budget_blocks():
    result = summarize([{'response': {'coverage_status': 'incomplete', 'claims': []},
                         'blocked': [{'stage': 'ExtractionCoverage'}]}], [])
    assert result['coverage_failures'] == 1
    assert result['blocked_calls'] == 1
    assert result['incomplete_cases'] == 1
