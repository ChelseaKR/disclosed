"""How a value is absent, which is not the same as what the value is.

This module exists because of a mistake that is easy to make and expensive to publish. A field that
an institution did not report, a field suppressed to protect small-cohort privacy, and a field whose
true value is zero are three different facts. Rendered naively they all become ``0`` on a page, and
a reader cannot tell a college that admits nobody from a college that declined to say.

Everything downstream goes through :func:`classify`. Nothing else is permitted to look at a raw
value and decide whether it counts, so there is exactly one place where the distinction can be got
wrong, and exactly one place to fix it.

The reverse error is just as real and shows up in live federal data. In a 600-institution sample of
the College Scorecard, one institution publishes an admission rate of exactly ``0`` and two publish
a four-year completion rate of exactly ``0``. Those are not schools that admitted nobody and
graduated nobody; they are reporting artifacts that survived because zero is a legal number. So a
value being present is not sufficient. It also has to be credible for the field it sits in, which is
why :class:`Disclosure` has an ``IMPLAUSIBLE`` member and why :mod:`disclosed.fields` carries a
credible range for every field it knows about.
"""

from __future__ import annotations

import math
from enum import Enum
from typing import Final

__all__ = ["SUPPRESSION_MARKERS", "Disclosure", "classify"]

# Values that mean "we know this and are withholding it", as opposed to "we never had it".
# College Scorecard emits the literal string ``PrivacySuppressed`` in some vintages; IPEDS uses
# its own imputation codes. Matching is case-insensitive and substring-based because the exact
# spelling has changed across releases.
SUPPRESSION_MARKERS: Final[frozenset[str]] = frozenset(
    {"privacysuppressed", "suppressed", "redacted", "withheld"}
)

# Markers meaning the question does not apply to this institution, e.g. an admission rate for an
# open-enrollment school. Distinct from suppression: nothing is being withheld.
_NOT_APPLICABLE_MARKERS: Final[frozenset[str]] = frozenset({"na", "n/a", "notapplicable", "none"})


class Disclosure(Enum):
    """Why a value is or is not usable. Ordered from most to least informative."""

    REPORTED = "reported"
    """A credible value the publisher actually disclosed."""

    IMPLAUSIBLE = "implausible"
    """A value was disclosed but falls outside the credible range for its field.

    Graded as a disclosure failure rather than dropped. A wrong number published as fact is worse
    than a gap, because a gap is visible to the reader and a wrong number is not.
    """

    SUPPRESSED = "suppressed"
    """Withheld deliberately, usually to protect a small cohort. The publisher is not at fault."""

    NOT_APPLICABLE = "not_applicable"
    """The field does not apply to this institution. Excluded from the denominator entirely."""

    MISSING = "missing"
    """No value and no stated reason. This is the one that counts against a publisher."""

    @property
    def is_usable(self) -> bool:
        """Whether a consumer may read this as a real measurement."""
        return self is Disclosure.REPORTED

    @property
    def counts_against_publisher(self) -> bool:
        """Whether this outcome is the publisher's responsibility.

        Suppression is a policy decision made for good reasons and is not held against anyone.
        ``NOT_APPLICABLE`` leaves the denominator. Missing and implausible both count.
        """
        return self in (Disclosure.MISSING, Disclosure.IMPLAUSIBLE)


def _normalize(text: str) -> str:
    return "".join(ch for ch in text.casefold() if ch.isalnum() or ch == "/")


def _as_number(value: object) -> float | Disclosure:
    """Reduce a raw value to a finite float, or to the :class:`Disclosure` that ends the question.

    Split out from :func:`classify` so that the parsing rules and the credibility rules stay
    separately readable. Everything here decides *whether there is a measurement at all*;
    :func:`classify` decides whether the measurement is believable.
    """
    if value is None:
        return Disclosure.MISSING

    if isinstance(value, str):
        token = _normalize(value)
        if not token:
            return Disclosure.MISSING
        if any(marker in token for marker in SUPPRESSION_MARKERS):
            return Disclosure.SUPPRESSED
        if token in _NOT_APPLICABLE_MARKERS:
            return Disclosure.NOT_APPLICABLE
        try:
            value = float(value)
        except ValueError:
            # A non-numeric string in a numeric field tells us nothing we can use.
            return Disclosure.MISSING

    if isinstance(value, bool):
        # bool subclasses int and would otherwise sail through the numeric path as 0 or 1.
        return Disclosure.MISSING

    if not isinstance(value, (int, float)):
        return Disclosure.MISSING

    number = float(value)
    if math.isnan(number) or math.isinf(number):
        return Disclosure.MISSING
    return number


def classify(
    value: object,
    *,
    credible_min: float | None = None,
    credible_max: float | None = None,
    zero_is_credible: bool = True,
) -> Disclosure:
    """Decide how to treat one raw value from a publisher.

    Args:
        value: The value exactly as the source returned it. Do not coerce it first.
        credible_min: Lower bound of the credible range, inclusive. ``None`` disables the check.
        credible_max: Upper bound of the credible range, inclusive. ``None`` disables the check.
        zero_is_credible: Whether an exact zero is a believable measurement for this field. False
            for rates and prices where zero is nearly always a reporting artifact. This is kept
            separate from ``credible_min`` because a rate legitimately has a lower bound of zero;
            it is the exact value that is suspect, not the boundary.

    Returns:
        The :class:`Disclosure` that applies. Never raises for unexpected input; anything
        uninterpretable is ``MISSING``, because guessing is how the null-versus-zero bug gets in.
    """
    parsed = _as_number(value)
    if isinstance(parsed, Disclosure):
        return parsed

    number = parsed
    if number == 0.0 and not zero_is_credible:
        return Disclosure.IMPLAUSIBLE
    if credible_min is not None and number < credible_min:
        return Disclosure.IMPLAUSIBLE
    if credible_max is not None and number > credible_max:
        return Disclosure.IMPLAUSIBLE

    return Disclosure.REPORTED
