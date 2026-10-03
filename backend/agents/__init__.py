"""The agents that make up the fact-checking system. See README.md in this folder.

Each agent is a model with its own tools: every step it chooses a tool, the application runs it, and
the agent sees the result before choosing again (`runtime.py`).

    orchestrator         chooses which agent works on a claim next; can send it back for more research
    content_extractor    video -> transcript, on-screen text and caption; decides if frames need a closer look
    claim_extractor      intent and word-for-word claims; revises when the application's check fails
    research_agent       plans its own searches and reads pages, one per claim
    analyst_agent        evidence for and against; replaces selections that were set aside
    citation_verifier    confirms each quote says what it is cited for; reads the full page when unsure
    verdict_agent        verdict from verified evidence only; may ask for more evidence instead
    runtime              the tool loop all of them share
"""
