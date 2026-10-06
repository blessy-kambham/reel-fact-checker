"""Verify stage isolation and evidence gates without paid calls."""
import asyncio
import json
import pytest
from schemas import Analysis, AtomicClaim, VerdictDecision
from services.pipeline import research_claim
from tools.providers import ProviderFailure
from tests.test_pipeline import FakeProvider, fake_fetch, DRAFT


def test_final_verdict_uses_only_target_and_verified_evidence():
    class Provider(FakeProvider):
        async def structured(self, schema, instructions, data):
            if schema is Analysis:
                return Analysis(verdict='MISLEADING', evidence=[DRAFT], limitations=['Neighboring assertion is false.'])
            if schema is VerdictDecision:
                payload = json.loads(data)
                assert set(payload) == {'target_assertion', 'verified_evidence'}
                assert payload['target_assertion'] == 'The sensor measured 12 units.'
                assert 'Neighboring' not in data and 'MISLEADING' not in data
                assert payload['verified_evidence'][0]['id'] == 'E1'
                return VerdictDecision(verdict='TRUE', evidence_ids=['E1'])
            return await super().structured(schema, instructions, data)
    claim = AtomicClaim(text='The sensor measured 12 units.', context='Neighboring assertion is false.')
    result = asyncio.run(research_claim(claim, Provider(), fake_fetch))
    assert result.verdict == 'TRUE' and result.status == 'complete'
    assert 'Neighboring' not in result.model_dump_json()


@pytest.mark.parametrize('ids', [[], ['invented']])
def test_verdict_requires_real_verified_evidence(ids):
    class Provider(FakeProvider):
        async def structured(self, schema, instructions, data):
            if schema is VerdictDecision:
                return VerdictDecision(verdict='TRUE', evidence_ids=ids)
            return await super().structured(schema, instructions, data)
    result = asyncio.run(research_claim(AtomicClaim(text='Claim', context=''), Provider(), fake_fetch))
    assert result.verdict == 'UNVERIFIABLE' and result.status == 'incomplete'
    assert result.evidence  # Preserve prior verification.


@pytest.mark.parametrize('error', [ProviderFailure('secret'), asyncio.TimeoutError()])
def test_verdict_failure_preserves_evidence_and_redacts_error(error):
    class Provider(FakeProvider):
        async def structured(self, schema, instructions, data):
            if schema is VerdictDecision:
                raise error
            return await super().structured(schema, instructions, data)
    result = asyncio.run(research_claim(AtomicClaim(text='Claim', context=''), Provider(), fake_fetch))
    assert result.status == 'incomplete' and result.verdict == 'UNVERIFIABLE'
    assert result.evidence and 'secret' not in result.model_dump_json()


def test_the_verdict_agent_is_not_asked_when_every_citation_is_rejected():
    class Provider(FakeProvider):
        async def structured(self, schema, instructions, data):
            assert schema is not VerdictDecision
            return await super().structured(schema, instructions, data)
    result = asyncio.run(research_claim(AtomicClaim(text='Claim', context=''), Provider(approved=False), fake_fetch))
    assert result.withheld_reason == 'no_relevant_evidence' and not result.evidence and len(result.rejected_citations) == 1
