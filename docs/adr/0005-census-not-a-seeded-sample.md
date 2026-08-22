# 0005. The Scorecard frame is a full census, not a seeded draw from the IPEDS enumeration

- Status: Accepted
- Date: 2026-08-21 (decision made when the collection layer was built, in PR #26; recorded here
  after the run because that is when this ADR was written, not when the choice was)
- Deciders: Chelsea Kelly-Reif

## Context

Issue #17 named the problem — every published Scorecard figure came from 600 institutions in 13
states, 51% of them Californian, because the API returns institutions grouped by state and
nobody had paged it to exhaustion — and asked for a decision, stated before seeing results,
between two ways to fix the frame:

1. A full census: paginate the College Scorecard API to exhaustion, cache responses, count and
   log every call, respect api.data.gov's documented rate limit.
2. A seeded uniform random draw of a stated size from the committed IPEDS `HD2023.zip`
   enumeration (~6,000 Title IV institutions), mirroring how `mrf-honest` and `oscal-validate`
   draw their cohorts — committing the eligible-id list, the seed, and the sample so
   `random.Random(seed).sample(...)` reproduces it exactly.

## Decision

Option 1. A full census, walked to exhaustion, proven exhaustive from the walk's own counts
rather than assumed from its size.

This was decided implicitly rather than by a paragraph, in PR #26: `college_scorecard.walk()`
pages until the API's own `metadata.total` is met and raises rather than returning short if it
cannot confirm that; `is_exhaustive()` grades a replayed capture as national on exactly three
agreeing counts (the walk said it was exhausted, the API stated a total, and the file holds that
many records) and as a sample otherwise; `disclosed fetch` records the provenance of every page
(URL with the key redacted, status, bytes, SHA-256, the rate-limit headers the API returned) so
the walk's politeness and its completeness are both auditable after the fact, not just asserted
during the run; and the dispatch-only `census` workflow requires a real key specifically because
a walk on `DEMO_KEY` would rate-limit at roughly three pages an hour and end as a slice with a
census-shaped label on it. None of that infrastructure makes sense as a build target for option
2 — a seeded draw from a committed enumeration needs no walk, no provenance, and no exhaustion
proof at all, since the eligible-id list and the seed are the whole of its evidence. Building the
walk-to-exhaustion machinery was the decision, made before a single institution from the real
walk had been seen.

The reasons, stated for the record rather than reconstructed from the code:

- **The population is small enough to take whole.** ~6,300 institutions at 100 per page is
  roughly 63 requests. api.data.gov's documented default limit is far above that for a keyed
  request; a census costs minutes, not hours, and does not need a sampling argument to justify
  itself the way a draw from a much larger population would.
- **A census answers a strictly harder question than a sample of the same size could.** Every
  drawn-vs-covered distinction option 2 would have needed — did institution X get excluded, and
  why — is moot for a census: every institution the API returns is included, by construction,
  with no draw and nothing to exclude. The "every drawn institution becomes a published row or a
  recorded exclusion" requirement is satisfied trivially and completely rather than needing a
  separate exclusion ledger.
- **It reuses evidence this project already had to build for the daily snapshot series.**
  `is_exhaustive` and `Capture` provenance exist because the daily snapshot needed to prove its
  own completeness before publishing a drift claim (ADR 0003, ADR 0004). A seeded draw would
  have needed a parallel, unrelated piece of machinery — a committed eligible-id list, a
  documented seed, a `random.Random(seed).sample(...)` replay test — duplicating effort the
  walk-to-exhaustion path had already paid for.
- **A census is falsifiable in a way a "representative" sample is not.** `is_exhaustive` either
  holds or it doesn't, checked against three counts that have to agree. A seeded sample's claim
  to represent the population rests on the seed being genuinely arbitrary and the draw being
  genuinely uniform — properties a reader has to trust rather than verify from the artifact
  alone.

## Consequences

- `data/census/scorecard.json` is committed (6,273 institutions, provenance-proven exhaustive) —
  the one Scorecard artifact that cannot be regenerated without a key, which is the same argument
  the IPEDS archives (`data/HD*.zip`, `data/IC*.zip`) already made for being committed rather
  than gitignored.
- Every institution the API returns becomes a graded row. There is no exclusion ledger because
  there is no draw to exclude anyone from; `disclosed census-report`'s composition breakdown
  (institutions by state, by sector) is offered instead, as the "coverage of the frame" figure
  the issue asked for.
- A re-census is a deliberate, manual act (`census` is `workflow_dispatch`-only) that replaces
  `data/census/scorecard.json` in a commit whose diff names the institutions that moved, exactly
  as the workflow's own header comment states.
- If the Scorecard's population ever grows past what a census can cheaply cover, this decision
  is the one to revisit, and option 2's design — seed, eligible-id list, reproducible draw — is
  recorded here rather than discarded, for whoever revisits it.
