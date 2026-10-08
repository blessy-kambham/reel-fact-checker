"""The Response Formatter's short summary, and the poor-audio flag from speech recognition. Offline."""
import math
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
import main
from services.demo import demo_report
from services.formatter import MAX_SUMMARY_CHARS, short_summary
from tests.test_history import live_report
from tools.transcribe import LocalWhisper


def one_claim(**changes):
    report = live_report()
    claim = report.claims[0].model_copy(update=changes)
    return report.model_copy(update={'claims': [claim]})


def test_an_issued_verdict_gives_the_verdict_the_claim_and_how_far_to_trust_it():
    text = short_summary(one_claim(claim='The Great Wall is visible from space.', verdict='FALSE', verdict_state='issued',
                                   verdict_site_count=3, confidence='low'))
    assert text == 'FALSE: "The Great Wall is visible from space." Checked against 3 sites, low confidence.'


def test_one_site_is_singular_and_missing_details_are_left_out():
    assert short_summary(one_claim(claim='A claim.', verdict='TRUE', verdict_state='issued', verdict_site_count=1,
                                   confidence=None)) == 'TRUE: "A claim." Checked against 1 site.'
    assert short_summary(one_claim(claim='A claim.', verdict='TRUE', verdict_state='issued', verdict_site_count=None,
                                   confidence=None)) == 'TRUE: "A claim."'


@pytest.mark.parametrize('reason,words', [
    ('no_relevant_evidence', 'no checked evidence addressed it'),
    ('conflicting_evidence', 'the checked evidence points both ways'),
    ('search_failed', 'the check could not be completed'),
    (None, 'the check could not be completed'),
])
def test_a_withheld_verdict_says_why_in_a_few_words(reason, words):
    text = short_summary(one_claim(claim='A claim.', verdict='UNVERIFIABLE', verdict_state='withheld', withheld_reason=reason))
    assert text == f'UNVERIFIABLE: "A claim." No verdict: {words}.'


def test_an_issued_unverifiable_answer_is_not_dressed_up_as_a_verdict():
    text = short_summary(one_claim(claim='A claim.', verdict='UNVERIFIABLE', verdict_state='issued', verdict_site_count=2))
    assert text == 'UNVERIFIABLE: "A claim." No verdict: not enough public evidence.'


def test_a_long_claim_is_shortened_and_the_summary_never_passes_280_characters():
    text = short_summary(one_claim(claim='word ' * 200, verdict='TRUE', verdict_state='issued', verdict_site_count=3,
                                   confidence='high'))
    assert len(text) <= MAX_SUMMARY_CHARS and text.endswith('" Checked against 3 sites, high confidence.') and '…' in text


def test_several_claims_give_the_overall_verdict():
    report = live_report().model_copy(update={'overall_verdict': 'UNVERIFIABLE',
                                              'overall_summary': 'Of 2 claims: 1 true, 1 without a verdict.'})
    report = report.model_copy(update={'claims': report.claims * 2})
    assert short_summary(report) == 'Overall UNVERIFIABLE. Of 2 claims: 1 true, 1 without a verdict.'


def test_nothing_checked_says_why_and_the_demo_gets_no_summary():
    assert short_summary(live_report().model_copy(update={'claims': [], 'intent': 'SATIRE'})) == \
        'No claims were checked. It reads as satire, so there was nothing to fact-check.'
    assert short_summary(live_report().model_copy(update={'claims': [], 'intent': 'SOMETHING'})).startswith('No claims')
    assert short_summary(demo_report()) is None


def test_every_live_report_is_saved_with_its_summary(monkeypatch, tmp_path):
    for key in ('OPENAI_API_KEY', 'TAVILY_API_KEY'):
        monkeypatch.setenv(key, 'offline-test')
    monkeypatch.setenv('OPENAI_MODEL', 'gpt-4.1-mini')
    monkeypatch.setenv('ENABLE_LIVE_RESEARCH', 'true')
    monkeypatch.setattr(main, 'DATA_DIR', tmp_path)

    class Stub:
        async def close(self):
            pass
    monkeypatch.setattr(main, 'Providers', Stub)

    async def pipeline(text, provider):
        return live_report(text)
    monkeypatch.setattr(main, 'run_pipeline', pipeline)
    with TestClient(main.app) as client:
        report = client.post('/fact-check', json={'claim': 'A statement'}).json()
        assert report['summary_text'] == short_summary(live_report())
        assert client.get(f"/history/{report['id']}").json()['summary_text'] == report['summary_text']
        assert client.get('/demo').json()['summary_text'] is None


# ---- speech recognition confidence --------------------------------------------------------------

class FakeModel:
    def __init__(self, segments):
        self.segments = segments

    def transcribe(self, audio, vad_filter):
        return iter(self.segments), SimpleNamespace(language='en')


def segment(text, start, end, probability):
    return SimpleNamespace(text=f' {text} ', start=start, end=end, avg_logprob=math.log(probability))


def transcribe_with(segments, tmp_path):
    whisper = LocalWhisper()
    whisper._model = FakeModel(segments)
    return whisper._transcribe(tmp_path / 'audio.wav')


def test_confidence_is_the_word_probability_weighted_by_speaking_time(tmp_path):
    transcript = transcribe_with([segment('Clear speech', 0, 3, 0.9), segment('mumbled', 3, 4, 0.3)], tmp_path)
    assert transcript.text == 'Clear speech mumbled' and transcript.language == 'en'
    assert transcript.confidence == pytest.approx((3 * 0.9 + 1 * 0.3) / 4, abs=0.001)


def test_no_speech_has_no_confidence(tmp_path):
    assert transcribe_with([], tmp_path).confidence is None
    assert transcribe_with([segment('', 0, 0, 0.9)], tmp_path).confidence is None


def test_a_value_that_is_not_a_number_is_ignored_rather_than_counted_as_sure(tmp_path):
    odd = segment('odd', 0, 2, 0.5)
    odd.avg_logprob = float('nan')
    assert transcribe_with([odd, segment('clear', 2, 3, 0.4)], tmp_path).confidence == pytest.approx(0.4)
    assert transcribe_with([odd], tmp_path).confidence is None
