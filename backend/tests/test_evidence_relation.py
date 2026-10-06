"""Control-flow regression tests; model semantics still require live evaluation."""
import asyncio
import json
import pytest
from schemas import EvidenceRelation, CitationJudgment, EvidenceSelection, Source
from tools.excerpts import source_excerpts
from services.pipeline import verify_selection
from tools.providers import ProviderFailure


def run(relation='CONTRADICTS', approved=True, fail=False):
    source = Source(id='S1', title='Fixture', url='https://example.org',
                    text='The fictional object reflects light.', retrieved_at='2026-09-28')
    calls = []
    class Provider:
        async def structured(self, schema, instructions, data):
            payload = json.loads(data)
            calls.append((schema, payload))
            if schema is EvidenceRelation:
                # Classification has no analyst verdict, proposed stance or statement to anchor on.
                assert set(payload) == {'target_assertion', 'quote', 'page'}
                if fail:
                    raise ProviderFailure('private provider error')
                return EvidenceRelation(relation=relation, reason='Scripted relation')
            assert schema is CitationJudgment
            return CitationJudgment(supports_attribution=approved, stance_matches=approved, opposite_stance=False, reason='Scripted independent check')
    selection = EvidenceSelection(source_id='S1', excerpt_id='S1:E1',
                                  statement='The fictional object reflects light.', stance='FOR')
    result = asyncio.run(verify_selection(selection, {'S1':source},
                       {e.id:e for e in source_excerpts(source)}, Provider(), 'The fictional object generates its own light.'))
    return result, calls


def test_reclassified_stance_requires_independent_verification_and_keeps_original():
    result, calls = run()
    assert result.verified and result.stance == 'AGAINST'
    assert result.proposed_stance == 'FOR'
    assert result.relation_reason == 'Scripted relation'
    assert calls[1][1]['stance'] == 'AGAINST'
    assert result.quote == 'The fictional object reflects light.'


def test_relation_does_not_override_failed_verifier():
    result, _ = run(approved=False)
    assert not result.verified and result.verification_code == 'attribution_rejected'


@pytest.mark.parametrize('relation', ['IRRELEVANT', 'UNCERTAIN'])
def test_unusable_relationship_stops_before_verification(relation):
    result, calls = run(relation)
    assert not result.verified and result.verification_code == 'relation_unresolved'
    assert len(calls) == 1


def test_relationship_outage_is_safe_and_redacted():
    result, calls = run(fail=True)
    assert not result.verified and result.verification_code == 'check_unavailable'
    assert len(calls) == 1
    assert 'private provider error' not in result.model_dump_json()


def test_background_is_not_forced_into_contradiction():
    result, calls = run('BACKGROUND')
    assert result.stance == 'CONTEXT' and result.verified
    assert calls[1][1]['stance'] == 'CONTEXT'
