"""Research agent.

One research agent runs per claim. It searches the web for supporting and for contradicting
evidence, reads the pages it finds and returns them as numbered sources. It does not judge the claim.

Used by: `agents/orchestrator.py`, which runs up to two research agents in parallel.
"""
from dataclasses import dataclass, field

from schemas import Source
from services.budget import BudgetExceeded
from services.fetcher import fetch_text
from services.providers import ProviderFailure

from agents.shared import COPY_MARKER_MIN_WORDS, loose, now, page_key

SEARCH_COPY_MIN_CHARS = 200
# One search per direction, so evidence against the claim is always looked for.
SEARCH_DIRECTIONS = [('FOR', 'primary sources evidence statistics'),
                     ('AGAINST', 'contradicting evidence limitations fact check')]


def search_copy(hit) -> str:
    """The search provider's extracted text for a page the app could not fetch itself, normalized and
    capped like fetched text. Raises when it is missing or too short to be a page (not just a snippet)."""
    raw = hit.get('raw_content')
    text = ' '.join(raw.split())[:18000] if isinstance(raw, str) else ''
    if len(text) < SEARCH_COPY_MIN_CHARS:
        raise ValueError('No usable page text from the search provider.')
    return text


@dataclass
class EvidencePack:
    """What a research agent hands to the analyst: the pages it read and how the searches went."""
    sources: dict = field(default_factory=dict)          # 'S1' -> Source
    search_status: dict = field(default_factory=dict)    # 'FOR' / 'AGAINST' -> human-readable status
    warnings: list = field(default_factory=list)

    @property
    def search_failed(self) -> bool:
        return 'Failed' in self.search_status.values()


class ResearchAgent:
    name = 'Research Agent'

    def __init__(self, provider, fetch=fetch_text):
        self.provider, self.fetch = provider, fetch

    async def gather(self, claim, exclude=frozenset(), copy_markers=()) -> EvidencePack:
        """`exclude` holds page keys that may not serve as evidence (for example the article being checked).
        A page containing any of `copy_markers` (long passages of the checked material) word for word is a
        copy or repost of that material, not independent evidence, and is excluded."""
        markers = [loose(m) for m in copy_markers if len(m.split()) >= COPY_MARKER_MIN_WORDS]
        pack = EvidencePack()
        sources, warnings, search_status = pack.sources, pack.warnings, pack.search_status
        urls = set()
        for direction, suffix in SEARCH_DIRECTIONS:
            try:
                hits = await self.provider.search(f'{claim.text} {claim.context} {suffix}')
                search_status[direction] = 'Search completed; no usable pages retrieved in this direction.'
                for hit in hits:
                    url = hit.get('url', '')
                    if url in urls or page_key(url) in exclude:
                        continue
                    urls.add(url)
                    try:
                        retrieval = 'fetched'
                        try:
                            final_url, text = await self.fetch(url)
                        except Exception:
                            final_url, text = url, search_copy(hit)
                            retrieval = 'search_copy'
                        if any(s.url == final_url for s in sources.values()) or page_key(final_url) in exclude:
                            continue
                        if markers and any(marker in loose(text) for marker in markers):
                            warnings.append('A page repeating the checked material word for word was treated as a copy and excluded.')
                            continue
                        source_id = f'S{len(sources) + 1}'
                        sources[source_id] = Source(id=source_id, title=str(hit.get('title', 'Source'))[:250],
                                                    url=final_url, text=text, retrieved_at=now(), retrieval=retrieval)
                        if retrieval == 'search_copy':
                            warnings.append(f'{source_id} refused the app\'s page reader; its text is the search provider\'s '
                                            'copy of the page, not a copy fetched by this app.')
                        search_status[direction] = 'Search completed and pages retrieved; see evidence below.'
                    except Exception:
                        warnings.append('A search result could not be retrieved safely as readable text; it was excluded.')
            except BudgetExceeded:
                raise
            except ProviderFailure as exc:
                search_status[direction] = 'Failed'
                warnings.append(str(exc))
        return pack
