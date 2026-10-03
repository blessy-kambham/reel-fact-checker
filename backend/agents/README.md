# Agents

The fact-checking pipeline is built from single-purpose agents. Each one has one job, its own instructions and a defined input and output, and sees only what it needs. An orchestrator runs them in a fixed order. Inside that order, the Research Agent is autonomous: it plans its own research with tools.

```text
video ──▶ Content Extractor ─┐
article ─────────────────────┼─▶ Claim Extractor ─▶ for each claim, two at a time:
statement ───────────────────┘
                                   Research Agent ─▶ Analyst Agent ─▶ Citation Verifier ─▶ Verdict Agent
                                                                                                │
                                                                              report ◀──────────┘
```

## The agents

| Agent | File | What it does | Where it is used |
| --- | --- | --- | --- |
| Orchestrator | [`orchestrator.py`](orchestrator.py) | Runs the agents in order, runs claims in parallel, and applies the rules for withholding a verdict. Plain code, not a model. | `main.py` (`/fact-check`), `services/article.py`, `services/video.py` |
| Content Extractor | [`content_extractor.py`](content_extractor.py) | Turns a video into text: ffmpeg extracts audio and keyframes, Whisper transcribes speech, the model reads on-screen text, and the caption is added. | `services/video.py`, `run_video_pipeline` |
| Claim Extractor | [`claim_extractor.py`](claim_extractor.py) | Classifies the content (factual, opinion, satire, fictional, unrelated) and extracts up to three claims copied word for word. A second pass audits that nothing was dropped or reworded. | `orchestrator.py`, `run_pipeline`; `services/article.py`, `select_and_research` |
| Research Agent | [`research_agent.py`](research_agent.py) | One per claim. Autonomous: each step it chooses a tool (`search_web` with a query it writes, `read_pages`, or `finish`), sees the result and decides the next step. Returns the pages it read as numbered sources. | `orchestrator.py`, `research_claim` (step 1) |
| Analyst Agent | [`analyst_agent.py`](analyst_agent.py) | Selects passages for and against the claim by ID, then classifies how each passage relates to that exact claim. It never writes a quote. | `orchestrator.py`, `research_claim` (step 2) and `verify_selection` |
| Citation Verifier | [`citation_verifier.py`](citation_verifier.py) | Checks that each quote appears in the page that was read and supports what it is cited for. | `orchestrator.py`, `verify_selection` (step 3) |
| Verdict Agent | [`verdict_agent.py`](verdict_agent.py) | Gives the verdict from verified evidence only and must cite evidence IDs. The application checks that the cited evidence justifies the verdict. | `orchestrator.py`, `research_claim` (step 4) |

The report itself is formatted in the frontend ([`frontend/src/Report.jsx`](../../frontend/src/Report.jsx)).

[`shared.py`](shared.py) holds helpers and messages the agents share.

## How the agents work together

- **Each agent is isolated.** The Verdict Agent, for example, receives only the claim and the verified evidence. It does not see the rest of the submission, the analyst's proposed verdict or the research notes, so a neighbouring claim cannot colour its answer.
- **The order of the agents is fixed in code.** The orchestrator decides which agent runs when, so no agent can skip citation verification or issue a verdict without evidence.
- **The Research Agent decides how to research.** It runs a tool-use loop: the model picks one tool per step, the application runs it and reports back, and the loop continues until the agent finishes. The steps it took are saved with the report.
- **Research runs in parallel.** One research branch per claim, at most two at a time, each with a time limit.
- **Failures are named.** If an agent fails or the evidence is not good enough, the claim's verdict is withheld with a specific reason rather than guessed.
- **Every model call is budgeted.** All agents call the model through `services/providers.py`, which reserves the cost of each call against the daily spending cap before it runs.

## The Research Agent's loop

```text
          ┌──────────────────────────────────────────────────────────────┐
          ▼                                                              │
  state: claim, searches so far, pages read, remaining budget, last result
          │                                                              │
          ▼                                                              │
  model chooses one tool ──▶ search_web(query, looking_for) ─┐           │
                         ──▶ read_pages(result_ids) ─────────┼─▶ application runs it
                         ──▶ finish(reason) ──▶ done         ┘
```

The agent is free to plan, but the application enforces the limits (`research_agent.py`):

- **Budget:** at most 5 searches, 8 pages and 10 steps per claim.
- **Both sides:** it may not finish before searching for contradicting as well as supporting evidence. A direction it never searches is searched for it afterwards.
- **Only search results can be read.** It chooses result IDs, never arbitrary addresses.
- **It gathers, it does not judge.** The analyst, citation verifier and verdict agents assess whatever it finds.
- **Fallback:** if planning fails, or `RESEARCH_AGENT_MODE=fixed` is set, it follows a fixed plan of one supporting and one contradicting search.

## Following one claim through the code

Start at `research_claim` in [`orchestrator.py`](orchestrator.py). Its four numbered steps call the Research Agent, the Analyst Agent, the Citation Verifier and the Verdict Agent in turn, and the rest of the function assembles the result.
