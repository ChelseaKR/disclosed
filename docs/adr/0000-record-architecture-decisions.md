# 0000. Record architecture decisions

- Status: Accepted
- Date: 2026-08-07
- Deciders: Chelsea Kelly-Reif

## Context

This project makes judgement calls that a reader is invited to dispute: credible ranges, the 2%
drift threshold, the decision to never resolve the Grand Canyon University sector disagreement.
The code and README carry those. Decisions about the *shape* of the project (toolchain floors,
what is released, what is declared out of scope) need the same disputable, dated record, and a
commit message is not it.

## Decision

Architecturally significant and guardrail-affecting decisions are recorded as ADRs in
`docs/adr/NNNN-kebab-title.md`, MADR format (Status, Date, Deciders, Context, Decision,
Consequences), numbered sequentially. ADRs are append-only: a change of mind is a new ADR that
supersedes the old one via its Status line, never an edit to an accepted record.

## Consequences

- Declaring any portfolio standard N/A for this repo requires an ADR here, so a skipped
  obligation always has a written, dated reason someone can argue with.
- The log starts at the first conformance pass rather than at the first commit, so decisions
  made before 2026-08-07 are recorded only where the README and module docstrings already
  explain them. New decisions land here from now on.
