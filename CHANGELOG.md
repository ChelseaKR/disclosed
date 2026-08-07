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
