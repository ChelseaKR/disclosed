# 0001. No versioned release while nothing is consumed downstream

- Status: Accepted
- Date: 2026-08-07
- Deciders: Chelsea Kelly-Reif

## Context

The portfolio's Release & Versioning standard requires a hardened, tag-triggered release
workflow (signed tags, split verification and publication authority, SBOM, provenance) for
repos that publish versioned artifacts. This repo publishes nothing: the package version is
`0.1.0.dev0`, there are no git tags, no PyPI package, no container image, and no downstream
consumer. Its durable outputs are committed data artifacts (`data/national.json`,
`data/dataset.csv`, the snapshot history) whose provenance is the git history itself, and a
static site that is rebuilt from those artifacts rather than released.

## Decision

The Release & Versioning standard is N/A for this repository until a versioned artifact
exists. No release workflow is added, because a release workflow with nothing to release is a
gate that never runs and therefore never fails, which is the kind of reassuring non-check this
project exists to argue against.

## Consequences

- `CITATION.cff` omits `version` and `date-released`, as the CFF schema permits pre-release.
- The first real release (a git tag, a published package, or any artifact someone else
  consumes) supersedes this ADR and must bring the full hardened release workflow with it in
  the same change.
- `CHANGELOG.md` keeps everything under Unreleased until then.
