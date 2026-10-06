"""The scorecard: how cases are scored and how a run is saved, capped and resumed. Offline."""
import asyncio
import json
import pytest
from evaluation import scorecard
from evaluation.scorecard import load_cases, markdown, outcome, summarize
from schemas import Analysis, CitationJudgment, Verdict
from tests.test_pipeline import CLAIM, DRAFT, FakeProvider, fake_fetch

TRUE_CASE = {'id': 'a', 'claim': CLAIM.text, 'expected': 'TRUE', 'accepted': ['TRUE']}
FALSE_CASE = {'id': 'b', 'claim': CLAIM.text, 'expected': 'FALSE', 'accepted': ['FALSE', 'MISLEADING']}
UNCHECKABLE = {'id': 'c', 'claim': CLAIM.text, 'expected': 'UNVERIFIABLE', 'accepted': ['UNVERIFIABLE']}


def claim(verdict, state='issued', reason=None, evidence=(), rejected=(), cited=(), strength=None):
    return {'claim': CLAIM.text, 'verdict': verdict, 'verdict_state': state, 'withheld_reason': reason, 'evidence': list(evidence),
            'rejected_citations': list(rejected), 'verdict_evidence_ids': list(cited), 'evidence_strength': strength}


def test_the_case_file_is_usable():
    cases = load_cases()
    labels = set(Verdict.__args__)
    assert len(cases) == 30 and all(set(case['accepted']) <= labels and case['reference'] for case in cases)
    expected = [case['expected'] for case in cases]
    assert expected.count('TRUE') >= 10 and expected.count('FALSE') >= 10 and 'UNVERIFIABLE' in expected


@pytest.mark.parametrize('case,result,expected', [
    (TRUE_CASE, claim('TRUE'), 'correct'),
    (TRUE_CASE, claim('FALSE'), 'wrong'),
    (TRUE_CASE, claim('UNVERIFIABLE'), 'no_verdict'),                                  # issued, but says nothing
    (TRUE_CASE, claim('UNVERIFIABLE', 'withheld', 'citation_failed'), 'no_verdict'),   # withheld is never "wrong"
    (FALSE_CASE, claim('MISLEADING'), 'correct'),                                      # any accepted label is right
    (FALSE_CASE, claim('TRUE'), 'wrong'),
    (UNCHECKABLE, claim('UNVERIFIABLE'), 'correct'),
    (UNCHECKABLE, claim('UNVERIFIABLE', 'withheld', 'no_sources'), 'correct'),
    (UNCHECKABLE, claim('TRUE'), 'wrong'),
    (TRUE_CASE, None, 'error'),
])
def test_how_one_case_is_scored(case, result, expected):
    assert outcome(case, result) == expected


def evidence(evidence_id, url, code='verified'):
    return {'evidence_id': evidence_id, 'url': url, 'verification_code': code, 'stance': 'FOR'}


def saved(case, result, seconds=40.0, cost=0.03, what_if=None, error=None):
    return {'id': case['id'], 'report': {'claims': [result]} if result else None, 'seconds': seconds, 'cost_usd': cost,
            'usage': {'model_calls': 24, 'search_calls': 2}, 'what_if': what_if, 'error': error}


def test_the_summary_counts_what_the_brief_asks_for():
    cases = [TRUE_CASE, FALSE_CASE, UNCHECKABLE, {**TRUE_CASE, 'id': 'd'}, {**TRUE_CASE, 'id': 'e'}]
    results = {
        'a': saved(TRUE_CASE, claim('TRUE', cited=['E1', 'E2', 'E3'], strength='strong', evidence=[
            evidence('E1', 'https://www.nasa.gov/a'), evidence('E2', 'https://www.britannica.com/b'), evidence('E3', 'https://example.org/c')])),
        'b': saved(FALSE_CASE, claim('TRUE', cited=['E1'], strength='weak', evidence=[evidence('E1', 'https://example.org/x')]), seconds=60.0),
        'c': saved(UNCHECKABLE, claim('UNVERIFIABLE', 'withheld', 'no_sources'), seconds=20.0, cost=0.01),
        'd': saved(TRUE_CASE, claim('UNVERIFIABLE', 'withheld', 'citation_failed', evidence=[evidence('E1', 'https://example.org/y')],
                                    rejected=[{'verification_code': 'attribution_rejected'}, {'verification_code': 'relation_unresolved'}]),
                   seconds=80.0, what_if='TRUE'),
        'e': saved(TRUE_CASE, None, error='spending_limit'),
    }
    summary = summarize(cases, results)
    assert (summary['ran'], summary['correct'], summary['wrong'], summary['no_verdict'], summary['errors']) == (4, 2, 1, 1, 1)
    assert summary['accuracy'] == 0.5 and summary['accuracy_when_a_verdict_was_issued'] == round(2 / 3, 4)
    assert summary['no_verdict_rate'] == round(1 / 3, 4)            # the uncheckable case is not expected to get a verdict
    assert summary['withheld_reasons'] == {'citation_failed': 1, 'no_sources': 1}
    # Passages: 5 verified and 1 rejected reached the verifier; the one set aside earlier is counted apart.
    assert (summary['passages_checked_by_citation_verifier'], summary['passages_it_rejected']) == (6, 1)
    assert summary['citation_failure_rate'] == round(1 / 6, 4) and summary['passages_set_aside_before_verification'] == 1
    assert summary['cost_per_claim_usd'] == 0.025 and summary['seconds_median'] == 40.0 and summary['seconds_p95'] == 80.0
    assert (summary['verdicts_citing_three_or_more_sites'], summary['verdicts_citing_one_site'], summary['verdicts_with_sites_counted']) == (1, 1, 2)
    assert summary['source_strength'] == {'strong': 1, 'weak': 1}
    what_if = summary['if_failed_citations_were_dropped']
    assert what_if['accuracy'] == 0.75 and what_if['wrong'] == 1 and what_if['cases_that_would_change'] == [{'id': 'd', 'would_be': 'TRUE', 'right': True}]
    table = markdown(summary, 'test')
    assert '| Accuracy (right verdict) | 2 of 4 = 50% | 85% or more | not met |' in table
    assert '| Citation checks failed | 1 of 6 passages = 17% | under 5% | not met |' in table
    assert 'withheld (citation failed)' in table and '| WRONG |' in table and '| not run |' in table


def test_a_what_if_that_would_be_wrong_counts_against_the_relaxed_rule():
    results = {'a': saved(TRUE_CASE, claim('UNVERIFIABLE', 'withheld', 'citation_failed'), what_if='FALSE')}
    what_if = summarize([TRUE_CASE], results)['if_failed_citations_were_dropped']
    assert what_if['accuracy'] == 0.0 and what_if['wrong'] == 1


def test_an_empty_summary_does_not_divide_by_zero():
    summary = summarize([TRUE_CASE], {})
    assert summary['ran'] == 0 and summary['accuracy'] is None and 'n/a' in markdown(summary)


# ---- running ----------------------------------------------------------------------------------

class Provider(FakeProvider):
    made = 0

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        type(self).made += 1
        self.usage = {'input_tokens': 1000, 'output_tokens': 100, 'model_calls': 4, 'search_calls': 2}

    async def close(self):
        pass


@pytest.fixture
def model(monkeypatch):
    monkeypatch.setenv('OPENAI_MODEL', 'gpt-4.1-mini')
    Provider.made = 0


def go(tmp_path, cases, provider=Provider, max_usd='1.00', name='t'):
    said = []
    summary = asyncio.run(scorecard.run(name, max_usd, 20, cases, tmp_path, provider, said.append, fake_fetch))
    return summary, said


def test_a_run_saves_each_case_and_resumes_without_repeating(model, tmp_path):
    summary, said = go(tmp_path, [TRUE_CASE, FALSE_CASE])
    folder = tmp_path / 'scorecard-t'
    assert sorted(p.name for p in folder.glob('case-*.json')) == ['case-a.json', 'case-b.json']
    assert (summary['correct'], summary['wrong']) == (1, 1) and Provider.made == 2
    assert json.loads((folder / 'summary.json').read_text())['ran'] == 2 and 'Ran 2 of 2 cases (t).' in (folder / 'summary.md').read_text()
    assert json.loads((folder / 'case-a.json').read_text())['cost_usd'] == round(1000 * 0.0000004 + 100 * 0.0000016, 5)
    again, said = go(tmp_path, [TRUE_CASE, FALSE_CASE])
    assert Provider.made == 2 and said[0] == '2 of 2 cases already have a result; running 0.' and again['ran'] == 2


def test_no_case_starts_close_to_the_spending_cap(model, tmp_path):
    summary, said = go(tmp_path, [TRUE_CASE], max_usd='0.03')
    assert Provider.made == 0 and summary['ran'] == 0 and said[-1] == '  skipped a: too close to the spending cap'


def test_a_claim_with_one_failed_citation_is_judged_on_the_rest(model, tmp_path):
    class OneBadCitation(Provider):
        async def structured(self, schema, instructions, data):
            if schema is Analysis:
                return Analysis(verdict='TRUE', evidence=[DRAFT, DRAFT], limitations=[])
            if schema is CitationJudgment and self.verifier_calls:
                self.verifier_calls += 1
                return CitationJudgment(supports_attribution=False, stance_matches=True, opposite_stance=False, reason='Does not say that.')
            return await super().structured(schema, instructions, data)
    summary, _ = go(tmp_path, [TRUE_CASE], OneBadCitation)
    row = summary['results'][0]
    assert row['outcome'] == 'correct' and row['withheld_reason'] is None
    assert summary['if_failed_citations_were_dropped']['cases_that_would_change'] == []
    assert summary['passages_it_rejected'] == 1 and summary['passages_checked_by_citation_verifier'] == 2


def test_a_case_stopped_by_a_provider_error_is_run_again(model, tmp_path):
    class Down(Provider):
        async def search(self, query):
            from tools.providers import ProviderFailure
            raise ProviderFailure('down')
        async def structured(self, schema, instructions, data):
            from tools.providers import ProviderFailure
            raise ProviderFailure('down')
    summary, _ = go(tmp_path, [TRUE_CASE], Down)
    assert summary['errors'] == 1 and summary['ran'] == 0
    summary, said = go(tmp_path, [TRUE_CASE])
    assert said[0] == '0 of 1 cases already have a result; running 1.' and summary['correct'] == 1


def test_a_claim_withheld_for_a_provider_failure_is_run_again(model, tmp_path):
    class AnalysisDown(Provider):
        async def structured(self, schema, instructions, data):
            if schema is Analysis:
                from tools.providers import ProviderFailure
                raise ProviderFailure('The model provider was limiting requests.')
            return await super().structured(schema, instructions, data)
    summary, _ = go(tmp_path, [TRUE_CASE], AnalysisDown)
    assert summary['results'][0]['withheld_reason'] == 'provider_failure' and summary['no_verdict'] == 1
    summary, said = go(tmp_path, [TRUE_CASE])
    assert said[0] == '0 of 1 cases already have a result; running 1.' and summary['correct'] == 1 and summary['no_verdict'] == 0


def test_run_names_and_caps_are_checked(model, tmp_path):
    with pytest.raises(ValueError):
        go(tmp_path, [TRUE_CASE], name='Bad Name')
    with pytest.raises(ValueError):
        go(tmp_path, [TRUE_CASE], max_usd='5.00')
