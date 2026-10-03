"""Every agent choosing its own steps, and the limits the application keeps whatever they choose. Offline."""
import asyncio
import json
from collections import defaultdict
from pathlib import Path
import pytest
from agents import analyst_agent, citation_verifier, claim_extractor, content_extractor, orchestrator, research_agent, verdict_agent
from agents.content_extractor import ContentExtractorAgent, merge_screen_text
from agents.orchestrator import research_all, research_claim, run_pipeline
from agents.runtime import AGENTS, autonomous
from schemas import (Analysis, AnalystAction, AtomicClaim, CitationJudgment, ClaimResult, ContentAction, EvidenceRelation,
                     Extraction, ExtractionCoverage, ExtractorAction, OrchestratorAction,
                     ResearchAction, ScreenText, VerdictAction, VerdictDecision, VerifierAction)
from services import media
from services.article import run_article_pipeline
from services.budget import BudgetExceeded
from services.excerpts import source_excerpts
from services.providers import ProviderFailure
from services.transcribe import Transcript
from tests.test_pipeline import CLAIM, DRAFT, PAGE, FakeProvider

ACTIONS = (OrchestratorAction, ResearchAction, AnalystAction, VerifierAction, VerdictAction, ExtractorAction, ContentAction)
SECOND = DRAFT.model_copy(update={'excerpt_id': 'S1:E2', 'statement': 'The sample was limited to one room.'})
LONG_PAGE = PAGE + ' ' + 'Unrelated filler sentence about the laboratory building. ' * 80


def go(tool, instruction='', looking_for='supporting', reason='Next.'):
    return OrchestratorAction(tool=tool, instruction=instruction, looking_for=looking_for, reason=reason)


def verdict(value='TRUE', ids=('E1',)):
    return VerdictAction(tool='issue_verdict', verdict=value, evidence_ids=list(ids), missing='', looking_for='supporting')


def ask(missing, looking_for='supporting'):
    return VerdictAction(tool='request_evidence', verdict='UNVERIFIABLE', evidence_ids=[], missing=missing, looking_for=looking_for)


def judge(tool, attribution=True, stance=True):
    return VerifierAction(tool=tool, supports_attribution=attribution, stance_matches=stance, reason='Scripted.')


ROUTE = [go('research_agent'), go('analyst_agent'), go('citation_verifier'), go('verdict_agent')]


class Team(FakeProvider):
    """Scripted answers for every agent. `mode` says which agents plan their own steps."""
    def __init__(self, mode, script=(), analyses=(), relations=(), page=PAGE, **kwargs):
        super().__init__(**kwargs)
        self.agent_mode, self.page = mode, page
        self.script = defaultdict(list)
        for action in script:   # an action, or (schema, exception) to make that agent's call fail
            schema, action = action if isinstance(action, tuple) else (type(action), action)
            self.script[schema].append(action)
        self.analyses, self.relations = list(analyses), list(relations)
        self.seen = defaultdict(list)   # schema -> every input that agent was shown
        self.calls = []                 # schema names, in order

    async def search(self, query):
        self.queries.append(query)
        return [{'url': f'https://example.org/page-{len(self.queries)}', 'title': f'Result {len(self.queries)}'}]

    async def fetch(self, url):
        return url, self.page

    async def structured(self, schema, instructions, data):
        self.calls.append(schema.__name__)
        if schema in ACTIONS:
            self.seen[schema].append(json.loads(data))
            if not self.script[schema]:
                raise AssertionError(f'No scripted {schema.__name__} left')
            action = self.script[schema].pop(0)
            if isinstance(action, BaseException):
                raise action
            return action
        if schema is Analysis:
            self.seen[schema].append(json.loads(data))
            return Analysis(verdict=self.verdict, evidence=self.analyses.pop(0) if self.analyses else [self.draft], limitations=[])
        if schema is EvidenceRelation and self.relations:
            return EvidenceRelation(relation=self.relations.pop(0), reason='Scripted relation')
        if schema in (VerdictDecision, CitationJudgment):
            self.seen[schema].append(json.loads(data))
        return await super().structured(schema, instructions, data)


def run(provider, claim=CLAIM):
    return asyncio.run(research_claim(claim, provider, provider.fetch))


def lines(result, agent):
    return [line.removeprefix(f'{agent}: ') for line in result.agent_steps if line.startswith(f'{agent}: ')]


# ---- Orchestrator -------------------------------------------------------------------------------

def test_the_orchestrator_chooses_which_agent_works_next():
    provider = Team('orchestrator', ROUTE)
    result = run(provider)
    assert result.verdict == 'TRUE' and result.verdict_state == 'issued'
    assert provider.calls == ['OrchestratorAction', 'OrchestratorAction', 'Analysis', 'EvidenceRelation',
                              'OrchestratorAction', 'CitationJudgment', 'OrchestratorAction', 'VerdictDecision']
    assert result.agent_steps == ['Orchestrator: Sent the claim to the Research Agent.',
                                  'Orchestrator: Sent 2 page(s) to the Analyst Agent.',
                                  'Orchestrator: Sent 1 passage(s) to the Citation Verifier.',
                                  'Orchestrator: Asked the Verdict Agent for a verdict.']


def test_the_orchestrator_sees_progress_but_never_page_text():
    provider = Team('orchestrator', ROUTE)
    run(provider)
    first, second, third, fourth = provider.seen[OrchestratorAction]
    assert set(first['tools']) == {'research_agent', 'analyst_agent', 'citation_verifier', 'verdict_agent', 'finish'}
    assert first['progress']['research_rounds'] == 0 and first['last_step'] == ''
    assert second['progress']['pages_waiting_for_analysis'] == 2 and second['last_step'] == 'research_agent read 2 page(s).'
    assert third['progress']['passages_waiting_for_verification'] == 1
    assert fourth['progress']['verified_evidence'] == {'supporting': 1, 'contradicting': 0, 'background': 0}
    assert fourth['last_step'] == 'citation_verifier verified 1 of 1 passage(s).'
    assert 'sensor measured 12 units during' not in json.dumps(provider.seen[OrchestratorAction])


@pytest.mark.parametrize('script', [
    [go('verdict_agent'), go('finish', reason='Nothing else to do.')],
    [go('research_agent'), go('verdict_agent'), go('citation_verifier'), go('finish')],
    [go('finish')],
])
def test_no_route_can_skip_a_stage(script):
    provider = Team('orchestrator', script)
    result = run(provider)
    # Whatever was chosen, pages were analysed, the passage was verified and only then was a verdict given.
    assert result.verdict == 'TRUE' and provider.verifier_calls == 1 and len(provider.queries) == 2
    assert provider.calls.index('CitationJudgment') < provider.calls.index('VerdictDecision')


def test_out_of_order_choices_are_refused_with_a_reason():
    provider = Team('orchestrator', [go('analyst_agent'), go('verdict_agent'), go('research_agent'),
                                     go('citation_verifier'), go('verdict_agent'), go('finish')])
    run(provider)
    feedback = [state['last_step'] for state in provider.seen[OrchestratorAction]]
    assert feedback[1] == 'analyst_agent refused: there are no pages yet. Call research_agent first.'
    assert feedback[2] == 'verdict_agent refused: research, analysis and citation verification must finish first.'
    assert feedback[4] == 'citation_verifier refused: no passages are waiting to be verified.'
    assert feedback[5] == 'verdict_agent refused: research, analysis and citation verification must finish first.'


def test_the_orchestrator_sends_a_claim_back_for_a_second_research_round():
    context = DRAFT.model_copy(update={'stance': 'CONTEXT'})
    new_page = DRAFT.model_copy(update={'source_id': 'S3', 'excerpt_id': 'S3:E1'})
    provider = Team('orchestrator', ROUTE + [go('research_agent', 'an official test report with the reading'),
                                             go('analyst_agent'), go('citation_verifier'), go('verdict_agent')],
                    analyses=[[context], [new_page]], relations=['BACKGROUND', 'SUPPORTS'])
    result = run(provider)
    states = provider.seen[OrchestratorAction]
    assert states[4]['last_step'].startswith('verdict_agent refused: no verified passage directly supports or contradicts')
    assert provider.queries[2] == f'{CLAIM.text} an official test report with the reading'
    assert states[5]['last_step'] == 'research_agent read 1 new page(s).' and states[5]['remaining']['research_rounds'] == 0
    # The second analysis saw only the new page, and its passage went through the same checks.
    assert [s['id'] for s in provider.seen[Analysis][1]['sources']] == ['S3']
    assert result.verdict == 'TRUE' and result.sources_checked == 3 and provider.verifier_calls == 2
    assert [e.stance for e in result.evidence] == ['CONTEXT', 'FOR']
    assert 'Sent the claim back to the Research Agent for supporting evidence: an official test report with the reading' in lines(result, 'Orchestrator')
    assert 'Searched for the missing supporting evidence (1 new result(s)).' in lines(result, 'Research Agent')


def test_only_one_second_round_and_only_with_a_stated_gap():
    context = DRAFT.model_copy(update={'stance': 'CONTEXT'})
    provider = Team('orchestrator', [go('research_agent'), go('research_agent', 'too early'), go('analyst_agent'),
                                     go('citation_verifier'), go('research_agent'), go('research_agent', 'a primary source'),
                                     go('research_agent', 'another source'), go('finish')],
                    analyses=[[context], []], relations=['BACKGROUND'])
    result = run(provider)
    feedback = [state['last_step'] for state in provider.seen[OrchestratorAction]]
    assert feedback[2] == 'research_agent refused: pages or passages from the last round are still waiting. Analyse and verify them first.'
    assert feedback[5] == 'research_agent refused: say in instruction what evidence is still missing.'
    assert feedback[6] == 'research_agent read 1 new page(s).'
    assert feedback[7] == 'research_agent refused: this claim has already had its second research round.'
    assert len(provider.queries) == 3 and result.withheld_reason == 'no_relevant_evidence'


def test_a_second_round_is_refused_when_time_is_short(monkeypatch):
    monkeypatch.setattr(orchestrator, 'MIN_ROUND_SECONDS', orchestrator.CLAIM_TIMEOUT_SECONDS + 1)
    provider = Team('orchestrator', ROUTE[:3] + [go('research_agent', 'a primary source'), go('verdict_agent')])
    result = run(provider)
    assert provider.seen[OrchestratorAction][4]['last_step'] == 'research_agent refused: too little of the time limit is left for another round.'
    assert len(provider.queries) == 2 and result.verdict == 'TRUE'


def test_a_failed_second_round_search_does_not_undo_the_first_round():
    class Flaky(Team):
        async def search(self, query):
            if len(self.queries) == 2:
                self.queries.append(query)
                raise ProviderFailure('Search request failed; this direction was not fully researched.')
            return await super().search(query)
    provider = Flaky('orchestrator', ROUTE[:3] + [go('research_agent', 'an official report'), go('verdict_agent')])
    result = run(provider)
    assert provider.seen[OrchestratorAction][4]['last_step'] == 'research_agent found no new readable pages.'
    assert result.verdict == 'TRUE' and result.supporting_search == research_agent.RETRIEVED
    assert 'A later search for supporting evidence failed; the earlier results were kept.' in result.limitations


def test_a_first_search_that_fails_can_be_repaired_by_a_second_round():
    class Flaky(Team):
        async def search(self, query):
            if len(self.queries) == 1:
                self.queries.append(query)
                raise ProviderFailure('Search request failed; this direction was not fully researched.')
            return await super().search(query)
    provider = Flaky('orchestrator', ROUTE + [go('research_agent', 'evidence against the reading', 'contradicting'),
                                              go('analyst_agent'), go('verdict_agent')], analyses=[[DRAFT], []])
    result = run(provider)
    assert provider.seen[OrchestratorAction][4]['last_step'].startswith('verdict_agent refused: a search failed')
    assert result.contradicting_search == research_agent.RETRIEVED and result.verdict == 'TRUE' and result.sources_checked == 2

    # Without the second round the same failure still withholds the verdict.
    provider = Flaky('orchestrator', ROUTE + [go('finish')])
    assert run(provider).withheld_reason == 'search_failed'


def test_second_round_pages_that_cannot_be_analysed_are_left_out():
    class Flaky(Team):
        async def structured(self, schema, instructions, data):
            if schema is Analysis and len(self.seen[Analysis]) == 1:
                raise ProviderFailure('Model unavailable')
            return await super().structured(schema, instructions, data)
    provider = Flaky('orchestrator', ROUTE[:3] + [go('research_agent', 'an official report'), go('analyst_agent'), go('verdict_agent')])
    result = run(provider)
    assert result.verdict == 'TRUE' and len(result.evidence) == 1 and result.sources_checked == 3
    assert 'Pages from the second research round could not be analysed, so they were not used.' in result.limitations


def test_a_second_round_reads_at_most_three_more_pages():
    class Wide(Team):
        async def search(self, query):
            self.queries.append(query)
            return [{'url': f'https://example.org/page-{len(self.queries)}-{i}', 'title': 'Result'}
                    for i in range(1 if len(self.queries) < 3 else 3)]
    def search(query, looking_for='supporting'):
        return ResearchAction(tool='search_web', query=query, looking_for=looking_for, result_ids=[], reason='')
    def read(*ids):
        return ResearchAction(tool='read_pages', query='', looking_for='supporting', result_ids=list(ids), reason='')
    done = ResearchAction(tool='finish', query='', looking_for='supporting', result_ids=[], reason='Done.')
    provider = Wide('orchestrator,research',
                    ROUTE[:3] + [go('research_agent', 'an official report'), go('analyst_agent'), go('verdict_agent')]
                    + [search('a'), read('R1'), search('b', 'contradicting'), read('R2'), done]
                    + [search('c'), read('R3', 'R4', 'R5'), search('d'), read('R6', 'R7', 'R8')], analyses=[[DRAFT], []])
    result = run(provider)
    follow_up = provider.seen[ResearchAction][5]
    assert follow_up['request'] == 'an official report' and follow_up['remaining'] == {'searches': 3, 'pages': 3, 'steps': 4}
    assert result.sources_checked == 2 + research_agent.FOLLOW_UP_PAGES
    # The last read was refused: the round's page budget was already spent.
    assert lines(result, 'Research Agent')[-2:] == ['Searched for supporting evidence: "d" (3 new result(s)).',
                                                    'Stopped: the step budget for this claim was spent.']


def test_a_second_round_that_cannot_be_planned_still_reads_what_it_found():
    def search(query, looking_for='supporting'):
        return ResearchAction(tool='search_web', query=query, looking_for=looking_for, result_ids=[], reason='')
    def read(*ids):
        return ResearchAction(tool='read_pages', query='', looking_for='supporting', result_ids=list(ids), reason='')
    done = ResearchAction(tool='finish', query='', looking_for='supporting', result_ids=[], reason='Done.')
    provider = Team('orchestrator,research',
                    ROUTE[:3] + [go('research_agent', 'an official report'), go('analyst_agent'), go('verdict_agent')]
                    + [search('a'), read('R1'), search('b', 'contradicting'), read('R2'), done,
                       search('c'), (ResearchAction, ProviderFailure('Model unavailable'))], analyses=[[DRAFT], []])
    result = run(provider)
    assert provider.queries == ['a', 'b', 'c'] and result.sources_checked == 3
    assert any('could not plan all of its second round' in text for text in result.limitations)


def test_a_failed_citation_still_withholds_the_verdict():
    provider = Team('orchestrator', ROUTE + [go('finish', reason='A citation failed.')], approved=False)
    result = run(provider)
    assert provider.seen[OrchestratorAction][4]['last_step'].startswith('verdict_agent refused: a citation failed verification')
    assert provider.seen[OrchestratorAction][4]['progress']['citations_failed'] == 1
    assert result.withheld_reason == 'citation_failed' and 'VerdictDecision' not in provider.calls
    assert lines(result, 'Orchestrator')[-1] == 'Stopped directing: A citation failed.'


def test_when_the_orchestrator_cannot_plan_the_standard_order_runs():
    provider = Team('orchestrator', [(OrchestratorAction, ProviderFailure('Model unavailable'))])
    result = run(provider)
    assert result.verdict == 'TRUE' and provider.verifier_calls == 1
    assert result.agent_steps == ['Orchestrator: Could not choose the next agent, so the remaining stages ran in the standard order.']


def test_running_out_of_steps_runs_the_standard_order():
    provider = Team('orchestrator', [go('verdict_agent')] * orchestrator.MAX_STEPS)
    result = run(provider)
    assert result.verdict == 'TRUE' and len(provider.seen[OrchestratorAction]) == orchestrator.MAX_STEPS
    assert lines(result, 'Orchestrator')[-1] == 'Used all of its steps, so the remaining stages ran in the standard order.'


def test_a_spending_limit_while_directing_stops_the_claim():
    provider = Team('orchestrator', [go('research_agent'), (OrchestratorAction, BudgetExceeded("Today's spending limit has been reached."))])
    [result] = asyncio.run(research_all([CLAIM], provider, provider.fetch))
    assert result.withheld_reason == 'spending_limit' and result.verdict == 'UNVERIFIABLE'


# ---- Verdict Agent ------------------------------------------------------------------------------

def test_the_verdict_agent_sees_only_the_claim_and_verified_evidence():
    provider = Team('verdict', [verdict()])
    result = run(provider)
    [state] = provider.seen[VerdictAction]
    assert set(state) == {'target_assertion', 'verified_evidence', 'tools', 'last_step'}
    assert set(state['tools']) == {'issue_verdict'}   # No research round can follow, so asking is not offered.
    assert result.verdict == 'TRUE' and result.verdict_evidence_ids == ['E1']


def test_the_verdict_agent_can_ask_for_more_evidence_and_the_orchestrator_follows_up():
    new_page = DRAFT.model_copy(update={'source_id': 'S3', 'excerpt_id': 'S3:E1'})
    provider = Team('orchestrator,verdict',
                    ROUTE + [go('research_agent', 'an official measurement'), go('analyst_agent'), go('citation_verifier'),
                             go('verdict_agent')] + [ask('an official measurement'), verdict(ids=('E1', 'E2'))],
                    analyses=[[DRAFT], [new_page]])
    result = run(provider)
    first, second = provider.seen[VerdictAction]
    assert set(first['tools']) == {'issue_verdict', 'request_evidence'} and set(second['tools']) == {'issue_verdict'}
    assert len(first['verified_evidence']) == 1 and len(second['verified_evidence']) == 2
    asked = provider.seen[OrchestratorAction][4]
    assert asked['last_step'] == 'verdict_agent asked for more supporting evidence before deciding: an official measurement'
    assert asked['progress']['verdict_agent_request'] == 'an official measurement'
    assert result.verdict == 'TRUE' and result.verdict_evidence_ids == ['E1', 'E2'] and result.sources_checked == 3
    assert lines(result, 'Verdict Agent') == ['Asked for more evidence before deciding: an official measurement']


def test_a_request_that_cannot_be_met_is_refused_and_a_verdict_is_given():
    provider = Team('verdict', [ask('more data'), verdict()])
    result = run(provider)
    assert provider.seen[VerdictAction][1]['last_step'].startswith('request_evidence refused: no further research is possible')
    assert result.verdict == 'TRUE' and len(provider.queries) == 2


def test_an_unanswered_request_ends_in_a_verdict_from_the_evidence_in_hand():
    provider = Team('orchestrator,verdict', ROUTE + [go('finish', reason='No time.')] + [ask('more data'), verdict()])
    result = run(provider)
    assert result.verdict == 'TRUE' and len(provider.queries) == 2
    assert set(provider.seen[VerdictAction][1]['tools']) == {'issue_verdict'}


def test_a_citation_slip_can_be_corrected_but_only_with_the_same_verdict():
    provider = Team('verdict', [verdict(ids=('E9',)), verdict(ids=('E1',))])
    result = run(provider)
    assert provider.seen[VerdictAction][1]['last_step'].startswith('issue_verdict refused (unknown evidence ids)')
    assert result.verdict == 'TRUE' and result.verdict_state == 'issued'
    assert lines(result, 'Verdict Agent') == ['Cited its evidence again after a refusal; the verdict was then accepted.']

    provider = Team('verdict', [verdict('TRUE', ids=()), verdict('MISLEADING', ids=('E1',))])
    result = run(provider)
    assert result.verdict == 'UNVERIFIABLE' and result.withheld_reason == 'missing_evidence_ids'
    assert result.decision_verdict == 'TRUE'
    assert lines(result, 'Verdict Agent') == ['Changed its verdict after a refusal, so the first refusal stands.']


def test_a_verdict_the_evidence_does_not_allow_is_withheld_without_a_second_try():
    against = DRAFT.model_copy(update={'stance': 'AGAINST', 'excerpt_id': 'S1:E2', 'statement': 'The sample was limited to one room.'})
    provider = Team('verdict', [verdict('TRUE', ids=('E1',)), verdict('MISLEADING', ids=('E1', 'E2'))],
                    analyses=[[DRAFT, against]], relations=['SUPPORTS', 'CONTRADICTS'])
    result = run(provider)
    assert result.withheld_reason == 'conflicting_evidence' and len(provider.seen[VerdictAction]) == 1


def test_a_verdict_agent_that_cannot_answer_withholds_the_verdict():
    result = run(Team('verdict', [(VerdictAction, ProviderFailure('Model unavailable'))]))
    assert result.withheld_reason == 'verdict_check_unavailable'


def test_a_refusal_the_agent_cannot_correct_keeps_the_first_answer():
    provider = Team('verdict', [verdict(ids=('E9',)), (VerdictAction, ProviderFailure('Model unavailable'))])
    result = run(provider)
    assert result.withheld_reason == 'unknown_evidence_ids' and result.decision_verdict == 'TRUE'
    assert result.decision_evidence_ids == ['E9']


def test_a_verdict_its_evidence_does_not_support_is_not_coached():
    provider = Team('verdict', [verdict('FALSE', ids=('E1',)), verdict('TRUE', ids=('E1',))])
    result = run(provider)
    assert result.withheld_reason == 'evidence_stance_mismatch' and len(provider.seen[VerdictAction]) == 1


@pytest.mark.parametrize('mode,schema', [('verdict', VerdictAction), ('citation_verifier', VerifierAction), ('analyst', AnalystAction),
                                         ('research', ResearchAction)])
def test_a_spending_limit_inside_any_agent_stops_the_claim(mode, schema):
    provider = Team(mode, [(schema, BudgetExceeded("Today's spending limit has been reached."))], page=LONG_PAGE,
                    relations=['IRRELEVANT'] if mode == 'analyst' else [])
    [result] = asyncio.run(research_all([CLAIM], provider, provider.fetch))
    assert result.withheld_reason == 'spending_limit' and result.verdict == 'UNVERIFIABLE'


def test_rejected_passages_stay_in_the_order_they_were_selected():
    provider = Team('fixed', analyses=[[DRAFT, SECOND]], relations=['SUPPORTS', 'IRRELEVANT'], approved=False)
    result = run(provider)
    assert [c.verification_code for c in result.rejected_citations] == ['attribution_rejected', 'relation_unresolved']
    assert result.agent_steps == []


def test_reports_saved_under_the_earlier_field_name_still_load():
    saved = run(Team('fixed')).model_dump()
    saved['research_steps'] = [saved.pop('agent_steps'), 'Finished: Both sides are covered.'][1:]
    assert ClaimResult.model_validate(saved).agent_steps == ['Research Agent: Finished: Both sides are covered.']


# ---- Citation Verifier --------------------------------------------------------------------------

def test_the_verifier_first_sees_the_passage_and_may_decide_from_it():
    provider = Team('citation_verifier', [judge('accept')], page=LONG_PAGE)
    result = run(provider)
    [state] = provider.seen[VerifierAction]
    assert 'page' not in state and PAGE in state['passage'] and len(state['passage']) < len(LONG_PAGE)
    assert set(state['tools']) == {'accept', 'reject', 'read_full_page'}
    assert result.evidence[0].verified and result.verdict == 'TRUE' and 'CitationJudgment' not in provider.calls


def test_the_verifier_can_read_the_full_page_before_deciding():
    provider = Team('citation_verifier', [judge('read_full_page', False, False), judge('accept')], page=LONG_PAGE)
    result = run(provider)
    first, second = provider.seen[VerifierAction]
    assert second['page'] == LONG_PAGE and 'passage' not in second and set(second['tools']) == {'accept', 'reject'}
    assert second['last_step'] == 'read_full_page: the whole page is now in "page". Accept or reject.'
    assert result.evidence[0].verified
    assert lines(result, 'Citation Verifier') == ['Read the full page of S1 before deciding on a passage.']


@pytest.mark.parametrize('script', [
    [judge('reject', True, False)],
    [judge('accept', True, False)],                       # 'accept' with a failed check is not an acceptance
    [judge('read_full_page', False, False), judge('read_full_page', False, False)],
    [judge('read_full_page', False, False), judge('reject', False, True)],
])
def test_anything_short_of_a_clear_acceptance_rejects_the_citation(script):
    result = run(Team('citation_verifier', script, page=LONG_PAGE))
    assert result.evidence == [] and result.rejected_citations[0].verification_code == 'attribution_rejected'
    assert result.withheld_reason == 'citation_failed'


def test_the_passage_shown_is_the_one_around_the_selected_excerpt():
    sentence = PAGE.split('. ')[0] + '.'
    page = LONG_PAGE + ' ' + sentence
    from schemas import Source
    last = source_excerpts(Source(id='S1', title='', url='https://example.org', text=page, retrieved_at=''))[-1]
    assert last.text == sentence and last.start > len(LONG_PAGE)
    provider = Team('citation_verifier', [judge('accept')], page=page, analyses=[[DRAFT.model_copy(update={'excerpt_id': last.id})]])
    result = run(provider)
    passage = provider.seen[VerifierAction][0]['passage']
    assert passage.endswith(sentence) and not passage.startswith(sentence) and result.evidence[0].source_start == last.start


def test_a_short_page_is_judged_in_full_in_one_step():
    provider = Team('citation_verifier')
    result = run(provider)
    assert provider.seen[CitationJudgment][0]['page'] == PAGE and not provider.seen[VerifierAction]
    assert result.verdict == 'TRUE'


def test_a_verifier_that_cannot_answer_does_not_verify():
    result = run(Team('citation_verifier', [(VerifierAction, ProviderFailure('Model unavailable'))], page=LONG_PAGE))
    assert result.rejected_citations[0].verification_code == 'check_unavailable' and result.withheld_reason == 'citation_failed'


# ---- Analyst Agent ------------------------------------------------------------------------------

def replace(*selections):
    return AnalystAction(tool='select_evidence', evidence=list(selections), reason='Better passages exist.')


def test_the_analyst_replaces_selections_the_relation_check_set_aside():
    provider = Team('analyst', [replace(SECOND)], relations=['IRRELEVANT', 'SUPPORTS'])
    result = run(provider)
    [state] = provider.seen[AnalystAction]
    assert state['kept'] == [] and state['set_aside'] == [{'excerpt_id': 'S1:E1', 'why': 'Scripted relation'}]
    assert set(state['tools']) == {'select_evidence', 'finish'} and state['sources'][0]['excerpts'][1]['id'] == 'S1:E2'
    assert [e.excerpt_id for e in result.evidence] == ['S1:E2'] and result.verdict == 'TRUE'
    assert result.rejected_citations[0].verification_code == 'relation_unresolved'


def test_replacements_face_the_same_relation_check_and_cannot_repeat_a_selection():
    provider = Team('analyst', [replace(DRAFT, SECOND)], relations=['IRRELEVANT', 'UNCERTAIN'])
    result = run(provider)
    # DRAFT was already set aside, so only SECOND was checked, and it was set aside too.
    assert provider.calls.count('EvidenceRelation') == 2 and result.evidence == []
    assert result.withheld_reason == 'no_relevant_evidence' and len(result.rejected_citations) == 2


def test_the_same_replacement_is_checked_once():
    provider = Team('analyst', [AnalystAction.model_construct(tool='select_evidence', evidence=[SECOND, SECOND, SECOND], reason='')],
                    relations=['IRRELEVANT', 'SUPPORTS'])
    result = run(provider)
    assert provider.calls.count('EvidenceRelation') == 2 and len(result.evidence) == 1


def test_replacements_are_capped():
    page = PAGE + ' A third sentence follows. A fourth sentence ends the page.'
    many = [DRAFT.model_copy(update={'source_id': source, 'excerpt_id': f'{source}:E{i}'}) for source in ('S1', 'S2') for i in (2, 3, 4)]
    provider = Team('analyst', [AnalystAction.model_construct(tool='select_evidence', evidence=many, reason='')],
                    relations=['IRRELEVANT'] + ['SUPPORTS'] * len(many), page=page)
    result = run(provider)
    assert provider.calls.count('EvidenceRelation') == 1 + analyst_agent.MAX_REPLACEMENTS
    assert [e.excerpt_id for e in result.evidence] == ['S1:E2', 'S1:E3', 'S1:E4']


def test_the_analyst_may_decide_nothing_else_fits():
    provider = Team('orchestrator,analyst', ROUTE[:3] + [go('finish')] + [AnalystAction(tool='finish', evidence=[], reason='None.')],
                    relations=['IRRELEVANT'])
    result = run(provider)
    assert result.withheld_reason == 'no_relevant_evidence'
    assert lines(result, 'Analyst Agent') == ['Selected 1 passage(s) from 2 page(s); the relation check set 1 aside.',
                                              'Reviewed its selections and found no other passage that bears directly on the claim.']


def test_the_analyst_is_not_asked_again_when_enough_direct_evidence_remains():
    third = DRAFT.model_copy(update={'source_id': 'S2', 'excerpt_id': 'S2:E1'})
    provider = Team('analyst', analyses=[[DRAFT, SECOND, third]], relations=['SUPPORTS', 'IRRELEVANT', 'SUPPORTS'])
    result = run(provider)
    assert not provider.seen[AnalystAction] and len(result.evidence) == 2


def test_an_analyst_that_cannot_review_keeps_its_first_selection():
    provider = Team('analyst', [(AnalystAction, ProviderFailure('Model unavailable'))], analyses=[[DRAFT, SECOND]], relations=['SUPPORTS', 'IRRELEVANT'])
    result = run(provider)
    assert result.verdict == 'TRUE' and len(result.evidence) == 1


# ---- Claim Extractor ----------------------------------------------------------------------------

TEXT = 'The sensor measured 12 units, so it works in every room.'
GOOD = Extraction(intent='FACTUAL', omitted_claims=False, note='', claims=[
    AtomicClaim(text='The sensor measured 12 units', context=TEXT), AtomicClaim(text='it works in every room.', context=TEXT)])
PARTIAL = Extraction(intent='FACTUAL', omitted_claims=False, note='', claims=[AtomicClaim(text='The sensor measured 12 units', context=TEXT)])


class Extracting(Team):
    """First extraction is PARTIAL; the coverage audit fails anything that is not word-for-word complete."""
    def __init__(self, mode, script=(), first=PARTIAL, **kwargs):
        super().__init__(mode, script, **kwargs)
        self.first = first

    async def structured(self, schema, instructions, data):
        if schema is Extraction:
            self.calls.append('Extraction')
            return self.first
        if schema is ExtractionCoverage:
            self.calls.append('ExtractionCoverage')
            return ExtractionCoverage(complete=False, issues=['"so it works in every room" is missing.'])
        return await super().structured(schema, instructions, data)


def revise(extraction, tool='revise'):
    return ExtractorAction(tool=tool, extraction=extraction, reason='Scripted.')


def test_the_claim_extractor_revises_an_extraction_the_check_refused():
    provider = Extracting('claim_extractor', [revise(GOOD)])
    report = asyncio.run(run_pipeline(TEXT, provider, provider.fetch))
    [state] = provider.seen[ExtractorAction]
    assert state['submission'] == TEXT and state['problems'] == ['"so it works in every room" is missing.']
    assert state['previous_extraction']['claims'][0]['text'] == 'The sensor measured 12 units'
    assert report.coverage_status == 'passed' and [c.claim for c in report.claims] == [c.text for c in GOOD.claims]
    assert report.agent_steps == ['Claim Extractor: Revised its extraction after the coverage check found a problem; the revision passed the check.']
    assert [span.text for span in report.input_spans] == [c.text for c in GOOD.claims]


@pytest.mark.parametrize('script,step', [
    ([revise(PARTIAL)], 'Revised its extraction after the coverage check found a problem; the revision did not pass the check.'),
    ([revise(GOOD, 'keep')], 'Kept its extraction after the coverage check found a problem.'),
    ([(ExtractorAction, ProviderFailure('Model unavailable'))], 'Kept its extraction after the coverage check found a problem.'),
])
def test_a_revision_must_pass_the_same_check(script, step):
    provider = Extracting('claim_extractor', script)
    report = asyncio.run(run_pipeline(TEXT, provider, provider.fetch))
    assert report.coverage_status == 'incomplete' and not provider.queries
    assert report.claims[0].withheld_reason == 'coverage_failed' and report.agent_steps == [f'Claim Extractor: {step}']


def test_in_fixed_mode_the_claim_extractor_is_not_asked_again():
    provider = Extracting('fixed')
    report = asyncio.run(run_pipeline(TEXT, provider, provider.fetch))
    assert report.coverage_status == 'incomplete' and report.agent_steps == [] and not provider.seen[ExtractorAction]


ARTICLE_URL = 'https://news.example.org/story'
ARTICLE = 'Officials said the fictional Harbor Bridge closed on March 3. Repairs are expected to take eight months.'
EXACT = AtomicClaim(text='Repairs are expected to take eight months', context='')
SLIP = AtomicClaim(text='The Harbor Bridge was closed on March 3', context='')
FIXED = AtomicClaim(text='the fictional Harbor Bridge closed on March 3', context='')


def article(provider):
    async def fetch(url):
        return (url, ARTICLE) if url == ARTICLE_URL else (url, PAGE)
    return asyncio.run(run_article_pipeline(ARTICLE_URL, provider, fetch))


def selection(*claims):
    return Extraction(intent='FACTUAL', claims=list(claims), omitted_claims=False, note='')


def test_claims_not_copied_word_for_word_get_a_second_attempt():
    provider = Extracting('claim_extractor', [revise(selection(EXACT, FIXED))], first=selection(EXACT, SLIP))
    report = article(provider)
    assert provider.seen[ExtractorAction][0]['problems'] == [f'Not a word-for-word copy of the text: {SLIP.text}']
    assert report.coverage_status == 'passed' and [c.claim for c in report.claims] == [EXACT.text, FIXED.text]
    assert all(c.withheld_reason != 'coverage_failed' for c in report.claims)
    assert report.agent_steps == ['Claim Extractor: Revised its claims after 1 were not copied word for word; 2 of 2 are now exact copies.']


@pytest.mark.parametrize('second', [selection(EXACT), selection(EXACT, SLIP), selection(SLIP, SLIP)])
def test_a_revision_cannot_pass_by_dropping_the_claim(second):
    provider = Extracting('claim_extractor', [revise(second)], first=selection(EXACT, SLIP))
    report = article(provider)
    # The first extraction stands: the inexact claim is still shown, refused, and never researched.
    assert [c.claim for c in report.claims] == [EXACT.text, SLIP.text] and report.coverage_status == 'incomplete'
    assert report.claims[1].withheld_reason == 'coverage_failed'
    assert report.agent_steps == ['Claim Extractor: 1 claim(s) were not copied word for word, and its review did not correct them.']


# ---- Content Extractor --------------------------------------------------------------------------

class Screens(Team):
    def __init__(self, mode, script=(), screens=()):
        super().__init__(mode, script)
        self.screens, self.image_batches = list(screens), []

    async def read_images(self, schema, instructions, images):
        self.image_batches.append(len(images))
        return ScreenText(text=self.screens.pop(0))


class Speech:
    async def transcribe(self, audio):
        return Transcript(text='', language=None)


@pytest.fixture
def fake_media(monkeypatch):
    """Stand-ins for ffmpeg, so these run wherever the tests run."""
    async def probe(path):
        return media.MediaInfo(duration=40.0, has_audio=False, format_name='mp4')
    async def extract_keyframes(path, workdir, duration, count=media.KEYFRAMES):
        frames = [Path(workdir) / f'frame-{i}.jpg' for i in range(count)]
        for frame in frames:
            frame.write_bytes(b'\xff\xd8 fake frame')
        return frames
    monkeypatch.setattr(media, 'probe', probe)
    monkeypatch.setattr(media, 'extract_keyframes', extract_keyframes)


def extract(provider, tmp_path):
    return asyncio.run(ContentExtractorAgent(provider, Speech()).extract(tmp_path / 'clip.mp4', 'A caption'))


def test_the_content_extractor_can_take_a_closer_look_at_on_screen_text(fake_media, tmp_path):
    provider = Screens('content_extractor', [ContentAction(tool='read_more_frames', reason='No speech; the text looks cut off.')],
                       screens=['MYTH 1\nThe wall is visible', 'The wall is visible\nfrom the Moon\nMYTH 2'])
    content = extract(provider, tmp_path)
    [state] = provider.seen[ContentAction]
    assert state['speech_chars'] == 0 and state['frames_read'] == media.KEYFRAMES and 'MYTH 1' in state['screen_text']
    assert provider.image_batches == [media.KEYFRAMES, content_extractor.EXTRA_KEYFRAMES]
    assert content.frames_read == content.summary.frames_read == media.KEYFRAMES + content_extractor.EXTRA_KEYFRAMES
    assert 'MYTH 1\nThe wall is visible\nfrom the Moon\nMYTH 2' in content.text and content.text.count('The wall is visible') == 1
    assert content.steps == ['Content Extractor: Read 8 more frames for on-screen text and found 2 more line(s).']


def test_the_content_extractor_can_decide_the_first_frames_are_enough(fake_media, tmp_path):
    provider = Screens('content_extractor', [ContentAction(tool='finish', reason='The frames show no text.')], screens=[''])
    content = extract(provider, tmp_path)
    assert provider.image_batches == [media.KEYFRAMES] and content.frames_read == media.KEYFRAMES
    assert content.steps == ['Content Extractor: Kept the first 4 frames: The frames show no text.']


@pytest.mark.parametrize('mode,script', [('fixed', []), ('content_extractor', [(ContentAction, ProviderFailure('Model unavailable'))])])
def test_without_a_decision_the_first_pass_stands(fake_media, tmp_path, mode, script):
    provider = Screens(mode, script, screens=['ON SCREEN'])
    content = extract(provider, tmp_path)
    assert provider.image_batches == [media.KEYFRAMES] and content.steps == [] and 'ON SCREEN' in content.text


def test_a_failed_second_reading_keeps_the_first(fake_media, tmp_path):
    class Failing(Screens):
        async def read_images(self, schema, instructions, images):
            if self.image_batches:
                raise ProviderFailure('Model unavailable')
            return await super().read_images(schema, instructions, images)
    content = extract(Failing('content_extractor', [ContentAction(tool='read_more_frames', reason='')], screens=['ON SCREEN']), tmp_path)
    assert 'ON SCREEN' in content.text and content.frames_read == media.KEYFRAMES


def test_merging_keeps_each_line_once():
    assert merge_screen_text('A\nB', 'b\nC\n\nC\n “A”') == 'A\nB\nC'
    assert merge_screen_text('', 'A') == 'A'


# ---- The whole team -----------------------------------------------------------------------------

def test_all_agents_working_on_their_own_reach_a_checked_verdict():
    def search(query, looking_for):
        return ResearchAction(tool='search_web', query=query, looking_for=looking_for, result_ids=[], reason='Find evidence.')
    def read(*ids):
        return ResearchAction(tool='read_pages', query='', looking_for='supporting', result_ids=list(ids), reason='Read.')
    done = ResearchAction(tool='finish', query='', looking_for='supporting', result_ids=[], reason='Both sides covered.')
    provider = Team('autonomous', ROUTE + [search('sensor reading test report', 'supporting'), read('R1'),
                                           search('sensor reading disputed', 'contradicting'), read('R2'), done,
                                           judge('accept'), verdict()], page=LONG_PAGE, claims=[CLAIM])
    report = asyncio.run(run_pipeline(CLAIM.text, provider, provider.fetch))
    [claim] = report.claims
    assert claim.verdict == 'TRUE' and claim.verdict_state == 'issued' and claim.evidence[0].verified
    assert provider.queries == ['sensor reading test report', 'sensor reading disputed']
    assert [line.split(':')[0] for line in claim.agent_steps] == [
        'Orchestrator', 'Research Agent', 'Research Agent', 'Research Agent', 'Research Agent', 'Research Agent',
        'Orchestrator', 'Analyst Agent', 'Orchestrator', 'Orchestrator']
    assert not any(provider.script.values())   # every scripted choice was used


@pytest.mark.parametrize('module,schema', [
    (orchestrator, OrchestratorAction), (research_agent, ResearchAction), (analyst_agent, AnalystAction),
    (citation_verifier, VerifierAction), (verdict_agent, VerdictAction), (claim_extractor, ExtractorAction),
    (content_extractor, ContentAction)])
def test_every_tool_an_agent_is_offered_exists(module, schema):
    assert set(module.TOOLS) == set(schema.model_fields['tool'].annotation.__args__)


def test_every_agent_can_be_switched_on_by_name():
    assert set(AGENTS) == {'orchestrator', 'content_extractor', 'claim_extractor', 'research', 'analyst', 'citation_verifier', 'verdict'}
    for agent in AGENTS:
        assert autonomous(Team(agent), agent) and not autonomous(Team('fixed'), agent) and autonomous(Team('autonomous'), agent)
    assert not autonomous(FakeProvider(), 'research')   # A provider that says nothing uses the standard pass.
