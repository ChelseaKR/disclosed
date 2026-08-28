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

1. **Credential Registry (CTDL) adapter.** No longer blocked, and it never was; and now
   measured, which is a further step. Probed unauthenticated on 2026-08-15:
   `GET /ce-registry/search?resource_type=credential` returns HTTP 200 with `x-total: 133346`,
   `?resource_type=organization` returns 34,082, and `GET /ce-registry/envelopes?page=N&per_page=1`
   returns 200 with `x-total: 395878` and a full `decoded_resource` per envelope. `/robots.txt`
   404s, so no crawl directives are published. The old note read an `x-total: 0` as a locked door;
   the filter parameter is `resource_type` and an unmatched value is answered 200-with-zero rather
   than with an error, so the zero meant "your query matched nothing" (README, "The zero in the
   Credential Registry row was our own failure mode").

   The precondition this milestone set itself is met. It read: "in a 200-organization sample only
   8 records mentioned IPEDS at all, so measure the join rate to the two federal corpora before
   designing around it." The join was measured on 2026-08-27 by walking
   `resource_type=organization` to the registry's own stated total, 33,809 organizations over 340
   pages, and it is good: 4,818 organizations publish a typed `ceterms:ipedsID`, which resolves to
   4,794 of the 6,163 institutions in the IPEDS directory (77.8%) and 4,510 of the 6,273 in the
   Scorecard census. The 200-organization figure was six-tenths of one percent of the registry,
   drawn from the front of an offset-paginated set, and it looked for the string "IPEDS" rather
   than for the typed property that carries the identifier; the registry's most common
   IPEDS-shaped free-text identifier is `"IPEDS NCES Data Year": "2023"`, which is a year.
   `docs/adr/0007` records the decision and the numbers; `disclosed registry-fetch` and
   `disclosed registry-join` produce them; `data/registry-join.json` is the committed artifact and
   replays byte-for-byte in `make verify`.

   The adapter is still unwritten, which remains a different status and a different sentence.
   What it inherits from the measurement is a stated limit as well as a stated basis: roughly a
   quarter of the IPEDS directory is not in the registry at all, so anything built on this join
   has to render those institutions as outside the frame rather than as institutions that
   disclosed nothing. The second open question is what CTDL would be graded *on*: this project
   grades published disclosures against duties, and whether the registry carries a duty worth
   grading is not answered by the join.

2. **Veterans-page grading rule.** The IPEDS characteristics file could supply an
   applicability rule, but no universal publication duty exists, so it stays ungraded until a
   defensible rule does (README).
3. **Grounded disclosure Q&A (ADR 0006).** An optional runtime layer, `disclosed.ask`, that
   answers "what does this institution not disclose, and why does that matter" from the
   project's own classified records, refuses performance judgement, never collapses the five
   states, and is measured on both. Built in stages: corpus of federal definitions, evidence
   store and question structuring, grounded narration with a verifier, evaluation suites,
   front-end opt-in, and an unapplied deployment template (`deploy/`, tested against the code
   it would run). Deployment is a separate owner decision; `deploy/README.md` lists what it
   does not make.
4. **First release.** Supersedes `docs/adr/0001-no-versioned-release.md` and brings the
   hardened release workflow with it.

## The phase sequence

The milestones above say what gets built. This says in what order, over what horizon, what each
phase is not allowed to do, and which of them is finished. It is a plan and not a promise: a
phase whose precondition fails is a phase that gets rewritten, which has already happened here
once, when milestone 1's own precondition was measured on 2026-08-27 and came back the opposite
way round from the note that set it (`docs/adr/0007`).

Roughly: phases 2 and 3 are the coming year's work, phases 4 to 6 the two years after it, and
phase 4 may never happen at all, which is the reason phase 3 comes first rather than a hedge.

**Phase 1. The budget file is read where a static checker can read it. Built, 2026-08-27**
(`docs/adr/0008`). The `resourceSizes` lines of `lighthouse-budget.json` are enforced in `make
verify` over the fixture and over all 617 pages of the committed build; every line of that file
is now either enforced by a named check or declared unenforceable with a written reason, and a
line in neither register fails the build. The three timing lines are still enforced by nothing,
and the ledger row above says so.

**Phase 2. The timing budget, after the runner has been measured. Built, 2026-08-28**
(`docs/adr/0010`). The precondition ADR 0008 set was met first and separately: the largest page
was added to the audits that collect the performance category, a step reported the numbers
without gating on them, and run 33129896655 came back at 751.7 ms and 1052.4 ms, within a
millisecond of the laptop's figures, because lighthouse throttles by simulation rather than by
applying anything. Then the gate. The budget stayed at 1500 ms rather than being tightened to
30 ms above the measurement, because a budget set just above today's number gets widened under
deadline instead of investigated, which is the failure ADR 0008 was written about.

**Phase 3. What the Credential Registry publishes that a duty could be graded against.**
Milestone 1's second open question, which ADR 0007 states plainly is not answered by a join: this
project grades published disclosures against duties, and the join says only that the two corpora
share institutions. The measurement takes the shape ADR 0007 set: walk once, commit the capture,
reduce it to what the question needs, and publish the number even when the answer is that the
registry carries no duty worth grading and the adapter should not be written. What this phase may
not do: write the adapter and measure from its output, which ADR 0007 rejected by name, because
an adapter that exists is an adapter somebody will publish figures from.

**Phase 4. The CTDL adapter, if and only if phase 3 finds a duty.** It inherits a limit before it
inherits anything else: roughly a quarter of the IPEDS directory is not in the registry at all,
so those institutions have to render as outside the frame and never as institutions that
disclosed nothing. That is a sixth thing a page can say about a field, beside the five
classifications, and it needs its own name, its own rendering and its own tests before it needs
an adapter.

**Phase 5. The two accessibility artifacts recorded as open.** A human assistive-technology
walkthrough and an ACR or VPAT, open in `docs/RESPONSIBLE-TECH-AUDITS.md` since 2026-08-07, where
the automated evidence is explicitly stated not to substitute for them. Neither is code and
neither can be produced by a gate; they need a person with a screen reader and a document written
by hand.

**Phase 6. The first release (milestone 4).** Supersedes ADR 0001 and brings with it the two
artifacts the security declarations name as required at that point, an SBOM and signing, plus the
hardened release workflow, the CHANGELOG's first tagged section, and the internationalization
work `docs/I18N.md` defers to exactly this moment: the page templates' strings into a message
catalog, with the five classification tokens kept as machine keys in the CSV export and
translated only at the presentation layer. Milestone 4 is the first release and this sequence
does not subdivide it away.

**Owner-gated, and therefore not scheduled here.** Deploying `disclosed.ask` is the owner's
decision (`deploy/README.md`); applying it moves the observability tier above, adds a
subprocessor record to the privacy inventory and reopens the EU AI Act question. Issues #36 and
#37 change the grading contract itself and are marked as needing the owner's decision rather than
an implementer's.

**Decided, and therefore not on this list.** Milestone 2, the veterans-page grading rule, stays
ungraded because no universal publication duty exists, and a rule about who a duty reaches is
worthless when the duty does not exist. That is a decision with a reason. Carrying it as pending
work would misdescribe it.

## Observability

Tier C (library/CLI). The project is a CLI that reads public archives and writes files; the
site is a build artifact, not a hosted service. There is no server, no accounts, no telemetry,
and no production boundary to observe. Operational visibility is the CI record itself: the
daily snapshot workflow fails loudly (missing API key, non-exhaustive walk) rather than
committing a partial truth, which for this shape of project is the observability that matters.
OTel instrumentation re-enters scope if a hosted surface ever ships. The prepared (unapplied)
shape of the question-answering service in `deploy/` carries the observability it would need
on day one: a 14-day log group that holds runtime errors and nothing a reader typed, an
invocations alarm at the daily cap, and a monthly budget; if it is applied, this tier moves.

## Metrics ledger

Per QUALITY-AND-METRICS-STANDARD's ledger shape. Values as measured 2026-08-07.

| Metric | Target | Measured by | Gate |
|---|---|---|---|
| Branch coverage | >= 90% | `pytest --cov --cov-branch --cov-fail-under=90` in `make verify` and CI | AUTO |
| Lint, format, types | zero findings | ruff check + ruff format --check + strict mypy in `make verify` and CI, over `src`, `tests` and `.github/scripts` | AUTO |
| Lighthouse accessibility | == 100 on all six page classes | `.github/workflows/accessibility.yml`; missing report or missing category fails | AUTO |
| Resource counts | 0 of every non-document type, on every page | `tests/test_accessibility.py::TestTheResourceBudget` (one page of each kind) and `::TestTheResourceBudgetOverThePublishedSite` (all 617 pages of the committed build), both in `make verify` | AUTO |
| Resource transfer sizes | every page inside the `resourceSizes` lines of `lighthouse-budget.json` (80 KiB document, 80 KiB total, zero for every other type) | `tests/test_accessibility.py::TestTheTransferSizeBudget` in `make verify`, over one page of each kind and again over all 617 pages of the committed build, reading the numbers out of the budget file rather than restating them; the largest published page (65.5 KiB) is a README figure recomputed from the build (ADR 0008) | AUTO |
| Lighthouse timings (`largest-contentful-paint`, `cumulative-layout-shift`, `total-blocking-time`) | as stated in `lighthouse-budget.json` | `.github/scripts/check_lighthouse_timings.py`, run by `.github/workflows/accessibility.yml` over the home page and the largest page, both audited with the performance category. It fails on a metric over budget, on a metric a report does not carry (lighthouse collects timings only when that category is asked for, which is how this gate would otherwise stop applying), and on a report that was never written. Gated only after the runner was measured rather than assumed: run 33129896655 reported LCP 751.7 ms on the home page and 1052.4 ms on state/CA against a 1500 ms line, within a millisecond of the laptop figures in ADR 0008, because lighthouse throttles by simulation (ADR 0010) | AUTO |
| Every budget line is in one register or the other | no line of `lighthouse-budget.json` enforced by nobody and unnamed | `tests/test_accessibility.py::TestEveryBudgetLineIsAccountedFor`; a new line that is neither enforced by a named check nor declared unenforceable with a reason fails `make verify`, and a register entry for a line the file no longer carries fails too | AUTO |
| Static WCAG checks | zero violations | `tests/test_accessibility.py` (contrast both themes, landmarks, headings, table semantics, colour-independence) in `make verify` | AUTO |
| Committed artifacts match their generators | byte-for-byte | tests tying `data/dataset.csv` / `data/national.json` to the code that writes them | AUTO |
| SHA-pinned `uses:` | 100% | full 40-char SHAs in all workflows; Dependabot keeps them current | AUTO |
| Secret / SAST / dependency scan | zero unwaived findings | `.github/workflows/security.yml` (gitleaks, semgrep, pip-audit), blocking, with no severity floor on semgrep and no `.semgrepignore` exclusions; the three waived findings carry an inline `nosemgrep` and a reason | AUTO |
| Snapshot cadence | daily, or a loud failure | `.github/workflows/snapshot.yml`; the post-condition checks `origin/master`, the commit is gated by `verify.yml` and `security.yml` dispatched on a staging ref, and each dispatched job's watched result is transcribed to a commit status before the push (ADR 0004; ADR 0003 alone was rejected twice on its first real run) | AUTO |
| Credential Registry join | measured, never assumed; the artifact replays from committed inputs | `data/registry-join.json` rebuilt from `data/registry/organizations.json`, `data/HD2023.zip` and `data/census/scorecard.json` in `tests/test_registry.py::TestTheCommittedMeasurement`; every README figure in that section re-derived in `tests/test_doc_counts.py` | AUTO |
| Fetch provenance | every page recorded; key never in git | `disclosed fetch` writes redacted URL, time, status, bytes, SHA-256 and rate-limit headers per page; the `census` workflow refuses to commit a capture containing the key; `tests/test_sources.py::TestProvenance` | AUTO |
| Scorecard census coverage | national, not a 600-institution slice; every figure re-derived and stated beside the sample | `data/census/scorecard.json` (6,273 institutions, provenance-proven exhaustive) reduced by `disclosed census-report` to `data/scorecard-census.json`; byte-for-byte replay in `tests/test_census_replay.py`; README's "What is a sample and what is national" states both frames' composition | AUTO |
| Drift threshold | 2 points of rate, reviewed against new collection years | README "Drift is a change in rate" section records the calibration | REVIEW |
| Rationale disputability | every credible range carries a written rationale | `src/disclosed/fields.py`; reviewed when a range changes | REVIEW |

AI-DEV-MEASUREMENT: APPLIES (delivery and quality-debt metrics are mined portfolio-wide from
git/PR history). Track B applies from ADR 0006: the AI product surface is `disclosed.ask`, and
its evaluation results under `evals/` are the Track B record - see the AI Evaluation row in the
README conformance table.

## CI stages 6-8 applicability (CI-CD-STANDARD section 10)

| Stage | Applicable? | Gate |
|---|---|---|
| 6. a11y | **Applicable** | Static WCAG suite in `make verify` + Lighthouse 100 gate in `accessibility.yml` |
| 7. perf | **Applicable (budget form)** | Every line of `lighthouse-budget.json` is enforced by something named. Counts and transfer sizes statically over all 617 pages in `make verify`; the three timing lines by `.github/scripts/check_lighthouse_timings.py` in `accessibility.yml`, gated only after the runner itself was measured (ADR 0008, then ADR 0010). No load-test surface exists (static files, no server) |
| 8. responsible | **Applicable** | `docs/RESPONSIBLE-TECH-AUDITS.md`; the ethics constraints are code (classifier, scope refusals) and are tested |
