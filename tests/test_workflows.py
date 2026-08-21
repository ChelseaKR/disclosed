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

The second chapter was quieter still. With the diff fixed, five more runs (2026-08-16 to
2026-08-20) wrote the snapshot, committed it, and were rejected by the ``protect-main`` ruleset
for lacking the ``verify`` status it requires of every commit on master. The job now earns that
status by running ``make verify`` on the commit and recording the result against the SHA before
it pushes (ADR 0002). The tests in :class:`TestTheDailySnapshotEarnsItsCheck` pin the order,
because the honest version and the dishonest version of that step differ only in whether the
gate runs first.
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

    def test_the_post_condition_looks_at_the_remote_and_not_the_runner(self) -> None:
        """Five runs passed ``git ls-files`` and still committed nothing anyone can read: the
        file was tracked on a runner that was discarded minutes later. The series lives on
        origin/master or it does not exist."""
        assert re.search(r"git ls-tree -r --name-only origin/master", _WORKFLOW), (
            "the snapshot workflow no longer checks that the snapshot is on origin/master. A "
            "file tracked only in the runner's clone is the shape of the 2026-08-16 to "
            "2026-08-20 failures: committed, rejected, gone."
        )


class TestTheDailySnapshotEarnsItsCheck:
    """The route by which a bot commit satisfies a ruleset that names no bypass actor.

    The ruleset requires a ``verify`` status on every commit that lands on master. The job runs
    the gate and records the status itself (ADR 0002), and the honest version of that is
    distinguishable from the dishonest one only by order: the gate has to run before the status
    is written, and the status has to be written before the push. Both are asserted from the
    file, the same way the security scans are.
    """

    def _index(self, pattern: str) -> int:
        lines = _steps(_WORKFLOW)
        found = [i for i, line in enumerate(lines) if re.search(pattern, line)]
        assert found, (
            f"snapshot.yml has no executable line matching {pattern!r}. If the step was "
            "rewritten, bring these assertions with it: they guard the only route by which the "
            "daily series reaches master."
        )
        return found[0]

    def test_the_gate_runs_on_the_commit_before_any_status_is_recorded(self) -> None:
        gate = self._index(r"^run: make verify$")
        status = self._index(r"repos/\$\{GITHUB_REPOSITORY\}/statuses/")
        assert gate < status, (
            "the verify status is recorded before `make verify` runs. A status written ahead of "
            "the gate is a green check that proves nothing, which is the failure this project "
            "exists to name in other people's data."
        )

    def test_the_status_is_recorded_before_the_push_and_is_the_context_the_ruleset_names(
        self,
    ) -> None:
        status = self._index(r"repos/\$\{GITHUB_REPOSITORY\}/statuses/")
        pushed = self._index(r"^git push origin HEAD:master$")
        assert status < pushed
        # The `gh api` call is wrapped with backslashes, so each flag is its own line with a
        # continuation marker after it; matched on the flag, not on the whole line.
        window = _steps(_WORKFLOW)[status : pushed + 1]
        assert any(line.startswith("-f context=verify") for line in window), (
            "the recorded status is not the `verify` context the ruleset requires, so the push "
            "will be rejected exactly as it was from 2026-08-16 to 2026-08-20."
        )
        assert any(line.startswith("-f state=success") for line in window)

    def test_the_status_names_the_run_that_earned_it(self) -> None:
        """A status with no target is an assertion. One that links the run is evidence."""
        assert re.search(r"-f target_url=.*GITHUB_RUN_ID", _WORKFLOW)
        assert re.search(r"-f description=.*make verify passed", _WORKFLOW)

    def test_the_staging_ref_is_removed_after_the_push(self) -> None:
        """The staging ref exists only so a SHA can carry a status before master takes it.
        Left behind, it is a second copy of master that nothing protects."""
        pushed = self._index(r"^git push origin HEAD:master$")
        deleted = self._index(r"^git push --delete origin refs/heads/snapshot/staging$")
        assert pushed < deleted

    def test_the_site_is_rebuilt_because_a_token_push_starts_nothing(self) -> None:
        """Without the dispatch the published site lags the series by a day, silently."""
        pushed = self._index(r"^git push origin HEAD:master$")
        rebuilt = self._index(r"^run: gh workflow run publish-site --ref master$")
        assert pushed < rebuilt

    def test_the_gate_is_the_same_install_verify_yml_uses(self) -> None:
        """`make verify` over a different dependency set is a different gate."""
        for command in ("uv lock --check", "uv sync --locked"):
            assert self._index(rf"^run: {re.escape(command)}$") < self._index(r"^run: make verify$")

    def test_the_token_is_scoped_to_exactly_what_the_route_needs(self) -> None:
        """contents to push, statuses to record the check, actions to dispatch the rebuild.
        Anything wider is a write the job does not need and nobody reviewed."""
        job_permissions = re.search(
            r"permissions:\n((?:\s+\w+: \w+.*\n)+)", _WORKFLOW.split("jobs:", 1)[1]
        )
        assert job_permissions is not None
        # Keys read from the start of each line, so a `word: word` inside the trailing comment
        # that explains a grant is not mistaken for a fourth grant.
        granted = dict(re.findall(r"^\s*(\w+): (\w+)", job_permissions.group(1), re.MULTILINE))
        assert granted == {"contents": "write", "statuses": "write", "actions": "write"}


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
