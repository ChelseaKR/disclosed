# 0009. The Credential Registry publishes identity, not disclosure, so the adapter is not written

- Status: Accepted
- Date: 2026-08-27
- Deciders: Chelsea Kelly-Reif

## Context

`docs/adr/0007` measured the join between the Credential Registry and the two federal corpora and
answered the question the roadmap had set: 4,818 of 33,809 registry organizations publish a typed
`ceterms:ipedsID`, reaching 4,794 of the 6,163 institutions in the IPEDS directory. It then said,
in the same breath, that this does not license an adapter:

> The second open question is what CTDL would be graded *on*: this project grades published
> disclosures against duties, and whether the registry carries a duty worth grading is not
> answered by the join.

An adapter written without that answer would be a third source rendered beside two federal ones,
grading institutions on something nobody established was a disclosure. The join says the two
populations overlap. It says nothing about whether the overlap carries anything to grade.

So the same method: count it, over the whole walk, and publish the number whichever way it comes
out.

## Decision

Measure what the registry publishes, and write the adapter only if the measurement finds
something a duty could reach. It did not, so it is not written.

1. **Presence, not values.** `disclosed registry-properties` walks the same
   `resource_type=organization` set with the same adapter, the same page cache, the same
   provenance record and the same refusal to report a walk it cannot prove reached the end, and
   captures which CTDL property *names* appear on each organization's node. No value is read.
   Presence is the right shape of fact because it is the shape this project already grades: a
   required disclosure is present or it is not, and a property nobody publishes cannot be a
   disclosure anybody is failing to make.

2. **Two denominators, never summed**, the rule ADR 0007 set. Over the whole registry a rate
   describes a population that is mostly training providers who were never in IPEDS. Over the
   4,818 organizations that publish an IPEDS id it describes the only population this project
   could grade. Both are in the artifact.

3. **The capture is distinct property sets with counts, not rows.** Written per organization the
   property names add about 8.5 MB to the 7.9 MB join capture. Aggregated to distinct sets they
   are 245 KiB and lose nothing any rate here depends on. What they do lose is the ability to say
   which organization carried which set, and that loss is deliberate: nothing in this measurement
   is about a named institution, and a capture that cannot name one cannot be misread as a grade.
   `Organization.as_dict` therefore leaves the two new fields out of the join capture, which is an
   omission with a reason and a test on it rather than the silent kind.

4. **The report replays byte-for-byte from the committed census** in `make verify`, and the census
   is asserted to describe the same walk as the join capture. Two files can drift; one
   measurement cannot be allowed to.

5. **The adapter is not written, and this is the sentence that says why.** Not "not yet", not
   "blocked". The measurement below is the reason.

## What the measurement says

Walked on 2026-08-27, from the same pages as the join capture: 33,809 organizations, 62 distinct
CTDL property names in use, 442 distinct property sets.

Over the 4,818 organizations that publish a typed `ceterms:ipedsID`, twelve properties are on
effectively all of them and then there is a cliff:

| Property | Organizations | Share |
|---|---|---|
| `ceterms:ctid`, `ceterms:name`, `ceterms:description`, `ceterms:address`, `ceterms:subjectWebpage`, `ceterms:agentType`, `ceterms:agentSectorType`, `ceterms:lifeCycleStatusType`, `ceterms:ipedsID` | 4,818 | 100% |
| `ceterms:opeID` | 4,793 | 99.5% |
| `ceterms:identifier` | 4,733 | 98.2% |
| `ceterms:fein` | 4,710 | 97.8% |
| `ceterms:email`, the next most common | 52 | 1.1% |

Every one of the twelve is identity, location, a self-description or a federal id. Not one of
them is a disclosure with a duty behind it. The nearest thing the vocabulary has to a cost
disclosure, `ceterms:hasCostManifest`, is on **6** of the 4,818 (0.12%). `ceterms:offers` is on
10 (0.21%), `ceterms:accreditedBy` on 17 (0.35%), `ceterms:approvedBy` on 3.

Two further facts make the shape of the set plain. **4,627 of the 4,818, or 96.0%, carry exactly
the same twelve properties**: 34 distinct property sets across 4,818 organizations, against 442
across the registry as a whole. And **4,730 of them, 98.2%, carry a free-text identifier whose
type name is `IPEDS NCES Data Year`**, which ADR 0007 already had to name as a year rather than a
unit id.

That is not 4,818 institutions describing themselves. It is a directory loaded from IPEDS,
dated to the collection year this project already reads from IPEDS directly. The 77.8% coverage
ADR 0007 measured is real, and what it covers is a copy of one of the two corpora already graded.

## Consequences

- **Milestone 1's adapter is not written, and the milestone closes rather than waiting.** The
  roadmap's Credential Registry entry becomes a finding instead of a plan, and the README's
  source table row changes from "adapter unwritten" to "measured, and there is nothing here to
  grade". Both open questions ADR 0007 left are now answered, in opposite directions: the join is
  good, and the thing it joins to publishes nothing this project could grade an institution on.
- **What would reopen it, stated so it can be checked rather than remembered.** If the
  registry's postsecondary organizations began publishing `ceterms:hasCostManifest`,
  `ceterms:availabilityListing` or another property that carries a published duty at a rate that
  is not a rounding error, the measurement would say so on a rerun, and the argument for an
  adapter would be that number. `make registry-properties` is the rerun.
- **The 25% limit ADR 0007 wrote down is not spent.** It was the constraint a future adapter
  would inherit; there is no adapter to inherit it, and it stays in ADR 0007 for whoever revisits
  this.
- Two committed artifacts: `data/registry/properties.json` (403 KB, the capture) and
  `data/registry-properties.json` (17 KB, the report). Together they are 5% of the join capture
  already in the repository.
- `make verify` gains a fourth replay contract.

## Alternatives considered

- **Write a thin adapter that grades registry presence itself**, for example "this institution is
  in the registry / is not". Rejected, and it is worth being explicit about why, because it is
  the tempting version. Publication to the Credential Registry is not a duty this project has
  found published anywhere, and grading an institution on a voluntary listing would be grading
  participation rather than disclosure. It would also be grading a bulk load: 96% of the joined
  records carry an identical property set, so the "disclosure" being graded would be somebody
  else's import job.
- **Read the values, not just the property names.** Rejected as unnecessary and as a bigger
  claim than the question needs. If a property is on 0.12% of the population, what is inside it
  cannot make it a gradeable disclosure for the population, and reading values would multiply the
  capture size and put registry-published prose into this repository for no decision it changes.
- **Measure over the credentials resource type as well** (`resource_type=credential`, 133,346
  records on 2026-08-15). Rejected for this decision, and it is a real limit on it rather than a
  dismissal: a credential is not an institution, and this project grades institutions on
  institution-level duties. If the project ever grades programmes, the credential type is where
  that measurement would start, and it has not been walked.
- **Say the answer is "not yet" and leave the milestone open.** Rejected. It is the shape of a
  decision that never gets revisited and never gets closed. The measurement is what would have to
  change, and the ADR says which numbers.
