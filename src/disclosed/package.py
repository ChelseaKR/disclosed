"""The whole corpus described as one Frictionless data package, and as one schema.org Dataset.

Every artifact this project publishes is committed, reproducible and individually documented,
and until now there was no single document saying *what the set is*. A reader who found
``data/dataset.csv`` had no way to learn that the report it was exported from, the capture that
report was graded from, the national artifact, the two registry measurements and twenty-one
committed snapshots are all part of the same corpus, let alone which bytes they were supposed
to be.

Two descriptors, because two different readers ask the question:

* ``datapackage.json`` (Frictionless) is for somebody who wants to *use* the data: it names
  every resource, its media type, its size, its SHA-256, and — for the CSV — the full Table
  Schema, including what an empty cell means.
* ``dataset.jsonld`` (schema.org ``Dataset``) is for a catalogue harvester, which is the only
  reader that will never read prose.

**The descriptor cannot describe a file the repository does not hold.** Every resource is built
by reading the file: the byte count and the digest come from the bytes, not from a table
somebody maintains, and a resource whose path does not exist raises here rather than being
written out. That is the whole point of the artifact, and it is the direction that is gated.

**The asymmetry is deliberate and is worth saying out loud.** A committed descriptor can be
*stale* — a snapshot taken after it was generated is a file the package does not mention — and
nothing here goes red for that. The daily snapshot workflow regenerates the package in the same
commit that adds the snapshot, and ``tests/test_workflows.py`` holds it to that; but if that
step were ever removed, the failure would be a package that under-describes the corpus rather
than one that describes files nobody has. Of the two, only the second can mislead a consumer
into fetching something that is not there, and only the second is worth failing a build over.

**No clock.** ``created`` is the ``walked_at`` the capture's own provenance records, which is
the date the corpus is actually about. Reading it from the clock would make regenerating the
package a diff every time, and would date the corpus to the day somebody happened to rerun a
command.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from .dataset import to_schema

__all__ = [
    "DATASET_JSONLD_NAME",
    "PACKAGE_NAME",
    "PackageError",
    "Resource",
    "build",
    "dumps",
    "resource_paths",
    "to_jsonld",
]

#: What the descriptors are called wherever they are written. The package descriptor sits at the
#: repository root rather than under ``data/`` because every resource path in it is relative to
#: the descriptor's own directory: that is what makes ``frictionless validate datapackage.json``
#: work from a clone with no arguments, and a descriptor whose paths only resolve from somewhere
#: else is a descriptor a tool cannot follow.
PACKAGE_NAME: Final[str] = "datapackage.json"
DATASET_JSONLD_NAME: Final[str] = "dataset.jsonld"

_HOMEPAGE: Final[str] = "https://chelseakr.github.io/disclosed"
_REPOSITORY: Final[str] = "https://github.com/ChelseaKR/disclosed"

_LICENSES: Final[tuple[dict[str, str], ...]] = (
    {
        "name": "Apache-2.0",
        "title": "Apache License 2.0",
        "path": "https://www.apache.org/licenses/LICENSE-2.0",
    },
)

#: The publishers whose records this corpus is derived from. Named with the URL a reader would
#: go to, rather than the API endpoint a program would, because a ``sources`` entry is read by
#: people deciding whether to trust the derivation.
_SOURCES: Final[tuple[dict[str, str], ...]] = (
    {"title": "College Scorecard", "path": "https://collegescorecard.ed.gov/data/"},
    {"title": "IPEDS", "path": "https://nces.ed.gov/ipeds/use-the-data"},
    {"title": "Credential Registry", "path": "https://credentialengine.org/credential-registry/"},
)

#: Media types this project actually publishes. ``mimetypes`` alone answers ``None`` for a
#: ``.zip`` on some platforms and has no opinion at all about JSON-LD, and a resource with no
#: media type is one a harvester has to guess about.
_MEDIATYPES: Final[dict[str, str]] = {
    ".csv": "text/csv",
    ".json": "application/json",
    ".jsonld": "application/ld+json",
    ".zip": "application/zip",
}


class PackageError(ValueError):
    """A descriptor that would describe something the repository does not hold.

    Raised rather than warned about. A package descriptor exists to be dereferenced by a program
    that will not read a warning, and one naming a file that is not there is worse than no
    descriptor at all: it turns "I cannot find this data" into "this data is broken".
    """


@dataclass(frozen=True, slots=True)
class Resource:
    """One committed file, described from its own bytes."""

    name: str
    path: str
    title: str
    description: str
    schema: dict[str, Any] | None = None

    def as_dict(self, root: Path) -> dict[str, Any]:
        """The Frictionless resource object, with the size and digest read off disk.

        Raises:
            PackageError: If the file is not there. The descriptor is generated in one pass and
                either describes the corpus or is not written.
        """
        target = root / self.path
        if not target.is_file():
            raise PackageError(
                f"{self.path} is named as a resource and is not a file under {root}. A package "
                "descriptor is dereferenced by a program, so a path that does not resolve turns "
                "a missing file into a broken dataset."
            )
        payload = target.read_bytes()
        suffix = target.suffix.lower()
        mediatype = _MEDIATYPES.get(suffix) or mimetypes.guess_type(target.name)[0]
        if mediatype is None:
            raise PackageError(
                f"{self.path} has no known media type. Add one to _MEDIATYPES rather than "
                "publishing a resource a harvester has to guess the format of."
            )
        resource: dict[str, Any] = {
            "name": self.name,
            "path": self.path,
            "title": self.title,
            "description": self.description,
            "format": suffix.lstrip("."),
            "mediatype": mediatype,
            "bytes": len(payload),
            "hash": f"sha256:{hashlib.sha256(payload).hexdigest()}",
        }
        if suffix != ".zip":
            # Every text artifact here is written as UTF-8 by this project's own code. Declared
            # rather than assumed, because a consumer that guesses the encoding of a CSV holding
            # institution names is a consumer that will eventually guess wrong.
            resource["encoding"] = "utf-8"
        if self.schema is not None:
            resource["profile"] = "tabular-data-resource"
            resource["schema"] = self.schema
        return resource


#: Files under ``data/`` that are deliberately not resources, each with the reason. A register
#: rather than a silent skip: ``tests/test_package.py`` asserts that every file under ``data/``
#: is either a resource or named here, so a new artifact cannot be added to the corpus and left
#: out of the descriptor without somebody writing down why.
EXCLUDED: Final[dict[str, str]] = {
    "data/snapshots/scorecard/provenance": (
        "provenance sidecars: files about a run rather than runs. Describing one as a snapshot "
        "would be a file about the data read as the data, which is this project's own defect "
        "class."
    ),
    "data/snapshots/scorecard/README": (
        "a note to a maintainer about the directory layout, not an artifact anybody consumes."
    ),
    "data/crosscheck.json": (
        "gitignored on purpose: 2.9 MB, byte-reproducible from the committed IPEDS archives in "
        "under a second, and an intermediate rather than a claim. A descriptor naming it would "
        "point at a file a clone does not have."
    ),
}


def _snapshot_resources(root: Path) -> list[Resource]:
    """One resource per committed snapshot, oldest first within each source directory.

    Per file rather than per directory, because the "Done when" of this artifact is that every
    resource's digest matches its bytes, and a digest over a directory is not a thing. The glob
    is two levels deep for the reason :func:`disclosed.history.load`'s is: the provenance
    sidecars live one directory further down and are files *about* a run rather than runs.
    """
    resources: list[Resource] = []
    for path in sorted((root / "data" / "snapshots").glob("*/*.json")):
        collection = path.parent.name
        resources.append(
            Resource(
                name=f"snapshot-{collection}-{path.stem}",
                path=path.relative_to(root).as_posix(),
                title=f"Per-field disclosure counts, {collection}, {path.stem}",
                description=(
                    "One run reduced to per-field counts: how many institutions each field "
                    "reached, how many reported it, and how many did not. The denominator is "
                    "recorded rather than derived, because it moves for its own reasons and a "
                    "rate computed against a remembered one is a rate about a different "
                    "population."
                ),
            )
        )
    return resources


def _dispute_resources(root: Path) -> list[Resource]:
    """One resource per committed dispute, so the corpus descriptor names them too.

    None today: the register is empty because no institution has filed one, and a fabricated
    example committed to make a directory look used would be exactly the kind of plausible,
    unfounded statement this project objects to. The generator handles them anyway, so the first
    real dispute arrives in the descriptor rather than being noticed as missing later --
    ``test_the_committed_descriptor_is_what_the_code_generates`` fails on the pull request that
    adds one without regenerating, which is when somebody is looking.
    """
    return [
        Resource(
            name=f"dispute-{path.stem}",
            path=path.relative_to(root).as_posix(),
            title=f"Dispute filed by institution {path.stem}",
            description=(
                "A graded institution's stated objection to one finding about it, quoted "
                "verbatim on its page. A dispute is published beside a finding and never folded "
                "into one: no grade, score or published figure moves because it was filed."
            ),
        )
        for path in sorted((root / "disputes").glob("*.json"))
    ]


def _archive_resources(root: Path) -> list[Resource]:
    """The committed IPEDS archives, which are the only inputs to the national figures.

    Four megabytes of public federal data with no key, no quota and no terms. They are in the
    repository for the reason ``.gitignore`` records: without them every input to
    ``data/national.json`` was ignored, so nobody who cloned this could regenerate the file that
    carries every national figure the site publishes.
    """
    kinds = {"HD": "directory", "IC": "institutional characteristics"}
    resources: list[Resource] = []
    for path in sorted((root / "data").glob("*.zip")):
        prefix, year = path.stem[:2], path.stem[2:]
        resources.append(
            Resource(
                name=f"ipeds-{prefix.lower()}-{year}",
                path=path.relative_to(root).as_posix(),
                title=f"IPEDS {kinds.get(prefix, prefix)} file, {year} collection year",
                description=(
                    "The published archive exactly as downloaded. Committed so that the "
                    f"national figures derived from the {year} collection can be regenerated "
                    "from a clone rather than taken on trust."
                ),
            )
        )
    return resources


def _core_resources() -> list[Resource]:
    """The artifacts that are neither the daily series nor the IPEDS archives."""
    return [
        Resource(
            name="disclosure-grades",
            path="data/dataset.csv",
            title="Disclosure grades for US higher-education institutions",
            description=(
                "One row per institution. Every graded field is exported as a classification "
                "word rather than as a value, so that an unreported field, a field suppressed "
                "to protect a small cohort, a field that does not apply and a genuine zero stay "
                "four distinguishable facts in a spreadsheet."
            ),
            schema=to_schema()["schema"],
        ),
        Resource(
            name="disclosure-grades-schema",
            path="data/dataset.schema.json",
            title="The Table Schema for the CSV, published on its own",
            description=(
                "The same schema this descriptor carries inline, as a standalone file for a "
                "consumer who wants the column contract without the package. Generated from the "
                "field definitions in the pass that writes the CSV, so the two cannot drift."
            ),
        ),
        Resource(
            name="report",
            path="data/report.json",
            title="The graded report the CSV is exported from",
            description=(
                "Every institution's per-field classifications, score and letter, the scope the "
                "run covered, the rules version that graded it, and every implausible finding "
                "with the peer group behind it."
            ),
        ),
        Resource(
            name="scorecard-sample",
            path="data/sample.json",
            title="The College Scorecard records the committed report was graded from",
            description=(
                "The 600-institution slice the site's sample figures describe. Committed so "
                "that every grade on the site can be replayed from the bytes it was derived "
                "from, which is what the per-institution receipts verify against."
            ),
        ),
        Resource(
            name="scorecard-capture",
            path="data/census/scorecard.json",
            title="The College Scorecard walk the census figures rest on",
            description=(
                "Every institution the College Scorecard published on the walk date, with the "
                "provenance of every page fetched: the call count, each page's status and "
                "digest, and whether the walk paged the API to exhaustion. Without that last "
                "fact a large capture is still a sample."
            ),
        ),
        Resource(
            name="scorecard-census",
            path="data/scorecard-census.json",
            title="The census reduced to per-field figures, beside the sample's",
            description=(
                "What the whole College Scorecard population discloses, with the composition of "
                "both frames stated side by side so the skew of the 600-institution sample is a "
                "table rather than a sentence."
            ),
        ),
        Resource(
            name="national",
            path="data/national.json",
            title="The IPEDS-wide figures and the statute-backed gaps",
            description=(
                "The whole IPEDS directory graded on the six addresses it publishes, with the "
                "institutions a statute reaches that the federal record carries no address for."
            ),
        ),
        Resource(
            name="registry-organizations",
            path="data/registry/organizations.json",
            title="The Credential Registry organizations capture",
            description=(
                "The registry's organization records as walked, with the provenance of the "
                "walk. The input to the join measurement below."
            ),
        ),
        Resource(
            name="registry-properties-capture",
            path="data/registry/properties.json",
            title="The Credential Registry property capture",
            description=(
                "Which properties the registry's organizations actually carry, as walked. The "
                "input to the property census below."
            ),
        ),
        Resource(
            name="registry-join",
            path="data/registry-join.json",
            title="How far the Credential Registry reaches into the federal corpora",
            description=(
                "The measured join between the Credential Registry's organizations and the two "
                "federal identifier spaces, with the denominators every share is taken over."
            ),
        ),
        Resource(
            name="registry-properties",
            path="data/registry-properties.json",
            title="What the Credential Registry's organizations actually publish",
            description=(
                "The property census behind the decision not to write a registry adapter: which "
                "properties appear, over how many organizations, and how many of those the "
                "federal corpora can be joined to."
            ),
        ),
    ]


def build(root: Path) -> dict[str, Any]:
    """Assemble the Frictionless descriptor for the corpus committed under ``root``.

    Args:
        root: The repository root, the directory every resource path is relative to.

    Raises:
        PackageError: If any named resource is missing, has no known media type, or if the
            capture does not record the walk date the package is dated from.
    """
    capture = root / "data" / "census" / "scorecard.json"
    if not capture.is_file():
        raise PackageError(f"{capture} is missing, so there is no walk date to date the package")
    walked_at = (
        json.loads(capture.read_text(encoding="utf-8")).get("provenance", {}).get("walked_at")
    )
    if not isinstance(walked_at, str) or not walked_at:
        raise PackageError(
            "the committed capture records no walked_at, so the package has no date it could "
            "carry that is a fact about the data rather than about when a command was run"
        )
    resources = (
        _core_resources()
        + _archive_resources(root)
        + _snapshot_resources(root)
        + _dispute_resources(root)
    )
    return {
        "profile": "data-package",
        "name": "disclosed",
        "title": "disclosed: what US colleges do not tell you",
        "description": (
            "Grades US higher-education institutions on what they disclose rather than on how "
            "they perform. Every field is classified into one of five states -- reported, "
            "implausible, suppressed, not applicable, missing -- so that an absence is never "
            "published as a measurement. The corpus is the graded export, the federal captures "
            "it was derived from, and the committed run-by-run series that makes drift "
            "measurable."
        ),
        "homepage": _HOMEPAGE,
        "repository": _REPOSITORY,
        "created": walked_at,
        "licenses": [dict(licence) for licence in _LICENSES],
        "sources": [dict(source) for source in _SOURCES],
        "keywords": [
            "higher education",
            "disclosure",
            "transparency",
            "College Scorecard",
            "IPEDS",
            "missing data",
            "data quality",
        ],
        "resources": [resource.as_dict(root) for resource in resources],
    }


def to_jsonld(
    package: dict[str, Any], *, origin: str, distributions: str = "all"
) -> dict[str, Any]:
    """The same corpus as a schema.org ``Dataset``, for a harvester.

    A second rendering of one descriptor rather than a second descriptor: every field here is
    read out of ``package``, so the two cannot disagree about what the corpus is.
    ``contentUrl`` points into the repository rather than at the site, because that is where the
    files actually are; the site publishes pages about them.

    Args:
        package: A descriptor as :func:`build` returns it.
        origin: The absolute base URL the site is being published at.
        distributions: ``"all"`` names every resource, which is what
            ``dataset.jsonld`` carries. ``"primary"`` names the tabular export and the package
            descriptor only, which is what the home page's ``<head>`` carries: a head is read on
            every visit, and thirty-eight download entries inline there would be thirty-eight
            entries most readers never fetch. Both forms name the full descriptor, so nothing is
            hidden by the short one -- it is one dereference away rather than absent.
    """
    if distributions not in ("all", "primary"):
        raise PackageError(f"unknown distributions mode {distributions!r}")
    resources: list[dict[str, Any]] = list(package["resources"])
    if distributions == "primary":
        resources = [r for r in resources if r["name"] == "disclosure-grades"]
    downloads = [
        {
            "@type": "DataDownload",
            "name": resource["title"],
            "encodingFormat": resource["mediatype"],
            "contentSize": str(resource["bytes"]),
            "sha256": str(resource["hash"]).removeprefix("sha256:"),
            "contentUrl": f"{_REPOSITORY}/raw/master/{resource['path']}",
        }
        for resource in resources
    ]
    downloads.append(
        {
            "@type": "DataDownload",
            "name": "Frictionless data package descriptor for the whole corpus",
            "encodingFormat": "application/json",
            "contentUrl": f"{_REPOSITORY}/raw/master/{PACKAGE_NAME}",
        }
    )
    return {
        "@context": "https://schema.org",
        "@type": "Dataset",
        "name": package["title"],
        "description": package["description"],
        "url": origin + "/",
        "identifier": f"{origin}/{DATASET_JSONLD_NAME}",
        "license": _LICENSES[0]["path"],
        "creator": {"@type": "Person", "name": "Chelsea Kelly-Reif"},
        "dateCreated": package["created"],
        "keywords": list(package["keywords"]),
        "isBasedOn": [source["path"] for source in package["sources"]],
        "distribution": downloads,
    }


def dumps(payload: dict[str, Any]) -> str:
    """Serialize a descriptor deterministically, so regenerating it is a no-op in git."""
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def resource_paths(package: dict[str, Any]) -> Iterable[str]:
    """Every path the descriptor names, for a caller checking them against the tree."""
    return (str(resource["path"]) for resource in package.get("resources", []))
