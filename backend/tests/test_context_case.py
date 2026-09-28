import asyncio
import pytest
from schemas import CitationJudgment
from evaluation.context_case import extract_case, evaluate


def trace():
    citation = dict(source_id='S1',quote='A transition is a change of state.',
                    statement='A transition means a change of state.',stance='CONTEXT',
                    title='Synthetic fixture',url='https://example.org',retrieved_at='2026-09-28')
    return {'response':{'claims':[{'claim':'This sample freezes at 0°C.','rejected_citations':[citation]}]},
            'model':[{'stage':'CitationJudgment','input':dict(claim='This sample freezes at 0°C.',
                     quote=citation['quote'],statement=citation['statement'],stance='CONTEXT',
                     page='A transition is a change of state. Full context stays available.')} ]}


def test_saved_context_is_replayed_without_search():
    class Provider:
        async def structured(self,schema,instructions,data):
            import json
            assert schema is CitationJudgment
            assert json.loads(data)['page'].endswith('Full context stays available.')
            return CitationJudgment(supports_attribution=True,stance_matches=True,reason='Scripted')
    result = asyncio.run(evaluate(trace(),Provider()))
    assert result['citation']['verified']
    assert result['citation']['stance']=='CONTEXT'
    assert 'verdict' not in result


def test_missing_source_snapshot_is_not_reconstructed():
    data=trace();data['model']=[]
    with pytest.raises(ValueError,match='exactly one'):
        extract_case(data)


def test_ambiguous_source_snapshot_is_rejected():
    data=trace();data['model']*=2
    with pytest.raises(ValueError,match='exactly one'):
        extract_case(data)


def test_only_rejected_context_is_selected():
    data=trace();data['response']['claims'][0]['rejected_citations'][0]['stance']='FOR'
    with pytest.raises(ValueError,match='No rejected CONTEXT'):
        extract_case(data)
