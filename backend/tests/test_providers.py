"""Model calls that the provider refuses for a moment are retried; other failures are named, never quoted. Offline."""
import asyncio
from types import SimpleNamespace
import httpx
import pytest
from openai import APITimeoutError, RateLimitError
from schemas import VerdictDecision
from tools import providers
from tools.providers import MODEL_BUSY, MODEL_FAILED, MODEL_SLOW, ProviderFailure, Providers

REQUEST = httpx.Request('POST', 'https://api.openai.com/v1/responses')
ANSWER = SimpleNamespace(usage=SimpleNamespace(input_tokens=10, output_tokens=5),
                         output_parsed=VerdictDecision(verdict='TRUE', evidence_ids=['E1']))


def rate_limited(code='rate_limit_exceeded'):
    return RateLimitError('secret provider wording', response=httpx.Response(429, request=REQUEST), body={'code': code})


def provider(outcomes, monkeypatch):
    made, waits = [], []
    async def parse(**request):
        made.append(request)
        outcome = outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome
    async def sleep(seconds):
        waits.append(seconds)
    monkeypatch.setattr(providers.asyncio, 'sleep', sleep)
    monkeypatch.setenv('OPENAI_MODEL', 'gpt-4.1-mini')
    instance = Providers.__new__(Providers)
    instance.client = SimpleNamespace(responses=SimpleNamespace(parse=parse))
    instance.usage = {'input_tokens': 0, 'output_tokens': 0, 'model_calls': 0, 'search_calls': 0}
    instance.spending = None
    return instance, made, waits


def ask(instance):
    return asyncio.run(instance.structured(VerdictDecision, 'Decide.', '{}'))


def test_a_rate_limited_call_waits_and_tries_again(monkeypatch):
    instance, made, waits = provider([rate_limited(), rate_limited(), ANSWER], monkeypatch)
    assert ask(instance).verdict == 'TRUE'
    assert len(made) == 3 and waits == [5, 10] and instance.usage['model_calls'] == 1


def test_a_call_still_refused_after_every_wait_fails_with_a_named_reason(monkeypatch):
    instance, made, waits = provider([rate_limited()] * 4, monkeypatch)
    with pytest.raises(ProviderFailure) as failure:
        ask(instance)
    assert str(failure.value) == MODEL_BUSY and len(made) == 4 and waits == list(providers.RATE_LIMIT_WAITS)
    assert 'secret provider wording' not in str(failure.value)


def test_running_out_of_credit_is_not_retried(monkeypatch):
    instance, made, waits = provider([rate_limited('insufficient_quota')], monkeypatch)
    with pytest.raises(ProviderFailure) as failure:
        ask(instance)
    assert str(failure.value) == MODEL_FAILED and len(made) == 1 and not waits


def test_a_timeout_and_other_errors_are_named_without_a_retry(monkeypatch):
    instance, made, waits = provider([APITimeoutError(REQUEST)], monkeypatch)
    with pytest.raises(ProviderFailure) as failure:
        ask(instance)
    assert str(failure.value) == MODEL_SLOW and len(made) == 1 and not waits
    instance, made, _ = provider([RuntimeError('secret detail')], monkeypatch)
    with pytest.raises(ProviderFailure) as failure:
        ask(instance)
    assert str(failure.value) == MODEL_FAILED and len(made) == 1


def test_image_reading_retries_the_same_way(monkeypatch):
    from schemas import ScreenText
    answer = SimpleNamespace(usage=None, output_parsed=ScreenText(text='ON SCREEN'))
    instance, made, waits = provider([rate_limited(), answer], monkeypatch)
    assert asyncio.run(instance.read_images(ScreenText, 'Read.', [b'\xff\xd8'])).text == 'ON SCREEN'
    assert len(made) == 2 and waits == [5]
