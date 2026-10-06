"""Citation Verifier agent.

Confirms that a citation is real before it can count as evidence. First the application checks that
the quote appears in the page that was read. Then a separate model judgment checks that the quote
supports the statement attributed to it and has the stance assigned to it.

On a long page the verifier decides how much it needs to read. It is first shown the quote with the
text around it and chooses a tool: `accept`, `reject`, or `read_full_page` when the passage leaves any
doubt (reported speech, a myth being debunked, a qualification elsewhere). After reading the full page
it must accept or reject. Short pages are always judged in full.

Used by: `agents/orchestrator.py`, for every passage the analyst selects.
"""
import asyncio
import hashlib
import json

from schemas import Citation, CitationJudgment, VerifierAction
from services.budget import BudgetExceeded
from tools.providers import ProviderFailure

from agents.runtime import Done, PlanningUnavailable, autonomous, run_tools
from agents.shared import normalized

# Text shown on each side of the quote before the verifier decides whether to read the whole page.
PASSAGE_MARGIN = 2000
TOOLS = {
    'accept': 'The citation passes both checks. Set supports_attribution and stance_matches to true.',
    'reject': 'The citation fails a check. Set the check that failed to false and explain which.',
    'read_full_page': 'Read the whole page before deciding. Use it whenever the passage leaves any doubt.',
}

JUDGMENT_INSTRUCTIONS = (
    'Make two separate judgments. First, supports_attribution: does the quoted passage, read in the full page context, '
    'support the attributed STATEMENT without distortion? This is about the statement, not whether it proves the original claim. '
    'Second, stance_matches: does the supported statement have the assigned relationship to the ORIGINAL CLAIM? '
    'FOR requires direct support for that claim; AGAINST requires direct contradiction. '
    'A passage that only reports a belief, allegation or superseded model which the page does not present as fact '
    'is not FOR, unless the claim is itself about what was believed or alleged. '
    'CONTEXT requires relevant, accurately attributed background such as a definition or scope explanation; '
    'it need not establish the claim itself. Do not reject valid CONTEXT merely because it does not prove the claim. '
    'Reject irrelevant material, unsupported statements, missing qualifications, and cherry-picked attributions. '
    'Do not accept direct support or contradiction mislabeled as CONTEXT. A context label does not excuse an unsupported statement. '
    'Use false for uncertain judgments and explain which check failed. '
    'Also set opposite_stance. It is true only when the passage in fact has the OPPOSITE relationship to the ORIGINAL '
    'CLAIM from the assigned stance: assigned FOR but it contradicts the claim, or assigned AGAINST but it supports it. '
    'It is false when the passage is merely background, irrelevant or unclear, and whenever stance_matches is true. '
    'This is not a truth guarantee.')
INVESTIGATION_INSTRUCTIONS = JUDGMENT_INSTRUCTIONS + (
    ' Choose exactly ONE tool from "tools". "passage" is the quote with the text around it, not the whole page. '
    'Accept or reject from the passage only when it settles both judgments. Read the full page when the passage '
    'leaves any doubt: for example when the quote may be reported speech, a myth being corrected, a hypothetical, '
    'or qualified elsewhere on the page. When "page" is present you have the whole page and must accept or reject.')


class CitationVerifierAgent:
    name = 'Citation Verifier'

    def __init__(self, provider):
        self.provider = provider

    async def verify(self, draft, sources: dict, claim_text: str = '', steps=None, at: int | None = None) -> Citation:
        """`at` is where the selected excerpt starts in the page, when known."""
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
                judgment = await self._judge(draft, source, claim_text, steps, at)
                verified = judgment.supports_attribution and judgment.stance_matches
                # A passage the verifier places on the other side from the analyst is a disagreement about the
                # evidence, not just a weak citation, so it gets a code of its own.
                opposed = not judgment.stance_matches and judgment.opposite_stance and draft.stance in ('FOR', 'AGAINST')
                code = 'verified' if verified else 'stance_opposed' if opposed else 'attribution_rejected'
                reason = judgment.reason
            except BudgetExceeded:
                raise
            except (ProviderFailure, asyncio.TimeoutError, PlanningUnavailable):
                # Keep prior verified evidence; do not copy provider exception bodies into reports.
                code = 'check_unavailable'
                reason = 'The attribution check could not complete. This citation was not verified.'
        return Citation(**draft.model_dump(), title=source.title, url=source.url, verified=verified,
                        verification=reason, verification_code=code, retrieved_at=source.retrieved_at, retrieval=source.retrieval,
                        source_text_sha256=hashlib.sha256(source.text.encode('utf-8')).hexdigest())

    async def _judge(self, draft, source, claim_text: str, steps, at) -> CitationJudgment:
        """The attribution and stance judgment for a quote that is known to appear in the page."""
        base = {'claim': claim_text, 'stance': draft.stance, 'statement': draft.statement, 'quote': draft.quote}
        if at is None or source.text[at:at + len(draft.quote)] != draft.quote:
            at = source.text.find(draft.quote)
        start, end = max(0, at - PASSAGE_MARGIN), at + len(draft.quote) + PASSAGE_MARGIN
        if not autonomous(self.provider, 'citation_verifier') or at < 0 or (start == 0 and end >= len(source.text)):
            # Fixed mode, or a page short enough that the passage is the whole page: one judgment in full.
            return await self.provider.structured(CitationJudgment, JUDGMENT_INSTRUCTIONS,
                                                  json.dumps({**base, 'page': source.text}))

        # Investigation: the verifier chooses whether the passage is enough or the whole page is needed.
        undecided = CitationJudgment(supports_attribution=False, stance_matches=False, opposite_stance=False,
                                     reason='The verifier could not decide after reading the full page.')
        full_page = False

        async def decide(action):
            accepted = action.tool == 'accept'
            return Done(CitationJudgment(supports_attribution=accepted and action.supports_attribution,
                                         stance_matches=accepted and action.stance_matches,
                                         opposite_stance=action.opposite_stance, reason=action.reason))

        async def read_full_page(action):
            nonlocal full_page
            if full_page:
                return Done(undecided)
            full_page = True
            if steps is not None:
                steps.append(f'Read the full page of {source.id} before deciding on a passage.')
            return 'read_full_page: the whole page is now in "page". Accept or reject.'

        def state(steps_left, last_step):
            tools = {name: text for name, text in TOOLS.items() if not (full_page and name == 'read_full_page')}
            text = {'page': source.text} if full_page else {'passage': source.text[start:end]}
            return {**base, **text, 'tools': tools, 'last_step': last_step}

        done = await run_tools(self.provider, VerifierAction, INVESTIGATION_INSTRUCTIONS, state,
                               {'accept': decide, 'reject': decide, 'read_full_page': read_full_page}, max_steps=2)
        return done.value if done else undecided
