# Reel Fact-Checker

Paste a claim, an article link or a short video, and get a verdict for each claim with the evidence behind it. Every quote in a report is copied from a page the app actually read, and when the evidence is not good enough the app says so instead of guessing.

I built this as a portfolio project to explore a question I care about: how do you make an LLM pipeline that is honest about what it can and cannot support? A private, password-protected demo runs on Railway.

## What it does

- **Three kinds of input.** A typed statement, a public article URL, or an uploaded video (MP4, MOV or WebM, up to 3 minutes).
- **Video understanding.** Speech is transcribed locally with Whisper, on-screen text is read from keyframes, and an optional caption is included, so a silent Reel with text overlays works too.
- **Claim-by-claim reports.** Up to three claims per submission, each with a verdict (`TRUE`, `FALSE`, `PARTIALLY TRUE`, `MISLEADING`, `OUTDATED` or `UNVERIFIABLE`), the passages the verdict rests on, and links to their sources.
- **Verdicts can be withheld.** If citations fail their checks, sources cannot be read, or the evidence conflicts, the report gives the reason instead of a verdict.
- **Saved reports.** Live reports are stored in SQLite and can be reopened, printed, saved as PDF or downloaded as JSON.
- **A free demo mode.** A fictional example report that needs no API keys and makes no external calls.

## How it works

```text
statement ─┐
article ───┼─▶ extract up to 3 claims, copied word for word from the input
video ─────┘        │
  (ffmpeg, Whisper, │   for each claim (two at a time):
   keyframe text)   ▼
              search for supporting evidence ─┐
              search for contradicting evidence ─┤
                                                 ▼
              fetch the pages, split them into numbered excerpts
                                                 ▼
              analysis: the model selects excerpt IDs (it never writes quotes)
                                                 ▼
              relation check: does this passage support, contradict or
              merely relate to this exact claim?
                                                 ▼
              attribution check: does the quote really say what is claimed?
                                                 ▼
              verdict: a separate call that sees only the claim and the
              verified evidence, and must cite evidence IDs
                                                 ▼
                       report, saved to history
```

## Design decisions

These are the choices that shaped the project, most of them made after a live test showed a failure.

- **The model never writes a quote.** Early versions asked the model to quote its sources, and it altered punctuation, joined separate passages and paraphrased. Now the app splits each page into numbered excerpts, the model picks IDs, and the app copies the text itself. Citations record character offsets and a SHA-256 fingerprint of the page text.
- **Stance is decided separately from analysis.** The analysis step sometimes labelled contradicting evidence as supporting. A separate call now classifies each passage against the specific claim without seeing the proposed verdict, and passages about a neighbouring claim are excluded as irrelevant.
- **The verdict is isolated.** The final verdict comes from a call that receives only the claim and the verified evidence. In a compound sentence this stops one claim's verdict drifting toward the other's.
- **Claims must be verbatim.** Extracted claims are checked against the original text, so the model cannot quietly drop, soften or rewrite an assertion. Minor differences in quotes, apostrophes and dashes are tolerated.
- **Withhold rather than guess.** Each claim reports whether its verdict was issued or withheld, with a specific reason such as `no_sources`, `citation_failed` or `conflicting_evidence`. A verdict resting on a single page is flagged.
- **Copies are not corroboration.** An article's own page is never accepted as evidence for its claims, and pages that repeat the checked text word for word are treated as reposts.
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
  services/
    pipeline.py          claim extraction, research, citation checks, verdicts
    article.py           article ingestion and verbatim claim selection
    video.py, media.py   video validation, audio and keyframe extraction
    transcribe.py        local Whisper transcription
    providers.py         OpenAI and Tavily adapters
    fetcher.py           safe page fetching
    excerpts.py          numbered excerpts with character offsets
    input_mapping.py     verbatim mapping of claims to the input
    budget.py            spending reservations and the daily ledger
    history.py           saved reports (SQLite)
    access.py            password sign-in, sessions, rate limits
  evaluation/            offline policy cases and the live validation runner
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
| `WHISPER_MODEL` | Whisper size for video; default `small` |

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
cd backend && .venv/bin/python -m pytest -q        # 276 tests, no network or keys
.venv/bin/python -m evaluation.run                 # 13 policy regression cases
cd ../frontend && npm run build
npx playwright install chromium && npm run test:e2e   # 14 browser tests, mocked backend
```

The backend tests use scripted providers, so they run offline and cost nothing. They cover input validation, claim mapping, citation checks, verdict gating, spending limits, history, access control, article and video ingestion, and the safe fetcher. The policy cases pin down how the pipeline must behave in specific situations, such as invented source IDs, misattributed quotes, search outages and conflicting evidence. GitHub Actions runs the backend tests, the policy cases and the frontend build on every push.

Passing tests show that the pipeline follows its rules. They do not measure factual accuracy; see below.

## Validation with real providers

I tested the pipeline against live providers in small, budgeted sessions, and used each failure to change the design. The latest rounds returned correct verdicts for the text claims they covered and for three test videos, including one with no audio. Article mode produced one wrong verdict in its live run; its causes are fixed and tested offline but not yet re-run live. The full account, including what went wrong along the way, is in [docs/VALIDATION.md](docs/VALIDATION.md).

## Deployment

One Docker image serves the website and the API on a single port. [docs/DEPLOY.md](docs/DEPLOY.md) covers the settings, HTTPS, volumes, backups and the Railway setup.

## Limitations

- This is a demo, not a fact-checking service. Verdicts are automated and can be wrong; read the evidence.
- The live validation set is small. It shows the pipeline working on specific cases, not a measured accuracy rate.
- Source quality is judged only by the model's instructions. There is no credibility scoring, and several citations can come from one page.
- The same model performs analysis and checking in separate calls, so correlated mistakes are possible.
- Only HTML and plain-text pages are read. PDFs, paywalled pages and pages that need JavaScript are skipped.
- Video needs a file upload. Instagram offers no official way to download other people's Reels, so pasting a Reel link is not supported.
- Whisper can mishear fast speech or speech over loud music, and on-screen text is read from four keyframes, so brief captions can be missed.
- One server process handles one report at a time, with in-memory rate limits. That suits a demo, not public traffic.

## What I would do next

- Score source credibility and require independent sources for a verdict.
- Review accepted citations by hand on a larger reference set to measure precision.
- Read more keyframes for videos with fast-changing text.
