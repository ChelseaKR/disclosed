"""The CSV export, where an empty cell is the most dangerous token in the project.

A spreadsheet renders "not collected", "not applicable", "withheld" and "zero" identically and
then lets someone average them. These tests exist to keep the export from ever offering that
opportunity.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

import pytest

from disclosed import cli, dataset
from disclosed.disclosure import Disclosure
from disclosed.fields import FIELDS

_REPORT: dict[str, Any] = {
    "grades": [
        {
            "unit_id": "2",
            "name": "Gradeable But Silent",
            "state": "CA",
            "score": 0.0,
            "letter": "F",
            "fields": {f.label: "missing" for f in FIELDS},
        },
        {
            "unit_id": "1",
            "name": "Complete College",
            "state": "CA",
            "score": 1.0,
            "letter": "A",
            "fields": {f.label: "reported" for f in FIELDS},
        },
        {
            "unit_id": "3",
            "name": "Everything Suppressed",
            "state": "VT",
            "score": None,
            "letter": None,
            "fields": {f.label: "suppressed" for f in FIELDS},
        },
    ]
}


def _rows(report: dict[str, Any] | None = None) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(dataset.to_csv(report or _REPORT))))


class TestTheEmptyCell:
    def test_an_ungradeable_institution_has_an_empty_score_and_says_why(self) -> None:
        """Empty, never 0. And the reason travels in the next column so the blank is not
        ambiguous on its own."""
        row = next(r for r in _rows() if r["unit_id"] == "3")
        assert row["disclosure_score"] == ""
        assert row["gradeable"] == "false"
        assert row["letter"] == ""

    def test_a_genuine_zero_is_a_zero_and_is_marked_gradeable(self) -> None:
        """The mirror case. This institution was gradeable and published nothing, which is a
        different fact from having nothing to grade, and the file has to keep them apart."""
        row = next(r for r in _rows() if r["unit_id"] == "2")
        assert row["disclosure_score"] == "0.000000"
        assert row["gradeable"] == "true"
        assert row["letter"] == "F"

    def test_no_classification_column_is_ever_empty(self) -> None:
        """An empty cell in a classification column would put the export straight back into the
        ambiguity the whole file is arranged to avoid."""
        for row in _rows():
            for field in FIELDS:
                assert row[field.column] != ""

    def test_a_field_absent_from_the_report_is_named_rather_than_blanked(self) -> None:
        report = json.loads(json.dumps(_REPORT))
        del report["grades"][0]["fields"][FIELDS[0].label]
        row = next(r for r in _rows(report) if r["unit_id"] == "2")
        assert row[FIELDS[0].column] == "not_in_report"

    def test_an_absent_name_is_an_empty_cell_not_the_word_none(self) -> None:
        report = json.loads(json.dumps(_REPORT))
        report["grades"][0]["name"] = None
        report["grades"][0]["unit_id"] = None
        row = next(r for r in _rows(report) if r["state"] == "CA" and r["letter"] == "F")
        assert row["name"] == ""
        assert row["unit_id"] == ""

    def test_no_cell_anywhere_contains_the_string_none(self) -> None:
        report = json.loads(json.dumps(_REPORT))
        report["grades"][0]["name"] = None
        for row in _rows(report):
            assert "None" not in row.values()


class TestShape:
    def test_rows_are_sorted_by_unit_id_so_the_file_diffs_cleanly(self) -> None:
        assert [r["unit_id"] for r in _rows()] == ["1", "2", "3"]

    def test_regenerating_from_the_same_report_is_byte_identical(self) -> None:
        assert dataset.to_csv(_REPORT) == dataset.to_csv(_REPORT)

    def test_column_names_trace_back_to_the_source_key(self) -> None:
        assert dataset.to_csv(_REPORT).startswith(
            "unit_id,name,state,disclosure_score,gradeable,letter,"
        )
        for field in FIELDS:
            assert field.column in dataset.to_csv(_REPORT)

    def test_rfc4180_line_endings(self) -> None:
        assert dataset.to_csv(_REPORT).count("\r\n") == 4


class TestSchema:
    def test_the_schema_describes_exactly_the_columns_the_csv_has(self) -> None:
        """Generated in the same pass as the CSV, so a schema that drifts is a test failure."""
        described = [f["name"] for f in dataset.to_schema()["schema"]["fields"]]
        written = next(csv.reader(io.StringIO(dataset.to_csv(_REPORT))))
        assert described == written

    def test_missing_values_is_declared_as_the_empty_string_and_nothing_else(self) -> None:
        """Left at the default, a consumer may decide 0 or NA or -1 also mean missing, and this
        project exists because of what happens when people guess about that."""
        assert dataset.to_schema()["schema"]["missingValues"] == [""]

    def test_every_classification_column_enumerates_its_allowed_values(self) -> None:
        by_name = {f["name"]: f for f in dataset.to_schema()["schema"]["fields"]}
        for field in FIELDS:
            enum = by_name[field.column]["constraints"]["enum"]
            for disclosure in Disclosure:
                assert disclosure.value in enum

    def test_every_classification_column_carries_its_rationale(self) -> None:
        by_name = {f["name"]: f for f in dataset.to_schema()["schema"]["fields"]}
        for field in FIELDS:
            assert field.rationale in by_name[field.column]["description"]
            assert by_name[field.column]["source_key"] == field.key

    def test_the_score_column_warns_that_empty_is_not_zero(self) -> None:
        by_name = {f["name"]: f for f in dataset.to_schema()["schema"]["fields"]}
        description = by_name["disclosure_score"]["description"]
        assert "EMPTY, never" in description
        assert "gradeable" in description

    def test_primary_key_is_the_unit_id(self) -> None:
        assert dataset.to_schema()["schema"]["primaryKey"] == ["unit_id"]

    def test_schema_json_is_stable(self) -> None:
        assert dataset.to_schema_json() == dataset.to_schema_json()


class TestDatasetCommand:
    def test_writes_the_csv_and_the_schema_beside_it(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        report = tmp_path / "report.json"
        report.write_text(json.dumps(_REPORT))
        out = tmp_path / "export" / "dataset.csv"
        assert cli.main(["dataset", "--report", str(report), "--out", str(out)]) == 0
        assert out.exists()
        schema = json.loads((tmp_path / "export" / "dataset.schema.json").read_text())
        assert schema["path"] == "dataset.csv"
        assert "exported 3 rows" in capsys.readouterr().out

    def test_refuses_to_export_an_empty_report(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        report = tmp_path / "report.json"
        report.write_text(json.dumps({"grades": []}))
        out = tmp_path / "dataset.csv"
        assert cli.main(["dataset", "--report", str(report), "--out", str(out)]) == 1
        assert "refusing to export" in capsys.readouterr().err
        assert not out.exists()

    def test_the_committed_export_matches_the_committed_report(self) -> None:
        """The repo ships both; if they disagree, the citable file is the wrong one.

        Compared as bytes. ``read_text`` applies universal-newline translation, which silently
        turns the RFC 4180 line endings back into ``\\n`` and makes this assertion pass or fail
        for reasons that have nothing to do with the data.
        """
        root = Path(__file__).resolve().parent.parent
        report = json.loads((root / "data" / "report.json").read_text(encoding="utf-8"))
        assert (root / "data" / "dataset.csv").read_bytes() == dataset.to_csv(report).encode()

    def test_the_committed_schema_matches_the_field_definitions(self) -> None:
        """The schema ships beside the CSV and had no such check, so it went stale on its own.

        Every field's rationale is copied into the schema as that column's description, which
        makes the published schema a second copy of prose that lives in ``fields.py``. When the
        completion-rate rationale was wrong, this file was one of the three places it was
        published, and the only one nothing was watching.
        """
        root = Path(__file__).resolve().parent.parent
        committed = (root / "data" / "dataset.schema.json").read_text(encoding="utf-8")
        assert committed == dataset.to_schema_json()
