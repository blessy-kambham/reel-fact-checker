"""Shared API and model-output contracts."""
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=5000)]
Verdict = Literal['TRUE', 'FALSE', 'PARTIALLY TRUE', 'MISLEADING', 'UNVERIFIABLE', 'OUTDATED', 'SATIRE']

class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')

class ClaimRequest(StrictModel):
    claim: Text

class AtomicClaim(StrictModel):
    text: Text = Field(description="A checkable assertion as stated, even if false. Preserve negation, dates, quantities, attribution and qualifiers; do not correct it.")
    context: str = Field(description="Context from the submission only, without adding background knowledge.")

class Extraction(StrictModel):
    intent: Literal['FACTUAL', 'OPINION', 'SATIRE', 'FICTIONAL', 'UNRELATED']
    claims: list[AtomicClaim] = Field(max_length=3)
    omitted_claims: bool = Field(description="True if any checkable assertion was left out, including when the three-claim limit is exceeded. False assertions must be extracted too.")
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

class CitationJudgment(StrictModel):
    supports_attribution: bool = Field(description='The quote supports the attributed statement in the context of the full page, without distortion.')
    stance_matches: bool = Field(description='The assigned stance accurately relates the supported statement to the original claim; relevant background may be CONTEXT without proving the claim.')
    reason: str

class Source(StrictModel):
    id: str
    title: str
    url: str | None
    text: str
    retrieved_at: str
    source_type: str = 'Unrated; assess methodology and relevance'

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
    verification_code: Literal['not_checked', 'verified', 'unknown_source', 'unknown_excerpt', 'empty_quote', 'quote_not_found', 'attribution_rejected', 'check_unavailable'] = 'not_checked'
    retrieved_at: str | None = None
    source_text_sha256: str | None = None

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
