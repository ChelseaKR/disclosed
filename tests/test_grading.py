"""Grading and drift, including the cases where a grade must be withheld."""

from __future__ import annotations

import pytest

from disclosed.drift import Snapshot, compare, snapshot
from disclosed.fields import FIELDS
from disclosed.grading import grade_institution, summarize

_COMPLETE: dict[str, object] = {
    "id": 100654,
    "school.name": "Example University",
    "school.state": "CA",
    "latest.earnings.10_yrs_after_entry.median": 52_000,
    "latest.completion.completion_rate_4yr_150nt": 0.64,
    "latest.admissions.admission_rate.overall": 0.31,
    "latest.aid.median_debt.completers.overall": 22_300,
    "latest.cost.tuition.in_state": 11_400,
    "latest.student.size": 11_635,
}


def _with(**overrides: object) -> dict[str, object]:
    record = dict(_COMPLETE)
    record.update(overrides)
    return record


class TestInstitutionGrade:
    def test_complete_record_scores_perfectly(self) -> None:
        grade = grade_institution(_COMPLETE)
        assert grade.score == 1.0
        assert grade.letter == "A"
        assert grade.failures == ()

    def test_missing_field_lowers_the_score(self) -> None:
        grade = grade_institution(_with(**{"latest.cost.tuition.in_state": None}))
        assert grade.score is not None
        assert grade.score < 1.0
        assert [r.field.label for r in grade.failures] == ["In-state tuition"]

    def test_absent_key_is_treated_like_an_explicit_null(self) -> None:
        record = dict(_COMPLETE)
        del record["latest.cost.tuition.in_state"]
        assert (
            grade_institution(record).score
            == grade_institution(_with(**{"latest.cost.tuition.in_state": None})).score
        )

    def test_suppression_leaves_the_denominator_rather_than_scoring_zero(self) -> None:
        """A suppressed field must not drag the grade down; it is removed from the question."""
        suppressed = grade_institution(
            _with(**{"latest.earnings.10_yrs_after_entry.median": "PrivacySuppressed"})
        )
        assert suppressed.score == 1.0
        assert suppressed.failures == ()

    def test_implausible_zero_is_a_failure_not_a_value(self) -> None:
        grade = grade_institution(_with(**{"latest.admissions.admission_rate.overall": 0}))
        assert [r.field.label for r in grade.implausible] == ["Admission rate"]
        assert grade.score is not None
        assert grade.score < 1.0

    def test_credible_zero_is_not_a_failure(self) -> None:
        """Median debt of zero is real. It must not be swept up with the artifacts."""
        grade = grade_institution(_with(**{"latest.aid.median_debt.completers.overall": 0}))
        assert grade.score == 1.0
        assert grade.implausible == ()

    def test_fully_suppressed_record_has_no_grade_and_not_a_zero(self) -> None:
        """The project's own discipline applied to itself: absence is reported as absence."""
        record: dict[str, object] = {"id": 1, "school.name": "Tiny", "school.state": "VT"}
        for field in FIELDS:
            record[field.key] = "PrivacySuppressed"
        grade = grade_institution(record)
        assert grade.score is None
        assert grade.letter is None


class TestIdentityIsNotRenderedAsAValue:
    """An absent name must not become the string "None", for the same reason an absent rate must
    not become 0. Both are absences wearing the costume of a measurement."""

    def test_missing_identity_is_none_not_the_word_none(self) -> None:
        grade = grade_institution(_with(**{"id": None, "school.name": None, "school.state": None}))
        assert grade.name is None
        assert grade.unit_id is None
        assert grade.state is None

    def test_absent_identity_keys_behave_like_explicit_nulls(self) -> None:
        record = dict(_COMPLETE)
        for key in ("id", "school.name", "school.state"):
            del record[key]
        grade = grade_institution(record)
        assert (grade.unit_id, grade.name, grade.state) == (None, None, None)

    def test_whitespace_only_identity_is_an_absence(self) -> None:
        """IPEDS sends a single space for unpopulated text columns; that is not a name."""
        grade = grade_institution(_with(**{"school.name": "  ", "school.state": ""}))
        assert grade.name is None
        assert grade.state is None

    def test_present_identity_survives_unchanged(self) -> None:
        grade = grade_institution(_COMPLETE)
        assert grade.unit_id == "100654"
        assert grade.name == "Example University"

    def test_two_unidentified_records_do_not_share_a_key(self) -> None:
        """They both used to stringify to "None", so one shadowed the other in every id lookup."""
        first = grade_institution(_with(**{"id": None, "school.name": None}))
        second = grade_institution(_with(**{"id": None, "school.name": None}))
        assert first.unit_id is None and second.unit_id is None


class TestSummary:
    def test_ungradeable_are_counted_separately_from_the_mean(self) -> None:
        blank: dict[str, object] = {"id": 2, "school.name": "Blank", "school.state": "CA"}
        for field in FIELDS:
            blank[field.key] = "PrivacySuppressed"
        rows = [grade_institution(_COMPLETE), grade_institution(blank)]
        result = summarize(rows, label="CA")
        assert result.graded == 1
        assert result.ungradeable == 1
        assert result.mean_score == 1.0

    def test_worst_fields_are_ranked_by_failure_count(self) -> None:
        rows = [
            grade_institution(_with(**{"latest.cost.tuition.in_state": None})),
            grade_institution(_with(**{"latest.cost.tuition.in_state": None})),
            grade_institution(_with(**{"latest.student.size": None})),
        ]
        result = summarize(rows, label="all")
        assert result.worst_fields[0] == ("In-state tuition", 2)

    def test_empty_group_has_no_mean(self) -> None:
        assert summarize([], label="none").mean_score is None


class TestDrift:
    def test_snapshot_counts_reported_and_missing_per_field(self) -> None:
        rows = [
            grade_institution(_COMPLETE),
            grade_institution(_with(**{"latest.student.size": None})),
        ]
        snap = snapshot(rows, taken="2026-08-05")
        assert snap.institutions == 2
        assert snap.reported["Enrollment"] == 1
        assert snap.missing["Enrollment"] == 1

    def test_the_snapshot_records_the_denominator_every_rate_divides_by(self) -> None:
        """Suppressed and inapplicable institutions leave it, exactly as they leave the grade."""
        rows = [
            grade_institution(_COMPLETE),
            grade_institution(_with(**{"latest.student.size": "PrivacySuppressed"})),
        ]
        snap = snapshot(rows, taken="2026-08-05")
        assert snap.applicable["Enrollment"] == 1
        assert snap.rate("Enrollment") == 1.0

    def test_a_field_that_reached_nobody_has_no_rate_rather_than_a_zero(self) -> None:
        snap = Snapshot("a", 10, {"Enrollment": 0}, {"Enrollment": 0}, {"Enrollment": 0})
        assert snap.rate("Enrollment") is None
        assert snap.rate("A Field Not In This Snapshot") is None

    def test_systemic_loss_is_flagged(self) -> None:
        earlier = Snapshot(
            "a", 1000, {"Admission rate": 900}, {"Admission rate": 100}, {"Admission rate": 1000}
        )
        later = Snapshot(
            "b", 1000, {"Admission rate": 400}, {"Admission rate": 600}, {"Admission rate": 1000}
        )
        (drift,) = compare(earlier, later)
        assert drift.direction == "lost"
        assert drift.delta == -500
        assert drift.rate_change == pytest.approx(-0.5)
        assert drift.is_systemic

    def test_small_change_is_not_systemic(self) -> None:
        earlier = Snapshot(
            "a", 1000, {"Enrollment": 900}, {"Enrollment": 100}, {"Enrollment": 1000}
        )
        later = Snapshot("b", 1000, {"Enrollment": 895}, {"Enrollment": 105}, {"Enrollment": 1000})
        (drift,) = compare(earlier, later)
        assert not drift.is_systemic

    def test_gains_are_reported_too(self) -> None:
        earlier = Snapshot("a", 100, {"Enrollment": 10}, {"Enrollment": 90}, {"Enrollment": 100})
        later = Snapshot("b", 100, {"Enrollment": 90}, {"Enrollment": 10}, {"Enrollment": 100})
        (drift,) = compare(earlier, later)
        assert drift.direction == "gained"

    def test_a_rate_that_did_not_move_is_unchanged_not_lost(self) -> None:
        """The applicable population and the count of reporters both moved (so the record is not
        skipped by ``compare``), but they moved in exact proportion: the share of applicable
        institutions reporting is identical before and after. ``rate_change == 0.0`` must not fall
        through to "lost" -- that is the same "absence rendered as a value" defect this module's
        own docstring argues against, just on ``direction`` instead of on a count."""
        earlier = Snapshot(
            "a",
            200,
            {"Institution web address": 50},
            {"Institution web address": 50},
            {"Institution web address": 100},
        )
        later = Snapshot(
            "b",
            400,
            {"Institution web address": 100},
            {"Institution web address": 100},
            {"Institution web address": 200},
        )
        (drift,) = compare(earlier, later)
        assert drift.was_reported == 50 and drift.now_reported == 100
        assert drift.rate_change == 0.0
        assert drift.direction == "unchanged"
        assert not drift.is_systemic

    def test_a_shrinking_population_is_not_a_change_in_disclosure(self) -> None:
        """The real IPEDS web address numbers, 2021 to 2023. 130 fewer institutions published one
        because 131 stopped existing. Measured on counts this was a systemic 2.1% collapse; the
        share reporting it actually rose."""
        earlier = Snapshot(
            "a", 6289, {"Web address": 6115}, {"Web address": 4}, {"Web address": 6119}
        )
        later = Snapshot(
            "b", 6163, {"Web address": 5985}, {"Web address": 3}, {"Web address": 5988}
        )
        (drift,) = compare(earlier, later)
        assert drift.delta == -130
        assert drift.applicability_moved == -131
        assert drift.rate_change is not None and drift.rate_change > 0
        assert drift.direction == "gained"
        assert not drift.is_systemic

    def test_an_unmeasurable_rate_is_never_systemic(self) -> None:
        """A snapshot that recorded no denominator has not demonstrated anything. Treating the
        unknown as a large movement would be the loudest way of reading an absence as a number."""
        earlier = Snapshot("a", 1000, {"Enrollment": 900}, {"Enrollment": 100})
        later = Snapshot("b", 1000, {"Enrollment": 100}, {"Enrollment": 900})
        (drift,) = compare(earlier, later)
        assert drift.rate_change is None
        assert not drift.is_systemic
        assert drift.direction == "lost"

    def test_measurable_changes_sort_ahead_of_unmeasurable_ones(self) -> None:
        earlier = Snapshot(
            "a", 100, {"Known": 90, "Unknown": 90}, {"Known": 10, "Unknown": 10}, {"Known": 100}
        )
        later = Snapshot(
            "b", 100, {"Known": 89, "Unknown": 10}, {"Known": 11, "Unknown": 90}, {"Known": 100}
        )
        assert [d.field_label for d in compare(earlier, later)] == ["Known", "Unknown"]

    def test_field_added_to_the_graded_set_is_not_reported_as_publisher_drift(self) -> None:
        """A change in this project must never look like a change at the publisher."""
        earlier = Snapshot("a", 100, {"Enrollment": 50}, {"Enrollment": 50}, {"Enrollment": 100})
        later = Snapshot("b", 100, {"Enrollment": 50, "New Field": 50}, {}, {"Enrollment": 100})
        assert compare(earlier, later) == ()

    def test_no_institutions_yields_no_drift(self) -> None:
        assert compare(Snapshot("a", 0, {}, {}, {}), Snapshot("b", 0, {}, {}, {})) == ()


class TestCrossSourceDriftIsRefused:
    """Two populations compared is not drift, and the silent version of it is the dangerous one.

    The Scorecard and IPEDS field sets do not overlap, so a comparison across them skips every
    field and prints "no change in per-field disclosure": a reassuring sentence about nothing.
    """

    def _snap(self, source: str) -> Snapshot:
        return Snapshot("a", 10, {"Enrollment": 5}, {"Enrollment": 5}, {"Enrollment": 10}, source)

    def test_two_sources_cannot_be_compared(self) -> None:
        with pytest.raises(ValueError, match="different populations"):
            compare(self._snap("College Scorecard"), self._snap("IPEDS directory"))

    def test_the_same_source_compares_normally(self) -> None:
        assert compare(self._snap("IPEDS directory"), self._snap("IPEDS directory")) == ()

    def test_an_unstated_source_is_not_treated_as_a_match_or_a_mismatch(self) -> None:
        """Snapshots predating the source field still compare, because refusing them would break
        a history that is not wrong, only older."""
        assert compare(self._snap(""), self._snap("IPEDS directory")) == ()
