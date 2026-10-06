"""Shared API and model-output contracts."""
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=5000)]
Verdict = Literal['TRUE', 'FALSE', 'PARTIALLY TRUE', 'MISLEADING', 'UNVERIFIABLE', 'OUTDATED', 'SATIRE']
# Why the application replaced a verdict with UNVERIFIABLE. None means the verdict stage's answer was issued.
WithheldReason = Literal['coverage_failed', 'no_sources', 'search_failed', 'citation_failed', 'citation_unchecked', 'citation_disputed',
                         'no_relevant_evidence', 'verdict_check_unavailable', 'unknown_evidence_ids', 'missing_evidence_ids',
                         'evidence_stance_mismatch', 'conflicting_evidence', 'claim_timeout', 'provider_failure',
                         'spending_limit']

SourceTier = Literal['official', 'established', 'unrated', 'user_generated']

class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')

class ClaimRequest(StrictModel):
    claim: Text

class AtomicClaim(StrictModel):
    text: Text = Field(description="A checkable assertion as stated, even if false. Preserve negation, dates, quantities, attribution and qualifiers; do not correct it.")
    context: str = Field(description="Context from the submission only, without adding background knowledge.")

class Extraction(StrictModel):
    intent: Literal['FACTUAL', 'OPINION', 'SATIRE', 'FICTIONAL', 'UNRELATED']
    claims: list[AtomicClaim] = Field(max_length=5)  # agents.shared.MAX_STATEMENT_CLAIMS
    omitted_claims: bool = Field(description="True if any checkable assertion was left out, including when the claim limit is exceeded. False assertions must be extracted too.")
    note: str

class ExtractionCoverage(StrictModel):
    complete: bool = Field(description='Every checkable assertion is represented without changed meaning; intent and exclusions are justified without deciding truth.')
    issues: list[str] = Field(max_length=6, description='Missing or changed assertions, lost qualifiers, invented context, or incorrect intent. Empty only when coverage is complete.')

class EvidenceDraft(StrictModel):
    source_id: str
    quote: str = Field(min_length=1, max_length=400)
    statement: str = Field(min_length=1, max_length=1000)
    stance: Literal['FOR', 'AGAINST', 'CONTEXT'] = Field(description='Relationship to the original submitted claim, never to the proposed verdict: FOR supports the claim; AGAINST contradicts it; CONTEXT is relevant background only.')

class EvidenceSelection(StrictModel):
    source_id: str
    excerpt_id: str
    statement: str = Field(min_length=1, max_length=1000)
    stance: Literal['FOR', 'AGAINST', 'CONTEXT'] = Field(description='Relationship to the original submitted claim, never to the proposed verdict: FOR supports the claim; AGAINST contradicts it; CONTEXT is relevant background only.')

class Analysis(StrictModel):
    verdict: Verdict
    evidence: list[EvidenceSelection] = Field(max_length=6)
    limitations: list[str] = Field(max_length=6)

class VerdictDecision(StrictModel):
    verdict: Verdict
    evidence_ids: list[str] = Field(max_length=12, description='IDs from the verified evidence that justify this verdict. Never invent IDs.')

# ---- Agent actions: one step chosen by an agent. `tool` names the tool; the other fields are its arguments.
# Free text has no length limit here; the application truncates whatever it stores or passes on. ----

class OrchestratorAction(StrictModel):
    """Which agent the orchestrator hands the claim to next."""
    tool: Literal['research_agent', 'analyst_agent', 'citation_verifier', 'verdict_agent', 'finish']
    instruction: str = Field(description='research_agent, second round only: what evidence is still missing. Empty otherwise.')
    looking_for: Literal['supporting', 'contradicting'] = Field(description='research_agent, second round only: the kind of evidence that is missing.')
    reason: str = Field(description='One sentence: why this agent next.')

class ExtractorAction(StrictModel):
    """The claim extractor's answer after seeing the problems a check found in its extraction."""
    tool: Literal['revise', 'keep']
    extraction: Extraction
    reason: str = Field(description='One sentence: why.')

class ResearchAction(StrictModel):
    """One step chosen by the research agent."""
    tool: Literal['search_web', 'read_pages', 'finish']
    query: str = Field(description='search_web only: the search query, written by you. Empty for other tools.')
    looking_for: Literal['supporting', 'contradicting'] = Field(description='search_web only: the kind of evidence this search is meant to find.')
    result_ids: list[str] = Field(max_length=3, description='read_pages only: IDs of unread search results to read (R1, R2, ...). Empty for other tools.')
    reason: str = Field(description='One sentence: why this step.')

class AnalystAction(StrictModel):
    """The analyst's answer after seeing which of its selections were set aside."""
    tool: Literal['select_evidence', 'finish']
    evidence: list[EvidenceSelection] = Field(max_length=3, description='select_evidence only: replacement excerpts not selected before. Empty for finish.')
    reason: str = Field(description='One sentence: why.')

class VerifierAction(StrictModel):
    """One step chosen by the citation verifier."""
    tool: Literal['accept', 'reject', 'read_full_page']
    supports_attribution: bool = Field(description='The quote supports the attributed statement in context, without distortion. False when not yet decided.')
    stance_matches: bool = Field(description='The assigned stance accurately relates the supported statement to the original claim. False when not yet decided.')
    opposite_stance: bool = Field(description='True only when the passage has the OPPOSITE relationship to the original claim from the assigned stance: assigned FOR but it contradicts the claim, or assigned AGAINST but it supports it. False otherwise and when not yet decided.')
    reason: str

class VerdictAction(StrictModel):
    """One step chosen by the verdict agent."""
    tool: Literal['issue_verdict', 'request_evidence']
    verdict: Verdict = Field(description='issue_verdict only. Use UNVERIFIABLE with request_evidence.')
    evidence_ids: list[str] = Field(max_length=12, description='issue_verdict only: IDs from the verified evidence that justify this verdict. Never invent IDs.')
    missing: str = Field(description='request_evidence only: the specific evidence that would settle the target assertion. Empty otherwise.')
    looking_for: Literal['supporting', 'contradicting'] = Field(description='request_evidence only: the kind of evidence that is missing.')

class ContentAction(StrictModel):
    """One step chosen by the content extractor after its first look at a video."""
    tool: Literal['read_more_frames', 'finish']
    reason: str = Field(description='One sentence: why.')

class EvidenceRelation(StrictModel):
    relation: Literal['SUPPORTS', 'CONTRADICTS', 'BACKGROUND', 'IRRELEVANT', 'UNCERTAIN']
    reason: str

class CitationJudgment(StrictModel):
    supports_attribution: bool = Field(description='The quote supports the attributed statement in the context of the full page, without distortion.')
    stance_matches: bool = Field(description='The assigned stance accurately relates the supported statement to the original claim; relevant background may be CONTEXT without proving the claim.')
    opposite_stance: bool = Field(description='True only when the passage has the OPPOSITE relationship to the original claim from the assigned stance: assigned FOR but it contradicts the claim, or assigned AGAINST but it supports it. False for background, irrelevant or unclear passages.')
    reason: str

class Source(StrictModel):
    id: str
    title: str
    url: str | None
    text: str
    retrieved_at: str
    # Where the page comes from, rated from its address by tools/credibility.py. Not a judgement of the page.
    source_type: str = 'Unrated website'
    source_tier: SourceTier = 'unrated'
    # 'search_copy': the page refused the app's fetcher, so the search provider's extracted text was used.
    retrieval: Literal['fetched', 'search_copy'] = 'fetched'

class Citation(EvidenceDraft):
    # Empty only when an invalid selection has no source text to quote.
    quote: str = Field(max_length=400)
    excerpt_id: str | None = None
    source_start: int | None = None
    source_end: int | None = None
    title: str
    url: str | None
    verified: bool
    verification: str
    # Stable per-claim ID (E1, E2...) assigned to verified evidence; the verdict stage cites these IDs.
    evidence_id: str | None = None
    proposed_stance: Literal['FOR', 'AGAINST', 'CONTEXT'] | None = None
    relation_reason: str | None = None
    verification_code: Literal['not_checked', 'verified', 'unknown_source', 'unknown_excerpt', 'empty_quote', 'quote_not_found', 'attribution_rejected', 'stance_opposed', 'check_unavailable', 'relation_unresolved'] = 'not_checked'
    retrieved_at: str | None = None
    retrieval: Literal['fetched', 'search_copy'] | None = None
    source_text_sha256: str | None = None
    # The kind of site the page is on (tools/credibility.py). None in reports saved before sources were rated.
    source_tier: SourceTier | None = None
    source_label: str | None = None

class ClaimResult(StrictModel):
    claim: str
    verdict: Verdict
    status: Literal['complete', 'incomplete']
    evidence: list[Citation]
    rejected_citations: list[Citation] = Field(default_factory=list)
    limitations: list[str]
    supporting_search: str
    contradicting_search: str
    sources_checked: int
    # What the agents did for this claim, step by step, in plain words. Empty when no agent planned its own steps.
    agent_steps: list[str] = Field(default_factory=list)

    @model_validator(mode='before')
    @classmethod
    def _earlier_field_name(cls, data):
        """Reports saved while this list was called `research_steps` still load."""
        if isinstance(data, dict) and 'research_steps' in data:
            data = dict(data)
            data.setdefault('agent_steps', [f'Research Agent: {step}' for step in data.pop('research_steps')])
        return data
    # Audit trail for the claim-specific verdict stage.
    verdict_state: Literal['issued', 'withheld'] = 'withheld'
    withheld_reason: WithheldReason | None = None
    withheld_message: str | None = None
    decision_verdict: Verdict | None = Field(default=None, description='Raw verdict returned by the verdict stage; None when it was not called.')
    decision_evidence_ids: list[str] = Field(default_factory=list, description='Raw evidence IDs returned by the verdict stage, including rejected ones.')
    verdict_evidence_ids: list[str] = Field(default_factory=list, description='Evidence IDs justifying an issued verdict; empty when withheld.')
    verdict_source_count: int | None = Field(default=None, description='Distinct pages behind an issued verdict; None when withheld.')
    verdict_site_count: int | None = Field(default=None, description='Different sites behind an issued verdict; None when withheld.')
    # How strong the sources behind an issued verdict are. Describes the sources, not the chance the verdict is right.
    evidence_strength: Literal['strong', 'moderate', 'weak'] | None = None
    source_score: int | None = Field(default=None, description='Average rating of the sites the verdict cites, 0-100.')

class InputSpan(StrictModel):
    start: int
    end: int
    text: str

class Report(StrictModel):
    id: str
    mode: Literal['live', 'demo']
    submitted_text: str
    created_at: str
    intent: str
    note: str
    claims: list[ClaimResult]
    limitations: list[str]
    usage: dict[str, int]
    omitted_claims: bool = False
    input_spans: list[InputSpan] = Field(default_factory=list)
    coverage_status: Literal["not_checked", "passed", "incomplete", "unavailable"] = "not_checked"
    input_type: Literal['text', 'article', 'video'] = 'text'
    source_url: str | None = None
    source_sha256: str | None = Field(default=None, description='Hash of the fetched article text or uploaded video the claims came from.')
    source_text: str | None = Field(default=None, description='For videos: the transcript, on-screen text and caption the claims were copied from.')
    media: 'MediaSummary | None' = None
    # What the content and claim extractor agents did beyond their first pass, in plain words.
    agent_steps: list[str] = Field(default_factory=list)

class MediaSummary(StrictModel):
    duration_seconds: float
    had_audio: bool
    transcript_language: str | None = None
    frames_read: int
    transcript_chars: int
    screen_text_chars: int
    caption_chars: int

class ScreenText(StrictModel):
    text: str = Field(max_length=4000, description='All text visible in the frames, copied exactly in reading order, '
                      'with each distinct caption or overlay once. Empty when no text is visible.')

class ArticleRequest(StrictModel):
    url: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]

Report.model_rebuild()
