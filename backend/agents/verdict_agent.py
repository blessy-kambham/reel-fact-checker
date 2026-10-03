"""Verdict agent.

Gives the verdict for one claim. It sees only the claim and the evidence that passed verification:
not the rest of the submission, the analyst's proposed verdict or the research notes. It must cite
the evidence IDs it relied on, and the application checks that those citations justify the verdict.

Used by: `agents/orchestrator.py`, after citation verification.
"""
import asyncio
import json
from dataclasses import dataclass, field

from schemas import VerdictDecision
from services.budget import BudgetExceeded
from services.providers import ProviderFailure


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


class VerdictAgent:
    name = 'Verdict Agent'

    def __init__(self, provider):
        self.provider = provider

    async def decide(self, claim_text: str, usable: list) -> VerdictOutcome:
        evidence = [{'id': c.evidence_id, 'statement': c.statement, 'quote': c.quote,
                     'stance': c.stance, 'url': c.url} for c in usable]
        try:
            decision = await self.provider.structured(VerdictDecision,
                'Judge only TARGET ASSERTION using only the supplied verified evidence. '
                'Do not judge neighboring assertions or infer a broader submission. '
                'Resolve pronouns only when the evidence makes their referent clear; otherwise use UNVERIFIABLE. '
                'TRUE requires direct support; FALSE requires contradiction. MISLEADING, PARTIALLY TRUE and OUTDATED '
                'require evidence establishing that specific defect in the target itself. '
                'A true assertion does not become misleading because a different assertion might be false. '
                'Background alone cannot establish a verdict. Conflicting evidence warrants UNVERIFIABLE unless '
                'the supplied evidence resolves the conflict. Return the evidence IDs supporting the decision. '
                'Do not use outside knowledge; source repetition is not independent confirmation.',
                json.dumps({'target_assertion': claim_text, 'verified_evidence': evidence}))
        except BudgetExceeded:
            raise
        except (ProviderFailure, asyncio.TimeoutError):
            return VerdictOutcome(withheld='verdict_check_unavailable')
        # Model-supplied strings: bound them before storing them in a report.
        return VerdictOutcome(verdict=decision.verdict, evidence_ids=[str(i)[:32] for i in decision.evidence_ids],
                              withheld=check_decision(decision, usable))
