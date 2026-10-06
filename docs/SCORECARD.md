# Scorecard

How the system did on 30 claims with known answers, set against the targets in the design brief it was built from. Run on 6 October 2026 with `gpt-4.1-mini`, Tavily search and every agent planning its own steps.

The claims were run twice: once as the system stood, and once after three changes the first run pointed to. This page reports both.

This is a small set of mostly well-known facts and myths, labelled by me. It shows how the system behaves on these cases. It is not an accuracy rate for claims in general.

## Result

| Measure | First run | After the changes | Brief's target | |
| --- | --- | --- | --- | --- |
| Accuracy (right verdict) | 25 of 30 = 83% | 26 of 30 = 87% | 85% or more | met |
| Wrong verdicts issued | 2 | 2 | | |
| No verdict when one was expected | 3 of 29 = 10% | 2 of 29 = 7% | under 15% | met |
| Citation checks failed | 4 of 167 passages = 2% | 12 of 198 = 6% | under 5% | not met |
| Cost per claim | $0.029 | $0.034 | under $0.15 | met |
| Time per claim | median 46 s, 95th percentile 66 s | median 64 s, 95th percentile 110 s | under 120 s | met |
| Verdicts citing three or more sites | 10 of 26 | 26 of 27 | every verdict | not met, by one |

In neither run did a verdict say the opposite of the truth. All four wrong verdicts were "partially true" where I expected "true".

The accuracy difference is one claim, and each claim was run once per run, so it is within what a repeat could change. The differences that are large enough to trust are the number of sites behind each verdict, the time per claim, and what happened to claims with a rejected citation (below).

## What the first run led to

The first run had five misses. Two were verdicts withheld because one citation failed its check, one was a real flaw in how evidence was labelled, and fewer than half the verdicts rested on three sites. Three changes followed.

**1. A failed citation is left out instead of withholding the verdict.** Until then, one rejected passage withheld a claim's verdict even when other passages passed every check. The first run measured what that cost: for both claims withheld that way, the verdict agent was asked what it would conclude from the passages that did pass, and both answers were right. Now a rejected passage is listed as rejected and the verdict rests on the rest. Two cases still withhold: a check that could not be run at all, and a passage the citation verifier places on the other side of the claim from the analyst, because leaving that one out could hide evidence against the verdict.

**2. Three different sites is a goal.** In the first run the analyst often took all six of its passages from one page, with four or five pages read. It is now asked to spread its selections across sites, and a claim whose verified evidence comes from fewer than three sites gets one more research round that looks beyond the sites already used. If that finds nothing, the verdict is still issued and the report says how many sites it rests on. Sites are counted by registered domain, so two sections of one site count once.

**3. A reported belief is not support.** The two checks that decide which side a passage is on were told that a passage describing what people once believed, or what a superseded model said, does not support a claim.

## What the changes did

**Leaving out failed citations mattered more than expected.** In the second run nine claims had at least one rejected passage. Under the old rule all nine verdicts would have been withheld, and the run would have scored 19 of 30. Seven of those nine verdicts were right. The other two are the run's two wrong verdicts, so the old rule would also have withheld those: the change bought seven right answers and let through two "partially true" answers that should have been "true". The safeguard for a passage placed on the other side never fired.

**The site goal worked.** Verdicts resting on three or more sites went from 10 of 26 to 26 of 27. Most claims reached three sites in the first research round, just from the analyst spreading its selections; ten claims had a second round. The cost is time: the median rose from 46 to 64 seconds and the 95th percentile from 66 to 110. One claim took 126 seconds without a second round, which is over the brief's limit and looks like the provider slowing down under three claims at once.

**More passages were rejected.** Citation checks failed for 6% of passages, up from 2% and now over the brief's 5%. Spreading selections across sites brings in weaker passages. Some of the twelve rejections are also the verifier's own mistakes: in several, its written explanation says the passage has the right stance and its answer says it does not. Because the answer fields come before the explanation in its output, it may be committing to an answer before reasoning. That is a guess, not something I tested. With the new rule these rejections no longer cost a verdict.

**The belief fix did not work.** The claim it was written for is still withheld, and a second claim now fails the same way:

| Claim | Expected | Second run | What happened |
| --- | --- | --- | --- |
| The Sun orbits the Earth. | FALSE | Withheld: conflicting evidence | Better than before, but not fixed. Three passages about the old Earth-centred model were filed as support in the first run; one still was in the second ("Under most geocentric models, the Sun ... orbit Earth"), and one is enough to make the evidence look split. |
| Humans use only 10 percent of their brains. | FALSE | Withheld: conflicting evidence | Right in the first run. This time the second research round brought in the sentence "Many believe that a person only ever uses 10 percent of their brain", which was filed as support, against eight passages that contradict the claim. |
| Water freezes at 0 degrees Celsius at standard atmospheric pressure. | TRUE | PARTIALLY TRUE | Right in the first run. A passage about the freezing point being 0.01 degrees below water's triple point was filed as contradicting the claim. |
| Sound cannot travel through a vacuum. | TRUE | PARTIALLY TRUE | Same as the first run: it weighed a 2023 experiment that passed sound across a tiny vacuum gap against the textbook statement. |

Three of these four come from the same step: the check that decides whether a passage supports or contradicts the claim. Telling it in words to separate a reported belief from a statement of fact was not enough. The more passages a claim gathers, the more chances that one is filed on the wrong side, and a single such passage withholds an otherwise clear verdict. That step is the next thing to fix.

Three claims changed for the better: the two that were withheld over a failed citation now have the right verdict (Sahara, Pluto), and the claim about 206 bones came back TRUE instead of PARTIALLY TRUE.

## The first run took two passes

The first pass ran all 30 claims three at a time and scored 19 of 30. Seven of the eleven misses were not judgements at all: a model call was refused part-way through and the whole claim was dropped. The first nine claims passed before refusals began, and the run was sending about 183,000 tokens a minute, so the likely cause is the provider's per-minute rate limit. The app does not record the provider's own error text, so that is an inference.

I changed the app to wait and retry when the provider limits requests, then ran those seven claims again. All seven got a verdict and all seven were right, which gives the 25 of 30 above.

The second run then ran all 30 claims three at a time with the retry in place. At least one claim was still interrupted by a provider failure, and was run again automatically.

The five misses of the first run:

| Claim | Expected | First run | What happened |
| --- | --- | --- | --- |
| An adult human body has 206 bones. | TRUE | PARTIALLY TRUE | It cited sources saying 206 is the usual count but some adults have up to 213. A defensible answer; my label was stricter. |
| Sound cannot travel through a vacuum. | TRUE | PARTIALLY TRUE | The 2023 experiment described above. |
| The Sun orbits the Earth. | FALSE | Withheld: conflicting evidence | Passages describing the old Earth-centred model were filed as supporting the claim, so the evidence looked split. |
| The Sahara is the largest desert on Earth. | FALSE or MISLEADING | Withheld: a citation failed | Five passages were verified; a sixth was rejected, and at that time one failed citation withheld the verdict. |
| The current IAU classification lists Pluto as the ninth planet. | OUTDATED or FALSE | Withheld: a citation failed | Three passages were verified; three more did not support what they were cited for. |

The first run cost $1.03 in model usage and 96 searches over its two passes. The second run cost $1.08 and 101 searches.

## All 30 claims

| Claim | Expected | First run | After the changes |
| --- | --- | --- | --- |
| The Moon orbits Earth. | TRUE | TRUE | TRUE |
| Water freezes at 0 degrees Celsius at standard atmospheric pressure. | TRUE | TRUE | **PARTIALLY TRUE (wrong)** |
| Water boils at 100 degrees Celsius at sea level. | TRUE | TRUE | TRUE |
| The Eiffel Tower is located in Paris. | TRUE | TRUE | TRUE |
| The Eiffel Tower was built for the 1889 Paris World's Fair. | TRUE | TRUE | TRUE |
| Mount Everest is the highest mountain on Earth above sea level. | TRUE | TRUE | TRUE |
| The Pacific Ocean is the largest ocean on Earth. | TRUE | TRUE | TRUE |
| An adult human body has 206 bones. | TRUE | **PARTIALLY TRUE (wrong)** | TRUE |
| The Amazon River flows through Brazil. | TRUE | TRUE | TRUE |
| Humans have walked on the Moon. | TRUE | TRUE | TRUE |
| Light travels faster than sound. | TRUE | TRUE | TRUE |
| Venus is the hottest planet in the Solar System. | TRUE | TRUE | TRUE |
| Antarctica is the coldest continent on Earth. | TRUE | TRUE | TRUE |
| Sound cannot travel through a vacuum. | TRUE | **PARTIALLY TRUE (wrong)** | **PARTIALLY TRUE (wrong)** |
| The Great Wall of China is visible from the Moon with the naked eye. | FALSE | FALSE | FALSE |
| The Moon produces its own visible light like the Sun. | FALSE | FALSE | FALSE |
| The Moon never rotates. | FALSE | FALSE | FALSE |
| Lightning never strikes the same place twice. | FALSE | FALSE | FALSE |
| Sharks are mammals. | FALSE | FALSE | FALSE |
| The Eiffel Tower is in Berlin. | FALSE | FALSE | FALSE |
| Humans use only 10 percent of their brains. | FALSE | FALSE | **withheld (conflicting evidence)** |
| Goldfish have a memory of only three seconds. | FALSE | FALSE | FALSE |
| Bats are blind. | FALSE | FALSE | FALSE |
| The Sun orbits the Earth. | FALSE | **withheld (conflicting evidence)** | **withheld (conflicting evidence)** |
| Vaccines cause autism. | FALSE | FALSE | FALSE |
| Bulls are enraged by the color red. | FALSE | FALSE | FALSE |
| The Sahara is the largest desert on Earth. | FALSE, MISLEADING or PARTIALLY TRUE | **withheld (citation failed)** | FALSE |
| Water freezes at 0 degrees Celsius under every condition. | PARTIALLY TRUE, FALSE or MISLEADING | FALSE | FALSE |
| The current IAU classification lists Pluto as the ninth planet. | OUTDATED or FALSE | **withheld (citation failed)** | FALSE |
| Exactly 123 people worldwide dreamed about the Eiffel Tower last night. | UNVERIFIABLE | UNVERIFIABLE | UNVERIFIABLE |

## How it is scored

- **Right:** a verdict was issued and it is one of the accepted answers for that claim. Three claims accept more than one label, because more than one is defensible. For the claim that cannot be checked, issuing no verdict is the right answer.
- **Wrong:** a verdict was issued and it is not an accepted answer.
- **No verdict:** a verdict was expected, but it was withheld or came back UNVERIFIABLE. This is never counted as wrong, and never as right.
- **Citation checks failed:** passages the citation verifier rejected, out of all the passages it was given.
- **Sites:** the different registered domains among the passages a verdict cites.
- The reference answer is never shown to the model.

## What this does not show

- **A fair test of the changes.** The three changes were chosen by looking at these same 30 claims, and then scored on them. A fresh set of claims would be the fair test, and would likely score lower.
- **Stability.** Each claim was run once per run. The model and the search results vary, so some of the claim-by-claim differences between the runs are chance rather than the changes.
- **Hard claims.** These are textbook facts and popular myths. Statistics, recent events, quotes and politically contested claims are not covered.
- **Independent labels.** I wrote the expected answers. The "partially true" results show that some of them could be argued.
- **Independent sources.** Three different sites are not three independent sources: sites copy each other, and nothing here checks for that. Five verdicts in each run rested entirely on sites the credibility lists do not rate.
- **Videos and articles.** Only typed statements were scored.

## Run it

From `backend/`:

```sh
python -m evaluation.scorecard                                    # offline: lists the cases, summarises saved runs
python -m evaluation.scorecard --allow-paid --name NAME --max-usd 1.25
```

The cases are in [`backend/evaluation/scorecard_cases.json`](../backend/evaluation/scorecard_cases.json). A run stops starting new claims near its spending cap, and running the same name again continues where it stopped and retries any claim a provider interrupted.
