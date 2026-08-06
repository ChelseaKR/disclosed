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

**Drift is a change in rate, not a change in count, and it took real history to prove it.** The
first version of this module compared reported counts and divided the difference by the number of
institutions. Run against three IPEDS collection years it produced three confident systemic
findings and all three were false. Between 2021 and 2023 the directory shrank from 6,289
institutions to 6,163, so 130 fewer institutions published a web address, and the module called
that a systemic 2.1% collapse in web address disclosure. The share of institutions publishing one
had in fact gone *up*, from 99.93% to 99.95%. Colleges closed; they did not stop reporting.
Meanwhile the one true movement in the period, the athletics disclosure climbing from 57.1% to
59.4%, ranked fourth and was never flagged at all, because 52 is a small number next to 130.

So every comparison here divides by the institutions the field applied to in that run. A field's
denominator moves for its own reasons — institutions close, open, start offering undergraduate
programmes, stop fielding teams — and a measurement that cannot see that will go on reporting the
denominator as though it were the numerator.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Final

from .disclosure import Disclosure
from .grading import InstitutionGrade

__all__ = ["SYSTEMIC_THRESHOLD", "FieldDrift", "Snapshot", "compare", "snapshot"]

# A change is called systemic when the share of applicable institutions reporting a field moves by
# at least this much. A judgement call, stated in the published methodology so a reader can
# disagree, and set low because a coordinated stop-reporting event is newsworthy well before it
# touches a majority of institutions.
#
# Three real IPEDS collection years say the bar is roughly in the right place. Across 2021, 2022
# and 2023 every year-on-year movement in the six IPEDS disclosures sits under one percentage
# point except the athletics disclosure, which rose 1.75 points in one year and 2.26 across both.
# At 2% the threshold flags that one movement and nothing else. At 1% it would report ordinary
# annual churn as policy; at 5% it would have found nothing in three years of federal data, which
# is not a measurement, it is a way of never having to say anything.
SYSTEMIC_THRESHOLD: Final[float] = 0.02

_OUT_OF_DENOMINATOR: Final[frozenset[Disclosure]] = frozenset(
    {Disclosure.SUPPRESSED, Disclosure.NOT_APPLICABLE}
)


@dataclass(frozen=True, slots=True)
class Snapshot:
    """Per-field disclosure counts for one run, keyed by field label."""

    taken: str
    """Caller-supplied run identifier, typically a date. Never generated here, so that a rerun of
    the same data produces byte-identical output."""

    institutions: int
    reported: dict[str, int]
    missing: dict[str, int]

    applicable: dict[str, int] = dataclass_field(default_factory=dict)
    """How many institutions each field reached: everything not suppressed and not inapplicable.

    Recorded rather than derived, because it is the denominator of every rate this module computes
    and it moves independently of the other two counts. Defaulted to empty so that a snapshot
    written before this existed still loads; :meth:`rate` then returns ``None`` for every field in
    it, and a comparison against it says it could not measure a rate rather than inventing one out
    of the institution count.
    """

    source: str = ""
    """Which publisher this run covered, or empty when the run did not record it.

    Carried so that :func:`compare` can refuse to put two different populations side by side. The
    two histories in this repository have no field in common, so comparing them would skip every
    field and print "no change in per-field disclosure", which reads as a reassuring finding and
    is actually a category error. Empty means unstated and is never treated as a match.
    """

    def rate(self, label: str) -> float | None:
        """Share of applicable institutions that reported a field, or ``None`` if unmeasurable.

        ``None`` and never ``0.0``. A field that reached nobody, or a snapshot that did not record
        what it reached, has no reporting rate; publishing a zero would say every institution it
        touched had failed it, which is the opposite claim.
        """
        denominator = self.applicable.get(label, 0)
        if denominator <= 0:
            return None
        return self.reported.get(label, 0) / denominator


@dataclass(frozen=True, slots=True)
class FieldDrift:
    """One field's change in disclosure between two snapshots."""

    field_label: str
    was_reported: int
    now_reported: int
    was_applicable: int
    now_applicable: int

    delta: int
    """Change in the raw count of institutions reporting. Kept because it is what a person reading
    a diff wants to see, and deliberately never used to decide whether a change is systemic."""

    rate_change: float | None
    """Change in the share of applicable institutions reporting, or ``None`` when either run could
    not measure a rate. ``None`` means unmeasured and is never read as zero."""

    @property
    def direction(self) -> str:
        """Which way disclosure moved, read from the rate and never from the count.

        A field can shed 13 reporters while the share of institutions reporting it rises, because
        131 institutions left the population underneath it. Reading the direction off the count
        printed "lost" beside a rise of 1.67 points: the count was right and the finding was
        backwards, which is worse than a wrong number because it comes with a word attached.
        """
        if self.rate_change is not None:
            return "gained" if self.rate_change > 0 else "lost"
        return "gained" if self.delta > 0 else "lost"

    @property
    def applicability_moved(self) -> int:
        """Change in how many institutions the field reached.

        Surfaced because it explains most surprising count movements. A field that lost 130
        reporters while its denominator lost 131 institutions has not lost anything.
        """
        return self.now_applicable - self.was_applicable

    @property
    def is_systemic(self) -> bool:
        """Whether this looks like a policy change rather than scattered data entry.

        Unmeasured is not systemic. A field whose rate could not be computed in one of the two runs
        has not demonstrated a change, and treating the unknown as a large movement would be the
        loudest possible version of reading an absence as a number.
        """
        return self.rate_change is not None and abs(self.rate_change) >= SYSTEMIC_THRESHOLD


def snapshot(grades: list[InstitutionGrade], *, taken: str) -> Snapshot:
    """Reduce a run's grades to per-field disclosure counts."""
    reported: dict[str, int] = {}
    missing: dict[str, int] = {}
    applicable: dict[str, int] = {}
    for grade in grades:
        for result in grade.results:
            label = result.field.label
            reported.setdefault(label, 0)
            missing.setdefault(label, 0)
            applicable.setdefault(label, 0)
            if result.disclosure not in _OUT_OF_DENOMINATOR:
                applicable[label] += 1
            if result.disclosure is Disclosure.REPORTED:
                reported[label] += 1
            elif result.disclosure is Disclosure.MISSING:
                missing[label] += 1
    return Snapshot(
        taken=taken,
        institutions=len(grades),
        reported=reported,
        missing=missing,
        applicable=applicable,
    )


def compare(earlier: Snapshot, later: Snapshot) -> tuple[FieldDrift, ...]:
    """Compare two snapshots, largest change in reporting rate first.

    Fields present in only one snapshot are skipped rather than treated as a total loss or gain:
    adding a field to the graded set is a change in this project, not in the publisher, and
    conflating the two would let a local edit look like a federal policy shift.

    Fields whose rate is unmeasurable in either run sort last, and sort last on purpose rather
    than by the accident of ``abs(None or 0)`` being small. An unknown is not a small change; it
    is not a change at all, and it is ordered behind everything that could actually be measured.

    Raises:
        ValueError: If the two snapshots name different sources. Drift between two populations is
            not drift. Because the College Scorecard and IPEDS field sets do not overlap, such a
            comparison would silently skip every field and report "no change in per-field
            disclosure", which is the most reassuring possible way of saying nothing at all.
    """
    if earlier.source and later.source and earlier.source != later.source:
        raise ValueError(
            f"refusing to compare a {earlier.source} run against a {later.source} run; "
            "these are different populations and the difference between them is not drift"
        )
    drifts: list[FieldDrift] = []
    for label, before in earlier.reported.items():
        if label not in later.reported:
            continue
        after = later.reported[label]
        was_rate = earlier.rate(label)
        now_rate = later.rate(label)
        rate_change = None if was_rate is None or now_rate is None else now_rate - was_rate
        if after - before == 0 and not rate_change:
            continue
        drifts.append(
            FieldDrift(
                field_label=label,
                was_reported=before,
                now_reported=after,
                was_applicable=earlier.applicable.get(label, 0),
                now_applicable=later.applicable.get(label, 0),
                delta=after - before,
                rate_change=rate_change,
            )
        )
    return tuple(
        sorted(
            drifts,
            key=lambda d: (
                d.rate_change is None,
                -abs(d.rate_change if d.rate_change is not None else 0.0),
                d.field_label,
            ),
        )
    )
