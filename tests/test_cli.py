"""The three verbs, end to end, against a stubbed source."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from disclosed import cli
from disclosed.sources import college_scorecard

_RECORDS: list[dict[str, Any]] = [
    {
        "id": 1,
        "school.name": "Complete College",
        "school.state": "CA",
        "latest.earnings.10_yrs_after_entry.median": 52_000,
        "latest.completion.completion_rate_4yr_150nt": 0.64,
        "latest.admissions.admission_rate.overall": 0.31,
        "latest.aid.median_debt.completers.overall": 22_300,
        "latest.cost.tuition.in_state": 11_400,
        "latest.student.size": 11_635,
    },
    {
        "id": 2,
        "school.name": "Gappy College",
        "school.state": "CA",
        "latest.earnings.10_yrs_after_entry.median": None,
        "latest.completion.completion_rate_4yr_150nt": None,
        "latest.admissions.admission_rate.overall": 0,  # implausible, not missing
        "latest.aid.median_debt.completers.overall": 0,  # credible zero
        "latest.cost.tuition.in_state": 9_000,
        "latest.student.size": 400,
    },
]


@pytest.fixture
def stub_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        college_scorecard,
        "iter_institutions",
        lambda limit=None: iter(_RECORDS if limit is None else _RECORDS[:limit]),
    )


class TestGrade:
    def test_writes_a_report_and_separates_implausible_from_missing(
        self, stub_source: None, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        out = tmp_path / "report.json"
        assert cli.main(["grade", "--out", str(out)]) == 0
        report = json.loads(out.read_text())

        assert report["institutions"] == 2
        # The exact-zero admission rate is a finding, not an absence and not a value.
        (implausible,) = report["implausible"]
        assert implausible["field"] == "Admission rate"
        assert implausible["value"] == 0
        assert implausible["rationale"]

        gappy = next(g for g in report["grades"] if g["name"] == "Gappy College")
        assert gappy["fields"]["Admission rate"] == "implausible"
        assert gappy["fields"]["Median earnings 10 years after entry"] == "missing"
        # Zero debt is a real disclosure and must not be swept in with the artifacts.
        assert gappy["fields"]["Median debt at completion"] == "reported"

        assert "graded 2 institutions" in capsys.readouterr().out

    def test_refuses_to_write_an_empty_report(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Zero institutions means the fetch failed, not that no college exists."""
        monkeypatch.setattr(college_scorecard, "iter_institutions", lambda limit=None: iter([]))
        out = tmp_path / "report.json"
        assert cli.main(["grade", "--out", str(out)]) == 1
        assert not out.exists()

    def test_limit_is_passed_through(self, stub_source: None, tmp_path: Path) -> None:
        out = tmp_path / "report.json"
        cli.main(["grade", "--limit", "1", "--out", str(out)])
        assert json.loads(out.read_text())["institutions"] == 1


class TestSnapshotAndDrift:
    def _report(self, tmp_path: Path, stub: None) -> Path:
        out = tmp_path / "report.json"
        cli.main(["grade", "--out", str(out)])
        return out

    def test_snapshot_counts_fields(self, stub_source: None, tmp_path: Path) -> None:
        report = self._report(tmp_path, stub_source)
        snap = tmp_path / "snap.json"
        assert cli.main(
            ["snapshot", "--report", str(report), "--taken", "2026-08-05", "--out", str(snap)]
        ) == 0
        data = json.loads(snap.read_text())
        assert data["taken"] == "2026-08-05"
        assert data["institutions"] == 2
        assert data["reported"]["In-state tuition"] == 2
        assert data["missing"]["Median earnings 10 years after entry"] == 1

    def test_drift_reports_no_change_between_identical_snapshots(
        self, stub_source: None, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        report = self._report(tmp_path, stub_source)
        for name in ("a", "b"):
            cli.main(
                ["snapshot", "--report", str(report), "--taken", name,
                 "--out", str(tmp_path / f"{name}.json")]
            )
        assert cli.main(["drift", str(tmp_path / "a.json"), str(tmp_path / "b.json")]) == 0
        assert "no change" in capsys.readouterr().out

    def test_drift_flags_a_systemic_loss(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        earlier = tmp_path / "earlier.json"
        later = tmp_path / "later.json"
        earlier.write_text(
            json.dumps(
                {"taken": "a", "institutions": 1000,
                 "reported": {"Admission rate": 900}, "missing": {"Admission rate": 100}}
            )
        )
        later.write_text(
            json.dumps(
                {"taken": "b", "institutions": 1000,
                 "reported": {"Admission rate": 300}, "missing": {"Admission rate": 700}}
            )
        )
        assert cli.main(["drift", str(earlier), str(later)]) == 0
        out = capsys.readouterr().out
        assert "SYSTEMIC" in out
        assert "lost" in out


def test_no_subcommand_is_an_error() -> None:
    with pytest.raises(SystemExit):
        cli.main([])
