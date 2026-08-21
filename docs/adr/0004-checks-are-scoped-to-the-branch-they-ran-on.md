# 0004. Required checks are scoped to the branch that triggered them; commit statuses are not

- Status: Accepted. Supersedes [0003](0003-the-gates-run-on-the-staging-ref.md).
- Date: 2026-08-21
- Deciders: Chelsea Kelly-Reif

## Context

ADR 0003 shipped as PR #26, merged, and was then run for real on `master` (run 32473991532,
2026-08-21) rather than trusted on the strength of its own workflow-shape tests. It failed. It
was rerun once more, immediately, to rule out a fluke, and failed again the same way:

```
remote: - Required status check "verify" is expected.
remote: - 5 of 5 required status checks are expected.
```

Both times, `verify.yml` and `security.yml` had been dispatched on `snapshot/staging`,
`gh run watch --exit-status` had returned zero for both, and `gh api
repos/.../commits/<sha>/check-runs` showed all five required checks (`verify`, `replay`,
`Secret scan (gitleaks)`, `SAST (semgrep)`, `Dependency audit (pip-audit over uv.lock)`) present
on the exact SHA being pushed, `status: completed`, `conclusion: success` — confirmed by querying
the API directly, not inferred from the job's own exit code. The push was refused anyway, both
times, within seconds of the checks completing, which rules out a propagation delay: `gh api
repos/ChelseaKR/disclosed/branches/master/protection` shows classic branch protection's five
required contexts each pinned to `app_id: 15368`, not just a context name. A check run's check
suite belongs to the branch whose event created it. `snapshot/staging` had one; `master` did not,
and GitHub does not treat identical SHAs on two branches as sharing a check suite.

The one thing on record that *did* satisfy a requirement for this SHA was ADR 0002's rejected
approach: a commit status, posted via the legacy Statuses API, satisfied the ruleset's `verify`
requirement (ADR 0003's own context: "the status landed and the push was still refused by a
second protection layer requiring four more checks the job had not run" — refused for what it
didn't cover, not for what it did). Commit statuses carry no branch or check-suite association,
which is exactly why one worked where five real, green, correctly-SHA'd check runs did not.

## Decision

Keep everything ADR 0003 got right: the commit is staged on `snapshot/staging`, `verify.yml` and
`security.yml` are dispatched there for real, and `gh run watch --exit-status` still fails the
job the moment either gate fails. This is genuine, external verification and none of it is
removed.

What changes is how that verdict crosses the branch boundary. Once a dispatched run has been
watched to completion — never before — the job reads back that run's own job list
(`gh api repos/.../actions/runs/<id>/jobs`) and, for each job, posts a commit status on the SHA:
`context` is the job's name (already identical to the required context, because these are the
same jobs a pull request runs), `state` is read from that job's own `conclusion` and the step
refuses to transcribe anything but `success`, and `target_url` links the specific job that
produced it. This is not ADR 0002's mistake repeated: ADR 0002 ran `make verify` inline, on the
snapshot job's own runner, and asserted a status for a check it had graded itself. Here the check
already ran, on GitHub's infrastructure, as its own workflow, and already made GitHub's own
`gh run watch` return non-zero if it disagreed; the status is a receipt quoting that outcome, not
a substitute for it.

## Consequences

- `statuses: write` returns to the job's permissions. `tests/test_workflows.py` no longer asserts
  its absence; it asserts the opposite invariant that actually matters — a status is never posted
  before the run that earned it has been watched to completion, and every status names the run
  and job it came from.
- A dispatched job that reports `gh run watch` success but whose own job list says otherwise
  (a state that should not be reachable, but the workflow does not assume it isn't) fails the
  snapshot job loudly rather than transcribing a status that contradicts the run it is quoting.
- The daily job still takes a few minutes longer than a bare push, unchanged from ADR 0003: the
  gates' real cost, paid for real.
- Neither protection layer on `master` is modified or weakened, and no bypass actor or deploy key
  is introduced. This ADR is about how an already-required check reaches the branch that requires
  it, not about who may skip one.
- The lesson generalises past this workflow: a GitHub Actions check run does not port across
  branches by SHA alone, no matter how convenient that would be for exactly this kind of
  stage-then-promote job. Anything in this project that tries the same trick again needs the same
  transcription step, not a repeat of ADR 0003's untested assumption.
