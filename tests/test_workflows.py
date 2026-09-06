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
2026-08-20) wrote the snapshot, committed it, and were rejected by master's protections for
lacking the checks they require of every commit. A sixteenth run recorded a ``verify`` status
on its own commit and was rejected for lacking the other four (ADR 0002).

The third chapter is the one that looked finished and was not. ADR 0003 dispatched the real gate
workflows on a staging ref, watched them with ``gh run watch --exit-status``, and only then
pushed -- no self-recorded status anywhere, every check earned by the workflow that owns it. It
merged as PR #26 with all of that pinned by tests and green in CI, and was rejected on its first
real run (32473991532) and its first rerun, both times seconds after ``gh api
.../check-runs`` showed all five checks green on the exact SHA being pushed. A check run's check
suite is scoped to the branch that triggered it; ``snapshot/staging`` having five green checks on
a SHA says nothing to a requirement bound to ``master``, however identical the SHA. Commit
statuses carry no such scoping, which is why the sixteenth run's single self-recorded status
worked when five real, correctly-SHA'd check runs did not (ADR 0004).

So the job keeps the dispatch-and-watch from ADR 0003 -- nothing here grades anything locally,
and a failing gate still fails the job before anything is pushed -- and adds back a status per
job, posted only after that job's own run has been watched to completion, quoting the run that
earned it. :class:`TestTheDailySnapshotIsGatedByTheRealChecks` pins that the status always
follows the watch it is transcribing and never substitutes for it.
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
_VERIFY = _read("verify.yml")
_ACCESSIBILITY = _read("accessibility.yml")


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


class TestTheDailySnapshotIsGatedByTheRealChecks:
    """The route by which a bot commit satisfies protections that name no bypass actor.

    master requires five checks from the Actions app on every commit: ``verify`` and ``replay``
    from verify.yml and the three scans in security.yml, each pinned to a specific app id. The
    job stages the commit on an unprotected ref, dispatches those two workflows on it, and waits
    for them with ``gh run watch --exit-status`` -- none of that is graded locally, and a failing
    gate still fails the job before anything is pushed. Run 32466145742 tried recording a status
    for a gate it ran itself and was refused for the four checks it had not run, which is the
    right outcome. Run 32473991532, dispatched and watched exactly as this class expects, was
    *also* refused, twice, with all five checks verifiably green on the SHA via the API: a check
    run's check suite belongs to the branch that triggered it, and ``snapshot/staging`` having
    five green checks says nothing to a requirement bound to ``master`` (ADR 0004). So the job
    transcribes each dispatched job's already-earned result to a commit status -- which carries
    no such branch scoping -- and the tests below pin that this only ever happens *after* the run
    producing that result has been watched to completion, never as a substitute for it.
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

    def test_a_status_is_posted_only_after_its_run_is_watched_to_completion(self) -> None:
        """The transcription step, not a self-graded assertion: nothing is posted about a run
        that has not already been watched to a successful exit."""
        watched = self._index(r'^gh run watch "\$\{run_id\}" --exit-status$')
        posted = self._index(r'--method POST "repos/\$\{GITHUB_REPOSITORY\}/statuses/\$\{sha\}"')
        assert watched < posted, (
            "a status is posted before the dispatched run it is supposed to be quoting has been "
            "watched to completion, which makes it an assertion rather than a receipt."
        )

    def test_a_status_is_never_posted_for_a_job_that_did_not_itself_succeed(self) -> None:
        """`gh run watch` exiting zero is the workflow's verdict, not each job's. This is the
        second check, against the job list the dispatched run actually reported."""
        lines = _steps(_WORKFLOW)
        guard = next(
            i for i, line in enumerate(lines) if 'if [ "${job_conclusion}" != "success" ]' in line
        )
        assert any("exit 1" in line for line in lines[guard : guard + 6]), (
            "a job whose own conclusion is not success can still reach the status-posting line "
            "below this guard."
        )
        posted = self._index(r'--method POST "repos/\$\{GITHUB_REPOSITORY\}/statuses/\$\{sha\}"')
        assert guard < posted

    def test_the_status_context_is_the_jobs_own_name_not_a_hardcoded_guess(self) -> None:
        """The five required contexts are exactly the five job names verify.yml and security.yml
        already carry. Reading the name back from the dispatched run's own job list, rather than
        retyping it here, is what keeps the two in sync if a job is ever renamed."""
        assert re.search(r'-f context="\$\{job_name\}"', _WORKFLOW)
        assert re.search(r"\.jobs\[\] \| \[\.name, \.html_url, \.conclusion\]", _WORKFLOW)

    def test_the_status_names_the_run_and_job_that_earned_it(self) -> None:
        """A status with no target is an assertion. One that links the specific job that produced
        it is evidence a reader can open and check for themselves."""
        assert re.search(r'-f target_url="\$\{job_url\}"', _WORKFLOW)
        assert re.search(
            r'-f description="passed in \$\{workflow\} run \$\{run_id\}, watched to completion"',
            _WORKFLOW,
        )

    def test_the_job_list_read_back_is_the_run_that_was_just_watched(self) -> None:
        """The jobs transcribed have to belong to the exact dispatched run this iteration
        waited on, not some other run of the same workflow."""
        assert re.search(
            r'repos/\$\{GITHUB_REPOSITORY\}/actions/runs/\$\{run_id\}/jobs"', _WORKFLOW
        )

    def test_the_commit_is_staged_before_the_gates_are_dispatched(self) -> None:
        staged = self._index(r"^run: git push --force origin HEAD:refs/heads/snapshot/staging$")
        dispatched = self._index(r'^gh workflow run "\$\{workflow\}" --ref snapshot/staging$')
        assert staged < dispatched, (
            "the gates are dispatched before the commit exists on the remote, so their check "
            "runs cannot land on it."
        )

    def test_the_gates_dispatched_are_the_ones_master_requires(self) -> None:
        """verify.yml carries `verify` and `replay`; security.yml carries the three scans. Those
        five are what classic branch protection on master names, pinned to the Actions app."""
        loops = [line for line in _steps(_WORKFLOW) if line.startswith("for workflow in ")]
        assert loops, "the dispatch loop is gone"
        for loop in loops:
            assert "verify.yml" in loop and "security.yml" in loop, loop

    def test_the_job_waits_for_the_gates_and_fails_with_them(self) -> None:
        watched = self._index(r'^gh run watch "\$\{run_id\}" --exit-status$')
        pushed = self._index(r"^git push origin HEAD:master$")
        assert watched < pushed, (
            "master is pushed before the dispatched gates have finished, so a failing gate "
            "would be discovered after the commit landed rather than before."
        )

    def test_the_runs_watched_are_the_ones_on_this_commit(self) -> None:
        """A stale staging ref from an earlier day has its own runs. Matching on the SHA keeps
        the job from waiting on, and trusting, somebody else's green."""
        assert re.search(r'select\(\.headSha == \\"\$\{sha\}\\"\)', _WORKFLOW)

    def test_a_gate_that_never_starts_fails_the_job_rather_than_being_skipped(self) -> None:
        lines = _steps(_WORKFLOW)
        missing = next(i for i, line in enumerate(lines) if 'if [ -z "${run_id}" ]; then' in line)
        assert any("exit 1" in line for line in lines[missing : missing + 6])

    def test_both_gate_workflows_accept_a_dispatch(self) -> None:
        """Without `workflow_dispatch:` in their triggers the dispatch above is a 422 and the
        job fails before anything is pushed, which is at least loud. This makes it unnecessary."""
        for name, text in (("verify.yml", _VERIFY), ("security.yml", _SECURITY)):
            assert "workflow_dispatch:" in _steps(text), f"{name} cannot be dispatched"

    def test_the_staging_ref_is_removed_after_the_push(self) -> None:
        """The staging ref exists only so a SHA can carry checks before master takes it.
        Left behind, it is a second copy of master that nothing protects."""
        pushed = self._index(r"^git push origin HEAD:master$")
        deleted = self._index(r"^git push --delete origin refs/heads/snapshot/staging$")
        assert pushed < deleted

    def test_the_site_is_rebuilt_because_a_token_push_starts_nothing(self) -> None:
        """Without the dispatch the published site lags the series by a day, silently."""
        pushed = self._index(r"^git push origin HEAD:master$")
        rebuilt = self._index(r"^run: gh workflow run publish-site --ref master$")
        assert pushed < rebuilt

    def test_the_token_is_scoped_to_exactly_what_the_route_needs(self) -> None:
        """contents to push, actions to dispatch the gates and the rebuild, statuses to
        transcribe a job's already-earned result (ADR 0004), issues to file a systemic or
        unmeasurable drift. Anything wider is a write the job does not need and nobody
        reviewed.

        Deliberately an equality and not a subset check: the point is that widening the token
        has to be done here, in a diff someone reads, rather than by adding a line to the
        workflow and finding the test still green.
        """
        job_permissions = re.search(
            r"permissions:\n((?:\s+\w+: \w+.*\n)+)", _WORKFLOW.split("jobs:", 1)[1]
        )
        assert job_permissions is not None
        # Keys read from the start of each line, so a `word: word` inside the trailing comment
        # that explains a grant is not mistaken for another grant.
        granted = dict(re.findall(r"^\s*(\w+): (\w+)", job_permissions.group(1), re.MULTILINE))
        assert granted == {
            "contents": "write",
            "actions": "write",
            "statuses": "write",
            "issues": "write",
        }


class TestTheDailySnapshotGradesACaptureItCanProve:
    """The walk is the only keyed step; everything after it reads the file it wrote.

    The capture carries the provenance of every page and the snapshot's sidecar carries the
    summary, so a drift finding can be traced to the bytes it was computed from. The grade has
    to come out national from the file alone, with no key in the environment: a snapshot of a
    slice would read as a nationwide collapse in reporting.
    """

    def _index(self, pattern: str) -> int:
        lines = _steps(_WORKFLOW)
        found = [i for i, line in enumerate(lines) if re.search(pattern, line)]
        assert found, f"snapshot.yml has no executable line matching {pattern!r}"
        return found[0]

    def test_the_walk_precedes_the_grade_and_the_grade_reads_the_file(self) -> None:
        fetched = self._index(r"^python -m disclosed\.cli fetch \\$")
        graded = self._index(
            r"^python -m disclosed\.cli grade --source /tmp/capture\.json --out report\.json$"
        )
        assert fetched < graded

    def test_the_provenance_sidecar_is_committed_beside_the_snapshot(self) -> None:
        """One directory down, so the drift step's glob over the snapshots cannot pick it up."""
        assert re.search(
            r'--provenance-out "data/snapshots/scorecard/provenance/\$\{taken\}\.json"', _WORKFLOW
        )
        assert re.search(r"ls data/snapshots/scorecard/\*\.json", _WORKFLOW)

    def test_a_capture_that_does_not_replay_as_national_takes_no_snapshot(self) -> None:
        lines = _steps(_WORKFLOW)
        guard = 'if [ "${kind}" != "national" ]'
        checked = next(i for i, line in enumerate(lines) if guard in line)
        assert any("exit 1" in line for line in lines[checked : checked + 6])
        assert checked < self._index(r"^python -m disclosed\.cli snapshot \\$")

    def test_the_raw_capture_is_kept_and_its_absence_is_an_error(self) -> None:
        lines = _steps(_WORKFLOW)
        kept = next(i for i, line in enumerate(lines) if "uses: actions/upload-artifact@" in line)
        block = lines[kept : kept + 6]
        assert "retention-days: 90" in block
        assert "if-no-files-found: error" in block
        assert "path: /tmp/capture.json" in block


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


# The two reports the timing gate reads, named as accessibility.yml names them. These are
# strings matched against a workflow file, not paths this suite opens, so the hardcoded-tmp rule
# is waived at the line rather than by loosening it anywhere.
_TIMED_REPORTS = ("/tmp/home.json", "/tmp/CA.json")  # noqa: S108 -- matched, never opened


class TestTheTimingBudgetIsActuallyEnforced:
    """The timing gate ``docs/adr/0010`` added, pinned to the shape that makes it one.

    The gate lives in ``.github/scripts/check_lighthouse_timings.py``, which
    ``tests/test_lighthouse_timings.py`` exercises directly. What that file cannot see is the
    workflow: the script is only a gate while the job runs it, and only measures anything while
    the reports it is given were audited with the performance category. Lighthouse collects no
    timings without it, and the script would then report three missing metrics rather than
    passing, so this is checked here as well as there. Both halves are needed and they break
    independently.
    """

    def _steps(self) -> list[str]:
        return _steps(_ACCESSIBILITY)

    def _index(self, pattern: str) -> int:
        for position, line in enumerate(self._steps()):
            if re.search(pattern, line):
                return position
        raise AssertionError(
            f"accessibility.yml has no step matching {pattern!r}. Every assertion in this class "
            "guards a step that stops enforcing the timing budget when it moves."
        )

    def test_the_gate_runs_the_script_over_the_budget_file(self) -> None:
        lines = self._steps()
        position = self._index(r"check_lighthouse_timings\.py")
        joined = " ".join(lines[position : position + 3])
        assert "lighthouse-budget.json" in joined, (
            "the timing gate no longer reads the budget file, so it is checking numbers that "
            "live somewhere the README does not point at"
        )
        for report in _TIMED_REPORTS:
            assert report in joined, f"the timing gate no longer checks {report}"

    def test_the_pages_it_gates_are_audited_with_the_performance_category(self) -> None:
        """Without it lighthouse writes a report with no timings in it at all.

        The script fails on a missing metric rather than passing, so this cannot silently
        disable the gate; it can only turn it into a red build for a confusing reason. Pinning
        it here makes the reason obvious in the file rather than in a CI log.
        """
        text = " ".join(self._steps())
        for page in _TIMED_REPORTS:
            head = text.split(page)[0]
            audit = head[head.rindex("npx --yes lighthouse@12") :]
            tail = text.split(page)[1].split("npx --yes lighthouse@12")[0]
            assert "performance" in audit + tail, (
                f"the audit that writes {page} no longer asks for the performance category, so "
                "the report it writes carries no timing metrics for the gate to read"
            )

    def test_the_gate_follows_the_audit_that_produces_what_it_reads(self) -> None:
        assert self._index(r"npx --yes lighthouse@12") < self._index(r"check_lighthouse_timings")

    def test_the_gate_is_not_softened(self) -> None:
        """``|| true`` on this line would restore the state ADR 0008 found and named."""
        position = self._index(r"check_lighthouse_timings\.py")
        for line in self._steps()[position : position + 3]:
            assert "|| true" not in line
            assert "continue-on-error" not in line


class TestASystemicDriftIsDelivered:
    """The snapshot's whole purpose is to notice a policy change in federal disclosure. Until
    the filing step existed, noticing meant writing it into a job summary on a run that was
    green because the fetch worked, which is the most reassuring possible way of saying
    nothing."""

    def test_the_drift_comparison_is_written_somewhere_a_script_can_read(self) -> None:
        assert "drift --json" in _WORKFLOW, (
            "the workflow no longer produces a machine-readable comparison, so nothing can "
            "decide whether the day's finding was worth telling anybody about."
        )

    def test_a_step_files_the_finding(self) -> None:
        assert "drift_issue.py" in _WORKFLOW, (
            "the step that turns a systemic drift into an issue is gone. Without it the "
            "finding stays in a job summary and the run reports success either way."
        )

    def test_the_filing_step_runs_only_when_a_comparison_happened(self) -> None:
        """The first snapshot of a source has nothing to compare against, and filing a drift
        issue from a comparison that did not happen would be an absence rendered as a finding."""
        assert "steps.drift.outputs.compared == 'true'" in _WORKFLOW

    def test_the_finding_is_filed_before_the_commit_is_pushed(self) -> None:
        """So a failure to file fails the job, rather than being hidden behind a successful
        push that makes the run look finished."""
        filed = _WORKFLOW.index("drift_issue.py")
        pushed = _WORKFLOW.index("git push")
        assert filed < pushed

    def test_the_job_may_write_issues(self) -> None:
        assert re.search(r"^\s*issues: write", _WORKFLOW, re.MULTILINE)
