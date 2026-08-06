# disclosed

**Grades US higher-education institutions on what they disclose, not on how they perform.**

There are many tools that will tell you a college's graduation rate. There is no tool that tells you
how many colleges did not report one, or which fields quietly stopped being published this year.
That is what this grades.

The distinction matters because the two failures look identical on a page. A college with a 0%
admission rate and a college that never reported an admission rate both render as a blank or a zero
in most tools, and a reader cannot tell them apart. In a 600-institution sample of the College
Scorecard, **64% publish no admission rate at all**, and separately, **one institution publishes an
admission rate of exactly zero** — which is not a school that admitted nobody, it is a reporting
artifact that survived because zero is a legal number.

## What it does

Every value from a publisher is classified before anything else touches it:

| Classification | Meaning | Counts against the publisher? |
| --- | --- | --- |
| `REPORTED` | A credible value they actually disclosed | — |
| `IMPLAUSIBLE` | Disclosed, but outside the credible range for that field | **Yes** |
| `SUPPRESSED` | Withheld deliberately, usually to protect a small cohort | No |
| `NOT_APPLICABLE` | The question doesn't apply to this institution | No, and it leaves the denominator |
| `MISSING` | No value and no stated reason | **Yes** |

Suppression is a policy decision made for good reasons and is never held against anyone. Punishing
an institution for protecting a twelve-person cohort would push publishers toward disclosing things
they shouldn't, which is the opposite of the point.

An institution whose every field is suppressed gets **no grade at all**, not a zero. The project
applies its own discipline to itself: absence is reported as absence.

## Disclosure drift

A single snapshot can't distinguish a field that was never collected from one that was collected
until recently and then stopped. Only the comparison between runs can, and the difference matters:
the first is a gap in the data model, the second is a change in what the public is allowed to know.

The unit of interest is the field, not the institution. One college dropping a field is a data-entry
event. Four hundred colleges dropping the same field between two runs is a policy change. Drift is
reported in both directions — fields that *started* being reported are as real a finding as fields
that stopped, and reporting only the losses would make this an argument rather than a measurement.

```
disclosed grade --out data/report.json
disclosed snapshot --taken 2026-08-05 --out data/snapshots/2026-08-05.json
disclosed drift data/snapshots/2026-07-05.json data/snapshots/2026-08-05.json
```

Snapshots are small enough to commit, so the record of what stopped being published lives in git
rather than in a bucket someone has to trust. A scheduled workflow does this daily.

## Two federal sources, one disagreement

IPEDS and the College Scorecard are published by the same department and keyed on the same unit
id, so the same institution can be checked against both. Across the 600 institutions in the
committed capture they agree on state for every one and disagree about exactly one on sector:

> **Grand Canyon University.** The College Scorecard files it as **private nonprofit**. IPEDS
> files it as **private for-profit**.

Sector decides which rules an institution answers to and which peer group it is compared against
in most analyses of federal education data. The disagreement is reported and deliberately never
resolved: deciding which federal source is correct is not something this project is in a position
to do, and quietly preferring one would throw away the only interesting part of the observation.

IPEDS also carries public disclosures the Scorecard does not. Among institutions that participate
in Title IV and enrol first-time undergraduates, **the federal record carries no net price
calculator for 34 of them**, a calculator that
[20 U.S.C. §1015a(h)(3)](https://www.law.cornell.edu/uscode/text/20/1015a) requires and that
[§1094(a)(17)](https://www.law.cornell.edu/uscode/text/20/1094) requires them to report. Which of
the two is missing is not something a blank cell can tell you, and the finding says so rather than
picking the more dramatic reading.

Getting to 34 rather than 62 is the applicability rule doing its job: graduate-only institutions,
system offices, closures, and institutions taking no federal aid are outside the statute and leave
the denominator instead of being marked down.

### The rule is the hard part, not the threshold

The Equity in Athletics disclosure went ungraded in an earlier pass. It is blank for **4,469 of
6,163** directory rows, and almost every one of those is a college with no athletics programme, so
grading it against the directory alone would have manufactured four thousand violations. What was
missing was not a better threshold. It was a way to know who the rule applied to.

The IPEDS institutional characteristics file supplies it: each institution's own answer about
whether it belongs to a national athletic association. That moves the denominator from 6,163 to
**1,998**, of which **812 give the federal record no athletics address**.
[20 U.S.C. §1092(g)](https://www.law.cornell.edu/uscode/text/20/1092) requires those institutions
to prepare the report and make it available; it does not require them to post it, so this is
graded as a disclosure gap and stated as weaker than the net price calculator finding, not louder.

The veterans page is still not graded, and the same file would now supply a rule for it. There is
no universal requirement to publish one, and a rule about who a duty reaches is worthless when the
duty does not exist.

```
disclosed crosscheck --cache data/HD2023.zip --characteristics data/IC2023.zip \
  --source data/sample.json --out data/crosscheck.json
```

Both IPEDS files are required. If the characteristics file cannot be read the load fails rather
than returning directory-only records, because a field that silently stops being graded looks on
the page exactly like a field everybody suddenly started reporting.

## Use the data

```
disclosed site    --report data/report.json --out site --generated 2026-08-05
disclosed dataset --report data/report.json --out data/dataset.csv
```

`data/dataset.csv` ships with a Table Schema at `data/dataset.schema.json`, generated in the same
pass so the two cannot drift. Every graded field is exported as a word (`reported`, `missing`,
`suppressed`, `not_applicable`, `implausible`) rather than as a value, so no cell in a
classification column is ever empty. Exactly one column may be empty, `disclosure_score`, and only
when an institution had nothing to be graded on. A `gradeable` column travels beside it saying so,
because an empty numeric cell is ambiguous on its own and spreadsheets coerce blanks to zero.

## How we grade

Every credible range is a judgement call, so every one carries a written rationale that a graded
institution can argue with. That is the whole contract: **a scorecard that cannot be disputed line
by line is not a scorecard, it is an accusation.** The rationales are written for the reader who
thinks their institution was marked unfairly.

The 2% threshold that separates "systemic" drift from scattered data entry is likewise a judgement
call, stated here so a reader can disagree with it. It is set low because a coordinated
stop-reporting event is newsworthy well before it touches a majority of institutions.

## Sources

| Source | Status | Notes |
| --- | --- | --- |
| College Scorecard | **Live** | Public API. `DEMO_KEY` works for small runs; set `DATA_GOV_API_KEY` for a higher rate limit. |
| IPEDS | **Live** | Public bulk directory file, no key and no quota. Adds required disclosures the Scorecard doesn't carry, and lets the same institution be checked against two federal sources. |
| Credential Registry (CTDL) | Blocked | The public search endpoint returns `x-total: 0` for every query shape tried; it appears to require an API key. Adapter deliberately not stubbed in until access is confirmed. |

A partial fetch is treated as a failure, not as data. Truncation would understate disclosure across
every institution that never arrived, which looks identical to a real reporting collapse.

IPEDS states absence three different ways, all negative integers: `-1` not reported, `-2` not
applicable, `-3` not available. They are not interchangeable and only the first counts against an
institution. They are matched on the raw value rather than the normalized token, because
normalization strips the minus sign and `-2` would otherwise collide with a real measurement of
two.

## What is a sample and what is national

Two corpora, and they are never mixed.

| | Corpus | Coverage | Fields |
| --- | --- | --- | --- |
| **Sample** | College Scorecard | 600 institutions, 13 states, California 51% | earnings, completion, admission, debt, tuition, enrollment |
| **National** | IPEDS directory + characteristics | every institution there is, 6,163 | the six public disclosure addresses |

The Scorecard is a paged API, and a run that stops early is a slice. IPEDS publishes a file, so
grading it grades the population — which is what makes national claims possible at all, and only
for the fields IPEDS carries.

Coverage travels *inside* the report rather than in a paragraph on the page. Every payload carries
a `scope` block, the site prints its sentence rather than a constant, and `disclosed national`
**refuses to build** from a run that did not cover the population: there is no correct way to
relabel a sample, so the only safe answer is to fail.

```
disclosed national --report data/crosscheck.json --out data/national.json
disclosed site --report data/report.json --national data/national.json --out site --generated 2026-08-05
```

`data/national.json` is 100 KB and committed; the 2.5 MB run it reduces is not, because it is
regenerable in a minute from two public archives that need no key. Without `--national` the site
builds with no national page and makes no national claim anywhere, which is the right default: a
missing corpus should show up as missing figures, not as sample figures with the qualifier
quietly dropped.

A run recorded before `scope` existed says so on the page rather than being assumed complete.

## Development

```
make verify     # lint, typecheck, test
make grade      # fetch and grade against the live API
```

Python 3.12+, no runtime dependencies. Strict mypy, ruff, and a 90% branch-coverage gate.

## License

Apache-2.0.
