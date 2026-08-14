"""The committed IPEDS artifacts, replayed from the committed archives that produced them.

`data/report.json` has been checkable since the capture it is graded from was committed beside it:
anyone who clones this repository can run the grader over `data/sample.json` and get the same
bytes back. The IPEDS chain had no such property. `data/national.json` carries every national
figure the site publishes, including the 846 institutions it names under two statutes, and every
one of its inputs was gitignored. The file could only be checked by the person holding the
archives, which is the shape of claim this project exists to object to when other people make it.

So the archives are committed now, and these tests are the reason to commit them. They replay the
whole IPEDS pipeline and compare the result against the artifacts the repository ships:

* `disclosed crosscheck` + `disclosed national` -> `data/national.json`
* `disclosed crosscheck` + `disclosed snapshot` -> `data/snapshots/ipeds/{year}.json`

Change an applicability rule and these fail. Before they existed, changing one moved the rationale
rendered on the methodology page and left the committed counts beside it untouched, and the whole
suite stayed green while the page and the artifact told a reader two different things.

The arithmetic tests below are the second line. A replay proves the artifact matches this code; the
invariants prove the artifact is internally coherent whatever produced it, which is what catches a
file that was regenerated halfway or edited by hand.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from disclosed import cli
from disclosed.fields import IPEDS_FIELDS
from disclosed.scope import NATIONAL

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

# The collection years whose archives and reduced artifacts are both committed. 2023 is the year
# the site's national page is built from; 2021 and 2022 exist because the 2% systemic threshold is
# justified against three years of movement, and a justification a reader cannot recompute is an
# assertion.
YEARS = (2021, 2022, 2023)


def _archives(year: int) -> tuple[Path, Path]:
    return DATA / f"HD{year}.zip", DATA / f"IC{year}.zip"


def _crosscheck(tmp_path: Path, year: int, *, with_scorecard: bool) -> Path:
    """Regrade the whole IPEDS directory for one year from the committed archives.

    No network: `load_institutions` reads the cache path when the file is there, and both files
    are in the repository. A run that reached NCES would be testing NCES.
    """
    directory, characteristics = _archives(year)
    out = tmp_path / f"crosscheck-{year}.json"
    argv = [
        "crosscheck",
        "--year",
        str(year),
        "--cache",
        str(directory),
        "--characteristics",
        str(characteristics),
        "--out",
        str(out),
    ]
    if with_scorecard:
        argv += ["--source", str(DATA / "sample.json")]
    assert cli.main(argv) == 0
    return out


@pytest.fixture(scope="module")
def national_artifact() -> dict[str, Any]:
    return json.loads((DATA / "national.json").read_text(encoding="utf-8"))


class TestTheArchivesAreThere:
    """The premise of every other test in this file, asserted rather than assumed.

    If an archive goes missing the replays below fail with a parse error from somewhere inside the
    zip module, which reads as a bug in the adapter. This says what actually happened.
    """

    @pytest.mark.parametrize("year", YEARS)
    def test_both_archives_for_each_committed_snapshot_are_in_the_repository(
        self, year: int
    ) -> None:
        for archive in _archives(year):
            assert archive.is_file(), (
                f"{archive.name} is missing. The IPEDS artifacts cannot be checked without it, "
                "and an artifact nobody can regenerate is not evidence."
            )
            assert archive.stat().st_size > 0


class TestTheCommittedNationalArtifact:
    def test_it_replays_byte_for_byte_from_the_committed_archives(self, tmp_path: Path) -> None:
        """The one test that makes `data/national.json` a claim a reader can check.

        Compared as bytes rather than as parsed JSON, because this file is committed, diffed, and
        cited. `disclosed national` writes it with `sort_keys=True` and a fixed indent, so the
        bytes are deterministic, and a reformat that changes them is itself worth noticing: the
        artifact is the thing the site is rendered from and the thing a reader downloads.
        """
        crosscheck = _crosscheck(tmp_path, 2023, with_scorecard=True)
        out = tmp_path / "national.json"
        assert cli.main(["national", "--report", str(crosscheck), "--out", str(out)]) == 0
        assert out.read_bytes() == (DATA / "national.json").read_bytes(), (
            "data/national.json does not match what the committed archives produce. Either a rule "
            "moved and the artifact was not regenerated, or the artifact was edited. Run "
            "`make replay` to see the difference."
        )

    @pytest.mark.parametrize("year", YEARS)
    def test_each_committed_ipeds_snapshot_replays_from_its_own_archives(
        self, tmp_path: Path, year: int
    ) -> None:
        """The three-year drift history, made reproducible.

        The published methodology argues for the 2% systemic threshold from movements measured
        across these three files. Two of the three had no committed inputs, so the argument rested
        on numbers only its author could recompute.
        """
        crosscheck = _crosscheck(tmp_path, year, with_scorecard=False)
        out = tmp_path / f"{year}.json"
        assert (
            cli.main(
                [
                    "snapshot",
                    "--report",
                    str(crosscheck),
                    "--taken",
                    str(year),
                    "--out",
                    str(out),
                ]
            )
            == 0
        )
        committed = DATA / "snapshots" / "ipeds" / f"{year}.json"
        assert out.read_bytes() == committed.read_bytes(), (
            f"data/snapshots/ipeds/{year}.json does not match what HD{year}.zip and IC{year}.zip "
            "produce."
        )


class TestTheNationalArtifactAddsUp:
    """Internal arithmetic, checked independently of what produced the file.

    The replay above catches an artifact that disagrees with this code. These catch an artifact
    that disagrees with itself: a half-regenerated file, a hand edit, a merge that took one side of
    a count and the other side of a list. They hold on the current file and would land green; that
    is the point of an invariant.
    """

    def test_the_artifact_says_it_is_national(self, national_artifact: dict[str, Any]) -> None:
        scope = national_artifact["scope"]
        assert scope["kind"] == NATIONAL
        assert scope["coverage"] == 1.0
        assert scope["institutions"] == scope["universe"]

    def test_every_field_names_a_field_this_project_actually_grades(
        self, national_artifact: dict[str, Any]
    ) -> None:
        """A label the code no longer knows is a column on the national page linking nowhere."""
        known = {f.label: f for f in IPEDS_FIELDS}
        published = [f["label"] for f in national_artifact["fields"]]
        assert published == list(known)
        for row in national_artifact["fields"]:
            field = known[row["label"]]
            assert row["key"] == field.key
            assert row["statute"] == field.statute

    def test_the_denominator_is_the_sum_of_what_it_is_made_of(
        self, national_artifact: dict[str, Any]
    ) -> None:
        for row in national_artifact["fields"]:
            assert row["applicable"] == row["reported"] + row["missing"] + row["implausible"], (
                f"{row['label']}: applicable is not reported + missing + implausible"
            )

    def test_every_institution_lands_in_exactly_one_bucket_for_every_field(
        self, national_artifact: dict[str, Any]
    ) -> None:
        """Suppressed and not-applicable leave the denominator; they do not leave the population.

        A field whose buckets do not add back up to the directory has lost institutions somewhere,
        and the ones lost silently are exactly the ones a reader would want counted.
        """
        institutions = national_artifact["scope"]["institutions"]
        for row in national_artifact["fields"]:
            total = row["applicable"] + row["suppressed"] + row["not_applicable"]
            assert total == institutions, f"{row['label']}: {total} accounted for of {institutions}"

    def test_a_share_is_the_division_it_claims_to_be_or_it_is_not_a_number(
        self, national_artifact: dict[str, Any]
    ) -> None:
        for row in national_artifact["fields"]:
            if row["applicable"] == 0:
                # Never 0.0. A field that reached nobody has no reporting rate, and a zero there
                # would say everybody it reached had failed it.
                assert row["share_reported"] is None
                continue
            assert row["share_reported"] == pytest.approx(row["reported"] / row["applicable"])

    def test_the_named_institutions_are_exactly_the_ones_the_counts_say_are_missing(
        self, national_artifact: dict[str, Any]
    ) -> None:
        """The counts and the names are written by two different functions over the same run.

        This is the pairing that publishes a person's institution by name, so a drift between them
        is the most expensive one in the file: a list longer than its count is an institution named
        without being counted, and a list shorter is a count nobody can check.
        """
        gaps = national_artifact["gaps"]
        for row in national_artifact["fields"]:
            listed = gaps.get(row["label"])
            if not row["statute"]:
                assert listed is None, (
                    f"{row['label']} has no statute behind it, so its institutions must be counted "
                    "and not named"
                )
                continue
            assert listed is not None
            assert len(listed) == row["missing"], (
                f"{row['label']}: {len(listed)} named, {row['missing']} counted"
            )

    def test_no_named_institution_is_published_as_the_word_none(
        self, national_artifact: dict[str, Any]
    ) -> None:
        for label, listed in national_artifact["gaps"].items():
            for row in listed:
                for key in ("unit_id", "name", "state"):
                    assert row[key] is None or (
                        isinstance(row[key], str) and row[key].strip() and row[key] != "None"
                    ), f"{label}: {key} is {row[key]!r}"

    def test_the_named_lists_are_sorted_so_a_diff_means_a_disclosure_changed(
        self, national_artifact: dict[str, Any]
    ) -> None:
        for label, listed in national_artifact["gaps"].items():
            keys = [(row["unit_id"] or "", row["name"] or "") for row in listed]
            assert keys == sorted(keys), f"{label} is not in a stable order"

    def test_no_institution_is_named_twice_under_one_disclosure(
        self, national_artifact: dict[str, Any]
    ) -> None:
        for label, listed in national_artifact["gaps"].items():
            identified = [row["unit_id"] for row in listed if row["unit_id"]]
            assert len(identified) == len(set(identified)), f"{label} names an institution twice"
