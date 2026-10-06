"""Every verdict is either issued with the evidence IDs behind it, or withheld with a specific reason."""
import asyncio
import json
import pytest
from schemas import Analysis, AtomicClaim, EvidenceRelation, ExtractionCoverage, Extraction, VerdictDecision
from services.pipeline import WITHHELD_MESSAGES, research_claim, run_pipeline
from tools.providers import ProviderFailure
from tests.test_pipeline import CLAIM, DRAFT, FakeProvider, fake_fetch

AGAINST = DRAFT.model_copy(update={'stance': 'AGAINST', 'statement': 'The sample was limited to one room.'})
RELATIONS = {'FOR': 'SUPPORTS', 'AGAINST': 'CONTRADICTS', 'CONTEXT': 'BACKGROUND'}


class Scripted(FakeProvider):
    """Returns a fixed verdict-stage decision and, optionally, several evidence selections."""
    def __init__(self, decision=None, drafts=None, **kwargs):
        super().__init__(**kwargs)
        self.decision, self.drafts, self.relations = decision, drafts or [DRAFT], 0
        self.verdict_inputs = []

    async def structured(self, schema, instructions, data):
        if schema is Analysis:
            return Analysis(verdict='TRUE', evidence=self.drafts, limitations=[])
        if schema is EvidenceRelation:
            draft = self.drafts[self.relations]
            self.relations += 1
            return EvidenceRelation(voice='PAGE', relation=RELATIONS[draft.stance], reason='Scripted relation')
        if schema is VerdictDecision:
            self.verdict_inputs.append(json.loads(data))
            if isinstance(self.decision, BaseException):
                raise self.decision
            if self.decision is not None:
                return self.decision
        return await super().structured(schema, instructions, data)


def check(provider, claim=CLAIM):
    return asyncio.run(research_claim(claim, provider, fake_fetch))


def assert_withheld(result, reason, status):
    assert result.verdict == 'UNVERIFIABLE' and result.status == status
    assert result.verdict_state == 'withheld' and result.withheld_reason == reason
    assert result.withheld_message == WITHHELD_MESSAGES[reason]
    if result.sources_checked:  # Early exits keep their own, more detailed limitation text.
        assert WITHHELD_MESSAGES[reason] in result.limitations
    assert result.verdict_evidence_ids == []


def test_issued_verdict_records_the_evidence_behind_it():
    result = check(Scripted(decision=VerdictDecision(verdict='TRUE', evidence_ids=['E1'])))
    assert result.verdict == 'TRUE' and result.status == 'complete'
    assert result.verdict_state == 'issued' and result.withheld_reason is None and result.withheld_message is None
    assert result.decision_verdict == 'TRUE'
    assert result.decision_evidence_ids == result.verdict_evidence_ids == ['E1']
    assert [c.evidence_id for c in result.evidence] == ['E1']


def test_evidence_ids_match_what_the_verdict_stage_saw():
    provider = Scripted(decision=VerdictDecision(verdict='FALSE', evidence_ids=['E2']), drafts=[DRAFT, AGAINST])
    result = check(provider)
    sent = {e['id']: e['statement'] for e in provider.verdict_inputs[0]['verified_evidence']}
    stored = {c.evidence_id: c.statement for c in result.evidence}
    assert sent == stored and set(stored) == {'E1', 'E2'}


def test_unverifiable_decision_is_issued_not_withheld():
    result = check(Scripted(decision=VerdictDecision(verdict='UNVERIFIABLE', evidence_ids=[])))
    assert result.verdict == 'UNVERIFIABLE' and result.verdict_state == 'issued' and result.status == 'complete'
    assert result.withheld_reason is None


@pytest.mark.parametrize('decision, reason', [
    (VerdictDecision(verdict='TRUE', evidence_ids=['E1', 'E9']), 'unknown_evidence_ids'),
    (VerdictDecision(verdict='UNVERIFIABLE', evidence_ids=['invented']), 'unknown_evidence_ids'),
    (VerdictDecision(verdict='TRUE', evidence_ids=[]), 'missing_evidence_ids'),
    (VerdictDecision(verdict='FALSE', evidence_ids=['E1']), 'evidence_stance_mismatch'),
])
def test_rejected_decisions_keep_the_raw_answer_for_audit(decision, reason):
    result = check(Scripted(decision=decision))
    assert_withheld(result, reason, 'incomplete')
    assert result.decision_verdict == decision.verdict
    assert result.decision_evidence_ids == decision.evidence_ids
    assert result.evidence  # Verified evidence is still shown.


def test_context_only_selection_is_a_stance_mismatch():
    context = DRAFT.model_copy(update={'stance': 'CONTEXT', 'statement': 'The test used one room.'})
    result = check(Scripted(decision=VerdictDecision(verdict='MISLEADING', evidence_ids=['E2']), drafts=[DRAFT, context]))
    assert_withheld(result, 'evidence_stance_mismatch', 'incomplete')


def test_conflicting_evidence_is_a_complete_research_outcome():
    result = check(Scripted(decision=VerdictDecision(verdict='TRUE', evidence_ids=['E1']), drafts=[DRAFT, AGAINST]))
    assert_withheld(result, 'conflicting_evidence', 'complete')
    assert result.decision_verdict == 'TRUE'


@pytest.mark.parametrize('error', [ProviderFailure('private detail'), asyncio.TimeoutError()])
def test_verdict_outage_is_withheld_without_a_decision(error):
    result = check(Scripted(decision=error))
    assert_withheld(result, 'verdict_check_unavailable', 'incomplete')
    assert result.decision_verdict is None and result.decision_evidence_ids == []
    assert 'private detail' not in result.model_dump_json()


def test_long_invented_ids_are_truncated_in_reports():
    result = check(Scripted(decision=VerdictDecision(verdict='TRUE', evidence_ids=['X' * 500])))
    assert result.withheld_reason == 'unknown_evidence_ids'
    assert result.decision_evidence_ids == ['X' * 32]


@pytest.mark.parametrize('provider, reason, status', [
    # Its only passage failed the citation check, so nothing is left to judge.
    (Scripted(approved=False), 'no_relevant_evidence', 'complete'),
    (Scripted(fail_search=True), 'search_failed', 'incomplete'),
    (Scripted(drafts=[DRAFT.model_copy(update={'stance': 'CONTEXT'})]), 'no_relevant_evidence', 'complete'),
])
def test_verdict_stage_is_skipped_with_a_reason(provider, reason, status):
    result = check(provider)
    assert_withheld(result, reason, status)
    assert provider.verdict_inputs == [] and result.decision_verdict is None


def test_unreadable_pages_are_no_sources():
    async def unreadable(url):
        raise ValueError('not readable')
    provider = Scripted()
    result = asyncio.run(research_claim(CLAIM, provider, unreadable))
    assert_withheld(result, 'no_sources', 'incomplete')


def test_one_failed_search_direction_is_named():
    class OneSided(Scripted):
        async def search(self, query):
            if 'contradicting' in query:
                raise ProviderFailure('Search unavailable')
            return await super().search(query)
    provider = OneSided()
    result = check(provider)
    assert_withheld(result, 'search_failed', 'incomplete')
    assert provider.verdict_inputs == []


def test_claim_timeout_and_provider_failure_are_named(monkeypatch):
    import agents.orchestrator as pipeline
    async def slow(*args, **kwargs):
        raise asyncio.TimeoutError()
    monkeypatch.setattr(pipeline, 'research_claim', slow)
    report = asyncio.run(run_pipeline(CLAIM.text, FakeProvider(), fake_fetch))
    assert report.claims[0].withheld_reason == 'claim_timeout'

    async def failing(*args, **kwargs):
        raise ProviderFailure('Provider unavailable')
    monkeypatch.setattr(pipeline, 'research_claim', failing)
    report = asyncio.run(run_pipeline(CLAIM.text, FakeProvider(), fake_fetch))
    assert report.claims[0].withheld_reason == 'provider_failure'


def test_coverage_failure_is_named():
    class Uncovered(FakeProvider):
        async def structured(self, schema, instructions, data):
            if schema is Extraction:
                return Extraction(intent='FACTUAL', claims=[AtomicClaim(text='Changed claim', context='')], omitted_claims=False, note='')
            if schema is ExtractionCoverage:
                return ExtractionCoverage(complete=False, issues=['Changed wording'])
            return await super().structured(schema, instructions, data)
    report = asyncio.run(run_pipeline('Original claim text.', Uncovered(), fake_fetch))
    assert report.claims[0].withheld_reason == 'coverage_failed'
    assert report.claims[0].verdict_state == 'withheld'


class TwoPages(Scripted):
    async def search(self, query):
        self.queries.append(query)
        page = 'b' if 'contradicting' in query else 'a'
        return [{'url': f'https://example.org/{page}', 'title': f'Page {page}'}]


def test_single_page_verdict_is_flagged():
    from services.pipeline import SINGLE_SOURCE_NOTE
    result = check(Scripted(decision=VerdictDecision(verdict='TRUE', evidence_ids=['E1'])))
    assert result.verdict == 'TRUE' and result.verdict_source_count == 1
    assert SINGLE_SOURCE_NOTE in result.limitations


def test_verdict_from_two_pages_is_not_flagged():
    from services.pipeline import SINGLE_SOURCE_NOTE
    second = DRAFT.model_copy(update={'source_id': 'S2', 'excerpt_id': 'S2:E1'})
    result = check(TwoPages(decision=VerdictDecision(verdict='TRUE', evidence_ids=['E1', 'E2']), drafts=[DRAFT, second]))
    assert result.verdict == 'TRUE' and result.verdict_source_count == 2
    assert SINGLE_SOURCE_NOTE not in result.limitations


def test_withheld_or_unverifiable_verdicts_are_not_flagged():
    from services.pipeline import SINGLE_SOURCE_NOTE
    withheld = check(Scripted(decision=VerdictDecision(verdict='FALSE', evidence_ids=['E1'])))
    assert withheld.verdict_source_count is None and SINGLE_SOURCE_NOTE not in withheld.limitations
    unverifiable = check(Scripted(decision=VerdictDecision(verdict='UNVERIFIABLE', evidence_ids=['E1'])))
    assert unverifiable.verdict_source_count == 1 and SINGLE_SOURCE_NOTE not in unverifiable.limitations


def test_prompts_carry_no_validation_case_examples():
    # Prompts must not contain the claims used to validate the pipeline.
    import inspect
    from agents import analyst_agent, citation_verifier, claim_extractor, content_extractor, verdict_agent
    for module in (analyst_agent, citation_verifier, claim_extractor, content_extractor, verdict_agent):
        source = inspect.getsource(module).casefold()
        assert 'provider.structured(' in source or 'provider.read_images(' in source  # The module holds a prompt.
        for word in ('moon', 'sunlight', 'eiffel', 'pluto', 'great wall'):
            assert word not in source


def test_relation_and_analysis_prompts_exclude_neighbouring_assertions():
    prompts = {}
    class Capture(Scripted):
        async def structured(self, schema, instructions, data):
            prompts[schema.__name__] = instructions
            return await super().structured(schema, instructions, data)
    check(Capture())
    assert 'evidence about another claim is never SUPPORTS' in prompts['EvidenceRelation']
    assert 'never select evidence about them' in prompts['Analysis']


def test_an_invented_id_is_ignored_and_a_misattributed_passage_is_left_out():
    invented = DRAFT.model_copy(update={'excerpt_id': 'S1:INVENTED'})
    ok = check(Scripted(decision=VerdictDecision(verdict='TRUE', evidence_ids=['E1']), drafts=[DRAFT, invented]))
    assert ok.verdict == 'TRUE' and ok.verdict_state == 'issued' and ok.status == 'complete'
    assert [c.verification_code for c in ok.rejected_citations] == ['unknown_excerpt']

    class Misattributed(Scripted):
        async def structured(self, schema, instructions, data):
            from schemas import CitationJudgment
            if schema is CitationJudgment and json.loads(data)['statement'] == 'The sample was limited to one room.':
                return CitationJudgment(supports_attribution=False, stance_matches=True, opposite_stance=False, reason='Scripted rejection')
            return await super().structured(schema, instructions, data)
    second = DRAFT.model_copy(update={'statement': 'The sample was limited to one room.'})
    judged = check(Misattributed(decision=VerdictDecision(verdict='TRUE', evidence_ids=['E1']), drafts=[DRAFT, second]))
    # A passage that fails its check is left out and reported; the verdict rests on the one that passed.
    assert judged.verdict == 'TRUE' and judged.verdict_state == 'issued' and judged.status == 'complete'
    assert [c.verification_code for c in judged.rejected_citations] == ['attribution_rejected']
    assert [e.evidence_id for e in judged.evidence] == ['E1']
    assert any('failed the citation check and were left out' in note for note in judged.limitations)


def test_relation_prompt_treats_general_facts_as_background():
    prompts = {}
    class Capture(Scripted):
        async def structured(self, schema, instructions, data):
            prompts[schema.__name__] = instructions
            return await super().structured(schema, instructions, data)
    check(Capture())
    assert 'General facts about the subject' in prompts['EvidenceRelation']
    assert 'BACKGROUND, not CONTRADICTS' in prompts['EvidenceRelation']


def test_prompts_separate_a_reported_belief_from_a_statement_of_fact():
    prompts = {}
    class Capture(Scripted):
        async def structured(self, schema, instructions, data):
            prompts[schema.__name__] = instructions
            return await super().structured(schema, instructions, data)
    check(Capture())
    assert 'REPORTED means the page only describes it as something people believe or once believed' in prompts['EvidenceRelation']
    assert 'If the target is itself a statement about what people believe' in prompts['EvidenceRelation']
    assert 'which the page does not present as fact is not FOR' in prompts['CitationJudgment']
    # The analyst is asked to spread its selections over sites rather than quote one page several times.
    assert 'at least three different sites and at most two excerpts from any one page' in prompts['Analysis']


def test_off_topic_pick_is_ignored_but_an_unavailable_relation_check_still_withholds():
    from tools.providers import ProviderFailure
    off_topic = DRAFT.model_copy(update={'statement': 'The building has a blue roof.'})
    class Relation(Scripted):
        def __init__(self, outcome, **kwargs):
            super().__init__(**kwargs)
            self.outcome = outcome
        async def structured(self, schema, instructions, data):
            if schema is EvidenceRelation and self.relations == 1:
                self.relations += 1
                if self.outcome == 'fail':
                    raise ProviderFailure('unavailable')
                return EvidenceRelation(voice='PAGE', relation='IRRELEVANT', reason='About something else')
            return await super().structured(schema, instructions, data)
    decision = VerdictDecision(verdict='TRUE', evidence_ids=['E1'])
    ignored = check(Relation('irrelevant', decision=decision, drafts=[DRAFT, off_topic]))
    assert ignored.verdict == 'TRUE' and ignored.verdict_state == 'issued'
    assert [c.verification_code for c in ignored.rejected_citations] == ['relation_unresolved']
    # A check that could not be run is different from one that was run and failed: the verdict is withheld.
    withheld = check(Relation('fail', decision=decision, drafts=[DRAFT, off_topic]))
    assert withheld.withheld_reason == 'citation_unchecked' and withheld.status == 'incomplete'
