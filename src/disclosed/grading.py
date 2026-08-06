"""Turn classified values into a disclosure grade, per institution and per group.

The grade answers one question: of the things this institution was in a position to tell the public,
how much did it actually tell them? It is deliberately not a quality measure. A well-funded
university with terrible outcomes that reports every field completely scores higher here than a
good school that reports nothing, and that is the intended behaviour. Outcomes are graded elsewhere,
by other people, using the very data this project is checking the existence of.

Two rules keep the grade honest:

Suppressed and not-applicable fields leave the denominator. Punishing an institution for protecting
a twelve-person cohort would push publishers toward disclosing things they should not, which is the
opposite of the point.

An institution with nothing left in its denominator gets no grade at all, not a zero. This is the
same discipline the classifier enforces one level down: absence of a measurement is reported as
absence, never rendered as the number zero.

The same rule applies to the identity fields, and it took a real bug to notice. ``str()`` on a
missing name yields the four-character string ``"None"``, which is a perfectly good institution
name as far as any renderer is concerned, and two records with no id both collapsed onto the key
``"None"`` so one silently shadowed the other and a finding was served its neighbour's peer
evidence. Identity is therefore ``str | None`` for exactly the same reason ``score`` is
``float | None``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from .disclosure import Disclosure, classify
from .fields import FIELDS, Field

__all__ = ["FieldResult", "GroupSummary", "InstitutionGrade", "grade_institution", "summarize"]

# Letter bands. Chosen so that a B means "a reader can mostly use this record" rather than
# "above average", because grading on a curve would hide a field-wide collapse in reporting.
_BANDS: Final[tuple[tuple[float, str], ...]] = (
    (0.95, "A"),
    (0.85, "B"),
    (0.70, "C"),
    (0.50, "D"),
)


@dataclass(frozen=True, slots=True)
class FieldResult:
    """What happened to one field for one institution."""

    field: Field
    raw: object
    disclosure: Disclosure

    @property
    def in_denominator(self) -> bool:
        """Whether this field is something the institution could fairly be graded on."""
        return self.disclosure not in (Disclosure.SUPPRESSED, Disclosure.NOT_APPLICABLE)


@dataclass(frozen=True, slots=True)
class InstitutionGrade:
    """One institution's disclosure result.

    ``score`` is ``None`` when every graded field was suppressed or not applicable. Callers must
    render that as "not gradeable" and never as zero.

    ``unit_id``, ``name`` and ``state`` are ``None`` when the source did not identify the
    institution, never the string ``"None"`` and never ``""``. A renderer that prints them
    unconditionally will show the word "None" as a name, which is the same error as printing a
    missing rate as zero, one level up.
    """

    unit_id: str | None
    name: str | None
    state: str | None
    results: tuple[FieldResult, ...]
    score: float | None
    letter: str | None

    @property
    def failures(self) -> tuple[FieldResult, ...]:
        """Fields the institution is answerable for: missing outright, or disclosed implausibly."""
        return tuple(r for r in self.results if r.disclosure.counts_against_publisher)

    @property
    def implausible(self) -> tuple[FieldResult, ...]:
        """Fields where a value was published but is not credible.

        Surfaced separately from plain gaps because it is the more serious finding. A gap is
        visible to a reader; a wrong number is not.
        """
        return tuple(r for r in self.results if r.disclosure is Disclosure.IMPLAUSIBLE)


@dataclass(frozen=True, slots=True)
class GroupSummary:
    """A rollup across institutions, e.g. by state or by ownership."""

    label: str
    graded: int
    ungradeable: int
    mean_score: float | None
    worst_fields: tuple[tuple[str, int], ...]
    """Field labels with the count of institutions that failed them, worst first."""


def _letter(score: float) -> str:
    for threshold, letter in _BANDS:
        if score >= threshold:
            return letter
    return "F"


def _identity(record: dict[str, object], key: str) -> str | None:
    """Read an identity field, distinguishing an absent label from a label that says nothing.

    Deliberately not ``str(record.get(key, ""))``. That renders a null name as ``"None"`` and a
    null id as the key ``"None"``, which two unidentified records then share. Whitespace-only
    values are absences too: a source that sends ``" "`` has told us no more than one that sent
    nothing, and IPEDS sends exactly that for unpopulated text columns.
    """
    value = record.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def grade_institution(record: dict[str, object]) -> InstitutionGrade:
    """Grade a single institution record as returned by a source adapter.

    Args:
        record: Flat mapping of source field key to raw value. Missing keys are treated exactly
            like present-but-null, because a source that omits a key is disclosing no more than a
            source that nulls it.
    """
    results: list[FieldResult] = []
    for field in FIELDS:
        raw = record.get(field.key)
        disclosure = classify(
            raw,
            credible_min=field.credible_min,
            credible_max=field.credible_max,
            zero_is_credible=field.zero_is_credible,
        )
        results.append(FieldResult(field=field, raw=raw, disclosure=disclosure))

    weighted_total = sum(r.field.weight for r in results if r.in_denominator)
    if weighted_total == 0.0:
        # Everything was suppressed or inapplicable. There is no grade to give, and inventing a
        # zero here would be the exact error this project exists to catch.
        return InstitutionGrade(
            unit_id=_identity(record, "id"),
            name=_identity(record, "school.name"),
            state=_identity(record, "school.state"),
            results=tuple(results),
            score=None,
            letter=None,
        )

    earned = sum(r.field.weight for r in results if r.in_denominator and r.disclosure.is_usable)
    score = earned / weighted_total
    return InstitutionGrade(
        unit_id=_identity(record, "id"),
        name=_identity(record, "school.name"),
        state=_identity(record, "school.state"),
        results=tuple(results),
        score=score,
        letter=_letter(score),
    )


def summarize(grades: list[InstitutionGrade], *, label: str) -> GroupSummary:
    """Roll a set of institution grades into one group line.

    ``ungradeable`` is reported alongside the mean rather than folded into it, so that a group
    where most records could not be graded cannot masquerade as a group that scored well.
    """
    scores = [g.score for g in grades if g.score is not None]
    failures: dict[str, int] = {}
    for grade in grades:
        for result in grade.failures:
            failures[result.field.label] = failures.get(result.field.label, 0) + 1

    ranked = sorted(failures.items(), key=lambda kv: (-kv[1], kv[0]))
    # Filtered into a list of plain floats rather than summed with ``g.score or 0.0``. That idiom
    # was correct here only because the list was already filtered, and it is the precise habit
    # this project exists to argue against: it silently reads an absent score as zero.
    mean = sum(scores) / len(scores) if scores else None
    return GroupSummary(
        label=label,
        graded=len(scores),
        ungradeable=len(grades) - len(scores),
        mean_score=mean,
        worst_fields=tuple(ranked[:5]),
    )
