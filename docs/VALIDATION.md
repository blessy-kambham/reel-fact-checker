# Validation with real providers

The automated tests use scripted providers, so they prove that the pipeline follows its rules, not that it reaches correct verdicts. To find out how it behaves for real, I ran it against OpenAI and Tavily in small, budgeted sessions between 27 September and 6 October 2026. This document summarises what those sessions found and what changed as a result.

These are engineering checks on a handful of cases. They are not an accuracy benchmark.

## Method

- **Real routes, real providers.** Requests went through the same FastAPI routes and pipeline the app uses, with `gpt-4.1-mini` and Tavily basic search.
- **A budget per session.** The validation runner (`backend/evaluation/live.py`) opens a named spending allowance with a dollar cap and a search cap. Every call reserves its worst-case cost before it runs, and the allowance is tied to a fingerprint of the code, so results cannot be mixed across versions.
- **No retries.** Model calls were never retried automatically, so a failure stayed visible.
- **Full traces.** Each run saved the extraction, the searches, the pages read, every accepted and rejected citation, token usage and latency. Traces contain third-party page text and are not committed.
- **Reference claims.** `backend/evaluation/real_cases.json` holds twelve starter claims with reference sources (NASA, USGS, the IAU, the Eiffel Tower's operator). Reference verdicts were never shown to the model.

All sessions together cost roughly $0.75 in model usage. A separate scored run of 30 claims is in [SCORECARD.md](SCORECARD.md).

## What went wrong, and what changed

| Finding in a live run | Change |
| --- | --- |
| The model altered quotes: degree symbols became control characters, separate passages were joined with ellipses. Only 5 of the first 11 proposed citations survived checking. | The model no longer writes quotes. Pages are split into numbered excerpts, the model selects IDs, and the app copies the text. |
| The analysis step labelled contradicting evidence as supporting, so correct FALSE verdicts were withheld. | A separate relation check classifies each passage against the claim without seeing the proposed verdict. |
| Extraction dropped the false half of a compound claim because it judged it incorrect. | Claims must map word for word onto the input, and all of the input must be accounted for. |
| A coverage audit rejected false statements for being false. | Textual fidelity is now checked deterministically; the model is not asked whether a claim is true at that stage. |
| In a compound claim, one assertion's verdict was coloured by its neighbour. | The verdict comes from an isolated call that sees only the claim and its verified evidence. |
| Passages about a neighbouring assertion were shown as support. | The relation check excludes passages about other assertions, and reports separate the evidence a verdict used from the rest. |
| One invented excerpt ID withheld an otherwise supported verdict. | Proposals that point at material never supplied are shown as rejected but no longer block a verdict. Misattributed quotes still did, until the scorecard measured what that cost (see [SCORECARD.md](SCORECARD.md)). |
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

### Agents choosing their own steps

The results above were produced when each agent did one fixed pass. After every agent was given its own tools and an orchestrator agent began routing each claim, I ran two statements again on 5 October to check that the agents reach checked verdicts when they plan for themselves.

| Claim | Result | What the agents did |
| --- | --- | --- |
| The Great Wall of China is visible from the Moon with the naked eye | FALSE | Research agent wrote two queries and read six pages; six passages verified; verdict cited five of them from four sites |
| Mount Everest is the highest mountain on Earth above sea level | TRUE | Research agent wrote two queries and read five pages; six passages verified; verdict cited five of them from three sites |

Each claim took 24 model calls and 2 searches, 42 to 49 seconds, and about three cents. In both runs the orchestrator chose the usual route (research, analysis, verification, verdict) in four steps, and the citation verifier decided every long page from the passage around the quote.

What these two runs did not exercise: a second research round, the analyst replacing set-aside selections, a full-page read by the verifier, the verdict agent asking for more evidence, and the claim extractor revising an extraction. None was needed, so those paths are covered by offline tests only. One accepted passage in the Great Wall run described visibility from low orbit and was classed as contradicting the claim, where background would have been more accurate; the verdict did not cite it.

### Source credibility

After sources began to be rated by where they come from, I re-ran the Great Wall statement on 6 October. Its earlier run had cited an Instagram reel.

| Claim | Result | What changed |
| --- | --- | --- |
| The Great Wall of China is visible from the Moon with the naked eye | FALSE | The same reel came back in both searches, labelled as social media, and the research agent did not read it. The verdict cited Britannica and two unrated sites: source strength moderate, source score 60. |

The run took 24 model calls, 2 searches, 53 seconds and about two cents. One of the unrated sites is a magazine from an established publisher that is not on the lists, which shows their limit: a site that is not listed is rated as unknown, not as poor.

The ratings were later aligned with the design brief's eight categories and weights. That changed labels and source scores only: the same pages are accepted or refused as before, so the score of 60 above was computed with the earlier weights.

### Five statements in one submission

After the limit for a typed submission was raised from three claims to five, I submitted five one-sentence statements together on 6 October, through the normal app.

| Claim | Result | Notes |
| --- | --- | --- |
| The Great Wall of China is visible from the Moon with the naked eye | FALSE | Six passages from four sites |
| Mount Everest is the highest mountain on Earth above sea level | Verdict withheld | Three passages from Wikipedia and Britannica were verified. A fourth selection pointed at a list of image captions that did not say what it was cited for; the citation verifier rejected it, and one failed citation withholds the verdict |
| Water boils at 100 degrees Celsius at sea level | TRUE | Four passages from three sites |
| The Eiffel Tower is in Berlin | FALSE | Cited the tower's own site and Wikipedia; also cited pages about a replica in Berlin, which are background at best |
| Humans have walked on the Moon | TRUE | All six passages came from one Wikipedia page, so the report flags a single source |

All five claims were extracted word for word and researched three at a time. The report took 100 seconds, 113 model calls and 12 searches, and cost about 15 cents. Four verdicts were correct and none was wrong. The withheld one shows the cost of the strictest rule: the verifier was right to reject the passage, but the claim lost a verdict that three good passages supported. That rule was changed after the scorecard: a rejected passage is now left out and the rest decide.

### Explanations and the balance of evidence

After each verdict began to carry an explanation and a for/against balance, I ran three scorecard claims again on 6 October and read each explanation against the evidence it cites.

| Claim | Result | Explanation | Balance (sites, weight) |
| --- | --- | --- | --- |
| Mount Everest is the highest mountain on Earth above sea level | TRUE | Three sentences, one each for Wikipedia, Britannica and a third site, each saying what its passage says | Supporting 3 sites, 1.80; contradicting none |
| Vaccines cause autism | FALSE | Four sentences over nine passages. Faithful to them, but it calls the sources "reliable", which is the model's word: all three sites are unrated here and the verdict's confidence is low | Supporting none; contradicting 3 sites, 1.50 |
| Sound cannot travel through a vacuum | PARTIALLY TRUE | Two sentences: the textbook answer from three passages, then the 2023 experiment from two | Supporting 2 sites, 1.40; contradicting 2 sites, 1.00 |

All three explanations passed the citation check, so none was withheld, and I found no sentence that went beyond its evidence apart from that adjective. Three claims is a look, not a measurement. The check confirms that each sentence points at verified evidence the verdict cites; reading them is still the only way to tell whether a sentence is a fair account of it. The three claims cost 11 cents.

### A video from a link

The link feature is covered offline by tests that never start a real download, so on 7 October I tried two from a laptop on a home connection, using `python -m tools.video_link`, which downloads, reports and deletes without any model or search call.

| Link | Result |
| --- | --- |
| A 19-second public YouTube video ("Me at the zoo") | Downloaded: a 0.5 MB MP4, with its title and its 262-character description as the caption |
| A public Instagram Reel, from its share link | Downloaded without signing in: a 0.3 MB MP4, with the post's 158-character caption. The tracking code in the share link was dropped before the request |

That is two links on two sites. A TikTok link has not been tried. One public Reel downloading today does not mean every Reel will: Instagram asks for a sign-in for some posts and at some times, and the app then asks for the file instead.

The same Reel was then pasted into the cloud deployment, with `ALLOW_VIDEO_LINKS` turned on, to run the whole path from a link to a report.

| Step | Result |
| --- | --- |
| Download from the cloud server | Worked, without signing in. The report shows the link with its tracking code removed |
| Reading the video | 12 seconds; speech transcribed, on-screen text read from four frames, and the post's own caption used |
| Claims | Two. The Reel advertises an app feature, so neither is a general fact |
| "I went through all ~2,700 of your saved posts and moved 140 interior-design ones into it" | No verdict: nothing found addresses it. That is the right outcome, but it is one person's account of their own posts and should not have been picked as a claim to research; five pages were read for nothing |
| "Connect your Instagram and @muse and it can sort them by theme…" | PARTIALLY TRUE, low confidence, from three passages on two unrated sites. Two of the three passages describe Instagram's own collections, not the feature the Reel is about, and were accepted as support anyway |

So the link feature works end to end from the deployment for this one Reel. It also shows two weaknesses that clear test videos did not: the claim step picks up personal and promotional statements from a real Reel, and the citation check accepted loosely related passages as support. The claim step has since been told to leave out what a speaker says about their own experience when picking claims from an article or a video; that has not been re-run on this Reel yet. The citation check is unchanged.

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

- **Accuracy at scale.** These sessions and the 30-claim scorecard show the pipeline working on specific, mostly well-known cases.
- **Citation precision.** Accepted citations passed automated checks. Nobody reviewed them by hand.
- **Source quality.** Some verdicts rested on a single page or on weak secondary sources, and one run cited a social media post alongside reference sites. Since then, social media pages are refused as evidence and each verdict reports the strength of its sources. That rating comes from short lists of known sites and has been checked live on one claim only.
- **Real Reels.** The test videos were generated with clear speech. One real Reel has been run, from its link, and it was an advert with no checkable general fact in it. Background music, fast speech and Reels that make factual claims are untested.

## Reproducing a session

From `backend/`, `python -m evaluation.live` prepares a run offline and makes no paid calls. Adding `--allow-paid --allowance NAME --max-usd 0.10 --max-searches 8` starts the validation server under a new allowance. `python -m evaluation.summarize evaluation/results/live-*.json` summarises saved traces without any network calls.
