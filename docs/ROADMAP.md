# Roadmap and metrics ledger

The README is the spec: what the project grades, why absence is data, what is a sample and
what is national. This file carries the declarations the portfolio standards ask each repo to
state in one place: the observability tier, the metrics ledger, and the applicability of the
optional CI stages.

## Where the project is

Working, pre-release (`0.1.0.dev0`, status: Beta). Both federal adapters are live, the drift
measurement has three real IPEDS collection years behind it, the national corpus is committed,
and the site holds a Lighthouse accessibility score of 100 and ships no subresource of any
kind. Next
milestones, in order:

1. **Credential Registry (CTDL) adapter.** No longer blocked, and it never was. Probed
   unauthenticated on 2026-08-15: `GET /ce-registry/search?resource_type=credential` returns
   HTTP 200 with `x-total: 133346`, `?resource_type=organization` returns 34,082, and
   `GET /ce-registry/envelopes?page=N&per_page=1` returns 200 with `x-total: 395878` and a full
   `decoded_resource` per envelope. `/robots.txt` 404s, so no crawl directives are published.
   The old note read an `x-total: 0` as a locked door; the filter parameter is `resource_type`
   and an unmatched value is answered 200-with-zero rather than with an error, so the zero meant
   "your query matched nothing" (README, "The zero in the Credential Registry row was our own
   failure mode"). The adapter is unwritten because nobody has written it, which is a different
   status and belongs in a different sentence. First question for whoever does: in a
   200-organization sample only 8 records mentioned IPEDS at all, so measure the join rate to
   the two federal corpora before designing around it.
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
| Lint, format, types | zero findings | ruff check + ruff format --check + strict mypy in `make verify` and CI, over `src`, `tests` and `.github/scripts` | AUTO |
| Lighthouse accessibility | == 100 on all five page classes | `.github/workflows/accessibility.yml`; missing report or missing category fails | AUTO |
| Resource counts | 0 of every non-document type, on every page | `tests/test_accessibility.py::TestTheResourceBudget` (one page of each kind) and `::TestTheResourceBudgetOverThePublishedSite` (all 616 pages of the committed build), both in `make verify` | AUTO |
| Resource transfer sizes and timings | as stated in `lighthouse-budget.json` | **nothing** - `--budget-path` never fails a Lighthouse run and Lighthouse 12 emits no budget audit; recorded here rather than claimed as a gate | NONE |
| Static WCAG checks | zero violations | `tests/test_accessibility.py` (contrast both themes, landmarks, headings, table semantics, colour-independence) in `make verify` | AUTO |
| Committed artifacts match their generators | byte-for-byte | tests tying `data/dataset.csv` / `data/national.json` to the code that writes them | AUTO |
| SHA-pinned `uses:` | 100% | full 40-char SHAs in all workflows; Dependabot keeps them current | AUTO |
| Secret / SAST / dependency scan | zero unwaived findings | `.github/workflows/security.yml` (gitleaks, semgrep, pip-audit), blocking, with no severity floor on semgrep and no `.semgrepignore` exclusions; the three waived findings carry an inline `nosemgrep` and a reason | AUTO |
| Snapshot cadence | daily, or a loud failure | `.github/workflows/snapshot.yml`; the post-condition checks `origin/master`, and the commit is gated by `verify.yml` and `security.yml` dispatched on a staging ref before it is pushed (ADR 0003) | AUTO |
| Fetch provenance | every page recorded; key never in git | `disclosed fetch` writes redacted URL, time, status, bytes, SHA-256 and rate-limit headers per page; the `census` workflow refuses to commit a capture containing the key; `tests/test_sources.py::TestProvenance` | AUTO |
| Drift threshold | 2 points of rate, reviewed against new collection years | README "Drift is a change in rate" section records the calibration | REVIEW |
| Rationale disputability | every credible range carries a written rationale | `src/disclosed/fields.py`; reviewed when a range changes | REVIEW |

AI-DEV-MEASUREMENT: APPLIES (delivery and quality-debt metrics are mined portfolio-wide from
git/PR history; there is no AI product surface in this repo, so Track B is N/A - see the AI
Evaluation row in the README conformance table).

## CI stages 6-8 applicability (CI-CD-STANDARD section 10)

| Stage | Applicable? | Gate |
|---|---|---|
| 6. a11y | **Applicable** | Static WCAG suite in `make verify` + Lighthouse 100 gate in `accessibility.yml` |
| 7. perf | **Applicable (budget form)** | Zero-subresource budget enforced statically over every page in `make verify`; the transfer-size and timing lines of `lighthouse-budget.json` are enforced by nothing and the ledger row says so. No load-test surface exists (static files, no server) |
| 8. responsible | **Applicable** | `docs/RESPONSIBLE-TECH-AUDITS.md`; the ethics constraints are code (classifier, scope refusals) and are tested |
