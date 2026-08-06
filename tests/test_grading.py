"""Grading and drift, including the cases where a grade must be withheld."""

from __future__ import annotations

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
        assert grade_institution(record).score == grade_institution(
            _with(**{"latest.cost.tuition.in_state": None})
        ).score

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

    def test_systemic_loss_is_flagged(self) -> None:
        earlier = Snapshot("a", 1000, {"Admission rate": 900}, {"Admission rate": 100})
        later = Snapshot("b", 1000, {"Admission rate": 400}, {"Admission rate": 600})
        (drift,) = compare(earlier, later)
        assert drift.direction == "lost"
        assert drift.delta == -500
        assert drift.is_systemic

    def test_small_change_is_not_systemic(self) -> None:
        earlier = Snapshot("a", 1000, {"Enrollment": 900}, {"Enrollment": 100})
        later = Snapshot("b", 1000, {"Enrollment": 895}, {"Enrollment": 105})
        (drift,) = compare(earlier, later)
        assert not drift.is_systemic

    def test_gains_are_reported_too(self) -> None:
        earlier = Snapshot("a", 100, {"Enrollment": 10}, {"Enrollment": 90})
        later = Snapshot("b", 100, {"Enrollment": 90}, {"Enrollment": 10})
        (drift,) = compare(earlier, later)
        assert drift.direction == "gained"

    def test_field_added_to_the_graded_set_is_not_reported_as_publisher_drift(self) -> None:
        """A change in this project must never look like a change at the publisher."""
        earlier = Snapshot("a", 100, {"Enrollment": 50}, {"Enrollment": 50})
        later = Snapshot("b", 100, {"Enrollment": 50, "New Field": 50}, {})
        assert compare(earlier, later) == ()

    def test_no_institutions_yields_no_drift(self) -> None:
        assert compare(Snapshot("a", 0, {}, {}), Snapshot("b", 0, {}, {})) == ()
