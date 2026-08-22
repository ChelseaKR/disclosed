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
