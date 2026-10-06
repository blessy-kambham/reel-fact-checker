"""What a report says about its verdicts beyond the verdicts themselves: a confidence level for each
issued verdict, and one overall verdict for a submission with several claims.

Both are rules over things that can be counted. Neither is a model's opinion, and the confidence
level is not a probability: thirty scored claims (docs/SCORECARD.md) are far too few to turn into a
percentage, so the report says "high", "medium" or "low" and lists why.
"""
from collections import Counter

from agents.shared import SITE_GOAL

LEVELS = ('low', 'medium', 'high')
WORDS = {'TRUE': 'true', 'FALSE': 'false', 'PARTIALLY TRUE': 'partially true', 'MISLEADING': 'misleading',
         'OUTDATED': 'outdated', 'SATIRE': 'satire'}


def confidence(sites: int, strength: str | None, both_sides: bool, rejected: int, selected: int) -> tuple[str, list[str]]:
    """How much weight an issued verdict can bear: (level, the reasons for it).

    It starts at high and loses a step for each weakness in the evidence behind the verdict:
    fewer than three different sites (two steps for a single site); sources that are not official,
    academic or established (two steps when none is, one when only one is); verified evidence on
    both sides of the claim; and more than a third of the selected passages failing the citation check.
    """
    level, reasons = 2, []
    if sites >= SITE_GOAL:
        reasons.append(f'{sites} different sites back it')
    elif sites <= 1:
        level -= 2
        reasons.append('it rests on a single site')
    else:
        level -= 1
        reasons.append(f'it rests on {sites} sites, fewer than {SITE_GOAL}')
    if strength == 'strong':
        reasons.append('two or more of them are official, academic or established sources')
    elif strength == 'moderate':
        level -= 1
        reasons.append('only one of them is an official, academic or established source')
    else:
        level -= 2
        reasons.append('none of them is an official, academic or established source')
    if both_sides:
        level -= 1
        reasons.append('verified evidence points both ways, so the verdict is a judgement between them')
    if selected and rejected * 3 > selected:
        level -= 1
        reasons.append(f'{rejected} of {selected} selected passages failed the citation check')
    return LEVELS[max(0, level)], reasons


def overall(claims) -> dict:
    """One verdict for a submission with two or more claims, as fields for the Report.

    It is built only from the claims that received a verdict. All true gives TRUE; a mix that
    includes something true gives PARTIALLY TRUE; a mix with nothing true gives FALSE when any
    claim is false, otherwise MISLEADING. When half or more of the claims have no verdict, the
    submission as a whole is UNVERIFIABLE.
    """
    if len(claims) < 2:
        return {'overall_verdict': None, 'overall_summary': None}
    judged = [c.verdict for c in claims if c.verdict_state == 'issued' and c.verdict != 'UNVERIFIABLE']
    unjudged = len(claims) - len(judged)
    counts = Counter(judged)
    parts = [f'{counts[verdict]} {WORDS[verdict]}' for verdict in WORDS if counts[verdict]]
    if unjudged:
        parts.append(f'{unjudged} without a verdict')
    summary = f'Of {len(claims)} claims: {", ".join(parts)}.'
    if unjudged * 2 >= len(claims):
        verdict = 'UNVERIFIABLE'
    elif len(counts) == 1:
        verdict = judged[0]
    elif counts['TRUE'] or counts['PARTIALLY TRUE']:
        verdict = 'PARTIALLY TRUE'
    else:
        verdict = 'FALSE' if counts['FALSE'] else 'MISLEADING'
    if unjudged and verdict != 'UNVERIFIABLE':
        summary += ' The overall verdict covers only the claims that received one.'
    return {'overall_verdict': verdict, 'overall_summary': summary}
