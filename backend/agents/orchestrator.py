"""Orchestrator agent.

Directs the other agents for one fact-check and owns the shared state. For each claim the model
chooses which agent works next: its tools are the other agents (`research_agent`, `analyst_agent`,
`citation_verifier`, `verdict_agent`) and `finish`. After each hand-off it sees what that agent
produced and decides again, so it can send a claim back for a second research round when the
verified evidence is thin or one-sided, or when the verdict agent asks for more.

    Claim Extractor ─▶ for each claim, two at a time:
                         Research Agent ⇄ Analyst Agent ─▶ Citation Verifier ─▶ Verdict Agent
                                  ▲                                                 │
                                  └──────────── second round, if needed ◀───────────┘

The orchestrator decides the route; the application enforces the rules. No hand-off can skip a stage:
pages must be analysed, passages must be verified, and a verdict is refused while a search or a
citation has failed. Any stage the orchestrator leaves out is run in the standard order afterwards,
which is also what happens in fixed mode (`AGENT_MODE`) or when the orchestrator cannot plan.

Used by: `main.py` (statements), `services/article.py` (articles) and `services/video.py` (videos).
"""
import asyncio
import time
from uuid import uuid4

from schemas import Citation, ClaimResult, OrchestratorAction, Report
from services.budget import BudgetExceeded
from services.fetcher import fetch_text
from services.input_mapping import map_input
from services.providers import ProviderFailure

from agents.analyst_agent import AnalystAgent
from agents.citation_verifier import CitationVerifierAgent
from agents.claim_extractor import ClaimExtractorAgent
from agents.research_agent import ResearchAgent
from agents.runtime import Done, PlanningUnavailable, autonomous, run_tools
from agents.shared import (COMPLETE_WITHHELD_REASONS, INVALID_REFERENCE_CODES, SINGLE_SOURCE_NOTE,
                           WITHHELD_MESSAGES, now, unresolved)
from agents.verdict_agent import VerdictAgent

NAME = 'Orchestrator'
MAX_PARALLEL_CLAIMS = 2
CLAIM_TIMEOUT_SECONDS = 150
MAX_STEPS = 9              # hand-offs the orchestrator may make for one claim, including refused ones
MAX_RESEARCH_ROUNDS = 2    # the first round plus one follow-up
MIN_ROUND_SECONDS = 85     # a follow-up round is refused when less of the claim's time limit is left
FIRST_SELECTIONS = 6       # passages the analyst may select from the first round's pages
FOLLOW_UP_SELECTIONS = 3   # and from a follow-up round's pages

# The orchestrator's tools are the other agents.
TOOLS = {
    'research_agent': 'Gathers web pages for the claim. Call it first. It may be called once more later: say what '
                      'evidence is still missing in "instruction" and set "looking_for".',
    'analyst_agent': 'Reads the pages not yet analysed and selects passages for and against the claim.',
    'citation_verifier': 'Checks every selected passage not yet checked. Only verified passages count as evidence.',
    'verdict_agent': 'Gives the verdict from the verified evidence. It may instead ask for more evidence.',
    'finish': 'Stop directing. Use it when no verdict can be reached; the report then says why.',
}
INSTRUCTIONS = (
    'You are the orchestrator of a fact-checking team working on one claim. You never research or judge the claim '
    'yourself: each step, choose exactly ONE tool from "tools" to say which agent works next, using "progress" and '
    '"last_step". The usual route is research_agent, analyst_agent, citation_verifier, verdict_agent. '
    'Send the claim back to research_agent when the verified evidence does not directly support or contradict the '
    'claim, when a search failed, or when the verdict agent asked for more evidence: write the specific evidence '
    'that is missing in "instruction". New pages must go to analyst_agent and citation_verifier before verdict_agent. '
    'Do not ask for more research when verified evidence already addresses the claim directly, or when "remaining" '
    'shows no research round is left. Choose finish only when a tool was refused and nothing else can help.')
VERDICT_REFUSALS = {
    'search_failed': 'a search failed, so the research is one-sided. Send the claim back to research_agent for the missing side, or finish.',
    'citation_failed': 'a citation failed verification, so no verdict may be issued for this claim. Finish.',
    'no_relevant_evidence': 'no verified passage directly supports or contradicts the claim. Send the claim back to research_agent, or finish.',
}


async def verify_citation(draft, sources, provider, claim_text=''):
    """Citation Verifier step for one drafted citation."""
    return await CitationVerifierAgent(provider).verify(draft, sources, claim_text)


async def verify_selection(selection, sources, excerpts, provider, claim_text):
    """Analyst relation check, then the Citation Verifier, for one selected passage."""
    rejected, draft, metadata = await AnalystAgent(provider).classify(selection, sources, excerpts, claim_text)
    if rejected is not None:
        return rejected
    # Independent attribution AND stance validation remains mandatory after classification.
    citation = await verify_citation(draft, sources, provider, claim_text)
    return citation.model_copy(update=metadata)


class _Case:
    """One claim while the agents work on it: what each agent has produced so far.

    Each method hands the claim to one agent. `complete` runs whatever has not run, in the standard
    order, and `result` applies the rules for issuing or withholding the verdict.
    """

    def __init__(self, claim, provider, fetch, exclude, copy_markers):
        self.claim, self.provider, self.exclude, self.copy_markers = claim, provider, exclude, copy_markers
        self.researcher = ResearchAgent(provider, fetch)
        self.pack = None               # the research agent's pages and search record
        self.rounds = 0                # research rounds run
        self.analysed = set()          # IDs of pages the analyst has read
        # Every selected passage, in the order selected: a (draft, metadata) pair while it waits for the
        # citation verifier, then a Citation, verified or not.
        self.passages = []
        self.outcome = None            # the verdict agent's answer
        self.evidence_request = None   # set when the verdict agent asked for more evidence instead
        self.steps = []                # what the agents did, in plain words
        self.started = time.monotonic()

    def log(self, agent: str, lines) -> None:
        self.steps.extend(f'{agent}: {line}' for line in lines)

    def seconds_left(self) -> float:
        return CLAIM_TIMEOUT_SECONDS - (time.monotonic() - self.started)

    @property
    def unread(self) -> dict:
        return {i: s for i, s in self.pack.sources.items() if i not in self.analysed} if self.pack else {}

    @property
    def pending(self) -> list:
        return [p for p in self.passages if not isinstance(p, Citation)]

    @property
    def citations(self) -> list:
        return [p for p in self.passages if isinstance(p, Citation)]

    @property
    def usable(self) -> list:
        """Verified evidence, numbered E1, E2, ... for the verdict agent to cite."""
        return [c.model_copy(update={'evidence_id': f'E{i + 1}'})
                for i, c in enumerate(c for c in self.citations if c.verified)]

    @property
    def failed(self) -> list:
        """Citations that failed a check. A proposal pointing at material that was never supplied, or at a
        passage the relation check found irrelevant, is the analyst's slip and is not counted here."""
        return [c for c in self.citations if not c.verified and c.verification_code not in INVALID_REFERENCE_CODES]

    def blocked(self):
        """The WithheldReason that rules out a verdict before the verdict agent is asked, or None."""
        if self.pack.search_failed:
            return 'search_failed'
        if self.failed:
            return 'citation_failed'
        if not any(c.stance in ('FOR', 'AGAINST') for c in self.citations if c.verified):
            return 'no_relevant_evidence'
        return None

    def follow_up_refusal(self):
        """Why a second research round is not possible, or None when it is."""
        if self.rounds >= MAX_RESEARCH_ROUNDS:
            return 'this claim has already had its second research round.'
        if self.unread or self.pending:
            return 'pages or passages from the last round are still waiting. Analyse and verify them first.'
        if not self.researcher.can_follow_up():
            return 'the search or page budget for this claim is spent.'
        if self.seconds_left() < MIN_ROUND_SECONDS:
            return 'too little of the time limit is left for another round.'
        return None

    # ---- hand-offs: one agent each ----------------------------------------------------------

    async def research(self, request: str = '', looking_for: str = 'supporting') -> int:
        """Research Agent: the first round, or a follow-up aimed at `request`. Returns new pages kept."""
        logged = len(self.pack.steps) if self.pack else 0
        if self.pack is None:
            self.pack = await self.researcher.gather(self.claim, self.exclude, self.copy_markers)
            new = len(self.pack.sources)
        else:
            new = await self.researcher.follow_up(request, looking_for)
            self.evidence_request = None
        self.rounds += 1
        self.log(ResearchAgent.name, self.pack.steps[logged:])
        return new

    async def analyse(self):
        """Analyst Agent: select passages from the pages not yet analysed and relate each to the claim.
        Returns (passages ready for verification, passages set aside)."""
        sources, first, notes = self.unread, not self.analysed, []
        self.analysed.update(sources)
        try:
            entries = await AnalystAgent(self.provider).build_case(
                self.claim, sources, self.pack.sources, FIRST_SELECTIONS if first else FOLLOW_UP_SELECTIONS, notes, review=first)
        except BudgetExceeded:
            raise
        except (ProviderFailure, asyncio.TimeoutError):
            if first:
                raise
            # A second round is extra: when its pages cannot be analysed, the first round's evidence stands.
            self.pack.warnings.append('Pages from the second research round could not be analysed, so they were not used.')
            return 0, 0
        self.passages += entries
        self.log(AnalystAgent.name, notes)
        ready = sum(not isinstance(e, Citation) for e in entries)
        return ready, len(entries) - ready

    async def verify(self):
        """Citation Verifier: check every selected passage that is waiting. Returns (verified, checked)."""
        notes, verified, checked = [], 0, 0
        verifier = CitationVerifierAgent(self.provider)
        for index, passage in enumerate(self.passages):
            if isinstance(passage, Citation):
                continue
            draft, metadata = passage
            # Independent attribution AND stance validation remains mandatory after classification.
            citation = await verifier.verify(draft, self.pack.sources, self.claim.text, notes, metadata['source_start'])
            self.passages[index] = citation.model_copy(update=metadata)
            verified, checked = verified + citation.verified, checked + 1
        self.log(CitationVerifierAgent.name, notes)
        return verified, checked

    async def decide(self, can_request: bool = False):
        """Verdict Agent: a verdict from the verified evidence, or a request for more evidence."""
        notes = []
        outcome = await VerdictAgent(self.provider).decide(self.claim.text, self.usable, can_request, notes)
        self.log(VerdictAgent.name, notes)
        if outcome.request:
            self.evidence_request = outcome
        else:
            self.outcome = outcome
        return outcome

    # ---- guarantees the application enforces whatever the orchestrator chose ---------------------

    async def complete(self) -> None:
        """Run every stage that has not run, in the standard order. In fixed mode this is the whole pipeline."""
        if self.pack is None:
            await self.research()                 # 1. Research Agent
        if self.unread:
            await self.analyse()                  # 2. Analyst Agent
        if self.pending:
            await self.verify()                   # 3. Citation Verifier
        if self.pack.sources and self.outcome is None and self.blocked() is None:
            await self.decide()                   # 4. Verdict Agent, only when a verdict is allowed at all

    def result(self) -> ClaimResult:
        """Issue the verdict, or withhold it with a named reason."""
        claim, pack = self.claim, self.pack
        sources, warnings, search_status = pack.sources, list(pack.warnings), pack.search_status
        if not sources:
            result = unresolved(claim.text, 'No readable sources were retrieved. Search snippets are not accepted as verified evidence.',
                                'search_failed' if pack.search_failed else 'no_sources')
            return result.model_copy(update={'supporting_search': search_status['FOR'], 'contradicting_search': search_status['AGAINST'],
                                             'limitations': result.limitations + warnings, 'agent_steps': list(self.steps)})
        citations, usable, failed = self.citations, self.usable, self.failed
        ignored = [c for c in citations if not c.verified and c.verification_code in INVALID_REFERENCE_CODES]
        if ignored:
            warnings.append(f'{len(ignored)} proposed citation(s) were not supplied material or did not concern this claim, and were ignored.')
        if failed:
            warnings.append(f'{len(failed)} citation(s) failed validation and were excluded.')

        decision_verdict, decision_ids = None, []
        withheld = self.blocked()
        if withheld is None:
            outcome = self.outcome
            if outcome is None:
                withheld = 'verdict_check_unavailable'
            else:
                decision_verdict, decision_ids, withheld = outcome.verdict, outcome.evidence_ids, outcome.withheld

        verdict = decision_verdict if withheld is None else 'UNVERIFIABLE'
        if withheld:
            warnings.append(WITHHELD_MESSAGES[withheld])
        incomplete = pack.search_failed or (withheld is not None and withheld not in COMPLETE_WITHHELD_REASONS)
        verdict_sources = None
        if withheld is None:
            verdict_sources = len({c.url or c.source_id for c in usable if c.evidence_id in decision_ids})
            if verdict != 'UNVERIFIABLE' and verdict_sources == 1:
                warnings.append(SINGLE_SOURCE_NOTE)
        if not any(c.stance == 'AGAINST' for c in usable):
            warnings.append('No verified contradicting evidence was identified in the retrieved pages. This does not prove the claim.')
        warnings.append('Citation checks use quote matching and a separate model judgment; human review may still find errors.')
        warnings.append('Source independence, publication dates, and methodology require review; no calibrated confidence score is available.')
        return ClaimResult(claim=claim.text, verdict=verdict, status='incomplete' if incomplete else 'complete',
                           evidence=usable, rejected_citations=[c for c in citations if not c.verified], limitations=list(dict.fromkeys(warnings)),
                           supporting_search=search_status['FOR'], contradicting_search=search_status['AGAINST'], sources_checked=len(sources),
                           agent_steps=list(self.steps), verdict_state='withheld' if withheld else 'issued', withheld_reason=withheld,
                           withheld_message=WITHHELD_MESSAGES[withheld] if withheld else None,
                           decision_verdict=decision_verdict, decision_evidence_ids=decision_ids,
                           verdict_evidence_ids=[] if withheld else decision_ids, verdict_source_count=verdict_sources)


async def _direct(case: _Case) -> None:
    """The orchestrator agent's loop: each step the model chooses which agent works on the claim next."""
    def say(line):
        case.steps.append(f'{NAME}: {line}')

    async def research_agent(action):
        if case.pack is None:
            say('Sent the claim to the Research Agent.')
            await case.research()
            return f'research_agent read {len(case.pack.sources)} page(s).'
        refusal = case.follow_up_refusal()
        request = ' '.join(action.instruction.split())[:300]
        if refusal or not request:
            return f'research_agent refused: {refusal or "say in instruction what evidence is still missing."}'
        say(f'Sent the claim back to the Research Agent for {action.looking_for} evidence: {request}')
        new = await case.research(request, action.looking_for)
        return f'research_agent read {new} new page(s).' if new else 'research_agent found no new readable pages.'

    async def analyst_agent(action):
        if case.pack is None:
            return 'analyst_agent refused: there are no pages yet. Call research_agent first.'
        if not case.unread:
            return ('analyst_agent refused: every page has been analysed.' if case.pack.sources else
                    'analyst_agent refused: no readable pages were found. Send the claim back to research_agent, or finish.')
        say(f'Sent {len(case.unread)} page(s) to the Analyst Agent.')
        ready, rejected = await case.analyse()
        return f'analyst_agent selected {ready} passage(s) for verification and set {rejected} aside.'

    async def citation_verifier(action):
        if not case.pending:
            return 'citation_verifier refused: no passages are waiting to be verified.'
        say(f'Sent {len(case.pending)} passage(s) to the Citation Verifier.')
        verified, total = await case.verify()
        return f'citation_verifier verified {verified} of {total} passage(s).'

    async def verdict_agent(action):
        if case.pack is None or case.unread or case.pending:
            return 'verdict_agent refused: research, analysis and citation verification must finish first.'
        blocked = case.blocked()
        if blocked:
            return f'verdict_agent refused: {VERDICT_REFUSALS[blocked]}'
        say('Asked the Verdict Agent for a verdict.')
        # It may ask for more evidence once, and only while another research round is possible.
        outcome = await case.decide(can_request=case.evidence_request is None and case.follow_up_refusal() is None)
        if outcome.request:
            return f'verdict_agent asked for more {outcome.looking_for} evidence before deciding: {outcome.request}'
        return Done()

    async def finish(action):
        say(f'Stopped directing: {" ".join(action.reason.split())[:300]}')
        return Done()

    def state(steps_left, last_step):
        pack, verified = case.pack, [c for c in case.citations if c.verified]
        return {
            'claim': case.claim.text, 'context': case.claim.context, 'tools': TOOLS,
            'progress': {
                'research_rounds': case.rounds,
                'pages_read': len(pack.sources) if pack else 0,
                'searches': {'supporting': pack.search_status.get('FOR'), 'contradicting': pack.search_status.get('AGAINST')} if pack else None,
                'pages_waiting_for_analysis': len(case.unread),
                'passages_waiting_for_verification': len(case.pending),
                'verified_evidence': {'supporting': sum(c.stance == 'FOR' for c in verified),
                                      'contradicting': sum(c.stance == 'AGAINST' for c in verified),
                                      'background': sum(c.stance == 'CONTEXT' for c in verified)},
                'passages_set_aside': len(case.citations) - len(verified) - len(case.failed),
                'citations_failed': len(case.failed),
                'verdict_agent_request': case.evidence_request.request if case.evidence_request else None,
            },
            'remaining': {'steps': steps_left, 'seconds': max(0, int(case.seconds_left())),
                          'research_rounds': 0 if pack is not None and not case.researcher.can_follow_up()
                                             else max(0, MAX_RESEARCH_ROUNDS - case.rounds)},
            'last_step': last_step,
        }

    try:
        done = await run_tools(case.provider, OrchestratorAction, INSTRUCTIONS, state,
                               {'research_agent': research_agent, 'analyst_agent': analyst_agent,
                                'citation_verifier': citation_verifier, 'verdict_agent': verdict_agent, 'finish': finish}, MAX_STEPS)
    except PlanningUnavailable:
        say('Could not choose the next agent, so the remaining stages ran in the standard order.')
        return
    if done is None:
        say('Used all of its steps, so the remaining stages ran in the standard order.')


async def research_claim(claim, provider, fetch=fetch_text, exclude=frozenset(), copy_markers=()) -> ClaimResult:
    """Take one claim through research, analysis, citation verification and the verdict."""
    case = _Case(claim, provider, fetch, exclude, copy_markers)
    if autonomous(provider, 'orchestrator'):
        await _direct(case)
    await case.complete()
    return case.result()


async def research_all(claims, provider, fetch=fetch_text, exclude=frozenset(), copy_markers=None):
    """Run one research branch per claim, at most two at a time; every failure becomes a named withheld verdict."""
    limiter = asyncio.Semaphore(MAX_PARALLEL_CLAIMS)
    async def branch(claim):
        async with limiter:
            try:
                markers = (copy_markers or {}).get(claim.text, ())
                return await asyncio.wait_for(research_claim(claim, provider, fetch, exclude, markers), timeout=CLAIM_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                return unresolved(claim.text, 'This claim exceeded its research time limit.', 'claim_timeout')
            except BudgetExceeded as exc:
                return unresolved(claim.text, str(exc), 'spending_limit')
            except ProviderFailure as exc:
                return unresolved(claim.text, str(exc), 'provider_failure')
    return list(await asyncio.gather(*(branch(c) for c in claims)))


def exact_submission_preserved(text, extraction):
    """An unchanged full submission cannot have lost words during extraction.

    This verifies representation only, never truth or atomicity.
    """
    if extraction.intent != 'FACTUAL' or extraction.omitted_claims or len(extraction.claims) != 1:
        return False
    claim = extraction.claims[0]
    return (claim.text.strip() == text.strip() and
            (not claim.context.strip() or claim.context.strip() in text))


async def _check_extraction(extractor, text, extraction):
    """The application's check of a statement extraction. Returns (input spans, coverage status, issues)."""
    # Claims must map word for word onto the submission; otherwise a separate audit judges the
    # original submission, not the extractor's confidence.
    input_spans = map_input(text, extraction)
    coverage_status, coverage_issues = ('incomplete' if extraction.omitted_claims else 'passed'), []
    if not extraction.omitted_claims and input_spans is None:
        coverage_status, coverage_issues = await extractor.audit_coverage(text, extraction)
    return input_spans, coverage_status, coverage_issues


async def run_pipeline(text, provider, fetch=fetch_text) -> Report:
    """Fact-check a typed statement."""
    extractor = ClaimExtractorAgent(provider)
    extraction = await extractor.extract_from_statement(text)
    input_spans, coverage_status, coverage_issues = await _check_extraction(extractor, text, extraction)
    steps = []
    if coverage_status == 'incomplete' and autonomous(provider, 'claim_extractor'):
        # The Claim Extractor sees what the check found and may revise once. A revision faces the same check.
        problems = coverage_issues or ['The extraction says checkable assertions were left out (omitted_claims). Every '
                                       'checkable assertion must be extracted, unless there are more than three.']
        revised = await extractor.review(text, extraction, problems)
        if revised is None:
            steps.append(f'{extractor.name}: Kept its extraction after the coverage check found a problem.')
        else:
            spans, status, issues = await _check_extraction(extractor, text, revised)
            if status == 'passed':
                extraction, input_spans, coverage_status, coverage_issues = revised, spans, status, issues
            steps.append(f'{extractor.name}: Revised its extraction after the coverage check found a problem; '
                         f'the revision {"passed" if status == "passed" else "did not pass"} the check.')
    if coverage_status != 'passed':
        reason = ('Extraction coverage could not be checked. No research was started.'
                  if coverage_status == 'unavailable' else
                  'Extraction did not cover the submission faithfully. No research was started; submit assertions separately.')
        return Report(id=str(uuid4()), mode='live', submitted_text=text, created_at=now(),
                      intent=extraction.intent, note=reason, claims=[unresolved(text, reason, 'coverage_failed')],
                      limitations=[reason] + coverage_issues, usage=provider.usage, agent_steps=steps,
                      omitted_claims=extraction.omitted_claims, coverage_status=coverage_status, input_spans=input_spans or [])
    results = await research_all(extraction.claims, provider, fetch) if extraction.intent == 'FACTUAL' else []
    limitations = ['At most three claims are checked per report, with up to eight pages read per claim.']
    return Report(id=str(uuid4()), mode='live', submitted_text=text, created_at=now(), intent=extraction.intent,
                  note=extraction.note, claims=results, limitations=limitations, usage=provider.usage, agent_steps=steps,
                  omitted_claims=extraction.omitted_claims, coverage_status=coverage_status, input_spans=input_spans or [])
