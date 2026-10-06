"""Run from backend: .venv/bin/python -m evaluation.run. No keys or network."""
import argparse
import asyncio
import json
from pathlib import Path

from schemas import VerdictDecision, EvidenceRelation, Analysis, AtomicClaim, CitationJudgment
from services.pipeline import research_claim
from tools.providers import ProviderFailure

CASE_PATH = Path(__file__).with_name('cases.json')


def load_cases():
    cases = json.loads(CASE_PATH.read_text())
    ids = [case['id'] for case in cases]
    if not cases or len(ids) != len(set(ids)):
        raise ValueError('Evaluation case IDs must be nonempty and unique.')
    return cases


class FixtureProvider:
    """Replay scripted model responses, never infer real-world facts."""
    def __init__(self, case):
        self.case = case
        self.search_calls = 0
        self.relations = 0
        self.judgments = 0

    async def search(self, query):
        self.search_calls += 1
        if self.search_calls in self.case.get('failed_searches', []):
            raise ProviderFailure('Simulated search failure')
        return [{'url': page['url'], 'title': 'Fictional evaluation source'} for page in self.case['pages']]

    async def structured(self, schema, instructions, data):
        if schema is Analysis:
            return Analysis.model_validate(self.case['candidate'])
        if schema is VerdictDecision:
            return VerdictDecision(verdict=self.case['candidate']['verdict'], evidence_ids=[e['id'] for e in json.loads(data)['verified_evidence']])
        if schema is EvidenceRelation:
            item = self.case['candidate']['evidence'][self.relations]
            self.relations += 1
            return EvidenceRelation(relation={'FOR':'SUPPORTS','AGAINST':'CONTRADICTS','CONTEXT':'BACKGROUND'}[item['stance']], reason='Scripted relation')
        if schema is CitationJudgment:
            self.judgments += 1
            approved = json.loads(data)['statement'] not in self.case.get('rejected_statements', [])
            return CitationJudgment(supports_attribution=approved, stance_matches=True, reason='Scripted offline judgment; no model called.')
        raise AssertionError(f'Unexpected model schema: {schema}')

    async def fetch(self, url):
        page = next(page for page in self.case['pages'] if page['url'] == url)
        if page.get('unavailable'):
            raise ValueError('Simulated inaccessible page')
        return url, page['text']


async def evaluate_case(case):
    provider = FixtureProvider(case)
    result = await research_claim(AtomicClaim(text=case['claim'], context='Fictional offline evaluation'), provider, provider.fetch)
    actual = {'verdict': result.verdict, 'evidence_count': len(result.evidence)}
    expected = case['expected']
    checks = {key: actual[key] == value for key, value in expected.items()}
    checks['both_searches_attempted'] = provider.search_calls == 2
    checks['all_returned_citations_checked'] = all(item.verified for item in result.evidence)
    if 'expected_judgment_calls' in case:
        checks['judgment_calls'] = provider.judgments == case['expected_judgment_calls']
    return {'id': case['id'], 'purpose': case['purpose'], 'passed': all(checks.values()),
            'checks': checks, 'expected': expected, 'actual': actual}


async def evaluate_all():
    results = []
    for case in load_cases():
        try:
            results.append(await evaluate_case(case))
        except Exception as exc:
            results.append({'id': case['id'], 'passed': False, 'error_type': type(exc).__name__})
    return {'suite': 'offline_policy_regression', 'synthetic': True,
            'limitation': 'Scripted sources and model judgments. Does not measure factual accuracy, search quality, or model citation precision.',
            'external_calls': 0, 'passed': sum(item['passed'] for item in results),
            'total': len(results), 'results': results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='Optional generated JSON report, e.g. evaluation/results/latest.json')
    args = parser.parse_args()
    summary = asyncio.run(evaluate_all())
    for item in summary['results']:
        print(f"{'PASS' if item['passed'] else 'FAIL'} {item['id']}")
    print(f"\n{summary['passed']}/{summary['total']} policy cases passed; zero external calls.")
    print(summary['limitation'])
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(summary, indent=2) + '\n')
    return 0 if summary['passed'] == summary['total'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
