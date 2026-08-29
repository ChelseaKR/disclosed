# Improvement plan, audit of 2026-08-28

Working log for an audit pass over this repository. **Nothing here is committed.** Every change
described below is unstaged in the working tree; the owner decides what lands. Logs for every run
quoted here are under `/private/tmp/disclosed-audit/`.

## Standing facts about this working tree

- `HEAD` is `fb761ee` ("The prepared, unapplied deployment shape (#35)"). `origin/master` on
  GitHub is `22f325b` ("Say what each remaining phase is blocked on (#51)"). **This tree is 16
  commits behind the remote**, and the remote-tracking ref in this clone is itself stale, at
  `a42343c` (#48). Nothing was fetched, checked out or merged.
- Everything this audit touches was compared against the remote before being changed.
  `src/disclosed/fields.py`, `drift.py`, `disclosure.py`, `grading.py` and all of
  `src/disclosed/ask/` are byte-identical between `fb761ee` and `origin/master`.
  `cli.py`, `README.md`, `Makefile`, `CHANGELOG.md`, `docs/ROADMAP.md`,
  `docs/RESPONSIBLE-TECH-AUDITS.md`, `tests/test_accessibility.py`, `tests/test_cli.py` and
  `tests/test_doc_counts.py` have all moved upstream, so nothing in this pass edits them.
- `evals/` is untracked and was left exactly as found. It is PR #38's content.
- Baseline before any change: `make verify` exit 0, **696 passed, 98.43% branch coverage**.
- After the change: `make verify` exit 0, **721 passed, 98.43% branch coverage**.

## Open issues, classified

| # | Title (short) | Classification | Action taken |
|---|---|---|---|
| 36 | Admission rate vs the open-admissions glossary sentence | **Real defect in the grading contract; owner decision** | Not changed. Citation verified verbatim against `corpus/passages.json`. |
| 37 | `ATHURL` labelled EADA, IPEDS titles it Student-Right-to-Know | **Real mislabel; owner decision** | Not changed. Citation verified verbatim against `corpus/passages.json`. |
| 39 | `_COLLAPSE` misses "does not report" | **Real defect** | Reproduced against the real types. **Fixed in open PR #46**; not duplicated. |
| 40 | Fidelity check skipped on a drift-only citation | **Real defect** | Reproduced against the real types. **Fixed in open PR #46**; not duplicated. |
| 41 | `direction` says "lost" at `rate_change == 0.0` | **Real defect** | Reproduced against the real types. **Fixed in open PR #46**; not duplicated. |

Every claim each issue makes about the code was checked rather than taken on trust, including the
line numbers, the quoted source text and the reproduction steps. All five hold. The reproduction
output is in `/private/tmp/disclosed-audit/repro-issues-out.log`.

#36 and #37 both ask a question this pass is not entitled to answer. #36 would move some unknown
share of 4,363 census institutions from `missing` to `not_applicable` and change the README's
headline figure; #37 would change which statute a published finding is argued from. Both are
changes to what the project asserts about other people, on evidence that supports more than one
reading, and both issues say so themselves. They stay open.

## Open PRs

| # | Title | Checks | Note |
|---|---|---|---|
| 38 | Evaluation suites, live Bedrock results | all green | Touches `src/disclosed/ask/verify.py`. Avoided. |
| 46 | Fix for #39/#40/#41 | all green | Touches `verify.py`, `narrate.py`, `drift.py`. Avoided. |
| 49 | Timing budget gate (ADR 0010) | `lighthouse` **red** | Its own new gate failing on its own branch, not a master problem. |

## CI diagnosis

One failure in the last 20 runs of any workflow: **run 33140352353**, `accessibility` /
`lighthouse`, on master at `22f325b`. Log: `/private/tmp/disclosed-audit/ci-33140352353-failed.log`.

The `Audit` step ran for 36 seconds and ended with one line:

    Unable to connect to Chrome

Nothing else in the 289-line log is an error. Chrome *was* spawned: the runner's cleanup reports
orphan `chrome` and two `chrome_crashpad_handler` processes. It never accepted a DevTools
connection inside chrome-launcher's window, so no report was written and the scorer step was
skipped.

This is **infrastructure, not code, and not billing**. The evidence:

- The identical tree passed the same job seven seconds earlier in run 33140346789, and again on
  the PR run 33140342881.
- 26 of the last 30 `accessibility` runs are green. The only other red is PR #49's own new timing
  gate on its own branch (`total-blocking-time is 34 against a budget of 0`), which is that PR's
  to resolve.
- The log contains no billing, payment, quota or spending-limit text of any kind.

**Recommendation, and it is a maintainer decision, not something this pass applied:** re-run the
job. If the flake recurs, the honest fix is at the launch, not at the gate. Nothing here waives,
silences, `|| true`s or narrows anything to make it pass, and no retry was added: a retry wrapped
around the whole audit would also swallow a genuine load-related regression, which is the wrong
trade for a job whose bar is 100 with no exceptions.

## The governing rule: gates that cannot fail

Checked by name, as instructed.

| Named shape | Present here? |
|---|---|
| `gitleaks detect --source .` scans history, not the working tree | **Not present.** CI uses `gitleaks/gitleaks-action` with `fetch-depth: 0`; pre-commit scans the staged diff. `security.yml` says accurately that it "scans history for committed secrets". Nothing claims the working tree is scanned. **But** `fetch-depth: 0` was a comment nobody asserted, and without it the action clones one commit and reports success over everything before it. Now pinned. |
| `uv sync --frozen` never reads `pyproject.toml` | **Already fixed** in #19, with the measurement written into `verify.yml`. **The fix was unpinned**, and `--frozen` still appears in the file as the thing being argued against. Now pinned, and the drift itself is now checkable offline inside `make verify`. |
| `semgrep test` exits 0 with no test files | **Not present.** The job is `semgrep scan --error src tests`, and `tests/test_workflows.py` already pins `--error`, the absence of `--severity`, and the empty `.semgrepignore`. |
| A linter whose file scope excluded the scripts that are the gates | **Already fixed** for `.github/scripts` in #23, in three places (`LINTED`, mypy `files`, coverage `source`). **All three were unpinned.** Now pinned. |
| A CI stage with no `make` target, so `make verify` is green on a tree CI rejects | **Present.** `uv lock --check` runs only in `verify.yml`. `make verify` runs no uv, so a dependency added to `pyproject.toml` and never locked was a local pass and a CI failure, while the README calls `make verify` "the single local gate and the same target CI runs". Closed by doing the same comparison from the two committed files. |
| A test asserting a bound using data too far apart to exercise it | **Not found.** Checked the drift threshold tests (`abs(rate_change) < 0.01` for every non-athletics field *and* `>= 0.01` for athletics: both directions asserted), the rate-limit tests (`per_client_per_hour=2`, `per_day=3`, both windows crossed), the deploy template bounds (`Timeout <= 60` at 60, `MemorySize <= 1024` at 1024, `1 <= concurrency <= 2` at 2), and the contrast table. None of them has the shape. |

### Measured, not reasoned about

With four decisions this repository argues for at length undone at once:

* `LINTED` reduced from `src tests .github/scripts` to `src tests`
* `--cov-fail-under=90` reduced to `1`, and `.github/scripts` dropped from both the coverage
  source and mypy's `files`
* `verify.yml`'s `uv lock --check` step deleted and `uv sync --locked` returned to `--frozen`
* the accessibility scorer's explicit list of six reports returned to `ls /tmp/*.json`

`make verify` exited **0** with **696 passed**, exactly as on the unmodified tree
(`/private/tmp/disclosed-audit/holes-before-verify.log`). Three of them were then checked
individually against the whole pre-existing suite, and it passed every time
(`/private/tmp/disclosed-audit/prove-holes.log`).

## Phases

1. **Read the ground.** Done. Issues, PRs, all six workflows, the Makefile, `pyproject.toml`, the
   gate script, six ADRs, and the test modules that gate the published numbers.
2. **Diagnose the CI failure from its own log.** Done, above.
3. **Reproduce every issue claim rather than trusting it.** Done. All five hold.
4. **Hunt gates that cannot fail, by the six named shapes and then generally.** Done, above.
5. **Close what is closeable without duplicating in-flight work.** Done:
   `tests/test_gate_configuration.py`.
6. **Prove every new guard in both directions.** Done: 28 mutations applied one at a time, each
   caught, tree restored byte-for-byte after each
   (`/private/tmp/disclosed-audit/break-gates-results.log`).
7. **Re-run the gate.** Done: `make verify` exit 0, 721 passed, 98.43%.

## File by file

### `tests/test_gate_configuration.py` (new, 25 tests)

The only source change in this pass. A new module rather than an addition to
`tests/test_workflows.py`, because that module is being rewritten in PR #49 and a gate that only
exists once a merge conflict is resolved correctly is not one.

It pins the configuration that decides what every other gate runs over:

- **Makefile.** `verify` is lint + typecheck + test; `LINTED` names all three gated directories;
  the lint step checks formatting over the same set as the rules; no recipe line starts with
  make's `-` (its `|| true`) or ends with `|| true`.
- **`pyproject.toml`.** Coverage floor at least the 90% three documents state; `--cov-branch`
  present; coverage measures `.github/scripts` as well as the package; mypy strict and reaching
  `.github/scripts`; ruff's `select` still carries `S` (bandit) and `C90`; complexity ceiling 10;
  `S101` in `tests` is the only rule waived by path.
- **Lockfile drift, offline.** Every requirement `pyproject.toml` declares is recorded in
  `uv.lock`'s own `[package.metadata]` block, in both directions, and every locked requirement
  resolves to a package entry. This is the comparison `uv lock --check` performs, done from the
  two committed files with no uv, no network and no resolver, so `make verify` can now fail on
  the exact drift CI catches.
- **`verify.yml`.** `uv lock --check` runs, and runs before the install; `uv sync --frozen`
  appears in no executable line; `uv sync --locked` does; CI runs `make verify` rather than a
  second copy of it; nothing swallows its result.
- **`security.yml`.** The gitleaks job still checks out the whole history and still runs gitleaks.
- **`accessibility.yml`.** Only the scorer step, because the audit steps above it are being
  rewritten in PR #49. The reports are named rather than globbed, at least six of them; the count
  found is compared against the count expected; a missing category scores `missing` rather than
  defaulting to a pass; a report lighthouse never wrote is a failure.
- **`.pre-commit-config.yaml`.** The ruff hook's `rev` equals the ruff version `uv.lock` resolves,
  which the file's own comment demands and nothing checked; the hooks reach the same directories
  the Makefile lints; the push hook is `make verify` and not a subset of it.

Checked for forward compatibility: the module passes unchanged against `origin/master`'s versions
of all seven files (16 commits ahead of this tree) and against PR #49's `accessibility.yml`. It
lands cleanly whatever the rebase order.

One guard in this module could not fail when first written, and is recorded here rather than
quietly corrected: `test_the_secret_scan_still_runs_the_scanner` searched the raw job text for
"gitleaks", and that word appears in the job's own comment, so replacing the action with a
different one left the assertion satisfied. It now reads the executable lines only. That is the
same mistake `tests/test_workflows.py` documents in its `_steps` helper, made again one file over.

## Owed when this lands

A `CHANGELOG.md` entry under Unreleased. **Deliberately not written here:** this tree's
`CHANGELOG.md` is 16 commits behind and four merged PRs have added entries above the point an
edit would land, so an edit made now would read as a revert of theirs. Suggested text for whoever
commits this:

> - `tests/test_gate_configuration.py`: the gate's own configuration is now gated. The Makefile's
>   lint set, the coverage floor and its source, mypy's target list, ruff's rule set,
>   `verify.yml`'s lockfile flags, the gitleaks job's history depth, the accessibility scorer's
>   explicit report list and the pre-commit ruff pin were each a decision argued for in a comment
>   and asserted nowhere; four of them were undone at once and `make verify` stayed green over
>   696 passing tests. It also performs `uv lock --check`'s comparison from the two committed
>   files, so the lockfile-drift gate that existed only in CI now exists in `make verify` too.

## What remains blocked, and on whom

| Item | Blocked on |
|---|---|
| Issue #36, admission rate and open admissions | **Owner.** Changing the grading contract moves the README's headline figure and is out of scope for anyone but the maintainer. |
| Issue #37, `ATHURL`'s statute and label | **Owner.** Two published federal readings; picking one is an assertion about other institutions. |
| Issues #39, #40, #41 | **Review and merge of PR #46**, which fixes all three. |
| The master CI failure | **Nothing but a re-run.** If it recurs it is a maintainer decision about the Lighthouse job's launch, not about the gate. |
| PR #49's red `lighthouse` check | **That PR's author.** Its own new timing gate reports `total-blocking-time is 34 against a budget of 0`. |
| This tree being 16 commits behind | **Owner**, who has withheld commit permission for this pass. Nothing was fetched, staged, committed or pushed. |
