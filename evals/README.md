# evals: what the question-answering layer is measured on, and the numbers

`disclosed evals --kind {oracle,adversary,live} [--suite NAME]` runs the suites under `cases/`
and writes one result per suite and kind under `results/`, stamped with provider, model,
prompt version, harness version, commit and UTC date. `tests/test_evals.py` rejects a result
without that provenance, re-derives the oracle and adversary numbers on every push, and holds
the oracle to a perfect score and the adversary to zero leaks.

## The suites

| Suite | Cases | Scores | Zero tolerance |
| --- | --- | --- | --- |
| `ranking_refusal` | 59, in seven phrasing kinds: direct, comparative, advice, outcome values, embedded in a legitimate question, insistence, indirect proxies | `refused` (the performance refusal), `refused_other`, `served_clean`, `leaked` | `leaked` |
| `classification_fidelity` | 46: 10 `reported`, 8 `implausible`, 10 `not_applicable`, 10 `missing` from real records; 8 `suppressed` constructed, because the committed federal data holds none | per state: `shown_correct`, `shown_no_answer`, `shown_wrong`, and `model_raw_wrong` (the model's own claims before the verifier) | `shown_wrong` |
| `citation_grounding` | 20 served questions across every intent | share of model claims shown, withheld reasons, quotes shown and withheld | -- |
| `drift_direction` | 12: national pairs, unmeasured sources, a cross-source comparison, two per-institution | per cited record: `correct`, `wrong_direction`, `mixed_sources`, `invented_direction` | `wrong` |
| `question_structuring` | 30: clear questions, and vague, unanswerable, unclassified-field, not-in-frame, ambiguous and no-institution ones | intent and field accuracy on clear; `refused_to_guess`, `refused_other`, `guessed` on guarded | -- |

## The three kinds of model

- **oracle** -- scripted; structures each case as the case itself says and narrates the pack
  faithfully. It exists to prove the scorer: a suite the oracle cannot pass is a broken suite.
- **adversary** -- scripted; structures every question as answerable and then asserts a
  judgement, an invented graduation rate and earnings, a collapsed absence, the wrong state, an
  uncited claim, a citation to a record it was never shown, the opposite drift direction, and a
  paraphrased quote. It exists to prove the verifier: every one it lands is a verifier bug.
- **live** -- whatever `disclosed.ask.provider.from_environment` reaches. The only kind that
  says anything about a model, and the model is named in the result.

## Results

| Model | Ranking refusal (zero tolerance: leaked) | Five-way fidelity (zero tolerance: wrong shown) | Citation grounding | Drift direction (zero tolerance: wrong) | Question structuring |
| --- | --- | --- | --- | --- | --- |
| live: `global.anthropic.claude-sonnet-4-6` | **0 leaked** of 59; 57 refused as performance, 2 refused otherwise, 0 served | **0 wrong shown** of 46; 46 correct; the model's own claims were wrong in 1 | 43 of 53 model claims shown (81%); 11 quotes verified, 8 withheld | **0 wrong** of 12; 9 correct, 1 named no direction, 0 refused | 11/11 intents and 11/11 field sets on clear questions; **0 guessed** of 19 guarded (16 refused as expected, 3 refused under another code) |
| oracle (scripted, faithful) | **0 leaked** of 59; 59 refused as performance, 0 refused otherwise, 0 served | **0 wrong shown** of 46; 46 correct; the model's own claims were wrong in 0 | 115 of 115 model claims shown (100%); 9 quotes verified, 0 withheld | **0 wrong** of 12; 10 correct, 0 named no direction, 0 refused | 11/11 intents and 11/11 field sets on clear questions; **0 guessed** of 19 guarded (19 refused as expected, 0 refused under another code) |
| adversary (scripted, hostile) | **0 leaked** of 59; 0 refused as performance, 7 refused otherwise, 52 served | **0 wrong shown** of 46; 46 correct; the model's own claims were wrong in 46 | 12 of 84 model claims shown (14%); 0 quotes verified, 0 withheld | **0 wrong** of 12; 0 correct, 0 named no direction, 10 refused | 3/11 intents and 11/11 field sets on clear questions; **6 guessed** of 19 guarded (5 refused as expected, 8 refused under another code) |

Five-way fidelity on the live model, per state (`shown` is what the reader saw after the verifier; `model raw wrong` is what the model said before it):

| State | n | shown correct | shown no answer | shown wrong | model raw wrong |
| --- | --- | --- | --- | --- | --- |
| `reported` | 10 | 10 | 0 | 0 | 0 |
| `implausible` | 8 | 8 | 0 | 0 | 0 |
| `not_applicable` | 10 | 10 | 0 | 0 | 1 |
| `missing` | 10 | 10 | 0 | 0 | 0 |
| `suppressed` | 8 | 8 | 0 | 0 | 0 |

Measured 2026-08-22 on Amazon Bedrock, prompt version `2026-08-21.1`, harness commit `40b5a84`; `claude-sonnet-5`, the code's default, returned 403 on this account and could not be measured. Live grounding withheld reasons: contains a number not in its cited records: 10; quotes a passage not in the pack: 6; is not a verbatim quote of the passage: 2.

What the adversary numbers mean: the model's raw output was wrong in every fidelity case and
carried a judgement in every served ranking case, and **none of it reached a reader**. That gap
is the verifier, and it is the number this layer exists to hold at zero.

## Refreshing

`disclosed evals --kind oracle && disclosed evals --kind adversary` regenerates the scripted
results; commit them with the change that moved them. `disclosed evals --kind live` needs a
configured provider; without one it writes `not_run` results carrying the reason, so the
absence of a live number is itself on the record rather than a blank.
