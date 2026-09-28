"""Recheck an exact rejected CONTEXT citation, without rerunning web searches.
Default is an offline inspection. --allow-paid requires an existing budget ledger.
"""
import argparse
import asyncio
import json
from pathlib import Path
from schemas import EvidenceDraft, Source
from services.pipeline import verify_citation


def extract_case(trace):
    for claim in trace.get('response', {}).get('claims', []):
        for citation in claim.get('rejected_citations', []):
            if citation['stance'] != 'CONTEXT':
                continue
            matches = [m['input'] for m in trace.get('model', [])
                       if m['stage'] == 'CitationJudgment' and
                       all(m['input'].get(k) == citation[k] for k in ('quote','statement','stance')) and
                       m['input'].get('claim') == claim['claim']]
            if len(matches) != 1:
                raise ValueError('Need exactly one matching saved verifier input; no source text will be guessed.')
            data = matches[0]
            draft = EvidenceDraft(**{k:citation[k] for k in ('source_id','quote','statement','stance')})
            source = Source(id=citation['source_id'],title=citation['title'],url=citation['url'],
                            text=data['page'],retrieved_at=citation['retrieved_at'])
            return claim['claim'], draft, source
    raise ValueError('No rejected CONTEXT citation exists in this trace.')


async def evaluate(trace, provider):
    claim, draft, source = extract_case(trace)
    result = await verify_citation(draft, {source.id:source}, provider, claim)
    return {'claim':claim,'previous_status':'rejected','citation':result.model_dump(),
            'scope':'Attribution/stance check on the exact saved page; not fresh research or a full verdict.',
            'expected':'Relevant, faithfully attributed background may pass CONTEXT; human review remains required.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('trace',type=Path)
    parser.add_argument('--allow-paid',action='store_true')
    parser.add_argument('--ledger',type=Path)
    parser.add_argument('--output',type=Path)
    args = parser.parse_args()
    trace = json.loads(args.trace.read_text())
    claim, draft, source = extract_case(trace)
    if not args.allow_paid:
        print(json.dumps({'mode':'offline_inspection','claim':claim,'statement':draft.statement,
                          'quote':draft.quote,'source_url':source.url,'external_calls':0},indent=2))
        return
    if not args.ledger or not args.ledger.exists() or not args.output:
        parser.error('Paid mode requires an existing --ledger and --output; it never creates a new allowance.')
    from dotenv import load_dotenv
    import os
    from evaluation.live import AuditProvider, Budget
    load_dotenv(Path(__file__).parents[1]/'.env')
    if os.getenv('OPENAI_MODEL') != 'gpt-4.1-mini' or not os.getenv('OPENAI_API_KEY'):
        parser.error('Configured gpt-4.1-mini and OpenAI key are required.')
    audit = {'model':[],'searches':[]}
    async def run():
        provider = AuditProvider(Budget(args.ledger), audit)
        try:
            result = await evaluate(trace,provider)
            result.update(usage=provider.usage,audit=audit)
            return result
        finally:
            await provider.close()
    result = asyncio.run(run())
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'verified':result['citation']['verified'], 'reason':result['citation']['verification'],
                      'usage':result['usage']},indent=2))

if __name__ == '__main__':
    main()
