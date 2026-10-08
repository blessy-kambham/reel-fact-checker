"""Response Formatter: the short version of a report, for pasting into a message.

The design brief's short "DM format" is a verdict, one line and a link to the full report, in at most
280 characters before the link. The brief gives this job to a model. Here it is plain code: the report
already holds the verdicts and the reasons, so a model rewording them could only add mistakes. The page
adds the link, because only the page knows the address it is served from.
"""
MAX_SUMMARY_CHARS = 280

NO_CLAIMS = {
    'FACTUAL': 'No checkable claims were found in it.',
    'OPINION': 'It reads as opinion, so there was nothing to fact-check.',
    'SATIRE': 'It reads as satire, so there was nothing to fact-check.',
    'FICTIONAL': 'It reads as fiction, so there was nothing to fact-check.',
    'UNRELATED': 'Nothing in it could be fact-checked.',
}
# Why there is no verdict, in a few words. Every other reason is a check that could not be completed.
NO_VERDICT = {
    'no_relevant_evidence': 'no checked evidence addressed it',
    'conflicting_evidence': 'the checked evidence points both ways',
    'citation_disputed': 'the checks disagreed about the evidence',
}


def _clip(text: str, limit: int = MAX_SUMMARY_CHARS) -> str:
    return text if len(text) <= limit else text[:limit - 1].rstrip() + '…'


def _quoted(prefix: str, claim: str, suffix: str) -> str:
    """`prefix "claim" suffix`, with the claim shortened so the whole line fits."""
    claim = ' '.join(claim.split())
    room = MAX_SUMMARY_CHARS - len(prefix) - len(suffix) - 2
    if len(claim) > room:
        claim = claim[:max(room - 1, 0)].rstrip() + '…'
    return _clip(f'{prefix}"{claim}"{suffix}')


def _claim_line(claim) -> str:
    if claim.verdict_state == 'issued' and claim.verdict != 'UNVERIFIABLE':
        details = []
        if claim.verdict_site_count:
            details.append(f'checked against {claim.verdict_site_count} site{"" if claim.verdict_site_count == 1 else "s"}')
        if claim.confidence:
            details.append(f'{claim.confidence} confidence')
        tail = f' {", ".join(details)}.' if details else ''
        return _quoted(f'{claim.verdict}: ', claim.claim, tail[:2].upper() + tail[2:])
    why = 'not enough public evidence' if claim.verdict_state == 'issued' else \
        NO_VERDICT.get(claim.withheld_reason or '', 'the check could not be completed')
    return _quoted('UNVERIFIABLE: ', claim.claim, f' No verdict: {why}.')


def short_summary(report) -> str | None:
    """The report in at most 280 characters, or None for the demo, which is not a real check."""
    if report.mode != 'live':
        return None
    if not report.claims:
        return _clip(f'No claims were checked. {NO_CLAIMS.get(report.intent, NO_CLAIMS["UNRELATED"])}')
    if len(report.claims) == 1:
        return _claim_line(report.claims[0])
    overall = report.overall_verdict or 'UNVERIFIABLE'
    return _clip(f'Overall {overall}. {report.overall_summary or f"{len(report.claims)} claims checked."}')
