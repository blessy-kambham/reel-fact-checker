"""Scripted regression checks; these do not evaluate a real model's semantics."""
import asyncio
import json
import pytest
from pydantic import ValidationError
from schemas import VerdictDecision, EvidenceRelation, Analysis, AtomicClaim, CitationJudgment, EvidenceSelection
from services.pipeline import research_claim

PAGE = 'A freezing point is the temperature where a liquid becomes solid. The fictional sample freezes at 0°C at standard pressure.'

class Provider:
    def __init__(self, evidence, attribution=True, stance=True):
        self.evidence = evidence
        self.attribution, self.stance = attribution, stance
        self.checks, self.relations = [], 0
    async def search(self, query):
        return [{'url':'https://example.org/fixture','title':'Synthetic fixture'}]
    async def structured(self, schema, instructions, data):
        if schema is Analysis:
            return Analysis(verdict='TRUE', evidence=self.evidence, limitations=[])
        if schema is VerdictDecision:
            return VerdictDecision(verdict='TRUE', evidence_ids=[e['id'] for e in json.loads(data)['verified_evidence']])
        if schema is EvidenceRelation:
            item = self.evidence[self.relations]
            self.relations += 1
            return EvidenceRelation(relation={'FOR':'SUPPORTS','AGAINST':'CONTRADICTS','CONTEXT':'BACKGROUND'}[item.stance], reason='Scripted relation')
        assert schema is CitationJudgment
        self.checks.append(json.loads(data))
        return CitationJudgment(supports_attribution=self.attribution,
                                stance_matches=self.stance, reason='Scripted judgment')


def evidence(stance, excerpt='S1:E1'):
    return EvidenceSelection(source_id='S1',excerpt_id=excerpt,
        statement='A freezing point is a liquid-to-solid transition temperature.' if excerpt=='S1:E1' else 'The fictional sample freezes at 0°C at standard pressure.',stance=stance)


def run(provider):
    async def fetch(url):
        return url, PAGE
    return asyncio.run(research_claim(AtomicClaim(text='The fictional sample freezes at 0°C at standard pressure.',context=''),provider,fetch))


def test_valid_context_alone_cannot_establish_true():
    result = run(Provider([evidence('CONTEXT')]))
    assert result.evidence[0].verified
    assert result.verdict == 'UNVERIFIABLE'


def test_valid_context_does_not_veto_independent_support():
    provider = Provider([evidence('CONTEXT'),evidence('FOR','S1:E2')])
    result = run(provider)
    assert result.verdict == 'TRUE' and result.status == 'complete'
    assert len(result.evidence) == 2 and not result.rejected_citations
    assert provider.checks[0]['statement'] != provider.checks[0]['claim']
    assert all(c['page'] == PAGE for c in provider.checks)


@pytest.mark.parametrize('stance',['FOR','AGAINST','CONTEXT'])
@pytest.mark.parametrize('attribution,stance_matches',[(False,True),(True,False),(False,False)])
def test_either_failed_judgment_rejects_any_stance(stance,attribution,stance_matches):
    result = run(Provider([evidence(stance)],attribution,stance_matches))
    assert result.verdict == 'UNVERIFIABLE' and result.status == 'incomplete'
    assert result.evidence == []
    assert result.rejected_citations[0].verification_code == 'attribution_rejected'


def test_model_must_supply_both_judgments():
    with pytest.raises(ValidationError):
        CitationJudgment(supports_attribution=True,reason='Missing stance judgment')
