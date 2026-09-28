"""Summarize local live traces without making network requests."""
import argparse
import json
from pathlib import Path


def summarize(traces, cases):
    by_claim = {c['claim']: c for c in cases}
    rows = []
    for trace in traces:
        report = trace.get('response', {})
        claims = report.get('claims', [])
        submitted = report.get('submitted_text', trace.get('submitted_text'))
        case = by_claim.get(submitted)
        accepted = [c for claim in claims for c in claim['evidence']]
        rejected = [c for claim in claims for c in claim.get('rejected_citations', [])]
        final = claims[0]['verdict'] if len(claims) == 1 else None
        rows.append({'claim': submitted, 'http_status': trace.get('http_status'),
            'reference_verdict': case['reference_verdict'] if case else None,
            'final_verdict': final,
            'agreement': final == case['reference_verdict'] if case and final else False,
            'incomplete': not claims or any(c['status'] != 'complete' for c in claims),
            'accepted_citations': len(accepted), 'rejected_citations': len(rejected),
            'unsupported_citations': sum(c.get('verification_code') in ('unknown_source','unknown_excerpt','empty_quote','quote_not_found','attribution_rejected') for c in rejected),
            'extraction_failure': not any(m.get('stage') == 'Extraction' and m.get('output') for m in trace.get('model', [])),
            'provider_failures': sum('error' in x for x in trace.get('model', []) + trace.get('searches', [])),
            'rejections': [{'code':c.get('verification_code'), 'reason':c['verification'], 'quote':c['quote']} for c in rejected],
            'latency_seconds': trace.get('latency_seconds'), 'usage':report.get('usage', {})})
    accepted = sum(r['accepted_citations'] for r in rows)
    total = accepted + sum(r['rejected_citations'] for r in rows)
    scored = [r for r in rows if r['reference_verdict']]
    return {'cases_attempted':len(rows), 'cases_scored':len(scored),
        'verdict_agreement':sum(r['agreement'] for r in scored)/len(scored) if scored else None,
        'incomplete_cases':sum(r['incomplete'] for r in rows),
        'citation_acceptance_rate':accepted/total if total else None,
        'unsupported_citation_count':sum(r['unsupported_citations'] for r in rows),
        'extraction_failures':sum(r['extraction_failure'] for r in rows),
        'provider_failures':sum(r['provider_failures'] for r in rows),
        'human_reviewed_unsupported_accepted_citations': None,
        'limitation':'Unsupported citation count means proposals rejected by automated checks, not proven false claims. Automated citation acceptance is not human-reviewed precision. Missing extraction requires diagnostic review. Small selected set is not an accuracy benchmark.',
        'results':rows}

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('traces', nargs='+', type=Path)
    args = parser.parse_args()
    cases = json.loads(Path(__file__).with_name('real_cases.json').read_text())
    print(json.dumps(summarize([json.loads(p.read_text()) for p in args.traces], cases), indent=2))
