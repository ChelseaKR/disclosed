"""Which federal passage defines which graded field, and how exactly.

The mapping is written down rather than searched for, because "the passage that mentions
admission" is not the same thing as "the passage that defines the variable this project reads".
Each entry says which it is:

* ``defines`` -- the passage documents the exact variable behind the field. The College
  Scorecard data dictionary row for ``ADM_RATE`` defines the admission rate field.
* ``related`` -- the passage defines the concept as the publisher presents it to the public,
  through a different variable. The Scorecard glossary's "Graduation Rate" is the website's
  eight-year measure (``C150_L4_PELL_POOLED_SUPP`` and kin); this project grades ``C150_4``, the
  150%-of-normal-time rate. Both are real federal definitions and they are not the same
  measure, so a narration that quotes the glossary for the project's field has to say so.

A field with no ``defines`` passage cannot be explained by the question-answering layer, and the
test suite holds that every graded field has one. Applicability flags are mapped too, because
"not applicable" is only an honest answer when the reader can see the federal rule it came from.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from ..fields import ALL_FIELDS

__all__ = ["APPLICABILITY_FLAGS", "FIELD_DEFINITIONS", "Definition", "definitions_for"]


@dataclass(frozen=True, slots=True)
class Definition:
    passage_id: str
    role: str
    """``defines`` or ``related``; see the module docstring."""

    note: str = ""
    """Stated beside a ``related`` quote, so the reader is never shown a definition of a measure
    the project does not grade as though it were the one it does."""


_GLOSSARY_GRADUATION_NOTE: Final[str] = (
    "The Scorecard glossary's graduation rate is the website's eight-year outcome measure. This "
    "project grades the 150%-of-normal-time completion rate (C150_4), defined in the data "
    "dictionary; the two are different measures of completion."
)
_GLOSSARY_EARNINGS_NOTE: Final[str] = (
    "The Scorecard glossary's median earnings entry describes the measure the website presents "
    "(MD_EARN_WNE_4YR, four years after completion). This project grades median earnings ten "
    "years after entry (MD_EARN_WNE_P10), defined in the data dictionary."
)
_GLOSSARY_ACCEPTANCE_NOTE: Final[str] = (
    "The glossary entry names ADM_RATE_SUPP, the website's suppressed-and-supplemented variant; "
    "this project reads ADM_RATE. The definition of the rate is the same; the glossary also "
    "states that open-admissions institutions do not report one."
)
_ATHURL_NOTE: Final[str] = (
    "IPEDS titles this variable as the Student-Right-to-Know student athlete graduation rate "
    "address. This project labels the field 'Equity in athletics disclosure' and grades it "
    "against 20 U.S.C. 1092(g); the federal title is quoted here as published, and the "
    "difference between the two descriptions is stated rather than resolved."
)

FIELD_DEFINITIONS: Final[dict[str, tuple[Definition, ...]]] = {
    "latest.earnings.10_yrs_after_entry.median": (
        Definition("scorecard-data-dictionary:MD_EARN_WNE_P10", "defines"),
        Definition("scorecard-glossary:median-earnings", "related", _GLOSSARY_EARNINGS_NOTE),
    ),
    "latest.completion.completion_rate_4yr_150nt": (
        Definition("scorecard-data-dictionary:C150_4", "defines"),
        Definition("scorecard-glossary:graduation-rate", "related", _GLOSSARY_GRADUATION_NOTE),
    ),
    "latest.admissions.admission_rate.overall": (
        Definition("scorecard-data-dictionary:ADM_RATE", "defines"),
        Definition("scorecard-glossary:acceptance-rate", "related", _GLOSSARY_ACCEPTANCE_NOTE),
    ),
    "latest.aid.median_debt.completers.overall": (
        Definition("scorecard-data-dictionary:GRAD_DEBT_MDN", "defines"),
        Definition(
            "scorecard-glossary:median-total-debt-after-graduation-for-loans-taken-out-at-this-school",
            "related",
            "The glossary entry describes the website's presentation of completer debt at this "
            "school; the data dictionary row is the variable this project reads.",
        ),
    ),
    "latest.cost.tuition.in_state": (
        Definition("scorecard-data-dictionary:TUITIONFEE_IN", "defines"),
    ),
    "latest.student.size": (
        Definition("scorecard-data-dictionary:UGDS", "defines"),
        Definition("scorecard-glossary:total-undergraduate-students-enrolled", "related"),
    ),
    "ipeds.NPRICURL": (Definition("ipeds-hd2023-dictionary:NPRICURL", "defines"),),
    "ipeds.ATHURL": (Definition("ipeds-hd2023-dictionary:ATHURL", "defines", _ATHURL_NOTE),),
    "ipeds.FAIDURL": (Definition("ipeds-hd2023-dictionary:FAIDURL", "defines"),),
    "ipeds.DISAURL": (Definition("ipeds-hd2023-dictionary:DISAURL", "defines"),),
    "ipeds.ADMINURL": (Definition("ipeds-hd2023-dictionary:ADMINURL", "defines"),),
    "ipeds.WEBADDR": (Definition("ipeds-hd2023-dictionary:WEBADDR", "defines"),),
}

# The IPEDS flags the applicability rules in :mod:`disclosed.fields` read. When a field is
# ``not_applicable`` for an institution, these are the federal definitions of the facts that
# made it so.
APPLICABILITY_FLAGS: Final[dict[str, Definition]] = {
    "ipeds.INSTCAT": Definition("ipeds-hd2023-dictionary:INSTCAT", "defines"),
    "ipeds.CYACTIVE": Definition("ipeds-hd2023-dictionary:CYACTIVE", "defines"),
    "ipeds.UGOFFER": Definition("ipeds-hd2023-dictionary:UGOFFER", "defines"),
    "ipeds.PSET4FLG": Definition("ipeds-hd2023-dictionary:PSET4FLG", "defines"),
    "ipeds.ATHASSOC": Definition("ipeds-ic2023-dictionary:ATHASSOC", "defines"),
}

_GRADED_KEYS: Final[frozenset[str]] = frozenset(f.key for f in ALL_FIELDS)


def definitions_for(field_key: str) -> tuple[Definition, ...]:
    """The passages that define a graded field, ``defines`` first. Empty for an ungraded key."""
    if field_key not in _GRADED_KEYS:
        return ()
    return FIELD_DEFINITIONS.get(field_key, ())
