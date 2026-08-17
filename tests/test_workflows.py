"""The CI steps whose failure mode is silence, checked from the workflow files themselves.

Most of this project's gates announce themselves: a test fails, a score is not 100, a fetch
raises rather than returning short. The steps below are the ones that do not. Each of them can
report success without having examined anything, each did so at some point, and none of them
looks wrong in review. That is the whole reason they are asserted here rather than trusted.

The daily snapshot commit is the founding case. It runs on a
schedule nobody watches, its whole job is a side effect on the repository, and when it does
nothing it says so in a sentence that reads like good news.

It said that sentence ten times. Between 2026-08-06 and 2026-08-15 the workflow graded 6,273
institutions on every run, wrote a snapshot, and then decided there was nothing to commit,
because it asked ``git diff -- data/snapshots/`` whether the tree had changed. ``git diff``
compares the index against the working tree, and a brand-new untracked file is in neither, so
a new snapshot is invisible to it. Every one of those runs was green. Not one snapshot reached
the history, and the drift step reported "First Scorecard snapshot; nothing to compare against
yet" every single day.

The bug is a one-word fix and completely invisible in review, which is exactly why it is worth
a test. This asserts the two properties that make the step honest: it compares something a new
file can fail, and it checks afterwards that the commit actually happened.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_WORKFLOWS = _ROOT / ".github" / "workflows"


def _read(name: str) -> str:
    """A workflow's text, refusing to return nothing.

    A missing file would make every ``in``-style assertion below fail loudly, but a *renamed*
    one would not: the read would raise from inside a test and read as a broken test rather than
    as a missing gate. Naming the file here says which it is.
    """
    path = _WORKFLOWS / name
    assert path.is_file(), (
        f"{path} is gone. If the workflow was renamed, bring these assertions with it: every one "
        "of them guards a step that reports success without having checked anything when it "
        "breaks."
    )
    return path.read_text(encoding="utf-8")


_WORKFLOW = _read("snapshot.yml")
_SECURITY = _read("security.yml")
_PAGES = _read("pages.yml")


def _flat(text: str) -> list[str]:
    """Every line of a workflow, whitespace-normalized so a wrapped command still matches."""
    return [" ".join(line.split()) for line in text.splitlines()]


def _steps(text: str) -> list[str]:
    """The executable lines only, with the commentary dropped.

    These files argue with themselves at length in comments, and the arguments quote the very
    strings being searched for: ``security.yml`` explains why no step may end in ``|| true``,
    and ``pages.yml`` names ``check_site_origin.py`` in its header thirty lines above the step
    that runs it. Searching the raw text finds the prose and calls it a gate.
    """
    return [line for line in _flat(text) if line and not line.startswith("#")]


def _commands() -> list[str]:
    """Every git invocation in the workflow, whitespace-normalized, in order."""
    return [" ".join(line.split()) for line in _WORKFLOW.splitlines() if " git " in f" {line} "]


class TestTheDailySnapshotCommitCanFail:
    def test_the_change_check_compares_the_index_and_not_the_working_tree(self) -> None:
        """``git diff`` without ``--cached`` cannot see an untracked file, and every daily
        snapshot is an untracked file. A check that can only return "unchanged" is not a check.
        """
        diffs = [c for c in _commands() if re.search(r"\bgit diff\b", c)]
        assert diffs, (
            "the snapshot workflow no longer compares anything before committing. If the step "
            "was rewritten, bring this test with it rather than deleting it: the failure this "
            "guards against was silent for ten consecutive green runs."
        )
        for command in diffs:
            assert "--cached" in command, (
                f"{command!r} compares the working tree against the index. A new snapshot is "
                "untracked, so it appears in neither and this reports 'unchanged' forever. "
                "Stage first and compare the index against HEAD with --cached."
            )

    def test_the_snapshot_is_staged_before_it_is_compared(self) -> None:
        """Order matters as much as the flag: ``--cached`` against an unstaged new file is just
        as blind. The ``git add`` has to come first."""
        sequence = _commands()
        added = next(i for i, c in enumerate(sequence) if re.search(r"\bgit add\b", c))
        compared = next(i for i, c in enumerate(sequence) if re.search(r"\bgit diff\b", c))
        assert added < compared, (
            "the workflow compares data/snapshots/ before staging it, so a new snapshot file is "
            "still untracked at the moment of the comparison and the step decides there is "
            "nothing to commit."
        )

    def test_the_step_proves_the_snapshot_reached_the_history(self) -> None:
        """The post-condition. Everything above is about one flag; this is the check that does
        not care how the commit was attempted, only whether the file is in the repository
        afterwards. It is the property the daily series actually needs."""
        assert re.search(r"git ls-files --error-unmatch", _WORKFLOW), (
            "nothing in the snapshot workflow verifies that the snapshot it wrote is tracked. "
            "Without that, any future rewrite of the commit step can go back to reporting "
            "success while committing nothing, which is what it did for ten days."
        )


class TestTheSecurityScansCanFail:
    """The three scans in ``security.yml``, checked for the ways a scan reports nothing.

    The header of that file says the three gates may not be silenced, and names ``|| true`` as
    the way it would happen. It is not the only way, and it is the loud one. Two quieter ones
    were both live here at once:

    * **A severity floor above every finding the scan has.** ``--severity=ERROR`` was on the
      semgrep step, described as rigour. Measured under the pinned semgrep 1.169.0, that flag
      ran 141 rules and found nothing; without it the same command ran 321 rules and found
      three, all of them ``WARNING`` -- the three ``urllib.request.urlopen`` calls in the two
      adapters, which is the taint-shaped class the job exists for. The floor sat above every
      finding the scan had ever produced on this source, so the step could not fail.
    * **Targets named on the command line and dropped before the scan.** semgrep applies its
      own default ignore list when a project supplies none, and that list excludes test
      directories. ``semgrep scan ... src tests`` reported "Targets scanned: 15" and "Files
      matching .semgrepignore patterns: 15": all fifteen test modules skipped while the command
      line named them. A repository-level ``.semgrepignore`` that excludes nothing takes the
      count to 30.

    Both are asserted from the file rather than by running semgrep, which needs a network and a
    hundred megabytes. That is the same trade the snapshot tests above make.
    """

    def _semgrep(self) -> str:
        """The semgrep invocation, joined across its line continuations."""
        joined = re.sub(r"\\\s*\n\s*", " ", _SECURITY)
        found = [line for line in _steps(joined) if re.search(r"\bsemgrep scan\b", line)]
        assert len(found) == 1, (
            f"expected exactly one `semgrep scan` invocation in security.yml, found {found}. "
            "If the step was rewritten, bring these assertions with it."
        )
        return found[0]

    def test_the_sast_scan_does_not_filter_out_every_finding_it_has(self) -> None:
        """A floor at ERROR made this step incapable of failing on this repository's own code."""
        command = self._semgrep()
        assert "--severity" not in command, (
            f"{command!r} filters findings by severity. Every semgrep finding on this source has "
            "been a WARNING, so a floor at ERROR runs 141 rules instead of 321 and reports "
            "success over three real findings. If a floor is genuinely wanted, name the findings "
            "it is meant to drop and waive them at their lines instead."
        )

    def test_the_sast_scan_fails_the_job_rather_than_reporting_findings_and_passing(self) -> None:
        """``--error`` is what turns a finding into a non-zero exit. Without it semgrep prints
        the findings and exits 0, which is a scan with a report and no gate."""
        assert "--error" in self._semgrep()

    def test_the_scan_reaches_the_directories_the_command_line_names(self) -> None:
        """``tests`` was an argument to a scan that never opened it."""
        command = self._semgrep()
        named = [target for target in ("src", "tests") if re.search(rf"\s{target}\b", command)]
        assert named == ["src", "tests"], command
        ignore = _ROOT / ".semgrepignore"
        assert ignore.is_file(), (
            "no .semgrepignore at the repository root, so semgrep falls back to its bundled "
            "default ignore list, which excludes test directories. The scan would then skip "
            "every file under tests/ while the command line above still names it."
        )
        patterns = [
            line.strip()
            for line in ignore.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        assert not patterns, (
            f".semgrepignore now excludes {patterns}. Exclusions are allowed, but a pattern that "
            "silently drops one of the targets named on the command line is how this gate "
            "stopped scanning tests/ in the first place. State the reason in the file."
        )

    def test_no_security_step_is_allowed_to_swallow_its_own_result(self) -> None:
        """The failure mode the file's own header names, asserted rather than promised."""
        for line in _steps(_SECURITY):
            assert "|| true" not in line, line
            assert not line.startswith("continue-on-error:"), line
        assert "--strict" in _SECURITY, (
            "pip-audit dropped --strict, so a dependency it could not resolve becomes a warning "
            "rather than a failure."
        )
        assert "--locked" in _SECURITY, (
            "the audited requirements are no longer exported with --locked, so a lockfile that "
            "has drifted from pyproject.toml would be audited clean while the set anyone "
            "installs went unexamined."
        )


class TestThePublishedSiteCannotNameAnOriginNobodyConfirmed:
    """``pages.yml`` renders the canonical links, the sitemap and robots.txt from the deploy
    target the Pages API reports, and ``check_site_origin.py`` refuses the build when they
    disagree. Issue #2 was 616 canonical links naming a host that served a 404, and the reason
    it survived is that nothing in the build compared the two.

    Nothing asserted that the comparison was still wired in, which is the same exposure the
    snapshot post-condition closed one workflow over: the guard is one deleted line away from
    being gone, and its absence looks exactly like its success.
    """

    def test_the_render_is_followed_by_the_origin_check(self) -> None:
        lines = _steps(_PAGES)
        rendered = next(i for i, line in enumerate(lines) if "disclosed.cli site" in line)
        checked = next((i for i, line in enumerate(lines) if "check_site_origin.py" in line), None)
        assert checked is not None, (
            "pages.yml no longer runs .github/scripts/check_site_origin.py. Without it nothing "
            "compares the origin stamped into 616 canonical links against the origin Pages "
            "actually serves, which is issue #2 exactly."
        )
        assert rendered < checked, "the site is checked before it is rendered"

    def test_the_build_refuses_an_unconfirmed_deploy_target(self) -> None:
        """An empty base URL must stop the build rather than stamp an empty origin into every
        canonical link, which would produce 616 pages self-canonicalising to ``/``."""
        assert 'if [ -z "$base_url" ]; then' in _PAGES
        assert "Refusing to publish a site that names an origin nobody" in _PAGES

    def test_a_render_that_produced_nothing_is_not_published(self) -> None:
        assert "test -s site/index.html" in _PAGES
