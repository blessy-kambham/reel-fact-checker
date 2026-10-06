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
    assert rate(url).tier == tier and rate(url).label == credibility.TIERS[tier].label


@pytest.mark.parametrize('url', ['https://nasa.gov.example.com/moon', 'https://notgov.com', 'https://wikipedia.org.example.net',
                                 'https://mygov.co', 'https://edu.example.com', 'https://go.example.jp', 'https://gov', '', None, 'not a url'])
def test_lookalike_and_missing_addresses_are_unrated(url):
    assert rate(url).tier == 'unrated'


def test_a_lookalike_cannot_escape_the_social_media_rule_by_adding_a_suffix():
    # The real platform is rated by its own host; a different host that merely contains the name is just unrated.
    assert rate('https://instagram.com.example.net/reel').tier == 'unrated'
    assert rate('https://blog.gov.instagram.com/x').tier == 'user_generated'


def test_strength_counts_each_site_once():
    assert assess(['https://www.nasa.gov/a', 'https://www.britannica.com/b']) == ('strong', 90)
    assert assess(['https://www.nasa.gov/a', 'https://www.nasa.gov/b', 'https://example.org/c']) == ('moderate', 75)
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
    assert provider.analysed[0]['source_type'] == 'Official, academic or peer-reviewed source'
    [evidence] = result.evidence
    assert evidence.source_tier == 'official' and evidence.source_label == provider.analysed[0]['source_type']


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
        'Social media or user-generated platform', 'Reference work, fact-checker or established publisher']


@pytest.mark.parametrize('urls,strength,score,weak', [
    (['https://www.nasa.gov/report', 'https://www.britannica.com/report'], 'strong', 90, False),
    (['https://www.nasa.gov/report'], 'moderate', 100, False),
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
