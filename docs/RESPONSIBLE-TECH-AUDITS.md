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
