"""Article URL ingestion: fetch a public article, pick up to three central claims copied verbatim,
and research them with the same pipeline. The article itself never counts as evidence."""
import hashlib
import json
from uuid import uuid4

from schemas import AtomicClaim, Extraction, Report
from services.fetcher import fetch_text
from services.pipeline import loose, now, page_key, research_all, unresolved  # noqa: F401 (loose re-exported)

MAX_CONTEXT_CHARS = 600


class ArticleUnavailable(Exception):
    """The URL could not be fetched safely as a readable article."""


def verbatim(value: str, article: str) -> bool:
    return bool(value.strip()) and loose(value) in loose(article)


async def select_and_research(text, provider, fetch=fetch_text, *, kind, exclude=frozenset()):
    """Pick up to three central claims copied verbatim from `text` and research them.

    Returns (extraction, results, refused count, claims whose context was dropped). Claims not found
    word for word are refused, never researched; context not found word for word is dropped. Pages in
    `exclude`, and pages repeating a claim or its context word for word, are never used as evidence.
    """
    extraction = await provider.structured(Extraction,
        f'This is {kind}. Classify its intent. Select at most three central factual claims it '
        'itself asserts: the claims a reader most needs checked, capable of being checked, not known to be true. '
        'Include false, misleading and uncertain claims exactly as asserted; never skip one because you think it is wrong. '
        'Copy each claim VERBATIM as a contiguous substring of the text; do not paraphrase, merge or expand pronouns. '
        'Prefer claims that stand alone. Set context to the verbatim surrounding sentence or two (at most 600 characters) '
        'that a reader needs to understand the claim. Ignore navigation, advertising, comments and quotes the text rejects. '
        'Set omitted_claims when the text contains other checkable claims. For opinion, satire or fiction return no claims. '
        'Do not determine truth.', json.dumps({'text': text}))
    checked, refused, context_dropped = [], [], 0
    for claim in extraction.claims if extraction.intent == 'FACTUAL' else []:
        if not verbatim(claim.text, text):
            refused.append(unresolved(claim.text, 'This claim was not copied word for word from the source, so it was not researched.',
                                      'coverage_failed'))
            continue
        context = claim.context.strip()[:MAX_CONTEXT_CHARS]
        if context and not verbatim(context, text):
            # Surrounding text the source does not contain could change the claim's meaning: drop it.
            context, context_dropped = '', context_dropped + 1
        checked.append(AtomicClaim(text=claim.text, context=context))
    # Reposts of the checked material are not independent evidence for it.
    markers = {claim.text: [claim.text, claim.context] for claim in checked}
    results = await research_all(checked, provider, fetch, exclude=exclude, copy_markers=markers) + refused
    return extraction, results, len(refused), context_dropped


async def run_article_pipeline(url, provider, fetch=fetch_text) -> Report:
    try:
        final_url, text = await fetch(url)
    except Exception as exc:
        raise ArticleUnavailable('That link could not be read as a public article. Check it is a public HTTPS page with readable text.') from exc
    extraction, results, refused, context_dropped = await select_and_research(
        text, provider, fetch, kind='the text of a web article',
        exclude=frozenset({page_key(url), page_key(final_url)}))
    limitations = ['Article mode checks at most three central claims. Other claims in the article were not checked.',
                   'The article itself, and pages repeating its claims word for word, are excluded as evidence for its own claims.',
                   'Article text is read from the public page as fetched; paywalled or script-rendered content may be missing.']
    if context_dropped:
        limitations.append(f'{context_dropped} claim(s) were checked without surrounding context, because the context '
                           'supplied was not found word for word in the article.')
    return Report(id=str(uuid4()), mode='live', submitted_text=final_url, created_at=now(), intent=extraction.intent,
                  note=extraction.note, claims=results, limitations=limitations, usage=provider.usage,
                  omitted_claims=extraction.omitted_claims,
                  coverage_status='incomplete' if refused else 'passed',
                  input_type='article', source_url=final_url, source_sha256=hashlib.sha256(text.encode('utf-8')).hexdigest())
