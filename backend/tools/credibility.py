"""Source credibility: a rule-based rating of where a page comes from.

Every page the app reads is placed in one of four tiers by its web address alone. The rating says
what kind of publisher a page belongs to. It does not say the page is right: a government site can
be wrong and an unrated site can be correct, which is why every passage still has to pass the
citation checks whatever its tier.

The rating is used in three places:

- pages on social media and other user-generated platforms are not accepted as evidence;
- the research agent and the analyst are shown each page's tier, so they can prefer better sources;
- each issued verdict reports how strong its sources are (`evidence_strength`).

The lists below are short and deliberately conservative. A site that is not listed is "unrated",
not "bad". The weights are stated assumptions, not measurements.
"""
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Rating:
    tier: str      # 'official', 'established', 'unrated' or 'user_generated'
    label: str     # shown in reports
    weight: float  # 0 to 1, used only for the source score


OFFICIAL = Rating('official', 'Official, academic or peer-reviewed source', 1.0)
ESTABLISHED = Rating('established', 'Reference work, fact-checker or established publisher', 0.8)
UNRATED = Rating('unrated', 'Unrated website', 0.5)
USER_GENERATED = Rating('user_generated', 'Social media or user-generated platform', 0.2)
TIERS = {rating.tier: rating for rating in (OFFICIAL, ESTABLISHED, UNRATED, USER_GENERATED)}

# Government, military, intergovernmental and academic addresses, recognised by how they end.
OFFICIAL_ENDINGS = ('.gov', '.mil', '.edu', '.int')
# The same kinds of body under a country code: gov.uk, edu.au, gouv.fr, gob.mx and so on.
OFFICIAL_SECOND_LEVEL = {'gov', 'mil', 'edu', 'gouv', 'gob'}
# Country registries that use other names for government and universities.
OFFICIAL_COUNTRY_ENDINGS = ('.ac.uk', '.ac.jp', '.ac.in', '.ac.nz', '.ac.za', '.ac.kr', '.go.jp', '.go.kr', '.go.id',
                            '.gc.ca', '.canada.ca', '.admin.ch', '.bund.de')

OFFICIAL_SITES = {
    # Intergovernmental and scientific bodies
    'europa.eu', 'un.org', 'unesco.org', 'worldbank.org', 'imf.org', 'oecd.org', 'ipcc.ch', 'cern.ch', 'iau.org',
    'iaea.org', 'nationalacademies.org', 'royalsociety.org', 'nobelprize.org',
    # Peer-reviewed journals
    'nature.com', 'science.org', 'thelancet.com', 'nejm.org', 'bmj.com', 'pnas.org', 'cell.com', 'jamanetwork.com',
}
ESTABLISHED_SITES = {
    # Reference works
    'britannica.com', 'wikipedia.org', 'merriam-webster.com',
    # Fact-checkers
    'snopes.com', 'politifact.com', 'factcheck.org', 'fullfact.org',
    # News agencies, public broadcasters and newspapers of record
    'reuters.com', 'apnews.com', 'afp.com', 'bbc.com', 'bbc.co.uk', 'npr.org', 'pbs.org', 'nytimes.com',
    'washingtonpost.com', 'wsj.com', 'theguardian.com', 'economist.com', 'ft.com', 'bloomberg.com',
    # Science and history publishers
    'scientificamerican.com', 'nationalgeographic.com', 'smithsonianmag.com', 'newscientist.com', 'sciencenews.org',
    'sciencefocus.com',
}
USER_GENERATED_SITES = {
    'instagram.com', 'facebook.com', 'fb.com', 'tiktok.com', 'x.com', 'twitter.com', 'threads.net', 'threads.com',
    'youtube.com', 'youtu.be', 'reddit.com', 'quora.com', 'pinterest.com', 'tumblr.com', 'linkedin.com', 'snapchat.com',
    't.me', 'discord.com', 'vk.com', 'weibo.com', 'medium.com', 'substack.com', 'blogspot.com', 'wordpress.com',
    'stackexchange.com', 'fandom.com',
}


def site(url: str | None) -> str:
    """The host a page is on, without a leading `www.`; empty when there is none."""
    host = (urlsplit(url or '').hostname or '').lower().rstrip('.')
    return host[4:] if host.startswith('www.') else host


def _listed(host: str, sites: set) -> bool:
    return any(host == name or host.endswith('.' + name) for name in sites)


def rate(url: str | None) -> Rating:
    """The tier of the site a page is on, judged from its address only."""
    host = site(url)
    if '.' not in host:
        return UNRATED
    # User-generated platforms first: a page there is user content whatever else the address suggests.
    if _listed(host, USER_GENERATED_SITES):
        return USER_GENERATED
    labels, dotted = host.split('.'), '.' + host
    if (dotted.endswith(OFFICIAL_ENDINGS) or dotted.endswith(OFFICIAL_COUNTRY_ENDINGS) or _listed(host, OFFICIAL_SITES)
            or (len(labels) >= 2 and len(labels[-1]) == 2 and labels[-2] in OFFICIAL_SECOND_LEVEL)):
        return OFFICIAL
    if _listed(host, ESTABLISHED_SITES):
        return ESTABLISHED
    return UNRATED


def accepted_as_evidence(url: str | None) -> bool:
    """Whether a page may serve as evidence at all. Social media and user-generated pages may not."""
    return rate(url).tier != USER_GENERATED.tier


def assess(urls) -> tuple[str | None, int | None]:
    """How strong the sources behind a verdict are, from the pages it cites.

    Returns (strength, score). Each site counts once however many passages come from it.
    Strength: 'strong' with two or more official or established sites, 'moderate' with one, 'weak'
    with none. Score: the sites' average weight as 0-100. Both describe the sources, not the
    chance that the verdict is right. Returns (None, None) when no page is cited.
    """
    ratings = {site(url): rate(url) for url in urls if site(url)}
    if not ratings:
        return None, None
    rated = sum(rating.tier in (OFFICIAL.tier, ESTABLISHED.tier) for rating in ratings.values())
    strength = 'strong' if rated >= 2 else 'moderate' if rated == 1 else 'weak'
    return strength, round(100 * sum(rating.weight for rating in ratings.values()) / len(ratings))
