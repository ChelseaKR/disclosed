"""Disclosure drift: what a publisher used to tell you and no longer does.

A single snapshot cannot distinguish a field that was never collected from a field that was
collected until recently and then stopped. Only the comparison between runs can, and the difference
matters: the first is a gap in the data model, the second is a change in what the public is allowed
to know.

The unit of interest is the field, not the institution. One college dropping a field is a data-entry
event. Four hundred colleges dropping the same field between two runs is a policy change, and that
is what :func:`compare` is built to surface.

Drift is reported in both directions. Fields that started being reported are as real a finding as
fields that stopped, and reporting only the losses would make the project an argument rather than a
measurement.
"""

from __future__ import annotations

from dataclasses import dataclass

from .disclosure import Disclosure
from .grading import InstitutionGrade

__all__ = ["FieldDrift", "Snapshot", "compare", "snapshot"]


@dataclass(frozen=True, slots=True)
class Snapshot:
    """Per-field disclosure counts for one run, keyed by field label."""

    taken: str
    """Caller-supplied run identifier, typically a date. Never generated here, so that a rerun of
    the same data produces byte-identical output."""

    institutions: int
    reported: dict[str, int]
    missing: dict[str, int]


@dataclass(frozen=True, slots=True)
class FieldDrift:
    """One field's change in disclosure between two snapshots."""

    field_label: str
    was_reported: int
    now_reported: int
    delta: int
    share_of_institutions: float
    """``delta`` as a share of the smaller institution count, so a change in corpus size cannot
    masquerade as a change in disclosure."""

    @property
    def direction(self) -> str:
        return "gained" if self.delta > 0 else "lost"

    @property
    def is_systemic(self) -> bool:
        """Whether this looks like a policy change rather than scattered data entry.

        The 2 percent threshold is a judgement call and is stated in the published methodology so a
        reader can disagree with it. It is set low because a coordinated stop-reporting event is
        newsworthy well before it touches a majority of institutions.
        """
        return abs(self.share_of_institutions) >= 0.02


def snapshot(grades: list[InstitutionGrade], *, taken: str) -> Snapshot:
    """Reduce a run's grades to per-field disclosure counts."""
    reported: dict[str, int] = {}
    missing: dict[str, int] = {}
    for grade in grades:
        for result in grade.results:
            label = result.field.label
            reported.setdefault(label, 0)
            missing.setdefault(label, 0)
            if result.disclosure is Disclosure.REPORTED:
                reported[label] += 1
            elif result.disclosure is Disclosure.MISSING:
                missing[label] += 1
    return Snapshot(
        taken=taken, institutions=len(grades), reported=reported, missing=missing
    )


def compare(earlier: Snapshot, later: Snapshot) -> tuple[FieldDrift, ...]:
    """Compare two snapshots, most systemic change first.

    Fields present in only one snapshot are skipped rather than treated as a total loss or gain:
    adding a field to the graded set is a change in this project, not in the publisher, and
    conflating the two would let a local edit look like a federal policy shift.
    """
    denominator = min(earlier.institutions, later.institutions)
    if denominator == 0:
        return ()

    drifts: list[FieldDrift] = []
    for label, before in earlier.reported.items():
        if label not in later.reported:
            continue
        after = later.reported[label]
        delta = after - before
        if delta == 0:
            continue
        drifts.append(
            FieldDrift(
                field_label=label,
                was_reported=before,
                now_reported=after,
                delta=delta,
                share_of_institutions=delta / denominator,
            )
        )
    return tuple(sorted(drifts, key=lambda d: (-abs(d.share_of_institutions), d.field_label)))
