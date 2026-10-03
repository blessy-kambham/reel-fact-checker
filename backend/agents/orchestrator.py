"""Orchestrator.

Runs the agents in order and owns the shared state for one fact-check. It is ordinary code, not a
model: the order of the steps and the rules for withholding a verdict are fixed here, so no agent
can skip a check.

    Claim Extractor ─▶ for each claim, two at a time:
                         Research Agent ─▶ Analyst Agent ─▶ Citation Verifier ─▶ Verdict Agent

Used by: `main.py` (statements), `services/article.py` (articles) and `services/video.py` (videos).
"""
import asyncio
from uuid import uuid4

from schemas import ClaimResult, Report
from services.budget import BudgetExceeded
from services.fetcher import fetch_text
from services.input_mapping import map_input
from services.providers import ProviderFailure

from agents.analyst_agent import AnalystAgent
from agents.citation_verifier import CitationVerifierAgent
from agents.claim_extractor import ClaimExtractorAgent
from agents.research_agent import ResearchAgent
from agents.shared import (COMPLETE_WITHHELD_REASONS, INVALID_REFERENCE_CODES, SINGLE_SOURCE_NOTE,
                           WITHHELD_MESSAGES, now, unresolved)
from agents.verdict_agent import VerdictAgent

MAX_PARALLEL_CLAIMS = 2
CLAIM_TIMEOUT_SECONDS = 150


async def verify_citation(draft, sources, provider, claim_text=''):
    """Citation Verifier step for one drafted citation."""
    return await CitationVerifierAgent(provider).verify(draft, sources, claim_text)


async def verify_selection(selection, sources, excerpts, provider, claim_text):
    """Analyst relation check, then the Citation Verifier, for one selected passage."""
    rejected, draft, metadata = await AnalystAgent(provider).classify(selection, sources, excerpts, claim_text)
    if rejected is not None:
        return rejected
    # Independent attribution AND stance validation remains mandatory after classification.
    citation = await verify_citation(draft, sources, provider, claim_text)
    return citation.model_copy(update=metadata)


async def research_claim(claim, provider, fetch=fetch_text, exclude=frozenset(), copy_markers=()) -> ClaimResult:
    """Take one claim through research, analysis, citation verification and the verdict."""
    # 1. Research Agent: search both directions and read the pages.
    pack = await ResearchAgent(provider, fetch).gather(claim, exclude, copy_markers)
    sources, warnings, search_status = pack.sources, pack.warnings, pack.search_status
    if not sources:
        result = unresolved(claim.text, 'No readable sources were retrieved. Search snippets are not accepted as verified evidence.',
                            'search_failed' if pack.search_failed else 'no_sources')
        return result.model_copy(update={'supporting_search': search_status['FOR'], 'contradicting_search': search_status['AGAINST'], 'limitations': result.limitations + warnings})

    # 2. Analyst Agent: select passages for and against the claim.
    analysis, excerpts = await AnalystAgent(provider).propose(claim, sources)

    # 3. Analyst relation check and Citation Verifier, for every selected passage.
    citations = []
    for draft in analysis.evidence:
        citations.append(await verify_selection(draft, sources, excerpts, provider, claim.text))
    # A proposal pointing at a source or excerpt that was never supplied, or at a passage the relation
    # check found irrelevant or unresolvable for this claim, is an analyst slip, not evidence: it is shown
    # as rejected but does not block a verdict. Misattribution and unavailable checks still withhold it.
    ignored = [c for c in citations if not c.verified and c.verification_code in INVALID_REFERENCE_CODES]
    failed = any(not c.verified and c.verification_code not in INVALID_REFERENCE_CODES for c in citations)
    if ignored:
        warnings.append(f'{len(ignored)} proposed citation(s) were not supplied material or did not concern this claim, and were ignored.')
    if failed:
        warnings.append(f'{sum(not c.verified for c in citations) - len(ignored)} citation(s) failed validation and were excluded.')
    usable = [citation.model_copy(update={'evidence_id': f'E{i + 1}'})
              for i, citation in enumerate(c for c in citations if c.verified)]

    # 4. Verdict Agent: only when the research and the citations allow a verdict at all.
    search_failed = pack.search_failed
    decision_verdict, decision_ids, withheld = None, [], None
    if search_failed:
        withheld = 'search_failed'
    elif failed:
        withheld = 'citation_failed'
    elif not any(c.stance in ('FOR', 'AGAINST') for c in usable):
        withheld = 'no_relevant_evidence'
    else:
        outcome = await VerdictAgent(provider).decide(claim.text, usable)
        decision_verdict, decision_ids, withheld = outcome.verdict, outcome.evidence_ids, outcome.withheld

    verdict = decision_verdict if withheld is None else 'UNVERIFIABLE'
    if withheld:
        warnings.append(WITHHELD_MESSAGES[withheld])
    incomplete = search_failed or (withheld is not None and withheld not in COMPLETE_WITHHELD_REASONS)
    verdict_sources = None
    if withheld is None:
        verdict_sources = len({c.url or c.source_id for c in usable if c.evidence_id in decision_ids})
        if verdict != 'UNVERIFIABLE' and verdict_sources == 1:
            warnings.append(SINGLE_SOURCE_NOTE)
    if not any(c.stance == 'AGAINST' for c in usable):
        warnings.append('No verified contradicting evidence was identified in the retrieved pages. This does not prove the claim.')
    warnings.append('Citation checks use quote matching and a separate model judgment; human review may still find errors.')
    warnings.append('Source independence, publication dates, and methodology require review; no calibrated confidence score is available.')
    return ClaimResult(claim=claim.text, verdict=verdict, status='incomplete' if incomplete else 'complete',
                       evidence=usable, rejected_citations=[c for c in citations if not c.verified], limitations=list(dict.fromkeys(warnings)),
                       supporting_search=search_status['FOR'], contradicting_search=search_status['AGAINST'], sources_checked=len(sources),
                       verdict_state='withheld' if withheld else 'issued', withheld_reason=withheld,
                       withheld_message=WITHHELD_MESSAGES[withheld] if withheld else None,
                       decision_verdict=decision_verdict, decision_evidence_ids=decision_ids,
                       verdict_evidence_ids=[] if withheld else decision_ids, verdict_source_count=verdict_sources)


async def research_all(claims, provider, fetch=fetch_text, exclude=frozenset(), copy_markers=None):
    """Run one research branch per claim, at most two at a time; every failure becomes a named withheld verdict."""
    limiter = asyncio.Semaphore(MAX_PARALLEL_CLAIMS)
    async def branch(claim):
        async with limiter:
            try:
                markers = (copy_markers or {}).get(claim.text, ())
                return await asyncio.wait_for(research_claim(claim, provider, fetch, exclude, markers), timeout=CLAIM_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                return unresolved(claim.text, 'This claim exceeded its research time limit.', 'claim_timeout')
            except BudgetExceeded as exc:
                return unresolved(claim.text, str(exc), 'spending_limit')
            except ProviderFailure as exc:
                return unresolved(claim.text, str(exc), 'provider_failure')
    return list(await asyncio.gather(*(branch(c) for c in claims)))


def exact_submission_preserved(text, extraction):
    """An unchanged full submission cannot have lost words during extraction.

    This verifies representation only, never truth or atomicity.
    """
    if extraction.intent != 'FACTUAL' or extraction.omitted_claims or len(extraction.claims) != 1:
        return False
    claim = extraction.claims[0]
    return (claim.text.strip() == text.strip() and
            (not claim.context.strip() or claim.context.strip() in text))


async def run_pipeline(text, provider, fetch=fetch_text) -> Report:
    """Fact-check a typed statement."""
    extractor = ClaimExtractorAgent(provider)
    extraction = await extractor.extract_from_statement(text)
    # Claims must map word for word onto the submission; otherwise a separate audit judges the
    # original submission, not the extractor's confidence.
    input_spans = map_input(text, extraction)
    coverage_status = 'incomplete' if extraction.omitted_claims else 'passed'
    coverage_issues = []
    if not extraction.omitted_claims and input_spans is None:
        coverage_status, coverage_issues = await extractor.audit_coverage(text, extraction)
    if coverage_status != 'passed':
        reason = ('Extraction coverage could not be checked. No research was started.'
                  if coverage_status == 'unavailable' else
                  'Extraction did not cover the submission faithfully. No research was started; submit assertions separately.')
        return Report(id=str(uuid4()), mode='live', submitted_text=text, created_at=now(),
                      intent=extraction.intent, note=reason, claims=[unresolved(text, reason, 'coverage_failed')],
                      limitations=[reason] + coverage_issues, usage=provider.usage,
                      omitted_claims=extraction.omitted_claims, coverage_status=coverage_status, input_spans=input_spans or [])
    results = await research_all(extraction.claims, provider, fetch) if extraction.intent == 'FACTUAL' else []
    limitations = ['At most three claims and six search results per claim are checked per report.']
    return Report(id=str(uuid4()), mode='live', submitted_text=text, created_at=now(), intent=extraction.intent,
                  note=extraction.note, claims=results, limitations=limitations, usage=provider.usage, omitted_claims=extraction.omitted_claims, coverage_status=coverage_status, input_spans=input_spans or [])
