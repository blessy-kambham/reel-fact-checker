"""Verdict agent.

Gives the verdict for one claim. It sees only the claim and the evidence that passed verification:
not the rest of the submission, the analyst's proposed verdict or the research notes. It must cite
the evidence IDs it relied on, and the application checks that those citations justify the verdict.

The agent chooses between two tools. `issue_verdict` gives the verdict. `request_evidence`, offered
only while another research round is possible, says the evidence does not settle the claim and names
what is missing, so the orchestrator can send the claim back for research. When the application
refuses a verdict because it cited evidence IDs that do not exist, or none at all, the agent is told
why and may cite again, but it may not change the verdict to get past the check.

Used by: `agents/orchestrator.py`, after citation verification.
"""
import asyncio
import json
from dataclasses import dataclass, field

from schemas import VerdictAction, VerdictDecision
from services.budget import BudgetExceeded
from services.providers import ProviderFailure

from agents.runtime import Done, PlanningUnavailable, autonomous, run_tools

# Refusals the agent may correct by citing again. Every other refusal withholds the verdict at once.
CITATION_SLIPS = {
    'unknown_evidence_ids': 'cite only IDs listed in verified_evidence',
    'missing_evidence_ids': 'cite the evidence IDs the verdict relies on',
}
TOOLS = {
    'issue_verdict': 'Give the verdict for the target assertion, with the IDs of the verified evidence that justify it.',
    'request_evidence': 'Use when the verified evidence does not settle the target but a specific kind of evidence would. '
                        'Name it in "missing". One more research round will look for it.',
}

VERDICT_INSTRUCTIONS = (
    'Judge only TARGET ASSERTION using only the supplied verified evidence. '
    'Do not judge neighboring assertions or infer a broader submission. '
    'Resolve pronouns only when the evidence makes their referent clear; otherwise use UNVERIFIABLE. '
    'TRUE requires direct support; FALSE requires contradiction. MISLEADING, PARTIALLY TRUE and OUTDATED '
    'require evidence establishing that specific defect in the target itself. '
    'A true assertion does not become misleading because a different assertion might be false. '
    'Background alone cannot establish a verdict. Conflicting evidence warrants UNVERIFIABLE unless '
    'the supplied evidence resolves the conflict. Return the evidence IDs supporting the decision. '
    'Do not use outside knowledge; source repetition is not independent confirmation.')
TOOL_INSTRUCTIONS = VERDICT_INSTRUCTIONS + (
    ' Choose exactly ONE tool from "tools". Prefer issue_verdict whenever the verified evidence settles the target. '
    'If "last_step" says a verdict was refused, issue the same verdict again with corrected evidence IDs.')


def check_decision(decision, usable):
    """Return a WithheldReason when a verdict-stage answer is not justified by its cited evidence, else None."""
    by_id = {c.evidence_id: c for c in usable}
    selected = set(decision.evidence_ids)
    stances = {by_id[i].stance for i in selected if i in by_id}
    if not selected <= by_id.keys():
        return 'unknown_evidence_ids'
    if decision.verdict == 'UNVERIFIABLE':
        return None
    if not selected:
        return 'missing_evidence_ids'
    if not stances & {'FOR', 'AGAINST'}:
        return 'evidence_stance_mismatch'
    if decision.verdict == 'TRUE' and 'FOR' not in stances:
        return 'evidence_stance_mismatch'
    if decision.verdict == 'FALSE' and 'AGAINST' not in stances:
        return 'evidence_stance_mismatch'
    if decision.verdict in ('TRUE', 'FALSE') and {'FOR', 'AGAINST'} <= {c.stance for c in usable}:
        return 'conflicting_evidence'
    return None


@dataclass
class VerdictOutcome:
    verdict: str | None = None            # the agent's raw answer, kept for audit
    evidence_ids: list = field(default_factory=list)
    withheld: str | None = None           # a WithheldReason when the verdict may not be issued
    request: str | None = None            # set instead of a verdict: the evidence the agent says is missing
    looking_for: str = 'supporting'


class VerdictAgent:
    name = 'Verdict Agent'

    def __init__(self, provider):
        self.provider = provider

    async def decide(self, claim_text: str, usable: list, can_request: bool = False, steps=None) -> VerdictOutcome:
        """The verdict for one claim from its verified evidence. With `can_request`, the agent may ask
        for more evidence instead; the outcome then carries `request` and no verdict."""
        evidence = [{'id': c.evidence_id, 'statement': c.statement, 'quote': c.quote,
                     'stance': c.stance, 'url': c.url} for c in usable]
        data = {'target_assertion': claim_text, 'verified_evidence': evidence}
        try:
            if autonomous(self.provider, 'verdict'):
                return await self._choose(data, usable, can_request, steps if steps is not None else [])
            decision = await self.provider.structured(VerdictDecision, VERDICT_INSTRUCTIONS, json.dumps(data))
        except BudgetExceeded:
            raise
        except (ProviderFailure, asyncio.TimeoutError, PlanningUnavailable):
            return VerdictOutcome(withheld='verdict_check_unavailable')
        return self._outcome(decision, usable)

    @staticmethod
    def _outcome(decision, usable) -> VerdictOutcome:
        # Model-supplied strings: bound them before storing them in a report.
        return VerdictOutcome(verdict=decision.verdict, evidence_ids=[str(i)[:32] for i in decision.evidence_ids],
                              withheld=check_decision(decision, usable))

    async def _choose(self, data: dict, usable: list, can_request: bool, steps: list) -> VerdictOutcome:
        """The agent's tool loop: issue a verdict, or ask for the evidence that is missing."""
        first = None  # the first refused answer, kept so a second answer cannot change the verdict

        async def issue_verdict(action):
            nonlocal first
            outcome = self._outcome(action, usable)
            if first is not None:
                if outcome.verdict != first.verdict:
                    steps.append('Changed its verdict after a refusal, so the first refusal stands.')
                    return Done(first)
                if outcome.withheld is None:
                    steps.append('Cited its evidence again after a refusal; the verdict was then accepted.')
                return Done(outcome)
            if outcome.withheld in CITATION_SLIPS:
                first = outcome
                return (f'issue_verdict refused ({outcome.withheld.replace("_", " ")}): {CITATION_SLIPS[outcome.withheld]}. '
                        'Issue the same verdict with corrected evidence IDs.')
            return Done(outcome)

        async def request_evidence(action):
            missing = ' '.join(action.missing.split())[:300]
            if not can_request or first is not None or not missing:
                return 'request_evidence refused: no further research is possible. Issue a verdict from the verified evidence.'
            steps.append(f'Asked for more evidence before deciding: {missing}')
            return Done(VerdictOutcome(request=missing, looking_for=action.looking_for))

        tools = {name: text for name, text in TOOLS.items() if can_request or name != 'request_evidence'}
        try:
            done = await run_tools(self.provider, VerdictAction, TOOL_INSTRUCTIONS,
                                   lambda steps_left, last_step: {**data, 'tools': tools, 'last_step': last_step},
                                   {'issue_verdict': issue_verdict, 'request_evidence': request_evidence}, max_steps=2)
        except PlanningUnavailable:
            if first is None:
                raise
            done = None  # The second answer never came: the first refusal stands.
        return done.value if done else (first or VerdictOutcome(withheld='verdict_check_unavailable'))
