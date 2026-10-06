"""Helpers and constants shared by the agents."""
import unicodedata
from datetime import datetime, timezone
from urllib.parse import urldefrag

from schemas import ClaimResult


# How many claims one report checks. Typed statements are split into their assertions; for an article or
# a video the claim extractor picks the central ones. schemas.Extraction allows up to the larger number.
MAX_STATEMENT_CLAIMS = 5
MAX_DOCUMENT_CLAIMS = 3


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalized(text: str) -> str:
    return ' '.join(text.split()).casefold()


# User-facing explanation for every WithheldReason. Messages never include provider error bodies.
WITHHELD_MESSAGES = {
    'coverage_failed': 'Extraction coverage was incomplete or could not be checked, so no research or verdict was attempted.',
    'no_sources': 'No readable sources were retrieved, so no verdict was attempted.',
    'search_failed': 'A search direction failed, so a verdict was withheld rather than judged on one-sided research.',
    'citation_failed': 'At least one proposed citation failed validation, so the verdict was withheld.',
    'no_relevant_evidence': 'No verified evidence directly supports or contradicts this claim, so no verdict was attempted.',
    'verdict_check_unavailable': 'The claim-specific verdict check was unavailable; the verdict was withheld.',
    'unknown_evidence_ids': 'The verdict cited evidence IDs that were not among the verified evidence; it was withheld.',
    'missing_evidence_ids': 'The verdict did not cite any verified evidence; it was withheld.',
    'evidence_stance_mismatch': 'The evidence the verdict cited does not have the direction that verdict requires; it was withheld.',
    'conflicting_evidence': 'Verified evidence supports and contradicts the claim. An unqualified verdict was withheld pending review.',
    'claim_timeout': 'This claim exceeded its research time limit, so the verdict was withheld.',
    'provider_failure': 'A research provider failed, so the verdict was withheld.',
    'spending_limit': 'The spending limit was reached before this claim finished, so the verdict was withheld.',
}
# Proposals that are the analyst's slips, not evidence problems: shown as rejected, never block a verdict.
INVALID_REFERENCE_CODES = frozenset({'unknown_source', 'unknown_excerpt', 'empty_quote', 'relation_unresolved'})
SINGLE_SOURCE_NOTE = 'This verdict rests on a single web page. Check that source before relying on it.'
# Reasons that reflect a legitimate research outcome rather than a failed or rejected check.
COMPLETE_WITHHELD_REASONS = {'no_relevant_evidence', 'conflicting_evidence'}

# Quote marks, apostrophes and dash styles vary between copies of the same words; only these are ignored.
IGNORED_MARKS = str.maketrans('', '', "'’‘`\"“”")
DASHES = str.maketrans({'–': '-', '—': '-', '‑': '-', '‐': '-'})
COPY_MARKER_MIN_WORDS = 12


def loose(text: str) -> str:
    return normalized(unicodedata.normalize('NFKC', text).translate(IGNORED_MARKS).translate(DASHES))


def page_key(url: str) -> str:
    """Compare pages without fragments or a trailing slash."""
    return urldefrag(url or '')[0].rstrip('/').casefold()


def unresolved(claim: str, reason: str, code: str) -> ClaimResult:
    return ClaimResult(claim=claim, verdict='UNVERIFIABLE', status='incomplete', evidence=[],
                       limitations=[reason], supporting_search='Incomplete', contradicting_search='Incomplete', sources_checked=0,
                       withheld_reason=code, withheld_message=WITHHELD_MESSAGES[code])
