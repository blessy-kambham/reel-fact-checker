# Reels / Social Media Fact-Checker

**In progress:** an AI fact-checking portfolio project built with React and FastAPI. The current stage supports text input, a fictional demo, and an opt-in research pipeline. Video/Reels input and Instagram integration are planned, not implemented.

**Current milestone: a free fictional walkthrough and an implemented, opt-in text research pipeline. Live provider calls have not been validated with real credentials. This is an experimental portfolio MVP, not a trusted fact-checking service.**

## Start here — no accounts or purchases

Open the project folder in VS Code. Open **Terminal → Run Task**, then run **Backend: start** and **Frontend: start** in separate terminals. If the servers are already running, just open http://127.0.0.1:5173.

Click **Explore the free demo**. Its claim, town, numbers, and excerpts are fictional fixtures. It makes zero model or search requests and does not assess text you enter. A demo label appears on the report and example verdict. You can download its JSON.

## What is implemented

- Validated text input: 1–5,000 characters, whitespace trimmed.
- Structured extraction of up to three atomic claims and content intent.
- Bounded parallel research: two claims at a time, one report at a time per server process.
- Separate supporting and contradicting searches for each claim.
- Retrieval of up to six source pages per claim; snippets alone are never verified evidence.
- Analysis using only the retrieved pages, with source IDs assigned by the application.
- Citation checks: exact normalized quote matching followed by a separate model check of attribution and stance.
- Failed citation checks withhold the verdict. Missing research produces UNVERIFIABLE.
- Partial claim results on model/search failures, time limits, report limitations, token/call counts.
- Claim-by-claim web report, source links, evidence panels, and JSON export.
- Explicit provider configuration status; live mode is off by default.
- Offline evaluation runner with 12 fictional policy regression cases; no keys or network required.
- Blank quotes are rejected; unqualified TRUE/FALSE verdicts are withheld when verified evidence is divided.

No confidence percentage is fabricated. Quote presence does not prove truth; model-based attribution checks can still be wrong.

## Local setup on macOS

Dependencies are already installed on the original machine. On a new checkout:

**Backend terminal:**

```sh
cd backend
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-lock.txt
.venv/bin/python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

**Frontend terminal (from project root):**

```sh
cd frontend
npm ci
npm run dev
```

Open http://127.0.0.1:5173. API docs: http://127.0.0.1:8000/docs. Stop each server with Control+C. Both servers bind only to this machine. Node 23.6.1 and Python 3.10.5 were used during development; use a supported Node LTS for future setup.

## Live research — optional, not needed today

Keep using the free demo until you are ready to configure providers with guidance.

Live requests require OpenAI API access, a model in your account that supports Responses structured outputs, and a Tavily search key. ChatGPT billing is separate from API billing. This project has not signed up for or purchased either service. Exact charges depend on the selected model and provider plan; check current billing, available free allowances, and set spending limits before enabling live mode.

When ready, copy `backend/.env.example` to `backend/.env`, enter your keys locally, select `OPENAI_MODEL`, and explicitly set `ENABLE_LIVE_RESEARCH=true`. Restart the backend, then use **Retry connection**. The configuration endpoint checks whether settings exist; it does not test account access or balances. No keys belong in frontend files, Git, or chat.

Live research sends submitted text and retrieved pages to OpenAI, search queries to Tavily, and fetches public source pages. `store=False` is used for Responses requests. This is not a claim of zero provider-side retention. Do not enter private information without reviewing provider policies.

| Variable | Location | Default / purpose |
| --- | --- | --- |
| `ENABLE_LIVE_RESEARCH` | backend | `false`; explicit switch for paid external calls |
| `OPENAI_API_KEY` | backend | Empty; kept on the server |
| `OPENAI_MODEL` | backend | Empty; choose an available structured-output model |
| `TAVILY_API_KEY` | backend | Empty; search provider credential |
| `CORS_ORIGINS` | backend | Localhost and 127.0.0.1 on port 5173 |
| `VITE_API_BASE_URL` | frontend | `http://127.0.0.1:8000`; public, never a secret |

## Architecture and files

```text
React form → FastAPI → intent + atomic claims
                         ├─ claim research (max two concurrent)
                         │    ├─ supporting search
                         │    └─ contradicting search
                         │         ↓ fetch public pages
                         │      evidence analysis
                         │         ↓ quote + attribution checks
                         └─ aggregate partial results → report → React
```

- `frontend/src/main.jsx`: input, connection status, demo/live controls, errors.
- `frontend/src/Report.jsx`: report and JSON download.
- `frontend/src/style.css`: responsive layout.
- `backend/main.py`: API routes and live-mode guard.
- `backend/schemas.py`: shared Pydantic contracts.
- `backend/services/providers.py`: OpenAI and Tavily adapters.
- `backend/services/pipeline.py`: extraction, research concurrency, evidence and citation validation.
- `backend/services/fetcher.py`: safe bounded HTTPS source fetching.
- `backend/services/demo.py`: fixed fictional report, separate from live submissions.
- `backend/tests/`: offline regression tests with mock providers.
- `.vscode/tasks.json`: start servers and run backend tests from VS Code.

An async function can wait on a network request while another claim progresses. A semaphore limits how many claim branches run together. Pydantic schemas validate the JSON exchanged with both the browser and model.

## API

| Endpoint | Behavior |
| --- | --- |
| `GET /health` | Server health |
| `GET /config` | Readiness and missing setting names; never secret values |
| `GET /demo` | Fictional fixed report; no external calls |
| `POST /fact-check` | `{"claim":"Your statement"}` → live report, or 503 when disabled |

Validation errors return 422, concurrent requests 429, extraction/provider failure 502, and report timeout 504. Per-claim research failures return an incomplete UNVERIFIABLE claim within the report. There is a 330-second total report timeout; this MVP does not meet the eventual 120-second production target.

## Tests and verification

```sh
cd backend
.venv/bin/python -m pytest -q
cd ../frontend
npm run build
```

45 offline tests pass, the frontend production build passes, and the fictional demo was verified through the browser. Tests use fake providers, not real API accounts. They cover input validation, disabled live mode, secret redaction, fictional demo labeling, supporting/contradicting searches, invented source IDs, missing quotes, semantic citation rejection, missing pages, provider failures, partial results, concurrency limits, nonfactual content, and public URL/DNS restrictions.

This is engineering regression coverage, not an accuracy benchmark. We still need labeled real claims, citation precision evaluation, and live integration validation. The installed Starlette test client currently emits an httpx deprecation warning; tests pass.

## Limits before real use

- No video, article input, Instagram, database, accounts, deployment, or durable history yet.
- Reports live in the browser until cleared/reloaded; download JSON to keep them.
- HTML/text only; PDFs, paywalls, JS-only pages, and blocked pages may be excluded.
- HTTPS only, no credentials or custom ports; private/reserved DNS targets and redirects are checked. Fetch size is capped at 1 MB and extracted text at 18,000 characters per source.
- Source independence, freshness, and credibility need better evaluation. Source quality is assessed in the prompt; there is no calibrated credibility scoring model.
- Analysis and citation checking use the same configured model in separate calls; correlated mistakes remain possible. Human review is necessary for consequential use.
- Failed citations are excluded and flagged; the prototype withholds its verdict rather than automatically regenerating.
- No hard dollar budget, distributed rate limiter, production authentication, durable queue, or public-hosting safeguards. Keep it local.
- No guarantee of exhaustive searches, complete claim extraction, or factual accuracy.

## Next increments

1. Configure providers together and validate a small set of live text claims.
2. Evaluate live claims against professional fact-check references and strengthen source-quality checks.
3. Persist submissions and evidence in PostgreSQL.
4. Add video transcription and visible-text extraction.
5. Deploy only after evaluation and operational safeguards; integrate Instagram afterward.

## Git

The development branch is `main`. Local secrets, virtual environments, dependencies, caches, build output, and generated preview screenshots are excluded from version control. Keep future completed milestones on this same repository; never commit `.env` or API keys.

## Implementation references

[OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs), [Tavily Search API](https://docs.tavily.com/documentation/api-reference/endpoint/search), [FastAPI](https://fastapi.tiangolo.com/tutorial/first-steps/), [Vite](https://vite.dev/guide/).

## Free offline evaluation

In VS Code, run **Terminal → Run Task → Evaluation: offline**, or run from `backend`:

```sh
.venv/bin/python -m evaluation.run --output evaluation/results/latest.json
```

The runner checks 12 versioned fictional cases from `backend/evaluation/cases.json`. It reports expected vs actual verdicts and citation counts, and exits nonzero on a failed case. Generated reports are ignored by Git.

The cases cover supported evidence, unknown source IDs, fabricated/blank quotes, misattribution, unavailable pages, search outages, failure to research contradictions, a FALSE candidate without contradicting evidence, conflicting evidence, context-only evidence, and mixed valid/invalid citations.

**These are policy regression tests, not fact-check accuracy scores.** Sources, analyst responses, and semantic citation judgments are scripted. The suite runs the application's research and citation validation logic with injected offline providers. It does not measure whether a real model extracts claims correctly, finds good sources, or makes accurate semantic judgments. The pytest wrapper blocks network connections. No API keys are loaded, and no credits are spent.
