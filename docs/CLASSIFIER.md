# The five-state classifier, as a library

Surface revision: 1

A field an institution did not report, a field suppressed to protect a small cohort, a field that
was never asked of it, a field whose disclosed value nobody should believe, and a field whose true
value is zero are five different facts. Rendered naively, four of them become `0` on a page and a
reader cannot tell a college that admits nobody from a college that declined to say.

This repository grades US higher education on that distinction. The distinction itself is not
about higher education, so it is published here separately from the twelve federal columns it was
built for.

Nothing here adds a dependency. It is standard library only, offline, and does no network I/O.

## The Python surface

```python
from disclosed import CLASSIFICATIONS, Disclosure, classify

classify(-1, sentinels={"-1": Disclosure.MISSING})       # Disclosure.MISSING
classify("PrivacySuppressed")                            # Disclosure.SUPPRESSED
classify(0.0, zero_is_credible=False)                    # Disclosure.IMPLAUSIBLE
classify(0.0, zero_is_credible=True)                     # Disclosure.REPORTED
classify("", )                                           # Disclosure.MISSING
```

These names, and only these, are the public API. Everything else in the package is internal and
may be renamed without notice.

| Name | What it is |
| --- | --- |
| `CLASSIFICATIONS` | The five state words, as a frozen set. What a reader of an output file may meet. |
| `Disclosure` | The five states, with `is_usable` and `counts_against_publisher`. |
| `InstitutionGrade` | One institution's graded fields, as this project produces them. |
| `classify` | The classifier. One value in, one state out; never raises. |
| `grade_institution` | Grade one record against a set of fields. |
| `summarize` | Reduce graded institutions to per-field counts. |

`disclosed.rules` carries the portable rule format below. It is documented, and it is deliberately
not re-exported from the package root: the root is the small stable surface, and a caller reaching
for a rule file has already read this page.

Changing the table above means changing `Surface revision` above it, and a surface revision must
be named in `CHANGELOG.md`. `tests/test_classifier_library.py` holds all three together, so the
surface cannot move quietly. Rule-file format changes bump `RULES_FORMAT_VERSION` instead; the two
version numbers are separate because a consumer pinning a rule file needs to know whether their
file still parses, not what this site was rendered from that week.

## The rule file

A rule file states, as data, what `classify` would otherwise be told through keyword arguments at
a call site somebody has to remember to write. The schema is committed at
[`schema/classification.v1.schema.json`](../schema/classification.v1.schema.json), printed by
`disclosed classify --schema`, and **served** at the address its own `$id` names:
<https://chelseakr.github.io/disclosed/schema/classification.v1.schema.json>.

That last clause is newer than the other two. The `$id` claimed that address from the day the
schema was written, and nothing put a file there: the deployed site is exactly what
`disclosed site` writes, and that path was never among the files it wrote, so the URL a
validator dereferences returned 404 while this document, the schema and the code all described
it as published. `site.build` now writes it, stamped with the origin the build is for in the
same way canonical links are, and `.github/scripts/check_site_origin.py` refuses a build whose
published `$id` points anywhere the build does not serve.

```json
{
  "version": 1,
  "rules": [
    {
      "column": "adm_rate",
      "label": "Admission rate",
      "credible_min": 0.0,
      "credible_max": 1.0,
      "zero_is_credible": false,
      "sentinels": {"-1": "missing", "-2": "not_applicable", "-3": "suppressed"},
      "text_is_a_value": false,
      "applies_when": null
    }
  ]
}
```

`disclosed classify --rules` prints this repository's own twelve graded fields in exactly this
format, which is the worked example and also the test that the format can express the rules it
claims to.

`applies_when` names a predicate the reader implements, or is `null` for a column that applies to
every row. This build implements `is_an_institution`,`owes_a_net_price_calculator` and
`has_an_intercollegiate_athletic_program`; each reads IPEDS columns on the same row.

## `classify-csv`

```
disclosed classify-csv data/dataset.csv --rules my-rules.json --out classified.csv
```

Every column a rule names gains a `<column>_disclosure` column immediately after it, carrying one
of the five words. Columns with no rules pass through untouched, and row order is preserved.

Exit code 0 on success, 2 on a refusal.

## `classify-csv --report`

```
disclosed classify-csv their-table.csv --rules their-rules.json --report markdown
disclosed classify-csv their-table.csv --rules their-rules.json --report json --out report.json
```

Instead of rewriting the table, report on it. **Point it at any published table of numbers and a
one-page rule file, and it tells you which cells cannot be distinguished from a value nobody
measured — and it refuses to score a table it could not read.**

The report carries per-column counts of all five states, every one present with a zero rather than
omitted, and the **denominator those counts are out of**: rows read, columns in the table, columns
the rules cover, columns they do not, cells seen, cells covered, cells not covered. The
denominator is not decoration. A conformance tool that answers "0 problems" over a file whose
columns none of its rules reached has derived a clean bill of health from nothing, which is the
defect this project exists to name, committed by the instrument built to name it.

`not_covered` appears in the report and **never as a classification**. There is no sixth state and
the schema says so; a column nobody wrote a rule for is a fact about the *rule file*, not a finding
about a cell, and it sits beside the counts rather than inside them.

### Four exit codes, and why not two

| code | meaning |
| --- | --- |
| 0 | read fine; every covered cell is a reported value |
| 1 | read fine; at least one covered cell cannot be told apart from a value nobody measured |
| 2 | the input or the rule file was refused |
| 3 | there was nothing to look at — no data rows, or no rule reached a column of this table |

**An unreadable table and a clean table must not share an exit code**, and neither must a clean
table and one nothing examined. An exit code is the only part of this a script reads, so all four
are different numbers and a test asserts that they are.

The count of cells "indistinguishable from a value nobody measured" is everything the rules
covered except `reported`, and `suppressed` and `not_applicable` are inside it on purpose. That is
not a criticism of the publisher: withholding a value to protect a small cohort is the right thing
to do, and a question that does not apply was right not to be answered. The question this number
answers is what a *reader of the table* can tell apart, and in both of those cases the answer is
"not a measurement". The per-state breakdown is right there for anyone who wants to say something
narrower.

## What it refuses, and why

Both refusals are the module's reason for existing. Each has a permissive reading that would
produce a plausible file saying something false.

**A rule naming a column the CSV does not have.** The permissive reading is to classify a missing
column, which marks every row `missing` and writes a file reporting that nobody disclosed it. What
actually happened is that this file does not ask the question. That is *absence rendered as a
value* — this project's own headline failure mode, committed by the tool built to prevent it.

**A rule naming an applicability predicate this build does not implement.** The permissive reading
is "applies to everyone", which moves every row the rule never reached into the denominator and
manufactures violations out of a rule nobody wrote. Excluding a row is the conservative direction;
including one is not.

A sentinel mapping to a sixth state word, an unknown key, a `credible_min` above its
`credible_max`, a duplicated column, and a version this build does not read are all refused for
the same reason: each one is a file whose author believed something the tool would otherwise
quietly not do.
