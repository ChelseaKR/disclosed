# Working in this repository

For anyone, human or agent, changing this project. The README is the spec; `docs/adr/` is the
dated record of why the shape is what it is. This file is the short list of things that are easy
to get wrong.

## What this is

A grader of what US higher-education institutions **disclose**, never of how they perform.
Every published value is classified five ways before anything reads it — `reported`,
`implausible`, `suppressed`, `not_applicable`, `missing` — and those are five different facts.
The defect this project exists to name is an absence rendered as a value: a `0` that was never
a measurement, a blank that was a policy, a "no data" that was "does not apply".

## The gate

`make verify` is the whole local gate and the same target CI runs: ruff (including the bandit
rules), `ruff format --check`, strict mypy, and pytest with a **90% branch-coverage floor** over
`src`, `tests` and `.github/scripts`. Nothing merges red. `uv sync --locked` installs exactly
the lockfile; `uv lock --check` is the drift check. Run `.venv/bin/python`, not `uv run`, for
the gate — a bare `uv run` re-locks silently.

`master` is protected by the `protect-main` ruleset and classic branch protection requiring five
checks. Merge by squash with `(#N)` in the subject; `delete_branch_on_merge` is on. Never force
push. Stage explicit paths; never `git add -A`.

## Data discipline

- Committed artifacts replay byte-for-byte from the committed inputs (`tests/test_replay.py`,
  `tests/test_census_replay.py`). If you change a generator, regenerate the artifact in the same
  commit and let the diff show what moved.
- Every payload carries a `scope` block. A sample is never relabelled as national; the code
  refuses rather than guesses (`disclosed national`, `disclosed census-report`).
- Every figure the README prints is re-derived by `tests/test_doc_counts.py` and
  `tests/test_published_figures.py`. Change a number in prose and the test tells you whether
  the data agrees.
- The site ships no subresource of any kind, enforced over every page from the built bytes.
  Adding one is a visible decision made in the test and the budget file together.

## The AI layer (ADR 0006)

`disclosed.ask` is optional, runtime, and bounded. The rules are code, not prompt wording, and
the tests that enforce them are the ones to read first.

- **The evidence store is the only evidence.** The model structures a question into a lookup
  and narrates the records that come back. It never answers from memory, and it is never shown
  a `reported` value — only classifications, and the published value behind an `implausible`
  one. If you find yourself passing a graduation rate to the model, stop.
- **Every claim cites a record and is verified before display.** Unverifiable claims are
  withheld and counted; they are never shown with a softer word. A claim whose classification
  word disagrees with the cited record is wrong in exactly the way this project names.
- **Performance judgement is refused.** "Which is better", "rank these", "should I go", "what
  is their real rate" — refused, redirected to disclosure, and measured by
  `evals/ranking_refusal` at zero tolerance. A disclosure grade is not a quality grade.
- **The five states are never collapsed.** "They have no graduation rate" over a
  `not_applicable` record is a failure; so is treating an `implausible` zero as a value.
  Measured by `evals/classification_fidelity`, per state.
- **Definitions are quoted, never paraphrased.** From `corpus/`, verbatim, hash-pinned, and the
  quote is verified against the corpus text.
- **Drift keeps its source and its direction.** IPEDS and the Scorecard are never compared to
  each other; direction comes from the project's rate, never from a count the model sees.
- **Credentials from the environment only.** Never write a key to any file. Never commit an
  evaluation result without provider, model, prompt version, commit and date; never commit a
  number that was not measured.
- **Deployment is the owner's decision.** Prepare templates; apply nothing.

## Records

- A decision that changes the project's shape gets an ADR (`docs/adr/`, append-only, MADR).
- `docs/RESPONSIBLE-TECH-AUDITS.md` is append-only: add a dated addendum, never edit a finding.
- `CHANGELOG.md` stays under Unreleased until the first release (ADR 0001).
