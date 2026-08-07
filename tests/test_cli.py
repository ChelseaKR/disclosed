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

    def test_a_failed_fetch_exits_nonzero_with_the_reason(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A scheduled run that cannot reach the API must break visibly. Committing a snapshot
        from a half-finished fetch would publish a nationwide reporting collapse that never
        happened."""

        def refuse(limit: int | None = None) -> Any:
            raise college_scorecard.RateLimited("page 4 still returning HTTP 429")

        monkeypatch.setattr(college_scorecard, "iter_institutions", refuse)
        out = tmp_path / "report.json"
        assert cli.main(["grade", "--out", str(out)]) == 1
        assert not out.exists()
        assert "429" in capsys.readouterr().err

    def test_a_truncated_walk_exits_nonzero_rather_than_publishing_national(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Issue #1: a walk that stops early -- a well-formed page carrying nothing, before
        metadata.total was reached -- must reach the caller as a failure, not as a completed
        national run. ``iter_institutions`` now raises ScorecardError for exactly this instead of
        returning silently; this test proves that failure propagates through `grade` the same way
        a transport failure already does, and nothing is written."""

        def truncated(limit: int | None = None) -> Any:
            yield _RECORDS[0]
            raise college_scorecard.ScorecardError(
                "College Scorecard page 1 returned no usable results after 1 institutions, "
                "short of the API's stated total of 6300."
            )

        monkeypatch.setattr(college_scorecard, "iter_institutions", truncated)
        out = tmp_path / "report.json"
        assert cli.main(["grade", "--out", str(out)]) == 1
        assert not out.exists()
        err = capsys.readouterr().err
        assert "fetch failed, no report written" in err
        assert "short of the API's stated total" in err

    def test_a_national_run_is_labelled_national_with_full_coverage(
        self, stub_source: None, tmp_path: Path
    ) -> None:
        """Regression guard: a genuinely exhausted walk with no --source and no --limit must keep
        reporting national/coverage 1.0 exactly as before this fix."""
        out = tmp_path / "report.json"
        assert cli.main(["grade", "--out", str(out)]) == 0
        scope = json.loads(out.read_text())["scope"]
        assert scope["kind"] == "national"
        assert scope["coverage"] == 1.0

    def test_a_limited_run_is_labelled_sample_not_national(
        self, stub_source: None, tmp_path: Path
    ) -> None:
        """--limit is a deliberate sample and must never be affected by this fix."""
        out = tmp_path / "report.json"
        assert cli.main(["grade", "--limit", "1", "--out", str(out)]) == 0
        scope = json.loads(out.read_text())["scope"]
        assert scope["kind"] == "sample"
        assert scope["coverage"] != 1.0

    def test_a_replayed_source_is_labelled_sample_not_national(
        self, tmp_path: Path
    ) -> None:
        """--source is a replay of a capture and must never be affected by this fix."""
        source = tmp_path / "records.json"
        source.write_text(json.dumps(_RECORDS))
        out = tmp_path / "report.json"
        assert cli.main(["grade", "--source", str(source), "--out", str(out)]) == 0
        assert json.loads(out.read_text())["scope"]["kind"] == "sample"

    def test_a_source_file_that_is_not_a_list_is_refused(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps({"results": []}))
        out = tmp_path / "report.json"
        assert cli.main(["grade", "--source", str(bad), "--out", str(out)]) == 1
        assert "not a JSON array" in capsys.readouterr().err
        assert not out.exists()

    def test_replay_from_a_captured_file_matches_the_api_path(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Replay is what makes a run reproducible, so it must grade identically to a live fetch."""
        source = tmp_path / "records.json"
        source.write_text(json.dumps(_RECORDS))
        out = tmp_path / "report.json"
        assert cli.main(["grade", "--source", str(source), "--out", str(out)]) == 0
        assert json.loads(out.read_text())["institutions"] == 2

    def test_limit_is_passed_through(self, stub_source: None, tmp_path: Path) -> None:
        out = tmp_path / "report.json"
        cli.main(["grade", "--limit", "1", "--out", str(out)])
        assert json.loads(out.read_text())["institutions"] == 1

    def test_unidentified_records_never_borrow_each_others_peers(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Two id-less records once collided on the key "None", and the second finding was
        published carrying the first record's peer group. Peer evidence attached to the wrong
        school is worse than none, because a reader can cite it."""
        anonymous: list[dict[str, Any]] = [
            {
                "id": None,
                "school.name": None,
                "school.state": "CA",
                "latest.admissions.admission_rate.overall": 0,
                "latest.student.size": 500,
            },
            {
                "id": None,
                "school.name": None,
                "school.state": "NY",
                "latest.cost.tuition.in_state": 0,
                "latest.student.size": 900,
            },
        ]
        monkeypatch.setattr(
            college_scorecard, "iter_institutions", lambda limit=None: iter(anonymous)
        )
        out = tmp_path / "report.json"
        assert cli.main(["grade", "--out", str(out)]) == 0
        report = json.loads(out.read_text())

        assert len(report["implausible"]) == 2
        for finding in report["implausible"]:
            # No id means no defensible peer claim, so none is made.
            assert "peers" not in finding
            assert finding["unit_id"] is None
            assert finding["name"] is None


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

    def _snapshots(self, tmp_path: Path, earlier: dict[str, Any], later: dict[str, Any]) -> None:
        (tmp_path / "earlier.json").write_text(json.dumps({"taken": "a", **earlier}))
        (tmp_path / "later.json").write_text(json.dumps({"taken": "b", **later}))

    def test_drift_flags_a_systemic_loss(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._snapshots(
            tmp_path,
            {"institutions": 1000, "reported": {"Admission rate": 900},
             "missing": {"Admission rate": 100}, "applicable": {"Admission rate": 1000}},
            {"institutions": 1000, "reported": {"Admission rate": 300},
             "missing": {"Admission rate": 700}, "applicable": {"Admission rate": 1000}},
        )
        assert cli.main(
            ["drift", str(tmp_path / "earlier.json"), str(tmp_path / "later.json")]
        ) == 0
        out = capsys.readouterr().out
        assert "SYSTEMIC" in out
        assert "lost" in out

    def test_a_shrinking_population_is_not_a_reporting_collapse(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The real 2021-to-2023 IPEDS numbers for the institution web address. 130 fewer
        institutions published one because 131 institutions stopped existing; the share reporting
        it went up. The count-based version called this a systemic 2.1% collapse."""
        self._snapshots(
            tmp_path,
            {"institutions": 6289, "reported": {"Institution web address": 6115},
             "missing": {"Institution web address": 4},
             "applicable": {"Institution web address": 6119}},
            {"institutions": 6163, "reported": {"Institution web address": 5985},
             "missing": {"Institution web address": 3},
             "applicable": {"Institution web address": 5988}},
        )
        assert cli.main(
            ["drift", str(tmp_path / "earlier.json"), str(tmp_path / "later.json")]
        ) == 0
        out = capsys.readouterr().out
        assert "SYSTEMIC" not in out
        assert "gained" in out
        assert "a change in who it applies to rather than in who answered" in out

    def test_a_snapshot_that_recorded_no_denominator_says_so(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Snapshots predating the applicable counts cannot yield a rate. Unmeasured must print
        as unmeasured: through a percent format it would read as "we checked, nothing moved"."""
        self._snapshots(
            tmp_path,
            {"institutions": 1000, "reported": {"Admission rate": 900},
             "missing": {"Admission rate": 100}},
            {"institutions": 1000, "reported": {"Admission rate": 300},
             "missing": {"Admission rate": 700}},
        )
        assert cli.main(
            ["drift", str(tmp_path / "earlier.json"), str(tmp_path / "later.json")]
        ) == 0
        out = capsys.readouterr().out
        assert "rate unmeasured" in out
        assert "SYSTEMIC" not in out


class TestCrosscheck:
    """Grading IPEDS and reporting where it disagrees with the Scorecard about one institution."""

    _HEADER = (
        "UNITID,INSTNM,STABBR,CONTROL,ICLEVEL,SECTOR,INSTCAT,UGOFFER,CYACTIVE,PSET4FLG,"
        "WEBADDR,NPRICURL,FAIDURL,ADMINURL,DISAURL,ATHURL"
    )
    _ROW = (
        '104717,"Grand Canyon University",AZ,3,1,3,2,1,1,1,'
        "www.gcu.edu/,www.gcu.edu/npc,www.gcu.edu/aid,www.gcu.edu/admit,"
        "www.gcu.edu/disability,www.gcu.edu/athletics"
    )

    @pytest.fixture
    def cache(self, tmp_path: Path) -> Path:
        import zipfile

        path = tmp_path / "HD2023.zip"
        with zipfile.ZipFile(path, "w") as bundle:
            bundle.writestr("hd2023.csv", f"{self._HEADER}\n{self._ROW}\n")
        return path

    @pytest.fixture
    def characteristics(self, tmp_path: Path) -> Path:
        """A cached characteristics archive, so no test in this file touches the network.

        Both IPEDS files are mandatory, so without this fixture these tests quietly downloaded
        380 KB from NCES on every run and passed for the wrong reason: green because the internet
        was up, not because the code was right.
        """
        import zipfile

        path = tmp_path / "IC2023.zip"
        with zipfile.ZipFile(path, "w") as bundle:
            bundle.writestr("ic2023.csv", "UNITID,ATHASSOC,SPORT1,SPORT2,SPORT3,SPORT4\n"
                                          "104717,1,1,1,1,1\n")
        return path

    def _argv(self, cache: Path, characteristics: Path, out: Path) -> list[str]:
        return [
            "crosscheck",
            "--cache", str(cache),
            "--characteristics", str(characteristics),
            "--out", str(out),
        ]

    def test_grades_ipeds_alone_when_given_no_scorecard_capture(
        self, cache: Path, characteristics: Path, tmp_path: Path,
        capsys: pytest.CaptureFixture[str]
    ) -> None:
        out = tmp_path / "cross.json"
        assert cli.main(self._argv(cache, characteristics, out)) == 0
        payload = json.loads(out.read_text())
        assert payload["institutions"] == 1
        assert payload["contradictions"] == []
        assert "pass --source" in capsys.readouterr().out

    def test_the_directory_is_the_population_so_the_run_is_national(
        self, cache: Path, characteristics: Path, tmp_path: Path
    ) -> None:
        """IPEDS publishes a file, not a page of a file, so grading it grades everyone."""
        out = tmp_path / "cross.json"
        assert cli.main(self._argv(cache, characteristics, out)) == 0
        assert json.loads(out.read_text())["scope"]["kind"] == "national"

    def test_reports_a_disagreement_between_the_two_federal_sources(
        self, cache: Path, characteristics: Path, tmp_path: Path,
        capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The live finding: the Scorecard files GCU as private nonprofit, IPEDS as for-profit."""
        source = tmp_path / "sc.json"
        source.write_text(
            json.dumps(
                [{"id": 104717, "school.name": "Grand Canyon University",
                  "school.state": "AZ", "school.ownership": 2}]
            )
        )
        out = tmp_path / "cross.json"
        assert cli.main([*self._argv(cache, characteristics, out), "--source", str(source)]) == 0
        (found,) = json.loads(out.read_text())["contradictions"]
        assert found["field_label"] == "Sector"
        assert found["scorecard_value"] == "private nonprofit (2)"
        assert found["ipeds_value"] == "private for-profit (3)"
        printed = capsys.readouterr().out
        assert "Grand Canyon University" in printed
        assert "cross-source disagreements  1" in printed

    def test_an_unreadable_directory_writes_nothing(
        self, characteristics: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        bad = tmp_path / "HD2023.zip"
        bad.write_bytes(b"not a zip")
        out = tmp_path / "cross.json"
        assert cli.main(self._argv(bad, characteristics, out)) == 1
        assert "IPEDS unreadable" in capsys.readouterr().err
        assert not out.exists()


def test_no_subcommand_is_an_error() -> None:
    with pytest.raises(SystemExit):
        cli.main([])


class TestSnapshotProvenance:
    def test_a_snapshot_records_the_source_its_report_declared(
        self, stub_source: None, tmp_path: Path
    ) -> None:
        report = tmp_path / "report.json"
        cli.main(["grade", "--out", str(report)])
        snap = tmp_path / "snap.json"
        assert cli.main(
            ["snapshot", "--report", str(report), "--taken", "x", "--out", str(snap)]
        ) == 0
        assert json.loads(snap.read_text())["source"] == "College Scorecard"

    def test_drift_across_two_populations_exits_nonzero_rather_than_saying_nothing(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The failure mode is not a crash, it is the sentence "no change in per-field
        disclosure" printed about two corpora that share no field."""
        for name, source in (("a", "College Scorecard"), ("b", "IPEDS directory")):
            (tmp_path / f"{name}.json").write_text(
                json.dumps(
                    {"taken": name, "institutions": 10, "reported": {"Enrollment": 5},
                     "missing": {"Enrollment": 5}, "applicable": {"Enrollment": 10},
                     "source": source}
                )
            )
        assert cli.main(["drift", str(tmp_path / "a.json"), str(tmp_path / "b.json")]) == 1
        assert "different populations" in capsys.readouterr().err
