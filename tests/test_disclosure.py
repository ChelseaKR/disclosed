"""The null-versus-zero boundary, tested from both sides.

These are the tests that matter most in the project. If :func:`classify` regresses, every number
downstream is wrong in a way that looks plausible on a page, which is the failure mode the whole
codebase is arranged to prevent.
"""

from __future__ import annotations

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from disclosed.disclosure import Disclosure, classify


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
        """An admission rate of exactly zero is an artifact; three real institutions publish one."""
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


@given(st.floats(allow_nan=False, allow_infinity=False, min_value=1e-6, max_value=1e6))
def test_any_positive_finite_value_in_range_is_reported(value: float) -> None:
    assert classify(value, credible_min=0.0, credible_max=1e9) is Disclosure.REPORTED


@given(st.one_of(st.none(), st.just(""), st.just("  ")))
def test_every_absence_form_is_missing(value: object) -> None:
    assert classify(value) is Disclosure.MISSING
