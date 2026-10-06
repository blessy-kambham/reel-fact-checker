"""Orchestrator agent.

Directs the other agents for one fact-check and owns the shared state. For each claim the model
chooses which agent works next: its tools are the other agents (`research_agent`, `analyst_agent`,
`citation_verifier`, `verdict_agent`) and `finish`. After each hand-off it sees what that agent
produced and decides again, so it can send a claim back for a second research round when the
verified evidence is thin or one-sided, or when the verdict agent asks for more.

    Claim Extractor ─▶ for each claim, three at a time:
                         Research Agent ⇄ Analyst Agent ─▶ Citation Verifier ─▶ Verdict Agent
                                  ▲                                                 │
                                  └──────────── second round, if needed ◀───────────┘

The orchestrator decides the route; the application enforces the rules. No hand-off can skip a stage:
pages must be analysed, passages must be verified, and a verdict is refused while a search has
failed, a citation check could not be run, or the citation verifier places a passage on the other
side of the claim from the analyst. A passage that simply fails its check is left out, and the
verdict rests on the passages that passed. Any stage the orchestrator leaves out is run in the
standard order afterwards, which is also what happens in fixed mode (`AGENT_MODE`) or when the
orchestrator cannot plan.

A verdict should rest on three different sites. When the verified evidence comes from fewer, the
claim gets its second research round to look for other sites before the verdict agent is asked. This
is a goal, not a condition: if the round finds nothing, the verdict is issued and says how many sites
it rests on. A second round can only add. It works inside the time the claim has left, and whatever it
does not finish is left out, so it cannot cost a claim the evidence it already had.

Used by: `main.py` (statements), `services/article.py` (articles) and `services/video.py` (videos).
"""
import asyncio
import hashlib
import time
from uuid import uuid4

from schemas import Citation, ClaimResult, OrchestratorAction, Report
from services.budget import BudgetExceeded
from tools.fetcher import fetch_text
from services.input_mapping import map_input
from services.summary import confidence, overall
from tools import credibility
from tools.providers import ProviderFailure

from agents.analyst_agent import AnalystAgent
from agents.citation_verifier import CitationVerifierAgent
from agents.claim_extractor import ClaimExtractorAgent
from agents.research_agent import ResearchAgent
from agents.runtime import Done, PlanningUnavailable, autonomous, run_tools
from agents.shared import (COMPLETE_WITHHELD_REASONS, COPY_MARKER_MIN_WORDS, FEW_SITES_NOTE, INVALID_REFERENCE_CODES,
                           MAX_STATEMENT_CLAIMS, SINGLE_SOURCE_NOTE, SITE_GOAL, WITHHELD_MESSAGES, loose, now, unresolved)
from agents.verdict_agent import VerdictAgent

NAME = 'Orchestrator'
MAX_PARALLEL_CLAIMS = 3    # five claims then finish within two time limits, inside the report's own limit
CLAIM_TIMEOUT_SECONDS = 150
MAX_STEPS = 9              # hand-offs the orchestrator may make for one claim, including refused ones
MAX_RESEARCH_ROUNDS = 2    # the first round plus one follow-up
MIN_ROUND_SECONDS = 85     # a follow-up round is refused when less of the claim's time limit is left
FIRST_SELECTIONS = 6       # passages the analyst may select from the first round's pages
FOLLOW_UP_SELECTIONS = 3   # and from a follow-up round's pages
VERDICT_RESERVE_SECONDS = 25   # of the claim's time limit, kept back from a follow-up round for the verdict
ROUND_OUT_OF_TIME = 'The second research round ran out of time, so what it had not finished was left out.'

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
    'claim, when a search failed, when the verdict agent asked for more evidence, or when "sites_with_direct_evidence" '
    f'is below {SITE_GOAL}: write the specific evidence that is missing in "instruction", or ask for pages on other '
    'sites. New pages must go to analyst_agent and citation_verifier before '
    f'verdict_agent. Do not ask for more research when verified evidence from {SITE_GOAL} or more sites already addresses '
    'the claim directly, or when "remaining" shows no research round is left. A passage that failed its citation check '
    'is simply left out. Choose finish only when a tool was refused and nothing else can help.')
VERDICT_REFUSALS = {
    'search_failed': 'a search failed, so the research is one-sided. Send the claim back to research_agent for the missing side, or finish.',
    'citation_unchecked': 'a citation check could not be run, so no verdict may be issued for this claim. Finish.',
    'citation_disputed': 'the citation verifier places a passage on the other side of the claim from the analyst, so no verdict may be issued for this claim. Finish.',
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


WEAK_SOURCES_NOTE = ('None of the pages behind this verdict is from an official, academic, reference or established '
                     'publisher. Check the sources before relying on it.')


def _rated(citation: Citation) -> Citation:
    """The citation with the kind of site its page is on."""
    if not citation.url:
        return citation
    rating = credibility.rate(citation.url)
    return citation.model_copy(update={'source_tier': rating.tier, 'source_label': rating.label, 'source_weight': rating.weight})


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
        self.extra_from = None         # where a second round's passages start, when the claim could already be judged
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
        """Citations that failed a check and are left out of the evidence. A proposal pointing at material
        that was never supplied, or at a passage the relation check found irrelevant, is the analyst's slip
        and is not counted here."""
        return [c for c in self.citations if not c.verified and c.verification_code not in INVALID_REFERENCE_CODES]

    @property
    def unchecked(self) -> list:
        """Citations whose check could not be run at all. Unlike a passage that was checked and rejected,
        nothing is known about these, so a verdict would rest on partly checked evidence.

        One exception. A second round for a claim that could already be judged can only add, so a passage it
        could not check is left out, unless that passage was selected as evidence for the side the verified
        evidence does not take: leaving that one out could hide evidence against the verdict."""
        sides = {c.stance for c in self.citations if c.verified and c.stance in ('FOR', 'AGAINST')}

        def left_out(index, passage):
            return (self.extra_from is not None and index >= self.extra_from
                    and (passage.stance == 'CONTEXT' or passage.stance in sides))
        return [p for i, p in enumerate(self.passages) if isinstance(p, Citation) and not p.verified
                and p.verification_code == 'check_unavailable' and not left_out(i, p)]

    @property
    def disputed(self) -> list:
        """Passages the citation verifier places on the other side of the claim from the analyst. Leaving
        such a passage out could hide evidence against the verdict, so it rules a verdict out instead."""
        return [c for c in self.citations if c.verification_code == 'stance_opposed']

    @property
    def sites(self) -> set:
        """The different sites behind the verified evidence that bears directly on the claim."""
        return {credibility.domain(c.url) for c in self.citations
                if c.verified and c.stance in ('FOR', 'AGAINST') and credibility.domain(c.url)}

    def blocked(self):
        """The WithheldReason that rules out a verdict before the verdict agent is asked, or None."""
        if self.pack.search_failed:
            return 'search_failed'
        if self.unchecked:
            return 'citation_unchecked'
        if self.disputed:
            return 'citation_disputed'
        if not any(c.stance in ('FOR', 'AGAINST') for c in self.citations if c.verified):
            return 'no_relevant_evidence'
        return None

    def wants_more_sites(self) -> bool:
        """Whether a verdict could be asked for but rests on too few sites, and another round can still look."""
        return (self.pack is not None and not self.unread and not self.pending and self.blocked() is None
                and len(self.sites) < SITE_GOAL and self.follow_up_refusal() is None)

    def site_request(self) -> tuple:
        """What to ask the research agent for when more sites are wanted: (request, kind of evidence)."""
        direct = [c.stance for c in self.citations if c.verified and c.stance in ('FOR', 'AGAINST')]
        looking_for = 'contradicting' if direct.count('AGAINST') > direct.count('FOR') else 'supporting'
        return 'Pages on other sites that directly address the claim, to confirm the evidence independently.', looking_for

    def round_time(self) -> float:
        """Seconds one hand-off of a second round may still take, leaving time for the verdict."""
        return max(0.0, self.seconds_left() - VERDICT_RESERVE_SECONDS)

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
        self.rounds += 1
        if self.pack is None:
            self.pack = await self.researcher.gather(self.claim, self.exclude, self.copy_markers)
            new = len(self.pack.sources)
        else:
            judgeable = self.blocked() is None
            if judgeable:
                self.extra_from = len(self.passages)   # the claim can already be judged: this round can only add
            # A claim that can be judged but rests on too few sites does not need more pages from those sites.
            # A round that repairs a gap may use any site.
            avoid = self.sites if judgeable and len(self.sites) < SITE_GOAL else frozenset()
            before, self.evidence_request = len(self.pack.sources), None
            try:
                await asyncio.wait_for(self.researcher.follow_up(request, looking_for, avoid), self.round_time())
            except asyncio.TimeoutError:
                self.pack.warnings.append(ROUND_OUT_OF_TIME)
            new = len(self.pack.sources) - before
        self.log(ResearchAgent.name, self.pack.steps[logged:])
        return new

    async def analyse(self):
        """Analyst Agent: select passages from the pages not yet analysed and relate each to the claim.
        Returns (passages ready for verification, passages set aside)."""
        sources, first, notes = self.unread, not self.analysed, []
        self.analysed.update(sources)
        work = AnalystAgent(self.provider).build_case(
            self.claim, sources, self.pack.sources, FIRST_SELECTIONS if first else FOLLOW_UP_SELECTIONS, notes, review=first)
        try:
            entries = await (work if first else asyncio.wait_for(work, self.round_time()))
        except BudgetExceeded:
            raise
        except (ProviderFailure, asyncio.TimeoutError):
            if first:
                raise
            # A second round is extra: when its pages cannot be analysed, the first round's evidence stands.
            self.pack.warnings.append('Pages from the second research round could not be analysed, so they were not used.')
            return 0, 0
        self.passages += [_rated(e) if isinstance(e, Citation) else e for e in entries]
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
            check = verifier.verify(draft, self.pack.sources, self.claim.text, notes, metadata['source_start'])
            try:
                citation = await (check if self.rounds < 2 else asyncio.wait_for(check, self.round_time()))
            except asyncio.TimeoutError:
                # Second round only: every passage still waiting is recorded as not checked, never used unchecked.
                self.passages = [p if isinstance(p, Citation) else self._not_checked(*p) for p in self.passages]
                self.pack.warnings.append(ROUND_OUT_OF_TIME)
                break
            self.passages[index] = _rated(citation.model_copy(update=metadata))
            verified, checked = verified + citation.verified, checked + 1
        self.log(CitationVerifierAgent.name, notes)
        return verified, checked

    def _not_checked(self, draft, metadata) -> Citation:
        """A selected passage whose citation check did not finish in the time a second round had."""
        source = self.pack.sources[draft.source_id]
        return _rated(Citation(**draft.model_dump(), title=source.title, url=source.url, verified=False,
                               verification='The citation check ran out of time. This citation was not verified.',
                               verification_code='check_unavailable', retrieved_at=source.retrieved_at,
                               retrieval=source.retrieval, **metadata))

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

    async def complete(self, widen: bool = False) -> None:
        """Run every stage that has not run, in the standard order. In fixed mode this is the whole pipeline.
        With `widen`, a claim resting on too few sites first gets the second research round it was not given."""
        if self.pack is None:
            await self.research()                 # 1. Research Agent
        if self.unread:
            await self.analyse()                  # 2. Analyst Agent
        if self.pending:
            await self.verify()                   # 3. Citation Verifier
        if widen and self.outcome is None and self.wants_more_sites():
            request, looking_for = self.site_request()
            self.steps.append(f'{NAME}: The verified evidence came from {len(self.sites)} site(s), so the claim went '
                              'back to the Research Agent to look for other sites.')
            await self.research(request, looking_for)
            if self.unread:
                await self.analyse()
            if self.pending:
                await self.verify()
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
        rejected = sum(c.verification_code != 'check_unavailable' for c in failed)
        if rejected:
            warnings.append(f'{rejected} selected passage(s) failed the citation check and were left out. '
                            'Any verdict rests only on the passages that passed.')
        if len(failed) > rejected:
            warnings.append(f'{len(failed) - rejected} selected passage(s) could not be checked and were left out.')

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
        verdict_sources, verdict_sites, strength, score, level, reasons = None, None, None, None, None, []
        if withheld is None:
            cited = [c for c in usable if c.evidence_id in decision_ids]
            verdict_sources = len({c.url or c.source_id for c in cited})
            # Sites are counted over the cited passages that bear directly on the claim, not background.
            verdict_sites = len({credibility.domain(c.url) or c.source_id for c in cited if c.stance in ('FOR', 'AGAINST')})
            if verdict != 'UNVERIFIABLE':
                # Source credibility: how strong the sites behind the verdict are. It never changes the verdict.
                strength, score = credibility.assess(c.url for c in cited)
                if verdict_sources == 1:
                    warnings.append(SINGLE_SOURCE_NOTE)
                elif verdict_sites < SITE_GOAL:
                    warnings.append(FEW_SITES_NOTE)
                # Confidence looks at the same passages the site count does: the cited ones that bear on the claim.
                direct_strength = credibility.assess(c.url for c in cited if c.stance in ('FOR', 'AGAINST'))[0]
                level, reasons = confidence(verdict_sites, direct_strength, {'FOR', 'AGAINST'} <= {c.stance for c in usable},
                                            rejected, len(usable) + rejected)
                if strength == 'weak':
                    warnings.append(WEAK_SOURCES_NOTE)
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
                           verdict_evidence_ids=[] if withheld else decision_ids, verdict_source_count=verdict_sources,
                           verdict_site_count=verdict_sites, evidence_strength=strength, source_score=score,
                           confidence=level, confidence_reasons=reasons)


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
        if case.wants_more_sites():
            return (f'verdict_agent refused: the verified evidence comes from {len(case.sites)} site(s) and a verdict '
                    f'should rest on {SITE_GOAL}. Send the claim back to research_agent for pages on other sites; '
                    'results on the sites already used are left out of that round.')
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
                'sites_with_direct_evidence': len(case.sites),
                'passages_set_aside': len(case.citations) - len(verified) - len(case.failed),
                'citations_failed_and_left_out': len(case.failed),
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
    directed = autonomous(provider, 'orchestrator')
    if directed:
        await _direct(case)
    # Fixed mode is one pass. With the orchestrator on, a second round for more sites is guaranteed here.
    await case.complete(widen=directed)
    return case.result()


def claim_key(claim, copy_markers=()) -> str:
    """A fingerprint of a claim as it was researched: its words, its context, and whether copies of the
    checked material were being kept out of the evidence. Two claims with the same key are the same
    question asked under the same rules."""
    guarded = any(len(marker.split()) >= COPY_MARKER_MIN_WORDS for marker in copy_markers)
    parts = [loose(claim.text), loose(claim.context), 'copies excluded' if guarded else '']
    return hashlib.sha256('\n'.join(parts).encode('utf-8')).hexdigest()[:32]


def reused(earlier: ClaimResult, report_id: str, checked_at: str, claim_text: str | None = None) -> ClaimResult:
    """An earlier result for the same claim, marked as shown again rather than researched again. It carries
    the claim as it was worded this time, which may differ from the earlier wording in case or spacing."""
    day = checked_at[:10]
    note = f'This claim was checked on {day}. That result is shown again; no new research was done.'
    return earlier.model_copy(update={
        'claim': claim_text or earlier.claim, 'reused_from': report_id, 'first_checked_at': checked_at, 'limitations': [note] + earlier.limitations,
        'agent_steps': [f'{NAME}: Recognised a claim already checked on {day} and reused its result.'] + earlier.agent_steps})


async def research_all(claims, provider, fetch=fetch_text, exclude=frozenset(), copy_markers=None):
    """Run one research branch per claim, a few at a time; every failure becomes a named withheld verdict.

    When the provider carries a `recall` function (the live app's history), a claim that was already
    checked recently is not researched again: its earlier result is shown, marked as reused. This does
    not apply when pages are excluded for this submission (an article's own page), because an earlier
    result may rest on exactly those pages.
    """
    limiter = asyncio.Semaphore(MAX_PARALLEL_CLAIMS)
    recall = None if exclude else getattr(provider, 'recall', None)
    async def branch(claim):
        markers = (copy_markers or {}).get(claim.text, ())
        key = None if exclude else claim_key(claim, markers)
        earlier = recall(key) if recall else None
        if earlier is not None:
            return reused(*earlier, claim.text)
        async with limiter:
            try:
                result = await asyncio.wait_for(research_claim(claim, provider, fetch, exclude, markers), timeout=CLAIM_TIMEOUT_SECONDS)
                return result.model_copy(update={'claim_key': key})
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
                                       f'checkable assertion must be extracted, unless there are more than {MAX_STATEMENT_CLAIMS}.']
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
    limitations = [f'At most {MAX_STATEMENT_CLAIMS} claims are checked per report, with up to eight pages read per claim.']
    return Report(id=str(uuid4()), mode='live', submitted_text=text, created_at=now(), intent=extraction.intent,
                  note=extraction.note, claims=results, limitations=limitations, usage=provider.usage, agent_steps=steps,
                  **overall(results),
                  omitted_claims=extraction.omitted_claims, coverage_status=coverage_status, input_spans=input_spans or [])
