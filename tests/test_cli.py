"""The three verbs, end to end, against a stubbed source."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest

from disclosed import cli, drift
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

    def test_a_replayed_source_is_labelled_sample_not_national(self, tmp_path: Path) -> None:
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


class TestTheCommittedReport:
    def test_it_reproduces_from_the_committed_capture(self, tmp_path: Path) -> None:
        """The report and the capture it claims to describe, checked against each other.

        ``data/report.json`` is where every published figure about the sample comes from, and
        until this existed nothing tied it to ``data/sample.json``. CI regrades the capture on
        every push and throws the result away into ``/tmp``, which proves the pipeline runs and
        proves nothing about the file the repository ships: a rule could change, or the committed
        report could be edited by hand, and both the export test and the replay job would stay
        green while the citable artifact described a grading run nobody could reproduce.

        Compared as parsed JSON rather than as bytes, so that a reformat is not a failure while a
        changed number is.
        """
        root = Path(__file__).resolve().parent.parent
        out = tmp_path / "report.json"
        assert (
            cli.main(["grade", "--source", str(root / "data" / "sample.json"), "--out", str(out)])
            == 0
        )
        committed = json.loads((root / "data" / "report.json").read_text(encoding="utf-8"))
        assert json.loads(out.read_text(encoding="utf-8")) == committed


class TestSnapshotAndDrift:
    def _report(self, tmp_path: Path, stub: None) -> Path:
        out = tmp_path / "report.json"
        cli.main(["grade", "--out", str(out)])
        return out

    def test_snapshot_counts_fields(self, stub_source: None, tmp_path: Path) -> None:
        report = self._report(tmp_path, stub_source)
        snap = tmp_path / "snap.json"
        assert (
            cli.main(
                ["snapshot", "--report", str(report), "--taken", "2026-08-05", "--out", str(snap)]
            )
            == 0
        )
        data = json.loads(snap.read_text())
        assert data["taken"] == "2026-08-05"
        assert data["institutions"] == 2
        assert data["reported"]["In-state tuition"] == 2
        assert data["missing"]["Median earnings 10 years after entry"] == 1

    def test_a_classification_this_version_does_not_know_stays_out_of_the_denominator(
        self, tmp_path: Path
    ) -> None:
        """A report written by a newer version must not read as institutions disclosing less.

        Reports outlive the code that reads them, and the tempting rule -- "it is not suppressed
        and not inapplicable, so it belongs in the denominator" -- puts an unrecognized word into
        the divisor of every drift rate for that field without ever putting it in the numerator.
        The rate falls by an amount that has nothing to do with what any institution published,
        and this module's whole argument is about denominators that move for reasons the
        measurement cannot see. So an unknown word is treated exactly like a field the row never
        carried: counted nowhere, and the rate reported as unmeasurable rather than as a drop.
        """
        report = tmp_path / "report.json"
        report.write_text(
            json.dumps(
                {
                    "grades": [
                        {"unit_id": "1", "fields": {"Admission rate": "embargoed"}},
                        {"unit_id": "2", "fields": {"Admission rate": "embargoed"}},
                    ]
                }
            )
        )
        snap = tmp_path / "snap.json"
        assert (
            cli.main(["snapshot", "--report", str(report), "--taken", "x", "--out", str(snap)]) == 0
        )
        data = json.loads(snap.read_text())
        assert data["applicable"]["Admission rate"] == 0
        assert data["reported"]["Admission rate"] == 0
        assert data["missing"]["Admission rate"] == 0
        assert drift.Snapshot(**data).rate("Admission rate") is None

    def test_drift_reports_no_change_between_identical_snapshots(
        self, stub_source: None, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        report = self._report(tmp_path, stub_source)
        for name in ("a", "b"):
            cli.main(
                [
                    "snapshot",
                    "--report",
                    str(report),
                    "--taken",
                    name,
                    "--out",
                    str(tmp_path / f"{name}.json"),
                ]
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
            {
                "institutions": 1000,
                "reported": {"Admission rate": 900},
                "missing": {"Admission rate": 100},
                "applicable": {"Admission rate": 1000},
            },
            {
                "institutions": 1000,
                "reported": {"Admission rate": 300},
                "missing": {"Admission rate": 700},
                "applicable": {"Admission rate": 1000},
            },
        )
        assert (
            cli.main(["drift", str(tmp_path / "earlier.json"), str(tmp_path / "later.json")]) == 0
        )
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
            {
                "institutions": 6289,
                "reported": {"Institution web address": 6115},
                "missing": {"Institution web address": 4},
                "applicable": {"Institution web address": 6119},
            },
            {
                "institutions": 6163,
                "reported": {"Institution web address": 5985},
                "missing": {"Institution web address": 3},
                "applicable": {"Institution web address": 5988},
            },
        )
        assert (
            cli.main(["drift", str(tmp_path / "earlier.json"), str(tmp_path / "later.json")]) == 0
        )
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
            {
                "institutions": 1000,
                "reported": {"Admission rate": 900},
                "missing": {"Admission rate": 100},
            },
            {
                "institutions": 1000,
                "reported": {"Admission rate": 300},
                "missing": {"Admission rate": 700},
            },
        )
        assert (
            cli.main(["drift", str(tmp_path / "earlier.json"), str(tmp_path / "later.json")]) == 0
        )
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
            bundle.writestr(
                "ic2023.csv", "UNITID,ATHASSOC,SPORT1,SPORT2,SPORT3,SPORT4\n104717,1,1,1,1,1\n"
            )
        return path

    def _argv(self, cache: Path, characteristics: Path, out: Path) -> list[str]:
        return [
            "crosscheck",
            "--cache",
            str(cache),
            "--characteristics",
            str(characteristics),
            "--out",
            str(out),
        ]

    def test_grades_ipeds_alone_when_given_no_scorecard_capture(
        self, cache: Path, characteristics: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
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
        self, cache: Path, characteristics: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The live finding: the Scorecard files GCU as private nonprofit, IPEDS as for-profit."""
        source = tmp_path / "sc.json"
        source.write_text(
            json.dumps(
                [
                    {
                        "id": 104717,
                        "school.name": "Grand Canyon University",
                        "school.state": "AZ",
                        "school.ownership": 2,
                    }
                ]
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


def _page_record(page: int = 0) -> college_scorecard.PageRecord:
    return college_scorecard.PageRecord(
        page=page,
        url=f"{college_scorecard.BASE_URL}?api_key=REDACTED&per_page=100&page={page}",
        fetched_at="2026-08-21T09:00:00Z",
        status=200,
        bytes=120,
        sha256="ab" * 32,
        attempts=1,
        from_cache=False,
        ratelimit_limit=1000,
        ratelimit_remaining=999 - page,
    )


def _capture(
    records: list[dict[str, Any]] | None = None, **overrides: Any
) -> college_scorecard.Capture:
    rows = _RECORDS if records is None else records
    fields: dict[str, Any] = {
        "records": rows,
        "pages": [_page_record(0)],
        "total_stated": len(rows),
        "exhausted": True,
        "limit": None,
        "walked_at": "2026-08-21T09:00:00Z",
        "finished_at": "2026-08-21T09:00:31Z",
        "demo_key": False,
    }
    fields.update(overrides)
    return college_scorecard.Capture(**fields)


class TestFetch:
    """The one keyed, network-bound verb, and the file it leaves behind for every other one."""

    def test_writes_the_capture_and_its_provenance_summary(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        asked: dict[str, Any] = {}

        def fake_walk(**kwargs: Any) -> college_scorecard.Capture:
            asked.update(kwargs)
            return _capture()

        monkeypatch.setattr(college_scorecard, "walk", fake_walk)
        out = tmp_path / "census" / "capture.json"
        sidecar = tmp_path / "provenance" / "2026-08-21.json"
        assert (
            cli.main(
                [
                    "fetch",
                    "--out",
                    str(out),
                    "--provenance-out",
                    str(sidecar),
                    "--cache-dir",
                    str(tmp_path / "cache"),
                ]
            )
            == 0
        )
        assert asked == {"limit": None, "cache_dir": tmp_path / "cache"}

        records, provenance = college_scorecard.read_capture(json.loads(out.read_text()))
        assert records == _RECORDS
        assert provenance is not None and provenance["exhausted"] is True
        summary = json.loads(sidecar.read_text())
        assert summary["records"] == 2
        assert summary["calls"] == 1
        assert summary["ratelimit_remaining_min"] == 999
        assert "REDACTED" in summary["url_template"]

        printed = capsys.readouterr().out
        assert "captured 2 institutions" in printed
        assert "exhausted        yes; API stated 2" in printed
        assert "999 of 1,000 requests left" in printed
        assert "DATA_GOV_API_KEY" in printed

    def test_a_failed_walk_writes_nothing(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        def refuse(**kwargs: Any) -> college_scorecard.Capture:
            raise college_scorecard.RateLimited("page 4 still returning HTTP 429")

        monkeypatch.setattr(college_scorecard, "walk", refuse)
        out = tmp_path / "capture.json"
        assert cli.main(["fetch", "--out", str(out)]) == 1
        assert not out.exists()
        assert "fetch failed, nothing written" in capsys.readouterr().err

    def test_an_empty_walk_is_refused(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Zero institutions means the fetch failed, not that no college exists."""
        monkeypatch.setattr(
            college_scorecard, "walk", lambda **kw: _capture([], total_stated=0, exhausted=True)
        )
        out = tmp_path / "capture.json"
        assert cli.main(["fetch", "--out", str(out)]) == 1
        assert not out.exists()
        assert "refusing to write an empty capture" in capsys.readouterr().err

    def test_an_unreported_rate_limit_prints_as_words_not_as_zero(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        page = college_scorecard.PageRecord(
            **{**asdict(_page_record()), "ratelimit_limit": None, "ratelimit_remaining": None}
        )
        monkeypatch.setattr(
            college_scorecard,
            "walk",
            lambda **kw: _capture(pages=[page], demo_key=True, exhausted=False, limit=1),
        )
        assert cli.main(["fetch", "--out", str(tmp_path / "c.json"), "--limit", "1"]) == 0
        printed = capsys.readouterr().out
        assert "not reported by the API" in printed
        assert "exhausted        no" in printed
        assert "DEMO_KEY" in printed


class TestReplayingACapture:
    """A capture envelope replays as national on the strength of its own counts, or not at all."""

    def _envelope(self, tmp_path: Path, capture: college_scorecard.Capture) -> Path:
        path = tmp_path / "capture.json"
        college_scorecard.write_capture(capture, path)
        return path

    def test_an_exhaustive_capture_replays_as_national_and_names_its_day(
        self, tmp_path: Path
    ) -> None:
        source = self._envelope(tmp_path, _capture())
        out = tmp_path / "report.json"
        assert cli.main(["grade", "--source", str(source), "--out", str(out)]) == 0
        scope = json.loads(out.read_text())["scope"]
        assert scope["kind"] == "national"
        assert scope["coverage"] == 1.0
        assert "paged the API to exhaustion on 2026-08-21" in scope["note"]
        assert "no key" in scope["note"]

    def test_a_capture_truncated_after_it_was_written_replays_as_a_sample(
        self, tmp_path: Path
    ) -> None:
        """The provenance says two records arrived; the file holds one. Whichever was edited,
        the counts disagree, and a disagreement is graded as the smaller claim."""
        source = self._envelope(tmp_path, _capture())
        envelope = json.loads(source.read_text())
        envelope["records"] = envelope["records"][:1]
        source.write_text(json.dumps(envelope))
        out = tmp_path / "report.json"
        assert cli.main(["grade", "--source", str(source), "--out", str(out)]) == 0
        assert json.loads(out.read_text())["scope"]["kind"] == "sample"

    def test_a_walk_that_did_not_reach_the_total_replays_as_a_sample(self, tmp_path: Path) -> None:
        source = self._envelope(tmp_path, _capture(total_stated=6300, exhausted=False, limit=2))
        out = tmp_path / "report.json"
        assert cli.main(["grade", "--source", str(source), "--out", str(out)]) == 0
        assert json.loads(out.read_text())["scope"]["kind"] == "sample"

    def test_a_limited_replay_of_an_exhaustive_capture_is_a_sample(self, tmp_path: Path) -> None:
        """--limit is a deliberate sample and outranks what the file could have proved."""
        source = self._envelope(tmp_path, _capture())
        out = tmp_path / "report.json"
        assert cli.main(["grade", "--source", str(source), "--limit", "1", "--out", str(out)]) == 0
        report = json.loads(out.read_text())
        assert report["institutions"] == 1
        assert report["scope"]["kind"] == "sample"

    def test_an_envelope_with_no_records_list_is_refused(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps({"kind": college_scorecard.CAPTURE_KIND, "provenance": {}}))
        out = tmp_path / "report.json"
        assert cli.main(["grade", "--source", str(bad), "--out", str(out)]) == 1
        assert "not a JSON array" in capsys.readouterr().err
        assert not out.exists()


class TestCensusReport:
    """``census-report``: a graded national payload plus two captures, reduced to one artifact."""

    def _envelope(self, tmp_path: Path, name: str, capture: college_scorecard.Capture) -> Path:
        path = tmp_path / name
        college_scorecard.write_capture(capture, path)
        return path

    def _graded(self, tmp_path: Path, source: Path) -> Path:
        out = tmp_path / "graded.json"
        assert cli.main(["grade", "--source", str(source), "--out", str(out)]) == 0
        return out

    def test_reduces_a_national_capture_to_per_field_coverage_and_composition(
        self, tmp_path: Path
    ) -> None:
        source = self._envelope(tmp_path, "capture.json", _capture())
        sample = self._envelope(tmp_path, "sample.json", _capture())
        graded = self._graded(tmp_path, source)
        out = tmp_path / "census.json"
        assert (
            cli.main(
                [
                    "census-report",
                    "--report",
                    str(graded),
                    "--source",
                    str(source),
                    "--sample",
                    str(sample),
                    "--out",
                    str(out),
                ]
            )
            == 0
        )
        payload = json.loads(out.read_text())
        assert payload["scope"]["kind"] == "national"
        assert {f["label"] for f in payload["fields"]} == {
            "Median earnings 10 years after entry",
            "Completion rate, 150% of normal time",
            "Admission rate",
            "Median debt at completion",
            "In-state tuition",
            "Enrollment",
        }
        assert payload["composition"]["institutions"] == len(_RECORDS)
        assert payload["sample_composition"]["institutions"] == len(_RECORDS)

    def test_refuses_to_build_from_a_report_that_is_not_national(self, tmp_path: Path) -> None:
        """A sample graded through here would publish census-scale claims about 600 institutions
        under a name that says every institution the Scorecard publishes."""
        sample_source = tmp_path / "sample.json"
        sample_source.write_text(json.dumps(_RECORDS))
        graded = self._graded(tmp_path, sample_source)  # bare list: replays as a sample
        out = tmp_path / "census.json"
        rc = cli.main(
            [
                "census-report",
                "--report",
                str(graded),
                "--source",
                str(sample_source),
                "--sample",
                str(sample_source),
                "--out",
                str(out),
            ]
        )
        assert rc == 1
        assert not out.exists()

    def test_a_source_that_is_not_a_capture_is_refused(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        source = self._envelope(tmp_path, "capture.json", _capture())
        graded = self._graded(tmp_path, source)
        bad_source = tmp_path / "not-a-capture.json"
        bad_source.write_text(json.dumps({"nope": True}))
        out = tmp_path / "census.json"
        rc = cli.main(
            [
                "census-report",
                "--report",
                str(graded),
                "--source",
                str(bad_source),
                "--sample",
                str(source),
                "--out",
                str(out),
            ]
        )
        assert rc == 1
        assert str(bad_source) in capsys.readouterr().err
        assert not out.exists()

    def test_a_sample_that_is_not_a_capture_is_refused(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        source = self._envelope(tmp_path, "capture.json", _capture())
        graded = self._graded(tmp_path, source)
        bad_sample = tmp_path / "not-a-capture.json"
        bad_sample.write_text(json.dumps({"nope": True}))
        out = tmp_path / "census.json"
        rc = cli.main(
            [
                "census-report",
                "--report",
                str(graded),
                "--source",
                str(source),
                "--sample",
                str(bad_sample),
                "--out",
                str(out),
            ]
        )
        assert rc == 1
        assert str(bad_sample) in capsys.readouterr().err
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
        assert (
            cli.main(["snapshot", "--report", str(report), "--taken", "x", "--out", str(snap)]) == 0
        )
        assert json.loads(snap.read_text())["source"] == "College Scorecard"

    def test_drift_across_two_populations_exits_nonzero_rather_than_saying_nothing(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The failure mode is not a crash, it is the sentence "no change in per-field
        disclosure" printed about two corpora that share no field."""
        for name, source in (("a", "College Scorecard"), ("b", "IPEDS directory")):
            (tmp_path / f"{name}.json").write_text(
                json.dumps(
                    {
                        "taken": name,
                        "institutions": 10,
                        "reported": {"Enrollment": 5},
                        "missing": {"Enrollment": 5},
                        "applicable": {"Enrollment": 10},
                        "source": source,
                    }
                )
            )
        assert cli.main(["drift", str(tmp_path / "a.json"), str(tmp_path / "b.json")]) == 1
        assert "different populations" in capsys.readouterr().err


class TestTheRegistryVerbs:
    """``registry-fetch`` and ``registry-join``: a capture, and the measurement it supports.

    The join reads three committed inputs and writes one artifact. Every failure path here exits
    non-zero with a sentence rather than writing a file, because a join measurement written from
    an input that could not be read is a number over a denominator nobody saw.
    """

    def _capture(self, tmp_path: Path, *, exhausted: bool = True) -> Path:
        from disclosed.sources import credential_registry

        capture = credential_registry.Capture(
            organizations=[
                credential_registry.Organization(
                    ctid="ce-1",
                    name="Example College",
                    ipeds_id="201885",
                    ope_id="00308800",
                    org_types=("orgType:Postsecondary",),
                    state="Ohio",
                    homepage_host="example.edu",
                ),
                credential_registry.Organization(
                    ctid="ce-2",
                    name="Example Training",
                    ipeds_id=None,
                    ope_id=None,
                    org_types=("orgType:TrainingProvider",),
                    state="Ohio",
                    homepage_host="training.test",
                ),
            ],
            pages=[],
            total_stated=2,
            exhausted=exhausted,
            limit=None,
            walked_at="2026-08-27T00:00:00Z",
            finished_at="2026-08-27T00:00:10Z",
            duplicates=0,
            unreduced=0,
        )
        path = tmp_path / "registry.json"
        credential_registry.write_capture(capture, path)
        return path

    def _ipeds_archive(self, tmp_path: Path) -> Path:
        import io
        import zipfile

        rows = "UNITID,INSTNM,STABBR,CONTROL,WEBADDR\n201885,Example College,OH,1,example.edu\n"
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as bundle:
            bundle.writestr("hd2023.csv", rows)
        path = tmp_path / "HD.zip"
        path.write_bytes(buffer.getvalue())
        return path

    def _scorecard(self, tmp_path: Path) -> Path:
        path = tmp_path / "scorecard.json"
        college_scorecard.write_capture(_capture(), path)
        return path

    def test_a_join_reads_three_committed_inputs_and_writes_one_measurement(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        out = tmp_path / "join.json"
        assert (
            cli.main(
                [
                    "registry-join",
                    "--capture",
                    str(self._capture(tmp_path)),
                    "--cache",
                    str(self._ipeds_archive(tmp_path)),
                    "--source",
                    str(self._scorecard(tmp_path)),
                    "--out",
                    str(out),
                ]
            )
            == 0
        )
        payload = json.loads(out.read_text())
        assert payload["kind"] == "credential-registry-join"
        assert payload["registry"]["organizations"] == 2
        assert payload["registry"]["postsecondary"] == 1
        over_all = payload["identifier_join"]["over_all_organizations"]
        assert over_all["matched_ipeds_directory"] == 1
        assert "registry join over 2 organizations" in capsys.readouterr().out

    def test_a_partial_walk_is_refused_rather_than_measured(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        out = tmp_path / "join.json"
        assert (
            cli.main(
                [
                    "registry-join",
                    "--capture",
                    str(self._capture(tmp_path, exhausted=False)),
                    "--cache",
                    str(self._ipeds_archive(tmp_path)),
                    "--source",
                    str(self._scorecard(tmp_path)),
                    "--out",
                    str(out),
                ]
            )
            == 1
        )
        assert not out.exists()
        assert "did not reach the registry's own total" in capsys.readouterr().err

    def test_a_capture_that_is_not_a_capture_is_refused(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        bare = tmp_path / "bare.json"
        bare.write_text(json.dumps([{"ctid": "ce-1"}]))
        assert (
            cli.main(
                [
                    "registry-join",
                    "--capture",
                    str(bare),
                    "--cache",
                    str(self._ipeds_archive(tmp_path)),
                    "--source",
                    str(self._scorecard(tmp_path)),
                    "--out",
                    str(tmp_path / "join.json"),
                ]
            )
            == 1
        )
        assert "carries its own provenance" in capsys.readouterr().err

    def test_an_unreadable_ipeds_archive_is_refused(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        broken = tmp_path / "broken.zip"
        broken.write_bytes(b"not a zip")
        assert (
            cli.main(
                [
                    "registry-join",
                    "--capture",
                    str(self._capture(tmp_path)),
                    "--cache",
                    str(broken),
                    "--source",
                    str(self._scorecard(tmp_path)),
                    "--out",
                    str(tmp_path / "join.json"),
                ]
            )
            == 1
        )
        assert "IPEDS directory unreadable" in capsys.readouterr().err

    def test_a_scorecard_source_of_an_unknown_shape_is_refused(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A bare array is a legitimate Scorecard source shape (``data/sample.json`` is one), so
        the refusal has to be triggered by a shape that is neither that nor a capture envelope."""
        odd = tmp_path / "odd-scorecard.json"
        odd.write_text(json.dumps({"institutions": 6273}))
        assert (
            cli.main(
                [
                    "registry-join",
                    "--capture",
                    str(self._capture(tmp_path)),
                    "--cache",
                    str(self._ipeds_archive(tmp_path)),
                    "--source",
                    str(odd),
                    "--out",
                    str(tmp_path / "join.json"),
                ]
            )
            == 1
        )
        assert "not a JSON array of records" in capsys.readouterr().err

    def test_a_fetch_writes_a_capture_and_a_failed_one_writes_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from disclosed.sources import credential_registry

        out = tmp_path / "fetched.json"

        def boom(**kwargs: Any) -> None:
            raise credential_registry.RegistryError("the registry said no")

        monkeypatch.setattr(cli.credential_registry, "walk", boom)
        assert cli.main(["registry-fetch", "--out", str(out)]) == 1
        assert not out.exists()
        assert "registry fetch failed" in capsys.readouterr().err

        source = self._capture(tmp_path)
        organizations, _ = credential_registry.read_capture(json.loads(source.read_text()))
        monkeypatch.setattr(
            cli.credential_registry,
            "walk",
            lambda **kwargs: credential_registry.Capture(
                organizations=organizations,
                pages=[],
                total_stated=2,
                exhausted=True,
                limit=None,
                walked_at="2026-08-27T00:00:00Z",
                finished_at="2026-08-27T00:00:10Z",
                duplicates=0,
                unreduced=0,
            ),
        )
        assert cli.main(["registry-fetch", "--out", str(out)]) == 0
        assert "captured 2 organizations" in capsys.readouterr().out

    def test_an_empty_walk_writes_no_capture(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from disclosed.sources import credential_registry

        out = tmp_path / "empty.json"
        monkeypatch.setattr(
            cli.credential_registry,
            "walk",
            lambda **kwargs: credential_registry.Capture(
                organizations=[],
                pages=[],
                total_stated=None,
                exhausted=False,
                limit=None,
                walked_at="2026-08-27T00:00:00Z",
                finished_at="2026-08-27T00:00:10Z",
                duplicates=0,
                unreduced=0,
            ),
        )
        assert cli.main(["registry-fetch", "--out", str(out)]) == 1
        assert not out.exists()
        assert "refusing to write an empty capture" in capsys.readouterr().err


class TestTheRegistryPropertyVerbs:
    """``registry-properties`` and ``registry-property-report``.

    The first walks and captures which CTDL property names each organization publishes; the
    second reduces that to the rates ``docs/adr/0009`` is argued from. Both refuse rather than
    write: a property rate over a walk that did not reach the end is a rate over the front of an
    offset-paginated set, which is the mistake ADR 0007 exists because this project already made.
    """

    def _capture(self, *, exhausted: bool = True) -> Any:
        from disclosed.sources import credential_registry

        return credential_registry.Capture(
            organizations=[
                credential_registry.Organization(
                    ctid="ce-1",
                    name="Example College",
                    ipeds_id="201885",
                    ope_id=None,
                    org_types=("orgType:Postsecondary",),
                    state="Ohio",
                    homepage_host="example.edu",
                    properties=("ceterms:ctid", "ceterms:ipedsID", "ceterms:name"),
                    identifier_type_names=("IPEDS NCES Data Year",),
                ),
                credential_registry.Organization(
                    ctid="ce-2",
                    name="Example Training",
                    ipeds_id=None,
                    ope_id=None,
                    org_types=("orgType:TrainingProvider",),
                    state="Ohio",
                    homepage_host="training.test",
                    properties=("ceterms:ctid", "ceterms:name"),
                ),
            ],
            pages=[],
            total_stated=2,
            exhausted=exhausted,
            limit=None,
            walked_at="2026-08-27T00:00:00Z",
            finished_at="2026-08-27T00:00:10Z",
            duplicates=0,
            unreduced=0,
        )

    def test_a_census_is_written_and_then_reduced_to_a_report(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        census = tmp_path / "nested" / "properties.json"
        monkeypatch.setattr(cli.credential_registry, "walk", lambda **kwargs: self._capture())
        assert cli.main(["registry-properties", "--out", str(census)]) == 0
        assert "property census over 2 organizations" in capsys.readouterr().out

        report = tmp_path / "report.json"
        assert (
            cli.main(["registry-property-report", "--census", str(census), "--out", str(report)])
            == 0
        )
        payload = json.loads(report.read_text())
        assert payload["kind"] == "credential-registry-property-report"
        assert payload["publishing_an_ipeds_id"] == 1
        # Ordered by how many organizations publish each, then by name, the same order the
        # properties table itself is in: ctid and name are on both organizations, ipedsID on one.
        assert payload["universal_over_joined"] == [
            "ceterms:ctid",
            "ceterms:name",
            "ceterms:ipedsID",
        ]
        assert "largest joined set" in capsys.readouterr().out

    def test_a_walk_short_of_the_registrys_total_writes_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        out = tmp_path / "properties.json"
        monkeypatch.setattr(
            cli.credential_registry, "walk", lambda **kwargs: self._capture(exhausted=False)
        )
        assert cli.main(["registry-properties", "--out", str(out)]) == 1
        assert not out.exists()
        assert "property census failed" in capsys.readouterr().err

    def test_a_failed_walk_writes_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from disclosed.sources import credential_registry

        def boom(**kwargs: Any) -> None:
            raise credential_registry.RegistryError("the registry said no")

        out = tmp_path / "properties.json"
        monkeypatch.setattr(cli.credential_registry, "walk", boom)
        assert cli.main(["registry-properties", "--out", str(out)]) == 1
        assert not out.exists()

    def test_reducing_something_that_is_not_a_census_writes_nothing(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        census = tmp_path / "wrong.json"
        census.write_text(json.dumps({"kind": "credential-registry-capture"}), encoding="utf-8")
        out = tmp_path / "report.json"
        assert (
            cli.main(["registry-property-report", "--census", str(census), "--out", str(out)]) == 1
        )
        assert not out.exists()
        assert "not a property census" in capsys.readouterr().err
