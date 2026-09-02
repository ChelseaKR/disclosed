# 0007. The Credential Registry join is measured before it is adapted, and the measurement is the deliverable

- Status: Accepted
- Date: 2026-08-27
- Deciders: Chelsea Kelly-Reif

## Context

`docs/ROADMAP.md` has carried the Credential Registry (CTDL) as the next milestone for weeks,
with one condition written into the milestone itself: "in a 200-organization sample only 8
records mentioned IPEDS at all, so measure the join rate to the two federal corpora before
designing around it." That sentence exists because of the row above it in the README, where this
project spent weeks recording the registry as "blocked behind an API key" on the evidence of an
`x-total: 0` that meant "your filter matched nothing". The lesson written down at the time was
that absence of a value and absence of a query are not the same absence. The 200-organization
note is the same lesson pointed at a different question and left unanswered.

The reason the condition is not bureaucratic is what an unmeasured join would do to the product.
This project's entire argument is that a missing value and an absent measurement look identical
on a page and must not be rendered the same way. A third source joined on a key nobody measured
would publish per-institution findings for whichever institutions happened to match; every
institution the join silently missed would render exactly like an institution that disclosed
nothing. That is this project's own defect class turned inward, and it would arrive with a third
federal-looking source attached to it.

There is also a smaller decision inside the larger one. The registry answers a request with a
list of CTDL envelopes, and an organization can carry a federal identifier in two very different
places: the typed `ceterms:ipedsID` and `ceterms:opeID` properties, or a free-text pair inside
`ceterms:identifier` whose type name a publisher writes themselves. The most common IPEDS-shaped
free-text pair in the registry is `"IPEDS NCES Data Year": "2023"`, which is a year.

## Decision

Ship the measurement as the deliverable, and leave the adapter unwritten.

1. **A read-only adapter that walks and reduces, and grades nothing.**
   `disclosed.sources.credential_registry` pages `resource_type=organization` to the registry's
   own `x-total`, records the provenance of every page (URL, time, status, bytes, SHA-256,
   attempts, the stated total, whether it came from the cache), and reduces each envelope to the
   fields a join needs. It refuses to return a walk it cannot prove reached the end, the way the
   College Scorecard adapter does, and it refuses specifically on a stated total of zero, because
   that is the answer the registry also gives an unmatched filter and this project has already
   read one of those as a measurement once.

2. **Only typed properties are read as federal identifiers.** `ceterms:ipedsID` and
   `ceterms:opeID` are read; nothing in `ceterms:identifier` is. A join rate is only as honest as
   the field it was counted from, and a data year read as a unit id would overstate it in exactly
   the direction the author of the adapter would like.

3. **Three candidate keys, measured separately and never summed.** The identifier join is the
   real one. The OPE id is counted and joined to nothing, because neither committed corpus
   carries one: calling it unmatched would understate the registry, calling it matched would
   invent a join. The web host is a weaker key, measured only over the organizations the strong
   key left unresolved, with every host that resolves to more than one IPEDS institution counted
   as ambiguous rather than resolved to whichever row came first. What that key resolves to and
   what it adds are reported as two numbers, because they are two numbers.

4. **Two denominators, both published.** Over the whole registry the identifier rate is small,
   because most of the registry is training providers that were never in IPEDS. Over the
   organizations the registry itself types as postsecondary it is large. The number that decides
   whether an adapter is worth writing is neither of those but the third one: what share of the
   federal corpora the registry reaches.

5. **The measurement is committed and replays from committed inputs.** `data/registry-join.json`
   is rebuilt in `make verify` from `data/registry/organizations.json`, `data/HD2023.zip` and
   `data/census/scorecard.json`, and must match byte for byte. The capture is committed for the
   reason the Scorecard census capture is: the registry's publishers edit it continuously, so a
   rerun does not reproduce it, and the file is the only durable record of what it held that day.

6. **The adapter stays unwritten, and that stays a separate sentence.** Nothing here grades an
   institution, adds a field, changes a classification, or touches `disclosed.ask`.

## What the measurement says

Walked to the registry's own stated total on 2026-08-27: 33,809 organizations over 340 pages, no
repeated ctid and no unreadable envelope. 6,799 are typed postsecondary. 4,818 publish a
`ceterms:ipedsID`, resolving to 4,794 of the 6,163 institutions in the IPEDS directory (77.8%)
and 4,510 of the 6,273 in the Scorecard census; 6 of the 4,800 distinct unit ids do not resolve
in the 2023 directory. 4,969 publish an OPE id. The web-host key would add 831 institutions
beyond those, at the cost of 255 ambiguous matches it refuses to make.

The condition the roadmap set is met, and it is met in the opposite direction from the note that
set it. The 200-organization figure was six-tenths of one percent of the registry, taken from the
front of an offset-paginated set, looking for a string rather than for the typed property that
carries the identifier. It was not wrong about what it counted; it was a sample answering a
different question, and this project already had to publish a census once to correct a sample it
had quoted as a population.

## Consequences

- The README's Credential Registry row changes from "adapter unwritten" to "joinable, adapter
  unwritten", and the paragraph that read the 200-organization sample as doubt is replaced by the
  measurement, with the earlier claim named rather than quietly deleted. The roadmap milestone
  keeps its position and loses its precondition.
- `make verify` gains a third replay contract. A change to the join code that moves a number
  fails the suite unless the artifact is regenerated in the same commit.
- Every figure the new README section states is re-derived in `tests/test_doc_counts.py`,
  including the one qualitative claim ("roughly three quarters"), which is held to a band.
- `data/registry/organizations.json` is 7.9 MB, the largest single committed artifact in the
  repository. That is the price of a capture that cannot be regenerated, and it is the same trade
  `data/census/scorecard.json` already made at 2.6 MB.
- A future adapter now has a stated basis and a stated limit. Roughly a quarter of the IPEDS
  directory is not in the registry at all, so anything built on this join has to render those
  institutions as outside the frame rather than as institutions that disclosed nothing. That
  constraint is the reason this ADR exists, and it is the first thing whoever writes the adapter
  inherits.

## Alternatives considered

- **Write the adapter and measure the join from its output.** Rejected. The join rate is the
  input to the decision about what the adapter should be, not a diagnostic of one that already
  exists, and an adapter that exists is an adapter somebody will publish figures from.
- **Ask the registry for the count with a filtered query.** Rejected, and it is the specific
  mistake this project has already made here: the registry answers an unmatched filter with HTTP
  200 and a zero, so a query-shaped count is evidence about the query. The walk is slower and it
  is evidence about the registry.
- **Join on institution name.** Rejected without measuring it. Name matching needs a
  normalization rule, and a normalization rule is a judgement call that would have to be argued
  in `fields.py` terms with a written rationale. The web host is a weaker key with a rule that
  fits in one sentence, and it was enough to answer whether the strong key was leaving much on
  the table.
- **Do not commit the capture.** Rejected. Without it the measurement replays from nothing and
  the 7.9 MB is replaced by a number only its author can check, which is the arrangement this
  project criticises in other people's data.
