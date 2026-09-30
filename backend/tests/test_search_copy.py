"""Fallback to the search provider's extracted page text when a page refuses the app's fetcher. Offline."""
import asyncio
from services.pipeline import SEARCH_COPY_MIN_CHARS, research_claim
from tests.test_pipeline import CLAIM, PAGE, FakeProvider

FULL_PAGE = PAGE + ' ' + 'The report describes the room, the equipment and the method used for each reading. ' * 3


class CopyProvider(FakeProvider):
    def __init__(self, raw_content, **kwargs):
        super().__init__(**kwargs)
        self.raw_content = raw_content
    async def search(self, query):
        self.queries.append(query)
        return [{'url': 'https://example.org/report', 'title': 'Test report', 'content': 'snippet only',
                 'raw_content': self.raw_content}]


async def refused(url):
    raise ValueError('403 Forbidden')


async def readable(url):
    return url, PAGE


def research(raw_content, fetch=refused):
    return asyncio.run(research_claim(CLAIM, CopyProvider(raw_content), fetch))


def test_refused_page_uses_the_search_providers_page_text_and_says_so():
    result = research(FULL_PAGE)
    assert result.verdict == 'TRUE' and result.sources_checked == 1
    assert result.evidence[0].verified and result.evidence[0].retrieval == 'search_copy'
    assert result.evidence[0].quote in FULL_PAGE
    assert any("search provider's copy" in text for text in result.limitations)


def test_a_fetched_page_is_preferred_and_labelled_fetched():
    result = research(FULL_PAGE.replace('12 units', '99 units'), fetch=readable)
    assert result.evidence[0].retrieval == 'fetched' and '12 units' in result.evidence[0].quote
    assert not any("search provider's copy" in text for text in result.limitations)


def test_snippets_and_missing_text_are_still_not_evidence():
    for raw in (None, '', 'x' * (SEARCH_COPY_MIN_CHARS - 1), 42):
        result = research(raw)
        assert result.verdict == 'UNVERIFIABLE' and result.sources_checked == 0
        assert result.withheld_reason == 'no_sources'


def test_search_copy_text_is_normalized_like_fetched_text():
    result = research('  ' + FULL_PAGE.replace(' ', '\n\n ') + ' filler' * 5000)
    quote = result.evidence[0].quote
    assert '\n' not in quote and '  ' not in quote
