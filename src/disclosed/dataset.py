"""CSV export, and the Table Schema that says what an empty cell means.

A CSV is where this project's whole argument is easiest to lose. An empty cell is the most
overloaded token in data publishing: it can mean not collected, not applicable, withheld, or zero,
and a spreadsheet will render all four identically and then let someone average them.

So the export never uses an empty cell to carry a classification. Every graded field is exported
as a word (``reported``, ``missing``, ``suppressed``, ``not_applicable``, ``implausible``), and a
reader who opens this in Excel cannot accidentally read an absence as a number because there is no
number there to read.

Exactly one column is allowed to be empty, ``disclosure_score``, and only for an institution with
an empty denominator. Because an empty numeric cell is ambiguous on its own, a ``gradeable``
column travels beside it stating in words why it is empty. That redundancy is the point: it is
cheap, and it means the file survives being opened by a tool that helpfully converts blanks to
zero.

The CSV and the schema are generated from the same field definitions in the same pass, so the
published schema cannot drift away from the published data. Describing columns by hand in a JSON
file is exactly the kind of second source of truth this project keeps finding other people's bugs
in.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any, Final

from .disclosure import Disclosure
from .fields import FIELDS, Field

__all__ = ["DISPUTED_COLUMN_SUFFIX", "IDENTITY_COLUMNS", "to_csv", "to_schema"]

# Fixed leading columns, in this order. ``gradeable`` sits next to ``disclosure_score`` so that
# the reason a score cell is empty is always visible in the adjacent column.
IDENTITY_COLUMNS: Final[tuple[tuple[str, str, str], ...]] = (
    (
        "unit_id",
        "string",
        "Federal IPEDS unit id. Empty only when the source published no identifier, in which "
        "case the row cannot be joined to anything and is included for completeness of counts.",
    ),
    (
        "name",
        "string",
        "Institution name exactly as published. Empty when the source did not name the "
        "institution; never the string 'None'.",
    ),
    (
        "state",
        "string",
        "Two-letter state or territory code as published by the source.",
    ),
    (
        "disclosure_score",
        "number",
        "Share of applicable fields the institution actually published, weighted. EMPTY, never "
        "zero, when every graded field was suppressed or inapplicable and there is therefore "
        "nothing to score. See the gradeable column. A genuine 0 means the institution was "
        "gradeable and published nothing, which is a different fact.",
    ),
    (
        "gradeable",
        "boolean",
        "Whether the institution had any applicable field to be graded on. False means "
        "disclosure_score is empty by definition rather than by accident.",
    ),
    (
        "letter",
        "string",
        "Letter band for disclosure_score. Empty exactly when gradeable is false.",
    ),
)

_CLASSIFICATION_VALUES: Final[tuple[str, ...]] = tuple(d.value for d in Disclosure)


#: Appended to a field's column name to make the column stating whether the institution has
#: filed a dispute about it. A separate column rather than a value inside the classification
#: column, because a dispute is not a sixth state and must never be read as one: the grade in
#: that cell is exactly what it was, and this says only that somebody has said so in writing.
DISPUTED_COLUMN_SUFFIX: Final[str] = "_disputed"


def _row(
    record: dict[str, Any],
    fields: tuple[Field, ...],
    disputed: frozenset[tuple[str, str]] = frozenset(),
) -> dict[str, str]:
    score = record.get("score")
    gradeable = score is not None
    row: dict[str, str] = {
        # An absent identity is an empty cell, never the word "None". str(None) is how a missing
        # name becomes a four-character institution name in every downstream reader.
        "unit_id": str(record.get("unit_id") or ""),
        "name": str(record.get("name") or ""),
        "state": str(record.get("state") or ""),
        "disclosure_score": "" if score is None else f"{float(score):.6f}",
        "gradeable": "true" if gradeable else "false",
        "letter": str(record.get("letter") or ""),
    }
    published = record.get("fields", {})
    unit_id = row["unit_id"]
    for field in fields:
        # Absence of a classification is itself stated as a word. An empty cell here would put the
        # export right back into the ambiguity the whole file is arranged to avoid.
        row[field.column] = str(published.get(field.label) or "not_in_report")
        # ``false`` and never an empty cell, for the same reason. A blank would mean "no
        # dispute", "not checked" and "this row predates the column" identically, and this
        # file exists because those are three different facts.
        row[field.column + DISPUTED_COLUMN_SUFFIX] = (
            "true" if (unit_id, field.label) in disputed else "false"
        )
    return row


def to_csv(
    report: dict[str, Any],
    *,
    fields: tuple[Field, ...] = FIELDS,
    disputed: frozenset[tuple[str, str]] = frozenset(),
) -> str:
    """Render a graded report as CSV.

    Rows are sorted by unit id so that regenerating the file from the same report produces the
    same bytes and a diff means the data moved. ``\\r\\n`` line endings are used because RFC 4180
    specifies them and some readers still care.

    Args:
        report: A payload as written by ``disclosed grade``.
        fields: The graded fields to export a column pair for.
        disputed: ``(unit_id, field label)`` pairs an institution has filed a dispute about.
            The grade in the classification column is unchanged; the dispute column beside it
            says only that somebody has said in writing that it is wrong.
    """
    columns = [name for name, _, _ in IDENTITY_COLUMNS]
    for field in fields:
        columns += [field.column, field.column + DISPUTED_COLUMN_SUFFIX]
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, lineterminator="\r\n")
    writer.writeheader()
    for record in sorted(report.get("grades", []), key=lambda r: str(r.get("unit_id") or "")):
        writer.writerow(_row(record, fields, disputed))
    return buffer.getvalue()


def to_schema(*, fields: tuple[Field, ...] = FIELDS, path: str = "dataset.csv") -> dict[str, Any]:
    """Build a Table Schema (frictionless tabular-data-resource) description of the export.

    ``missingValues`` is declared as the empty string and nothing else. Left at its default, a
    consumer is free to guess that ``0``, ``NA`` or ``-1`` also mean missing, and this project
    exists because of what happens when people guess about that.
    """
    schema_fields: list[dict[str, Any]] = [
        {"name": name, "type": kind, "description": description}
        for name, kind, description in IDENTITY_COLUMNS
    ]
    for field in fields:
        schema_fields.append(
            {
                "name": field.column,
                "type": "string",
                "description": (f"How {field.label} was disclosed. {field.rationale}"),
                "constraints": {"enum": [*_CLASSIFICATION_VALUES, "not_in_report"]},
                "source_key": field.key,
                "weight": field.weight,
            }
        )
        schema_fields.append(
            {
                "name": field.column + DISPUTED_COLUMN_SUFFIX,
                "type": "boolean",
                "description": (
                    f"Whether the institution has filed a dispute about how {field.label} "
                    "was classified, committed under disputes/ and rendered on its page. The "
                    "classification in the column before this one is unchanged: a dispute is "
                    "published beside a finding and never folded into one, so this is not a "
                    "sixth state and must not be read as one. Never empty."
                ),
                "constraints": {"enum": ["true", "false"]},
            }
        )
    return {
        "profile": "tabular-data-resource",
        "name": "disclosed",
        "path": path,
        "format": "csv",
        "mediatype": "text/csv",
        "encoding": "utf-8",
        "title": "Disclosure grades for US higher-education institutions",
        "description": (
            "Grades institutions on what they disclose rather than on how they perform. Each "
            "graded field is exported as a classification word rather than as a value, so that "
            "an unreported field, a field suppressed to protect a small cohort, a field that "
            "does not apply, and a genuine zero remain four distinguishable facts."
        ),
        "licenses": [{"name": "Apache-2.0", "path": "https://www.apache.org/licenses/LICENSE-2.0"}],
        "sources": [
            {
                "title": "College Scorecard",
                "path": "https://collegescorecard.ed.gov/data/",
            }
        ],
        "schema": {
            "fields": schema_fields,
            "primaryKey": ["unit_id"],
            "missingValues": [""],
        },
    }


def to_schema_json(**kwargs: Any) -> str:
    """Serialize the schema deterministically, so regenerating it is a no-op in git."""
    return json.dumps(to_schema(**kwargs), indent=2, sort_keys=True) + "\n"
