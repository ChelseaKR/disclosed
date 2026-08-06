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
rather than in a bucket someone has to trust.

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
| IPEDS | Planned | Bulk files are public and downloadable; adds fields the Scorecard doesn't carry. |
| Credential Registry (CTDL) | Blocked | The public search endpoint returns `x-total: 0` for every query shape tried; it appears to require an API key. Adapter deliberately not stubbed in until access is confirmed. |

A partial fetch is treated as a failure, not as data. Truncation would understate disclosure across
every institution that never arrived, which looks identical to a real reporting collapse.

## Development

```
make verify     # lint, typecheck, test
make grade      # fetch and grade against the live API
```

Python 3.12+, no runtime dependencies. Strict mypy, ruff, and a 90% branch-coverage gate.

## License

Apache-2.0.
