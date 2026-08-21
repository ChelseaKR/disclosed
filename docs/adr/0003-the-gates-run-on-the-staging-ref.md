# 0003. The snapshot commit is checked by the gates master requires, dispatched on a staging ref

- Status: Superseded by [0004](0004-checks-are-scoped-to-the-branch-they-ran-on.md). Merged as
  PR #26, then run for real (32473991532) and rejected twice the same way ADR 0002 was: all five
  checks green on the SHA via the API, push refused anyway. A check run's check suite is scoped
  to the branch that triggered it, which this ADR did not know and did not test against GitHub
  itself.
- Date: 2026-08-21
- Deciders: Chelsea Kelly-Reif

## Context

ADR 0002 had the daily snapshot job run `make verify` on its own commit and record a `verify`
commit status on the SHA before pushing to `master`. It was tried once, on 2026-08-21 (run
32466145742), and it did what it said: the status landed on the commit and the ruleset's one
requirement was met. The push was still refused, with `GH006` rather than `GH013`: `master` is
also protected by **classic branch protection**, which requires five checks from the Actions
app on every commit, `strict`, with `enforce_admins` on:

- `verify` and `replay`, the two jobs of `verify.yml`
- `Secret scan (gitleaks)`, `SAST (semgrep)` and `Dependency audit (pip-audit over uv.lock)`,
  the three jobs of `security.yml`

The response said so plainly: "4 of 5 required status checks are expected". The job could have
recorded the other four the same way. It would have been recording results for scans it had not
run, which is the failure this project names in other people's data, and it is not done.

## Decision

The job puts the commit where the real gates can reach it. After committing it pushes the SHA to
an unprotected staging ref (`snapshot/staging`), dispatches `verify.yml` and `security.yml` on
that ref with `gh workflow run` (`workflow_dispatch` is the one event `GITHUB_TOKEN` is allowed
to start), finds the two runs whose `headSha` is the commit, and waits on each with
`gh run watch --exit-status`. Both workflows gain a `workflow_dispatch` trigger for this. Only
when both have passed does the job fast-forward `master`, delete the staging ref, and dispatch
`publish-site`. The job records no status and needs no `statuses` permission; the gates record
their own results, or the push fails.

With the gates run by their own workflows the job no longer installs `uv` or runs the suite
itself. It walks the API once with provenance (`disclosed fetch`), keeps the raw capture as a
ninety-day workflow artifact, commits the provenance summary beside the snapshot, grades the
capture from the file with no key in the environment, and refuses to take a snapshot unless that
replay comes out `national`.

## Consequences

- Every bot commit on `master` carries the same five checks a pull request would, run on that
  exact SHA by the same workflows. A reader can open the commit and follow each check to its run.
- The job takes a few minutes longer, which is the gates' real cost and was always going to be
  paid by somebody.
- A gate that fails, or never starts, fails the job before anything reaches `master`. The day is
  lost loudly.
- Neither protection on `master` is modified or weakened, and no credential is minted. A deploy
  key as a bypass actor remains the simpler route if the maintainer chooses to create one, and
  would supersede this ADR as it would have superseded 0002.
- `tests/test_workflows.py` pins the order of the steps and asserts that no step in the job
  records a status or a check run.
