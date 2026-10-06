"""Source credibility: a rule-based rating of where a page comes from.

Every page the app reads is placed in a category by its web address alone. The eight categories and
their weights are the ones in the design brief this project was built from; "unrated" is added here
for the rest of the web, which the brief's table does not cover. The rating says what kind of
publisher a page belongs to. It does not say the page is right: a government site can be wrong and
an unrated site can be correct, which is why every passage still has to pass the citation checks
whatever its category.

Each category also belongs to one of four tiers, which is what the rules use:

- pages in the user-generated tier (social media and blogs) are not accepted as evidence. The brief
  gives them a low weight; this app goes further and leaves them out;
- the research agent and the analyst are shown each page's category, so they can prefer better sources;
- each issued verdict reports how strong its sources are (`evidence_strength`, from the tiers) and
  their average weight (`source_score`).

The lists below are short and deliberately conservative. A site that is not listed is "unrated",
not "bad". The weights are stated assumptions, not measurements, and never change a verdict.
"""
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Rating:
    category: str  # one of CATEGORIES
    tier: str      # 'official', 'established', 'unrated' or 'user_generated'
    label: str     # shown in reports
    weight: float  # 0 to 1, used only for the source score


GOVERNMENT = Rating('government', 'official', 'Government or intergovernmental body', 0.95)
ACADEMIC = Rating('academic', 'official', 'Academic or peer-reviewed source', 0.90)
FACT_CHECKER = Rating('fact_checker', 'established', 'Established fact-checker', 0.88)
WIRE_SERVICE = Rating('wire_service', 'established', 'News agency', 0.82)
NEWS = Rating('news', 'established', 'Major newspaper, broadcaster or reference publisher', 0.75)
WIKIPEDIA = Rating('wikipedia', 'established', 'Wikipedia', 0.55)
UNRATED = Rating('unrated', 'unrated', 'Unrated website', 0.50)
BLOG = Rating('blog', 'user_generated', 'Blog or self-published platform', 0.30)
SOCIAL_MEDIA = Rating('social_media', 'user_generated', 'Social media or user-generated platform', 0.10)
CATEGORIES = {rating.category: rating for rating in (GOVERNMENT, ACADEMIC, FACT_CHECKER, WIRE_SERVICE, NEWS, WIKIPEDIA,
                                                     UNRATED, BLOG, SOCIAL_MEDIA)}
RATED_TIERS = ('official', 'established')

# Government and military addresses, recognised by how they end; and the same under a country code
# (gov.uk, gouv.fr, gob.mx), or under the names some registries use instead.
GOVERNMENT_ENDINGS = ('.gov', '.mil', '.int')
GOVERNMENT_SECOND_LEVEL = {'gov', 'mil', 'gouv', 'gob', 'govt'}
GOVERNMENT_COUNTRY_ENDINGS = ('.go.jp', '.go.kr', '.go.id', '.gc.ca', '.canada.ca', '.admin.ch', '.bund.de')
# Universities and colleges, on the same pattern.
ACADEMIC_ENDINGS = ('.edu',)
ACADEMIC_SECOND_LEVEL = {'edu'}
ACADEMIC_COUNTRY_ENDINGS = ('.ac.uk', '.ac.jp', '.ac.in', '.ac.nz', '.ac.za', '.ac.kr')

# Listed sites are matched before the endings above, so a journal index on a government address is academic.
SITES = {
    GOVERNMENT: {'europa.eu', 'un.org', 'unesco.org', 'worldbank.org', 'imf.org', 'oecd.org', 'ipcc.ch', 'iaea.org'},
    ACADEMIC: {
        # Peer-reviewed journals and their indexes
        'nature.com', 'science.org', 'thelancet.com', 'nejm.org', 'bmj.com', 'pnas.org', 'cell.com', 'jamanetwork.com',
        'pubmed.ncbi.nlm.nih.gov', 'pmc.ncbi.nlm.nih.gov',
        # Scientific bodies
        'cern.ch', 'iau.org', 'nationalacademies.org', 'royalsociety.org', 'nobelprize.org',
    },
    FACT_CHECKER: {'snopes.com', 'politifact.com', 'factcheck.org', 'fullfact.org'},
    WIRE_SERVICE: {'reuters.com', 'apnews.com', 'afp.com'},
    NEWS: {
        # Newspapers of record and public broadcasters
        'bbc.com', 'bbc.co.uk', 'npr.org', 'pbs.org', 'nytimes.com', 'washingtonpost.com', 'wsj.com', 'theguardian.com',
        'economist.com', 'ft.com', 'bloomberg.com',
        # Edited reference works and science publishers, which the brief's table has no row for
        'britannica.com', 'merriam-webster.com', 'scientificamerican.com', 'nationalgeographic.com', 'smithsonianmag.com',
        'newscientist.com', 'sciencenews.org', 'sciencefocus.com',
    },
    WIKIPEDIA: {'wikipedia.org'},
}
USER_GENERATED = {
    BLOG: {'medium.com', 'substack.com', 'blogspot.com', 'wordpress.com'},
    SOCIAL_MEDIA: {
        'instagram.com', 'facebook.com', 'fb.com', 'tiktok.com', 'x.com', 'twitter.com', 'threads.net', 'threads.com',
        'youtube.com', 'youtu.be', 'reddit.com', 'quora.com', 'pinterest.com', 'tumblr.com', 'linkedin.com', 'snapchat.com',
        't.me', 'discord.com', 'vk.com', 'weibo.com', 'stackexchange.com', 'fandom.com',
    },
}


def site(url: str | None) -> str:
    """The host a page is on, without a leading `www.`; empty when there is none."""
    host = (urlsplit(url or '').hostname or '').lower().rstrip('.')
    return host[4:] if host.startswith('www.') else host


# Second-level names that registries put in front of a country code: bbc.co.uk, nature.com.au, u-tokyo.ac.jp.
COUNTRY_SECOND_LEVEL = {'co', 'com', 'org', 'net', 'ac', 'go', 'ne', 'or', 'nic', 'gc'} | GOVERNMENT_SECOND_LEVEL | ACADEMIC_SECOND_LEVEL


def domain(url: str | None) -> str:
    """The registered domain a page is on, so that sections of one organisation's site count as one
    site: en.wikipedia.org and simple.wikipedia.org are both wikipedia.org. This is what "different
    sites" means wherever sites are counted. It is an approximation made without a public-suffix
    list: an organisation that publishes under two names (bbc.com and bbc.co.uk) still counts twice."""
    host = site(url)
    if ':' in host or host.replace('.', '').isdigit():
        return host  # an IP address has no registered domain
    labels = host.split('.')
    keep = 3 if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in COUNTRY_SECOND_LEVEL else 2
    return '.'.join(labels[-keep:])


def _listed(host: str, sites: set) -> bool:
    return any(host == name or host.endswith('.' + name) for name in sites)


def _under_country_code(labels: list, names: set) -> bool:
    return len(labels) >= 2 and len(labels[-1]) == 2 and labels[-2] in names


def rate(url: str | None) -> Rating:
    """The category of the site a page is on, judged from its address only."""
    host = site(url)
    if '.' not in host:
        return UNRATED
    # User-generated platforms first: a page there is user content whatever else the address suggests.
    for rating, sites in (*USER_GENERATED.items(), *SITES.items()):
        if _listed(host, sites):
            return rating
    labels, dotted = host.split('.'), '.' + host
    if (dotted.endswith(GOVERNMENT_ENDINGS) or dotted.endswith(GOVERNMENT_COUNTRY_ENDINGS)
            or _under_country_code(labels, GOVERNMENT_SECOND_LEVEL)):
        return GOVERNMENT
    if (dotted.endswith(ACADEMIC_ENDINGS) or dotted.endswith(ACADEMIC_COUNTRY_ENDINGS)
            or _under_country_code(labels, ACADEMIC_SECOND_LEVEL)):
        return ACADEMIC
    return UNRATED


def accepted_as_evidence(url: str | None) -> bool:
    """Whether a page may serve as evidence at all. Social media and user-generated pages may not."""
    return rate(url).tier != 'user_generated'


def assess(urls) -> tuple[str | None, int | None]:
    """How strong the sources behind a verdict are, from the pages it cites.

    Returns (strength, score). Each site counts once however many passages come from it.
    Strength: 'strong' with two or more official or established sites, 'moderate' with one, 'weak'
    with none. Score: the sites' average weight as 0-100. Both describe the sources, not the
    chance that the verdict is right. Returns (None, None) when no page is cited.
    """
    ratings = {}
    for url in urls:
        if site(url):
            # Pages in two categories on one domain (a journal index on a government site) count once, at the higher weight.
            ratings[domain(url)] = max(ratings.get(domain(url), SOCIAL_MEDIA), rate(url), key=lambda rating: rating.weight)
    if not ratings:
        return None, None
    rated = sum(rating.tier in RATED_TIERS for rating in ratings.values())
    strength = 'strong' if rated >= 2 else 'moderate' if rated == 1 else 'weak'
    return strength, round(100 * sum(rating.weight for rating in ratings.values()) / len(ratings))
