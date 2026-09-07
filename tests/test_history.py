"""The disclosure-history pages, and the three absences a rate table would otherwise print as 0%.

The drift measurement is the most distinctive thing this project publishes and until #76 it
existed only in a job summary on a green run and in a terminal. Putting it on a page is where it
becomes readable by somebody who will never run the command -- and it is also where reading an
absence as a number stops being a private mistake, so most of what is asserted here is what must
never appear in a cell.

Every figure on the page is checked against :func:`disclosed.drift.compare` and
:meth:`disclosed.drift.Snapshot.rate` recomputed here, rather than against a number written into
this file. A fixture that restated the expected percentages would agree with a broken renderer
the moment somebody updated both together.
"""

from __future__ import annotations

import html.parser
import json
import re
from pathlib import Path
from typing import Any

import pytest

from disclosed import cli, history, site
from disclosed.drift import Snapshot, compare
from disclosed.fields import FIELDS

_ROOT = Path(__file__).resolve().parent.parent
_SNAPSHOTS = _ROOT / "data" / "snapshots"

_REPORT: dict[str, Any] = {
    "scope": {
        "kind": "sample",
        "source": "College Scorecard",
        "institutions": 1,
        "states": 1,
        "universe": 6300,
        "coverage": 1 / 6300,
        "note": "The first records the API returned.",
    },
    "institutions": 1,
    "ungradeable": 0,
    "overall": {
        "label": "all institutions",
        "graded": 1,
        "ungradeable": 0,
        "mean_score": 1.0,
        "worst_fields": [],
    },
    "by_state": [
        {"label": "CA", "graded": 1, "ungradeable": 0, "mean_score": 1.0, "worst_fields": []}
    ],
    "implausible": [],
    "grades": [
        {
            "unit_id": "1",
            "name": "Complete College",
            "state": "CA",
            "score": 1.0,
            "letter": "A",
            "fields": {f.label: "reported" for f in FIELDS},
        }
    ],
}


def _snapshot(
    taken: str,
    *,
    source: str = "College Scorecard",
    reported: dict[str, int] | None = None,
    applicable: dict[str, int] | None = None,
    institutions: int = 100,
) -> Snapshot:
    reported = {"Admission rate": 50} if reported is None else reported
    applicable = {label: 100 for label in reported} if applicable is None else applicable
    return Snapshot(
        taken=taken,
        institutions=institutions,
        reported=reported,
        missing={label: applicable.get(label, 0) - count for label, count in reported.items()},
        applicable=applicable,
        source=source,
    )


def _write(root: Path, folder: str, snapshots: list[Snapshot]) -> Path:
    directory = root / folder
    directory.mkdir(parents=True, exist_ok=True)
    for snap in snapshots:
        payload = {
            "taken": snap.taken,
            "institutions": snap.institutions,
            "reported": snap.reported,
            "missing": snap.missing,
            "applicable": snap.applicable,
            "source": snap.source,
        }
        (directory / f"{snap.taken}.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
        )
    return directory


class _Cells(html.parser.HTMLParser):
    """The text of every cell of every table, row by row, with the table's caption.

    Parsed rather than regexed because the assertions below are about which cell holds which
    number, and a regex over ``<td>`` would happily read a row of one table as a row of another.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[dict[str, Any]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._caption: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self.tables.append({"caption": "", "rows": []})
        elif tag == "caption":
            self._caption = []
        elif tag == "tr":
            self._row = []
        elif tag in ("td", "th"):
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "caption" and self._caption is not None and self.tables:
            self.tables[-1]["caption"] = "".join(self._caption).strip()
            self._caption = None
        elif tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append("".join(self._cell).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None and self.tables:
            self.tables[-1]["rows"].append(self._row)
            self._row = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)
        elif self._caption is not None:
            self._caption.append(data)


def _tables(body: str) -> list[dict[str, Any]]:
    parser = _Cells()
    parser.feed(body)
    return parser.tables


class TestLoadingTheCommittedSeries:
    """What ``data/snapshots`` actually holds, read the way the site reads it."""

    def test_the_committed_series_group_by_source_and_not_by_directory(self) -> None:
        series = history.load(_SNAPSHOTS)

        assert [s.source for s in series] == ["College Scorecard", "IPEDS directory"]
        for one in series:
            assert {snap.source for snap in one.snapshots} == {one.source}

    def test_every_committed_snapshot_is_in_exactly_one_series(self) -> None:
        series = history.load(_SNAPSHOTS)
        loaded = sum(len(s.snapshots) for s in series)
        files = sorted(_SNAPSHOTS.glob("*/*.json"))

        assert loaded == len(files) > 0

    def test_the_provenance_sidecars_are_not_read_as_snapshots(self) -> None:
        """The glob is two levels deep on purpose.

        The daily workflow writes a provenance sidecar per Scorecard run into
        ``scorecard/provenance/``. A recursive walk would load each of those as though it were a
        run, which is this project's own defect class exactly: a file *about* a run read as the
        run itself. There are as many sidecars as snapshots, so the failure would double the
        series rather than break it -- the worst shape a bug can have.
        """
        sidecars = sorted((_SNAPSHOTS / "scorecard" / "provenance").glob("*.json"))
        assert sidecars, "no provenance sidecars committed; this test no longer proves anything"

        series = {s.source: s for s in history.load(_SNAPSHOTS)}
        taken = {snap.taken for snap in series["College Scorecard"].snapshots}

        assert len(taken) == len(sorted((_SNAPSHOTS / "scorecard").glob("*.json")))

    def test_the_runs_of_a_series_are_ordered_oldest_first(self) -> None:
        for one in history.load(_SNAPSHOTS):
            taken = [snap.taken for snap in one.snapshots]
            assert taken == sorted(taken)

    def test_two_runs_that_claim_the_same_date_are_refused(self, tmp_path: Path) -> None:
        """There is no tiebreak that is not a guess about which file is the real run."""
        _write(tmp_path, "a", [_snapshot("2026-01-01")])
        _write(tmp_path, "b", [_snapshot("2026-01-01", reported={"Admission rate": 99})])

        with pytest.raises(history.HistoryError, match="2026-01-01"):
            history.load(tmp_path)

    def test_a_file_carrying_a_key_this_build_does_not_know_is_refused(
        self, tmp_path: Path
    ) -> None:
        directory = _write(tmp_path, "a", [_snapshot("2026-01-01")])
        path = directory / "2026-01-01.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["withheld"] = 3
        path.write_text(json.dumps(payload), encoding="utf-8")

        with pytest.raises(history.HistoryError, match="withheld"):
            history.load(tmp_path)

    def test_a_file_that_is_not_an_object_is_refused(self, tmp_path: Path) -> None:
        (tmp_path / "a").mkdir()
        (tmp_path / "a" / "one.json").write_text("[]", encoding="utf-8")

        with pytest.raises(history.HistoryError):
            history.load(tmp_path)

    def test_a_directory_with_no_snapshots_yields_no_series(self, tmp_path: Path) -> None:
        assert history.load(tmp_path) == ()

    def test_an_unstated_source_is_its_own_series_and_sorts_last(self, tmp_path: Path) -> None:
        """Empty is never treated as matching, which is ``drift.compare``'s rule too."""
        _write(tmp_path, "named", [_snapshot("2026-01-01"), _snapshot("2026-01-02")])
        _write(tmp_path, "old", [_snapshot("2020-01-01", source="")])

        series = history.load(tmp_path)

        assert [s.source for s in series] == ["College Scorecard", ""]
        assert series[-1].stated is False


class TestTheSeriesReadsItsOwnRuns:
    def test_the_field_set_is_the_union_and_not_the_intersection(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "a",
            [
                _snapshot("2026-01-01", reported={"Admission rate": 50}),
                _snapshot("2026-01-02", reported={"Admission rate": 50, "Enrollment": 90}),
            ],
        )

        (one,) = history.load(tmp_path)

        assert one.labels == ("Admission rate", "Enrollment")
        assert one.graded("Enrollment", one.snapshots[0]) is False
        assert one.graded("Enrollment", one.snapshots[1]) is True

    def test_one_run_is_not_a_comparison(self, tmp_path: Path) -> None:
        _write(tmp_path, "a", [_snapshot("2026-01-01")])

        (one,) = history.load(tmp_path)

        assert one.movement() == ()

    def test_the_movement_is_the_first_run_beside_the_last(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "a",
            [
                _snapshot("2026-01-01", reported={"Admission rate": 50}),
                _snapshot("2026-01-02", reported={"Admission rate": 90}),
                _snapshot("2026-01-03", reported={"Admission rate": 70}),
            ],
        )

        (one,) = history.load(tmp_path)

        assert one.movement() == compare(one.snapshots[0], one.snapshots[-1])


class TestEveryRateOnThePageComesFromTheSnapshots:
    """The rate table, recomputed rather than restated."""

    @pytest.mark.parametrize("index", [0, 1])
    def test_every_cell_equals_the_snapshot_it_is_a_cell_about(self, index: int) -> None:
        series = history.load(_SNAPSHOTS)[index]
        page = site.history_page(series)
        table = _tables(page.body)[0]

        header, *rows = table["rows"]
        assert header[1:] == [snap.taken for snap in series.snapshots]
        assert [row[0] for row in rows] == list(series.labels)
        for row, label in zip(rows, series.labels, strict=True):
            for cell, snap in zip(row[1:], series.snapshots, strict=True):
                rate = snap.rate(label)
                assert cell == f"{rate:.1%}"
                assert rate is not None

    @pytest.mark.parametrize("index", [0, 1])
    def test_no_cell_of_the_committed_series_reads_as_zero(self, index: int) -> None:
        """The one number a reader would misread, asserted absent rather than assumed absent."""
        series = history.load(_SNAPSHOTS)[index]
        rows = _tables(site.history_page(series).body)[0]["rows"][1:]

        assert "0.0%" not in {cell for row in rows for cell in row[1:]}

    def test_a_field_a_run_never_graded_says_so_rather_than_reading_as_zero(
        self, tmp_path: Path
    ) -> None:
        _write(
            tmp_path,
            "a",
            [
                _snapshot("2026-01-01", reported={"Admission rate": 50}),
                _snapshot("2026-01-02", reported={"Admission rate": 50, "Enrollment": 90}),
            ],
        )
        (one,) = history.load(tmp_path)

        rows = _tables(site.history_page(one).body)[0]["rows"][1:]
        enrollment = next(row for row in rows if row[0] == "Enrollment")

        assert enrollment[1] == "not graded in this run"
        assert enrollment[2] == "90.0%"

    def test_a_field_that_reached_nobody_says_the_rate_is_unmeasured(self, tmp_path: Path) -> None:
        """``Snapshot.rate`` returns ``None`` and never ``0.0``; the cell has to keep that apart.

        A field with an empty denominator has no reporting rate. Printing 0% would say every
        institution it touched had failed to report it, which is the opposite claim and the one
        this whole project exists to stop being made by accident.
        """
        _write(
            tmp_path,
            "a",
            [
                _snapshot(
                    "2026-01-01",
                    reported={"Admission rate": 0},
                    applicable={"Admission rate": 0},
                ),
                _snapshot("2026-01-02", reported={"Admission rate": 50}),
            ],
        )
        (one,) = history.load(tmp_path)
        assert one.snapshots[0].rate("Admission rate") is None

        rows = _tables(site.history_page(one).body)[0]["rows"][1:]

        assert rows[0][1] == "rate unmeasured"
        assert "0.0%" not in rows[0]


class TestTheMovementTableIsDriftCompare:
    def test_every_row_restates_a_field_drift_and_invents_nothing(self) -> None:
        series = {s.source: s for s in history.load(_SNAPSHOTS)}["IPEDS directory"]
        drifts = series.movement()
        assert drifts, "the committed IPEDS series shows no movement; this test proves nothing"

        rows = _tables(site.history_page(series).body)[1]["rows"][1:]

        assert [row[0] for row in rows] == [d.field_label for d in drifts]
        for row, drift in zip(rows, drifts, strict=True):
            assert drift.rate_change is not None
            assert row[1] == drift.direction.capitalize()
            assert row[2] == f"{drift.rate_change * 100:+.2f} percentage points"
            moved = drift.applicability_moved
            assert row[3] == f"{abs(moved)} {'more' if moved > 0 else 'fewer'}"
            assert row[4] == ("Yes" if drift.is_systemic else "No")

    def test_the_one_systemic_movement_in_three_collection_years_is_the_one_on_the_page(
        self,
    ) -> None:
        """The finding the drift module's docstring argues for, asserted on the page that shows it.

        Between 2021 and 2023 the athletics disclosure is the only movement over the threshold,
        and the admissions row is the one that gained while reaching 131 fewer institutions. If
        the page ever reads the direction off the count, this is the row that says so.
        """
        series = {s.source: s for s in history.load(_SNAPSHOTS)}["IPEDS directory"]
        rows = {row[0]: row for row in _tables(site.history_page(series).body)[1]["rows"][1:]}

        assert rows["Equity in athletics disclosure"][4] == "Yes"
        assert [label for label, row in rows.items() if row[4] == "Yes"] == [
            "Equity in athletics disclosure"
        ]
        assert rows["Admissions information"][1] == "Gained"
        assert rows["Admissions information"][3].endswith("fewer")

    def test_a_movement_that_could_not_be_measured_is_not_given_a_direction(
        self, tmp_path: Path
    ) -> None:
        """``FieldDrift.direction`` falls back to the count. A published page must not.

        The whole reason the module divides by the applicable population is that a count and a
        rate can point in opposite directions. A page printing "Lost" off a count would commit
        the mistake the methodology page describes, one link away from the description.
        """
        _write(
            tmp_path,
            "a",
            [
                _snapshot(
                    "2026-01-01",
                    reported={"Admission rate": 10},
                    applicable={"Admission rate": 0},
                ),
                _snapshot("2026-01-02", reported={"Admission rate": 50}),
            ],
        )
        (one,) = history.load(tmp_path)
        (drift,) = one.movement()
        assert drift.measured is False
        assert drift.direction in ("gained", "lost")

        row = _tables(site.history_page(one).body)[1]["rows"][1]

        assert row[1] == "Could not be measured"
        assert row[2] == "rate unmeasured"
        assert row[4] == "Not measured, so not systemic"

    def test_a_series_that_did_not_move_says_so_rather_than_rendering_an_empty_table(self) -> None:
        series = {s.source: s for s in history.load(_SNAPSHOTS)}["College Scorecard"]
        assert series.movement() == ()

        body = site.history_page(series).body

        assert "Nothing moved" in body
        assert len(_tables(body)) == 1

    def test_a_denominator_that_did_not_move_is_words_and_not_zero(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "a",
            [
                _snapshot("2026-01-01", reported={"Admission rate": 50}),
                _snapshot("2026-01-02", reported={"Admission rate": 60}),
            ],
        )
        (one,) = history.load(tmp_path)

        row = _tables(site.history_page(one).body)[1]["rows"][1]

        assert row[3] == "the same institutions"


class TestNoPageMixesSources:
    def test_each_page_names_one_source_and_only_its_own_runs(self) -> None:
        series = history.load(_SNAPSHOTS)
        assert len(series) > 1, "one series cannot demonstrate that two are kept apart"

        for one in series:
            other_dates = {snap.taken for two in series if two is not one for snap in two.snapshots}
            header = _tables(site.history_page(one).body)[0]["rows"][0]
            assert set(header[1:]) == {snap.taken for snap in one.snapshots}
            assert not set(header[1:]) & other_dates

    def test_a_series_with_no_source_uses_the_pre_scope_wording_and_names_nobody(
        self, tmp_path: Path
    ) -> None:
        """A snapshot written before scope existed says the source was not recorded.

        Guessing at the nearest named collection would be exactly the move this project objects
        to everywhere else: an absence rendered as a fact, and a plausible one.
        """
        _write(tmp_path, "old", [_snapshot("2020-01-01", source="")])
        (one,) = history.load(tmp_path)

        page = site.history_page(one)

        assert one.stated is False
        assert page.path == "history/source-not-stated"
        assert "did not state their source" in page.body
        assert "did not record which publisher" in page.description
        assert "College Scorecard" not in page.body
        assert "IPEDS" not in page.body


class TestTheBuildWritesThePagesAndNothingElseChanges:
    def test_without_snapshots_the_build_is_byte_for_byte_what_it_was(self, tmp_path: Path) -> None:
        """The same argument ``--receipts-from`` makes, asserted the same way.

        A feature that silently rewrites every other page is not an addition; it is a change to
        the whole site wearing an addition's flag.
        """
        first, second = tmp_path / "a", tmp_path / "b"
        site.build(_REPORT, first, generated="2026-09-07")
        site.build(_REPORT, second, generated="2026-09-07", histories=())

        for page in sorted(first.rglob("index.html")):
            assert page.read_bytes() == (second / page.relative_to(first)).read_bytes()

    def test_with_snapshots_one_page_is_written_per_series_and_linked_from_the_home_page(
        self, tmp_path: Path
    ) -> None:
        series = history.load(_SNAPSHOTS)

        pages = site.build(_REPORT, tmp_path, generated="2026-09-07", histories=series)

        written = {page.path for page in pages}
        assert {site.history_path(one) for one in series} <= written
        home = (tmp_path / "index.html").read_text(encoding="utf-8")
        for one in series:
            assert f'href="{site.history_path(one)}/"' in home
            assert (tmp_path / site.history_path(one) / "index.html").exists()

    def test_the_home_page_says_nothing_about_drift_when_it_was_shown_no_series(
        self, tmp_path: Path
    ) -> None:
        """An absent history renders as absence, not as a heading over an empty list."""
        site.build(_REPORT, tmp_path, generated="2026-09-07")

        home = (tmp_path / "index.html").read_text(encoding="utf-8")

        assert "Disclosure over time" not in home
        assert "history/" not in home

    def test_every_history_page_is_in_the_sitemap(self, tmp_path: Path) -> None:
        series = history.load(_SNAPSHOTS)

        site.build(_REPORT, tmp_path, generated="2026-09-07", histories=series)

        sitemap = (tmp_path / "sitemap.xml").read_text(encoding="utf-8")
        for one in series:
            assert f"/{site.history_path(one)}/" in sitemap


class TestTheCommandRefusesWhatItCannotRender:
    def test_a_snapshot_directory_that_holds_nothing_is_an_error_and_not_a_site(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """``--snapshots-from`` was given, so finding nothing is a broken invocation.

        Rendering a site with no history page would be indistinguishable from not passing the
        flag at all, and the operator who passed it would never learn that the path was wrong.
        """
        report = tmp_path / "report.json"
        report.write_text(json.dumps(_REPORT), encoding="utf-8")
        empty = tmp_path / "empty"
        empty.mkdir()

        code = cli.main(
            [
                "site",
                "--report",
                str(report),
                "--snapshots-from",
                str(empty),
                "--out",
                str(tmp_path / "site"),
                "--generated",
                "2026-09-07",
            ]
        )

        assert code == 1
        assert "holds no snapshots" in capsys.readouterr().err

    def test_a_series_it_cannot_read_is_refused_rather_than_part_rendered(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        report = tmp_path / "report.json"
        report.write_text(json.dumps(_REPORT), encoding="utf-8")
        snapshots = tmp_path / "snapshots"
        _write(snapshots, "a", [_snapshot("2026-01-01")])
        _write(snapshots, "b", [_snapshot("2026-01-01", reported={"Admission rate": 99})])

        code = cli.main(
            [
                "site",
                "--report",
                str(report),
                "--snapshots-from",
                str(snapshots),
                "--out",
                str(tmp_path / "site"),
                "--generated",
                "2026-09-07",
            ]
        )

        assert code == 1
        assert "refusing to build" in capsys.readouterr().err
        assert not (tmp_path / "site").exists()

    def test_the_command_writes_the_pages_the_published_site_carries(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        report = tmp_path / "report.json"
        report.write_text(json.dumps(_REPORT), encoding="utf-8")
        out = tmp_path / "site"

        code = cli.main(
            [
                "site",
                "--report",
                str(report),
                "--snapshots-from",
                str(_SNAPSHOTS),
                "--out",
                str(out),
                "--generated",
                "2026-09-07",
            ]
        )

        assert code == 0
        assert "built" in capsys.readouterr().out
        assert (out / "history" / "college-scorecard" / "index.html").exists()
        assert (out / "history" / "ipeds-directory" / "index.html").exists()


class TestThePageIsDeterministic:
    def test_two_renders_of_the_same_series_are_byte_identical(self) -> None:
        series = history.load(_SNAPSHOTS)[0]

        assert site.history_page(series).body == site.history_page(series).body

    def test_the_page_carries_no_date_it_did_not_read_from_a_snapshot(self) -> None:
        """No clock. Every date on the page is a ``taken`` the series carries."""
        series = history.load(_SNAPSHOTS)[0]
        page = site.history_page(series)
        taken = {snap.taken for snap in series.snapshots}

        found = set(re.findall(r"\b\d{4}-\d{2}-\d{2}\b", page.body))

        assert found == taken
