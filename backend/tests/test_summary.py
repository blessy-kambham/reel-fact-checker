"""The confidence level of a verdict and the overall verdict of a report: both are counting rules. Offline."""
import asyncio
import pytest
from agents.orchestrator import research_claim, run_pipeline
from schemas import AtomicClaim, ClaimResult
from services.summary import confidence, overall
from tests.test_pipeline import CLAIM, FakeProvider, fake_fetch


@pytest.mark.parametrize('sites,strength,both_sides,rejected,selected,level', [
    (3, 'strong', False, 0, 6, 'high'),
    (5, 'strong', False, 2, 6, 'high'),      # a third rejected is tolerated; more than a third is not
    (3, 'strong', False, 3, 6, 'medium'),
    (3, 'strong', True, 0, 6, 'medium'),     # verified evidence on both sides
    (3, 'moderate', False, 0, 6, 'medium'),  # only one rated site
    (2, 'strong', False, 0, 4, 'medium'),    # fewer than three sites
    (3, 'weak', False, 0, 6, 'low'),         # no rated site at all
    (1, 'moderate', False, 0, 3, 'low'),     # a single site
    (1, 'weak', True, 3, 4, 'low'),          # never below low
])
def test_confidence_starts_high_and_loses_a_step_for_each_weakness(sites, strength, both_sides, rejected, selected, level):
    assert confidence(sites, strength, both_sides, rejected, selected)[0] == level


def test_confidence_says_why():
    level, reasons = confidence(2, 'moderate', True, 2, 4)
    assert level == 'low' and reasons == [
        'it rests on 2 sites, fewer than 3', 'only one of them is an official, academic or established source',
        'verified evidence points both ways, so the verdict is a judgement between them',
        '2 of 4 selected passages failed the citation check']
    assert confidence(3, 'strong', False, 0, 6)[1] == ['3 different sites back it',
                                                       'two or more of them are official, academic or established sources']


def test_an_issued_verdict_carries_a_confidence_level_and_a_withheld_one_does_not():
    issued = asyncio.run(research_claim(CLAIM, FakeProvider(), fake_fetch))
    assert issued.verdict == 'TRUE' and issued.confidence == 'low'
    assert issued.confidence_reasons[0] == 'it rests on a single site'
    withheld = asyncio.run(research_claim(CLAIM, FakeProvider(approved=False), fake_fetch))
    assert withheld.verdict_state == 'withheld' and withheld.confidence is None and withheld.confidence_reasons == []


def claim(verdict, state='issued'):
    return ClaimResult(claim='c', verdict=verdict, status='complete', evidence=[], limitations=[], supporting_search='x',
                       contradicting_search='x', sources_checked=1, verdict_state=state)


WITHHELD = claim('UNVERIFIABLE', 'withheld')


@pytest.mark.parametrize('verdicts,expected,summary', [
    (['TRUE', 'TRUE'], 'TRUE', 'Of 2 claims: 2 true.'),
    (['FALSE', 'FALSE', 'FALSE'], 'FALSE', 'Of 3 claims: 3 false.'),
    (['TRUE', 'FALSE'], 'PARTIALLY TRUE', 'Of 2 claims: 1 true, 1 false.'),
    (['TRUE', 'MISLEADING', 'OUTDATED'], 'PARTIALLY TRUE', 'Of 3 claims: 1 true, 1 misleading, 1 outdated.'),
    (['FALSE', 'MISLEADING'], 'FALSE', 'Of 2 claims: 1 false, 1 misleading.'),
    (['MISLEADING', 'OUTDATED'], 'MISLEADING', 'Of 2 claims: 1 misleading, 1 outdated.'),
    (['OUTDATED', 'OUTDATED'], 'OUTDATED', 'Of 2 claims: 2 outdated.'),
])
def test_the_overall_verdict_is_built_from_the_claims(verdicts, expected, summary):
    assert overall([claim(v) for v in verdicts]) == {'overall_verdict': expected, 'overall_summary': summary}


def test_claims_without_a_verdict_are_counted_and_never_treated_as_true_or_false():
    mostly = overall([claim('TRUE'), claim('TRUE'), WITHHELD])
    assert mostly == {'overall_verdict': 'TRUE', 'overall_summary': 'Of 3 claims: 2 true, 1 without a verdict. '
                                                                    'The overall verdict covers only the claims that received one.'}
    # When half or more could not be judged, the submission as a whole cannot be either.
    assert overall([claim('TRUE'), WITHHELD])['overall_verdict'] == 'UNVERIFIABLE'
    assert overall([claim('FALSE'), claim('UNVERIFIABLE'), WITHHELD]) == {
        'overall_verdict': 'UNVERIFIABLE', 'overall_summary': 'Of 3 claims: 1 false, 2 without a verdict.'}
    assert overall([WITHHELD, WITHHELD])['overall_verdict'] == 'UNVERIFIABLE'


def test_one_claim_has_no_separate_overall_verdict():
    assert overall([claim('TRUE')]) == overall([]) == {'overall_verdict': None, 'overall_summary': None}


def test_a_report_with_several_claims_gets_an_overall_verdict():
    second = AtomicClaim(text='The sample was limited to one room.', context='Fictional test')
    report = asyncio.run(run_pipeline('The sensor measured 12 units. The sample was limited to one room.',
                                      FakeProvider(claims=[CLAIM, second]), fake_fetch))
    assert [c.verdict for c in report.claims] == ['TRUE', 'TRUE']
    assert report.overall_verdict == 'TRUE' and report.overall_summary == 'Of 2 claims: 2 true.'
    single = asyncio.run(run_pipeline(CLAIM.text, FakeProvider(), fake_fetch))
    assert single.overall_verdict is None and single.overall_summary is None
