"""The fields this project grades, and what makes a value credible in each one.

Every credible range here is a judgement call, so every one carries a ``rationale`` that a graded
institution can argue with. That is the whole contract: a scorecard that cannot be disputed line by
line is not a scorecard, it is an accusation. The rationales are written for the reader who thinks
their institution was marked unfairly.

``zero_is_credible`` is the field-level expression of the null-versus-zero problem. For a rate or a
price, an exact zero is almost never a measurement. For a count of students it can be.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Final

from .disclosure import Disclosure, classify

__all__ = [
    "FIELDS",
    "IPEDS_FIELDS",
    "IPEDS_SENTINELS",
    "SCORECARD_API_FIELDS",
    "Field",
    "field_by_key",
    "field_by_label",
]

# IPEDS encodes three different absences as negative integers. Without this map they would be
# graded as real measurements of minus one, minus two and minus three, which is the null-versus
# -zero bug wearing a different hat. The three are not interchangeable: -1 counts against the
# institution, -2 leaves the denominator, and -3 is withheld and never held against anyone.
IPEDS_SENTINELS: Final[Mapping[str, Disclosure]] = {
    "-1": Disclosure.MISSING,
    "-2": Disclosure.NOT_APPLICABLE,
    "-3": Disclosure.SUPPRESSED,
}


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

    sentinels: Mapping[str, Disclosure] = dataclass_field(default_factory=dict)
    """The source's own missing-data codes for this field. See :data:`IPEDS_SENTINELS`."""

    text_is_a_value: bool = False
    """Whether a non-numeric string is the measurement. True for URL columns."""

    applies_when: Callable[[Mapping[str, object]], bool] | None = None
    """Whether this field applies to a given institution at all, or ``None`` for always.

    Applicability has to be decided per record, not per field, and it took IPEDS to make that
    obvious. The net price calculator is required by law of institutions enrolling first-time
    undergraduates; a graduate-only medical school is not required to publish one and a system
    office is not an institution a student applies to. Grading either as "did not report" would
    manufacture a violation out of a rule that never applied to them, which is the same error as
    grading a suppressed field as a zero. Fields excluded this way leave the denominator.
    """

    @property
    def anchor(self) -> str:
        """Stable fragment id for this field's rationale on the methodology page.

        Derived from ``key`` rather than ``label`` because the label is prose and will be reworded;
        an anchor that moves silently breaks every published link to the reasoning behind a grade,
        and that reasoning is the one thing a graded institution is invited to go and read.
        """
        return "field-" + self.key.replace("latest.", "").replace(".", "-").replace("_", "-")

    @property
    def column(self) -> str:
        """Column name for this field in the exported CSV.

        Derived from the source key rather than the label so that a reader can trace any column
        straight back to the field the publisher actually serves, and so that rewording a label
        does not silently rename a column in a dataset someone has already cited. The ``latest.``
        prefix is dropped because it is on every Scorecard field and carries no information.
        """
        return self.key.removeprefix("latest.").replace(".", "_").lower()

    def classify(self, record: Mapping[str, object]) -> Disclosure:
        """Classify this field's value for one institution.

        The single place that assembles a field's rules and hands them to the one classifier.
        Callers used to spread the four arguments at the call site, which meant every new rule had
        to be remembered separately by everyone who graded anything.
        """
        if self.applies_when is not None and not self.applies_when(record):
            return Disclosure.NOT_APPLICABLE
        return classify(
            record.get(self.key),
            credible_min=self.credible_min,
            credible_max=self.credible_max,
            zero_is_credible=self.zero_is_credible,
            sentinels=self.sentinels,
            text_is_a_value=self.text_is_a_value,
        )


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


def _code(record: Mapping[str, object], key: str) -> str:
    return str(record.get(key, "")).strip()


def _is_an_institution(record: Mapping[str, object]) -> bool:
    """Whether this row is a school a student could attend, rather than an office or a closure.

    IPEDS lists system and district headquarters alongside campuses. "University of Alabama System
    Office" is a real row with a real unit id, and it does not admit anyone, so holding it to a
    student-facing disclosure requirement would invent a violation. ``INSTCAT`` marks these -2.
    ``CYACTIVE`` other than 1 means the institution is closed or no longer reporting; it is not
    obliged to maintain public pages either.

    ``INSTCAT`` of -1 is deliberately NOT excluded. That is "not reported", and the fourteen rows
    carrying it are real colleges, so excusing them would use one absence to excuse another.
    """
    return _code(record, "ipeds.INSTCAT") != "-2" and _code(record, "ipeds.CYACTIVE") == "1"


def _owes_a_net_price_calculator(record: Mapping[str, object]) -> bool:
    """Whether the net price calculator requirement reaches this institution.

    20 U.S.C. 1015a(h)(3) requires a net price calculator of institutions that participate in
    Title IV programs and enrol first-time, full-time undergraduates. Both conditions are checked,
    because dropping either one inflates the finding with institutions the statute never touched:
    284 rows offer no undergraduate education at all (UCSF and UC Law SF among them) and several
    hundred more take no federal student aid. They are not out of compliance; the rule does not
    apply to them, so they leave the denominator instead of being marked down.
    """
    return (
        _is_an_institution(record)
        and _code(record, "ipeds.UGOFFER") == "1"
        and _code(record, "ipeds.PSET4FLG") == "1"
    )


# Public disclosures IPEDS records that the College Scorecard does not carry. These are graded on
# whether a URL was published at all, never on whether the page behind it is any good: this
# project reads what a publisher declared and does not fetch anything.
#
# Two obvious candidates are deliberately absent, and their absence is the point. ATHURL is the
# Equity in Athletics disclosure, required only of institutions with intercollegiate athletics;
# 4,469 of 6,163 rows are blank and nearly all of those institutions simply have no athletics
# program. VETURL is blank for 2,377 and there is no universal requirement behind it. Grading
# either would produce a large, confident, and completely fabricated finding, which is exactly
# the failure this project was built to avoid committing itself.
IPEDS_FIELDS: Final[tuple[Field, ...]] = (
    Field(
        key="ipeds.NPRICURL",
        label="Net price calculator",
        credible_min=None,
        credible_max=None,
        zero_is_credible=True,
        text_is_a_value=True,
        sentinels=IPEDS_SENTINELS,
        applies_when=_owes_a_net_price_calculator,
        rationale=(
            "A net price calculator is required by 20 U.S.C. 1015a(h)(3) of institutions that "
            "participate in Title IV and enrol first-time, full-time undergraduates, and IPEDS "
            "collects the address of it. What a blank here establishes is that the federal "
            "record carries no calculator, which is not the same claim as the institution "
            "having none: either the calculator does not exist, which 1015a(h)(3) requires, or "
            "it exists and was not reported to IPEDS, which 20 U.S.C. 1094(a)(17) requires. "
            "Something required is absent either way, and stating which one is not this "
            "project's to do. Both applicability conditions are applied before grading: "
            "graduate-only institutions, institutions taking no federal student aid, system and "
            "district offices, and closed institutions leave the denominator entirely rather "
            "than being marked down, because the requirement does not reach them. Dropping "
            "either condition would inflate this finding with institutions the statute never "
            "touched. Only the presence of a published address is checked; the page behind it "
            "is never fetched and is not graded."
        ),
        weight=1.5,
    ),
    Field(
        key="ipeds.FAIDURL",
        label="Financial aid information",
        credible_min=None,
        credible_max=None,
        zero_is_credible=True,
        text_is_a_value=True,
        sentinels=IPEDS_SENTINELS,
        applies_when=_is_an_institution,
        rationale=(
            "34 CFR 668.42 requires Title IV institutions to make information about financial "
            "assistance readily available to prospective students. IPEDS records the address at "
            "which they do it. An institution can satisfy the underlying rule without giving "
            "IPEDS a URL, so a gap here is evidence that the information is hard to find rather "
            "than proof that it does not exist, and it is graded as an ordinary disclosure gap."
        ),
    ),
    Field(
        key="ipeds.DISAURL",
        label="Disability services information",
        credible_min=None,
        credible_max=None,
        zero_is_credible=True,
        text_is_a_value=True,
        sentinels=IPEDS_SENTINELS,
        applies_when=_is_an_institution,
        rationale=(
            "34 CFR 668.43(a)(9) requires institutions to disclose the facilities and services "
            "available to students with disabilities. Only 101 of 6,163 rows are blank, which "
            "makes an absence here unusually informative: this is a disclosure nearly everyone "
            "manages, so the handful who do not stand out against their own sector rather than "
            "against a threshold this project chose."
        ),
    ),
    Field(
        key="ipeds.ADMINURL",
        label="Admissions information",
        credible_min=None,
        credible_max=None,
        zero_is_credible=True,
        text_is_a_value=True,
        sentinels=IPEDS_SENTINELS,
        applies_when=_is_an_institution,
        rationale=(
            "Where a prospective student goes to find out how to apply. There is no single "
            "statute behind this one and it is graded as a plain disclosure gap, not as a "
            "compliance finding. Open-enrollment institutions still admit students and still "
            "have an admissions process, so they are not excused from the denominator."
        ),
    ),
    Field(
        key="ipeds.WEBADDR",
        label="Institution web address",
        credible_min=None,
        credible_max=None,
        zero_is_credible=True,
        text_is_a_value=True,
        sentinels=IPEDS_SENTINELS,
        applies_when=_is_an_institution,
        rationale=(
            "The floor. An operating institution with no published web address at all leaves a "
            "prospective student with no way in, and 76 rows in IPEDS are blank. This is included "
            "precisely because it should be uncontroversial: a source where even this is missing "
            "is telling you something about the rest of the record."
        ),
    ),
)

ALL_FIELDS: Final[tuple[Field, ...]] = FIELDS + IPEDS_FIELDS

_BY_KEY: Final[dict[str, Field]] = {f.key: f for f in ALL_FIELDS}
_BY_LABEL: Final[dict[str, Field]] = {f.label: f for f in ALL_FIELDS}


def field_by_key(key: str) -> Field:
    """Look up a graded field by its source-side key.

    Raises:
        KeyError: If the key is not graded. Callers should not invent fields; adding one is a
            deliberate act that requires writing a rationale.
    """
    return _BY_KEY[key]


def field_by_label(label: str) -> Field | None:
    """Look up a graded field by its published label, or ``None`` if nothing matches.

    Returns ``None`` rather than raising because the caller is the site generator reading a report
    that may have been produced by an older version of this code. A field that has since been
    renamed should cost that one row its link to a rationale, not the whole build.
    """
    return _BY_LABEL.get(label)
