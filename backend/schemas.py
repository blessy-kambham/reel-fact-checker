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
    text: Text
    context: str

class Extraction(StrictModel):
    intent: Literal['FACTUAL', 'OPINION', 'SATIRE', 'FICTIONAL', 'UNRELATED']
    claims: list[AtomicClaim] = Field(max_length=3)
    omitted_claims: bool
    note: str

class EvidenceDraft(StrictModel):
    source_id: str
    quote: str = Field(min_length=1, max_length=400)
    statement: str = Field(min_length=1, max_length=1000)
    stance: Literal['FOR', 'AGAINST', 'CONTEXT']

class Analysis(StrictModel):
    verdict: Verdict
    evidence: list[EvidenceDraft] = Field(max_length=6)
    limitations: list[str] = Field(max_length=6)

class CitationJudgment(StrictModel):
    supports_attribution: bool
    reason: str

class Source(StrictModel):
    id: str
    title: str
    url: str | None
    text: str
    retrieved_at: str
    source_type: str = 'Unrated; assess methodology and relevance'

class Citation(EvidenceDraft):
    title: str
    url: str | None
    verified: bool
    verification: str

class ClaimResult(StrictModel):
    claim: str
    verdict: Verdict
    status: Literal['complete', 'incomplete']
    evidence: list[Citation]
    limitations: list[str]
    supporting_search: str
    contradicting_search: str
    sources_checked: int

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
