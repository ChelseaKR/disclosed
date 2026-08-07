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

__all__ = ["IDENTITY_COLUMNS", "to_csv", "to_schema"]

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


def _row(record: dict[str, Any], fields: tuple[Field, ...]) -> dict[str, str]:
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
    for field in fields:
        # Absence of a classification is itself stated as a word. An empty cell here would put the
        # export right back into the ambiguity the whole file is arranged to avoid.
        row[field.column] = str(published.get(field.label) or "not_in_report")
    return row


def to_csv(report: dict[str, Any], *, fields: tuple[Field, ...] = FIELDS) -> str:
    """Render a graded report as CSV.

    Rows are sorted by unit id so that regenerating the file from the same report produces the
    same bytes and a diff means the data moved. ``\\r\\n`` line endings are used because RFC 4180
    specifies them and some readers still care.
    """
    columns = [name for name, _, _ in IDENTITY_COLUMNS] + [f.column for f in fields]
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, lineterminator="\r\n")
    writer.writeheader()
    for record in sorted(report.get("grades", []), key=lambda r: str(r.get("unit_id") or "")):
        writer.writerow(_row(record, fields))
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
