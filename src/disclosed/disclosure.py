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
from collections.abc import Mapping
from enum import Enum
from typing import Final

__all__ = ["CLASSIFICATIONS", "SUPPRESSION_MARKERS", "Disclosure", "classify"]

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


# Every word this project will ever write into the ``fields`` mapping of a report, as a set the
# readers of that mapping can check against. A reader is entitled to meet a word it does not
# know -- a report written by a newer version than the code reading it -- and what it must not do
# is guess. :mod:`disclosed.site` already says "unrecognized classification" on the page rather
# than rendering the row as a gap; the aggregators use this for the same reason, one level up.
CLASSIFICATIONS: Final[frozenset[str]] = frozenset(d.value for d in Disclosure)


def _normalize(text: str) -> str:
    return "".join(ch for ch in text.casefold() if ch.isalnum() or ch == "/")


def _sentinel_token(value: object) -> str | None:
    """Canonical string form of a value, for matching a source's own missing-data codes.

    Matching happens here, on the raw value, and deliberately not on the output of
    :func:`_normalize`. Normalization strips the minus sign, so IPEDS's ``-2`` ("not applicable")
    would normalize to ``"2"`` and collide with a real measurement of two. An integral float is
    reduced to its integer form because a CSV round trip turns ``-2`` into ``-2.0``.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return str(int(value))
    return None


def _classify_text(value: object) -> Disclosure:
    """Classify a field whose value is text, such as a published URL.

    Split from the numeric path because the numeric path treats any non-numeric string as an
    absence, which is right for a tuition column and catastrophic for a URL column: every
    institution in IPEDS would have been graded as failing to publish a net price calculator
    because ``https://...`` does not parse as a float.

    Suppression markers are matched on the whole token here rather than as substrings. Substring
    matching exists for numeric columns that carry a literal ``PrivacySuppressed`` in place of a
    number; a free-text column can legitimately contain the word, and a college with a page at
    ``/withheld-records`` has not suppressed anything.
    """
    if not isinstance(value, str):
        return Disclosure.MISSING
    token = _normalize(value)
    if not token:
        return Disclosure.MISSING
    if token in SUPPRESSION_MARKERS:
        return Disclosure.SUPPRESSED
    if token in _NOT_APPLICABLE_MARKERS:
        return Disclosure.NOT_APPLICABLE
    return Disclosure.REPORTED


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
    sentinels: Mapping[str, Disclosure] | None = None,
    text_is_a_value: bool = False,
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
        sentinels: The source's own missing-data codes, mapped to what they mean. IPEDS encodes
            three different absences as negative integers (``-1`` not reported, ``-2`` not
            applicable, ``-3`` not available), and without this they would all be graded as real
            measurements of minus one, minus two and minus three. Checked before anything else,
            because a sentinel is a statement about absence and outranks any reading of the number.
        text_is_a_value: Whether a non-numeric string is the measurement rather than noise. True
            for URL and other text columns. Left False by default so that a word appearing in a
            numeric column stays an absence.

    Returns:
        The :class:`Disclosure` that applies. Never raises for unexpected input; anything
        uninterpretable is ``MISSING``, because guessing is how the null-versus-zero bug gets in.
    """
    if sentinels:
        token = _sentinel_token(value)
        if token is not None:
            stated = sentinels.get(token)
            if stated is not None:
                return stated

    if text_is_a_value:
        return _classify_text(value)

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
