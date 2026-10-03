"""The agents that make up the fact-checking pipeline. See README.md in this folder.

    orchestrator         runs the agents in a fixed order (plain code, not a model)
    content_extractor    video -> transcript, on-screen text and caption
    claim_extractor      intent classification and word-for-word claims
    research_agent       autonomous tool-use loop: plans searches and reads pages, one per claim
    analyst_agent        evidence for and against, and how each passage relates to the claim
    citation_verifier    confirms each quote exists and says what it is cited for
    verdict_agent        verdict from verified evidence only
"""
