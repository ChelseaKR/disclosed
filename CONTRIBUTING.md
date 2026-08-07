# Contributing

## The one local gate

```sh
uv sync
make verify
```

`make verify` runs ruff (lint and format check), strict mypy, and the test suite with a 90%
branch-coverage floor, including the static accessibility checks. It is the exact gate CI runs
(`.github/workflows/verify.yml` invokes the same target), so green locally means green in CI.
Anything that weakens it, a lowered floor, a silenced check, an `|| true`, is a defect in its
own right.

Optional but recommended:

```sh
pre-commit install
pre-commit install --hook-type pre-push
```

The fast hooks (hygiene, ruff, gitleaks) run on commit; the full `make verify` gate runs on
push.

## Ground rules for changes

- **Never let a claim outrun its data.** Every number the site or README states must be
  computed from a committed artifact or reproducible with a stated command. If a change moves a
  finding, the finding's text moves with it in the same commit.
- **Absence is data.** Do not "clean up" code paths that distinguish `MISSING` from
  `SUPPRESSED` from `NOT_APPLICABLE`; that distinction is the product.
- Changes go through a pull request against the default branch. This is currently a
  single-maintainer project, so review is the maintainer's own pass plus every automated gate;
  a PR merges only with all checks green.
- Architecturally significant decisions get an ADR in `docs/adr/` (see
  `docs/adr/0000-record-architecture-decisions.md`).

## Which standards bind this repo

The README's **Standards Conformance** table is the authoritative declaration of which
portfolio standards (the `STANDARDS/` set in `ChelseaKR/portfolio-standards`) apply here and
how each is met. If your change affects a row, update the row.
