"""``data/scorecard-census.json``, replayed from the committed capture that produced it.

The same argument as ``tests/test_replay.py``, for the corpus #17 was opened about. Every
Scorecard figure this project published came from 600 institutions the API returned first,
because nobody had run the walk to exhaustion and committed what it found. PR #26 built the
adapter that proves exhaustion from its own counts; this is the reduction built on top of it, and
these are the tests that make the reduction a claim a reader can check rather than one only its
author can regenerate: no network, no key, everything read from committed bytes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from disclosed import cli
from disclosed.fields import FIELDS
from disclosed.scope import NATIONAL

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CAPTURE = DATA / "census" / "scorecard.json"
SAMPLE = DATA / "sample.json"
ARTIFACT = DATA / "scorecard-census.json"


class TestTheCaptureIsThere:
    def test_the_committed_capture_is_a_real_file_and_not_empty(self) -> None:
        assert CAPTURE.is_file(), (
            f"{CAPTURE} is missing. The census artifact cannot be checked without the capture it "
            "was reduced from, and an artifact nobody can regenerate is not evidence."
        )
        assert CAPTURE.stat().st_size > 0

    def test_the_capture_proves_its_own_exhaustion(self) -> None:
        """The premise every other test in this file relies on. If this is false, every 'national'
        claim on the census page and in the README is a mislabeled sample."""
        raw = json.loads(CAPTURE.read_text(encoding="utf-8"))
        provenance = raw["provenance"]
        assert provenance["exhausted"] is True
        assert provenance["total_stated"] == provenance["records"] == len(raw["records"])

    def test_the_capture_carries_no_api_key(self) -> None:
        text = CAPTURE.read_text(encoding="utf-8")
        assert "api_key=REDACTED" in text
        # Every occurrence of the parameter is the redacted literal; there is no second, unredacted
        # spelling sitting beside it.
        assert text.count("api_key=") == text.count("api_key=REDACTED")


@pytest.fixture(scope="module")
def graded(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The census, regraded from the committed capture with no key in the environment."""
    out = tmp_path_factory.mktemp("census") / "graded.json"
    assert cli.main(["grade", "--source", str(CAPTURE), "--out", str(out)]) == 0
    return out


@pytest.fixture(scope="module")
def census_artifact() -> dict[str, Any]:
    return json.loads(ARTIFACT.read_text(encoding="utf-8"))


class TestTheCommittedCensusArtifact:
    def test_it_replays_byte_for_byte_from_the_committed_capture(
        self, graded: Path, tmp_path: Path
    ) -> None:
        """The one test that makes ``data/scorecard-census.json`` a claim a reader can check.

        Compared as bytes, the same discipline ``test_replay.py`` holds ``data/national.json`` to:
        ``census-report`` writes with ``sort_keys=True`` and a fixed indent, so the bytes are
        deterministic and a reformat that changes them is itself worth noticing.
        """
        out = tmp_path / "scorecard-census.json"
        assert (
            cli.main(
                [
                    "census-report",
                    "--report",
                    str(graded),
                    "--source",
                    str(CAPTURE),
                    "--sample",
                    str(SAMPLE),
                    "--out",
                    str(out),
                ]
            )
            == 0
        )
        assert out.read_bytes() == ARTIFACT.read_bytes(), (
            "data/scorecard-census.json does not match what the committed capture produces. "
            "Either a rule moved and the artifact was not regenerated, or it was edited by hand. "
            "Run `make census-replay` to see the difference."
        )


class TestTheCensusArtifactAddsUp:
    """Internal arithmetic, checked independently of what produced the file. Mirrors
    ``TestTheNationalArtifactAddsUp`` in ``tests/test_replay.py``."""

    def test_the_artifact_says_it_is_national(self, census_artifact: dict[str, Any]) -> None:
        scope = census_artifact["scope"]
        assert scope["kind"] == NATIONAL
        assert scope["coverage"] == 1.0
        assert scope["institutions"] == scope["universe"]
        assert scope["source"] == "College Scorecard"

    def test_every_field_names_a_field_this_project_actually_grades(
        self, census_artifact: dict[str, Any]
    ) -> None:
        known = {f.label: f for f in FIELDS}
        published = [f["label"] for f in census_artifact["fields"]]
        assert published == list(known)
        for row in census_artifact["fields"]:
            field = known[row["label"]]
            assert row["key"] == field.key

    def test_the_denominator_is_the_sum_of_what_it_is_made_of(
        self, census_artifact: dict[str, Any]
    ) -> None:
        for row in census_artifact["fields"]:
            assert row["applicable"] == row["reported"] + row["missing"] + row["implausible"], (
                f"{row['label']}: applicable is not reported + missing + implausible"
            )

    def test_a_share_is_the_division_it_claims_to_be_or_it_is_not_a_number(
        self, census_artifact: dict[str, Any]
    ) -> None:
        for row in census_artifact["fields"]:
            if row["applicable"] == 0:
                assert row["share_reported"] is None
                continue
            assert row["share_reported"] == pytest.approx(row["reported"] / row["applicable"])

    def test_no_field_here_has_a_named_gap_list(self, census_artifact: dict[str, Any]) -> None:
        """None of the six Scorecard fields carries a statute (fields.py), so this artifact names
        no institution anywhere -- unlike data/national.json, which names several under two."""
        for label, listed in census_artifact["gaps"].items():
            assert listed in (None, []), f"{label}: Scorecard fields have no statute to name under"

    def test_the_composition_totals_match_the_scope_institutions(
        self, census_artifact: dict[str, Any]
    ) -> None:
        comp = census_artifact["composition"]
        assert comp["institutions"] == census_artifact["scope"]["institutions"]
        assert sum(comp["states"].values()) + comp["states_unstated"] == comp["institutions"]
        assert sum(comp["sectors"].values()) + comp["sectors_unstated"] == comp["institutions"]

    def test_the_sample_composition_totals_match_the_committed_sample(
        self, census_artifact: dict[str, Any]
    ) -> None:
        sample = json.loads(SAMPLE.read_text(encoding="utf-8"))
        comp = census_artifact["sample_composition"]
        assert comp["institutions"] == len(sample)
        assert sum(comp["states"].values()) + comp["states_unstated"] == comp["institutions"]

    def test_states_have_no_state_that_is_not_a_real_two_letter_code(
        self, census_artifact: dict[str, Any]
    ) -> None:
        """Not a claim about which codes are real US states or territories, only that the reduction
        did not fold blank or malformed values into a state bucket."""
        for label in census_artifact["composition"]["states"]:
            assert isinstance(label, str) and label.strip() and label == label.strip()


SNAPSHOTS = DATA / "snapshots" / "scorecard"
PROVENANCE = SNAPSHOTS / "provenance"

#: Every committed daily Scorecard snapshot, by the date it is filed under. Read from the
#: directory rather than listed, because the series accrues one file a day and a hand-maintained
#: list would stop covering it the moment nobody remembered to extend it. ``glob`` returning
#: nothing would silently parametrize every test below into zero tests, which is why
#: ``test_the_series_is_here_at_all`` asserts the list is not empty before any of them run.
SNAPSHOT_DATES = sorted(path.stem for path in SNAPSHOTS.glob("*.json"))

#: The day the committed capture was walked. The one date whose snapshot can be regenerated from
#: bytes in this repository, derived from the capture instead of written down so that refreshing
#: the capture moves it.
CAPTURE_DATE = json.loads(CAPTURE.read_text(encoding="utf-8"))["provenance"]["walked_at"][:10]


class TestTheCommittedScorecardSnapshots:
    """``data/snapshots/scorecard/``, which nothing regenerated and nothing compared.

    ``tests/test_replay.py`` replays every committed IPEDS snapshot from its own archives, and
    ``TestTheCommittedCensusArtifact`` above replays ``data/scorecard-census.json`` from the
    committed capture. The nine daily Scorecard snapshots beside them had neither: no test opened
    them, no ``make`` target regenerated them, and the only thing in the repository that mentioned
    them was ``tests/test_workflows.py`` asserting that a path string appears in the workflow YAML.
    They were committed counts standing in for a computation, with nothing checking the counts were
    still what the computation produces.

    What is gated here, and what deliberately is not:

    * The snapshot taken on the day of the committed capture is held to **byte equality** against
      what that capture regrades to. That is the missing gate, and it is the whole of it: it is the
      only snapshot whose input is in the repository.
    * The other snapshots are **not** compared against that replay. Their captures were ninety-day
      workflow artifacts and are gone, and more importantly the series exists to record drift. A
      day whose counts moved is the finding, not a failure, and freezing every file to today's
      replay would be a gate that forbids the thing being measured. They are held instead to what
      is true of them whatever the Scorecard published that morning: the date they claim, the walk
      they came from, and their own arithmetic.

    All nine replay byte-identically today, which is worth saying and is not worth asserting.
    """

    def test_the_series_is_here_at_all(self) -> None:
        """Run before anything parametrized over ``SNAPSHOT_DATES``, because an empty glob
        parametrizes into zero tests and reports as a passing suite."""
        assert SNAPSHOT_DATES, (
            f"{SNAPSHOTS} holds no snapshots. Every test below is parametrized over that "
            "directory, so an empty one turns this class into nothing at all."
        )
        assert CAPTURE_DATE in SNAPSHOT_DATES, (
            f"the committed capture was walked on {CAPTURE_DATE} and no snapshot is filed under "
            "that date, so no committed Scorecard snapshot can be regenerated from anything in "
            "this repository. Take the snapshot for that day, or commit the capture the series "
            "was actually taken from."
        )

    def test_the_snapshot_from_the_committed_capture_replays_byte_for_byte(
        self, graded: Path, tmp_path: Path
    ) -> None:
        """The one test that makes a committed Scorecard snapshot a claim a reader can check.

        Compared as bytes, the same discipline the national and census artifacts are held to.
        ``disclosed snapshot`` writes with ``sort_keys=True`` and a fixed indent, so the bytes are
        deterministic and a reformat that changes them is itself worth noticing.
        """
        out = tmp_path / f"{CAPTURE_DATE}.json"
        assert (
            cli.main(
                ["snapshot", "--report", str(graded), "--taken", CAPTURE_DATE, "--out", str(out)]
            )
            == 0
        )
        assert out.read_bytes() == (SNAPSHOTS / f"{CAPTURE_DATE}.json").read_bytes(), (
            f"data/snapshots/scorecard/{CAPTURE_DATE}.json does not match what the committed "
            "capture regrades to. Either a rule moved and the snapshot was not regenerated, or it "
            "was edited by hand. Run `make scorecard-snapshot-replay` to see the difference."
        )

    @pytest.mark.parametrize("taken", SNAPSHOT_DATES)
    def test_each_snapshot_names_the_day_it_is_filed_under(self, taken: str) -> None:
        """A snapshot whose ``taken`` disagrees with its filename would be read as drift on a day
        it was not taken, and `drift` orders the series by the name on the file."""
        recorded = json.loads((SNAPSHOTS / f"{taken}.json").read_text(encoding="utf-8"))
        assert recorded["taken"] == taken
        assert recorded["source"] == "College Scorecard"

    @pytest.mark.parametrize("taken", SNAPSHOT_DATES)
    def test_each_snapshot_is_paired_with_the_walk_it_was_computed_from(self, taken: str) -> None:
        """``snapshot.yml`` commits a provenance sidecar beside every snapshot so that "a drift
        finding can be traced to the bytes it was computed from". Nothing checked that the two
        files describe the same walk, or that the sidecar was there at all.

        The capture digests the sidecars record cannot be verified here: those captures are
        ninety-day workflow artifacts and have expired. What can be verified is that the walk
        claimed exhaustion and that the snapshot counted exactly the institutions the walk
        returned, which is the link a drift finding actually rests on.
        """
        sidecar = PROVENANCE / f"{taken}.json"
        assert sidecar.is_file(), (
            f"{sidecar} is missing. A snapshot with no provenance is a count with no walk behind "
            "it, and the drift it takes part in cannot be traced to anything."
        )
        walk = json.loads(sidecar.read_text(encoding="utf-8"))
        assert walk["exhausted"] is True, (
            f"{taken}: the walk did not confirm exhaustion, so this snapshot is a sample filed in "
            "a series that reads as national."
        )
        assert walk["total_stated"] == walk["records"]
        recorded = json.loads((SNAPSHOTS / f"{taken}.json").read_text(encoding="utf-8"))
        assert recorded["institutions"] == walk["records"], (
            f"{taken}: the snapshot counted {recorded['institutions']} institutions and the walk "
            f"beside it returned {walk['records']}. They are not the same run."
        )

    @pytest.mark.parametrize("taken", SNAPSHOT_DATES)
    def test_each_snapshot_adds_up(self, taken: str) -> None:
        """Internal arithmetic, checked independently of what produced the file. A replay proves
        one snapshot matches this code; this catches one written halfway or edited by hand,
        including on the days whose inputs are gone."""
        recorded = json.loads((SNAPSHOTS / f"{taken}.json").read_text(encoding="utf-8"))
        labels = {field.label for field in FIELDS}
        for section in ("applicable", "reported", "missing"):
            assert set(recorded[section]) == labels, (
                f"{taken}: {section} does not name the six fields this project grades"
            )
        for label in labels:
            applicable = recorded["applicable"][label]
            reported = recorded["reported"][label]
            missing = recorded["missing"][label]
            assert 0 <= applicable <= recorded["institutions"], (
                f"{taken}/{label}: applicable is outside 0..institutions"
            )
            # Not equality: implausible and suppressed values are applicable and are neither
            # reported nor missing, and folding them into either would be the collapse of the five
            # states this project exists to keep apart.
            assert reported >= 0 and missing >= 0
            assert reported + missing <= applicable, (
                f"{taken}/{label}: reported + missing exceeds applicable"
            )
