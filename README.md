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
- Retrieval of up to six source pages per claim; snippets alone are never verified evidence. When a site refuses the app's page reader, the search provider's own extracted text of that page is used instead (no extra cost), and the report labels that evidence and notes it under the claim's limitations.
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

135 offline tests pass, the frontend production build passes, and the fictional demo was verified through the browser. Tests use fake providers, not real API accounts. They cover input validation, disabled live mode, secret redaction, fictional demo labeling, supporting/contradicting searches, invented source/excerpt IDs, missing quotes, semantic citation rejection, missing pages, provider failures, partial results, concurrency limits, nonfactual content, and public URL/DNS restrictions.

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

First prepare offline, from `backend`: `.venv/bin/python -m evaluation.live`. This makes no network or paid calls. It fingerprints the exact source that would run (tracked and untracked files under `backend/`, `frontend/src/`, `scripts/` and `.github/`, never `.env` files, keys or generated output), copies it to the ignored `evaluation/results/snapshots/`, reports settings by name only and lists allowances.

After a new, explicitly approved allowance, start the paid server with the approved limits, for example `.venv/bin/python -m evaluation.live --allow-paid --allowance compound-retest-1 --max-usd 0.10 --max-searches 8`. From `frontend`, run `VITE_API_BASE_URL=http://127.0.0.1:8765 npm run dev -- --port 5174`, then visit http://127.0.0.1:5174. This temporarily enables the real API in the validation process; it does not edit `.env`. Stop both terminals with Control+C afterward.

Each allowance is a named ledger in `evaluation/results/allowances/`, tied to the code fingerprint it was opened with. It survives restarts, its limits can never be raised, and it is refused if the code has changed; a new approval needs a new name. The old `budget.json` ledger is left untouched and is no longer used. Every trace records its allowance, code fingerprint and snapshot. Model calls are never retried. Pricing is specific to gpt-4.1-mini. Reservations are written before calls; successful usage reconciles the reservation, while failed calls retain it. This is a local validation guard, not an account-level billing guarantee or a production budget mechanism. Tavily costs are separate.

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


### Extraction coverage safeguard

Before web research, a separate model judgment compares extraction with the original submission. It checks missing assertions, changed qualifiers, invented context, and intent classification. Flagged omissions, failed coverage judgments, and unavailable checks stop research and return an incomplete UNVERIFIABLE report for the original submission. The API exposes `coverage_status`, and the frontend shows an explicit warning.

This adds one structured model call when extraction has not already flagged an omission. It uses the same provider/model, so correlated mistakes remain possible; passing the check is not proof of coverage or truth. No automatic repair or retry is added.

105 offline tests, 12 policy cases, and the frontend build pass. The new coverage stage has not been tested with live providers; live accuracy and evidence stance reliability remain open acceptance gates.


### Coverage live retest findings

Three Moon cases were attempted after adding the coverage audit. Extraction preserved both assertions of the compound claim, but the audit rejected all three cases, incorrectly requiring false claims to be corrected or qualified. No research or citation checks ran. This is a confirmed coverage-audit false-rejection problem, not successful live validation. The next change must separate textual fidelity from factual truth more reliably.

The run used six model calls, approximately $0.0018868 in OpenAI tokens, and zero Tavily searches. Validation budgets now persist a stop after any budget refusal, record blocked stages, and serialize model calls for reliable per-call usage accounting. 108 offline tests and 12 policy cases pass. The text MVP remains incomplete.


### Current roadmap stage

We are in **Step 1: text validation**. Steps 2–9 remain: source-quality/app cost controls, browser tests, database/history and article ingestion, video pipeline, real-video validation, social links, deployment, Instagram DM integration.

Coverage now accepts an exact full-submission copy using a deterministic text comparison, provided intent is checkable, no omissions are flagged, and context contains no added text. Rewritten/split claims still require the model fidelity audit. Exact preservation proves no text was dropped; it does not prove truth, atomicity, or causal validity. The audit prompt now includes faithful false-statement and changed-negation examples.


Latest retest: unchanged single claims reached research, but evidence stance errors still withheld their verdicts. The compound claim's semantic audit still incorrectly rejected a false assertion. Total usage across the shared allowance is about $0.03070 and four searches. Step 1 remains incomplete; next work is structured assertion-to-input mapping and evidence stance handling. See the validation report for exact results.


### Verbatim input mapping

Extraction now requests ordered, verbatim substrings rather than paraphrased claims. The backend records validated `input_spans` (start, end, text) in reports. A complete mapping must account for all submitted text except a narrow set of clause separators; split claims retain the entire original submission as context. Missing assertions, changed wording, reordered claims, dropped conditions and added context cannot pass this deterministic path. Unmapped extraction still requires the conservative semantic audit and can be rejected.

This verifies textual coverage, not factual truth, atomicity, or valid causal reasoning. Research and independent citation checks still decide whether a verdict can be issued. 123 offline tests and 12 policy cases pass.


Live mapping retest: both compound Moon assertions were preserved and researched. The no-sunlight assertion returned FALSE; the same-side assertion remained UNVERIFIABLE due to conflicting evidence labels. All eight selected citations passed verification. This confirms the mapping path for this case; text validation remains incomplete. Shared allowance usage is $0.0561984 and eight searches. Details are in `docs/TEXT_VALIDATION.md`.


### Local evidence relationship follow-up

Each selected quote now receives a separate relationship classification against the specific target assertion. This call sees the quote and full page, but not the analyst's verdict, statement or proposed stance. SUPPORTS/CONTRADICTS/BACKGROUND map to FOR/AGAINST/CONTEXT; IRRELEVANT and UNCERTAIN are excluded. A separate attribution-and-stance verifier must still approve the result. Reports retain `proposed_stance` and `relation_reason` for audit. Failed or unavailable checks continue to withhold verdicts.

This adds up to six model calls per claim and has not been validated live. The analyst's candidate verdict still passes through conservative evidence gates; no verdict is automatically flipped when a stance changes. 129 offline tests, 12 policy cases and the frontend build pass. These changes are local pending a later GitHub update.


### Latest local live results

The new relationship classifier was tested live: Moon light and Moon rotation both returned FALSE/complete; all 24 citations across three requests passed automated verification. The compound case still exposed verdict-scope contamination: the same-side assertion was labeled MISLEADING while its explanation discussed the separate sunlight assertion. Source authority and independence also remain unresolved. Text validation is not complete.

The run used approximately $0.07821 and eight Tavily searches, with no retries or provider failures. Changes and results remain local; GitHub has not been updated. See `docs/TEXT_VALIDATION.md` for the case table and limitations.


### Claim-specific verdict stage

Final verdicts now come from a separate call receiving only the target assertion and verified evidence. The analyst's candidate verdict, full submission context and research-stage limitations do not feed that decision. Each non-UNVERIFIABLE decision must reference supplied evidence IDs; unknown/missing IDs, unsupported stance combinations or provider failures withhold the verdict. Previously verified evidence remains available for review. Research-stage free-form limitations are excluded from the final report to prevent neighboring-claim explanations from reappearing there; application-generated retrieval and verification warnings remain.

This adds at most one model call per claim after successful citation checks. It does not guarantee semantic isolation: evidence statements can still discuss neighboring facts, and ambiguous pronouns may remain unresolved. 135 tests and 12 policy cases pass offline. Live validation of this stage is pending. Committed on the `local-verdict-stage` branch.

### Verdict audit trail and validation-runner preparation

Every claim now states whether its verdict was `issued` or `withheld`. Withheld verdicts carry a specific `withheld_reason` and message (for example `citation_failed`, `unknown_evidence_ids`, `evidence_stance_mismatch`, `conflicting_evidence` or `verdict_check_unavailable`) instead of one generic warning. Verified citations receive stable IDs (E1, E2...), the same IDs the verdict stage sees. Reports keep the verdict stage's raw answer (`decision_verdict`, `decision_evidence_ids`) and, for issued verdicts, the evidence behind them (`verdict_evidence_ids`). The report page shows the withheld reason and marks citations used for the verdict. The trace summarizer counts issued verdicts and withheld reasons.

The validation runner now prepares offline by default, snapshots the exact code including untracked files, and uses named allowances tied to that code. Offline tests cover reservation and tracing of the relation and verdict calls, a budget stop before the verdict call, persistent stops across restarts, search caps and zero retries. Found while testing: when the search cap stops an allowance after one search, the analysis call is refused and the report withholds the claim as `provider_failure`; no call is made.

175 offline tests, 12 policy cases and the frontend build pass. None of this has been validated live.

### Off-topic evidence and single-source verdicts

The 2026-09-30 compound retest issued TRUE and FALSE correctly, but under the same-side assertion the report listed citations about the neighbouring sunlight assertion as support. The report now shows the evidence a verdict used first and groups the rest as other verified evidence, not used for the verdict. The analysis and relation prompts now exclude evidence about other assertions, and every prompt example is fictional; earlier examples mirrored validation cases. Issued verdicts record how many distinct pages they rest on, and verdicts resting on one page carry a visible single-source note. 180 offline tests, 12 policy cases and the frontend build pass. The prompt changes are not yet validated live.

### Daily spending cap, history, article links and browser tests

- **Daily spending cap.** Live research in the normal app now has a per-UTC-day limit on estimated OpenAI cost and Tavily searches (`DAILY_BUDGET_USD`, default 0.50; `DAILY_SEARCH_LIMIT`, default 40). Each call reserves its cost before it is made; the page shows today's usage. When the limit is reached, requests return HTTP 429 and claims cut short are withheld as `spending_limit`. Limits cannot be raised within the same day. Pricing is built in for gpt-4.1-mini only; other models disable live research with a clear message. The ledger shares code with the validation runner (`services/budget.py`). Still set account-level limits with your providers.
- **History.** Live reports are saved to a local SQLite file (`backend/data/history.sqlite3`, git-ignored): the full report plus rows for claims and citations, including which citations each verdict used. `GET /history`, `GET /history/{id}` and `DELETE /history/{id}`; the page has a Saved reports panel. Demo reports are not saved. A failed save never loses the report.
- **Article links.** `POST /fact-check-article` with `{"url": ...}` fetches a public HTTPS article with the existing safe fetcher, selects up to three central claims copied word for word (paraphrased claims or invented context are refused), and researches them with the same pipeline. The article's own page, including redirects back to it, is never accepted as evidence for its own claims. The page has a Statement / Article link switch.
- **Browser tests.** Ten Playwright tests run against a mocked backend (no keys, network or cost): demo and JSON download, verdict-evidence grouping, single-source note, withheld reasons, incomplete coverage, loading state, errors, unreachable backend, article mode, saved reports, and print. Run `npm ci && npx playwright install chromium && npm run test:e2e` in `frontend/`. GitHub CI does not run them yet: adding the job to `.github/workflows/checks.yml` has to be done by hand.

223 backend tests, 12 policy cases, 10 browser tests and the frontend build pass. None of these features has been exercised against the live providers yet.

### Live checkpoint 1 and follow-up fixes

Checkpoint 1 (four requests, $0.108) returned correct FALSE verdicts for the far-side sunlight assertion and for Pluto, and confirmed off-topic citations no longer appear. It also showed that one invented excerpt ID could withhold a supported verdict, that general facts could be labelled as contradictions, that article claims failed the verbatim check over apostrophes, and that budget reservations were about four times too high. All four are fixed offline; details are in `docs/TEXT_VALIDATION.md`. 229 backend tests, 13 policy cases, 10 browser tests and the frontend build pass.

### Video mode

`POST /fact-check-video` (multipart: `file`, optional `caption`) checks a short video with existing tools, no custom models, as in the original design:

1. **Validate** with ffprobe: MP4, MOV or WebM with a video track, at most 3 minutes and 100 MB. Oversized uploads are refused before the body is read; ffmpeg only reads local files.
2. **Extract** 16 kHz mono audio and four evenly spaced keyframes with ffmpeg, in a temporary folder that is always deleted.
3. **Transcribe** speech locally with Whisper (faster-whisper). Free, and audio never leaves the machine.
4. **Read on-screen text** from the keyframes with the configured OpenAI model's image input (low detail), reserved against the daily spending cap.
5. **Normalize** transcript, on-screen text and optional caption into one text, then select up to three central claims copied word for word and research them exactly like articles.

Reports include what the video says (transcript, on-screen text, caption) and a media summary so recognition mistakes are visible. Setup for video only: install ffmpeg (macOS: `brew install ffmpeg`) and `pip install -r requirements-video.txt`; the Whisper model (`WHISPER_MODEL`, default `small`, about 0.5 GB) downloads on first use. Without them, `/config` explains what is missing and the page disables video.

Tested offline with real ffmpeg on generated clips, fake transcription and a fake model: 251 backend tests, 13 policy cases, 12 browser tests and the frontend build pass. Not yet run against real videos or live providers (live checkpoint 2).

### Off-topic proposals no longer block verdicts

A proposed citation the relation check finds irrelevant or unresolvable for the claim is now shown under excluded citations but ignored, like proposals that point at material never supplied. A misattributed citation or an unavailable check still withholds the verdict. 252 backend tests pass.

### Deployment preparation and access control

- **One container** (`Dockerfile`) builds the website and serves it from the API on one port; video support is a build option. `.dockerignore` keeps `.env` files, local data and traces out of images (verified with decoy secrets).
- **Access control** (`services/access.py`): an optional `APP_PASSWORD` with a signed, HttpOnly, SameSite=Strict session cookie; sign-in attempts are throttled. Production (`ENVIRONMENT=production`) refuses live research until a password and a 32+ character `SESSION_SECRET` are set. Research and saved reports require a session when access control is on; video uploads are refused before their body is read.
- **Per-person limit** of `REPORTS_PER_HOUR` (default 10) on top of the daily spending cap.
- **Security headers** on every response, including a strict Content-Security-Policy.
- **Guide:** `docs/DEPLOY.md` covers settings, HTTPS, health checks, backups and rollback.

A production-mode rehearsal (built site served by the API, sign-in in a real browser, history, no CSP errors) passed locally. The Docker image itself has not been built here because Docker Hub is unreachable from this environment; build it with Docker Desktop. 265 backend tests, 13 policy cases, 13 browser tests and the frontend build pass.


### Live checkpoint 2: video mode verified

Three generated Reel-format test videos ran through the whole pipeline on the Mac. Local Whisper transcribed the speech exactly and the keyframes' on-screen text was read exactly, including a silent video. Verdicts: water boiling point TRUE, Pluto FALSE, and the Great Wall claim correctly withheld because none of its sources could be fetched. The run also exposed and led to fixes for corrupted punctuation in model output, overly strict context checks, reposts counted as independent evidence, and date misreadings. Details in `docs/TEXT_VALIDATION.md`. 270 backend tests and 13 policy cases pass.

Follow-up: pages that refuse the app's reader now fall back to the search provider's extracted page text, clearly labelled (same search cost). 274 backend tests, 13 policy cases and 14 browser tests pass. Live check: the Great Wall video, previously withheld, is now correctly FALSE from search-copy sources ($0.017).

### Deployed

The app runs on Railway (text and article modes, password sign-in) and redeploys from `main`. See `docs/DEPLOY.md` for the steps and for how to diagnose a deployment from `/config`.
