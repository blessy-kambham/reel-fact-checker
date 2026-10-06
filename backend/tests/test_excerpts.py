"""Excerpt selection must preserve source bytes without bypassing attribution."""
import asyncio
import pytest
from pydantic import ValidationError
from schemas import VerdictDecision, EvidenceRelation, Source, EvidenceSelection, CitationJudgment, Analysis, AtomicClaim
from tools.excerpts import source_excerpts
from services.pipeline import verify_selection, research_claim


def source(text, source_id='S1'):
    return Source(id=source_id,title='Synthetic test source',url='https://example.org/source',
                  text=text,retrieved_at='2026-09-28T00:00:00Z')

class Judge:
    def __init__(self, approved=True):
        self.approved = approved
        self.inputs = []
    async def structured(self, schema, instructions, data):
        import json
        if schema is VerdictDecision:
            return VerdictDecision(verdict='TRUE', evidence_ids=[e['id'] for e in json.loads(data)['verified_evidence']])
        if schema is EvidenceRelation:
            return EvidenceRelation(relation='SUPPORTS', reason='Scripted relation')
        assert schema is CitationJudgment
        self.inputs.append(json.loads(data))
        return CitationJudgment(supports_attribution=self.approved, stance_matches=True,reason='Scripted attribution check')


def selection(source_id='S1', excerpt_id='S1:E1'):
    return EvidenceSelection(source_id=source_id,excerpt_id=excerpt_id,statement='Synthetic test statement',stance='FOR')


@pytest.mark.parametrize('text',[
    'Water freezes at 0°C. Don’t replace the apostrophe or ° symbol.',
    'Café — Δ 你好. Exact Unicode is preserved.',
    'a'*1200,
    ('Long passage without punctuation and with qualifications '*60),
    '  First sentence.\n\tSecond sentence!  Last phrase  ',
    '',
])
def test_excerpts_are_bounded_exact_slices_with_no_lost_nonwhitespace(text):
    s = source(text)
    excerpts = source_excerpts(s)
    covered = set()
    assert len({e.id for e in excerpts}) == len(excerpts)
    for e in excerpts:
        assert 0 <= e.start < e.end <= len(text)
        assert e.text == text[e.start:e.end]
        assert 0 < len(e.text) <= 400
        covered.update(range(e.start,e.end))
    assert all(i in covered for i,c in enumerate(text) if not c.isspace())
    assert source_excerpts(s) == excerpts


def test_model_schema_cannot_supply_or_modify_a_quote():
    fields = EvidenceSelection.model_json_schema()['properties']
    assert 'quote' not in fields and 'source_start' not in fields
    with pytest.raises(ValidationError):
        EvidenceSelection(**selection().model_dump(),quote='Model-written replacement')


def test_unicode_quote_is_copied_and_full_context_is_checked():
    s = source('Water freezes at 0°C. This fictional measurement applies only to a specified sample.')
    excerpts = {e.id:e for e in source_excerpts(s)}
    judge = Judge()
    citation = asyncio.run(verify_selection(selection(), {'S1':s}, excerpts, judge, 'Claim'))
    assert citation.quote == 'Water freezes at 0°C.'
    assert citation.quote == s.text[citation.source_start:citation.source_end]
    assert citation.excerpt_id == 'S1:E1'
    assert citation.verified
    assert judge.inputs[0]['page'] == s.text
    assert judge.inputs[0]['claim'] == 'Claim'


@pytest.mark.parametrize('source_id,excerpt_id,code',[
    ('invented','S1:E1','unknown_source'),
    ('S1','invented','unknown_excerpt'),
    ('S1','S2:E1','unknown_excerpt'),
    ('S1',' ','unknown_excerpt'),
])
def test_bad_selection_is_rejected_without_model_call(source_id,excerpt_id,code):
    sources = {'S1':source('First source.'),'S2':source('Second source.','S2')}
    excerpts = {e.id:e for s in sources.values() for e in source_excerpts(s)}
    judge = Judge()
    citation = asyncio.run(verify_selection(selection(source_id,excerpt_id),sources,excerpts,judge,'Claim'))
    assert not citation.verified and citation.verification_code == code
    assert citation.quote == '' and judge.inputs == []
    assert citation.excerpt_id == excerpt_id


def test_existing_excerpt_does_not_bypass_attribution():
    s = source('The result applies to one sample, not every sample.')
    judge = Judge(False)
    result = asyncio.run(verify_selection(selection(),{'S1':s},{e.id:e for e in source_excerpts(s)},judge,'Every sample has this result.'))
    assert not result.verified
    assert result.verification_code == 'attribution_rejected'
    assert len(judge.inputs) == 1


def test_pipeline_passes_excerpts_and_keeps_good_evidence_when_id_is_invalid():
    import json
    class Provider(Judge):
        async def search(self, query):
            return [{'url':'https://example.org/source','title':'Fixture'}]
        async def structured(self, schema, instructions, data):
            if schema is Analysis:
                pages = json.loads(data)['sources']
                assert 'text' not in pages[0]  # Avoid sending each page twice.
                assert pages[0]['excerpts'][0]['text'] == 'Water freezes at 0°C.'
                return Analysis(verdict='TRUE',evidence=[selection(),selection(excerpt_id='invented')],limitations=[])
            return await super().structured(schema,instructions,data)
    async def fetch(url):
        return url,'Water freezes at 0°C. Synthetic fixture only.'
    result = asyncio.run(research_claim(AtomicClaim(text='Water freezes.',context='Fixture'),Provider(),fetch))
    # An invented excerpt ID is shown as rejected but no longer blocks the verdict.
    assert result.withheld_reason != 'citation_failed' and result.status == 'complete'
    assert len(result.evidence) == len(result.rejected_citations) == 1
    assert result.evidence[0].quote == 'Water freezes at 0°C.'
    assert result.rejected_citations[0].verification_code == 'unknown_excerpt'
    assert any('were ignored' in text for text in result.limitations)
