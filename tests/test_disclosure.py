"""The null-versus-zero boundary, tested from both sides.

These are the tests that matter most in the project. If :func:`classify` regresses, every number
downstream is wrong in a way that looks plausible on a page, which is the failure mode the whole
codebase is arranged to prevent.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Final

import pytest
from hypothesis import given
from hypothesis import strategies as st

from disclosed import disclosure
from disclosed.disclosure import Disclosure, classify
from disclosed.fields import field_by_key

_NUMBER_WORDS: Final[dict[int, str]] = {
    0: "no",
    1: "one",
    2: "two",
    3: "three",
    4: "four",
    5: "five",
}


def _capture() -> list[dict[str, Any]]:
    root = Path(__file__).resolve().parent.parent
    loaded: list[dict[str, Any]] = json.loads(
        (root / "data" / "sample.json").read_text(encoding="utf-8")
    )
    return loaded


def _exact_zeros(records: list[dict[str, Any]], field_key: str) -> int:
    """How many institutions published a literal zero for one field.

    ``bool`` is excluded because it subclasses ``int``, so a ``False`` in a numeric column would
    otherwise be counted as a published zero. That is the same trap :func:`classify` sidesteps and
    it would be embarrassing for the test that guards the claim to fall into it.
    """
    return sum(
        1
        for record in records
        if isinstance(record.get(field_key), (int, float))
        and not isinstance(record.get(field_key), bool)
        and record[field_key] == 0
    )


class TestAbsence:
    def test_none_is_missing(self) -> None:
        assert classify(None) is Disclosure.MISSING

    def test_empty_string_is_missing(self) -> None:
        assert classify("") is Disclosure.MISSING
        assert classify("   ") is Disclosure.MISSING

    @pytest.mark.parametrize(
        "marker",
        ["PrivacySuppressed", "privacysuppressed", "SUPPRESSED", "Redacted", "withheld"],
    )
    def test_suppression_markers_are_not_missing(self, marker: str) -> None:
        """Suppression is a policy decision and must not be held against the publisher."""
        assert classify(marker) is Disclosure.SUPPRESSED

    @pytest.mark.parametrize("marker", ["NA", "N/A", "n/a", "NotApplicable", "none"])
    def test_not_applicable_markers(self, marker: str) -> None:
        assert classify(marker) is Disclosure.NOT_APPLICABLE

    def test_unparseable_string_is_missing_not_implausible(self) -> None:
        """A word in a numeric field is an absence of data, not a bad measurement."""
        assert classify("see footnote") is Disclosure.MISSING


class TestZeroIsNotAbsence:
    def test_zero_is_reported_where_zero_is_credible(self) -> None:
        """Median debt of zero is a real, publishable fact about a fully funded institution."""
        assert classify(0, zero_is_credible=True) is Disclosure.REPORTED

    def test_zero_is_implausible_where_zero_is_not_credible(self) -> None:
        """An admission rate of exactly zero is an artifact; one real institution publishes one."""
        assert classify(0, zero_is_credible=False) is Disclosure.IMPLAUSIBLE

    def test_zero_is_never_missing(self) -> None:
        """The core invariant: a disclosed zero is never silently converted into an absence."""
        for zero_ok in (True, False):
            assert classify(0, zero_is_credible=zero_ok) is not Disclosure.MISSING
            assert classify(0.0, zero_is_credible=zero_ok) is not Disclosure.MISSING

    def test_missing_is_never_reported_as_a_number(self) -> None:
        """The inverse invariant: an absence never becomes usable."""
        assert not classify(None).is_usable
        assert not classify("").is_usable


class TestCredibleRange:
    def test_below_floor_is_implausible(self) -> None:
        assert classify(0.5, credible_min=1.0) is Disclosure.IMPLAUSIBLE

    def test_above_ceiling_is_implausible(self) -> None:
        assert classify(1.4, credible_max=1.0) is Disclosure.IMPLAUSIBLE

    def test_bounds_are_inclusive(self) -> None:
        assert classify(1.0, credible_min=1.0, credible_max=1.0) is Disclosure.REPORTED

    def test_numeric_string_is_parsed_then_bounded(self) -> None:
        assert classify("0.42", credible_min=0.0, credible_max=1.0) is Disclosure.REPORTED
        assert classify("1.42", credible_min=0.0, credible_max=1.0) is Disclosure.IMPLAUSIBLE


class TestHostileInput:
    def test_bool_is_missing_not_zero_or_one(self) -> None:
        """bool subclasses int and would otherwise be graded as a real 0 or 1."""
        assert classify(True) is Disclosure.MISSING
        assert classify(False) is Disclosure.MISSING

    @pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
    def test_non_finite_is_missing(self, value: float) -> None:
        assert classify(value) is Disclosure.MISSING

    @pytest.mark.parametrize("value", [[], {}, object(), (1, 2)])
    def test_unexpected_types_are_missing_and_do_not_raise(self, value: object) -> None:
        assert classify(value) is Disclosure.MISSING


class TestAccountability:
    def test_suppressed_does_not_count_against_publisher(self) -> None:
        assert not Disclosure.SUPPRESSED.counts_against_publisher
        assert not Disclosure.NOT_APPLICABLE.counts_against_publisher

    def test_missing_and_implausible_count(self) -> None:
        assert Disclosure.MISSING.counts_against_publisher
        assert Disclosure.IMPLAUSIBLE.counts_against_publisher

    def test_only_reported_is_usable(self) -> None:
        usable = [d for d in Disclosure if d.is_usable]
        assert usable == [Disclosure.REPORTED]


class TestTheFiguresTheProseStates:
    """Every count this codebase asserts about the committed capture, counted from the capture.

    A rationale is not a comment. It is rendered verbatim onto the methodology page and onto every
    finding that links to it, and it is the one thing a graded institution is invited to go and
    read, so a number inside one is a published claim. Until this existed those numbers were the
    only figures in the project with nothing checking them, and one of them was wrong: the
    completion-rate rationale said *three* institutions publish exactly zero, the capture holds
    two, and the site printed three on the methodology page and on both institution pages that
    cite it. The classifier's own module docstring had it right the whole time, which is the worst
    version of this: two files stating the same fact differently, and the one on the page losing.
    """

    def test_the_capture_is_still_the_600_the_prose_calls_it(self) -> None:
        assert len(_capture()) == 600

    def test_the_completion_rationale_states_the_number_of_zeros_the_capture_holds(self) -> None:
        records = _capture()
        key = "latest.completion.completion_rate_4yr_150nt"
        zeros = _exact_zeros(records, key)
        assert zeros == 2

        rationale = field_by_key(key).rationale
        stated = _NUMBER_WORDS[zeros]
        assert f"{stated} institutions in a 600-school sample publish exactly zero" in rationale
        # A reword must not be able to reintroduce a different count alongside the right one.
        others = [
            word
            for number, word in _NUMBER_WORDS.items()
            if number != zeros and f"{word} institutions in a 600-school sample" in rationale
        ]
        assert not others, f"the rationale also claims: {others}"

    def test_the_classifier_docstring_counts_the_same_zeros(self) -> None:
        """The two statements of this fact are checked against one source, not against each other.

        Asserting only that the two files agree would let both drift together onto a wrong number,
        which is the failure this project spends its whole time describing in other people's data.
        """
        records = _capture()
        admission = _exact_zeros(records, "latest.admissions.admission_rate.overall")
        completion = _exact_zeros(records, "latest.completion.completion_rate_4yr_150nt")
        assert (admission, completion) == (1, 2)

        # Whitespace-normalized: the claim is wrapped across two source lines and a literal match
        # would fail for the formatting rather than for the fact.
        text = " ".join((disclosure.__doc__ or "").split())
        assert (
            f"{_NUMBER_WORDS[admission]} institution publishes an admission rate of exactly ``0``"
            in text
        )
        assert (
            f"{_NUMBER_WORDS[completion]} publish a four-year completion rate of exactly ``0``"
            in text
        )


@given(st.floats(allow_nan=False, allow_infinity=False, min_value=1e-6, max_value=1e6))
def test_any_positive_finite_value_in_range_is_reported(value: float) -> None:
    assert classify(value, credible_min=0.0, credible_max=1e9) is Disclosure.REPORTED


@given(st.one_of(st.none(), st.just(""), st.just("  ")))
def test_every_absence_form_is_missing(value: object) -> None:
    assert classify(value) is Disclosure.MISSING
