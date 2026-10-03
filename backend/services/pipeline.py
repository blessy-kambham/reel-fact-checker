"""Public entry points for the fact-checking pipeline.

The implementation lives in `agents/`: an orchestrator directs the Claim Extractor, Research, Analyst,
Citation Verifier and Verdict agents. This module keeps the original import path.
"""
from agents.orchestrator import (exact_submission_preserved, research_all, research_claim, run_pipeline,  # noqa: F401
                                 verify_citation, verify_selection)
from agents.research_agent import SEARCH_COPY_MIN_CHARS, search_copy  # noqa: F401
from agents.shared import (COMPLETE_WITHHELD_REASONS, COPY_MARKER_MIN_WORDS, INVALID_REFERENCE_CODES,  # noqa: F401
                           SINGLE_SOURCE_NOTE, WITHHELD_MESSAGES, loose, normalized, now, page_key, unresolved)
from agents.verdict_agent import check_decision  # noqa: F401
