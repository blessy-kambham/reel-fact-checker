"""Extract → parallel research → analysis → independent citation check."""
import asyncio
import json
from datetime import datetime, timezone
from uuid import uuid4

from schemas import Extraction, Analysis, CitationJudgment, Citation, ClaimResult, Report, Source
from services.fetcher import fetch_text
from services.providers import ProviderFailure


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalized(text: str) -> str:
    return ' '.join(text.split()).casefold()


def unresolved(claim: str, reason: str) -> ClaimResult:
    return ClaimResult(claim=claim, verdict='UNVERIFIABLE', status='incomplete', evidence=[],
                       limitations=[reason], supporting_search='Incomplete', contradicting_search='Incomplete', sources_checked=0)


async def verify_citation(draft, sources, provider, claim_text="") -> Citation:
    source = sources.get(draft.source_id)
    if source is None:
        return Citation(**draft.model_dump(), title='Unknown source', url=None, verified=False,
                        verification='Rejected: source was not retrieved in this run.')
    verified = False
    reason = 'Rejected: quote was not found in the retrieved page.'
    if normalized(draft.quote) in normalized(source.text):
        judgment = await provider.structured(CitationJudgment,
            'Determine whether the supplied page actually supports the attributed statement and whether the quote is used in context. '
            'Also check that the assigned FOR/AGAINST/CONTEXT stance accurately describes its relationship to the original claim. Reject cherry-picked, contradictory, or ambiguous attributions. This is a separate citation check, not a truth guarantee.',
            json.dumps({'claim': claim_text, 'stance': draft.stance, 'statement': draft.statement, 'quote': draft.quote, 'page': source.text}))
        verified = judgment.supports_attribution
        reason = judgment.reason
    return Citation(**draft.model_dump(), title=source.title, url=source.url, verified=verified, verification=reason)


async def research_claim(claim, provider, fetch=fetch_text) -> ClaimResult:
    warnings = []
    sources = {}
    urls = set()
    search_status = {}
    for direction, suffix in [('FOR', 'primary sources evidence statistics'), ('AGAINST', 'contradicting evidence limitations fact check')]:
        try:
            hits = await provider.search(f'{claim.text} {claim.context} {suffix}')
            search_status[direction] = 'Search completed; no usable pages retrieved in this direction.'
            for hit in hits:
                url = hit.get('url', '')
                if url in urls:
                    continue
                urls.add(url)
                try:
                    final_url, text = await fetch(url)
                    if any(s.url == final_url for s in sources.values()):
                        continue
                    source_id = f'S{len(sources) + 1}'
                    sources[source_id] = Source(id=source_id, title=str(hit.get('title', 'Source'))[:250],
                                                url=final_url, text=text, retrieved_at=now())
                    search_status[direction] = 'Search completed and pages retrieved; see evidence below.'
                except Exception:
                    warnings.append('A search result could not be retrieved safely as readable text; it was excluded.')
        except ProviderFailure as exc:
            search_status[direction] = 'Failed'
            warnings.append(str(exc))
    if not sources:
        result = unresolved(claim.text, 'No readable sources were retrieved. Search snippets are not accepted as verified evidence.')
        return result.model_copy(update={'supporting_search': search_status['FOR'], 'contradicting_search': search_status['AGAINST'], 'limitations': result.limitations + warnings})
    analysis = await provider.structured(Analysis,
        'Analyze only the supplied pages for the claim. Compare FOR and AGAINST evidence. Use UNVERIFIABLE when evidence is '
        'insufficient, unclear, stale, or not directly relevant. Prefer primary evidence; evaluate author authority, methodology, publication date, and editorial standards. Do not treat multiple copied articles as independent sources. Distinguish correlation from causation, dates and scope. '
        'For every explanatory factual statement return an evidence entry with its exact short quote and supplied source_id. '
        'Never invent IDs or URLs. Limitations must describe research limitations only, not uncited factual assertions. '
        'Do not assign confidence percentages. Do not mistake the absence of contradictory evidence for proof.',
        json.dumps({'claim': claim.model_dump(), 'sources': [s.model_dump() for s in sources.values()]}))
    citations = []
    for draft in analysis.evidence:
        citations.append(await verify_citation(draft, sources, provider, claim.text))
    failed = any(not citation.verified for citation in citations)
    if failed:
        warnings.append(f'{sum(not c.verified for c in citations)} citation(s) failed validation and were excluded.')
    usable = [citation for citation in citations if citation.verified]
    verdict = analysis.verdict
    relevant = any(c.stance in ('FOR', 'AGAINST') for c in usable)
    if failed or not relevant or 'Failed' in search_status.values():
        verdict = 'UNVERIFIABLE'
        warnings.append('A verdict was withheld because evidence or citation checks were incomplete.')
    if verdict == 'TRUE' and not any(c.stance == 'FOR' for c in usable):
        verdict = 'UNVERIFIABLE'
    if verdict == 'FALSE' and not any(c.stance == 'AGAINST' for c in usable):
        verdict = 'UNVERIFIABLE'
    if not any(c.stance == 'AGAINST' for c in usable):
        warnings.append('No verified contradicting evidence was identified in the retrieved pages. This does not prove the claim.')
    warnings.append('Citation checks use quote matching and a separate model judgment; human review may still find errors.')
    warnings.append('Source independence, publication dates, and methodology require review; no calibrated confidence score is available.')
    return ClaimResult(claim=claim.text, verdict=verdict, status='incomplete' if failed or 'Failed' in search_status.values() else 'complete',
                       evidence=usable, limitations=list(dict.fromkeys(warnings + analysis.limitations)),
                       supporting_search=search_status['FOR'], contradicting_search=search_status['AGAINST'], sources_checked=len(sources))


async def run_pipeline(text, provider, fetch=fetch_text) -> Report:
    extraction = await provider.structured(Extraction,
        'Classify intent and extract at most three atomic factual claims without adding facts. Preserve dates, quantities, '
        'attribution, and qualifiers. Do not treat opinions or fictional content as factual claims. If mixed, extract only '
        'factual claims and explain exclusions in note. Set omitted_claims if more than three claims exist. '
        'For nonfactual intent return no claims. Do not determine truth during extraction.', text)
    limiter = asyncio.Semaphore(2)
    async def branch(claim):
        async with limiter:
            try:
                return await asyncio.wait_for(research_claim(claim, provider, fetch), timeout=150)
            except asyncio.TimeoutError:
                return unresolved(claim.text, 'This claim exceeded its research time limit.')
            except ProviderFailure as exc:
                return unresolved(claim.text, str(exc))
    results = await asyncio.gather(*(branch(c) for c in extraction.claims)) if extraction.intent == 'FACTUAL' else []
    limitations = ['At most three claims and six search results per claim are processed in this local MVP.']
    if extraction.omitted_claims:
        limitations.append('Additional claims were omitted. Submit those separately.')
    return Report(id=str(uuid4()), mode='live', submitted_text=text, created_at=now(), intent=extraction.intent,
                  note=extraction.note, claims=results, limitations=limitations, usage=provider.usage)
