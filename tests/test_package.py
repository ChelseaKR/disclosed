"""The package descriptor, held to the files it describes.

A descriptor is dereferenced by a program. If it names a file that is not there, or states a
digest that is not the file's, the failure reaches a consumer as "this dataset is broken" rather
than as "I could not find this dataset", and nobody here ever sees it. So every assertion below
reads the actual bytes rather than a number written into this file.

The one direction deliberately **not** gated is staleness in the other sense: a descriptor that
omits a file the repository holds. ``TestTheDescriptorCoversTheCorpus`` does gate the general
case, with an explicit exclusion register, but the daily snapshot series is the one part that
grows on a schedule, and it is kept current by the workflow rather than by a test that would go
red overnight for a reason nobody caused. ``tests/test_workflows.py`` holds the workflow to it.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from disclosed import package, site
from disclosed.fields import FIELDS

_ROOT = Path(__file__).resolve().parent.parent
_COMMITTED = _ROOT / package.PACKAGE_NAME

# Frictionless names a resource with lowercase alphanumerics, dots, dashes and underscores. A
# name outside that is not a name the spec's own tooling will accept.
_RESOURCE_NAME = re.compile(r"^[a-z0-9._-]+$")

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


@pytest.fixture(scope="module")
def committed() -> dict[str, Any]:
    assert _COMMITTED.is_file(), (
        f"{package.PACKAGE_NAME} is not committed. It is the only document saying what the "
        "corpus is; without it every artifact here is a file somebody has to already know about."
    )
    payload: dict[str, Any] = json.loads(_COMMITTED.read_text(encoding="utf-8"))
    return payload


@pytest.fixture(scope="module")
def generated() -> dict[str, Any]:
    return package.build(_ROOT)


class TestEveryResourceIsTheFileItSaysItIs:
    """Size and digest against the bytes, for all of them, every run."""

    def test_the_committed_descriptor_names_at_least_the_whole_core_corpus(
        self, committed: dict[str, Any]
    ) -> None:
        """Otherwise every loop below is a loop over nothing, or over almost nothing."""
        assert len(committed["resources"]) >= 20

    def test_every_named_resource_exists(self, committed: dict[str, Any]) -> None:
        missing = [
            path for path in package.resource_paths(committed) if not (_ROOT / path).is_file()
        ]

        assert not missing, (
            f"{package.PACKAGE_NAME} names files this repository does not hold: {missing}. A "
            "consumer following one of those gets a broken dataset rather than a missing one."
        )

    def test_every_digest_and_byte_count_is_the_file_s_own(self, committed: dict[str, Any]) -> None:
        wrong: list[str] = []
        for resource in committed["resources"]:
            payload = (_ROOT / resource["path"]).read_bytes()
            if len(payload) != resource["bytes"]:
                wrong.append(f"{resource['path']}: bytes")
            if f"sha256:{hashlib.sha256(payload).hexdigest()}" != resource["hash"]:
                wrong.append(f"{resource['path']}: hash")

        assert not wrong, (
            f"{package.PACKAGE_NAME} is stale against these files: {wrong}. Regenerate it with "
            "`make dataset`, in the commit that moved them."
        )

    def test_the_committed_descriptor_is_what_the_code_generates(
        self, committed: dict[str, Any], generated: dict[str, Any]
    ) -> None:
        assert committed == generated, (
            f"{package.PACKAGE_NAME} is not what `disclosed dataset --package` produces from "
            "this tree. Regenerate it rather than editing it by hand: every figure in it is "
            "read off a file, and a hand-edited one is a claim nobody measured."
        )

    def test_regenerating_it_twice_produces_the_same_bytes(self, generated: dict[str, Any]) -> None:
        assert package.dumps(generated) == package.dumps(package.build(_ROOT))


class TestTheTabularResourceDescribesTheActualCsv:
    def test_the_schema_fields_are_the_csv_header_in_order(self, committed: dict[str, Any]) -> None:
        """A schema whose column order differs from the file's is a schema for another file."""
        resource = next(r for r in committed["resources"] if r["name"] == "disclosure-grades")
        with (_ROOT / resource["path"]).open(encoding="utf-8", newline="") as handle:
            header = next(csv.reader(handle))

        assert [field["name"] for field in resource["schema"]["fields"]] == header

    def test_the_tabular_resource_declares_the_profile_that_makes_its_schema_readable(
        self, committed: dict[str, Any]
    ) -> None:
        resource = next(r for r in committed["resources"] if r["name"] == "disclosure-grades")

        assert resource["profile"] == "tabular-data-resource"
        assert resource["mediatype"] == "text/csv"

    def test_the_schema_still_says_that_an_empty_cell_means_exactly_one_thing(
        self, committed: dict[str, Any]
    ) -> None:
        """The whole argument of the export, carried into the descriptor rather than left behind."""
        resource = next(r for r in committed["resources"] if r["name"] == "disclosure-grades")

        assert resource["schema"]["missingValues"] == [""]

    def test_no_other_resource_claims_to_be_tabular(self, committed: dict[str, Any]) -> None:
        tabular = [r["name"] for r in committed["resources"] if "schema" in r]

        assert tabular == ["disclosure-grades"]


class TestTheDateIsAFactAboutTheDataAndNotAboutTheRun:
    def test_created_is_the_walk_date_the_capture_records(self, committed: dict[str, Any]) -> None:
        capture = json.loads(
            (_ROOT / "data" / "census" / "scorecard.json").read_text(encoding="utf-8")
        )

        assert committed["created"] == capture["provenance"]["walked_at"]

    def test_a_capture_that_records_no_walk_date_refuses_rather_than_reading_the_clock(
        self, tmp_path: Path
    ) -> None:
        """The tempting fallback is ``datetime.now``, and it would date the corpus to a rerun."""
        (tmp_path / "data" / "census").mkdir(parents=True)
        (tmp_path / "data" / "census" / "scorecard.json").write_text(
            json.dumps({"provenance": {}, "records": []}), encoding="utf-8"
        )

        with pytest.raises(package.PackageError, match="walked_at"):
            package.build(tmp_path)

    def test_a_missing_capture_refuses(self, tmp_path: Path) -> None:
        with pytest.raises(package.PackageError, match="walk date"):
            package.build(tmp_path)


class TestItRefusesToDescribeWhatIsNotThere:
    def test_a_resource_whose_file_is_missing_raises(self, tmp_path: Path) -> None:
        resource = package.Resource(
            name="absent", path="data/not-here.json", title="t", description="d"
        )

        with pytest.raises(package.PackageError, match="not a file"):
            resource.as_dict(tmp_path)

    def test_a_file_type_with_no_declared_media_type_raises(self, tmp_path: Path) -> None:
        """A harvester given no media type guesses, and the guess is not this project's to make."""
        (tmp_path / "data").mkdir()
        (tmp_path / "data" / "thing.qqq").write_bytes(b"x")
        resource = package.Resource(name="odd", path="data/thing.qqq", title="t", description="d")

        with pytest.raises(package.PackageError, match="media type"):
            resource.as_dict(tmp_path)


class TestTheDescriptorCoversTheCorpus:
    """Every committed file under ``data/`` is described, or is named as deliberately not.

    The register is the point. A silent skip and a documented exclusion look identical from
    outside, and only one of them is a decision somebody made.
    """

    def _committed_data_files(self) -> list[str]:
        listing = subprocess.run(  # noqa: S603 - fixed argv, no shell, repository root
            ["git", "-C", str(_ROOT), "ls-files", "data"],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
        )
        return sorted(line for line in listing.stdout.splitlines() if line)

    def test_git_is_actually_telling_us_about_files(self) -> None:
        assert len(self._committed_data_files()) > 20

    def test_every_committed_data_file_is_a_resource_or_a_named_exclusion(
        self, committed: dict[str, Any]
    ) -> None:
        described = set(package.resource_paths(committed))
        undescribed = [
            path
            for path in self._committed_data_files()
            if path not in described
            and not any(
                path == excluded or path.startswith(excluded + "/") for excluded in package.EXCLUDED
            )
        ]

        assert not undescribed, (
            f"these committed files are in neither the descriptor nor package.EXCLUDED: "
            f"{undescribed}. Add them as resources, or add them to the register with the reason "
            "they are not part of the published corpus."
        )

    def test_the_exclusion_register_names_nothing_that_is_also_a_resource(
        self, committed: dict[str, Any]
    ) -> None:
        described = set(package.resource_paths(committed))

        assert not described & set(package.EXCLUDED)

    def test_every_exclusion_gives_a_reason(self) -> None:
        assert all(len(reason) > 40 for reason in package.EXCLUDED.values())


class TestTheDescriptorIsAValidDataPackage:
    """A minimal structural check, and an honest account of what it is not.

    This is **not** ``frictionless validate``. Adding the ``frictionless`` package as a dev
    dependency would pull a large transitive tree into a repository whose runtime dependency
    list is empty, for the sake of one file; the trade the issue left open is taken here in
    favour of no new dependency, and this class is the cost of that choice written down. It
    checks the Data Package v1 requirements that can be checked structurally: the required keys,
    resource names that the spec's own tooling will accept, unique names, and paths that are
    relative and do not escape the package. It does **not** check the profile's full JSON Schema,
    dialect handling, or that the CSV parses under its declared dialect.
    """

    def test_the_required_top_level_keys_are_present(self, committed: dict[str, Any]) -> None:
        for key in ("name", "profile", "resources", "licenses"):
            assert key in committed, key

    def test_every_resource_carries_the_required_keys(self, committed: dict[str, Any]) -> None:
        for resource in committed["resources"]:
            assert resource.get("name"), resource
            assert resource.get("path"), resource

    def test_every_resource_name_is_one_the_spec_accepts_and_is_unique(
        self, committed: dict[str, Any]
    ) -> None:
        names = [resource["name"] for resource in committed["resources"]]

        assert len(names) == len(set(names))
        for name in names:
            assert _RESOURCE_NAME.match(name), name

    def test_no_resource_path_is_absolute_or_escapes_the_package(
        self, committed: dict[str, Any]
    ) -> None:
        """A path out of the package directory is how a descriptor reads a file it should not."""
        for path in package.resource_paths(committed):
            assert not path.startswith("/")
            assert ".." not in Path(path).parts


class TestTheDatasetHarvestersRead:
    def test_every_field_of_the_jsonld_comes_from_the_package(
        self, committed: dict[str, Any]
    ) -> None:
        document = package.to_jsonld(committed, origin="https://example.test/disclosed")

        assert document["@type"] == "Dataset"
        assert document["name"] == committed["title"]
        assert document["description"] == committed["description"]
        assert document["dateCreated"] == committed["created"]
        assert document["keywords"] == committed["keywords"]
        assert document["license"] == committed["licenses"][0]["path"]

    def test_the_full_form_names_every_resource_and_the_descriptor_itself(
        self, committed: dict[str, Any]
    ) -> None:
        document = package.to_jsonld(committed, origin="https://example.test")

        assert len(document["distribution"]) == len(committed["resources"]) + 1
        assert any(
            download["contentUrl"].endswith(package.PACKAGE_NAME)
            for download in document["distribution"]
        )

    def test_every_download_carries_the_digest_the_package_carries(
        self, committed: dict[str, Any]
    ) -> None:
        document = package.to_jsonld(committed, origin="https://example.test")
        digests = {
            download["sha256"] for download in document["distribution"] if "sha256" in download
        }

        assert digests == {r["hash"].removeprefix("sha256:") for r in committed["resources"]}

    def test_the_primary_form_is_the_export_and_the_descriptor_and_nothing_else(
        self, committed: dict[str, Any]
    ) -> None:
        """What the home page's head carries. Short, and never a different claim.

        Trimming the download list is a size decision, not an editorial one, so everything the
        long form says about the dataset itself has to survive into the short one.
        """
        full = package.to_jsonld(committed, origin="https://example.test")
        short = package.to_jsonld(committed, origin="https://example.test", distributions="primary")

        assert len(short["distribution"]) == 2
        assert {d["name"] for d in short["distribution"]} == {
            next(r for r in committed["resources"] if r["name"] == "disclosure-grades")["title"],
            "Frictionless data package descriptor for the whole corpus",
        }
        assert {k: v for k, v in short.items() if k != "distribution"} == {
            k: v for k, v in full.items() if k != "distribution"
        }

    def test_an_unknown_distributions_mode_raises_rather_than_quietly_naming_everything(
        self, committed: dict[str, Any]
    ) -> None:
        with pytest.raises(package.PackageError):
            package.to_jsonld(committed, origin="https://example.test", distributions="some")


class TestTheSitePublishesIt:
    def test_without_a_package_the_build_is_byte_for_byte_what_it_was(self, tmp_path: Path) -> None:
        first, second = tmp_path / "a", tmp_path / "b"
        site.build(_REPORT, first, generated="2026-09-07")
        site.build(_REPORT, second, generated="2026-09-07", package=None)

        for page in sorted(first.rglob("index.html")):
            assert page.read_bytes() == (second / page.relative_to(first)).read_bytes()
        assert not (first / package.DATASET_JSONLD_NAME).exists()

    def test_no_page_carries_a_dataset_block_when_the_build_was_shown_no_package(
        self, tmp_path: Path
    ) -> None:
        site.build(_REPORT, tmp_path, generated="2026-09-07")

        for page in sorted(tmp_path.rglob("index.html")):
            assert "application/ld+json" not in page.read_text(encoding="utf-8")

    def test_with_a_package_the_home_page_head_carries_the_dataset(
        self, tmp_path: Path, committed: dict[str, Any]
    ) -> None:
        site.build(
            _REPORT, tmp_path, origin="https://example.test", generated="x", package=committed
        )
        home = (tmp_path / "index.html").read_text(encoding="utf-8")
        block = re.search(r'<script type="application/ld\+json">(.*?)</script>', home, re.S)
        assert block is not None

        payload = json.loads(block.group(1).replace("\\u003c", "<"))

        assert payload["@type"] == "Dataset"
        assert payload["url"] == "https://example.test/"
        assert len(payload["distribution"]) == 2

    def test_the_block_cannot_close_the_script_element_it_sits_in(
        self, tmp_path: Path, committed: dict[str, Any]
    ) -> None:
        """A ``</script>`` inside a JSON string ends the element, and the rest of the document
        becomes markup. The values here are titles and URLs today, and this is the guard that
        keeps that from being a premise."""
        poisoned = json.loads(json.dumps(committed))
        poisoned["title"] = "</script><img src=x onerror=alert(1)>"

        site.build(_REPORT, tmp_path, generated="x", package=poisoned)
        home = (tmp_path / "index.html").read_text(encoding="utf-8")

        assert "</script><img" not in home
        assert "\\u003c/script" in home

    def test_the_full_descriptor_is_served_beside_the_pages(
        self, tmp_path: Path, committed: dict[str, Any]
    ) -> None:
        site.build(
            _REPORT, tmp_path, origin="https://example.test", generated="x", package=committed
        )

        served = json.loads((tmp_path / package.DATASET_JSONLD_NAME).read_text(encoding="utf-8"))

        assert served["@type"] == "Dataset"
        assert len(served["distribution"]) == len(committed["resources"]) + 1
        assert served["identifier"] == f"https://example.test/{package.DATASET_JSONLD_NAME}"

    def test_the_command_writes_both_descriptors(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from disclosed import cli

        report = tmp_path / "report.json"
        report.write_text(json.dumps(_REPORT), encoding="utf-8")
        out = tmp_path / "site"

        code = cli.main(
            [
                "site",
                "--report",
                str(report),
                "--package",
                str(_COMMITTED),
                "--out",
                str(out),
                "--generated",
                "x",
            ]
        )
        capsys.readouterr()

        assert code == 0
        assert (out / package.DATASET_JSONLD_NAME).is_file()

    def test_the_dataset_command_refuses_to_write_a_descriptor_it_cannot_build(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from disclosed import cli

        report = tmp_path / "report.json"
        report.write_text(json.dumps(_REPORT), encoding="utf-8")
        target = tmp_path / "datapackage.json"

        code = cli.main(
            [
                "dataset",
                "--report",
                str(report),
                "--out",
                str(tmp_path / "dataset.csv"),
                "--package",
                str(target),
                "--root",
                str(tmp_path / "not-a-repository"),
            ]
        )

        assert code == 1
        assert "refusing to write a package descriptor" in capsys.readouterr().err
        assert not target.exists()


class TestPythonCanReadItBack:
    def test_the_committed_descriptor_parses_and_round_trips(
        self, committed: dict[str, Any]
    ) -> None:
        assert json.loads(package.dumps(committed)) == committed

    def test_the_interpreter_running_this_is_the_one_that_wrote_it(self) -> None:
        """Guards the determinism claim against a dict-ordering surprise across versions."""
        assert sys.version_info >= (3, 12)
