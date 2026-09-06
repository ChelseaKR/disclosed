"""The version this project declares, held against the releases it actually has.

`pyproject.toml` declares `0.1.0.dev0` and `git tag` returns nothing, which is the honest
state and a deliberate one: ADR 0001 declines a release pipeline with nothing to release,
on the ground that it would be "a gate that never runs and therefore never fails, which is
the kind of reassuring non-check this project exists to argue against". A PEP 440
developmental release says "not released" in a form pip, `packaging` and an SBOM consumer
all read correctly.

What nothing checked was whether any of that was still true. The declared version was
written down again in four places -- `src/disclosed/__init__.py`, `README.md`,
`SECURITY.md`, `docs/ROADMAP.md` -- and any of them could drift from the file that owns it
without a gate noticing. ADR 0001's consequences (`CITATION.cff` omits `version` and
`date-released`; the CHANGELOG keeps everything under Unreleased) were prose that nothing
enforced. And a tag cut without a matching declaration would have gone unremarked.

Two states are distinguished here, and only one is a defect:

* **No tags at all** -- where this project stands. It passes, but only if the declared
  version says so in a form a tool can read (the `.devN` suffix) *and* the documents a
  reader opens say so too. If nothing says it, the silence is the finding.
* **Tags exist and none matches the declared version** -- a defect. The failure names the
  declared version and the newest tag, because "they disagree" is not actionable without
  both numbers.

The second branch is unreachable from this repository today, so it is driven below against
synthetic tag lists, and it was proved end to end against a throwaway clone of this
repository carrying real `v9.9.9` and `v0.0.1` tags. It is never proved by tagging this
repository: cutting a tag is the maintainer's act, and ADR 0001 says the first one has to
bring the whole hardened release workflow with it in the same change.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tomllib
from collections.abc import Sequence
from pathlib import Path

import pytest

from disclosed import __version__ as PACKAGE_VERSION

ROOT = Path(__file__).resolve().parent.parent

#: PEP 440 developmental release: `0.1.0.dev0` sorts below `0.1.0` and is skipped by a
#: plain `pip install`, which is right for something that was never released.
DEV_SUFFIX = re.compile(r"\.dev\d+$")


def declared_version(root: Path = ROOT) -> str:
    """The one source of truth for this project's version."""

    with (root / "pyproject.toml").open("rb") as handle:
        return str(tomllib.load(handle)["project"]["version"])


def repository_tags(root: Path = ROOT) -> list[str]:
    """Every tag this checkout can see. Empty means "none visible", not "none exist"."""

    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["git", "-C", str(root), "tag", "--list"],  # noqa: S607 - git is the tool
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def is_shallow(root: Path = ROOT) -> bool:
    """Whether this checkout was truncated -- an empty tag list then proves nothing."""

    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["git", "-C", str(root), "rev-parse", "--is-shallow-repository"],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() == "true"


def remote_tags(root: Path = ROOT) -> list[str] | None:
    """The tags the remote actually has, or None when the remote could not be asked.

    The local list is only as complete as the fetch that produced it: `actions/checkout`
    fetches no tags unless asked, so a gate reading `git tag` off a default checkout
    answers "none" whatever the truth is, and passes blind. `git ls-remote` is the
    authoritative answer, and it goes over the git protocol -- it costs no GitHub API
    quota at all.
    """

    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [  # noqa: S607 - git is the tool
            "git",
            "-C",
            str(root),
            "ls-remote",
            "--tags",
            "--refs",
            "origin",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if result.returncode != 0:
        return None
    return sorted(
        line.rsplit("refs/tags/", 1)[-1]
        for line in result.stdout.splitlines()
        if "refs/tags/" in line
    )


def strip_v(tag: str) -> str:
    return tag[1:] if tag.startswith("v") else tag


def newest_tag(tags: Sequence[str]) -> str:
    """The highest tag by numeric components, ties broken by name.

    Deliberately not `--sort=-creatordate`: a tag re-cut later would then read as newer
    than the release it replaced, and a shallow fetch may not carry creator dates at all.
    """

    def key(tag: str) -> tuple[tuple[int, ...], str]:
        return tuple(int(part) for part in re.findall(r"\d+", strip_v(tag))), tag

    return max(tags, key=key)


def version_against_tags(declared: str, tags: Sequence[str]) -> str | None:
    """The finding, or None when the declared version is answerable to the tags."""

    if not tags:
        return None
    if any(strip_v(tag) == declared for tag in tags):
        return None
    return (
        f"pyproject.toml declares version {declared!r}, and none of the {len(tags)} tag(s) in "
        f"this repository matches it. The newest tag is {newest_tag(tags)!r}. Either a release "
        "was cut without bumping the declared version, or the declared version has run ahead "
        "of what was released and should keep its PEP 440 '.devN' suffix until it is tagged."
    )


#: Where a reader is told, in prose, that nothing has been released. Required while there
#: are no tags; retired by the commit that cuts the first one -- both directions gated.
UNRELEASED_STATEMENTS: tuple[tuple[str, str], ...] = (
    ("README.md", r"There are deliberately no tagged releases"),
    ("CHANGELOG.md", r"nothing has been released yet"),
    ("docs/adr/0001-no-versioned-release.md", r"there are no git tags"),
)


def unreleased_statement_findings(root: Path, tags: Sequence[str]) -> list[str]:
    findings = []
    for name, pattern in UNRELEASED_STATEMENTS:
        text = (root / name).read_text(encoding="utf-8")
        present = re.search(pattern, text) is not None
        if not tags and not present:
            findings.append(
                f"{name} no longer tells a reader that nothing has been released (expected to "
                f"match {pattern!r}), and no tag exists to make that true."
            )
        if tags and present:
            findings.append(
                f"{name} still says nothing has been released, but {newest_tag(tags)!r} exists."
            )
    return findings


def citation_findings(root: Path, tags: Sequence[str]) -> list[str]:
    """ADR 0001: `CITATION.cff` omits `version` and `date-released`, as CFF permits.

    Both name a release. Without a tag there is none to name. This is the consequence the
    ADR wrote down and nothing checked.
    """

    text = (root / "CITATION.cff").read_text(encoding="utf-8")
    findings = []
    if not tags:
        for key in ("version", "date-released"):
            if re.search(rf"^{key}:", text, re.MULTILINE):
                findings.append(
                    f"CITATION.cff declares {key}, but this repository has no tags "
                    "(ADR 0001 omits both until a versioned artifact exists)."
                )
    return findings


#: Documents that state the version in prose, with the pattern that captures it.
RESTATEMENTS: tuple[tuple[str, str], ...] = (
    ("README.md", r"\*\*Status:\*\* Beta, pre-release \(`([^`]+)`\)"),
    ("SECURITY.md", r"\| default branch \(pre-release, ([^)]+)\) \|"),
    ("docs/ROADMAP.md", r"Working, pre-release \(`([^`]+)`, status: Beta\)"),
)


def restated_versions(root: Path) -> dict[str, str]:
    """Every place outside `pyproject.toml` that writes the version down again.

    `src/disclosed/__init__.py` used to be on this list. It now reads the installed
    distribution metadata, so it is checked as a derivation rather than as a copy.
    """

    found = {}
    for name, pattern in RESTATEMENTS:
        match = re.search(pattern, (root / name).read_text(encoding="utf-8"))
        found[f"{name} status line"] = match.group(1) if match else ""
    return found


def restatement_findings(root: Path, declared: str) -> list[str]:
    return [
        f"{where} says {found!r}, but pyproject.toml declares {declared!r}."
        for where, found in restated_versions(root).items()
        if found != declared
    ]


DECLARED = declared_version()
TAGS = repository_tags()


# --- the repository as it actually is --------------------------------------------------


def test_the_declared_version_is_answerable_to_the_tags_that_exist() -> None:
    finding = version_against_tags(DECLARED, TAGS)
    assert finding is None, finding


def test_an_empty_tag_list_is_measured_rather_than_inherited_from_a_shallow_clone() -> None:
    """An unfetched ref namespace looks exactly like a project that never released.

    That is an absence rendered as a value -- the defect class this project grades other
    people on -- so it fails here instead of quietly taking the easy branch. `verify.yml`
    checks out with `fetch-depth: 0`, which fetches `refs/tags/*` explicitly.
    """

    assert TAGS or not is_shallow(), (
        "this checkout is shallow and reports zero tags, which is indistinguishable from a "
        "repository that has never been released. Check out with fetch-depth: 0."
    )


def test_the_checkout_can_see_every_tag_the_remote_has() -> None:
    """The half the local tag list cannot prove about itself.

    A checkout that fetched no tags and a repository that has none are the same empty
    list, and the checks above would take the second reading and pass. The remote settles
    it. A skip here is a visible "not checked", not a pass: it means the remote could not
    be reached, which does not happen in CI.
    """

    published = remote_tags()
    if published is None:
        pytest.skip("the remote could not be reached; only this checkout's tag list is available")

    missing = sorted(set(published) - set(TAGS))
    assert not missing, (
        f"the remote has tag(s) this checkout cannot see: {missing}. A gate reading tags from "
        "this checkout would report 'never released' and pass. Check out with fetch-depth: 0."
    )

    finding = version_against_tags(DECLARED, published)
    assert finding is None, finding


def test_an_untagged_version_says_so_in_a_form_a_tool_can_read() -> None:
    """The prose is for people; this is the half a machine can act on."""

    if TAGS:
        assert not DEV_SUFFIX.search(DECLARED), (
            f"{DECLARED!r} is a developmental version, but {newest_tag(TAGS)!r} is tagged."
        )
    else:
        assert DEV_SUFFIX.search(DECLARED), (
            f"pyproject.toml declares {DECLARED!r} and this repository has no tags, so nothing "
            "was ever built or signed under that number. Keep the PEP 440 '.devN' suffix until "
            "one is; ADR 0001 says the first tag brings the hardened release workflow with it."
        )


def test_every_document_that_restates_the_version_agrees_with_pyproject() -> None:
    findings = restatement_findings(ROOT, DECLARED)
    assert findings == [], findings


def test_the_documents_say_nothing_has_been_released_while_nothing_has() -> None:
    findings = unreleased_statement_findings(ROOT, TAGS) + citation_findings(ROOT, TAGS)
    assert findings == [], findings


def test_the_package_reports_the_version_the_source_tree_declares() -> None:
    """`__version__` is derived from the installed metadata, so it has to agree."""

    assert PACKAGE_VERSION == DECLARED, (
        f"the installed package reports {PACKAGE_VERSION!r} but pyproject.toml declares "
        f"{DECLARED!r} -- re-run `uv sync --locked` so the metadata matches the source tree."
    )
    source = (ROOT / "src" / "disclosed" / "__init__.py").read_text(encoding="utf-8")
    assert f'"{DECLARED}"' not in source, (
        "src/disclosed/__init__.py hard-codes the version; read it from the installed "
        "distribution metadata so there is one source rather than two."
    )


# --- the branches this repository cannot reach ------------------------------------------


def test_a_tag_that_matches_the_declared_version_is_the_passing_case() -> None:
    assert version_against_tags("0.1.0", ["v0.1.0"]) is None
    assert version_against_tags("0.1.0", ["0.1.0"]) is None
    assert version_against_tags("0.2.0", ["v0.1.0", "v0.2.0"]) is None


@pytest.mark.parametrize(
    ("declared", "tags", "newest"),
    [
        ("0.1.0.dev0", ["v0.1.0"], "v0.1.0"),
        ("0.1.0", ["v0.2.0"], "v0.2.0"),
        ("0.3.0", ["v0.1.0", "v0.10.0", "v0.9.0"], "v0.10.0"),
    ],
)
def test_a_declared_version_no_tag_backs_is_a_failure_that_names_both(
    declared: str, tags: list[str], newest: str
) -> None:
    """A gate that cannot fail is not a gate -- ADR 0001's own words -- and this repository
    cannot reach the failing state, so it is driven here with tag lists it does not have."""

    finding = version_against_tags(declared, tags)
    assert finding is not None
    assert repr(declared) in finding
    assert repr(newest) in finding


def test_the_newest_tag_is_chosen_numerically_not_lexically() -> None:
    assert newest_tag(["v0.9.0", "v0.10.0"]) == "v0.10.0"
    assert newest_tag(["v1.0.0", "v0.10.0"]) == "v1.0.0"


# --- negative controls: each check is shown to bite on a sabotaged copy ------------------


@pytest.fixture
def sabotage(tmp_path: Path) -> Path:
    """A copy of the documents these checks read, so a mutation cannot touch the repo."""

    copy = tmp_path / "repo"
    (copy / "docs" / "adr").mkdir(parents=True)
    for name in ("README.md", "SECURITY.md", "CHANGELOG.md", "CITATION.cff", "pyproject.toml"):
        shutil.copy(ROOT / name, copy / name)
    for name in ("docs/ROADMAP.md", "docs/adr/0001-no-versioned-release.md"):
        shutil.copy(ROOT / name, copy / name)
    return copy


def test_the_clean_tree_produces_no_findings_at_all(sabotage: Path) -> None:
    """Without this, every negative control below could pass for the wrong reason."""

    assert declared_version(sabotage) == DECLARED
    assert restatement_findings(sabotage, DECLARED) == []
    assert unreleased_statement_findings(sabotage, []) == []
    assert citation_findings(sabotage, []) == []


@pytest.mark.parametrize(("document", "pattern"), UNRELEASED_STATEMENTS)
def test_deleting_a_release_stance_sentence_is_caught(
    sabotage: Path, document: str, pattern: str
) -> None:
    path = sabotage / document
    before = path.read_text(encoding="utf-8")
    after = re.sub(pattern, "REMOVED", before)
    path.write_text(after, encoding="utf-8")

    assert after != before, f"the sabotage did not land: {pattern!r} never matched {document}"
    assert re.search(pattern, after) is None

    findings = unreleased_statement_findings(sabotage, [])
    assert any(document in finding for finding in findings), findings


def test_a_stance_sentence_left_standing_after_a_release_is_caught(sabotage: Path) -> None:
    """The other direction: the first tag has to retire these sentences."""

    findings = unreleased_statement_findings(sabotage, ["v0.1.0"])
    assert len(findings) == len(UNRELEASED_STATEMENTS)
    assert all("v0.1.0" in finding for finding in findings)


@pytest.mark.parametrize("key", ["version", "date-released"])
def test_a_citation_release_field_without_a_tag_is_caught(sabotage: Path, key: str) -> None:
    """ADR 0001's consequence, reproduced deliberately on a copy."""

    path = sabotage / "CITATION.cff"
    before = path.read_text(encoding="utf-8")
    after = f"{before}{key}: 9.9.9\n"
    path.write_text(after, encoding="utf-8")

    assert re.search(rf"^{key}:", after, re.MULTILINE), "the sabotage did not land"

    assert citation_findings(sabotage, []) != []
    assert citation_findings(sabotage, ["v0.1.0"]) == []


@pytest.mark.parametrize("document", [name for name, _ in RESTATEMENTS])
def test_a_restated_version_drifting_from_pyproject_is_caught(
    sabotage: Path, document: str
) -> None:
    path = sabotage / document
    before = path.read_text(encoding="utf-8")
    after = before.replace(DECLARED, "9.9.9", 1)
    path.write_text(after, encoding="utf-8")

    assert after != before, f"the sabotage did not land: {DECLARED!r} not found in {document}"
    assert "9.9.9" in after

    findings = restatement_findings(sabotage, DECLARED)
    assert any(document in finding and "9.9.9" in finding for finding in findings), findings
