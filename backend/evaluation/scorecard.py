"""Scorecard: run claims with known answers through the real system and measure how it does.

  python -m evaluation.scorecard
      Offline (the default): lists the cases and summarises any results already saved. No paid calls.

  python -m evaluation.scorecard --allow-paid --name NAME --max-usd 1.00 --max-searches 150
      Runs every case that has no saved result yet, three at a time, under a spending cap that
      persists with the results. Stopping and starting again continues where it left off.

  --cases FILE   use another set of cases, for example `evaluation/scorecard_cases_fresh.json`, a
                 set kept aside so that changes can be scored on claims they were not chosen from.
  --only A,B     run only the cases with these IDs.

Each case is one claim and the verdicts that count as right for it (`scorecard_cases.json`). The
reference answer is never shown to the model. Results are written to
`evaluation/results/scorecard-NAME/` and stay local; `summary.md` there is the table to publish.

What is measured:
- accuracy: the share of cases that ended with a verdict that counts as right;
- wrong verdicts: a verdict was issued and it was not right (the error that matters most);
- no verdict: a verdict was expected but it was withheld or came back UNVERIFIABLE;
- citation checks failed: passages the citation verifier rejected, out of those it was given;
- cost and time per claim, and how many independent sites each verdict cites.

Results saved before 6 October 2026 also carry a "what if". At that time one failed citation
withheld the whole verdict, and for each such claim the verdict agent was asked what it would
conclude from the passages that did pass. That showed what the rule cost, and the rule was then
changed: a failed passage is now left out and the rest decide. Summaries of those earlier results
still report the what-if.
"""
import argparse
import asyncio
import json
import math
import os
import re
import statistics
import time
from collections import Counter
from decimal import Decimal
from pathlib import Path

from agents.orchestrator import run_pipeline
from agents.shared import SITE_GOAL
from services.budget import PRICES, Budget, BudgetExceeded
from tools import credibility
from tools.providers import ProviderFailure, Providers, missing_settings

CASES = Path(__file__).with_name('scorecard_cases.json')
RESULTS = Path(__file__).with_name('results')
PARALLEL = 3
# A new case is not started once spending is this close to the cap, so cases in flight can finish.
RESERVE_PER_CASE = Decimal('0.04')
MAX_USD_CEILING = Decimal('2.00')
CITATION_FAILURES = ('attribution_rejected', 'stance_opposed', 'quote_not_found', 'empty_quote')
# The targets this project's design brief sets, for comparison in the summary.
TARGETS = {'accuracy': 0.85, 'no_verdict_rate': 0.15, 'citation_failure_rate': 0.05, 'cost_per_claim_usd': 0.15,
           'p95_seconds': 120}


def load_cases(path: Path = CASES) -> list[dict]:
    cases = json.loads(path.read_text())
    ids = [case['id'] for case in cases]
    if not cases or len(ids) != len(set(ids)):
        raise ValueError('Scorecard case IDs must be nonempty and unique.')
    for case in cases:
        if case['expected'] not in case['accepted']:
            raise ValueError(f"{case['id']}: the expected verdict must be one of the accepted verdicts.")
    return cases


# ---- scoring: pure functions over saved results ------------------------------------------------

def outcome(case: dict, claim: dict | None) -> str:
    """How one case ended: 'correct', 'wrong', 'no_verdict' or 'error'.

    A withheld verdict is shown to the user as UNVERIFIABLE, so for a case that cannot be checked it
    counts as right, and for every other case it counts as no verdict, never as wrong.
    """
    if claim is None:
        return 'error'
    issued = claim.get('verdict_state') == 'issued' and claim['verdict'] != 'UNVERIFIABLE'
    if 'UNVERIFIABLE' in case['accepted'] and not issued:
        return 'correct'
    if not issued:
        return 'no_verdict'
    return 'correct' if claim['verdict'] in case['accepted'] else 'wrong'


def _scored_claim(saved: dict) -> dict | None:
    """The claim result a case is scored on, or None when the case did not produce one to score."""
    report = saved.get('report')
    if report is None or saved.get('error'):
        return None
    claims = report.get('claims') or []
    if len(claims) == 1:
        return claims[0]
    if not claims:
        # Nothing was treated as a checkable claim (for example it was classed as opinion): no verdict.
        return {'verdict': 'UNVERIFIABLE', 'verdict_state': 'withheld', 'withheld_reason': 'no_claim_extracted', 'evidence': []}
    return None  # split into several claims: these cases are written as one claim each


def _percentile(values: list[float], share: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered), max(1, math.ceil(share * len(ordered)))) - 1]  # nearest rank


def summarize(cases: list[dict], results: dict) -> dict:
    """Every number in the scorecard, from the saved result of each case."""
    rows, passages, strengths, sites_cited, by_confidence = [], Counter(), Counter(), [], {}
    for case in cases:
        saved = results.get(case['id'])
        if saved is None:
            continue
        claim = _scored_claim(saved)
        row = {'id': case['id'], 'claim': case['claim'], 'expected': case['expected'], 'accepted': case['accepted'],
               'verdict': claim['verdict'] if claim else None, 'outcome': outcome(case, claim),
               'withheld_reason': claim.get('withheld_reason') if claim else None,
               'error': saved.get('error'), 'seconds': saved.get('seconds'), 'cost_usd': saved.get('cost_usd'),
               'model_calls': (saved.get('usage') or {}).get('model_calls'),
               'search_calls': (saved.get('usage') or {}).get('search_calls'),
               'if_failed_citations_were_dropped': saved.get('what_if')}
        if claim:
            passages['verified'] += len(claim['evidence'])
            for rejected in claim.get('rejected_citations', []):
                passages[rejected.get('verification_code')] += 1
            if row['outcome'] in ('correct', 'wrong') and claim['verdict'] != 'UNVERIFIABLE':
                cited = [e for e in claim['evidence'] if e.get('evidence_id') in claim.get('verdict_evidence_ids', [])]
                row['sites_cited'] = len({credibility.domain(e.get('url')) for e in cited if credibility.domain(e.get('url'))})
                sites_cited.append(row['sites_cited'])
                strengths[claim.get('evidence_strength') or 'not rated'] += 1
                if claim.get('confidence'):
                    row['confidence'] = claim['confidence']
                    tally = by_confidence.setdefault(claim['confidence'], {'right': 0, 'wrong': 0})
                    tally['right' if row['outcome'] == 'correct' else 'wrong'] += 1
        rows.append(row)

    ran = [row for row in rows if row['outcome'] != 'error']
    expecting = [row for row in ran if 'UNVERIFIABLE' not in row['accepted']]
    count = Counter(row['outcome'] for row in rows)
    answered = count['correct'] + count['wrong']
    failed = sum(passages[code] for code in CITATION_FAILURES)
    reached = passages['verified'] + failed
    seconds = [row['seconds'] for row in ran if row['seconds'] is not None]
    costs = [row['cost_usd'] for row in ran if row['cost_usd'] is not None]

    # Earlier results only: what if a failed citation were dropped instead of withholding the verdict?
    relaxed_correct = count['correct']
    relaxed_wrong = count['wrong']
    changed = []
    for row in ran:
        what_if = row['if_failed_citations_were_dropped']
        if row['outcome'] == 'no_verdict' and what_if and what_if != 'UNVERIFIABLE':
            right = what_if in row['accepted']
            relaxed_correct, relaxed_wrong = relaxed_correct + right, relaxed_wrong + (not right)
            changed.append({'id': row['id'], 'would_be': what_if, 'right': right})

    def share(part, whole):
        return round(part / whole, 4) if whole else None

    return {
        'cases': len(cases), 'ran': len(ran), 'not_run': len(cases) - len(ran),
        'correct': count['correct'], 'wrong': count['wrong'], 'no_verdict': count['no_verdict'], 'errors': count['error'],
        'accuracy': share(count['correct'], len(ran)),
        'accuracy_when_a_verdict_was_issued': share(count['correct'], answered),
        'no_verdict_rate': share(count['no_verdict'], len(expecting)),
        'withheld_reasons': dict(sorted(Counter(r['withheld_reason'] for r in ran if r['withheld_reason']).items())),
        'passages_checked_by_citation_verifier': reached, 'passages_it_rejected': failed,
        'citation_failure_rate': share(failed, reached),
        'passages_set_aside_before_verification': sum(v for k, v in passages.items() if k not in CITATION_FAILURES + ('verified',)),
        'cost_total_usd': round(sum(costs), 4) if costs else None,
        'cost_per_claim_usd': round(statistics.mean(costs), 4) if costs else None,
        'seconds_median': _percentile(seconds, 0.5), 'seconds_p95': _percentile(seconds, 0.95),
        'model_calls_per_claim': round(statistics.mean(r['model_calls'] for r in ran if r['model_calls'] is not None), 1) if ran else None,
        'searches_per_claim': round(statistics.mean(r['search_calls'] for r in ran if r['search_calls'] is not None), 1) if ran else None,
        'verdicts_citing_three_or_more_sites': sum(n >= SITE_GOAL for n in sites_cited),
        'verdicts_citing_one_site': sum(n == 1 for n in sites_cited), 'verdicts_with_sites_counted': len(sites_cited),
        'source_strength': dict(sorted(strengths.items())),
        # Does the confidence level mean anything? Right and wrong verdicts at each level.
        'verdicts_by_confidence': {level: by_confidence[level] for level in ('high', 'medium', 'low') if level in by_confidence},
        'if_failed_citations_were_dropped': {'accuracy': share(relaxed_correct, len(ran)), 'wrong': relaxed_wrong,
                                             'cases_that_would_change': changed},
        'targets': TARGETS, 'results': rows,
        'limitation': 'A small set of mostly well-known claims, labelled by the author. It shows how the system behaves '
                      'on these cases; it is not a measured accuracy rate for claims in general.',
    }


def markdown(summary: dict, name: str = '') -> str:
    """The summary as the table to publish."""
    def pct(value):
        return 'n/a' if value is None else f'{100 * value:.0f}%'

    def money(value):
        return 'n/a' if value is None else f'${value:.3f}'

    def met(ok):
        return 'n/a' if ok is None else ('met' if ok else 'not met')

    target, s = summary['targets'], summary
    lines = [f"Ran {s['ran']} of {s['cases']} cases" + (f' ({name})' if name else '') + '.', '',
             '| Measure | Result | Brief\'s target | |', '| --- | --- | --- | --- |',
             f"| Accuracy (right verdict) | {s['correct']} of {s['ran']} = {pct(s['accuracy'])} | {pct(target['accuracy'])} or more | "
             f"{met(None if s['accuracy'] is None else s['accuracy'] >= target['accuracy'])} |",
             f"| Wrong verdicts issued | {s['wrong']} | | |",
             f"| No verdict when one was expected | {s['no_verdict']} = {pct(s['no_verdict_rate'])} | under {pct(target['no_verdict_rate'])} | "
             f"{met(None if s['no_verdict_rate'] is None else s['no_verdict_rate'] < target['no_verdict_rate'])} |",
             f"| Citation checks failed | {s['passages_it_rejected']} of {s['passages_checked_by_citation_verifier']} passages = "
             f"{pct(s['citation_failure_rate'])} | under {pct(target['citation_failure_rate'])} | "
             f"{met(None if s['citation_failure_rate'] is None else s['citation_failure_rate'] < target['citation_failure_rate'])} |",
             f"| Cost per claim | {money(s['cost_per_claim_usd'])} | under {money(target['cost_per_claim_usd'])} | "
             f"{met(None if s['cost_per_claim_usd'] is None else s['cost_per_claim_usd'] < target['cost_per_claim_usd'])} |",
             f"| Time per claim, 95th percentile | {s['seconds_p95']} s (median {s['seconds_median']} s) | under {target['p95_seconds']} s | "
             f"{met(None if s['seconds_p95'] is None else s['seconds_p95'] < target['p95_seconds'])} |",
             f"| Verdicts citing three or more sites | {s['verdicts_citing_three_or_more_sites']} of {s['verdicts_with_sites_counted']} | all | "
             f"{met(s['verdicts_citing_three_or_more_sites'] == s['verdicts_with_sites_counted'] if s['verdicts_with_sites_counted'] else None)} |",
             '']
    if s.get('verdicts_by_confidence'):
        lines += ['Verdicts by confidence level: ' + '; '.join(
            f"{level}: {tally['right']} right, {tally['wrong']} wrong" for level, tally in s['verdicts_by_confidence'].items()) + '.', '']
    what_if = s['if_failed_citations_were_dropped']
    if what_if['cases_that_would_change']:
        lines += [f"If a failed citation were dropped instead of withholding the verdict: accuracy {pct(what_if['accuracy'])}, "
                  f"wrong verdicts {what_if['wrong']}.", '']
    lines += ['| Claim | Expected | Result | |', '| --- | --- | --- | --- |']
    marks = {'correct': 'right', 'wrong': 'WRONG', 'no_verdict': 'no verdict', 'error': 'not run'}
    for row in s['results']:
        result = row['verdict'] or '-'
        if row['outcome'] == 'no_verdict' and row['withheld_reason']:
            result = f"withheld ({row['withheld_reason'].replace('_', ' ')})"
        lines.append(f"| {row['claim']} | {' or '.join(row['accepted'])} | {result} | {marks[row['outcome']]} |")
    return '\n'.join(lines) + '\n'


# ---- running -----------------------------------------------------------------------------------

def _cost(usage: dict, model: str) -> float:
    input_price, output_price = PRICES[model]
    return float(Decimal(usage.get('input_tokens', 0)) * input_price + Decimal(usage.get('output_tokens', 0)) * output_price)


async def run_case(case: dict, budget: Budget, make_provider=Providers, fetch=None) -> dict:
    """One claim through the real pipeline. Always returns a result to save, never raises."""
    provider = make_provider()
    provider.spending = budget
    started = time.monotonic()
    saved = {'id': case['id'], 'claim': case['claim']}
    try:
        pipeline = run_pipeline(case['claim'], provider, fetch) if fetch else run_pipeline(case['claim'], provider)
        report = (await pipeline).model_dump(mode='json')
        saved['report'] = report
        if len(report['claims']) > 1:
            saved['error'] = f"split into {len(report['claims'])} claims"
    except BudgetExceeded:
        saved['error'] = 'spending_limit'
    except ProviderFailure:
        saved['error'] = 'provider_failure'
    finally:
        saved['seconds'] = round(time.monotonic() - started, 1)
        saved['usage'] = dict(provider.usage)
        saved['cost_usd'] = round(_cost(provider.usage, os.environ['OPENAI_MODEL']), 5)
        await provider.close()
    return saved


def _interrupted(saved: dict) -> bool:
    """Whether a saved case never reached a judgement because a provider or the spending cap stopped it.
    Such a case says nothing about the system's verdicts, so a later run of the same name tries it again."""
    stopped = ('spending_limit', 'provider_failure')
    claims = (saved.get('report') or {}).get('claims') or []
    return saved.get('error') in stopped or any(claim.get('withheld_reason') in stopped for claim in claims)


def load_results(directory: Path) -> dict:
    results = {}
    for path in sorted(directory.glob('case-*.json')):
        saved = json.loads(path.read_text())
        results[saved['id']] = saved
    return results


async def run(name: str, max_usd, max_searches: int, cases=None, results_root: Path = RESULTS, make_provider=Providers,
              say=print, fetch=None) -> dict:
    """Run every case without a saved result, under the named run's spending cap. Returns the summary."""
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,39}', name or ''):
        raise ValueError('Run names use 1-40 lowercase letters, digits and hyphens.')
    max_usd = Decimal(str(max_usd))
    if not Decimal('0') < max_usd <= MAX_USD_CEILING:
        raise ValueError(f'--max-usd must be above 0 and at most {MAX_USD_CEILING}.')
    cases = cases if cases is not None else load_cases()
    directory = results_root / f'scorecard-{name}'
    directory.mkdir(parents=True, exist_ok=True)
    budget = Budget(directory / 'ledger.json', search_limit=max_searches, max_usd=max_usd, model=os.environ['OPENAI_MODEL'])
    done = load_results(directory)
    waiting = [case for case in cases if case['id'] not in done or _interrupted(done[case['id']])]
    say(f'{len(cases) - len(waiting)} of {len(cases)} cases already have a result; running {len(waiting)}.')
    limiter, active = asyncio.Semaphore(PARALLEL), 0

    async def one(case):
        nonlocal active
        async with limiter:
            if budget.stopped or budget.reserved + RESERVE_PER_CASE * (active + 1) > budget.max_usd:
                say(f"  skipped {case['id']}: too close to the spending cap")
                return
            active += 1
            try:
                saved = await run_case(case, budget, make_provider, fetch)
            finally:
                active -= 1
            (directory / f"case-{case['id']}.json").write_text(json.dumps(saved, indent=2))
            claim = _scored_claim(saved)
            say(f"  {case['id']}: {saved.get('error') or outcome(case, claim)} "
                f"({(claim or {}).get('verdict')}, {saved['seconds']} s, ${saved['cost_usd']:.3f})")

    await asyncio.gather(*(one(case) for case in waiting))
    summary = summarize(cases, load_results(directory))
    summary['spent_usd'], summary['searches'] = float(budget.reserved), budget.searches
    (directory / 'summary.json').write_text(json.dumps(summary, indent=2))
    (directory / 'summary.md').write_text(markdown(summary, name))
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--allow-paid', action='store_true', help='Run the cases against the real providers.')
    parser.add_argument('--name', help='Name of the run; results and its spending cap are kept under it.')
    parser.add_argument('--max-usd', type=Decimal, help='Model spending cap for this run.')
    parser.add_argument('--max-searches', type=int, default=150, help='Search cap for this run.')
    parser.add_argument('--cases', type=Path, default=CASES, help='Cases file (default: scorecard_cases.json).')
    parser.add_argument('--only', default='', help='Comma-separated case IDs to run; the rest are left out.')
    args = parser.parse_args(argv)
    cases = load_cases(args.cases)
    wanted = {name.strip() for name in args.only.split(',') if name.strip()}
    if wanted - {case['id'] for case in cases}:
        parser.error('Unknown case IDs: ' + ', '.join(sorted(wanted - {case['id'] for case in cases})))
    if wanted:
        cases = [case for case in cases if case['id'] in wanted]
    if not args.allow_paid:
        print(f'{len(cases)} cases: ' + ', '.join(f'{count} {verdict}' for verdict, count in Counter(c['expected'] for c in cases).items()))
        for directory in sorted(RESULTS.glob('scorecard-*')):
            print(f'\n{directory.name}\n' + markdown(summarize(cases, load_results(directory))))
        print('Offline only; no network or paid calls were made.')
        return
    if not args.name or args.max_usd is None:
        parser.error('--allow-paid requires --name and --max-usd.')
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / '.env')  # values are used, never printed
    if missing_settings():
        raise SystemExit('Provider settings are missing: ' + ', '.join(missing_settings()))
    if os.environ['OPENAI_MODEL'] not in PRICES:
        raise SystemExit('The spending cap only knows prices for: ' + ', '.join(PRICES))
    summary = asyncio.run(run(args.name, args.max_usd, args.max_searches, cases))
    print('\n' + markdown(summary, args.name))
    print(f"Spent ${summary['spent_usd']:.3f} and {summary['searches']} searches. Saved in evaluation/results/scorecard-{args.name}/")


if __name__ == '__main__':
    main()
