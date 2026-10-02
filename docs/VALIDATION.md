# Validation with real providers

The automated tests use scripted providers, so they prove that the pipeline follows its rules, not that it reaches correct verdicts. To find out how it behaves for real, I ran it against OpenAI and Tavily in small, budgeted sessions between 27 September and 2 October 2026. This document summarises what those sessions found and what changed as a result.

These are engineering checks on a handful of cases. They are not an accuracy benchmark.

## Method

- **Real routes, real providers.** Requests went through the same FastAPI routes and pipeline the app uses, with `gpt-4.1-mini` and Tavily basic search.
- **A budget per session.** The validation runner (`backend/evaluation/live.py`) opens a named spending allowance with a dollar cap and a search cap. Every call reserves its worst-case cost before it runs, and the allowance is tied to a fingerprint of the code, so results cannot be mixed across versions.
- **No retries.** Model calls were never retried automatically, so a failure stayed visible.
- **Full traces.** Each run saved the extraction, the searches, the pages read, every accepted and rejected citation, token usage and latency. Traces contain third-party page text and are not committed.
- **Reference claims.** `backend/evaluation/real_cases.json` holds twelve starter claims with reference sources (NASA, USGS, the IAU, the Eiffel Tower's operator). Reference verdicts were never shown to the model.

All sessions together cost roughly $0.50 in model usage.

## What went wrong, and what changed

| Finding in a live run | Change |
| --- | --- |
| The model altered quotes: degree symbols became control characters, separate passages were joined with ellipses. Only 5 of the first 11 proposed citations survived checking. | The model no longer writes quotes. Pages are split into numbered excerpts, the model selects IDs, and the app copies the text. |
| The analysis step labelled contradicting evidence as supporting, so correct FALSE verdicts were withheld. | A separate relation check classifies each passage against the claim without seeing the proposed verdict. |
| Extraction dropped the false half of a compound claim because it judged it incorrect. | Claims must map word for word onto the input, and all of the input must be accounted for. |
| A coverage audit rejected false statements for being false. | Textual fidelity is now checked deterministically; the model is not asked whether a claim is true at that stage. |
| In a compound claim, one assertion's verdict was coloured by its neighbour. | The verdict comes from an isolated call that sees only the claim and its verified evidence. |
| Passages about a neighbouring assertion were shown as support. | The relation check excludes passages about other assertions, and reports separate the evidence a verdict used from the rest. |
| One invented excerpt ID withheld an otherwise supported verdict. | Proposals that point at material never supplied are shown as rejected but no longer block a verdict. Misattributed quotes still do. |
| General facts (a staircase's total step count) were treated as contradicting a specific private claim. | Such facts are classified as background unless they rule the claim out. |
| Structured output arrived with curly apostrophes and dashes turned into control characters, failing the verbatim check. | Model output is repaired on receipt, with regression tests built from the failing strings. |
| Reposts of a press release were counted as independent sources for that release. | Pages repeating the checked text word for word are excluded. |
| "April 1" was read as contradicting "Wednesday" (the same day). | Dates written differently are treated as compatible unless they cannot both be true. |
| Reputable pages refused the app's fetcher, leaving a claim with no evidence. | The search provider's extracted page text is used as a labelled fallback. |
| Budget reservations were about four times too cautious and blocked calls that would have fitted. | Reservations now assume one token per three bytes. |

## Results after these changes

### Statements

| Claim | Result | Notes |
| --- | --- | --- |
| The Moon produces its own light | FALSE | 6 citations accepted, none rejected |
| The Moon never rotates | FALSE | 6 citations accepted, none rejected |
| We see the same side of the Moon, so the other side never receives sunlight | TRUE for the first assertion, FALSE for the second | Both assertions extracted verbatim; each verdict cites only on-topic evidence |
| The IAU currently lists Pluto as the ninth planet | FALSE | Citations from NASA, Britannica and the Smithsonian |
| Water freezes at 0°C at standard pressure | TRUE | |
| The Eiffel Tower was built for the 1889 World's Fair | TRUE | |
| The Great Wall of China is visible from the Moon | FALSE | Also verified on the deployed site |
| An unnamed visitor counted exactly 777 steps yesterday | Verdict withheld | Abstaining is right for an uncheckable private claim, but at the time it happened because of the step-count misreading above |

### Videos

Three generated Reel-format videos with known answers, run on a laptop with ffmpeg and the Whisper `small` model.

| Video | What the app heard and read | Result |
| --- | --- | --- |
| Great Wall myth (speech, on-screen text, caption) | Transcript and on-screen text exact | FALSE, from six verified passages on two sites, read through the search-provider fallback |
| Boiling point of water (speech and on-screen text) | Transcript and on-screen text exact | TRUE |
| Pluto, silent (on-screen text only) | On-screen text exact | FALSE, citing NASA and the Smithsonian |

A video check took about 45 seconds and cost about two cents.

### Articles

One article, a NASA launch press release, was run live. Two of its three claims received verdicts, and one of those was wrong: PARTIALLY TRUE instead of TRUE, caused by the date misreading and the reposts described above. The third claim was refused because its surrounding context skipped a paragraph. All three causes are fixed and covered by offline tests, but article mode has not been re-run live since, so it is the least validated of the three input types.

## What this does not show

- **Accuracy at scale.** A dozen claims and three videos show the pipeline working on specific cases.
- **Citation precision.** Accepted citations passed automated checks. Nobody reviewed them by hand.
- **Source quality.** Some verdicts rested on a single page or on weak secondary sources. The report flags single-source verdicts but does not score credibility.
- **Real Reels.** The test videos were generated with clear speech. Background music, fast speech and brief captions are untested.

## Reproducing a session

From `backend/`, `python -m evaluation.live` prepares a run offline and makes no paid calls. Adding `--allow-paid --allowance NAME --max-usd 0.10 --max-searches 8` starts the validation server under a new allowance. `python -m evaluation.summarize evaluation/results/live-*.json` summarises saved traces without any network calls.
