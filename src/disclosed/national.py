"""The national picture, reduced to something small enough to commit.

Grading the IPEDS directory produces a 2.5 MB payload with a row for every institution in the
country. That file is regenerable in a minute from two public archives that need no key and no
quota, so committing it would trade the readability of the history for nothing, which is the same
argument the daily snapshot workflow already makes about the full graded report.

What does have to be committed is every claim the site makes, because the site is built from the
published data and must not be able to say anything the data does not contain. So this module
reduces the national run to two things: the per-field counts behind every percentage, and the
institutions named in the findings.

Which institutions get named is a rule, not a judgement call made per finding. A field with a
statute behind it (:attr:`disclosed.fields.Field.statute`) is one an institution can be measured
against a requirement somebody else enacted, and those institutions are named. A field without one
is this project's opinion about what a college ought to publish, and those institutions are
counted and not named. Naming a college for falling short of a standard nobody wrote is closer to
a pillory than to a scorecard.

The counts keep suppressed and not-applicable outside the denominator, exactly as the grade does.
:attr:`FieldCoverage.share_reported` is ``None`` and never ``0.0`` when nothing was applicable,
because a field that reached nobody has no reporting rate, and a zero there would read as a field
that everybody failed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .disclosure import Disclosure
from .fields import IPEDS_FIELDS, Field
from .scope import Scope, scope_from_payload

__all__ = ["FieldCoverage", "NamedGap", "build", "coverage_for"]

_OUT_OF_DENOMINATOR = frozenset({Disclosure.SUPPRESSED.value, Disclosure.NOT_APPLICABLE.value})


@dataclass(frozen=True, slots=True)
class NamedGap:
    """One institution that owed a statute-backed disclosure and where the record carries none.

    Every field is ``str | None`` for the reason the whole project exists: an institution the
    source did not name must render as unnamed, never as the four-character string ``"None"``,
    and a row with no unit id must not be publishable as though it were traceable to a record.
    """

    unit_id: str | None
    name: str | None
    state: str | None


@dataclass(frozen=True, slots=True)
class FieldCoverage:
    """What a whole population did with one field."""

    label: str
    key: str
    statute: str
    applicable: int
    reported: int
    missing: int
    implausible: int
    suppressed: int
    not_applicable: int

    @property
    def share_reported(self) -> float | None:
        """Share of applicable institutions that published, or ``None`` when none applied.

        ``None`` rather than ``0.0``. A field that applied to nobody has no reporting rate at all,
        and publishing a zero would say every institution it reached had failed it, which is the
        opposite claim.
        """
        if self.applicable == 0:
            return None
        return self.reported / self.applicable

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "key": self.key,
            "statute": self.statute,
            "applicable": self.applicable,
            "reported": self.reported,
            "missing": self.missing,
            "implausible": self.implausible,
            "suppressed": self.suppressed,
            "not_applicable": self.not_applicable,
            "share_reported": self.share_reported,
        }


def coverage_for(
    grades: list[dict[str, Any]], *, fields: tuple[Field, ...] = IPEDS_FIELDS
) -> tuple[FieldCoverage, ...]:
    """Count how a population disclosed each field.

    Args:
        grades: The ``grades`` list of a graded payload, each row carrying a ``fields`` mapping of
            field label to classification word.
        fields: The graded field set, used for the order, the key and the statute. Read from the
            field definitions rather than from the payload so that a field which has since been
            renamed cannot silently appear as a second, empty column.

    A field absent from a row is not counted in any bucket. A row that never carried a field has
    not told us that the institution failed it, and inventing a classification here would be the
    null-versus-zero bug arriving through the back door.
    """
    coverages: list[FieldCoverage] = []
    for field in fields:
        counts: dict[str, int] = {}
        for row in grades:
            published = row.get("fields")
            if not isinstance(published, dict):
                continue
            state = published.get(field.label)
            if isinstance(state, str):
                counts[state] = counts.get(state, 0) + 1
        applicable = sum(v for k, v in counts.items() if k not in _OUT_OF_DENOMINATOR)
        coverages.append(
            FieldCoverage(
                label=field.label,
                key=field.key,
                statute=field.statute,
                applicable=applicable,
                reported=counts.get(Disclosure.REPORTED.value, 0),
                missing=counts.get(Disclosure.MISSING.value, 0),
                implausible=counts.get(Disclosure.IMPLAUSIBLE.value, 0),
                suppressed=counts.get(Disclosure.SUPPRESSED.value, 0),
                not_applicable=counts.get(Disclosure.NOT_APPLICABLE.value, 0),
            )
        )
    return tuple(coverages)


def _identity(row: dict[str, Any], key: str) -> str | None:
    value = row.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def named_gaps(
    grades: list[dict[str, Any]], *, fields: tuple[Field, ...] = IPEDS_FIELDS
) -> dict[str, list[NamedGap]]:
    """Institutions that owed a statute-backed disclosure the record does not carry.

    Only fields with a statute produce a list; see the module docstring for why. Sorted by unit id
    so that regenerating the artifact from the same run produces the same bytes and a diff in the
    committed file means an institution's disclosure changed.
    """
    gaps: dict[str, list[NamedGap]] = {}
    for field in fields:
        if not field.statute:
            continue
        found = [
            NamedGap(
                unit_id=_identity(row, "unit_id"),
                name=_identity(row, "name"),
                state=_identity(row, "state"),
            )
            for row in grades
            if isinstance(row.get("fields"), dict)
            and row["fields"].get(field.label) == Disclosure.MISSING.value
        ]
        gaps[field.label] = sorted(found, key=lambda g: (g.unit_id or "", g.name or ""))
    return gaps


def build(report: dict[str, Any], *, fields: tuple[Field, ...] = IPEDS_FIELDS) -> dict[str, Any]:
    """Reduce a graded national payload to the committable artifact the site is built from.

    Raises:
        ValueError: If the payload is not a national run. Reducing a sample through here would
            produce a file called ``national.json`` whose numbers describe 600 institutions in 13
            states, and every downstream reader of that file would be entitled to believe
            otherwise. Refusing is the only safe behaviour; there is no correct way to relabel a
            sample.
    """
    scope: Scope | None = scope_from_payload(report)
    if scope is None or not scope.is_national:
        raise ValueError(
            "refusing to build a national artifact from a run that did not cover the population; "
            "the file would state a national figure for a sample"
        )
    grades = list(report.get("grades", []))
    return {
        "scope": scope.as_dict(),
        "fields": [c.as_dict() for c in coverage_for(grades, fields=fields)],
        "gaps": {
            label: [{"unit_id": g.unit_id, "name": g.name, "state": g.state} for g in institutions]
            for label, institutions in named_gaps(grades, fields=fields).items()
        },
        "contradictions": report.get("contradictions", []),
        "ungradeable": report.get("ungradeable", 0),
    }
