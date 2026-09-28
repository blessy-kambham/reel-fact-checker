# Reels / Social Media Fact-Checker

**In progress:** an AI fact-checking portfolio project built with React and FastAPI. The current stage supports text input, a fictional demo, and an opt-in research pipeline. Video/Reels input and Instagram integration are planned, not implemented.

**Current milestone: a free fictional walkthrough and an implemented, opt-in text research pipeline. Controlled live runs and follow-up retests reached both providers. Water and Great Wall eventually returned complete reference-matching verdicts, but these runs span different code versions and do not establish accuracy for the current version. Full live reliability and factual accuracy are not established. This is an experimental portfolio MVP, not a trusted fact-checking service.**

## Start here — no accounts or purchases

Open the project folder in VS Code. Open **Terminal → Run Task**, then choose **App: start both servers**. This starts both servers in one terminal; press **Control+C** there to stop both. Use **Setup: check** to check dependencies and ports without starting anything. If the servers are already running, just open http://127.0.0.1:5173.

Click **Explore the free demo**. Its claim, town, numbers, and excerpts are fictional fixtures. It makes zero model or search requests and does not assess text you enter. A demo label appears on the report and example verdict. You can download its JSON.

## What is implemented

- Validated text input: 1–5,000 characters, whitespace trimmed.
- Structured extraction of up to three atomic claims and content intent.
- Bounded parallel research: two claims at a time, one report at a time per server process.
- Separate supporting and contradicting searches for each claim.
- Retrieval of up to six source pages per claim; snippets alone are never verified evidence.
- Analysis selects numbered excerpts from retrieved pages; the application copies quotes directly from source text.
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

96 offline tests pass, the frontend production build passes, and the fictional demo was verified through the browser. Tests use fake providers, not real API accounts. They cover input validation, disabled live mode, secret redaction, fictional demo labeling, supporting/contradicting searches, invented source/excerpt IDs, missing quotes, semantic citation rejection, missing pages, provider failures, partial results, concurrency limits, nonfactual content, and public URL/DNS restrictions.

This is engineering regression coverage, not an accuracy benchmark. All 12 starter reference claims have now been attempted across versions. Two of the latest seven were blocked during analysis by the validation budget guard; successful validation remains incomplete. Independent label review, citation precision evaluation, and broader live integration validation remain. The installed Starlette test client currently emits an httpx deprecation warning; tests pass.

## Limits before real use

- No video, article input, Instagram, database, accounts, deployment, or durable history yet.
- Reports live in the browser until cleared/reloaded; download JSON to keep them.
- HTML/text only; PDFs, paywalls, JS-only pages, and blocked pages may be excluded.
- HTTPS only, no credentials or custom ports; private/reserved DNS targets and redirects are checked. Fetch size is capped at 1 MB and extracted text at 18,000 characters per source.
- Source independence, freshness, and credibility need better evaluation. Source quality is assessed in the prompt; there is no calibrated credibility scoring model.
- Analysis and citation checking use the same configured model in separate calls; correlated mistakes remain possible. Human review is necessary for consequential use.
- Failed citations are excluded from accepted evidence and retained separately with failure codes; the prototype withholds its verdict rather than automatically regenerating.
- No hard dollar budget, distributed rate limiter, production authentication, durable queue, or public-hosting safeguards. Keep it local.
- No guarantee of exhaustive searches, complete claim extraction, or factual accuracy.

## Next increments

1. Fix extraction coverage and evidence stance errors found in live validation, then rerun the affected and budget-blocked cases.
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

The cases cover supported evidence, unknown source IDs, invented/blank excerpt IDs, misattribution, unavailable pages, search outages, failure to research contradictions, a FALSE candidate without contradicting evidence, conflicting evidence, context-only evidence, and mixed valid/invalid citations.

**These are policy regression tests, not fact-check accuracy scores.** Sources, analyst responses, and semantic citation judgments are scripted. The suite runs the application's research and citation validation logic with injected offline providers. It does not measure whether a real model extracts claims correctly, finds good sources, or makes accurate semantic judgments. The pytest wrapper blocks network connections. No API keys are loaded, and no credits are spent.

## Start both servers from a terminal

From the project root on macOS/Linux:

```sh
python3 scripts/dev.py --check
python3 scripts/dev.py
```

The launcher checks installed dependencies and ports 8000/5173, then waits for both servers to respond before printing the app link. If startup fails or either server exits, it stops the other server. It only stops processes it started; it never kills a process occupying a port. A busy port may mean the app is already running. It does not install packages, read API keys, or make external requests. Existing live-mode settings remain in effect for any fact-checks you submit in the app.

## Citation failure diagnostics

Reports now retain `rejected_citations` separately from accepted `evidence`. The UI labels them **Excluded citations — not evidence**, and hides the unverified proposal under a disclosure. JSON export includes the proposal, reason, and a stable failure code: `unknown_source`, `unknown_excerpt`, `empty_quote`, `quote_not_found`, `attribution_rejected`, or `check_unavailable`. Successful checks use `verified`; demo fixtures remain `not_checked`.

Retrieved citations include a retrieval timestamp and SHA-256 fingerprint of the exact extracted text supplied to validation. A fingerprint identifies that text but cannot reconstruct it, prove its accuracy, or guarantee the page will remain unchanged. Full page bodies are not included in the report.

If one semantic citation check fails as a provider operation, prior successful evidence is preserved. The claim remains incomplete and UNVERIFIABLE. Rejected citations never contribute to a verdict. Reports produced before these diagnostics cannot recover discarded citation proposals retroactively. The first Moon-orbit test is such a report, so its precise rejection reason remains unknown.

## Automatic GitHub checks

The **Project checks** workflow runs on pushes and pull requests, and can also be started from the repository's Actions tab. It installs locked dependencies, runs the backend tests and offline evaluation on Python 3.10, and builds the frontend on Node 22. Both jobs have a ten-minute timeout and read-only repository permissions.

No API secrets are supplied to these jobs, and live research is disabled. Dependency installation needs internet access; the checks do not request paid model or search services. A passing workflow verifies these engineering checks, not factual accuracy. See `.github/workflows/checks.yml` for the commands.

## Print or save a readable report

Open any demo or research report and choose **Print / Save PDF**. In your browser's print dialog, select a printer or **Save as PDF**. This runs locally and makes no model or search requests.

The print layout removes the input form and buttons, includes the report ID and source URLs, and expands coverage, limitations, and excluded-citation disclosures. Excluded citations keep their warning labels. Closing the print dialog restores the previous disclosure state. JSON export remains available for the full structured record.

## Controlled text validation

See [the validation report](docs/TEXT_VALIDATION.md) for actual outcomes, citation failure diagnosis, browser checks, cost, and remaining acceptance gates. The text MVP is not yet complete; video work remains deferred.

The 12-case real-world starter dataset is `backend/evaluation/real_cases.json`. It is separate from the 12 fictional policy tests. Seven reference cases have not been run live; do not count them as passes.

### Reproduce a budgeted live session

From `backend`, run `.venv/bin/python -m evaluation.live --allow-paid`. From `frontend`, run `VITE_API_BASE_URL=http://127.0.0.1:8765 npm run dev -- --port 5174`, then visit http://127.0.0.1:5174. This temporarily enables the real API in the validation process; it does not edit `.env`. Stop both terminals with Control+C afterward.

The validation allowance is $0.10 of conservatively reserved OpenAI text cost and 12 Tavily searches, with no model retries. Pricing is specific to gpt-4.1-mini. Reservations are written before calls; successful usage reconciles the reservation, while failed calls retain it. The local budget ledger survives restarts. This session used all 12 searches; a restart does not grant more. Any new allowance needs a deliberate new authorization, not deletion of the ledger to bypass the limit. This is a local validation guard, not an account-level billing guarantee or a production budget mechanism. Tavily costs are separate.

Summarize saved traces without network calls, from `backend`:

```sh
.venv/bin/python -m evaluation.summarize evaluation/results/live-*.json
```

This counts attempts (including repeats), agreement with available reference labels, incomplete reports, accepted/rejected citations, extraction failures, and provider failures. Citation acceptance is an automated gate, not human-reviewed factual precision. Generated traces include source text and stay ignored by Git.

For offline browser error checks, stop the live validation server and run `.venv/bin/python -m evaluation.replay`. Submit `provider failure` or `validation error` to simulate errors without API calls; other text replays the latest saved report with an explicit replay note. This test utility is local only and is not the production app.

## Excerpt selection — offline checks and limited live retests

The analyst now returns a source ID, excerpt ID, statement, and stance. Its output schema has no quote field. The application reconstructs the selected quote from the retrieved text and records `excerpt_id`, `source_start`, and `source_end` in the citation. Offsets are Python Unicode character indexes into the exact extracted source text, with an exclusive end index; they are not HTML or byte offsets.

Excerpts prefer sentence boundaries, with overlapping windows for passages longer than 400 characters. Source text is not rewritten. Unknown IDs and excerpts belonging to a different source fail validation. The quote-presence check and separate attribution/stance check still run, using the full retrieved source for context. A valid excerpt is not automatically accepted evidence.

Offline checks preserved 805 excerpts from 13 saved source snapshots without changing their characters. Tests also cover degree symbols, curly apostrophes, long passages, invalid IDs, partial results, and rejected attribution. This verifies source copying, not the model's ability to choose relevant excerpts. The six earlier live results in the validation report predate this change. No new paid requests were made, and the previous search allowance remains exhausted.

Remaining limitations: sentence splitting is heuristic; an excerpt can omit qualifications, and source credibility or model interpretation can still be wrong. Live water/Great Wall retests and independent citation review are required before claiming this solves end-to-end reliability.

### Excerpt live retest result

On 2026-09-28, two newly authorized real API tests used the excerpt-selection implementation. Great Wall returned FALSE/complete with three accepted citations. Water remained incomplete with five accepted citations and one rejected CONTEXT citation. All nine quotes exactly matched selected excerpts; the remaining rejection concerns semantic context checking rather than altered quote text. The run used four Tavily searches and approximately $0.0224 of OpenAI tokens. See the validation report for the detailed limits and next steps. Saved live mode remains disabled, and the text MVP is not yet complete.

### Context-aware citation verification

The verifier now returns two required judgments: `supports_attribution` checks whether the quoted source supports the attributed statement, and `stance_matches` checks its relationship to the original claim. Both must pass. Relevant background definitions can be valid CONTEXT without proving the claim; CONTEXT alone still cannot establish a verdict. Unsupported statements, incorrect stances, irrelevant text, and missing qualifications must still be rejected.

Twelve additional offline cases check the acceptance gates, valid background alongside direct support, context-only abstention, and missing stance judgments. These use scripted judgments and do not establish that the live model follows the clarified instructions. The implementation increment itself made no paid requests. The subsequent water retest is described below; it selected only FOR citations.

A subsequent water retest with the separated verifier judgments returned TRUE/complete: three citations accepted, zero rejected, and no provider errors. All three were FOR, so this success does not directly validate the problematic CONTEXT example. Source independence and quality still need review. See the validation report for usage and remaining acceptance gates.

### Recheck the exact saved CONTEXT citation

From `backend`, inspect a saved failed context example without network calls:

```sh
.venv/bin/python -m evaluation.context_case evaluation/results/excerpt-retest-2026-09-28/freezing.json
```

The command matches the rejected citation to its original verifier input and refuses missing or ambiguous page snapshots. With an explicitly authorized budget, `--allow-paid --ledger <existing-budget.json> --output <results/check.json>` performs one attribution/stance check with no web searches and no automatic retries. It uses the existing allowance, never creates a new one. A passing result validates that saved citation only; it does not generate or validate an entire verdict.

The exact saved CONTEXT citation passed a live check. The seven remaining reference requests were attempted; extraction/stance failures and two budget-blocked cases prevent sign-off. Several starter labels have documented defensible alternatives; exact-label disagreement is not automatically a factual error.


### Latest bounded validation — 2026-09-28

The exact CONTEXT replay passed. Seven additional requests exposed two important reliability defects: extraction can omit a false assertion, and the analyst can label contradicting evidence FOR. The verifier rejected seven such citation proposals. Water's universal claim returned FALSE (a documented defensible alternative to its starter label), and the Eiffel Tower year returned TRUE. Pluto and private-count analysis were blocked by conservative budget reservations. These are not seven successful fact checks.

The run used 37 successful model calls, 16 Tavily searches, and approximately $0.06633 in OpenAI tokens, within the approved $0.10/16-search allowance. No automatic retries were used. Saved live mode remains off. All 96 offline tests, 12 policy cases, and the frontend build pass. See `docs/TEXT_VALIDATION.md` for the full case table and limitations. The text MVP remains incomplete.
