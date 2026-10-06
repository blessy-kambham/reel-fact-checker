# Reel Fact-Checker

Paste a claim, an article link or a short video, and get a verdict for each claim with the evidence behind it. Every quote in a report is copied from a page the app actually read, and when the evidence is not good enough the app says so instead of guessing.

I built this as a portfolio project to explore a question I care about: how do you make an LLM pipeline that is honest about what it can and cannot support? A private, password-protected demo runs on Railway.

## What it does

- **Three kinds of input.** A typed statement, a public article URL, or a video up to 3 minutes long: an uploaded file (MP4, MOV or WebM) or, where the deployment allows it, a link to a public video on Instagram, TikTok or YouTube.
- **Video understanding.** Speech is transcribed locally with Whisper, on-screen text is read from keyframes, and an optional caption is included, so a silent Reel with text overlays works too.
- **Claim-by-claim reports.** Up to five claims from a typed submission, or the three central claims of an article or video, each with a verdict (`TRUE`, `FALSE`, `PARTIALLY TRUE`, `MISLEADING`, `OUTDATED` or `UNVERIFIABLE`), the passages the verdict rests on, and links to their sources.
- **Source credibility.** Every page is rated by where it comes from, in the design brief's categories and weights (government data 0.95, peer-reviewed 0.90, fact-checkers 0.88, down to social media 0.10). Social media and blogs are never used as evidence, and each verdict shows how strong its sources are and how many different sites it rests on.
- **A short explanation and an evidence bar.** Each verdict comes with two to four plain sentences saying why, every one tied to the evidence it rests on, and a bar showing how much verified evidence is on each side.
- **Confidence and an overall verdict.** Each verdict carries a confidence level (high, medium or low) with the reasons for it, and a submission with several claims gets one overall verdict. Both are counting rules, not a model's opinion.
- **Repeated claims are not researched twice.** A claim already checked in the last seven days is shown again from the saved result, marked as reused.
- **Verdicts can be withheld.** If sources cannot be read, a check could not be run, or the evidence conflicts, the report gives the reason instead of a verdict.
- **Saved reports.** Live reports are stored in SQLite and can be reopened, printed, saved as PDF or downloaded as JSON.
- **A free demo mode.** A fictional example report that needs no API keys and makes no external calls.

## How it works

The work is done by a team of agents. Each agent is a model with its own tools: on every step it chooses a tool, the application runs it, and the agent sees the result before choosing again. An orchestrator agent decides which agent works on a claim next.

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

| Agent | Job | Tools it chooses between |
| --- | --- | --- |
| [Orchestrator](backend/agents/orchestrator.py) | Directs the work on each claim and can send it back for more research. | The other agents, and `finish` |
| [Content Extractor](backend/agents/content_extractor.py) | Turns a video into text: speech (Whisper), on-screen text (keyframes) and caption. | `read_more_frames`, `finish` |
| [Claim Extractor](backend/agents/claim_extractor.py) | Classifies the content and extracts its claims, copied word for word: up to five from a statement, three from an article or video. | `revise`, `keep` |
| [Research Agent](backend/agents/research_agent.py) | One per claim. Writes its own search queries and chooses which results to read. | `search_web`, `read_pages`, `finish` |
| [Analyst Agent](backend/agents/analyst_agent.py) | Selects passages for and against the claim by ID and relates each one to it. | `select_evidence`, `finish` |
| [Citation Verifier](backend/agents/citation_verifier.py) | Confirms each quote exists in the page and says what it is cited for. | `accept`, `reject`, `read_full_page` |
| [Verdict Agent](backend/agents/verdict_agent.py) | Gives the verdict from verified evidence only, citing evidence IDs. | `issue_verdict`, `request_evidence` |

The agents decide how to do the work; the application enforces the rules they work inside. No route the orchestrator picks can skip a stage, a verdict needs verified evidence, and every loop has a budget. Each report lists the steps the agents chose. [`backend/agents/README.md`](backend/agents/README.md) explains the loop, how the agents hand work to each other, and what the application enforces.

## Design decisions

These are the choices that shaped the project, most of them made after a live test showed a failure.

- **The model never writes a quote.** Early versions asked the model to quote its sources, and it altered punctuation, joined separate passages and paraphrased. Now the app splits each page into numbered excerpts, the Analyst Agent picks IDs, and the app copies the text itself. Citations record character offsets and a SHA-256 fingerprint of the page text.
- **Stance is decided separately from selection.** The analyst's first pass sometimes labelled contradicting evidence as supporting. A separate call now classifies each passage against the specific claim without seeing the proposed verdict, and passages about a neighbouring claim are excluded as irrelevant.
- **The verdict is isolated.** The Verdict Agent receives only the claim and the verified evidence. In a compound sentence this stops one claim's verdict drifting toward the other's.
- **Claims must be verbatim.** Extracted claims are checked against the original text, so the model cannot quietly drop, soften or rewrite an assertion. Minor differences in quotes, apostrophes and dashes are tolerated.
- **Agents choose, the application enforces.** A model only ever picks a tool; the application runs it. So the research agent can plan its searches but cannot finish without looking for contradicting evidence or read a page no search returned; the orchestrator can reroute a claim but cannot skip verification; and the verdict agent can ask for more evidence but cannot change its verdict to get past a refused citation.
- **Withhold rather than guess.** Each claim reports whether its verdict was issued or withheld, with a specific reason such as `no_sources`, `citation_unchecked` or `conflicting_evidence`. A verdict resting on a single page, or on fewer than three sites, is flagged.
- **A failed citation is left out, not fatal.** A passage that fails its citation check is listed as rejected and the verdict rests on the passages that passed. Until the scorecard measured it, one failed citation withheld the whole verdict, which cost correct answers without preventing a wrong one. Two cases still withhold: a check that could not be run, and a passage the citation verifier places on the other side of the claim from the analyst, because leaving that one out could hide evidence against the verdict.
- **Three sites is a goal, not a gate.** The analyst is asked to spread its selections over different sites, and a claim whose verified evidence comes from fewer than three gets one more research round that looks beyond the sites already used. If that finds nothing, the verdict is still issued and says how many sites it rests on. Sections of one organisation's site (`en.wikipedia.org`, `simple.wikipedia.org`) count as one.
- **Credibility is a stated rule, not a guess.** Sources are rated from their web address against short, published lists in [`backend/tools/credibility.py`](backend/tools/credibility.py), using the eight categories and weights of the design brief plus "unrated" for the rest of the web. The rating keeps social media and blogs out of the evidence and labels each verdict's sources as strong, moderate or weak, but it never changes a verdict and is not presented as the chance that a verdict is right.
- **An explanation is shown only if it is cited.** The verdict agent returns its explanation as separate sentences, each with the IDs of the evidence it rests on, so the application can check them without guessing where a sentence ends. Every sentence must point at verified evidence that the verdict itself cites; if one does not, the explanation is left out and the verdict stands. The check covers where a sentence points, not whether it says what that evidence says, so the evidence is shown beside it.
- **Confidence is a level, not a percentage.** The brief asks for a confidence percentage. Thirty scored claims cannot support one, so each verdict gets high, medium or low from things that can be counted: how many sites back it, what kind of sites they are, whether verified evidence points both ways, and how many selected passages failed their check. The reasons are shown with the level ([`backend/services/summary.py`](backend/services/summary.py)).
- **A belief is not evidence.** "Many believe X" and "under the old model, X" are true sentences that do not support X. The check that files each passage as support or contradiction also says whose voice the passage is in, and the application treats a reported belief as background even when the model filed it as support. This is the second attempt at the problem, and it is unproven: in the live check that followed, the model never marked a passage that way (see the [scorecard](docs/SCORECARD.md)).
- **Reuse goes back to the original.** A repeated claim is recognised by its words and context, not by meaning, and only a real verdict issued on complete research is reused. A reused result is never itself reused, so nothing outlives its seven days, and deleting the original report makes the claim be researched again.
- **Copies are not corroboration.** An article's own page is never accepted as evidence for its claims, and pages that repeat the checked text word for word are treated as reposts.
- **A link is downloaded by a boxed-in process.** A video link must be a plain https link to one of three sites, checked before any request is made. The downloader then runs as a separate process with a fixed argument list, only the single-video extractors for those sites, a time limit and a file-size limit, in an empty folder, with none of the app's keys in its environment, and it is stopped together with anything it started when the request ends.
- **Blocked pages fall back transparently.** When a site refuses the app's fetcher, the search provider's extracted text for that page is used instead, and the report labels that evidence.
- **Spending is capped before it happens.** Every model and search call reserves its worst-case cost against a daily ledger before it runs and is reconciled afterwards. When the cap is reached, research stops for the day.
- **Fetching is defensive.** Only public HTTPS pages are fetched; redirects and DNS results are checked against private and reserved addresses, and responses are capped at 1 MB.
- **Model output is repaired on receipt.** Structured outputs occasionally arrived with curly punctuation turned into control characters, which broke exact matching. Outputs are normalised before any comparison.

## Tech stack

| Layer | Tools |
| --- | --- |
| Frontend | React 19, Vite, plain CSS, self-hosted variable fonts |
| Backend | Python, FastAPI, Pydantic, asyncio |
| Language model | OpenAI Responses API with structured outputs (`gpt-4.1-mini`), including image input for on-screen text |
| Search | Tavily |
| Video | ffmpeg and ffprobe, faster-whisper (local transcription) |
| Storage | SQLite |
| Testing | pytest, Playwright, GitHub Actions |
| Deployment | Docker, Railway |

## Project structure

```text
backend/
  main.py                API routes, access control, limits, static serving
  schemas.py             Pydantic contracts shared by the API and the model
  agents/                one file per agent
    runtime.py           the tool loop every agent runs
    orchestrator.py      chooses the next agent; shared state; withholding rules
    content_extractor.py claim_extractor.py research_agent.py
    analyst_agent.py     citation_verifier.py verdict_agent.py
  tools/                 what the agents work with
    providers.py         OpenAI and Tavily adapters
    fetcher.py           safe page fetching
    excerpts.py          numbered excerpts with character offsets
    credibility.py       source categories, weights and evidence strength
    media.py             ffmpeg validation, audio and keyframe extraction
    transcribe.py        local Whisper transcription
    video_link.py        fetches a video from a link with yt-dlp (off by default)
  services/              application plumbing
    pipeline.py          public entry points (the implementation is in agents/)
    article.py, video.py article and video entry points, verbatim claim checks
    input_mapping.py     verbatim mapping of claims to the input
    budget.py            spending reservations and the daily ledger
    history.py           saved reports (SQLite); finds a claim checked before
    summary.py           confidence level and overall verdict
    access.py            password sign-in, sessions, rate limits
  evaluation/            offline policy cases, the live validation runner and the scorecard
  tests/                 offline tests with scripted providers
frontend/
  src/                   the single-page app (main, Report, History, styles)
  tests/e2e/             Playwright tests against a mocked backend
docs/                    deployment guide and validation notes
scripts/dev.py           starts both servers for local development
```

## Getting started

Requirements: Python 3.10 or newer and Node 22.

```sh
# Backend
cd backend
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-lock.txt
.venv/bin/python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000

# Frontend, in a second terminal
cd frontend
npm ci
npm run dev
```

Open http://127.0.0.1:5173 and choose **Explore the free demo**. The demo needs no accounts or keys. `python3 scripts/dev.py` starts both servers with one command.

### Live research

Live research needs an OpenAI API key and a Tavily key, and it costs money (a check typically costs a few cents). Copy `backend/.env.example` to `backend/.env`, fill it in, and restart the backend:

| Setting | Purpose |
| --- | --- |
| `ENABLE_LIVE_RESEARCH` | `true` to allow paid research; off by default |
| `OPENAI_API_KEY`, `TAVILY_API_KEY` | Provider keys, kept on the server |
| `OPENAI_MODEL` | `gpt-4.1-mini` (the model the spending cap is priced for) |
| `DAILY_BUDGET_USD`, `DAILY_SEARCH_LIMIT` | Daily caps; defaults are 0.50 and 40 |
| `APP_PASSWORD`, `SESSION_SECRET` | Password sign-in; required in production |
| `REPORTS_PER_HOUR` | Per-person limit; default 10 |
| `AGENT_MODE` | `autonomous` (default): every agent plans its own steps. `fixed`: each agent does one standard pass. Or a list of agent names, such as `research,verdict` |
| `REUSE_DAYS` | Days a checked claim's result is shown again instead of researched again; default 7, `0` turns reuse off |
| `WHISPER_MODEL` | Whisper size for video; default `small` |
| `ALLOW_VIDEO_LINKS` | `true` lets a video be given as an Instagram, TikTok or YouTube link; default off (see Limitations) |

Never commit `.env` files or keys.

### Video mode

Install ffmpeg (on macOS, `brew install ffmpeg`) and the extra requirements:

```sh
cd backend
.venv/bin/python -m pip install -r requirements-video.txt
```

The Whisper model downloads on first use. Uploaded videos are processed in a temporary folder and deleted; they are never stored.

## API

| Endpoint | Purpose |
| --- | --- |
| `GET /health` | Health check |
| `GET /config` | Readiness and today's usage; setting names only, never values |
| `GET /demo` | The fictional demo report |
| `POST /login`, `POST /logout` | Password sign-in |
| `POST /fact-check` | Check a statement: `{"claim": "..."}` |
| `POST /fact-check-article` | Check an article: `{"url": "https://..."}` |
| `POST /fact-check-video` | Check a video: multipart `file` and optional `caption` |
| `GET /history`, `GET /history/{id}`, `DELETE /history/{id}` | Saved reports |

Interactive documentation is available at `/docs` when the backend is running.

## Testing

```sh
cd backend && .venv/bin/python -m pytest -q        # 678 tests, no network or keys
.venv/bin/python -m evaluation.run                 # 13 policy regression cases
cd ../frontend && npm run build
npx playwright install chromium && npm run test:e2e   # 21 browser tests, mocked backend
```

The backend tests use scripted providers, so they run offline and cost nothing. They cover input validation, claim mapping, citation checks, verdict gating, spending limits, history, access control, article and video ingestion, the safe fetcher, source ratings, and each agent's choices together with the limits on them. The policy cases pin down how the pipeline must behave in specific situations, such as invented source IDs, misattributed quotes, search outages and conflicting evidence. GitHub Actions runs the backend tests, the policy cases and the frontend build on every push.

Passing tests show that the pipeline follows its rules. They do not measure factual accuracy; see below.

## Validation with real providers

I tested the pipeline against live providers in small, budgeted sessions, and used each failure to change the design. The latest rounds returned correct verdicts for the text claims they covered and for three test videos, including one with no audio. After the agents were given their own tools, two statements were re-run live and both returned correct, fully cited verdicts; the agents' less common choices, such as a second research round, are covered by offline tests only. Article mode produced one wrong verdict in its live run; its causes are fixed and tested offline but not yet re-run live. The full account, including what went wrong along the way, is in [docs/VALIDATION.md](docs/VALIDATION.md).

## Scorecard

I ran 30 claims with known answers through the live system and scored the result against the targets in the design brief. Then I made three changes the results pointed to and ran the same claims again.

| Measure | First run | After the changes | Target |
| --- | --- | --- | --- |
| Right verdict | 25 of 30 (83%) | 26 of 30 (87%) | 85% or more |
| Wrong verdict | 2 | 2, none the opposite of the truth | |
| No verdict when one was expected | 10% | 7% | under 15% |
| Verdicts resting on three or more sites | 10 of 26 | 26 of 27 | every verdict |
| Citation checks failed | 2% of passages | 6% of passages | under 5% |
| Cost per claim | about 3 cents | about 3.5 cents | under 15 cents |
| Time per claim | median 46 s | median 64 s | under 120 s |

The changes: a passage that fails its citation check is now left out instead of withholding the whole verdict; the agents aim for three different sites per verdict; and a passage that only reports an old belief was meant to stop counting as support. The first two worked. The third did not: two claims were still withheld because one such passage was filed as support. The run also got slower, and more passages were rejected.

Because those changes were chosen by looking at the same 30 claims, I then scored the system on ten claims it had never been run on: all ten were right, with no rejected citations. That check also showed that a further attempt at the belief problem never took effect, and the claim it was written for ("The Sun orbits the Earth") was withheld a third time.

The sets are small and made of well-known facts and myths, so this is a behaviour check, not an accuracy claim. Every claim, every miss and the method are in [docs/SCORECARD.md](docs/SCORECARD.md).

## Deployment

One Docker image serves the website and the API on a single port. [docs/DEPLOY.md](docs/DEPLOY.md) covers the settings, HTTPS, volumes, backups and the Railway setup.

## Limitations

- This is a demo, not a fact-checking service. Verdicts are automated and can be wrong; read the evidence.
- The scorecard and live validation sets are small and made of well-known claims. They show the system working on specific cases, not a measured accuracy rate.
- Source credibility is a simple rule: sites are rated from short lists of known addresses, so most of the web is "unrated", and the rating says nothing about a particular page's accuracy. Three different sites per verdict is aimed for, not required.
- The confidence level is a rule over the evidence, not a measured probability, and the overall verdict is a count of the claims' verdicts.
- A verdict's explanation is written by the model. The app checks that each sentence cites verified evidence, not that the sentence is a fair reading of it.
- A repeated claim is recognised only when its wording and context match. The same claim in different words is researched again.
- The same model performs analysis and checking in separate calls, so correlated mistakes are possible.
- Only HTML and plain-text pages are read. PDFs, paywalled pages and pages that need JavaScript are skipped.
- A video link is fetched with [yt-dlp](https://github.com/yt-dlp/yt-dlp), and this is off unless `ALLOW_VIDEO_LINKS=true`. Instagram, TikTok and YouTube do not offer an official way to download other people's videos and their terms may not allow it, so the setting is the operator's decision and is meant for videos you have the right to download. The app never signs in to those sites, so private posts cannot be fetched, and sites often refuse downloads from cloud servers: when that happens the report says so and asks for the file instead. No download has been tested from this project's own test suite, which never touches the network.
- Whisper can mishear fast speech or speech over loud music, and on-screen text is read from four keyframes (twelve when the content extractor asks for a closer look), so brief captions can be missed.
- One server process handles one report at a time, with in-memory rate limits. That suits a demo, not public traffic.

## What I would do next

- Fix how a passage describing an old belief is filed: two attempts have not stopped "under the old model, X" counting as support for X.
- Score the system on a larger set of harder claims: statistics, recent events and contested topics.
- Extend the credibility lists, so that well-known medical and scientific organisations are not "unrated".
- Rate sources from more than their address (author, date, citations), and judge whether two sites are really independent rather than only different.
- Review accepted citations by hand on a larger reference set to measure precision.
- Test real Reels with background music, fast speech and fast-changing text.
