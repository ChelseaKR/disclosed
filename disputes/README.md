# Disputes

A graded institution can say here, on its own page, that we are wrong.

The README's contract is one sentence: *a scorecard that cannot be disputed line by line is not a
scorecard, it is an accusation.* Every credible range carries a written rationale so an
institution arguing with a grade is arguing with a stated rule. This directory is the other half
— somewhere for the argument to land that a reader of the finding will actually see.

**This directory is empty on purpose.** Nothing here is a placeholder or an example. A fabricated
dispute attributed to a real college would be exactly the kind of plausible, unfounded statement
this whole project exists to object to, so the fixtures live in `tests/` and the published set is
whatever institutions have actually filed. Today that is none.

## How to file one

Open a dispute issue (`.github/ISSUE_TEMPLATE/dispute.yml`), or send a pull request adding one
file:

```
disputes/<unit_id>.json
```

```json
{
  "unit_id": "100654",
  "field": "Admission rate",
  "disputed_classification": "missing",
  "statement": "We publish our admission rate at the address below and reported it to IPEDS for the same year.",
  "evidence_url": "https://example.edu/about/admissions",
  "filed": "2026-09-07",
  "filed_by": "Office of Institutional Research, Example University"
}
```

The schema is `schema/dispute.v1.schema.json`, published at
<https://chelseakr.github.io/disclosed/schema/dispute.v1.schema.json>.

## What happens to it

It is rendered beside the finding, on that institution's page, with the statement quoted
**verbatim** and the evidence linked. `make verify` refuses a dispute naming an institution this
project does not grade, a field it does not check, or a classification the report does not give
— each of those would be a rebuttal of a finding that does not exist.

**A dispute does not change a grade.** Report bytes, grade bytes and every published figure are
identical with and without disputes, and a test asserts it. A channel that silently moved a grade
would be a scoring input wearing a comment's clothes, and the institution best at filing paperwork
would score highest. If a finding is actually wrong, the fix is a change to the rule or to the
data, in a commit that says so — and the dispute is the record of how that change was asked for.

`evidence_url` is rendered as a link and never fetched. Whether the page behind it says what the
statement says is a judgement, and this channel carries the claim, attributed, rather than
adjudicating it.
