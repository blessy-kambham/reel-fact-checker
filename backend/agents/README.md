# Agents

The fact-checker is a team of agents. Each agent is a model with a goal, its own tools and a step budget. On every step the agent is shown the state of its work and chooses one tool; the application runs the tool and reports back; the agent decides again. An orchestrator agent chooses which agent works on a claim next.

```text
video ──▶ Content Extractor ─┐
article ─────────────────────┼─▶ Claim Extractor ─▶ for each claim, three at a time:
statement ───────────────────┘
                                   Orchestrator chooses the next agent
                                        │
                                        ▼
                                   Research Agent ─▶ Analyst Agent ─▶ Citation Verifier ─▶ Verdict Agent ─▶ report
                                        ▲                                                     │
                                        └────────── second round, when evidence is thin ◀─────┘
```

## The agents

| Agent | File | Its job | The tools it chooses between |
| --- | --- | --- | --- |
| Orchestrator | [`orchestrator.py`](orchestrator.py) | Directs the work on one claim and owns the shared state. | The other agents: `research_agent`, `analyst_agent`, `citation_verifier`, `verdict_agent`, and `finish`. It can send a claim back for a second research round. |
| Content Extractor | [`content_extractor.py`](content_extractor.py) | Turns a video into text: speech (Whisper), on-screen text (keyframes) and the caption. | After its first pass: `read_more_frames` for a closer look at on-screen text, or `finish`. |
| Claim Extractor | [`claim_extractor.py`](claim_extractor.py) | Classifies the content and extracts its claims copied word for word: up to five from a statement, three from an article or video. | When the application's check of its extraction fails: `revise` or `keep`. |
| Research Agent | [`research_agent.py`](research_agent.py) | Gathers the pages the claim will be judged on. One per claim. | `search_web` with a query it writes, `read_pages`, `finish`. |
| Analyst Agent | [`analyst_agent.py`](analyst_agent.py) | Selects passages for and against the claim by ID, and relates each one to that exact claim. | When the relation check sets its selections aside: `select_evidence` to pick replacements, or `finish`. |
| Citation Verifier | [`citation_verifier.py`](citation_verifier.py) | Confirms each quote is in the page and says what it is cited for. | `accept`, `reject`, or `read_full_page` when the passage around the quote is not enough. |
| Verdict Agent | [`verdict_agent.py`](verdict_agent.py) | Gives the verdict from verified evidence only, citing evidence IDs, with a short explanation in which every sentence carries the evidence it rests on. | `issue_verdict`, or `request_evidence` to say what is missing. |

[`runtime.py`](runtime.py) is the tool loop all of them share. [`shared.py`](shared.py) holds helpers and messages. What the tools actually do (the model, web search, page fetching, video and audio handling) lives in [`backend/tools/`](../tools). The report itself is formatted in the frontend ([`frontend/src/Report.jsx`](../../frontend/src/Report.jsx)), which also lists the steps the agents chose.

## The loop every agent runs

```text
          ┌───────────────────────────────────────────────┐
          ▼                                               │
  state: the goal, the work so far, what is left, the result of the last step
          │                                               │
          ▼                                               │
  model chooses one tool ──▶ application runs the tool ───┘
                         ──▶ a tool that ends the loop ──▶ done
```

The model only ever chooses. The application runs the tool, so the limits below hold whatever a model answers. That split is in [`runtime.py`](runtime.py): `run_tools` takes the action schema the model must return, a function that builds the state, the tools, and a step limit.

## How the agents work together

One claim, as the orchestrator usually directs it:

1. **Orchestrator → Research Agent.** The research agent searches for supporting and contradicting evidence with its own queries and reads the results it judges most useful.
2. **Orchestrator → Analyst Agent.** The analyst selects passages by ID and a separate relation check decides whether each one supports, contradicts or is only background for this exact claim.
3. **Orchestrator → Citation Verifier.** Every selected passage is checked. Only verified passages count as evidence.
4. **Orchestrator → Verdict Agent.** The verdict agent sees only the claim and the verified evidence.

The route can change. If no verified passage bears directly on the claim, if a search failed, if the verified evidence comes from fewer than three different sites, or if the verdict agent asks for more evidence, the orchestrator can send the claim back to the research agent with a note on what is missing. The new pages go through the analyst and the citation verifier before the verdict agent is asked again. A second round can only add: if it fails, the evidence from the first round stands.

The steps the agents chose are saved with each report (`agent_steps`) and shown under each claim.

## What the application enforces

The agents are free to plan. These rules are code, and no agent can choose its way around them:

- **No stage is skipped.** Pages must be analysed and passages verified before a verdict. Whatever the orchestrator chooses, any stage left out is run afterwards in the standard order.
- **Both sides are researched.** The research agent may not finish before searching for contradicting as well as supporting evidence; a direction it skips is searched for it.
- **Social media is not evidence.** Every page is rated by where it comes from ([`tools/credibility.py`](../tools/credibility.py)). The research agent sees each result's source type, and pages on social media or other user-generated platforms are refused whatever it chooses.
- **Quotes are copied, not written.** Agents pick excerpt IDs; the application copies the text from the page. Only pages a search returned can be read.
- **A verdict needs verified evidence.** A passage that fails its citation check is left out and listed as rejected, and the verdict rests on the passages that passed. The verdict is withheld, with a named reason, if a search failed, if a citation check could not be run, if the citation verifier places a passage on the other side of the claim from the analyst, if no verified passage bears on the claim, or if the cited evidence does not justify it. A verdict agent that cited evidence IDs that do not exist may cite again, but it may not change its verdict to get past the check.
- **An explanation cannot cost a verdict, or stand without evidence.** It is shown only when every sentence rests on verified evidence the verdict cites. Otherwise it is left out, with a note, and the verdict is unaffected.
- **A reported belief is not support.** The analyst's relation check also says whose voice a passage is in. A passage the page only reports as a belief, a myth or an earlier view is treated as background even if the check filed it as support.
- **Three sites is the goal.** A claim that could be given a verdict on evidence from fewer than three different sites first gets its second research round, which leaves out pages on the sites already used. The orchestrator is told to send it back; if it does not, the application does. This is a goal and not a condition: when the round finds nothing, the verdict is issued and the report says how many sites it rests on. Sites are counted by registered domain ([`tools/credibility.py`](../tools/credibility.py)). Because the claim has one second round, the verdict agent's own request for more evidence is only offered when the evidence already comes from three sites.
- **A second round can only add.** It works inside the time the claim has left, keeping some back for the verdict, and whatever it does not finish is left out. A passage it could not check does not cost a verdict the first round had earned, unless that passage was selected as evidence for the other side.
- **Every passage is read in full context at least once.** The citation verifier may decide from the text around a quote, but the analyst's relation check always reads the whole page, and a passage counts only if both agree.
- **A revision faces the same check.** The claim extractor's second attempt is checked exactly like its first, and cannot pass by dropping a claim.
- **Each agent sees only what it needs.** The orchestrator sees counts, never page text. The verdict agent never sees the rest of the submission, the analyst's proposed verdict or the research notes.
- **A claim checked before is not checked again.** When the app's history holds a real verdict for the same claim and context from the last seven days, the orchestrator shows that result, marked as reused, instead of starting the agents (`REUSE_DAYS`; not used for articles, whose own page must be kept out of the evidence).
- **Budgets.** Per claim: at most 5 searches, 8 pages, 2 research rounds (the second adds at most 3 pages) and 150 seconds; every loop has a step limit. Every model call is reserved against the daily spending cap before it runs (`tools/providers.py`).
- **Fallback.** If an agent cannot plan a step, it does its standard single pass. `AGENT_MODE=fixed` turns planning off for all agents and is a single pass with no second round, and a comma-separated list (for example `AGENT_MODE=research,verdict`) turns it on for some.

## Following one claim through the code

Start at `research_claim` in [`orchestrator.py`](orchestrator.py). `_direct` is the orchestrator's loop; each of its tools hands the claim to one agent through `_Case`, which holds what the agents have produced so far. `_Case.complete` runs any stage that has not run, and `_Case.result` issues or withholds the verdict.

The tests in [`backend/tests/test_agents.py`](../tests/test_agents.py) and [`test_research_agent.py`](../tests/test_research_agent.py) script each agent's choices and check both what it can do and what it cannot.
