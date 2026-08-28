# Responsible-Tech Audits: disclosed

Instantiates `STANDARDS/RESPONSIBLE-TECH-FRAMEWORK.md`. First recorded pass: 2026-08-07, as
part of the portfolio standards conformance pass. This is an honest record of what exists,
not an aspiration: where a control is code, the file and test are named; where a control is a
judgement, the place it is written down is named; where something is missing, it says so.

This file is append-only, like the ADR log.

## Applicability

- **A Ethics:** applies (the grading contract is the ethics artifact; findings below)
- **B Bias:** applies (credible ranges and peer groups are judgement calls; findings below)
- **C Privacy:** applies trivially (no personal data anywhere in the pipeline; inventory below)
- **D Transparency:** applies (findings below)
- **E Accessibility:** applies (evidence below, all of it pre-existing and automated)
- **F Security:** applies (declarations below, no blanks)
- **AI-EVAL:** N/A - no LLM or model component; every classification is a deterministic rule
  with a committed rationale
- **I18N:** Applies - deferred to the first public release; `docs/I18N.md`
- **EU AI Act:** out of scope - no AI system is placed on the market or put into service

## A. Ethics

The project grades named institutions in public, so the ethics surface is the grading itself.
The constraints are design decisions already enforced in code and tests, not a separate
checklist:

- **Suppression is never punished.** `SUPPRESSED` and `NOT_APPLICABLE` leave the denominator
  (`src/disclosed/disclosure.py`, `src/disclosed/grading.py`); punishing small-cohort privacy
  protection would push publishers toward disclosing things they should not.
- **No grade is not a zero.** An institution with an empty denominator gets no grade at all
  rather than a zero, and the CSV export carries a `gradeable` column so a blank cell cannot
  be coerced into one (`src/disclosed/dataset.py`).
- **Findings are stated at their strength, not their loudest reading.** The athletics gap is
  explicitly stated as weaker than the net price calculator gap because the statute requires
  preparation, not posting (README; `src/disclosed/crosswalk.py`).
- **The project refuses to overclaim scope.** `disclosed national` fails rather than relabel
  a sample as national; a non-exhaustive Scorecard walk fails rather than report
  (`src/disclosed/national.py`, `src/disclosed/sources/college_scorecard.py`, tested).

Open item: no separate dated consequence-scan narrative exists beyond this section and the
README. If the project gains an audience of graded institutions, a written dispute channel
(beyond GitHub issues) should be added here.

## B. Bias

Where bias could enter: the credible ranges (what counts as `IMPLAUSIBLE`), the peer groups a
finding cites, and the drift threshold.

- Every credible range carries a written rationale a graded institution can argue with
  (`src/disclosed/fields.py`); the README states this as the contract ("a scorecard that
  cannot be disputed line by line is not a scorecard, it is an accusation").
- Peer thresholds guard the evidence in the group, not just its size, after a fix that is in
  the history (`src/disclosed/peers.py`, commit "the peer threshold guarded the size of the
  group, not the evidence in it").
- The drift threshold (2 points of rate) is calibrated against three real IPEDS collection
  years and stated in the README so a reader can disagree with it.
- Sector, which decides peer comparisons, is the one field the two federal sources disagree
  about; the disagreement is reported and deliberately never resolved rather than silently
  preferring a source.

## C. Privacy (data inventory)

There is no personal data in this project. The inventory, exhaustively:

| Data | Source | Personal? | Retention |
| --- | --- | --- | --- |
| Institution-level statistics | College Scorecard public API | No (institutional aggregates; small cohorts arrive already suppressed by the publisher) | Committed capture (`data/sample.json`) |
| Institution directory and characteristics | IPEDS public bulk files | No | Committed zips (`data/HD*.zip`, `data/IC*.zip`) |
| Per-field disclosure counts | derived | No | Committed snapshots (`data/snapshots/`) |
| `DATA_GOV_API_KEY` | api.data.gov signup | Operator's own key | GitHub Actions secret, never in git |

The upstream suppression policy (small cohorts withheld) is respected by design: suppressed
values are never imputed, estimated, or counted against anyone.

## D. Transparency

- Every payload carries a `scope` block naming its corpus and coverage; the site prints that
  sentence rather than a constant, and this is tested.
- The methodology page renders the same rationales the classifier uses.
- The dataset ships with a Table Schema generated in the same pass so the two cannot drift.
- `CITATION.cff` tells a citing reader to carry the coverage with the numbers.
- This repo's own conformance state is declared in the README's Standards Conformance table.

## E. Accessibility

All evidence below is pre-existing and automated; nothing here is a new claim made for this
audit. Cited, not invented:

- **Static suite in the merge gate:** `tests/test_accessibility.py` runs in `make verify` with
  no browser: WCAG AA contrast for every colour pair in both light and dark themes (with a
  completeness test so a new colour fails the build), one `<main>` and one `<h1>` per page, a
  skip link with an existing target, named landmarks, no skipped heading level, caption and
  row headers on every data table, no meaning carried by colour alone.
- **Browser gate:** `.github/workflows/accessibility.yml` scores five page classes with
  Lighthouse and requires exactly 100 on accessibility for each; a missing report or missing
  category is a failure, and the suite is tested to be unable to pass over zero pages.
- **Budgets:** `lighthouse-budget.json` budgets every resource type except the document at
  zero, so a script, font, image, or third-party request is a build failure.

Honest gaps: no human assistive-technology walkthrough (screen reader, keyboard-only) has been
recorded, and no ACR/VPAT exists. Those are the standard's REVIEW artifacts and remain open;
the automated evidence above does not substitute for them and this file does not claim it does.

## F. Security declarations (no blanks)

- **ASVS level:** L1. No authentication, no sessions, no user input surface at runtime; the
  only network code is two read-only fetches of public federal data, and a partial fetch is a
  failure, not data.
- **SAST:** semgrep in `.github/workflows/security.yml` (blocking) plus ruff `S`/bandit rules
  in `make verify`.
- **Secret scanning:** gitleaks in `.github/workflows/security.yml` (blocking, full history)
  and as a pre-commit hook.
- **Dependency scanning:** pip-audit over the exported `uv.lock` set (blocking). The project
  has zero runtime dependencies by design; the dev toolchain is the entire dependency surface.
  Dependabot keeps the lockfile and action pins current.
- **Actions supply chain:** every `uses:` is pinned to a full 40-char commit SHA with a
  version comment; workflows carry least-privilege `permissions:` blocks.
- **Container scan:** N/A - no Dockerfile, no image.
- **SBOM + signing:** N/A while nothing is released; `docs/adr/0001-no-versioned-release.md`.
  The first release must bring both.
- **Secret management policy:** one secret exists (`DATA_GOV_API_KEY`), held only in GitHub
  Actions secrets; the workflow refuses to run without it rather than degrading to a partial
  fetch. Nothing else is secret: all inputs and outputs are public data.
- **VEX:** none required - no known unfixable HIGH/CRITICAL findings are being waived.
- **Branch protection:** a GitHub settings action, not a file in this repo; not yet verified
  as applied. Recorded here as the open item it is.

Last verified: 2026-08-07 · Recheck cadence: quarterly, and on any new data source, hosted
surface, or release.

## Addendum 2026-08-21: branch protection verified, and what the daily job does about it

Appended rather than edited, per the append-only rule above.

- **Branch protection is applied.** Read back from the API on 2026-08-21: ruleset
  `protect-main` (id 20564802), enforcement `active`, target `refs/heads/master`, rules
  `non_fast_forward`, `deletion`, `required_status_checks` with the single context `verify`,
  and **no bypass actors**. `delete_branch_on_merge` was switched on the same day so merged
  branches stop accumulating. The open item in section F above is closed by this line.
- **The daily snapshot was rejected by that ruleset** on five consecutive runs (2026-08-16 to
  2026-08-20, workflow runs 31940376474, 32018856289, 32123887915, 32239848772, 32356139212),
  after ten runs that had committed nothing for a different reason (#18). No bypass actor was
  added: GitHub refuses the Actions app as a bypass actor on a user-owned repository, and a
  deploy key was not minted. The job now runs `make verify` on its own commit, records the
  `verify` status on the SHA with a link to the run, and pushes; `docs/adr/0002` records the
  decision and `tests/test_workflows.py` pins the order of those steps.
- **Unrecoverable days are recorded as unrecoverable.** The fifteen graded reports were written
  to runners that no longer exist. The job logs print per-field fail counts, which are not the
  per-field reported/missing/applicable counts a snapshot holds, so nothing is reconstructed
  from them.
- **Correction, same day.** The self-recorded `verify` status above was tried once (run
  32466145742) and refused: `master` also carries classic branch protection requiring five
  checks from the Actions app (`verify`, `replay`, and the three `security.yml` scans; `strict`,
  `enforce_admins` on), read back from the API after the refusal. The job does not record the
  other four. It now stages its commit on an unprotected ref, dispatches the two gate workflows
  on that ref, waits for them, and pushes; `docs/adr/0003` supersedes 0002 and the test file
  asserts that no step records a status or check run. Sixteen days are unrecoverable, not
  fifteen.
- **A second item in the data inventory.** Every Scorecard walk now records the provenance of
  each page (redacted URL, time, status, size, SHA-256, rate-limit headers). The raw capture is
  a ninety-day workflow artifact; the provenance summary is committed under
  `data/snapshots/scorecard/provenance/`. Neither holds personal data or the key, and the
  `census` workflow greps the capture for the key and refuses to commit if it is there.
- **Second correction, same day.** ADR 0003 merged as #26 and was dispatched for real (run
  32473991532), then rerun once to rule out a fluke. Both were refused by the same rule, both
  times seconds after `gh api repos/.../commits/<sha>/check-runs` confirmed all five required
  checks completed and green on the exact SHA being pushed, read back from the API rather than
  assumed from `gh run watch`'s own exit code. A GitHub Actions check run's check suite belongs
  to the branch that triggered it; classic branch protection's five contexts are each pinned to
  `app_id: 15368` (read back from `repos/.../branches/master/protection`), and a check earned by
  dispatching `verify.yml`/`security.yml` on `snapshot/staging` does not satisfy a requirement
  bound to `master` for the same SHA. It does not fail loudly, either: `gh run watch
  --exit-status` returns zero because the dispatched workflow really did pass, and the failure
  surfaces only at the push two steps later. The job now transcribes each dispatched job's
  already-earned result to a commit status, posted only after that job has been watched to
  completion and only when its own reported conclusion is `success`, quoting the run and job that
  produced it; `docs/adr/0004` supersedes 0003 and the test file pins that the status always
  follows the watch it is transcribing. Eighteen days are unrecoverable, not sixteen.
- **A third item in the data inventory, and the reason the second one existed.** `census` was
  dispatched for real on 2026-08-21 and committed `data/census/scorecard.json`: institution-level
  College Scorecard records (name, state, ownership code, and the six graded fields) for 6,273
  institutions, no different in kind from the 600-institution `data/sample.json` already
  committed and already covered by this audit's public-data exemption -- these are institutional
  records the Department of Education itself publishes, not personal data about any individual.
  Grepped for the API key before commit (workflow step, and reconfirmed here by hand); none
  found. The capture is the input the #17 sampling-frame work (`disclosed census-report`,
  `data/scorecard-census.json`) is built from, and it is the reason a raw capture had to become
  committable at all rather than staying a ninety-day artifact.

## Addendum 2026-08-21: an AI component exists (ADR 0006)

The applicability line above recording **AI-EVAL: N/A** was true when written and is left as
written, because this file is append-only. It stopped being true on 2026-08-21, when the owner
directed that the project add a runtime question-answering layer
(`docs/adr/0006-ai-at-the-edges-the-classified-dataset-is-the-only-evidence.md`).

- **AI-EVAL: Applies.** The component is `disclosed.ask`, an optional subpackage that calls a
  language model at two edges (structuring a question; narrating records) with the project's
  classified evidence as the only evidence and a programmatic verifier before display. The
  evaluation suites are committed under `evals/`: performance-ranking refusal (zero tolerance),
  five-way classification fidelity (scored per state), citation grounding, drift-direction
  fidelity, and question structuring including refusal to guess. Results carry provider, model,
  prompt version, commit and date; a test rejects results without them; a suite not run live is
  recorded as not run.
- **C Privacy:** the service stores no request body and writes no question to disk or to logs.
  A reader's question is sent to the model provider for the duration of the request, which is a
  subprocessor relationship that a deployment must record before the service is exposed. No
  deployment exists at the time of this addendum.
- **D Transparency:** every AI answer is labelled AI-generated and unofficial, states that it is
  about disclosure and not quality, and shows the count of claims withheld by the verifier.
- **EU AI Act:** still out of scope while nothing is deployed; to be revisited in the deployment
  decision.
- The grading pipeline, the committed dataset, and the static pages contain no model-generated
  content. That claim narrows; it does not change.

## Addendum 2026-08-27: a third source is read, and only measured

Appended rather than edited, per the append-only rule above. The recheck cadence in section F
says "on any new data source"; this is that.

- **A fourth item in the data inventory.** `data/registry/organizations.json`: 33,809 Credential
  Registry organization records reduced to what a join needs (registry ctid, published name,
  `ceterms:ipedsID`, `ceterms:opeID`, CTDL organization types, address region, and the host of the
  published web address), walked to the registry's own stated total on 2026-08-27. No personal
  data: these are organizations, published openly by Credential Engine under a public,
  unauthenticated endpoint with no key, no quota and no crawl directives (`/robots.txt` 404s). No
  credential of any kind exists for this source, so there is none to keep out of git. Retention:
  committed, for the reason `data/census/scorecard.json` is committed, that a rerun does not
  reproduce it.
- **D Transparency.** The measurement (`data/registry-join.json`) carries a `scope` block like
  every other payload, and that block says in words that its `states` figure counts distinct
  free-text `ceterms:addressRegion` values, including regions outside the United States, rather
  than states. A count is named after what it counted.
- **A Ethics: nothing is graded from this source.** The adapter reads and reduces; it classifies
  nobody, adds no field, and changes no institution's grade. `docs/adr/0007` records why the
  measurement had to come first: an adapter built on an unmeasured join would render an
  institution the join missed exactly like an institution that disclosed nothing, which is this
  project's own defect class turned inward. Roughly a quarter of the IPEDS directory is not in the
  registry at all, and that limit is written into the ADR as the first thing a future adapter
  inherits.
- **F Security.** No new secret, no new credential, no new network path at grading time: the join
  runs offline from three committed inputs. The one network verb (`disclosed registry-fetch`) is a
  read-only GET against a fixed scheme and host with an urlencoded query, waived at the line for
  the same scanner finding, and with the same reason, as the two existing adapters.

## Addendum 2026-08-27: the budget file is read, and what still is not

Appended rather than edited, per the append-only rule above. Section E states, under
**Budgets**, that "`lighthouse-budget.json` budgets every resource type except the document at
zero, so a script, font, image, or third-party request is a build failure." That sentence was
already known to be wrong about the mechanism when it was written down, and the correction lived
in the README rather than here. Both halves are now accurate, and the difference between them is
recorded rather than smoothed over.

- **E Accessibility, budgets, corrected and extended.** Lighthouse enforces nothing in that file:
  `--budget-path` never makes it exit non-zero and `lighthouse@12` (12.8.2) emits no budget audit,
  reconfirmed on 2026-08-27. The **resource counts** are enforced instead by
  `tests/test_accessibility.py::TestTheResourceBudget` in `make verify`, over one page of each
  kind and again over all 617 pages of the committed build. As of `docs/adr/0008` the
  **transfer sizes** are enforced there too, by `::TestTheTransferSizeBudget`, reading the
  `resourceSizes` lines out of the budget file rather than restating them, and holding the bytes
  the generator writes to the 80 KiB document line. The largest published page is 65.5 KiB, which
  the README states and a test recomputes, so the headroom is a published figure and not an
  assumption.
- **What is still enforced by nothing, said here as well as in the ledger.** The three timing
  lines, largest contentful paint, cumulative layout shift and total blocking time. They need a
  rendering engine. Measured locally on 2026-08-27 with `lighthouse@12` against the built site:
  home 752 ms LCP, state/CA 1,052 ms, against the file's 1,500 ms line, with a layout shift and a
  blocking time of exactly zero on both. Those are a laptop's numbers, not the CI runner's, and
  ADR 0008 records the decision to measure the runner before gating rather than calibrate a gate
  on the wrong machine.
- **The class, not only the instance.** `::TestEveryBudgetLineIsAccountedFor` fails the build on
  any line of `lighthouse-budget.json` that is neither enforced by a named check nor declared
  unenforceable with a written reason, and fails equally on a register entry for a line the file
  no longer carries. The reason this exists is in this file's own history: a control described
  here as automated, which nothing was running.
- **No new data, no new network path, no new secret.** This addendum changes no item in the data
  inventory in section C and adds no runtime code. The measurement above ran locally against a
  site built from committed artifacts.
