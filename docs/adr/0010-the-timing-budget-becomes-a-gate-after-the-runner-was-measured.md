# 0010. The timing budget becomes a gate, after the runner was measured rather than assumed

- Status: Accepted
- Date: 2026-08-28
- Deciders: Chelsea Kelly-Reif

(ADR 0009 was accepted the same day on a separate branch, about the Credential Registry. The
numbering is a log, not an order of work.)

## Context

`docs/adr/0008` moved the `resourceSizes` lines of `lighthouse-budget.json` into `make verify`,
where a static checker can hold them from the built bytes, and left the three `timings` lines
enforced by nothing with the reason written down: a layout shift and a blocking time are facts
about a browser's main thread, and a paint time is a fact about a machine and a simulated network
as much as about a document.

It also refused a shortcut that was available. The `accessibility.yml` job already audits the
home page with `--only-categories=...,performance`, so LCP, CLS and TBT were already in a report
sitting on the runner's disk; a scoring step would have been a few lines. ADR 0008 rejected that:

> It was rejected because the numbers above are a laptop's, the largest page is not the page that
> job collects timings for, and a gate that goes red on its first real run gets widened rather
> than investigated. The right first commit is the runner's own measurement.

That is the same rule `docs/adr/0007` had just written down for the Credential Registry join,
pointed at this project's own CI. This ADR is the second half of it.

## What was measured

The largest page in the site, `state/CA`, was added to the audits that collect the performance
category, and a step was added that reported the metrics without gating on them. Run
33129896655, on `ubuntu-latest`, `lighthouse@12` (12.8.2), 2026-08-28:

| Page | Runner LCP | Laptop LCP (ADR 0008) | CLS | TBT | Runner `transferSize` |
|---|---|---|---|---|---|
| home | 751.67 ms | 752.30 ms | 0 | 0 ms | 10,638 |
| state/CA | 1052.39 ms | 1051.81 ms | 0 | 0 ms | 67,285 |

The two machines differ by **less than one millisecond on both pages**. That is not a
coincidence and it is the fact that decides this ADR: Lighthouse's default throttling is
*simulated* (Lantern), so the network is a model rather than a measurement, and for a document
with no subresources and no script the result barely depends on the host at all. The worry ADR
0008 recorded, that 1,052 of 1,500 was not headroom to calibrate a gate on from the wrong
machine, turned out to be a worry about a difference that is not there. It was still the right
order to do it in: the alternative was believing that in advance.

## Decision

Gate the three timing lines in `accessibility.yml`, and keep the budget where it is.

1. **A script, not a shell one-liner.** `.github/scripts/check_lighthouse_timings.py` reads the
   budget out of `lighthouse-budget.json` and holds each named report to it. It sits in the
   directory `make verify` already lints, types and covers, for the reason recorded in the
   Makefile when `check_site_origin.py` moved into scope: the thing that decides whether a build
   passes cannot be the one file nobody checks.

2. **Three failures, and they are three different failures.** A metric over its budget. A metric
   the report does not carry, because Lighthouse collects timings only when the performance
   category is requested, and treating an absent audit as a pass is how this gate would silently
   stop applying the day somebody trimmed a `--only-categories` flag. And a report that was never
   written, which is the rule the accessibility scorer in the same job already states in words: a
   pass over a partial set is not a pass. An absent measurement is never read as a zero, which
   matters most for `cumulative-layout-shift`, where zero is the best possible result.

3. **A budget file with no timing lines exits 2 rather than passing.** Delete the three lines and
   every report is trivially inside the budget. That is the exact state the whole file was in
   while three documents called it a gate, and the script refuses to be that.

4. **The gate covers the home page and the largest page.** Not all six: the other four share the
   same templates, and the home page at 10 KB and `state/CA` at 67 KB bracket what the site
   produces. The largest page is the one a timing budget is actually about, and it was not being
   measured at all before this.

5. **The budget stays at 1500 ms rather than being tightened to the measurement.** 1,052 ms
   leaves 30% of headroom, and a budget set just above today's number is a budget that fails on
   the first ordinary change and gets widened under deadline. The number to watch is in the
   ledger and in the README; a page that grows past 1,500 ms is a real regression rather than a
   rounding one.

## Consequences

- The metrics ledger's `Lighthouse timings` row moves from **NONE** to **AUTO**, and the ledger
  now has no row claiming a gate this project does not have. That is the whole point of the
  phase: every line of `lighthouse-budget.json` is enforced by something named.
- `TestEveryBudgetLineIsAccountedFor._NOT_STATICALLY_ENFORCEABLE` empties out. The register stays,
  because the class it guards is "a budget line nothing reads", not "a timing line nothing
  reads": the next line somebody adds to that file still has to be enforced or explained.
  `tests/test_accessibility.py` keeps failing on a line in neither register, and the test that
  required the ledger to keep naming the unenforced metrics now requires the opposite, that no
  metric is in the unenforceable register while the job gates it.
- `accessibility.yml` costs one more Lighthouse category on one more page, which is seconds.
- A page that adds 500 ms of paint time now fails CI. That is the intended cost and the reason
  for the 30% of headroom rather than 3%.

## Alternatives considered

- **Gate all six audited pages.** Rejected as cost without information: four of them are the same
  templates as the two, and the job would run four more performance audits to learn what the
  bracketing pair already says.
- **Tighten the budget to just above the measurement**, for example 1,200 ms. Rejected. It reads
  as rigour and behaves as a tripwire: the first ordinary content change fails the build, and the
  fix under deadline is to widen the number rather than to look at the page. ADR 0008 already
  made widening visible by putting the budget in the README with a test on it; a budget that
  needs widening monthly defeats that.
- **Enforce the timings in `make verify` instead**, with a headless browser. Rejected. It would
  put Chrome in the local gate for three numbers that a CI job already has in hand, and
  `make verify` is fast, offline and browserless on purpose.
- **Leave the row at NONE, since the sizes are gated and the timings correlate with them.**
  Rejected. It is true that the document size dominates the paint time here, and that is an
  argument, not a measurement. This project spends its time telling other people the difference.

## Amendment 2026-09-01: the total-blocking-time line was set to a single observation

Decision 5 above kept `largest-contentful-paint` at 1,500 ms rather than tightening it to the
1,052 ms that was measured, and wrote down why: a budget set just above today's number fails on
the first ordinary change and gets widened under deadline. That reasoning was applied to the one
line the table above had a non-zero number in, and not to the other two, which were left at the
`0` they had been carrying since before anything read this file. For `cumulative-layout-shift`
that is right: the site ships no script, no image, no stylesheet and no font — `resourceCounts`
budgets all four at 0 and `TestTheResourceBudget` holds every one of the 617 built pages to it —
so there is no source of a layout shift on the page, and 0 is a property of the document rather
than a reading off a machine.

For `total-blocking-time` it was wrong, and the gate said so on its own next run.
[Run 33139828844](https://github.com/ChelseaKR/disclosed/actions/runs/33139828844) reported
`home.json: total-blocking-time is 34 against a budget of 0` on a tree whose only change since
the measuring run was this gate. Nothing about the page moved. Total blocking time is the part of
every main-thread task over 50 ms between first contentful paint and time to interactive, and on
a shared `ubuntu-latest` runner a document can accumulate that from parse, style and layout under
somebody else's CPU load. The 0 ms in the table is one sample of a noisy measurement, which is
exactly the thing decision 5 refuses to calibrate on.

**The line moves to 200 ms**, which is where Lighthouse's own scoring curve stops calling total
blocking time good. It is a published third-party boundary rather than a number read off this
project's runner, which is the property that makes it a budget instead of a tripwire. A
script-free document that blocks the main thread for a fifth of a second is a regression in the
document; 34 ms is a busy runner.

The alternative was to move the line into
`TestEveryBudgetLineIsAccountedFor._NOT_ENFORCED` with "too noisy on a shared runner" as the
written reason, and to put the ledger row back to NONE for it. Rejected: the metric is not
unmeasurable, it was budgeted at a number no measurement supports. A gate held at a defensible
threshold is worth more than a gate deleted, and the register exists for lines nothing can hold,
not for lines somebody set badly.
