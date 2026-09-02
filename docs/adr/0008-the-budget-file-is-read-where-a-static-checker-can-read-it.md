# 0008. The budget file is read where a static checker can read it, and the rest is named rather than implied

- Status: Accepted
- Date: 2026-08-27
- Deciders: Chelsea Kelly-Reif

## Context

`lighthouse-budget.json` has been cited in three places in this repository as though it were a
gate: in the README's accessibility section, in the header comment of
`.github/workflows/accessibility.yml`, and in the metrics ledger. It was enforced by nothing.
That was found once already and fixed once already, and the fix was partial in a way worth being
precise about.

The finding was that `--budget-path` never makes Lighthouse exit non-zero, that the scoring step
reads only `categories.accessibility.score`, and that Lighthouse 12 emits no budget audit at all.
Reconfirmed on 2026-08-27 against `lighthouse@12` (12.8.2): a run of the home page with the
performance category requested produced no audit whose key contains the word "budget", and
`resource-summary`, which does exist, is collected only when the performance category is asked
for, which five of the six audits in that job do not do.

The fix moved the **counts** into `make verify`, as `tests/test_accessibility.py::TestTheResourceBudget`,
where a subresource added to any of 617 pages fails the build. It did not move the
`resourceSizes` lines or the `timings` lines, and the ledger has carried the honest row ever
since: "Resource transfer sizes and timings ... **nothing** ... Gate: NONE".

An honestly declared unenforced gate is much better than a dishonestly declared one, and it is
still a budget nothing reads. Two things follow from leaving it there. The first is that the
document budget is the one line in that file that constrains anything this project actually does:
the site ships no subresources, so every other size line is zero of nothing, while the state
pages grow a row per institution and are the pages a size budget is about. The second is the
shape of the defect rather than the instance: the counts were fixed one line at a time, and a
seventh `resourceSizes` line or a fourth timing added later by somebody who assumed the file was
wired up would be the original defect again, with nothing anywhere to notice.

## Decision

Enforce every line a static checker can honestly hold, and put every line it cannot into a
register that says why.

1. **The `resourceSizes` lines are enforced from the built bytes**, in `make verify`, over the
   six-page fixture and again over all 617 pages of the committed build
   (`TestTheTransferSizeBudget`). The numbers are read out of `lighthouse-budget.json` rather
   than restated in the test, so the file is the declaration and the input, and widening the
   budget widens the test.

2. **The check states what it measures, because it is not the same number Lighthouse measures.**
   Lighthouse's `transferSize` is the response body after any content-encoding plus the response
   headers; this reads the bytes the generator wrote. Served uncompressed on 2026-08-27, the
   state/CA page was 67,061 bytes on disk and 67,250 of `transferSize`, the difference being the
   header block; served from anything that compresses, which is every host this site would be
   published from, the on-disk figure is far the larger. So the static check is stricter than the
   wire in the ordinary case and looser by a few hundred bytes in the pathological one. That is
   written into the test's docstring and into the README rather than glossed as equivalence.

3. **A page that fetches something is reported as unweighable, not as zero.** The non-document
   size lines are zero only because nothing is fetched. A page with a request has a weight that
   is not on disk, and the checker says so rather than returning a number it invented, which is
   the same refusal `disclosed national` and the Scorecard walk already make about their own
   inputs.

4. **The budget is a published figure, and the headroom with it.** The README states the 80 KiB
   document line and the 65.5 KiB largest published page, and both are recomputed from the file
   and the build. A budget derived from a file stops being a gate the moment the file can be
   widened quietly, and a budget with a hundredfold of slack passes for the same reason a gate
   that cannot fail does.

5. **Every line is in one of two registers, and the registers are checked both ways.**
   `TestEveryBudgetLineIsAccountedFor` fails on a budget line no check claims and no reason
   excuses, and fails on a register entry naming a line the file no longer carries. This is the
   part that closes the class rather than the instance.

6. **The three timing lines stay enforced by nothing, and the ledger keeps saying so.** They need
   a rendering engine: a layout shift and a blocking time are facts about a browser's main
   thread, and a paint time is a fact about a machine and a simulated network as much as about a
   document. A test fails if the ledger stops naming them.

## What was measured

`lighthouse@12` (12.8.2), simulated throttling, mobile form factor, against the built site served
locally by `python -m http.server`, on 2026-08-27:

| Page | Bytes on disk | `transferSize` | LCP | CLS | TBT |
|---|---|---|---|---|---|
| home | 10,414 | 10,603 | 752 ms | 0 | 0 ms |
| state/CA (the largest) | 67,061 | 67,250 | 1,052 ms | 0 | 0 ms |

Both are inside the budget file's 1500 ms line. Neither is evidence about the runner
`accessibility.yml` uses: that is ubuntu-latest, with Lighthouse's 4x CPU slowdown applied to a
machine this project has never measured, and 1,052 of 1,500 is not the headroom on which to
calibrate a gate from somebody's laptop. A timing gate set here and red on the runner would be
worse than the NONE row, because it would be fixed by widening the budget under deadline.

So the runner gets measured first. This is the rule `docs/adr/0007` had just finished writing
down for the Credential Registry join, applied to this project's own CI: measure the precondition
before designing around it, and publish the measurement rather than the guess.

## Consequences

- The metrics ledger's single "transfer sizes and timings / NONE" row becomes three rows: the
  sizes at AUTO, the timings at NONE with the local measurement and the stated precondition, and
  the register completeness check at AUTO.
- `make verify` gains a size ceiling on every generated page. A template change that adds
  15 KiB to a state page fails the build rather than the browser.
- The README carries two more figures that a test recomputes, which is two more ways for a prose
  edit to fail the suite. That is the intended cost; it is the same trade `tests/test_doc_counts.py`
  already makes for every other number this project prints.
- Adding a line to `lighthouse-budget.json` now requires deciding, in the same commit, whether it
  is enforced or unenforceable, and writing the sentence either way.
- A future timing gate has a stated precondition and a stated first step, and it inherits the
  measurement above as the thing to compare the runner against.

## Alternatives considered

- **Turn the timing lines into a CI gate now, using the home page report the job already
  produces.** Rejected, and it was close: `--only-categories=...,performance` on the home page
  already yields LCP, CLS and TBT, so the data is there and a scoring step would be a few lines.
  It was rejected because the numbers above are a laptop's, the largest page is not the page that
  job collects timings for, and a gate that goes red on its first real run gets widened rather
  than investigated. The right first commit is the runner's own measurement.
- **Enforce the size budget against gzipped bytes, to match `transferSize` more closely.**
  Rejected. It would compare against a compression setting no committed file records and this
  project does not control, and the answer would move when a host changed its encoder. The bytes
  the generator writes are the thing this project decides, and the relationship to the wire is
  stated instead of simulated.
- **Derive the counts from the budget file too, for symmetry.** Rejected: it would weaken a
  passing guard. `TestTheResourceBudget` refuses *every* request, while the file names five types,
  so deriving the counts from it would stop failing on a resource of a type nobody thought to
  budget. The counts stay stricter than the file, and the register records which check holds each
  count line.
- **Delete the timing lines, since nothing enforces them.** Rejected. They are a readable
  statement of intent and the thing the runner measurement will be compared against; deleting them
  would turn an unenforced budget into no budget, which is not an improvement in honesty, only in
  tidiness.
