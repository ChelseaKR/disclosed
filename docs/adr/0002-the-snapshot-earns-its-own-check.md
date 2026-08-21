# 0002. The daily snapshot earns the status check it needs, and records where it earned it

- Status: Superseded by [0003](0003-the-gates-run-on-the-staging-ref.md). Tried once (run
  32466145742); the status landed and the push was still refused by a second protection layer
  requiring four more checks the job had not run.
- Date: 2026-08-21
- Deciders: Chelsea Kelly-Reif

## Context

The `protect-main` ruleset on `master` requires a passing `verify` status on every commit that
lands there, forbids non-fast-forward pushes and deletion, and names no bypass actors. It is the
right ruleset and it stays.

The daily snapshot workflow (`.github/workflows/snapshot.yml`) exists to put one small file a day
on `master`. Between 2026-08-06 and 2026-08-15 it committed nothing, because it asked `git diff`
whether an untracked file had changed (fixed in #18). Between 2026-08-16 and 2026-08-20 it wrote
the snapshot, committed it, and had the push rejected by the ruleset: "Required status check
'verify' is expected." Five graded days, none recoverable, and every drift claim the project
makes still had no daily series behind it.

Three routes were considered:

1. **Add the GitHub Actions app as a bypass actor.** The API refuses on a user-owned repository:
   `PUT /repos/{owner}/{repo}/rulesets/{id}` with `{"actor_type": "Integration", "actor_id":
   15368}` answers HTTP 422, "Actor GitHub Actions integration must be part of the ruleset source
   or owner organization". Closed.
2. **A deploy key with write access as the bypass actor.** Works on user-owned repositories and
   has the advantage that a deploy-key push fires the other workflows. It requires minting a
   write credential and storing its private half as an Actions secret. Not done in this change;
   it remains the cleaner option if the maintainer chooses to create the key, and adopting it
   would supersede this ADR.
3. **Open a pull request from a bot branch and auto-merge it.** A pull request opened with
   `GITHUB_TOKEN` never acquires the `verify` check, because nothing that token does starts
   another workflow run. Closed.

## Decision

The job earns the check. After committing the snapshot it runs `make verify` verbatim, the same
target `verify.yml` runs on every pull request, against the commit it is about to push. It then
pushes that commit to an unprotected staging ref (`snapshot/staging`) so the SHA exists on the
remote, records a `verify` commit status on the SHA whose description and `target_url` name the
workflow run that produced it, fast-forwards `master` to the SHA, deletes the staging ref, and
dispatches `publish-site` so the site reflects the new series. The ruleset pins no integration
for the `verify` context, so the status satisfies it; the ruleset itself is not modified.

## Consequences

- Every bot commit on `master` carries a `verify` status pointing at the run whose log shows the
  gate passing on that exact commit. A reader can follow it.
- A snapshot that fails the gate never reaches `master` and never gets a status; the day is lost
  loudly rather than committed quietly. That is the intended trade.
- The status is attested by the snapshot job rather than by `verify.yml`. The two run the same
  Makefile target over the same locked dependency set, and `tests/test_workflows.py` pins that
  the gate runs before the status is recorded and that the status context is exactly `verify`.
  Rewriting the step to record success without running the gate fails the suite.
- `GITHUB_TOKEN` pushes start no workflows, so the site rebuild is an explicit dispatch rather
  than a side effect. If the dispatch is ever removed, the site silently lags the series by a
  day; the same test file asserts the dispatch is present.
- The fifteen days before this change are recorded, not recovered: the graded reports from those
  runs were written to runners that no longer exist, and a snapshot reconstructed from the job
  logs' printed fail counts would be a number invented from a partial record.
