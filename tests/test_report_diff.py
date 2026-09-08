"""The institution-level diff: which institutions moved, and whether we moved the rules.

``drift`` measures the population. This measures the institution, and the two failure modes it
has to survive are the ones the rest of this repository is built around. A transition attributed
to a publisher when the grader changed underneath it is a wrong finding with a name attached, and
an institution that is missing, unidentified, or graded under a word this build does not know is
an absence that must not print as "unchanged".
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from disclosed import cli, grading
from disclosed.disclosure import CLASSIFICATIONS
from disclosed.report_diff import compare_reports, read_rules_version

_FIELD = "Admission rate"
_OTHER = "In-state tuition"


def _grade(
    unit_id: str | None,
    *,
    name: str | None = "Somewhere College",
    state: str | None = "CA",
    score: float | None = 0.9,
    letter: str | None = "B",
    fields: dict[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "unit_id": unit_id,
        "name": name,
        "state": state,
        "score": score,
        "letter": letter,
        "fields": fields if fields is not None else {_FIELD: "reported", _OTHER: "reported"},
    }


def _report(
    grades: list[dict[str, Any]],
    *,
    source: str = "College Scorecard",
    rules_version: str | None = "2026-09-06.1",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "scope": {
            "kind": "sample",
            "source": source,
            "institutions": len(grades),
            "states": 1,
            "universe": 6_300,
            "coverage": None,
            "note": "a fixture",
        },
        "institutions": len(grades),
        "grades": grades,
    }
    if rules_version is not None:
        payload["rules_version"] = rules_version
    return payload


class TestTheTransitionIsTheUnit:
    def test_one_field_moving_yields_exactly_that_transition(self) -> None:
        earlier = _report([_grade("105525")])
        later = _report([_grade("105525", fields={_FIELD: "missing", _OTHER: "reported"})])

        diff = compare_reports(earlier, later)

        (change,) = diff.changed
        (transition,) = change.transitions
        assert transition.field_label == _FIELD
        assert transition.label == "reported -> missing"
        assert diff.matrix == (("reported -> missing", 1),)
        assert diff.transition_count == 1

    def test_a_report_against_itself_has_no_transitions(self) -> None:
        report = _report([_grade("105525"), _grade("125310", name="Elsewhere College")])

        diff = compare_reports(report, report)

        assert diff.changed == ()
        assert diff.matrix == ()
        assert diff.compared == 2

    def test_a_value_that_moved_without_changing_state_is_not_a_transition(self) -> None:
        # The product's unit is the state flip. An institution that published 31% and now
        # publishes 44% has disclosed exactly as much as before, and this project does not
        # grade anyone on the number.
        earlier = _report([_grade("105525", score=0.9, letter="B")])
        later = _report([_grade("105525", score=0.9, letter="B")])

        assert compare_reports(earlier, later).changed == ()

    def test_every_one_of_the_five_states_can_be_named_on_both_sides(self) -> None:
        words = sorted(CLASSIFICATIONS)
        earlier = _report(
            [_grade(str(i), fields={_FIELD: word}) for i, word in enumerate(words)],
        )
        later = _report(
            [
                _grade(str(i), fields={_FIELD: words[(i + 1) % len(words)]})
                for i, _ in enumerate(words)
            ],
        )

        diff = compare_reports(earlier, later)

        assert len(diff.changed) == len(words)
        assert diff.transition_count == len(words)


class TestTheTwoRefusals:
    def test_two_sources_are_refused_in_drifts_own_wording(self) -> None:
        earlier = _report([_grade("105525")], source="College Scorecard")
        later = _report([_grade("105525")], source="IPEDS directory")

        with pytest.raises(ValueError, match="different populations"):
            compare_reports(earlier, later)

    def test_two_rules_versions_are_refused_and_both_are_named(self) -> None:
        earlier = _report([_grade("105525")], rules_version="2026-01-01.1")
        later = _report([_grade("105525")], rules_version="2026-09-06.1")

        with pytest.raises(ValueError) as caught:
            compare_reports(earlier, later)

        message = str(caught.value)
        assert "2026-01-01.1" in message
        assert "2026-09-06.1" in message

    def test_an_unstated_rules_version_is_not_treated_as_agreement(self) -> None:
        # Silence is not a match. The comparison still runs -- reports predating the version
        # stamp are readable -- but it must not print under a heading that claims the rules held.
        earlier = _report([_grade("105525")], rules_version=None)
        later = _report([_grade("105525")])

        diff = compare_reports(earlier, later)

        assert diff.earlier_rules_version == ""
        assert diff.rules_confirmed is False

    def test_an_unstated_version_on_both_sides_is_still_not_agreement(self) -> None:
        report = _report([_grade("105525")], rules_version=None)

        assert compare_reports(report, report).rules_confirmed is False

    def test_a_stated_matching_version_is_agreement(self) -> None:
        report = _report([_grade("105525")])

        assert compare_reports(report, report).rules_confirmed is True

    def test_a_whitespace_only_version_reads_as_unstated(self) -> None:
        assert read_rules_version({"rules_version": "   "}) == ""
        assert read_rules_version({"rules_version": 3}) == ""
        assert read_rules_version({}) == ""


class TestAbsenceIsNeverRenderedAsAValue:
    def test_a_grade_with_no_id_is_counted_and_never_keyed_on_a_placeholder(self) -> None:
        earlier = _report([_grade(None), _grade(None, name="Another"), _grade("105525")])
        later = _report([_grade(None), _grade("105525")])

        diff = compare_reports(earlier, later)

        assert diff.unmatchable_earlier == 2
        assert diff.unmatchable_later == 1
        # Two unidentified rows did not collide onto one key and produce a phantom institution.
        assert diff.compared == 1
        assert [c.unit_id for c in diff.changed] == []

    def test_an_unknown_classification_word_is_unreadable_and_not_a_transition(self) -> None:
        earlier = _report([_grade("105525", fields={_FIELD: "reported"})])
        later = _report([_grade("105525", fields={_FIELD: "provisionally_withheld"})])

        diff = compare_reports(earlier, later)

        (change,) = diff.changed
        assert change.transitions == ()
        assert change.unreadable_fields == (_FIELD,)
        assert diff.matrix == ()

    def test_a_score_that_had_nothing_to_move_from_is_none_and_not_zero(self) -> None:
        earlier = _report([_grade("105525", score=None, letter=None)])
        later = _report(
            [
                _grade(
                    "105525", score=0.5, letter="D", fields={_FIELD: "missing", _OTHER: "reported"}
                )
            ]
        )

        (change,) = compare_reports(earlier, later).changed

        assert change.score_change is None
        assert change.gradeability_changed is True
        assert change.letter_moved is False

    def test_a_field_graded_in_only_one_report_is_not_compared(self) -> None:
        earlier = _report([_grade("105525", fields={_FIELD: "reported"})])
        later = _report(
            [_grade("105525", fields={_FIELD: "reported", "Pell share": "missing"})],
        )

        diff = compare_reports(earlier, later)

        assert diff.changed == ()
        assert diff.fields_only_in_later == ("Pell share",)
        assert diff.fields_only_in_earlier == ()

    def test_a_field_this_project_stopped_grading_is_not_compared_either(self) -> None:
        """The other direction, which nothing here had ever produced.

        Every assertion about ``fields_only_in_earlier`` in this module compared it against the
        empty tuple, so the branch in ``_transitions`` that skips a vanished label had never
        executed and the sentence that reports it had only ever been reached through
        ``fields_only_in_later``. A field this project stops grading is the more consequential of
        the two directions: it is the grader going quiet about something, and it must not look
        like every institution losing it at once.
        """
        earlier = _report(
            [_grade("105525", fields={_FIELD: "reported", "Pell share": "reported"})],
        )
        later = _report([_grade("105525", fields={_FIELD: "reported"})])

        diff = compare_reports(earlier, later)

        assert diff.fields_only_in_earlier == ("Pell share",)
        assert diff.fields_only_in_later == ()
        assert diff.changed == ()
        assert diff.matrix == ()

    def test_a_field_that_vanished_is_not_a_transition_for_a_single_institution(self) -> None:
        """The skip has to hold per institution, not merely in the aggregate.

        Counted as a transition, a field dropped from the graded set would read as every graded
        institution moving at once -- the loudest wrong finding this verb could produce, and one
        no reader could distinguish from a real collapse in disclosure.
        """
        vanished = "Pell share"
        earlier = _report(
            [
                _grade("105525", fields={_FIELD: "reported", vanished: "reported"}),
                _grade("125310", fields={_FIELD: "missing", vanished: "suppressed"}),
            ]
        )
        later = _report(
            [
                _grade("105525", fields={_FIELD: "reported"}),
                _grade("125310", fields={_FIELD: "missing"}),
            ]
        )

        diff = compare_reports(earlier, later)

        assert diff.compared == 2
        assert diff.transition_count == 0
        assert all(vanished not in t.field_label for c in diff.changed for t in c.transitions)

    def test_an_entry_that_is_not_a_grade_is_counted_rather_than_dropped(self) -> None:
        """A row that is not an object at all: read, refused, and said out loud.

        It used to be skipped in silence, which is the failure this module already refuses one
        branch over for a grade with no id -- a comparison that quietly drops rows reports a
        smaller population as a stable one. It is counted apart from the unmatchable tally
        because the remedies differ: no id is a gap in the grader, an unreadable entry is a
        damaged or hand-edited file.
        """
        earlier: dict[str, Any] = _report([_grade("105525")])
        earlier["grades"] = [*earlier["grades"], None, "not a grade", 7]
        later = _report([_grade("105525")])

        diff = compare_reports(earlier, later)

        assert diff.unreadable_rows_earlier == 3
        assert diff.unreadable_rows_later == 0
        assert diff.unmatchable_earlier == 0
        assert diff.compared == 1
        assert diff.as_dict()["unreadable_rows_earlier"] == 3

    def test_a_fields_block_that_is_not_an_object_yields_no_states(self) -> None:
        """A grade whose ``fields`` is not a mapping has no classifications to compare.

        The row still carries an id, so it is matched and counted in ``compared``. What it must
        not do is manufacture transitions, and it must not take the run down either.
        """
        earlier = _report([{**_grade("105525"), "fields": "reported"}])
        later = _report([_grade("105525")])

        diff = compare_reports(earlier, later)

        assert diff.compared == 1
        assert diff.transition_count == 0
        assert diff.fields_only_in_later == (_FIELD, _OTHER)

    def test_an_institution_that_left_the_frame_did_not_stop_disclosing(self) -> None:
        earlier = _report([_grade("105525"), _grade("125310", name="Closed College")])
        later = _report([_grade("105525"), _grade("999999", name="New College")])

        diff = compare_reports(earlier, later)

        assert [m.unit_id for m in diff.left] == ["125310"]
        assert [m.unit_id for m in diff.entered] == ["999999"]
        # Neither shows up as a field transition, which is the institution-level form of the
        # denominator lesson drift.py records at length.
        assert diff.matrix == ()

    def test_a_null_name_never_becomes_the_string_none(self) -> None:
        earlier = _report([_grade("105525", name=None, state=None)])
        later = _report(
            [
                _grade(
                    "105525",
                    name=None,
                    state=None,
                    fields={_FIELD: "missing", _OTHER: "reported"},
                )
            ]
        )

        (change,) = compare_reports(earlier, later).changed

        assert change.name is None
        assert change.state is None


class TestTheRulesVersionIsStamped:
    def test_the_declared_version_is_the_one_this_build_writes(self) -> None:
        # Pinned to the literal. A property over "some string" would pass against a version that
        # silently became empty, and an empty version makes every diff say it could not confirm
        # the rules while looking exactly like one that could.
        assert grading.RULES_VERSION == "2026-09-06.1"
        assert grading.RULES_VERSION.strip() == grading.RULES_VERSION
        assert grading.RULES_VERSION != ""

    def test_a_graded_report_carries_it(self, tmp_path: Path) -> None:
        source = tmp_path / "capture.json"
        source.write_text(
            json.dumps([{"id": 7, "school.name": "Fixture College", "school.state": "CA"}]),
            encoding="utf-8",
        )
        out = tmp_path / "report.json"

        assert cli.main(["grade", "--source", str(source), "--out", str(out)]) == 0

        assert json.loads(out.read_text())["rules_version"] == grading.RULES_VERSION


class TestTheVerb:
    def _write(self, path: Path, payload: dict[str, Any]) -> str:
        path.write_text(json.dumps(payload), encoding="utf-8")
        return str(path)

    def test_identical_reports_exit_zero_and_say_so(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        report = self._write(tmp_path / "a.json", _report([_grade("105525")]))

        assert cli.main(["diff-report", report, report]) == 0

        assert "no institution-level transitions" in capsys.readouterr().out

    def test_a_transition_is_printed_with_its_institution(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        earlier = self._write(tmp_path / "a.json", _report([_grade("105525")]))
        later = self._write(
            tmp_path / "b.json",
            _report(
                [
                    _grade(
                        "105525",
                        score=0.5,
                        letter="D",
                        fields={_FIELD: "missing", _OTHER: "reported"},
                    )
                ]
            ),
        )

        assert cli.main(["diff-report", earlier, later]) == 0

        out = capsys.readouterr().out
        assert "reported -> missing" in out
        assert "105525" in out
        assert "B -> D" in out

    def test_a_mismatched_source_exits_one_with_the_reason(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        earlier = self._write(tmp_path / "a.json", _report([_grade("105525")]))
        later = self._write(
            tmp_path / "b.json", _report([_grade("105525")], source="IPEDS directory")
        )

        assert cli.main(["diff-report", earlier, later]) == 1

        assert "different populations" in capsys.readouterr().err

    def test_a_mismatched_rules_version_exits_one_naming_both(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        earlier = self._write(
            tmp_path / "a.json", _report([_grade("105525")], rules_version="2026-01-01.1")
        )
        later = self._write(tmp_path / "b.json", _report([_grade("105525")]))

        assert cli.main(["diff-report", earlier, later]) == 1

        err = capsys.readouterr().err
        assert "2026-01-01.1" in err and "2026-09-06.1" in err

    def test_an_unconfirmed_rules_version_is_said_out_loud(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        earlier = self._write(tmp_path / "a.json", _report([_grade("105525")], rules_version=None))
        later = self._write(tmp_path / "b.json", _report([_grade("105525")]))

        assert cli.main(["diff-report", earlier, later]) == 0

        assert "do not confirm the same" in capsys.readouterr().out

    def test_one_institution_can_be_asked_about(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        earlier = self._write(
            tmp_path / "a.json",
            _report([_grade("105525"), _grade("125310", name="Elsewhere College")]),
        )
        later = self._write(
            tmp_path / "b.json",
            _report(
                [
                    _grade("105525", fields={_FIELD: "missing", _OTHER: "reported"}),
                    _grade(
                        "125310",
                        name="Elsewhere College",
                        fields={_FIELD: "reported", _OTHER: "missing"},
                    ),
                ]
            ),
        )

        assert cli.main(["diff-report", earlier, later, "--institution", "105525"]) == 0

        out = capsys.readouterr().out
        assert "105525" in out
        assert "125310" not in out

    def test_an_institution_in_neither_report_is_an_error_not_an_empty_diff(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # The whole point. "No transitions" for a mistyped id is an absence rendered as a
        # finding, and the finding it renders is the most reassuring one available.
        report = self._write(tmp_path / "a.json", _report([_grade("105525")]))

        assert cli.main(["diff-report", report, report, "--institution", "000000"]) == 1

        assert "in neither report" in capsys.readouterr().err

    def test_json_output_is_byte_identical_across_runs(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        earlier = self._write(
            tmp_path / "a.json",
            _report([_grade("105525"), _grade("125310", name="Elsewhere College")]),
        )
        later = self._write(
            tmp_path / "b.json",
            _report(
                [
                    _grade("105525", fields={_FIELD: "missing", _OTHER: "suppressed"}),
                    _grade("999999", name="New College"),
                ]
            ),
        )

        assert cli.main(["diff-report", earlier, later, "--json"]) == 0
        first = capsys.readouterr().out
        assert cli.main(["diff-report", earlier, later, "--json"]) == 0
        second = capsys.readouterr().out

        assert first == second
        payload = json.loads(first)
        assert payload["rules_confirmed"] is True
        assert payload["matrix"] == [
            {"transition": "reported -> missing", "institutions": 1},
            {"transition": "reported -> suppressed", "institutions": 1},
        ]
        assert [m["unit_id"] for m in payload["left"]] == ["125310"]
        assert [m["unit_id"] for m in payload["entered"]] == ["999999"]

    def test_the_unmatchable_and_uncompared_are_printed(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        earlier = self._write(
            tmp_path / "a.json",
            _report([_grade(None), _grade("105525", fields={_FIELD: "reported"})]),
        )
        later = self._write(
            tmp_path / "b.json",
            _report(
                [_grade("105525", fields={_FIELD: "reported", "Pell share": "missing"})],
            ),
        )

        assert cli.main(["diff-report", earlier, later]) == 0

        out = capsys.readouterr().out
        assert "carry no id" in out
        assert "Pell share was graded in the later report and not the earlier one" in out

    def test_the_two_uncompared_directions_are_different_sentences(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """One field started being graded, one stopped, and the reader can tell which is which.

        Both used to print the identical sentence, because the renderer concatenated the two
        tuples the payload keeps apart. "Graded in only one of the two reports" is true of a
        field this project added and of one it dropped, and those send a reader to opposite
        places.
        """
        earlier = self._write(
            tmp_path / "a.json",
            _report([_grade("105525", fields={_FIELD: "reported", "Retention": "reported"})]),
        )
        later = self._write(
            tmp_path / "b.json",
            _report([_grade("105525", fields={_FIELD: "reported", "Pell share": "missing"})]),
        )

        assert cli.main(["diff-report", earlier, later]) == 0

        out = capsys.readouterr().out
        assert "Retention was graded in the earlier report and not the later one" in out
        assert "Pell share was graded in the later report and not the earlier one" in out
        # The disclaimer belongs on the dropped field and only on it: this verb has not shown
        # that anybody stopped publishing anything it stopped grading.
        stopped = next(line for line in out.splitlines() if line.strip().startswith("Retention"))
        started = next(line for line in out.splitlines() if line.strip().startswith("Pell share"))
        assert "stopped publishing" in stopped
        assert "stopped publishing" not in started

    def test_entries_that_are_not_grades_are_reported_and_not_confused_with_missing_ids(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # The malformed row is appended after `_report` has built the payload, because the
        # helper types its argument as a list of grades and this is deliberately not one.
        payload = _report([_grade(None), _grade("105525")])
        payload["grades"] = [*payload["grades"], None]
        earlier = self._write(tmp_path / "a.json", payload)
        later = self._write(tmp_path / "b.json", _report([_grade("105525")]))

        assert cli.main(["diff-report", earlier, later]) == 0

        out = capsys.readouterr().out
        assert "1 grades in the earlier report carry no id" in out
        assert "1 entries in the earlier report are not grades and could not be read" in out

    def test_an_unreadable_word_is_labelled_rather_than_counted(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        earlier = self._write(
            tmp_path / "a.json", _report([_grade("105525", fields={_FIELD: "reported"})])
        )
        later = self._write(
            tmp_path / "b.json",
            _report([_grade("105525", fields={_FIELD: "provisionally_withheld"})]),
        )

        assert cli.main(["diff-report", earlier, later]) == 0

        out = capsys.readouterr().out
        assert "unreadable classification" in out
        assert "reported -> provisionally_withheld" not in out

    def test_becoming_ungradeable_prints_as_words_and_never_as_f(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        earlier = self._write(tmp_path / "a.json", _report([_grade("105525")]))
        later = self._write(
            tmp_path / "b.json",
            _report(
                [
                    _grade(
                        "105525",
                        score=None,
                        letter=None,
                        fields={_FIELD: "suppressed", _OTHER: "suppressed"},
                    )
                ]
            ),
        )

        assert cli.main(["diff-report", earlier, later]) == 0

        out = capsys.readouterr().out
        assert "B -> not gradeable" in out
        assert "B -> F" not in out
