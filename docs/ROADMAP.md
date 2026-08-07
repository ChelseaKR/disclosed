# Roadmap and metrics ledger

The README is the spec: what the project grades, why absence is data, what is a sample and
what is national. This file carries the declarations the portfolio standards ask each repo to
state in one place: the observability tier, the metrics ledger, and the applicability of the
optional CI stages.

## Where the project is

Working, pre-release (`0.1.0.dev0`, status: Beta). Both federal adapters are live, the drift
measurement has three real IPEDS collection years behind it, the national corpus is committed,
and the site holds a Lighthouse accessibility score of 100 under zero resource budgets. Next
milestones, in order:

1. **Credential Registry (CTDL) access.** The public search endpoint returns `x-total: 0` for
   every query shape tried; the adapter stays unwritten until access is confirmed (README,
   Sources). No stub adapters.
2. **Veterans-page grading rule.** The IPEDS characteristics file could supply an
   applicability rule, but no universal publication duty exists, so it stays ungraded until a
   defensible rule does (README).
3. **First release.** Supersedes `docs/adr/0001-no-versioned-release.md` and brings the
   hardened release workflow with it.

## Observability

Tier C (library/CLI). The project is a CLI that reads public archives and writes files; the
site is a build artifact, not a hosted service. There is no server, no accounts, no telemetry,
and no production boundary to observe. Operational visibility is the CI record itself: the
daily snapshot workflow fails loudly (missing API key, non-exhaustive walk) rather than
committing a partial truth, which for this shape of project is the observability that matters.
OTel instrumentation re-enters scope if a hosted surface ever ships.

## Metrics ledger

Per QUALITY-AND-METRICS-STANDARD's ledger shape. Values as measured 2026-08-07.

| Metric | Target | Measured by | Gate |
|---|---|---|---|
| Branch coverage | >= 90% | `pytest --cov --cov-branch --cov-fail-under=90` in `make verify` and CI | AUTO |
| Lint, format, types | zero findings | ruff check + ruff format --check + strict mypy in `make verify` and CI | AUTO |
| Lighthouse accessibility | == 100 on all five page classes | `.github/workflows/accessibility.yml`; missing report or missing category fails | AUTO |
| Resource budgets | 0 of every non-document type | `lighthouse-budget.json` in the same workflow | AUTO |
| Static WCAG checks | zero violations | `tests/test_accessibility.py` (contrast both themes, landmarks, headings, table semantics, colour-independence) in `make verify` | AUTO |
| Committed artifacts match their generators | byte-for-byte | tests tying `data/dataset.csv` / `data/national.json` to the code that writes them | AUTO |
| SHA-pinned `uses:` | 100% | full 40-char SHAs in all workflows; Dependabot keeps them current | AUTO |
| Secret / SAST / dependency scan | zero unwaived findings | `.github/workflows/security.yml` (gitleaks, semgrep, pip-audit), blocking | AUTO |
| Snapshot cadence | daily, or a loud failure | `.github/workflows/snapshot.yml` | AUTO |
| Drift threshold | 2 points of rate, reviewed against new collection years | README "Drift is a change in rate" section records the calibration | REVIEW |
| Rationale disputability | every credible range carries a written rationale | `src/disclosed/fields.py`; reviewed when a range changes | REVIEW |

AI-DEV-MEASUREMENT: APPLIES (delivery and quality-debt metrics are mined portfolio-wide from
git/PR history; there is no AI product surface in this repo, so Track B is N/A - see the AI
Evaluation row in the README conformance table).

## CI stages 6-8 applicability (CI-CD-STANDARD section 10)

| Stage | Applicable? | Gate |
|---|---|---|
| 6. a11y | **Applicable** | Static WCAG suite in `make verify` + Lighthouse 100 gate in `accessibility.yml` |
| 7. perf | **Applicable (budget form)** | `lighthouse-budget.json` zero budgets enforced in `accessibility.yml`; no load-test surface exists (static files, no server) |
| 8. responsible | **Applicable** | `docs/RESPONSIBLE-TECH-AUDITS.md`; the ethics constraints are code (classifier, scope refusals) and are tested |
