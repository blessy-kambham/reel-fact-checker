"""The explanation shown with a verdict, and the balance of evidence for and against. Offline."""
import asyncio
import json
import pytest
from agents.orchestrator import research_claim
from agents.verdict_agent import EXPLANATION_REJECTED, VERDICT_INSTRUCTIONS, checked_explanation
from schemas import Analysis, CitedSentence, Citation, EvidenceRelation, VerdictAction, VerdictDecision
from tools import credibility
from tests.test_pipeline import CLAIM, DRAFT, PAGE, FakeProvider, fake_fetch

def evidence(*ids):
    return [Citation(source_id='S1', quote='q', statement='s', stance='FOR', title='t', url='https://example.org', verified=True,
                     verification='ok', evidence_id=i) for i in ids]


def said(text, *ids):
    return CitedSentence(text=text, evidence_ids=list(ids))


USABLE = evidence('E1', 'E2', 'E3')


def explain(*sentences, cited=('E1', 'E2')):
    return checked_explanation(list(sentences), list(cited), USABLE)


def test_sentences_are_joined_with_their_evidence_after_each_one():
    assert explain(said('The sensor measured 12 units in the test.', 'E1'), said('The test covered one room', 'E1', 'E2')) == (
        'The sensor measured 12 units in the test [E1]. The test covered one room [E1][E2].')


@pytest.mark.parametrize('text', [
    'The U.S. Army built it in St. Louis at 9 a.m. on Monday',     # abbreviations cannot be mistaken for sentence ends
    'Dr. Smith measured 12.5 units, e.g. in room A.',
    '"It is accurate," the report says',
    'Élan Corp measured 12 units',
])
def test_what_a_sentence_looks_like_does_not_matter(text):
    assert explain(said(text, 'E1')) == f"{text.rstrip('.')} [E1]."


def test_ids_are_tidied_and_ids_typed_into_the_text_are_removed():
    assert explain(said('The sensor measured 12 units [E1].', ' e1 ', 'E1', 'e2')) == 'The sensor measured 12 units [E1][E2].'
    assert explain(said('It measured 12 units [E1, E2] in the test.', 'E1')) == 'It measured 12 units in the test [E1].'


@pytest.mark.parametrize('sentences', [
    [],
    [said('The sensor measured 12 units.')],                                  # no evidence given
    [said('The sensor measured 12 units.', 'E1'), said('Everyone knows it is accurate.')],   # one sentence without evidence
    [said('The sensor measured 12 units.', 'E9')],                            # not verified evidence
    [said('The sensor measured 12 units.', 'E1', 'E9')],
    [said('The sensor measured 12 units.', 'E3')],                            # verified, but not what the verdict cites
    [said('The sensor measured 12 units.', 'S1')],                            # a source ID is not evidence
    [said('', 'E1')], [said(' . ', 'E1')], [said('12 [E1]', 'E1')],           # nothing said
    [said('x' * 301, 'E1')],                                                  # not a sentence
])
def test_one_sentence_without_the_verdicts_own_verified_evidence_and_nothing_is_shown(sentences):
    assert explain(*sentences) is None


def test_a_long_answer_is_cut_to_four_sentences_not_refused():
    result = explain(*[said(f'Point {n} holds', 'E1') for n in range(1, 7)])
    assert result == 'Point 1 holds [E1]. Point 2 holds [E1]. Point 3 holds [E1]. Point 4 holds [E1].'


def test_the_verdict_agent_is_asked_for_sentences_that_rest_on_its_evidence():
    assert 'With each sentence list the IDs of the evidence it rests on' in VERDICT_INSTRUCTIONS


class Explains(FakeProvider):
    def __init__(self, explanation, **kwargs):
        super().__init__(**kwargs)
        self.explanation = explanation

    async def structured(self, schema, instructions, data):
        if schema is VerdictDecision:
            return VerdictDecision(verdict=self.verdict, evidence_ids=[e['id'] for e in json.loads(data)['verified_evidence']],
                                   explanation=self.explanation)
        return await super().structured(schema, instructions, data)


def check(provider):
    return asyncio.run(research_claim(CLAIM, provider, fake_fetch))


def test_an_issued_verdict_shows_its_explanation():
    result = check(Explains([said('The sensor measured 12 units in the test.', 'E1')]))
    assert result.verdict == 'TRUE' and result.explanation == 'The sensor measured 12 units in the test [E1].'
    assert EXPLANATION_REJECTED not in result.limitations


def test_an_explanation_that_is_not_cited_is_left_out_and_the_verdict_stands():
    result = check(Explains([said('Everyone knows this sensor is accurate.')]))
    assert result.verdict == 'TRUE' and result.verdict_state == 'issued' and result.explanation is None
    assert EXPLANATION_REJECTED in result.limitations


def test_no_explanation_is_fine_and_says_nothing():
    result = check(FakeProvider())
    assert result.verdict == 'TRUE' and result.explanation is None and EXPLANATION_REJECTED not in result.limitations


def test_a_withheld_verdict_never_shows_an_explanation():
    # FALSE without contradicting evidence is refused by the application, whatever the explanation says.
    result = check(Explains([said('The sensor measured 12 units in the test.', 'E1')], verdict='FALSE'))
    assert result.verdict_state == 'withheld' and result.explanation is None and EXPLANATION_REJECTED not in result.limitations


def test_an_answer_of_unverifiable_shows_no_explanation():
    result = check(Explains([said('The evidence does not settle it.', 'E1')], verdict='UNVERIFIABLE'))
    assert result.verdict == 'UNVERIFIABLE' and result.explanation is None


def test_the_explanation_is_also_taken_from_the_verdict_agent_when_it_chooses_its_own_tools():
    class Chooses(FakeProvider):
        agent_mode = 'verdict'
        async def structured(self, schema, instructions, data):
            if schema is VerdictAction:
                return VerdictAction(tool='issue_verdict', verdict='TRUE', evidence_ids=['E1'], missing='', looking_for='supporting',
                                     explanation=[said('The sensor measured 12 units in the test.', 'E1')])
            return await super().structured(schema, instructions, data)
    assert check(Chooses()).explanation == 'The sensor measured 12 units in the test [E1].'


def test_the_model_must_always_supply_the_explanation_field():
    """Code and tests may leave it out; the schema the model answers to may not."""
    from openai.lib._pydantic import to_strict_json_schema
    for schema in (VerdictDecision, VerdictAction):
        strict = to_strict_json_schema(schema)
        assert 'explanation' in strict['required'] and 'default' not in strict['properties']['explanation']
        assert 'maxItems' not in strict['properties']['explanation']   # too many sentences must not fail the verdict call


# ---- the balance of evidence --------------------------------------------------------------------

def test_one_side_of_the_evidence_is_weighed_by_site_not_by_passage():
    assert credibility.weigh(['https://www.nasa.gov/a', 'https://www.nasa.gov/b', 'https://en.wikipedia.org/wiki/A']) == (2, 1.5)
    assert credibility.weigh([]) == (0, 0) and credibility.weigh([None, '']) == (0, 0)


class TwoSides(FakeProvider):
    """Two pages on different sites; the analyst selects one passage from each, with the given relations."""
    def __init__(self, relations, **kwargs):
        super().__init__(**kwargs)
        self.relations = list(relations)

    async def search(self, query):
        self.queries.append(query)
        return ([{'url': 'https://www.nasa.gov/report', 'title': 'Official'}] if len(self.queries) == 1
                else [{'url': 'https://example.org/report', 'title': 'Unrated'}])

    async def structured(self, schema, instructions, data):
        if schema is Analysis:
            second = DRAFT.model_copy(update={'source_id': 'S2', 'excerpt_id': 'S2:E1'})
            return Analysis(verdict=self.verdict, evidence=[DRAFT, second], limitations=[])
        if schema is EvidenceRelation:
            return EvidenceRelation(voice='PAGE', relation=self.relations.pop(0), reason='Scripted relation')
        return await super().structured(schema, instructions, data)


def test_the_report_shows_how_much_verified_evidence_is_on_each_side():
    result = check(TwoSides(['SUPPORTS', 'SUPPORTS']))
    assert result.evidence_balance.model_dump() == {'supporting': {'sites': 2, 'weight': 1.45},
                                                    'contradicting': {'sites': 0, 'weight': 0}}


def test_the_balance_is_shown_when_conflicting_evidence_withholds_the_verdict():
    result = check(TwoSides(['SUPPORTS', 'CONTRADICTS']))
    assert result.withheld_reason == 'conflicting_evidence'
    assert result.evidence_balance.model_dump() == {'supporting': {'sites': 1, 'weight': 0.95},
                                                    'contradicting': {'sites': 1, 'weight': 0.5}}


def test_background_does_not_count_on_either_side():
    result = check(TwoSides(['SUPPORTS', 'BACKGROUND']))
    assert result.evidence_balance.supporting.sites == 1 and result.evidence_balance.contradicting.sites == 0
    assert check(TwoSides(['BACKGROUND', 'BACKGROUND'])).evidence_balance is None


def test_a_corrected_citation_slip_keeps_the_explanation_given_with_the_accepted_answer():
    class SlipsOnce(FakeProvider):
        agent_mode = 'verdict'
        answers = [dict(evidence_ids=['E7'], explanation=[said('First try.', 'E7')]),
                   dict(evidence_ids=['E1'], explanation=[said('The sensor measured 12 units in the test.', 'E1')])]
        async def structured(self, schema, instructions, data):
            if schema is VerdictAction:
                return VerdictAction(tool='issue_verdict', verdict='TRUE', missing='', looking_for='supporting', **self.answers.pop(0))
            return await super().structured(schema, instructions, data)
    provider = SlipsOnce()
    provider.answers = list(SlipsOnce.answers)
    result = check(provider)
    assert result.verdict == 'TRUE' and result.explanation == 'The sensor measured 12 units in the test [E1].'


def test_a_reused_result_keeps_its_explanation_and_balance():
    from agents.orchestrator import research_all
    [first] = asyncio.run(research_all([CLAIM], Explains([said('The sensor measured 12 units in the test.', 'E1')]), fake_fetch))
    provider = FakeProvider()
    provider.recall = lambda key: (first, 'earlier', '2026-10-05T09:30:00+00:00')
    [again] = asyncio.run(research_all([CLAIM], provider, fake_fetch))
    assert again.reused_from == 'earlier' and again.explanation == first.explanation and again.evidence_balance == first.evidence_balance
