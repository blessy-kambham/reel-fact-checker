"""Source credibility: how pages are rated, and what the rating changes. Offline."""
import asyncio
import json
import pytest
from agents.orchestrator import WEAK_SOURCES_NOTE, research_claim
from schemas import Analysis, ClaimResult, ResearchAction, VerdictDecision
from tools import credibility
from tools.credibility import assess, rate
from tests.test_pipeline import CLAIM, DRAFT, PAGE, FakeProvider


@pytest.mark.parametrize('url,tier', [
    ('https://www.nasa.gov/moon', 'official'), ('https://oceanservice.noaa.gov/facts', 'official'),
    ('https://www.gov.uk/guidance', 'official'), ('https://www.ox.ac.uk/news', 'official'),
    ('https://www.who.int/news', 'official'), ('https://www.nature.com/articles/x', 'official'),
    ('https://si.edu/object', 'official'), ('https://www.gob.mx/salud', 'official'),
    ('https://www.u-tokyo.ac.jp/en', 'official'), ('https://www.canada.ca/en/health', 'official'),
    ('https://en.wikipedia.org/wiki/Moon', 'established'), ('https://www.britannica.com/topic', 'established'),
    ('https://www.reuters.com/world', 'established'), ('https://www.bbc.co.uk/news', 'established'),
    ('https://www.snopes.com/fact-check/x', 'established'),
    ('https://www.instagram.com/reel/abc', 'user_generated'), ('https://m.youtube.com/watch?v=1', 'user_generated'),
    ('https://old.reddit.com/r/space', 'user_generated'), ('https://someone.substack.com/p/post', 'user_generated'),
    ('https://example.org/report', 'unrated'), ('https://beijing-travels.com/wall', 'unrated'),
])
def test_pages_are_rated_by_where_they_come_from(url, tier):
    assert rate(url).tier == tier and rate(url).tier == credibility.CATEGORIES[rate(url).category].tier


@pytest.mark.parametrize('url,category,weight', [
    # The eight categories and weights of the design brief, with one of its own examples for each.
    ('https://www.imf.org/en/Data', 'government', 0.95), ('https://www.census.gov/data', 'government', 0.95),
    ('https://www.who.int/data', 'government', 0.95), ('https://www.health.govt.nz/a', 'government', 0.95),
    ('https://pubmed.ncbi.nlm.nih.gov/123/', 'academic', 0.90), ('https://www.thelancet.com/article', 'academic', 0.90),
    ('https://www.mit.edu/research', 'academic', 0.90), ('https://www.ox.ac.uk/news', 'academic', 0.90),
    ('https://fullfact.org/health/x', 'fact_checker', 0.88), ('https://www.politifact.com/x', 'fact_checker', 0.88),
    ('https://www.reuters.com/world', 'wire_service', 0.82), ('https://apnews.com/article/x', 'wire_service', 0.82),
    ('https://www.nytimes.com/2026/x', 'news', 0.75), ('https://www.theguardian.com/world', 'news', 0.75),
    ('https://www.britannica.com/topic', 'news', 0.75),
    ('https://en.wikipedia.org/wiki/Moon', 'wikipedia', 0.55),
    ('https://someone.substack.com/p/post', 'blog', 0.30), ('https://medium.com/@a/post', 'blog', 0.30),
    ('https://x.com/someone/status/1', 'social_media', 0.10), ('https://www.facebook.com/page', 'social_media', 0.10),
    # Not in the brief's table: the rest of the web.
    ('https://example.org/report', 'unrated', 0.50),
])
def test_categories_and_weights_follow_the_design_brief(url, category, weight):
    rating = rate(url)
    assert (rating.category, rating.weight) == (category, weight) and rating is credibility.CATEGORIES[category]


def test_weights_fall_from_primary_sources_to_social_media():
    weights = [rating.weight for rating in credibility.CATEGORIES.values()]
    assert weights == sorted(weights, reverse=True) and len(credibility.CATEGORIES) == 9


def test_blogs_and_social_media_are_weighted_but_still_not_accepted_as_evidence():
    assert not credibility.accepted_as_evidence('https://medium.com/@a/post')
    assert not credibility.accepted_as_evidence('https://x.com/someone/status/1')
    assert credibility.accepted_as_evidence('https://en.wikipedia.org/wiki/Moon')


@pytest.mark.parametrize('url', ['https://nasa.gov.example.com/moon', 'https://notgov.com', 'https://wikipedia.org.example.net',
                                 'https://mygov.co', 'https://edu.example.com', 'https://go.example.jp', 'https://gov', '', None, 'not a url'])
def test_lookalike_and_missing_addresses_are_unrated(url):
    assert rate(url).tier == 'unrated'


def test_a_lookalike_cannot_escape_the_social_media_rule_by_adding_a_suffix():
    # The real platform is rated by its own host; a different host that merely contains the name is just unrated.
    assert rate('https://instagram.com.example.net/reel').tier == 'unrated'
    assert rate('https://blog.gov.instagram.com/x').tier == 'user_generated'


def test_strength_counts_each_site_once():
    assert assess(['https://www.nasa.gov/a', 'https://www.britannica.com/b']) == ('strong', 85)
    assert assess(['https://www.nasa.gov/a', 'https://www.nasa.gov/b', 'https://example.org/c']) == ('moderate', 72)
    assert assess(['https://example.org/a', 'https://example.net/b']) == ('weak', 50)
    assert assess([]) == (None, None) and assess([None, '']) == (None, None)


# ---- what the rating changes -------------------------------------------------------------------

class Web(FakeProvider):
    """Search results at chosen addresses; records the pages the analyst was shown."""
    def __init__(self, urls, **kwargs):
        super().__init__(**kwargs)
        self.urls, self.analysed = urls, None

    async def search(self, query):
        self.queries.append(query)
        return [{'url': url, 'title': 'Result'} for url in self.urls]

    async def structured(self, schema, instructions, data):
        if schema is Analysis:
            self.analysed = json.loads(data)['sources']
        return await super().structured(schema, instructions, data)


def check(provider, redirect=None):
    fetched = []
    async def fetch(url):
        fetched.append(url)
        return (redirect or {}).get(url, url), PAGE
    result = asyncio.run(research_claim(CLAIM, provider, fetch))
    return result, fetched


def test_social_media_pages_are_not_read_or_used_as_evidence():
    result, fetched = check(Web(['https://www.instagram.com/reel/abc', 'https://www.nasa.gov/report']))
    assert fetched == ['https://www.nasa.gov/report'] and result.sources_checked == 1
    assert [e.url for e in result.evidence] == ['https://www.nasa.gov/report']
    assert 'A social media or user-generated page was not accepted as evidence.' in result.limitations


def test_a_page_that_redirects_to_social_media_is_not_used_either():
    result, _ = check(Web(['https://short.example/abc']), redirect={'https://short.example/abc': 'https://www.tiktok.com/@a/video/1'})
    assert result.sources_checked == 0 and result.withheld_reason == 'no_sources'


def test_only_social_media_results_leave_the_claim_without_sources():
    result, fetched = check(Web(['https://www.reddit.com/r/space/1', 'https://x.com/someone/status/1']))
    assert not fetched and result.verdict == 'UNVERIFIABLE' and result.withheld_reason == 'no_sources'


def test_the_analyst_and_the_report_show_each_source_type():
    provider = Web(['https://www.nasa.gov/report'])
    result, _ = check(provider)
    assert provider.analysed[0]['source_tier'] == 'official'
    assert provider.analysed[0]['source_type'] == 'Government or intergovernmental body'
    [evidence] = result.evidence
    assert evidence.source_tier == 'official' and evidence.source_label == provider.analysed[0]['source_type']
    assert evidence.source_weight == 0.95


def test_the_research_agent_sees_source_types_when_choosing_what_to_read():
    class Planner(Web):
        agent_mode = 'research'
        states = []
        async def structured(self, schema, instructions, data):
            if schema is ResearchAction:
                self.states.append(json.loads(data))
                if len(self.states) == 1:
                    return ResearchAction(tool='search_web', query='q', looking_for='supporting', result_ids=[], reason='')
                return ResearchAction(tool='finish', query='', looking_for='supporting', result_ids=[], reason='Done.')
            return await super().structured(schema, instructions, data)
    provider = Planner(['https://www.instagram.com/reel/abc', 'https://en.wikipedia.org/wiki/Sensor'])
    check(provider)
    assert [r['source_type'] for r in provider.states[1]['searches'][0]['results']] == [
        'Social media or user-generated platform', 'Wikipedia']


@pytest.mark.parametrize('urls,strength,score,weak', [
    (['https://www.nasa.gov/report', 'https://www.britannica.com/report'], 'strong', 85, False),
    (['https://www.nasa.gov/report'], 'moderate', 95, False),
    (['https://example.org/report', 'https://example.net/report'], 'weak', 50, True),
])
def test_an_issued_verdict_reports_how_strong_its_sources_are(urls, strength, score, weak):
    second = DRAFT.model_copy(update={'source_id': 'S2', 'excerpt_id': 'S2:E1'})
    class Both(Web):
        async def structured(self, schema, instructions, data):
            if schema is Analysis:
                return Analysis(verdict='TRUE', evidence=[DRAFT, second][:len(self.urls)], limitations=[])
            return await super().structured(schema, instructions, data)
    result, _ = check(Both(urls))
    assert result.verdict == 'TRUE' and (result.evidence_strength, result.source_score) == (strength, score)
    assert (WEAK_SOURCES_NOTE in result.limitations) is weak


def test_strength_covers_only_the_pages_the_verdict_cites():
    second = DRAFT.model_copy(update={'source_id': 'S2', 'excerpt_id': 'S2:E1'})
    class CitesOne(Web):
        async def structured(self, schema, instructions, data):
            if schema is Analysis:
                return Analysis(verdict='TRUE', evidence=[DRAFT, second], limitations=[])
            if schema is VerdictDecision:
                return VerdictDecision(verdict='TRUE', evidence_ids=['E2'])
            return await super().structured(schema, instructions, data)
    result, _ = check(CitesOne(['https://www.nasa.gov/report', 'https://example.org/report']))
    assert result.verdict_evidence_ids == ['E2'] and (result.evidence_strength, result.source_score) == ('weak', 50)


def test_a_withheld_or_unverifiable_verdict_has_no_strength():
    withheld, _ = check(Web(['https://www.nasa.gov/report'], approved=False))
    assert withheld.verdict_state == 'withheld' and withheld.evidence_strength is None and withheld.source_score is None
    unverifiable, _ = check(Web(['https://www.nasa.gov/report'], verdict='UNVERIFIABLE'))
    assert unverifiable.verdict_state == 'issued' and unverifiable.evidence_strength is None


def test_the_rating_never_changes_a_verdict():
    strong, _ = check(Web(['https://www.nasa.gov/report']))
    unrated, _ = check(Web(['https://example.org/report']))
    assert strong.verdict == unrated.verdict == 'TRUE' and strong.verdict_state == unrated.verdict_state == 'issued'


def test_reports_saved_before_sources_were_rated_still_load():
    saved, _ = check(Web(['https://www.nasa.gov/report']))
    data = saved.model_dump()
    for key in ('evidence_strength', 'source_score'):
        del data[key]
    for evidence in data['evidence']:
        del evidence['source_tier'], evidence['source_label']
    loaded = ClaimResult.model_validate(data)
    assert loaded.evidence_strength is None and loaded.evidence[0].source_tier is None


@pytest.mark.parametrize('url, expected', [
    ('https://en.wikipedia.org/wiki/Moon', 'wikipedia.org'),
    ('https://simple.wikipedia.org/wiki/Moon', 'wikipedia.org'),
    ('https://science.nasa.gov/moon/', 'nasa.gov'),
    ('https://www.bbc.co.uk/news/1', 'bbc.co.uk'),
    ('https://www.gov.uk/guidance', 'gov.uk'),
    ('https://www.u-tokyo.ac.jp/en/', 'u-tokyo.ac.jp'),
    ('https://example.org/page', 'example.org'),
    ('https://www.health.govt.nz/a', 'health.govt.nz'),
    ('http://192.168.1.10:8000/x', '192.168.1.10'),
    ('https://EN.Wikipedia.org./wiki/Moon', 'wikipedia.org'),
    ('not a url', ''),
    (None, ''),
])
def test_sections_of_one_site_count_as_one_site(url, expected):
    assert credibility.domain(url) == expected


def test_two_sections_of_one_site_are_one_source_for_strength():
    assert credibility.assess(['https://en.wikipedia.org/wiki/A', 'https://simple.wikipedia.org/wiki/A'])[0] == 'moderate'
    assert credibility.assess(['https://en.wikipedia.org/wiki/A', 'https://www.britannica.com/a'])[0] == 'strong'


def test_one_domain_in_two_categories_counts_once_at_the_higher_weight_whatever_the_order():
    urls = ['https://www.nih.gov/a', 'https://pubmed.ncbi.nlm.nih.gov/1/']
    assert assess(urls) == assess(urls[::-1]) == ('moderate', 95)


@pytest.mark.parametrize('url,tier', [
    ('https://www.mit.edu/x', 'official'), ('https://www.ox.ac.uk/x', 'official'), ('https://www.unimelb.edu.au/x', 'official'),
    ('https://si.edu/x', 'official'), ('https://pmc.ncbi.nlm.nih.gov/x', 'official'), ('https://ec.europa.eu/x', 'official'),
    ('https://www.canada.ca/x', 'official'), ('https://www.cern.ch/x', 'official'), ('https://simple.wikipedia.org/x', 'established'),
    ('https://www.bbc.co.uk/x', 'established'), ('https://someone.medium.com/x', 'user_generated'),
    ('https://physics.stackexchange.com/x', 'user_generated'), ('https://selfhosted-blog.example/x', 'unrated'),
    ('https://nasa.gov.example.com/x', 'unrated'), ('http://10.0.0.1/x', 'unrated'),
])
def test_the_categories_did_not_move_sites_between_tiers(url, tier):
    """The rules act on tiers. Adding the brief's categories changed labels and weights, not what is accepted."""
    assert rate(url).tier == tier
