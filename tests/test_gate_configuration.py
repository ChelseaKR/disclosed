"""The gate's own configuration, gated.

Everything else in this repository is checked by something. The README's figures are recomputed
from the committed data (``tests/test_doc_counts.py``), the published prose is recounted from the
archives (``tests/test_published_figures.py``), and the three CI steps whose failure mode is
silence are pinned to the shape that makes them checks (``tests/test_workflows.py``).

The files that decide what any of that runs over were not. Four separate fixes this project
already made, each with its reason written down beside it, could all be undone by a one-line edit
with the whole suite still green. Measured, not reasoned about: on 2026-08-28, with

* ``LINTED`` in the Makefile reduced from ``src tests .github/scripts`` to ``src tests``,
* ``--cov-fail-under=90`` reduced to ``1`` and ``.github/scripts`` dropped from both the coverage
  source and mypy's ``files``,
* ``verify.yml``'s ``uv lock --check`` step deleted and ``uv sync --locked`` returned to
  ``uv sync --frozen``,
* and the accessibility scorer's explicit list of six reports returned to ``ls /tmp/*.json``,

``make verify`` exited 0 with 696 tests passed, exactly as it does on the unmodified tree. Each of
those four is a decision this repository argues for at length in a comment; none of them was
asserted anywhere.

That is the failure this project exists to name, applied to itself one level up. A rule that
stopped applying and a rule that is being satisfied look identical from outside, and the badge is
the same colour either way.

Two things are checked here that are not merely restatements of a file:

* :class:`TestTheLockfileAgreesWithPyprojectWithoutRunningUv` is the drift check that
  ``uv lock --check`` performs in CI, done from the two files alone. ``make verify`` runs no uv,
  so a dependency added to ``pyproject.toml`` and never locked was a local pass and a CI failure,
  which is the local/CI parity the README claims and did not have. This closes the gap in the
  direction that keeps the gate: the suite now fails on the drift rather than the workflow being
  loosened to match the suite.
* :class:`TestTheAccessibilityScorerCannotPassOverNothing` pins the scorer step, which the
  Lighthouse job's own comment says was once a glob that would have exited 0 over zero pages.

This module is deliberately separate from ``tests/test_workflows.py`` rather than added to it:
that module is being rewritten in an open pull request, and a gate that only exists after a merge
conflict is resolved correctly is not one.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent

# The directories this project has decided are inside the gate. `.github/scripts` is the one that
# had to be argued for: `check_site_origin.py` decides whether the published site may name the
# origin it names, and it sat outside the lint, outside strict mypy and outside the coverage
# floor, so the percentage the report printed was a percentage of the code being looked at.
_GATED_DIRECTORIES = ("src", "tests", ".github/scripts")


def _read(name: str) -> str:
    """A configuration file's text, refusing to return nothing.

    Named here so that a rename reads as "the gate's configuration moved" rather than as a broken
    test. Every assertion below guards a decision that is invisible once it is undone.
    """
    path = _ROOT / name
    assert path.is_file(), (
        f"{path} is gone. If it was renamed, bring these assertions with it: each one guards a "
        "gate that reports success without having examined anything when it is removed."
    )
    return path.read_text(encoding="utf-8")


_MAKEFILE = _read("Makefile")
_PYPROJECT: dict[str, Any] = tomllib.loads(_read("pyproject.toml"))
_LOCK: dict[str, Any] = tomllib.loads(_read("uv.lock"))
_VERIFY = _read(".github/workflows/verify.yml")
_ACCESSIBILITY = _read(".github/workflows/accessibility.yml")
_SECURITY = _read(".github/workflows/security.yml")
_PRE_COMMIT = _read(".pre-commit-config.yaml")


def _steps(text: str) -> list[str]:
    """A workflow's executable lines, whitespace-normalized, with the commentary dropped.

    Re-implemented rather than imported from ``tests/test_workflows.py`` on purpose. These files
    argue with themselves in comments that quote the very strings being searched for: the whole
    reason ``uv sync --frozen`` still appears in ``verify.yml`` is the paragraph explaining why it
    is not used, and a search over the raw text would find that paragraph and call it a gate.
    """
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return [line for line in lines if line and not line.startswith("#")]


# -- the Makefile ------------------------------------------------------------------------------


def _prerequisites(target: str) -> list[str]:
    match = re.search(rf"(?m)^{re.escape(target)}:[ \t]*(.*)$", _MAKEFILE)
    assert match is not None, f"the Makefile has no {target!r} target"
    return match.group(1).split()


def _recipe(target: str) -> list[str]:
    """The command lines of one Makefile target, tabs and continuations resolved."""
    match = re.search(rf"(?ms)^{re.escape(target)}:[^\n]*\n((?:\t[^\n]*\n)*)", _MAKEFILE)
    assert match is not None, f"the Makefile has no {target!r} target"
    body = match.group(1).replace("\\\n", " ")
    return [" ".join(line.split()) for line in body.splitlines() if line.strip()]


def _assignment(name: str) -> list[str]:
    match = re.search(rf"(?m)^{re.escape(name)}[ \t]*[:?+]?=[ \t]*(.*)$", _MAKEFILE)
    assert match is not None, f"the Makefile no longer defines {name}"
    return match.group(1).split()


class TestTheLocalGateIsTheThreeChecksItIsDescribedAs:
    """``make verify`` is named in the README, in AGENTS.md and in CONTRIBUTING.md as the single
    local gate. What it consists of is one line in one file and nothing reads it."""

    def test_verify_runs_lint_typecheck_and_test(self) -> None:
        assert _prerequisites("verify") == ["lint", "typecheck", "test"]

    def test_every_gated_directory_is_in_the_lint_set(self) -> None:
        """The Makefile's own comment explains why ``.github/scripts`` is here: "the thing that
        decides whether the published site may name the host it names cannot be the one file
        nobody checks". Dropping it again is a silent edit."""
        linted = _assignment("LINTED")
        missing = [d for d in _GATED_DIRECTORIES if d not in linted]
        assert not missing, (
            f"LINTED no longer names {missing}. A coverage or lint percentage is a percentage of "
            "its denominator, and a directory removed from here leaves the gate without saying so."
        )

    def test_the_lint_step_checks_formatting_over_the_same_set_as_the_rules(self) -> None:
        recipe = _recipe("lint")
        assert any(re.search(r"\bruff check\b.*\$\(LINTED\)", line) for line in recipe), recipe
        assert any(re.search(r"\bruff format --check\b.*\$\(LINTED\)", line) for line in recipe), (
            "`ruff format` without `--check`, or over a narrower set than the rules, rewrites "
            "files instead of failing on them"
        )

    def test_no_gate_recipe_ignores_its_own_exit_status(self) -> None:
        """A leading ``-`` is make's ``|| true``, and it is quieter: the line still looks like a
        command that has to succeed."""
        for target in ("lint", "typecheck", "test"):
            for line in _recipe(target):
                assert not line.startswith("-"), f"{target}: {line!r} ignores its exit status"
                assert "|| true" not in line, f"{target}: {line!r} swallows its result"


# -- pyproject.toml ----------------------------------------------------------------------------


def _addopts() -> str:
    return str(_PYPROJECT["tool"]["pytest"]["ini_options"]["addopts"])


class TestTheMeasuredSetIsTheSetTheDocumentsClaim:
    """The coverage floor, the branch flag, the mypy target list and the ruff rule set.

    Every one of these is stated as a fact in the README's Standards Conformance table, and every
    one of them is a single token in ``pyproject.toml``.
    """

    def test_the_coverage_floor_is_at_least_the_ninety_five_percent_stated(self) -> None:
        match = re.search(r"--cov-fail-under=(\d+)", _addopts())
        assert match is not None, (
            "pytest no longer fails under a coverage floor, so the percentage in the report is a "
            "number printed beside a green check rather than a gate."
        )
        assert int(match.group(1)) >= 95, (
            f"the coverage floor is {match.group(1)}%, below the 95% the README, AGENTS.md and "
            "the Standards Conformance table all state."
        )

    def test_coverage_is_measured_by_branch_and_not_only_by_line(self) -> None:
        assert "--cov-branch" in _addopts(), (
            "branch coverage is what the documents claim; line coverage passes over an untaken "
            "`if` that a branch measure would name."
        )

    def test_coverage_measures_the_gate_scripts_as_well_as_the_package(self) -> None:
        source = _PYPROJECT["tool"]["coverage"]["run"]["source"]
        assert "src/disclosed" in source and ".github/scripts" in source, source

    def test_the_type_check_is_strict_and_reaches_the_gate_scripts(self) -> None:
        mypy = _PYPROJECT["tool"]["mypy"]
        assert mypy["strict"] is True
        assert "src" in mypy["files"] and ".github/scripts" in mypy["files"], mypy["files"]

    def test_the_lint_rules_still_include_the_sets_the_readme_names(self) -> None:
        """The README says "ruff (incl. bandit ``S`` rules, complexity <= 10)" as a fact about
        what runs. Dropping either code from ``select`` leaves the published claim standing
        alone."""
        select = _PYPROJECT["tool"]["ruff"]["lint"]["select"]
        assert "S" in select, "the bandit rules are no longer selected"
        assert "C90" in select, "the complexity rules are no longer selected"
        assert _PYPROJECT["tool"]["ruff"]["lint"]["mccabe"]["max-complexity"] == 10

    def test_the_only_rule_waived_anywhere_is_assert_inside_the_tests(self) -> None:
        """A per-file ignore is the quiet form of a severity floor: the rule still runs, and its
        findings land nowhere. One waiver exists and it is ``S101`` in ``tests``, because a test
        without an assert statement is not a test."""
        ignores = _PYPROJECT["tool"]["ruff"]["lint"]["per-file-ignores"]
        assert ignores == {"tests/*": ["S101"]}, (
            f"per-file-ignores is now {ignores}. Waiving a rule for a path is allowed; doing it "
            "without a written reason is how a scan stops finding things. State the reason and "
            "bring this assertion with it."
        )


# -- the lockfile ------------------------------------------------------------------------------

_REQUIREMENT = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[([^\]]*)\])?\s*(.*)$")


def _canonical(name: str) -> str:
    """PEP 503 normalization, so ``types-defusedxml`` and ``types_defusedxml`` are one name."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _from_pyproject(requirement: str, marker: str = "") -> tuple[str, tuple[str, ...], str, str]:
    match = _REQUIREMENT.match(requirement.strip())
    assert match is not None, f"cannot read the requirement {requirement!r}"
    name, extras, specifier = match.groups()
    parsed = tuple(sorted(_canonical(e) for e in (extras or "").split(",") if e.strip()))
    return _canonical(name), parsed, specifier.strip(), marker


def _from_lock(entry: dict[str, Any]) -> tuple[str, tuple[str, ...], str, str]:
    extras = tuple(sorted(_canonical(e) for e in entry.get("extras", [])))
    return (
        _canonical(str(entry["name"])),
        extras,
        str(entry.get("specifier", "")).strip(),
        str(entry.get("marker", "")),
    )


def _declared() -> set[tuple[str, tuple[str, ...], str, str]]:
    """Every requirement ``pyproject.toml`` declares, in the shape ``uv.lock`` records it."""
    project = _PYPROJECT["project"]
    declared = {_from_pyproject(r) for r in project.get("dependencies", [])}
    for extra, requirements in project.get("optional-dependencies", {}).items():
        declared |= {_from_pyproject(r, f"extra == '{extra}'") for r in requirements}
    for requirements in _PYPROJECT.get("dependency-groups", {}).values():
        declared |= {_from_pyproject(r) for r in requirements}
    return declared


def _locked() -> set[tuple[str, tuple[str, ...], str, str]]:
    """Every requirement ``uv.lock`` records for this project, from its own metadata block."""
    root = next(p for p in _LOCK["package"] if _canonical(str(p["name"])) == "disclosed")
    metadata = root["metadata"]
    entries = list(metadata.get("requires-dist", []))
    for group in metadata.get("requires-dev", {}).values():
        entries += list(group)
    return {_from_lock(e) for e in entries}


class TestTheLockfileAgreesWithPyprojectWithoutRunningUv:
    """The drift check CI runs as ``uv lock --check``, done from the two committed files.

    ``verify.yml`` explains at length that ``uv sync --frozen`` "means install from the lock
    without consulting pyproject.toml at all, so it cannot notice the two disagreeing", and that
    the right flags are ``uv lock --check`` and ``uv sync --locked``. Both of those live only in
    the workflow. ``make verify`` runs neither and has no uv on its path, so the README's claim
    that it is "the single local gate and the same target CI runs" was true of the last step and
    not of the job: a dependency added to ``pyproject.toml`` and left out of ``uv.lock`` passed
    locally and failed in CI, which is the wrong way round for a gate a contributor is told to
    trust.

    Comparing the two files directly needs no network, no uv and no resolver: ``uv.lock`` records
    the project's own requirements verbatim in its ``[package.metadata]`` block, which is the
    thing ``uv lock --check`` compares ``pyproject.toml`` against.
    """

    def test_every_requirement_pyproject_declares_is_recorded_in_the_lockfile(self) -> None:
        unlocked = _declared() - _locked()
        assert not unlocked, (
            f"pyproject.toml declares {sorted(unlocked)} and uv.lock does not record it. "
            "`uv sync --frozen` installs the stale set and says nothing; run `uv lock` and commit "
            "the result in the same change as the dependency."
        )

    def test_the_lockfile_records_no_requirement_pyproject_has_dropped(self) -> None:
        stale = _locked() - _declared()
        assert not stale, (
            f"uv.lock still records {sorted(stale)}, which pyproject.toml no longer declares. "
            "The installed set is wider than the declared one, and pip-audit is auditing the "
            "wider one."
        )

    def test_every_locked_requirement_has_a_package_entry_to_install(self) -> None:
        """A name in the metadata block with no resolved package behind it is a lockfile that
        cannot actually install what it claims to pin."""
        resolved = {_canonical(str(p["name"])) for p in _LOCK["package"]}
        for name, _extras, _specifier, _marker in _locked():
            assert name in resolved, f"uv.lock requires {name!r} and resolves no package for it"


# -- verify.yml --------------------------------------------------------------------------------


class TestTheLockfileGateInCIIsStillTheOneThatCanFail:
    """``verify.yml``'s three install steps, pinned to the flags its own comment measured.

    That comment records the measurement: under uv 0.12.1, on a copy of this project with a
    dependency added to ``pyproject.toml`` and ``uv.lock`` untouched, ``uv lock --check`` and
    ``uv sync --locked`` both exited 1 and ``uv sync --frozen`` exited 0. The argument is written
    down; the flag it is about was asserted nowhere, and ``--frozen`` still appears in the file as
    the thing being argued against, so a search of the raw text finds it either way.
    """

    def test_the_lockfile_is_checked_against_pyproject_before_anything_is_installed(self) -> None:
        steps = _steps(_VERIFY)
        checked = [i for i, line in enumerate(steps) if re.search(r"\buv lock --check\b", line)]
        assert checked, (
            "verify.yml no longer runs `uv lock --check`. Without it nothing in CI compares "
            "uv.lock against pyproject.toml, which is the check the workflow's own comment says "
            "`uv sync --frozen` cannot perform."
        )
        installed = [i for i, line in enumerate(steps) if re.search(r"\buv sync\b", line)]
        assert installed and checked[0] < installed[0], (
            "the lockfile is checked after the install, so the run has already proceeded on the "
            "set it was meant to refuse."
        )

    def test_the_install_is_locked_and_never_frozen(self) -> None:
        for line in _steps(_VERIFY):
            assert "uv sync --frozen" not in line, (
                f"{line!r} installs with --frozen, which does not read pyproject.toml at all and "
                "so cannot notice a dependency that was never locked. Measured on this project: "
                "--frozen exits 0 where --locked exits 1."
            )
        assert any("uv sync --locked" in line for line in _steps(_VERIFY)), (
            "verify.yml no longer installs with --locked, so the tested set is not the pinned set"
        )

    def test_ci_runs_the_local_gate_rather_than_a_second_copy_of_it(self) -> None:
        """Local/CI parity is a published claim. It holds because CI runs the same target, not
        because two lists of commands were kept in step by hand."""
        assert any(re.fullmatch(r"run: make verify", line) for line in _steps(_VERIFY)), (
            "verify.yml no longer runs `make verify`. A second list of commands drifts from the "
            "first without either one failing."
        )

    def test_no_step_in_the_gate_workflow_swallows_its_own_result(self) -> None:
        for line in _steps(_VERIFY):
            assert "|| true" not in line, line
            assert not line.startswith("continue-on-error:"), line


class TestTheSecretScanStillReadsTheHistoryItClaimsToRead:
    """The one property of the gitleaks job that is not visible in its own output.

    ``security.yml`` says "gitleaks scans history for committed secrets", and the checkout above
    it carries ``fetch-depth: 0`` with the comment "gitleaks scans history, not just the tip".
    Without that line ``actions/checkout`` clones one commit, the scan finds nothing because
    there is almost nothing to find, and it reports success in exactly the same words. That is
    the same shape as a severity floor above every finding: the tool ran, the badge is green, and
    the set it examined shrank without anybody being told.

    ``tests/test_workflows.py`` pins the semgrep step's flags and the pip-audit step's, and stops
    short of this one.
    """

    def _job(self) -> list[str]:
        """The secret-scan job's executable lines. Comments are dropped for the reason the whole
        module exists: the word "gitleaks" appears three times in this file's prose, and an
        assertion satisfied by a comment is satisfied by a job that no longer runs the tool."""
        block = _SECURITY.split("secret-scan:", 1)
        assert len(block) == 2, "security.yml no longer has a secret-scan job"
        return _steps(block[1].split("\n  sast:", 1)[0])

    def test_the_secret_scan_checks_out_the_whole_history(self) -> None:
        job = self._job()
        assert any(line.startswith("fetch-depth: 0") for line in job), (
            "the gitleaks job no longer checks out the full history. actions/checkout defaults to "
            "a depth of one, so the scan would examine a single commit and report success over "
            "everything before it."
        )

    def test_the_secret_scan_still_runs_the_scanner(self) -> None:
        assert any(line.startswith("- uses: gitleaks/gitleaks-action@") for line in self._job()), (
            "the secret-scan job no longer runs gitleaks. The job name, the workflow's header "
            "paragraph and the README's Standards Conformance row all still say it does."
        )


# -- accessibility.yml -------------------------------------------------------------------------

# The glob the scorer used to use and the shape of the paths it names instead. Both are strings
# matched against a workflow file, never paths this suite opens, so the rule about hardcoded
# temporary directories is waived at the line rather than loosened anywhere.
_REPORT_GLOB = "/tmp/*.json"  # noqa: S108 -- matched against a workflow, never opened
_REPORT_PATH = r"/tmp/[\w.-]+\.json"  # noqa: S108 -- matched against a workflow, never opened


class TestTheAccessibilityScorerCannotPassOverNothing:
    """The scorer step in the Lighthouse job, pinned to the three properties that make it a gate.

    Its own comment records what it replaced: ``/tmp/*.json`` swept up a file that was not a
    lighthouse report, and, worse, "had lighthouse written nothing, the loop body would never run
    and ``exit "${fail}"`` would exit 0, so a gate that measured zero pages would have reported
    success". The fix is in the file. Nothing asserted it, and it is one edit from being undone.

    Only the scorer is pinned here. The audit steps above it are being rewritten in an open pull
    request, and pinning those would make this module fail on a change that is not a regression.
    """

    def _scorer(self) -> list[str]:
        steps = _steps(_ACCESSIBILITY)
        start = next(
            (i for i, line in enumerate(steps) if "name: Require 100 on accessibility" in line),
            None,
        )
        assert start is not None, (
            "accessibility.yml no longer has a step named 'Require 100 on accessibility'. If it "
            "was renamed, bring these assertions with it: they are the difference between a "
            "scorer and a loop that runs zero times."
        )
        return steps[start:]

    def test_the_reports_are_named_rather_than_globbed(self) -> None:
        scorer = self._scorer()
        for line in scorer:
            assert _REPORT_GLOB not in line, (
                f"{line!r} globs the reports. A glob that matches nothing runs the loop zero "
                "times and exits 0, and a glob that matches too much dies on a file that is not "
                "a lighthouse report after every real page has already scored."
            )
        named = next((line for line in scorer if line.startswith("reports=")), None)
        assert named is not None, "the scorer no longer names the reports it scores"
        assert len(re.findall(_REPORT_PATH, named)) >= 6, named

    def test_a_pass_over_a_partial_set_is_not_a_pass(self) -> None:
        """The count is the whole point: six reports are expected and six have to be found."""
        scorer = " ".join(self._scorer())
        assert re.search(r"expected=\d+", scorer), "the scorer no longer states how many it wants"
        assert re.search(r'\$\{found\}" -ne "\$\{expected\}', scorer), (
            "nothing compares the number of reports scored against the number expected, so a run "
            "that audited one page of six would report success."
        )

    def test_a_missing_score_is_a_failure_and_never_a_default(self) -> None:
        scorer = " ".join(self._scorer())
        assert "'missing' if category is None" in scorer, (
            "a report with no accessibility category no longer scores 'missing'. Defaulting it "
            "to 1.0 turns a broken run into a green check."
        )
        assert '[ "${score}" = "1" ] || fail=1' in scorer
        assert re.search(r'if \[ ! -f "\$\{report\}" \]', scorer), (
            "a report lighthouse never wrote is no longer counted as a failure"
        )


# -- .pre-commit-config.yaml -------------------------------------------------------------------


class TestThePreCommitHooksAreTheGateAndNotAnApproximationOfIt:
    """The hooks are described in their own file as mirroring the CI gates. Two of the three
    claims in that description are checkable from the committed files and neither was checked."""

    def test_the_ruff_hook_pins_the_ruff_the_gate_actually_runs(self) -> None:
        """The file says so itself: "Keep equal to the ruff version resolved in uv.lock so the
        hook and ``make verify`` can never disagree about what is clean. Bump both together." A
        hook a version behind reformats what the gate then rejects, or the reverse."""
        locked = next(p for p in _LOCK["package"] if _canonical(str(p["name"])) == "ruff")
        version = str(locked["version"])
        assert f"rev: v{version}" in _PRE_COMMIT, (
            f"uv.lock resolves ruff {version} and .pre-commit-config.yaml pins a different "
            "revision, so the hook and `make verify` can disagree about what is clean."
        )

    def test_the_hooks_lint_the_same_directories_the_makefile_does(self) -> None:
        patterns = re.findall(r"files: \^\((.*?)\)/", _PRE_COMMIT)
        assert patterns, ".pre-commit-config.yaml no longer scopes the ruff hooks to a path set"
        for pattern in patterns:
            named = {part.replace("\\", "") for part in pattern.split("|")}
            missing = [d for d in _GATED_DIRECTORIES if d not in named]
            assert not missing, f"the ruff hook does not reach {missing}: {pattern!r}"

    def test_the_push_hook_is_the_gate_itself_and_not_a_subset_of_it(self) -> None:
        assert "entry: make verify" in _PRE_COMMIT
        assert "stages: [pre-push]" in _PRE_COMMIT
