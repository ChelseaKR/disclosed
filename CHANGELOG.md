# Changelog

All notable changes to this project are recorded here, in
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) form. The project uses
[SemVer](https://semver.org/) once it starts tagging releases; nothing has been released yet, so
everything to date lives under Unreleased. The commit history is the fine-grained record; this
file is the human-readable one.

## [Unreleased]

### Added

- Five-way classification of every published value (`REPORTED`, `IMPLAUSIBLE`, `SUPPRESSED`,
  `NOT_APPLICABLE`, `MISSING`), with written rationales for every credible range.
- College Scorecard adapter (600-institution committed capture) and IPEDS adapter (full
  directory plus institutional characteristics), with the sector disagreement between the two
  federal sources reported and deliberately left unresolved.
- Disclosure drift measured as a change in rate against the applicable population, not a change
  in raw counts, with three committed IPEDS collection years as real history.
- National corpus (`data/national.json`) for the fields IPEDS covers, with a `scope` block in
  every payload; `disclosed national` refuses to build from a run that did not cover the
  population.
- Static site generator with per-institution, per-state, methodology, and national pages;
  Lighthouse accessibility gate at 100 with every non-document resource budgeted at zero.
- Citable CSV export with a Table Schema generated in the same pass, plus `CITATION.cff`.
- Daily scheduled snapshot workflow accruing per-field disclosure counts in git.
- Portfolio standards conformance set: security workflow (gitleaks, semgrep, pip-audit),
  Dependabot config, pre-commit hooks, committed `uv.lock` and `.python-version`, ADR log,
  `SECURITY.md`, `CONTRIBUTING.md`, roadmap metrics ledger, and responsible-tech audit record.

### Fixed

- A Scorecard walk that cannot confirm exhaustion now fails instead of reporting national
  figures.
- Drift no longer reports a shrinking directory as a reporting collapse.
- Site claims are computed from the report payload rather than from constants.
- **The SAST gate could not fail.** `semgrep --severity=ERROR` ran 141 rules and found nothing;
  the same scan without the floor runs 321 and finds three, all `WARNING`, and all of them the
  `urllib` calls the job was added to watch. The floor is gone and the three are waived at their
  lines with the reason written beside them. `semgrep scan ... src tests` was also skipping
  every one of the fifteen test modules, silently, under semgrep's bundled ignore list; a
  repository `.semgrepignore` takes the scan from 15 targets to 30.
- **The site-origin guard was outside every gate it protects.** `.github/scripts/` sat outside
  the ruff targets, outside strict mypy's `files`, and outside the coverage source, so the one
  executable deciding whether the published site may name the origin it names was the only
  Python file here that nothing read. It is now linted, typed, and covered, with tests that
  break each of its three checks in turn.
- **The zero-subresource budget was audited over a fixture, not the site.** The claim was
  "every one of the 616 generated pages in `make verify`"; `make verify` built six, from a
  report with no implausible finding in it, so the markup rendered around a finding was never
  parsed. The committed report and national artifact are now rendered whole and every page of
  the result is checked, and the page count in the prose is checked against the build.
- **An unrecognized classification word counted against institutions.** A report written by a
  newer version puts a word the reader does not know into `fields`; both aggregators counted it
  as applicable-but-not-reported, so the published national rate and every drift rate for that
  field fell for a reason that had nothing to do with any publisher. It is now counted exactly
  where an absent field is counted: nowhere.
- **The daily snapshot still reached nobody.** With the diff fixed (#18), five more runs
  (2026-08-16 to 2026-08-20) wrote a snapshot, committed it, and had the push rejected by the
  `protect-main` ruleset for lacking the `verify` status it requires. The Actions app cannot be
  a bypass actor on a user-owned repository and a token-opened pull request never acquires a
  check, so the job now runs `make verify` on its own commit, records the `verify` status on
  that SHA with a link to the run, fast-forwards master, and dispatches the site rebuild that a
  token push would otherwise never start. The post-condition now checks `origin/master`, not
  the runner's clone. Fifteen graded days remain unrecoverable and are recorded as such
  (ADR 0002, `docs/RESPONSIBLE-TECH-AUDITS.md`).
