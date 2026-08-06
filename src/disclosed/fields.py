"""The fields this project grades, and what makes a value credible in each one.

Every credible range here is a judgement call, so every one carries a ``rationale`` that a graded
institution can argue with. That is the whole contract: a scorecard that cannot be disputed line by
line is not a scorecard, it is an accusation. The rationales are written for the reader who thinks
their institution was marked unfairly.

``zero_is_credible`` is the field-level expression of the null-versus-zero problem. For a rate or a
price, an exact zero is almost never a measurement. For a count of students it can be.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

__all__ = ["FIELDS", "SCORECARD_API_FIELDS", "Field", "field_by_key"]


@dataclass(frozen=True, slots=True)
class Field:
    """One graded field, with the bounds used to judge whether a disclosed value is credible."""

    key: str
    """Source-side field path, used verbatim in the API request."""

    label: str
    """Plain-language name. Appears on the site and in findings."""

    credible_min: float | None
    credible_max: float | None
    zero_is_credible: bool
    rationale: str
    """Why these bounds. Written to be argued with by a graded institution."""

    weight: float = 1.0
    """Relative contribution to the institution's disclosure grade."""


# Ordered roughly by how much a prospective student loses when the field is absent.
FIELDS: Final[tuple[Field, ...]] = (
    Field(
        key="latest.earnings.10_yrs_after_entry.median",
        label="Median earnings 10 years after entry",
        credible_min=1_000.0,
        credible_max=500_000.0,
        zero_is_credible=False,
        rationale=(
            "Median earnings of zero across a whole cohort ten years out would mean no former "
            "student had any earnings at all, which no real institution produces. The floor of "
            "$1,000 is deliberately far below any plausible median so that only artifacts trip it. "
            "The ceiling accommodates specialist medical and dental programs."
        ),
        weight=1.5,
    ),
    Field(
        key="latest.completion.completion_rate_4yr_150nt",
        label="Completion rate, 150% of normal time",
        credible_min=0.0,
        credible_max=1.0,
        zero_is_credible=False,
        rationale=(
            "A rate is a proportion, so anything outside 0 to 1 is a unit error rather than a "
            "measurement. An exact zero is treated as an artifact because an institution that "
            "graduated nobody in the cohort would not still be reporting; three institutions in a "
            "600-school sample publish exactly zero here."
        ),
        weight=1.5,
    ),
    Field(
        key="latest.admissions.admission_rate.overall",
        label="Admission rate",
        credible_min=0.0,
        credible_max=1.0,
        zero_is_credible=False,
        rationale=(
            "Same proportion bounds as completion. An exact zero would mean the institution "
            "admitted no applicant at all, which is an artifact rather than a policy. Note that "
            "open-enrollment institutions legitimately have no admission rate; those are expected "
            "to arrive as an explicit not-applicable rather than as a zero, and are excluded from "
            "the denominator when they do."
        ),
    ),
    Field(
        key="latest.aid.median_debt.completers.overall",
        label="Median debt at completion",
        credible_min=0.0,
        credible_max=400_000.0,
        zero_is_credible=True,
        rationale=(
            "Zero is credible here and is not treated as an artifact: a fully funded institution "
            "can genuinely graduate students with no federal debt. The ceiling is set above "
            "aggregate federal borrowing limits for professional programs."
        ),
    ),
    Field(
        key="latest.cost.tuition.in_state",
        label="In-state tuition",
        credible_min=0.0,
        credible_max=150_000.0,
        zero_is_credible=False,
        rationale=(
            "A published tuition of exactly zero is nearly always an unpopulated field rather than "
            "a free institution. The handful of genuinely tuition-free colleges are a known, small "
            "set and are better served by an explicit exemption than by a silent zero."
        ),
    ),
    Field(
        key="latest.student.size",
        label="Enrollment",
        credible_min=1.0,
        credible_max=250_000.0,
        zero_is_credible=False,
        rationale=(
            "An institution reporting zero students is either closed or has not populated the "
            "field. Either way the rest of its record should not be read as current."
        ),
    ),
)

# Identity fields fetched alongside the graded ones. Never graded; used to label and group.
IDENTITY_FIELDS: Final[tuple[str, ...]] = (
    "id",
    "school.name",
    "school.state",
    "school.ownership",
    "school.degrees_awarded.predominant",
)

SCORECARD_API_FIELDS: Final[tuple[str, ...]] = IDENTITY_FIELDS + tuple(f.key for f in FIELDS)

_BY_KEY: Final[dict[str, Field]] = {f.key: f for f in FIELDS}


def field_by_key(key: str) -> Field:
    """Look up a graded field by its source-side key.

    Raises:
        KeyError: If the key is not graded. Callers should not invent fields; adding one is a
            deliberate act that requires writing a rationale.
    """
    return _BY_KEY[key]
