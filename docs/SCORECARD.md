# Scorecard

How the system did on 30 claims with known answers, set against the targets in the design brief it was built from. Run on 6 October 2026 with `gpt-4.1-mini`, Tavily search and every agent planning its own steps.

This is a small set of mostly well-known facts and myths, labelled by me. It shows how the system behaves on these cases. It is not an accuracy rate for claims in general.

## Result

| Measure | Result | Brief's target | |
| --- | --- | --- | --- |
| Accuracy (right verdict) | 25 of 30 = 83% | 85% or more | not met |
| Wrong verdicts issued | 2 (both "partially true" where I expected "true"; none said the opposite of the truth) | | |
| No verdict when one was expected | 3 of 29 = 10% | under 15% | met |
| Citation checks failed | 4 of 167 passages = 2% | under 5% | met |
| Cost per claim | $0.029 | under $0.15 | met |
| Time per claim | median 46 s, 95th percentile 66 s | under 120 s | met |
| Verdicts citing three or more sites | 10 of 26 | every verdict | not met |

When the system did issue a verdict, it was right 25 times out of 27.

## It took two passes

The first pass ran all 30 claims three at a time and scored 19 of 30 (63%). Seven of the eleven misses were not judgements at all: a model call was refused part-way through and the whole claim was dropped. The first nine claims passed before refusals began, and the run was sending about 183,000 tokens a minute, so the likely cause is the provider's per-minute rate limit. The app does not record the provider's own error text, so that is an inference.

I changed the app to wait and retry when the provider limits requests, then ran those seven claims again. All seven got a verdict and all seven were right, which gives the 25 of 30 above. The second pass was only seven claims, so it did not put the retry under the load that caused the problem; that is tested offline only.

Both passes together cost $1.03 in model usage and 96 searches.

## The five misses

| Claim | Expected | Result | What happened |
| --- | --- | --- | --- |
| An adult human body has 206 bones. | TRUE | PARTIALLY TRUE | It cited sources saying 206 is the usual count but some adults have up to 213. A defensible answer; my label was stricter. |
| Sound cannot travel through a vacuum. | TRUE | PARTIALLY TRUE | It found a 2023 experiment that passed sound across a tiny vacuum gap between two crystals, and weighed that against the textbook statement. |
| The Sun orbits the Earth. | FALSE | Withheld: conflicting evidence | Passages describing the old Earth-centred model were classed as supporting the claim, so the evidence looked split. A real weakness: it does not separate "people once believed" from "this is so". |
| The Sahara is the largest desert on Earth. | FALSE or MISLEADING | Withheld: a citation failed | Five passages were verified; a sixth was rejected, and one failed citation withholds the verdict. |
| The current IAU classification lists Pluto as the ninth planet. | OUTDATED or FALSE | Withheld: a citation failed | Three passages were verified; three more did not support what they were cited for. |

## What the strict citation rule costs

One failed citation withholds a claim's verdict, even when other passages passed every check. For each claim withheld that way, the scorecard asks the verdict agent what it would conclude from the passages that did pass.

Both such claims (Sahara and Pluto) would have come out FALSE, which is right. Dropping the failed passage instead of withholding the verdict would have given 27 of 30 (90%) with no additional wrong verdicts. On this evidence the rule costs accuracy without preventing an error, but two cases are too few to settle it.

## All 30 claims

| Claim | Expected | Result | |
| --- | --- | --- | --- |
| The Moon orbits Earth. | TRUE | TRUE | right |
| Water freezes at 0 degrees Celsius at standard atmospheric pressure. | TRUE | TRUE | right |
| Water boils at 100 degrees Celsius at sea level. | TRUE | TRUE | right |
| The Eiffel Tower is located in Paris. | TRUE | TRUE | right |
| The Eiffel Tower was built for the 1889 Paris World's Fair. | TRUE | TRUE | right |
| Mount Everest is the highest mountain on Earth above sea level. | TRUE | TRUE | right |
| The Pacific Ocean is the largest ocean on Earth. | TRUE | TRUE | right |
| An adult human body has 206 bones. | TRUE | PARTIALLY TRUE | wrong |
| The Amazon River flows through Brazil. | TRUE | TRUE | right |
| Humans have walked on the Moon. | TRUE | TRUE | right (second pass) |
| Light travels faster than sound. | TRUE | TRUE | right |
| Venus is the hottest planet in the Solar System. | TRUE | TRUE | right |
| Antarctica is the coldest continent on Earth. | TRUE | TRUE | right (second pass) |
| Sound cannot travel through a vacuum. | TRUE | PARTIALLY TRUE | wrong |
| The Great Wall of China is visible from the Moon with the naked eye. | FALSE | FALSE | right |
| The Moon produces its own visible light like the Sun. | FALSE | FALSE | right |
| The Moon never rotates. | FALSE | FALSE | right |
| Lightning never strikes the same place twice. | FALSE | FALSE | right |
| Sharks are mammals. | FALSE | FALSE | right (second pass) |
| The Eiffel Tower is in Berlin. | FALSE | FALSE | right |
| Humans use only 10 percent of their brains. | FALSE | FALSE | right (second pass) |
| Goldfish have a memory of only three seconds. | FALSE | FALSE | right |
| Bats are blind. | FALSE | FALSE | right (second pass) |
| The Sun orbits the Earth. | FALSE | withheld (conflicting evidence) | no verdict |
| Vaccines cause autism. | FALSE | FALSE | right |
| Bulls are enraged by the color red. | FALSE | FALSE | right |
| The Sahara is the largest desert on Earth. | FALSE, MISLEADING or PARTIALLY TRUE | withheld (citation failed) | no verdict (second pass) |
| Water freezes at 0 degrees Celsius under every condition. | PARTIALLY TRUE, FALSE or MISLEADING | FALSE | right (second pass) |
| The current IAU classification lists Pluto as the ninth planet. | OUTDATED or FALSE | withheld (citation failed) | no verdict |
| Exactly 123 people worldwide dreamed about the Eiffel Tower last night. | UNVERIFIABLE | UNVERIFIABLE | right |

## How it is scored

- **Right:** a verdict was issued and it is one of the accepted answers for that claim. Three claims accept more than one label, because more than one is defensible. For the claim that cannot be checked, issuing no verdict is the right answer.
- **Wrong:** a verdict was issued and it is not an accepted answer.
- **No verdict:** a verdict was expected, but it was withheld or came back UNVERIFIABLE. This is never counted as wrong, and never as right.
- **Citation checks failed:** passages the citation verifier rejected, out of all the passages it was given.
- The reference answer is never shown to the model.

## What this does not show

- **Hard claims.** These are textbook facts and popular myths. Statistics, recent events, quotes and politically contested claims are not covered.
- **Stability.** Each claim was run once. The model and the search results vary from run to run, so a repeat could differ by a claim or two.
- **Independent labels.** I wrote the expected answers. The two "wrong" results show that some of them could be argued.
- **Source independence.** Fewer than half the verdicts cited three sites, three cited only one, and five rested entirely on sites the credibility lists do not rate.
- **Videos and articles.** Only typed statements were scored.

## Run it

From `backend/`:

```sh
python -m evaluation.scorecard                                    # offline: lists the cases, summarises saved runs
python -m evaluation.scorecard --allow-paid --name NAME --max-usd 1.00
```

The cases are in [`backend/evaluation/scorecard_cases.json`](../backend/evaluation/scorecard_cases.json). A run stops starting new claims near its spending cap, and running the same name again continues where it stopped.
