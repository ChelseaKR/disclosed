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
[`schema/classification.v1.schema.json`](../schema/classification.v1.schema.json) and printed by
`disclosed classify --schema`.

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
