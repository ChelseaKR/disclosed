# Changelog

All notable changes to this project are recorded here, in
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) form. The project uses
[SemVer](https://semver.org/) once it starts tagging releases; nothing has been released yet, so
everything to date lives under Unreleased. The commit history is the fine-grained record; this
file is the human-readable one.

## [Unreleased]

### Fixed

- **Two different colleges were one page in every result list.** Unit 104708,
  Glendale Community College in Arizona, graded B, and unit 115001, Glendale
  Community College in California, graded D, rendered the identical
  `<title>` and near-identical description: title by name alone, and the name
  alone does not identify an institution. That is this project's own subject
  matter, two different facts rendered as one, appearing in its own `<head>`.
  Institution titles and descriptions now carry the state, which was already
  on the page in the breadcrumb and the facts list. Where the report publishes
  no state the qualifier is left off rather than filled in.
- The home page's `<title>` read "disclosed: what US colleges do not tell you
  | disclosed". `_shell` appends " | disclosed" to every title, so the site's
  name appeared twice in fifty-four characters on the page most likely to be
  seen in a result list. The page title is now "What US colleges do not tell
  you".

### Added

- **The timing budget is a gate, and it was calibrated on the runner rather than on a laptop
  ([ADR 0010](docs/adr/0010-the-timing-budget-becomes-a-gate-after-the-runner-was-measured.md)).**
  ADR 0008 left largest contentful paint, cumulative layout shift and total blocking time
  enforced by nothing, with the reason and a precondition: measure what `accessibility.yml`'s own
  runner reports, on the largest page and not only the home page. That measurement landed first
  and separately (run 33129896655: 751.7 ms and 1052.4 ms against a 1500 ms line, within a
  millisecond of the laptop's figures, because lighthouse throttles by simulation), and this is
  the gate it licensed. `.github/scripts/check_lighthouse_timings.py` reads the budget out of the
  file and fails on a metric over budget, on a metric a report does not carry, and on a report
  that was never written; a budget file with no timing lines exits 2 rather than passing over
  everything, which is the state the whole file was in while three documents called it a gate.
  The metrics ledger's last `Gate: NONE` row becomes AUTO, and
  `TestEveryBudgetLineIsAccountedFor`'s unenforced register is now empty.
  ADR 0010's 2026-09-01 amendment moves `total-blocking-time` off the `0` it had been carrying:
  the headroom argument that kept paint time at 1500 ms was never applied to it, and the gate
  reported `total-blocking-time is 34 against a budget of 0` on its own next run, on a tree whose
  only change was the gate. The site ships no script, so 34 ms is a shared runner's main thread.
  The line is 200 ms, where Lighthouse's own scoring stops calling blocking time good — a
  published boundary rather than a reading off this runner. `cumulative-layout-shift` stays at 0,
  because with no script, image, stylesheet or font on any page there is nothing that can shift.

- **The daily Scorecard snapshots are replayed, not merely committed.**
  `data/snapshots/scorecard/` held nine committed artifacts that nothing regenerated and nothing
  compared. `tests/test_replay.py` replays every IPEDS snapshot from its own archives and
  `tests/test_census_replay.py` replays `data/scorecard-census.json` from the committed capture;
  the series beside them had neither, and the only thing in the repository that mentioned it was
  `tests/test_workflows.py` asserting that a path string appears in the workflow YAML. They were
  counts standing in for a computation, with nothing checking the counts were still what the
  computation produces. All nine replay byte-identically today, which is the point: nothing was
  keeping them that way. The snapshot taken on the day of the committed capture is now held to
  byte equality against what that capture regrades to, with the date read out of the capture's own
  provenance so refreshing the capture moves it, and `make scorecard-snapshot-replay` shows the
  diff when it fails. The other days are deliberately **not** frozen to that replay: their
  captures were ninety-day workflow artifacts and are gone, and a series that exists to record
  drift must not be gated on never drifting. They are held instead to what is true of them
  whatever the Scorecard published that morning: the date they claim, the walk they came from,
  and their own arithmetic. `snapshot.yml` commits a provenance sidecar beside every snapshot so
  that "a drift finding can be traced to the bytes it was computed from", and nothing checked the
  sidecar was there or that the two files describe the same run; both are checked now. The glob
  the series is discovered through is asserted non-empty before anything is parametrized over it,
  because an empty one parametrizes into zero tests and reports as a passing suite.

- **`check_site_origin.py` checks three more promises, and says what its
  fourth cannot do.** It held canonicals, the sitemap and robots.txt to the
  deploy target. It now also refuses a page whose `og:url` disagrees with its
  own canonical, a page carrying a root-relative `href`, `src` or `content`,
  and any page with an empty or duplicated title or description. The
  root-relative check is issue #2 one level down: this site is served at a
  path on an origin five sibling projects share, and
  `https://chelseakr.github.io/` is itself a 404, so `href="/methodology/"`
  resolves against the origin and lands on another project or on nothing.
  The duplicate-title check found the Glendale defect above on its first run.

  Its docstring now also records what `robots.txt` here is not. A crawler
  reads one robots.txt per origin, at `https://chelseakr.github.io/robots.txt`,
  which this repository does not own and which serves a 404. The file written
  under `/disclosed/` is correct for anyone who fetches it and is discovered
  by nobody, so its `Sitemap:` line advertises the sitemap to no one. It stays,
  because it is true and is where a reader will look; getting the sitemap seen
  is a submission and the owner's action, not a file this build can write.

- Every page carries `og:site_name` and `twitter:card`.

- **What the Credential Registry publishes, counted, and the adapter decided against
  ([ADR 0009](docs/adr/0009-the-registry-publishes-identity-not-disclosure.md)).** ADR 0007
  measured the join and said in as many words that a join does not tell you what there is to
  grade. `disclosed registry-properties` walks the same set with the same adapter, the same page
  cache and the same refusal to report a walk it cannot prove reached the end, and captures which
  CTDL property *names* each organization publishes, never what is inside them;
  `disclosed registry-property-report` reduces that to rates over two denominators that are never
  summed. The answer, over the 4,818 organizations that publish an IPEDS id: nine properties on
  100% of them and three more above 97%, every one identity, location, a self-description or a
  federal id; `ceterms:email`, the next most common property in the whole vocabulary, on 52;
  `ceterms:hasCostManifest` on 6. 96.0% carry an identical set of twelve properties and 98.2%
  carry a free-text `IPEDS NCES Data Year`, which is a year. That is a directory loaded from
  IPEDS. **No adapter is written**, milestone 1 closes as a finding, and the ADR states the
  measurement that would reopen it. The capture is aggregated to distinct property sets, 403 KB
  rather than the 8.5 MB the same facts cost per organization, and the report replays
  byte-for-byte in `make verify`.

- **The transfer-size budget is a gate, and every line of the budget file is accounted for
  ([ADR 0008](docs/adr/0008-the-budget-file-is-read-where-a-static-checker-can-read-it.md)).**
  `lighthouse-budget.json` had its resource *counts* moved into `make verify` when Lighthouse
  turned out to enforce none of the file; the `resourceSizes` lines did not move with them, and
  the metrics ledger has carried a "Gate: NONE" row ever since. They move now:
  `tests/test_accessibility.py::TestTheTransferSizeBudget` holds every page of the six-page
  fixture and all 617 pages of the committed build to the file's own 80 KiB document and total
  lines, reading the numbers out of the file rather than restating them, and reporting a page
  that fetches something as unweighable rather than as a zero it invented. The largest published
  page, 65.5 KiB, is now a README figure a test recomputes from the build, because a budget with
  a hundredfold of slack passes for the same reason a gate that cannot fail does.
  `::TestEveryBudgetLineIsAccountedFor` closes the class rather than the instance: a budget line
  that is neither enforced by a named check nor declared unenforceable with a written reason
  fails the build, and so does a register entry naming a line the file no longer carries. The
  three timing lines stay enforced by nothing, with the reason, the local measurement (home
  752 ms LCP, state/CA 1,052 ms against a 1,500 ms line, CLS and TBT exactly 0) and the
  precondition for gating them written down; a test fails if the ledger stops saying so.

- **The Credential Registry join, measured before an adapter is designed around it
  ([ADR 0007](docs/adr/0007-the-credential-registry-join-is-measured-before-it-is-adapted.md)).**
  `disclosed.sources.credential_registry` walks `resource_type=organization` to the registry's own
  `x-total`, records the provenance of every page, caches pages so a rerun resumes rather than
  starting over, and refuses to report a walk it cannot prove reached the end, including on a
  stated total of zero, which is what the registry also answers to an unmatched filter.
  `disclosed.registry` measures three candidate keys separately and never sums them: the typed
  `ceterms:ipedsID`, the `ceterms:opeID` it counts and joins to nothing because neither committed
  corpus carries one, and the web host it treats as weaker and reports as both what it resolves to
  and what it adds. Two new verbs, `registry-fetch` and `registry-join`. The answer, on 2026-08-27:
  33,809 organizations, of which 4,818 publish an IPEDS unit id reaching 4,794 of the 6,163
  institutions in the IPEDS directory and 4,510 of the 6,273 in the Scorecard census. The
  roadmap's precondition is met and the adapter remains unwritten, which is a different sentence.
  Nothing here grades an institution or touches `disclosed.ask`.
- **The evaluation suites and their results.** Five suites under `evals/` (167 cases): ranking
  refusal, five-way classification fidelity scored per state, citation grounding, drift
  direction judged per cited record, question structuring including refused-to-guess. Three
  kinds of model behind a run: live, a faithful oracle (passes everything, proving the scorer),
  and a hostile adversary (leaks nothing, proving the verifier). Every result carries provider,
  model, prompt version, commit and date, and a test rejects one that does not. Measured live on
  `global.anthropic.claude-sonnet-4-6`: 0 leaked of 59 ranking questions, 0 wrong states shown of 46,
  0 wrong drift directions of 12, 0 guesses on 19 guarded questions.
- **`deploy/`: the prepared, unapplied deployment shape.** A SAM template (JSON, so the test
  suite reads it with the standard library) for one Lambda behind a Function URL with CORS locked
  to the Pages origin, reserved concurrency of 2, IAM limited to invoking the one configured
  model, an invocations alarm and a monthly budget; a build script that assembles the package
  and never talks to AWS; a README that says it is not applied and lists the decisions it does
  not make. `tests/test_deploy.py` holds the template to the code it would run.
- **The opt-in question form on institution pages, off by default.** `disclosed site
  --ask-endpoint URL` adds, to each institution page, a labelled form and one inline script with
  no `src` whose only network call is inside the submit handler; without the flag the build is
  byte-for-byte what it was and carries no script. Rendering uses `textContent` only; a failed
  or rate-limited request leaves the page unchanged. `tests/test_ask_widget.py` proves all of
  it from the built bytes.
- **ADR 0006: runtime AI at the edges.** An owner-directed change of direction, recorded before
  the code: an optional question-answering layer (`disclosed.ask`) in which the model structures
  a question and narrates the project's own classified records, never sees a reported value,
  cites a record for every claim, passes a verifier before display, refuses performance
  judgement, and never collapses the five classifications. `AGENTS.md` states the working rules.
- **`corpus/`: the federal definitions the AI layer may quote.** The College Scorecard glossary
  and data dictionary and the IPEDS HD2023/IC2023 dictionaries, kept as fetched (hash and
  retrieval date in `manifest.json`), reduced to 3,545 passages by `disclosed corpus`, replayed
  byte-for-byte in the test suite. `disclosed.ask.definitions` maps every graded field to the
  passage that defines its exact variable, and separately to related glossary entries with a
  note when they define a different measure. Quotes verify verbatim or are withheld.
- **The evidence store and the question-structuring step of `disclosed.ask`.** `evidence.build`
  reduces the committed inputs, in about a second and with no key, to every classification the
  project has made: 7,095 institutions, 153,486 records across two Scorecard snapshots and three
  IPEDS years, with the applicability condition behind every IPEDS `not_applicable`, the 18
  field-level drift measurements from the snapshot series, and 15 cross-source sector
  contradictions computed over the full census rather than the 600. A `reported` value is never
  carried; only an `implausible` one is. `provider` is the SDK seam (first-party or Bedrock,
  credentials from the environment only, scripted fake for tests); `structure` turns a question
  into a typed lookup whose field vocabulary is the schema's enum; `lookup` resolves the
  institution exactly, gathers the pack per intent, and refuses with fixed text: performance or
  ranking, outside disclosure, not in the frame, ambiguous, unclassified measure, unclear.
- **Grounded narration, the verifier, and the service.** `narrate` asks the model for claims
  that each cite a record id and for verbatim quotes; `verify` withholds and counts every claim
  it cannot prove against the pack: uncited or foreign citations, a classification word none of
  the cited records is in, an absence rendered as a non-state ("has no", "unavailable"), a number
  the model was never given, a judgement or recommendation; quotes verify verbatim against the
  corpus or are withheld. `service` runs the path with a per-client hourly limit and a hard
  daily cap before the first model call, labels every answer AI-generated and unofficial, keeps
  no request body, and carries the provenance of every quote; a Lambda Function URL handler and
  a stdlib development server share it. `disclosed ask` and `disclosed serve` on the CLI.

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
- **Provenance for every page the Scorecard adapter fetches.** `disclosed fetch` walks the API
  and writes a capture envelope: the records, plus for each page the request URL with the key
  redacted, the fetch time, HTTP status, byte count, SHA-256, attempts, and the rate-limit
  headers the API returned. `Retry-After` is honoured, consecutive fetches are paused, and a
  `--cache-dir` lets a rerun touch no network. `grade --source` replays the envelope and labels
  it national only when its own counts prove the walk was exhaustive; the daily job now grades
  from such a capture with no key in the environment, keeps the raw capture as a ninety-day
  artifact, and commits the provenance summary beside each snapshot. A dispatch-only `census`
  workflow commits a full capture to the branch it was run on, refusing if the replay is not
  national or the key is in the file.
- **A real Scorecard census (#17), beside the 600-institution sample, never in place of it.**
  Every Scorecard figure this project published came from 600 institutions in 13 states, 51% of
  them Californian, because the API returns institutions grouped by state and nobody had paged
  it to exhaustion. `census` was dispatched for real on 2026-08-21 and committed
  `data/census/scorecard.json`: 6,273 institutions, provenance-proven exhaustive, no key in the
  file. `disclosed census-report` reduces it and the committed sample to
  `data/scorecard-census.json` -- per-field national coverage plus both frames' composition
  (institutions by state and by sector) side by side, so "51% Californian" is answered with a
  table rather than asserted away. The re-derived headline: **4,363 of 6,273, or 69.6%, publish
  no admission rate at all**, five points higher than the sample's 64.5% -- the sample, if
  anything, understated non-disclosure. The sample also turns out skewed by sector and not just
  by state: 47.8% public against the census's 32.6%, and private for-profits at a fifth of the
  sample against over a third of the census. The site gains a `/census/` page
  (`disclosed.site.scorecard_census_page`) with a pointer from the home page; the README's
  "What is a sample and what is national" table gains a third row and the sector comparison.
  `tests/test_census_replay.py` gates the reduction byte-for-byte against the committed capture,
  the same discipline `tests/test_replay.py` holds `data/national.json` to.
- Portfolio standards conformance set: security workflow (gitleaks, semgrep, pip-audit),
  Dependabot config, pre-commit hooks, committed `uv.lock` and `.python-version`, ADR log,
  `SECURITY.md`, `CONTRIBUTING.md`, roadmap metrics ledger, and responsible-tech audit record.

### Fixed

- **`disclosed.ask`'s absence-collapse check missed the field's own most natural phrasing.**
  `_COLLAPSE` (`src/disclosed/ask/verify.py`) caught "has no", "no data", "unavailable", "did not
  provide" and kin, but not "does not report", "did not report", "not published", or "never
  reported" — a disclosure project's most natural paraphrase for an absent field. A correctly
  cited claim using that phrasing passed verification unchecked. The pattern now catches it;
  `tests/test_narrate_verify.py` gains four cases.
- **A claim naming a classification state, cited only to a drift or contradiction record, skipped
  the classification-fidelity check entirely.** `_check_claim` (`src/disclosed/ask/verify.py`)
  only checked a named state against `ClassificationRecord`s among the claim's citations; a claim
  citing only a drift or contradiction id — both citable per `Pack.citable_ids()` — had no
  `ClassificationRecord` to check against, so "suppressed" over a field with no suppressed record
  anywhere in the frame could stand unverified. Such a claim is now withheld outright, with the
  same exemption a note-only citation has always had.
- **`FieldDrift.direction` reported an unchanged reporting rate as "lost".** When `rate_change` is
  exactly `0.0` — the applicable population and the count of reporters both moved, in exact
  proportion, so the record is not skipped by `compare()` — `direction` fell through to the
  branch built for a real loss and returned `"lost"`, the same "absence rendered as a value"
  defect this module's own docstring argues against. It is now its own case, `"unchanged"`;
  `disclosed.ask.narrate`'s prompt and `tests/test_grading.py`/`tests/test_evidence.py` are
  updated to match.
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
  (2026-08-16 to 2026-08-20) wrote a snapshot, committed it, and had the push rejected by
  master's protections for lacking the checks they require; a sixteenth recorded a `verify`
  status on its own commit (ADR 0002) and was refused for the four checks it had not run. The
  Actions app cannot be a bypass actor on a user-owned repository and a token-opened pull
  request never acquires a check, so the job now pushes its commit to a staging ref, dispatches
  `verify.yml` and `security.yml` on that ref, waits for both to pass on that exact SHA,
  fast-forwards master, and dispatches the site rebuild that a token push would otherwise never
  start. No step records a check. The post-condition checks `origin/master`, not the runner's
  clone. Sixteen graded days remain unrecoverable and are recorded as such (ADR 0003,
  `docs/RESPONSIBLE-TECH-AUDITS.md`).
- **ADR 0003 also reached nobody, on its first real run.** Merged as #26 and dispatched for real
  (run 32473991532), the push was refused twice, seconds after `gh api .../check-runs` showed
  all five required checks green on the exact SHA being pushed. A GitHub Actions check run's
  check suite is scoped to the branch that triggered it; dispatching `verify.yml` and
  `security.yml` on `snapshot/staging` earns checks that satisfy nothing bound to `master`, no
  matter how identical the SHA. Commit statuses carry no such scoping, which is why ADR 0002's
  rejected single self-recorded status had satisfied the ruleset's one check when five real,
  correctly-SHA'd check runs did not. The job keeps ADR 0003's real dispatch-and-watch
  verification and, only after each dispatched job has been watched to completion, transcribes
  that job's own already-earned result to a commit status quoting the run it came from (ADR
  0004). Eighteen graded days are now unrecoverable, not sixteen.
