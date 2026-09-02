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

   The second open question was what CTDL would be graded *on*, and it is now answered too, in
   the other direction. `docs/adr/0009`: the registry was walked again on 2026-08-27 counting
   which CTDL property names each organization publishes. Over the 4,818 that publish an IPEDS
   id, nine properties are on 100% of them and three more on over 97%, every one of them
   identity, location, a self-description or a federal id; the next most common property in the
   whole vocabulary is `ceterms:email` on 1.1%, and `ceterms:hasCostManifest`, the nearest thing
   to a cost disclosure, is on 6. 96.0% of them carry an identical set of twelve properties and
   98.2% carry a free-text `IPEDS NCES Data Year`. That is a directory loaded from IPEDS, dated
   to the collection year this project already reads from IPEDS directly.

   **So the adapter is not written, and this milestone closes as a finding rather than staying
   open as a plan.** ADR 0009 states what would reopen it: a property carrying a published duty
   appearing at a rate that is not a rounding error, which `make registry-properties` would
   report on a rerun. The 25% of the IPEDS directory the registry does not reach stays recorded
   in ADR 0007 as the limit whoever revisits this inherits; it is not spent, because there is no
   adapter to spend it. One stated limit on the finding: it is about organizations, and the
   registry's `resource_type=credential` set has not been walked, because a credential is not an
   institution.

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

Roughly: phase 2 is the coming year's work and phases 5 and 6 the two years after it. Phase 3
was expected to take much of the first year and instead answered itself in a day, which is what
measuring first is for; phase 4 will now not happen at all, and that is the same fact rather than
a setback.

**Phase 1. The budget file is read where a static checker can read it. Built, 2026-08-27**
(`docs/adr/0008`). The `resourceSizes` lines of `lighthouse-budget.json` are enforced in `make
verify` over the fixture and over all 617 pages of the committed build; every line of that file
is now either enforced by a named check or declared unenforceable with a written reason, and a
line in neither register fails the build. The three timing lines are still enforced by nothing,
and the ledger row above says so.

**Phase 2. The timing budget, after the runner has been measured.** Precondition, from ADR 0008:
record what `accessibility.yml`'s own runner reports for LCP, CLS and TBT, on the largest page
and not only on the home page, over enough runs to know the spread. Locally the largest page
reports 1,052 ms against a 1,500 ms line, and that is not headroom to calibrate a CI gate from a
laptop. What this phase may not do: gate on a number nobody measured, or widen the budget to turn
a red gate green without the argument for the wider budget appearing in the README.

**Phase 3. What the Credential Registry publishes that a duty could be graded against. Built,
2026-08-27** (`docs/adr/0009`). Milestone 1's second open question, which ADR 0007 states plainly
is not answered by a join. Measured the way ADR 0007 set: walk once, capture, reduce, publish the
number whichever way it comes out. It came out that the registry publishes identity and not
disclosure for the institutions this project could grade, so phase 4 below is not built. The
adapter was not written first and measured from its output, which ADR 0007 rejected by name.

**Phase 4. The CTDL adapter. Not built, and not pending: phase 3 answered it.** The condition was
"if and only if phase 3 finds a duty", and phase 3 found nine universal properties of which none
is a disclosure with a duty behind it. Writing the adapter anyway would mean grading institutions
on a voluntary listing that is 96% a bulk load of a corpus this project already reads. The limit
it would have inherited, that roughly a quarter of the IPEDS directory is not in the registry and
those institutions must render as outside the frame rather than as institutions that disclosed
nothing, stays written in ADR 0007 for whoever reopens this. ADR 0009 names the measurement that
would reopen it.

**Phase 5. The two accessibility artifacts recorded as open. Blocked on a person.** A human
assistive-technology walkthrough and an ACR or VPAT, open in `docs/RESPONSIBLE-TECH-AUDITS.md`
since 2026-08-07, where the automated evidence is explicitly stated not to substitute for them.

- *Blocked by:* both are records of a human evaluation. A walkthrough is what somebody found
  operating the site with a screen reader and a keyboard, and an ACR is a conformance claim
  attributed to whoever evaluated it. Neither can be produced from the automated evidence without
  the resulting document being a fabrication, which is the one thing this project cannot ship.
- *Unblocked by:* one person, one session with NVDA or VoiceOver and the keyboard, on the six
  page classes; then the ACR written from what that session found, signed by them. The suite in
  `make verify` is the input to that session, not a substitute for it.

**Phase 6. The first release (milestone 4). Blocked on the owner's decision to cut it.** It
supersedes `docs/adr/0001-no-versioned-release.md`, which is a decision this sequence does not
get to make. What it brings with it is recorded in the audit's section F and in `docs/I18N.md`,
and the parts are listed here so that "blocked" names something specific:

- **The cut itself.** Owner's. Nothing below is worth doing before it is made, because a release
  workflow nobody has run is a configuration file nobody has read, which is the defect phases 1
  and 2 of this sequence existed to remove.
- **SBOM and signing.** Named in the audit as required at the first release and N/A until then.
  Signing needs key material or an OIDC identity that does not exist yet; an SBOM needs a format
  decision and the dev dependency that emits it, which is an owner call about this repository's
  dependency surface rather than an implementation detail. Neither is written here, and neither is
  stubbed.
- **Internationalization.** `docs/I18N.md` defers it *to* the first release and records the entry
  point: the page templates' strings into a message catalog, with the five classification tokens
  kept as machine keys in the CSV export and translated only at the presentation layer. That is a
  recorded decision with a reason, so it is not started early.
- **The CHANGELOG's first tagged section.** Mechanical, and it is the cut.

**Owner-gated, and therefore not scheduled here.**

- **Deploying `disclosed.ask`** (`deploy/README.md`). Applying the template moves the
  observability tier above, adds a subprocessor record to the privacy inventory and reopens the
  EU AI Act question. *Unblocked by:* the owner deciding to apply it, with an AWS account and the
  model access it names.
- **Issues #36 and #37**, both marked "decision needed (owner)". #36 would move open-admissions
  institutions from `missing` to `not_applicable` and change the README's headline figure; #37
  would change which statute the athletics gap is argued from. Both change the grading contract,
  which is the product. *Unblocked by:* the owner choosing, in the issue.

**Decided, and therefore not on this list.** Milestone 2, the veterans-page grading rule, stays
ungraded because no universal publication duty exists, and a rule about who a duty reaches is
worthless when the duty does not exist. That is a decision with a reason. Carrying it as pending
work would misdescribe it, and building it would contradict the record.

### Status of the sequence

| Phase | State |
|---|---|
| 1. The budget file is read where a static checker can read it | **Built** (ADR 0008) |
| 2. The timing budget, after the runner was measured | **Built** (ADR 0010) |
| 3. What the registry publishes that a duty could be graded against | **Built** (ADR 0009) |
| 4. The CTDL adapter | **Not built, decided** by phase 3's measurement |
| 5. Human accessibility walkthrough and ACR | **Blocked** on a person operating a screen reader |
| 6. The first release | **Blocked** on the owner's decision to cut it |
| Deploying `disclosed.ask`; issues #36, #37 | **Blocked**, owner decisions |
| Milestone 2, the veterans rule | **Decided** against, with a reason |

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
| Branch coverage | >= 95% | `pytest --cov --cov-branch --cov-fail-under=95` in `make verify` and CI | AUTO |
| Lint, format, types | zero findings | ruff check + ruff format --check + strict mypy in `make verify` and CI, over `src`, `tests` and `.github/scripts` | AUTO |
| Lighthouse accessibility | == 100 on all six page classes | `.github/workflows/accessibility.yml`; missing report or missing category fails | AUTO |
| Resource counts | 0 of every non-document type, on every page | `tests/test_accessibility.py::TestTheResourceBudget` (one page of each kind) and `::TestTheResourceBudgetOverThePublishedSite` (all 617 pages of the committed build), both in `make verify` | AUTO |
| Resource transfer sizes | every page inside the `resourceSizes` lines of `lighthouse-budget.json` (80 KiB document, 80 KiB total, zero for every other type) | `tests/test_accessibility.py::TestTheTransferSizeBudget` in `make verify`, over one page of each kind and again over all 617 pages of the committed build, reading the numbers out of the budget file rather than restating them; the largest published page (65.5 KiB) is a README figure recomputed from the build (ADR 0008) | AUTO |
| Lighthouse timings (`largest-contentful-paint`, `cumulative-layout-shift`, `total-blocking-time`) | as stated in `lighthouse-budget.json` | **nothing** - `--budget-path` never fails a Lighthouse run and Lighthouse 12 emits no budget audit, and the three timing lines need a rendering engine no static checker has. Measured locally 2026-08-27 with `lighthouse@12`: home 752 ms LCP, state/CA 1052 ms, against a 1500 ms line, CLS and TBT exactly 0 on both. A laptop is not the runner, so ADR 0008 puts the runner's own numbers ahead of the gate. Recorded here rather than claimed as a gate | NONE |
| Every budget line is in one register or the other | no line of `lighthouse-budget.json` enforced by nobody and unnamed | `tests/test_accessibility.py::TestEveryBudgetLineIsAccountedFor`; a new line that is neither enforced by a named check nor declared unenforceable with a reason fails `make verify`, and a register entry for a line the file no longer carries fails too | AUTO |
| Static WCAG checks | zero violations | `tests/test_accessibility.py` (contrast both themes, landmarks, headings, table semantics, colour-independence) in `make verify` | AUTO |
| Committed artifacts match their generators | byte-for-byte | tests tying `data/dataset.csv` / `data/national.json` to the code that writes them | AUTO |
| SHA-pinned `uses:` | 100% | full 40-char SHAs in all workflows; Dependabot keeps them current | AUTO |
| Secret / SAST / dependency scan | zero unwaived findings | `.github/workflows/security.yml` (gitleaks, semgrep, pip-audit), blocking, with no severity floor on semgrep and no `.semgrepignore` exclusions; the three waived findings carry an inline `nosemgrep` and a reason | AUTO |
| Snapshot cadence | daily, or a loud failure | `.github/workflows/snapshot.yml`; the post-condition checks `origin/master`, the commit is gated by `verify.yml` and `security.yml` dispatched on a staging ref, and each dispatched job's watched result is transcribed to a commit status before the push (ADR 0004; ADR 0003 alone was rejected twice on its first real run) | AUTO |
| Credential Registry property census | measured, never assumed; the report replays from the committed census, and the census is proven to describe the same walk as the join capture | `data/registry-properties.json` rebuilt from `data/registry/properties.json` in `tests/test_registry_properties.py::TestTheCommittedPropertyCensus`; every README figure in that section re-derived in `tests/test_doc_counts.py` (ADR 0009) | AUTO |
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
| 7. perf | **Applicable (budget form)** | Zero-subresource budget **and** the transfer-size lines of `lighthouse-budget.json` enforced statically over every page in `make verify`; the three timing lines are enforced by nothing and the ledger row says so, with ADR 0008 naming what has to be measured before they become a gate. No load-test surface exists (static files, no server) |
| 8. responsible | **Applicable** | `docs/RESPONSIBLE-TECH-AUDITS.md`; the ethics constraints are code (classifier, scope refusals) and are tested |
