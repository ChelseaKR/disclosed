# 0011. A published address is not a retrieved page, so reachability is measured and kept apart

- Status: Accepted
- Date: 2026-09-13
- Deciders: Chelsea Kelly-Reif

## Context

The IPEDS directory carries six addresses per institution — `WEBADDR`, `NPRICURL`, `FAIDURL`,
`ADMINURL`, `DISAURL`, `ATHURL` — and this project grades whether the **cell is populated**. Across
the 2023 directory that produces 29,163 populated address cells, every one of which the site has
been reporting under the column heading "Published".

"Published" is accurate about the cell and is routinely read as a claim about the page. A populated
`NPRICURL` establishes that an institution told IPEDS where its net price calculator is. It
establishes nothing whatever about whether anything is served there, and until this change the
project had verified **0 of 29,163** of them. That gap is this repository's own subject matter
happening to it: a report about a disclosure rendered as the disclosure, which is an absence
rendered as a value with the absent thing being the page.

`fields.py` was already honest about it in the small — the net price calculator rationale says "the
page behind it is never fetched and is not graded" — and the site was silent about it in the large.
A caveat inside a rationale is not the same as a number on the page.

## The decision that had to come first

Issue #91 put it plainly: **is this project willing to make any claim about a live page?**
Everything else it publishes is reproducible offline, from committed federal captures, by somebody
with no API key and no network. A link check is reproducible only as *"this is what happened on
that date"*, which is a weaker kind of claim than anything currently on the site.

**Yes, with the date as a first-class part of every figure, and on a separate axis that can never
move a grade.** The alternative is worse: not measuring it does not make the project's numbers
mean something narrower, it leaves a reader to supply the stronger reading unaided.

## Decision

1. **A separate axis, never a sixth state.** `disclosed.reachability` defines `READABLE`, `MOVED`,
   `GONE`, `UNCHECKED` and `INDETERMINATE`. No word is shared with the five classifications, no
   function here returns a `Disclosure`, and `Reachability.counts_against_publisher` is `False`
   for every member including `GONE`. `tests/test_reachability.py` asserts the vocabularies are
   disjoint and that a graded record is byte-identical with every reachability word written into
   it.

2. **The two numbers are published together or not at all.** Every reachability figure states
   *addresses published*, *addresses requested* and *addresses verified retrievable*. The
   denominator is read from the graded national artifact and never from the capture, so a capture
   covering a cohort cannot shrink the population it is measured against, and the gap between
   requested and published is visible as unverified rather than inferred as fine.

3. **The capture date travels with the verdict.** It is written into the capture, copied into the
   report, and repeated on **every** verdict rather than held once at the top of the file, so
   there is no way to render a verdict on a page without the day beside it.

4. **This project's own failed fetch is never an institution's failure.** Three rules, and they
   are the whole reason the vocabulary is not just "up/down":

   - A **refusal is not an absence**. `401`, `403`, `407`, `429` and `451` are a host declining to
     serve this client, and they are `UNCHECKED` — the same verdict a `robots.txt` exclusion
     earns. In the capture committed with this ADR, treating `403` as dead would have named
     institutions for a disclosure failure that was a bot filter meeting an unfamiliar user agent.
   - A **server fault is not an absence**. `5xx`, an unfollowable `3xx`, and a `200` whose own
     `<title>` announces an error page are `INDETERMINATE`: something answered and it settles
     nothing.
   - **Only `404` and `410` are `GONE`**, and only after more than one attempt. A single failed
     request from one vantage point against one named institution is not a finding, and
     `reachability_fetch.observe` will not write one down.

5. **Politeness is the design.** One request per host at a time with at least 1.5 seconds between
   them, `robots.txt` fetched once per host and obeyed (a `5xx` on the probe disallows the host
   entirely; a transport failure on the probe is evidence about the host, not a policy, and is not
   cached as one), a declared product token `disclosed-reachability` so a host can exclude this
   check by name in one line, a 64 KiB bounded read, a hard request budget checked *before* the
   robots probe so a run cannot exceed its stated ceiling even by one request, and nothing
   followed, crawled or requested that the federal record did not itself declare. No cookies, no
   scripts, no third parties.

6. **The digest is for change detection only.** It is a `sha256` of the bounded read, the capture
   records how many bytes that was, and it is never a pass/fail input. A vendor's redesign is not
   a disclosure failure, and a check that goes red on an ordinary edit is a check that gets
   ignored.

7. **The reduction replays offline, byte for byte.** `make reachability-replay` and
   `tests/test_reachability.py::TestTheCommittedCapture` hold the report to the same contract
   `replay`, `census-replay` and `registry-replay` already hold their artifacts to. The capture
   itself is the one thing in the repository that a rerun does not reproduce, which is exactly why
   it is committed: it is the only durable record of what those addresses served that day.

8. **Compliance is out of scope and stays out.** Reachability is checkable. Whether the page behind
   an address is a *compliant* disclosure is a legal conclusion this project does not draw, and
   `GONE` says only that this address does not reach a disclosure — never that the institution has
   none.

## What was measured

Captured on 2026-09-13, over the cohort named in the capture file: the 600 institutions of the
committed College Scorecard sample, joined to the IPEDS 2023 directory and characteristics files,
for every address the grader counts as published for the two statute-backed disclosures.

The numbers are in `data/reachability-report.json` and on the national page, and the README states
them in prose that `tests/test_doc_counts.py` re-derives. They are not repeated here, because a
figure written into an append-only decision record is a figure that goes stale silently — which is
the failure mode this ADR is otherwise about.

## Consequences

- The site now carries a dated claim. Every surface that shows a verdict shows the day it was
  taken; a stale capture reads as a stale date rather than as a current fact.
- The cohort is a sample and is labelled as one beside a national denominator. The gap is large and
  visible on purpose: 738 addresses requested against 29,163 published is the honest shape of what
  has been verified, and rounding it up to a reassuring sentence is the thing this ADR exists to
  prevent.
- Widening the cohort is a capture run and a committed file, not a code change. The budget, the
  politeness settings and the request count are written into each capture, so two captures can be
  compared without anyone having to remember how either was taken.
- `docs/adr/0006`'s boundary is unchanged: the `ask` evidence store carries statuses and digests
  only, and no reported value enters it.
