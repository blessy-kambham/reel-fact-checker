"""Citation Verifier agent.

Confirms that a citation is real before it can count as evidence. First the application checks that
the quote appears in the page that was read. Then a separate model judgment checks that the quote
supports the statement attributed to it and has the stance assigned to it.

Used by: `agents/orchestrator.py`, for every passage the analyst selects.
"""
import asyncio
import hashlib
import json

from schemas import Citation, CitationJudgment
from services.budget import BudgetExceeded
from services.providers import ProviderFailure

from agents.shared import normalized


class CitationVerifierAgent:
    name = 'Citation Verifier'

    def __init__(self, provider):
        self.provider = provider

    async def verify(self, draft, sources: dict, claim_text: str = '') -> Citation:
        source = sources.get(draft.source_id)
        if source is None:
            return Citation(**draft.model_dump(), title='Unknown source', url=None, verified=False,
                            verification='Rejected: source was not retrieved in this run.', verification_code='unknown_source')
        verified = False
        quote = normalized(draft.quote)
        code = 'quote_not_found' if quote else 'empty_quote'
        reason = 'Rejected: quote was not found in the retrieved page.' if quote else 'Rejected: quote is empty after whitespace normalization.'
        if quote and quote in normalized(source.text):
            try:
                judgment = await self.provider.structured(CitationJudgment,
                    'Make two separate judgments. First, supports_attribution: does the quoted passage, read in the full page context, '
                    'support the attributed STATEMENT without distortion? This is about the statement, not whether it proves the original claim. '
                    'Second, stance_matches: does the supported statement have the assigned relationship to the ORIGINAL CLAIM? '
                    'FOR requires direct support for that claim; AGAINST requires direct contradiction. '
                    'CONTEXT requires relevant, accurately attributed background such as a definition or scope explanation; '
                    'it need not establish the claim itself. Do not reject valid CONTEXT merely because it does not prove the claim. '
                    'Reject irrelevant material, unsupported statements, missing qualifications, and cherry-picked attributions. '
                    'Do not accept direct support or contradiction mislabeled as CONTEXT. A context label does not excuse an unsupported statement. '
                    'Use false for uncertain judgments and explain which check failed. This is not a truth guarantee.',
                    json.dumps({'claim': claim_text, 'stance': draft.stance, 'statement': draft.statement, 'quote': draft.quote, 'page': source.text}))
                verified = judgment.supports_attribution and judgment.stance_matches
                code = 'verified' if verified else 'attribution_rejected'
                reason = judgment.reason
            except BudgetExceeded:
                raise
            except (ProviderFailure, asyncio.TimeoutError):
                # Keep prior verified evidence; do not copy provider exception bodies into reports.
                code = 'check_unavailable'
                reason = 'The attribution check could not complete. This citation was not verified.'
        return Citation(**draft.model_dump(), title=source.title, url=source.url, verified=verified,
                        verification=reason, verification_code=code, retrieved_at=source.retrieved_at, retrieval=source.retrieval,
                        source_text_sha256=hashlib.sha256(source.text.encode('utf-8')).hexdigest())
