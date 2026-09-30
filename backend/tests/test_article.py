"""Article URL ingestion, offline: fixture pages instead of the web."""
import asyncio
import json
import pytest
from fastapi.testclient import TestClient
import main
from schemas import AtomicClaim, Extraction
from services.article import ArticleUnavailable, run_article_pipeline
from services.fetcher import validate_url
from tests.test_pipeline import FakeProvider, PAGE

ARTICLE_URL = 'https://news.example.org/story'
ARTICLE = ('City officials said the fictional Harbor Bridge closed on March 3 after inspectors found cracked welds. '
           'Repairs are expected to take eight months. Local shops report fewer visitors since the closure.')
CLAIM_1 = 'the fictional Harbor Bridge closed on March 3 after inspectors found cracked welds'
CLAIM_2 = 'Repairs are expected to take eight months'


class ArticleProvider(FakeProvider):
    def __init__(self, claims, intent='FACTUAL', search_urls=None, **kwargs):
        super().__init__(**kwargs)
        self.article_claims, self.intent = claims, intent
        self.search_urls = search_urls or ['https://evidence.example.org/report']
        self.extraction_input = None
    async def search(self, query):
        self.queries.append(query)
        return [{'url': url, 'title': 'Result'} for url in self.search_urls]
    async def structured(self, schema, instructions, data):
        if schema is Extraction:
            self.extraction_input = json.loads(data)
            return Extraction(intent=self.intent, claims=self.article_claims, omitted_claims=True, note='')
        return await super().structured(schema, instructions, data)


def fetcher(pages):
    fetched = []
    async def fetch(url):
        fetched.append(url)
        if url not in pages:
            raise ValueError('unreadable')
        return pages[url]
    fetch.fetched = fetched
    return fetch


def run(provider, fetch):
    return asyncio.run(run_article_pipeline(ARTICLE_URL, provider, fetch))


def test_article_claims_are_researched_even_when_others_are_omitted():
    provider = ArticleProvider([AtomicClaim(text=CLAIM_1, context=''), AtomicClaim(text=CLAIM_2, context='')])
    report = run(provider, fetcher({ARTICLE_URL: (ARTICLE_URL, ARTICLE),
                                    'https://evidence.example.org/report': ('https://evidence.example.org/report', PAGE)}))
    assert report.input_type == 'article' and report.source_url == ARTICLE_URL and report.source_sha256
    assert report.coverage_status == 'passed' and report.omitted_claims
    assert [c.claim for c in report.claims] == [CLAIM_1, CLAIM_2]
    assert all(c.sources_checked == 1 for c in report.claims)
    assert provider.extraction_input == {'text': ARTICLE}
    assert any('Other claims in the article were not checked' in text for text in report.limitations)


def test_the_article_never_counts_as_evidence_for_itself():
    provider = ArticleProvider([AtomicClaim(text=CLAIM_1, context='')],
                               search_urls=[ARTICLE_URL, ARTICLE_URL + '/#comments', 'https://evidence.example.org/report'])
    fetch = fetcher({ARTICLE_URL: (ARTICLE_URL, ARTICLE),
                     'https://evidence.example.org/report': ('https://evidence.example.org/report', PAGE)})
    report = run(provider, fetch)
    assert fetch.fetched.count(ARTICLE_URL) == 1  # Only the initial article fetch.
    assert report.claims[0].sources_checked == 1
    assert all(c.url != ARTICLE_URL for c in report.claims[0].evidence)


def test_a_redirect_back_to_the_article_is_also_excluded():
    provider = ArticleProvider([AtomicClaim(text=CLAIM_1, context='')], search_urls=['https://short.example/a'])
    report = run(provider, fetcher({ARTICLE_URL: (ARTICLE_URL, ARTICLE), 'https://short.example/a': (ARTICLE_URL, ARTICLE)}))
    assert report.claims[0].sources_checked == 0 and report.claims[0].withheld_reason == 'no_sources'


def test_paraphrased_claims_are_refused_but_verbatim_ones_proceed():
    provider = ArticleProvider([AtomicClaim(text=CLAIM_1, context=''),
                                AtomicClaim(text='The bridge will reopen next year', context='')])
    report = run(provider, fetcher({ARTICLE_URL: (ARTICLE_URL, ARTICLE),
                                    'https://evidence.example.org/report': ('https://evidence.example.org/report', PAGE)}))
    assert report.coverage_status == 'incomplete'
    refused = report.claims[-1]
    assert refused.claim == 'The bridge will reopen next year' and refused.withheld_reason == 'coverage_failed'
    assert report.claims[0].sources_checked == 1


def test_invented_context_is_dropped_and_the_verbatim_claim_is_still_checked():
    provider = ArticleProvider([AtomicClaim(text=CLAIM_2, context='Officials promised a free ferry.')])
    report = run(provider, fetcher({ARTICLE_URL: (ARTICLE_URL, ARTICLE),
                                    'https://evidence.example.org/report': ('https://evidence.example.org/report', PAGE)}))
    assert report.coverage_status == 'passed' and report.claims[0].sources_checked == 1
    assert all('free ferry' not in query for query in provider.queries)  # The invented context never reaches research.
    assert any('without surrounding context' in text for text in report.limitations)


def test_reposted_copies_of_the_article_are_not_evidence():
    long_claim = ('City officials said the fictional Harbor Bridge closed on March 3 after inspectors found cracked welds')
    repost = 'https://aggregator.example.net/harbor-bridge'
    provider = ArticleProvider([AtomicClaim(text=long_claim, context='')], search_urls=[repost, 'https://evidence.example.org/report'])
    report = run(provider, fetcher({ARTICLE_URL: (ARTICLE_URL, ARTICLE), repost: (repost, 'Reposted: ' + ARTICLE),
                                    'https://evidence.example.org/report': ('https://evidence.example.org/report', PAGE)}))
    claim = report.claims[0]
    assert claim.sources_checked == 1 and all(c.url != repost for c in claim.evidence)
    assert any('treated as a copy' in text for text in claim.limitations)


def test_short_claims_are_not_treated_as_copies():
    # A short, common sentence appearing elsewhere is normal corroboration, not a repost.
    provider = ArticleProvider([AtomicClaim(text=CLAIM_2, context='')])
    report = run(provider, fetcher({ARTICLE_URL: (ARTICLE_URL, ARTICLE),
                                    'https://evidence.example.org/report': ('https://evidence.example.org/report', CLAIM_2 + '. ' + PAGE)}))
    assert report.claims[0].sources_checked == 1


def test_whitespace_differences_still_count_as_verbatim():
    provider = ArticleProvider([AtomicClaim(text='Repairs are  expected\nto take eight months', context='')])
    report = run(provider, fetcher({ARTICLE_URL: (ARTICLE_URL, ARTICLE),
                                    'https://evidence.example.org/report': ('https://evidence.example.org/report', PAGE)}))
    assert report.coverage_status == 'passed'


def test_opinion_articles_are_not_researched():
    provider = ArticleProvider([AtomicClaim(text=CLAIM_1, context='')], intent='OPINION')
    report = run(provider, fetcher({ARTICLE_URL: (ARTICLE_URL, ARTICLE)}))
    assert report.claims == [] and provider.queries == []


def test_unreadable_article_is_a_clear_error_without_model_calls():
    provider = ArticleProvider([])
    with pytest.raises(ArticleUnavailable):
        run(provider, fetcher({}))
    assert provider.extraction_input is None


@pytest.mark.parametrize('url', ['http://news.example.org/story', 'https://127.0.0.1/admin', 'file:///etc/passwd',
                                 'https://user:pass@news.example.org/'])
def test_unsafe_article_urls_are_rejected_by_the_real_fetcher(url):
    with pytest.raises(ValueError):
        validate_url(url)


@pytest.fixture
def client(monkeypatch, tmp_path):
    for key in ('OPENAI_API_KEY', 'TAVILY_API_KEY'):
        monkeypatch.setenv(key, 'offline-test')
    monkeypatch.setenv('OPENAI_MODEL', 'gpt-4.1-mini')
    monkeypatch.setenv('ENABLE_LIVE_RESEARCH', 'true')
    monkeypatch.setattr(main, 'DATA_DIR', tmp_path)
    class Stub:
        async def close(self):
            pass
    monkeypatch.setattr(main, 'Providers', Stub)
    with TestClient(main.app) as client:
        yield client


def test_article_route_saves_to_history(client, monkeypatch):
    from tests.test_history import live_report
    async def pipeline(url, provider):
        return live_report(url).model_copy(update={'input_type': 'article', 'source_url': url})
    monkeypatch.setattr(main, 'run_article_pipeline', pipeline)
    report = client.post('/fact-check-article', json={'url': ARTICLE_URL}).json()
    assert report['input_type'] == 'article'
    assert client.get(f"/history/{report['id']}").json()['source_url'] == ARTICLE_URL


def test_article_route_turns_unreadable_links_into_422(client, monkeypatch):
    async def pipeline(url, provider):
        raise ArticleUnavailable('That link could not be read as a public article.')
    monkeypatch.setattr(main, 'run_article_pipeline', pipeline)
    response = client.post('/fact-check-article', json={'url': ARTICLE_URL})
    assert response.status_code == 422 and 'could not be read' in response.json()['detail']


@pytest.mark.parametrize('payload', [{}, {'url': ''}, {'url': 'x' * 2001}, {'url': ARTICLE_URL, 'claim': 'extra'}])
def test_article_route_validates_input(client, payload):
    assert client.post('/fact-check-article', json=payload).status_code == 422


def test_dropped_apostrophes_and_straight_quotes_still_count_as_verbatim():
    from services.article import verbatim
    page = 'NASA’s SLS rocket lifted off from the agency’s Kennedy Space Center — on schedule, “flawlessly”.'
    assert verbatim('NASAs SLS rocket lifted off from the agencys Kennedy Space Center - on schedule, "flawlessly"', page)
    assert verbatim("NASA's SLS rocket lifted off", page)
    assert not verbatim('NASAs SLS rocket lifted off from Cape Canaveral', page)
    assert not verbatim('NASA SLS rocket lifted', page.replace('’s', ' s'))  # A changed word is still refused.


def test_corrupted_model_punctuation_is_repaired_before_the_verbatim_check(monkeypatch):
    # Exact failure from live checkpoint 2: the model returned U+0019 for U+2019.
    import asyncio
    from types import SimpleNamespace
    from services.article import verbatim
    from services.providers import Providers, repair_text
    page = 'NASA’s SLS rocket lifted off from Launch Pad 39B at the agency’s Kennedy Space Center — on “schedule”.'
    corrupted = 'NASA\x19s SLS rocket lifted off from Launch Pad 39B at the agency\x19s Kennedy Space Center \x14 on \x1cschedule\x1d.'
    assert not verbatim(corrupted, page)
    assert repair_text(corrupted) == page and verbatim(repair_text(corrupted), page)

    extraction = Extraction(intent='FACTUAL', claims=[AtomicClaim(text=corrupted, context='Orion\x19s module')],
                            omitted_claims=False, note='It\x19s fine')
    repaired = repair_text(extraction)
    assert repaired.claims[0].text == page and repaired.claims[0].context == 'Orion’s module' and repaired.note == 'It’s fine'

    class Responses:
        async def parse(self, **kwargs):
            return SimpleNamespace(output_parsed=extraction, usage=None)
    # CI sets OPENAI_API_KEY to an empty string, so set it explicitly rather than only when absent.
    monkeypatch.setenv('OPENAI_API_KEY', 'offline-test')
    monkeypatch.setenv('OPENAI_MODEL', 'gpt-4.1-mini')
    provider = Providers()
    provider.client = SimpleNamespace(responses=Responses())
    result = asyncio.run(provider.structured(Extraction, 'extract', 'text'))
    assert '\x19' not in result.model_dump_json() and result.claims[0].text == page


def test_split_escape_punctuation_variant_is_repaired():
    # Second corruption form seen in live checkpoint 2: U+0002 followed by the last three hex digits.
    from services.providers import repair_text
    assert repair_text('Four astronauts \x02013 three from NASA') == 'Four astronauts – three from NASA'
    assert repair_text('NASA\x02019s rocket; we\x02019ll see') == 'NASA’s rocket; we’ll see'
    assert repair_text('Built in 2019, flight 013') == 'Built in 2019, flight 013'  # Real numbers are untouched.
    assert repair_text('line\nbreak\ttab') == 'line\nbreak\ttab'


def test_relation_prompt_treats_differently_written_dates_as_compatible():
    from services.pipeline import verify_selection
    import inspect
    assert 'weekday versus a calendar date' in inspect.getsource(verify_selection)
