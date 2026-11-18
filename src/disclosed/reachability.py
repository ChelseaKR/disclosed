"""Whether the disclosure an address points at can actually be retrieved.

The federal record carries six addresses per institution and this project grades whether the
**cell** is populated. That is a report about a disclosure; it is not the disclosure. A populated
``NPRICURL`` establishes that the institution told IPEDS where its net price calculator is, and
establishes nothing whatever about whether anything is served there. Publishing the first as
though it were the second is the same substitution the rest of this codebase exists to name, one
level up: an absence rendered as a value, where the absent thing is the page.

So reachability is a **separate axis** and never a sixth state. :class:`Reachability` shares no
word with :class:`disclosed.disclosure.Disclosure`, nothing here returns a ``Disclosure``, and no
verdict in this module can move a grade. ``tests/test_reachability.py`` asserts both halves of
that: the two vocabularies are disjoint, and a graded report is byte-identical with and without a
capture present.

Three rules decide the vocabulary, and all three exist to keep this project's own failed fetch
from being published as an institution's failure.

**A refusal is not an absence.** ``401``, ``403``, ``407``, ``429`` and ``451`` are a host
declining to serve *this client*. They are ``UNCHECKED``, the same verdict a ``robots.txt``
exclusion earns, because in both cases the host's policy stopped the request and nobody learned
anything about the disclosure. Folding them into ``GONE`` would name institutions as having lost
a statutory disclosure on the strength of a bot filter meeting an unfamiliar user agent.

**A server fault is not an absence either.** A ``5xx`` says the host broke, not that the page is
missing, and a ``3xx`` that could not be followed says the chain did not end. Both are
``INDETERMINATE``, together with the ``200`` whose body is a soft error page -- the case that gave
this axis its shape, because a soft 404 read as content is the "HTTP 200 error page parsed as
content" defect wearing this project's hat.

**Only the server saying the resource is not there is ``GONE``.** ``404`` and ``410``, nothing
else, and only after :mod:`disclosed.reachability_fetch` has tried more than once: a single failed
attempt against one institution from one vantage point is not a finding, and this module is the
place that refuses to publish it as one.

``MOVED`` is reported and never resolved, in the same spirit as the sector disagreement in
:mod:`disclosed.crosswalk`: the record names one host and the document arrived from another, which
is a fact about the record worth publishing and not an error to be quietly corrected.

The content digest is here for change detection between two captures and is never a pass/fail
input. A vendor redesigning a calculator is not a disclosure failure, and a check that goes red on
an ordinary edit is a check that gets ignored.
"""

from __future__ import annotations

import urllib.parse
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any, Final

__all__ = [
    "ABSENT_STATUSES",
    "CAPTURE_FORMAT_VERSION",
    "REACHABILITIES",
    "REFUSED_STATUSES",
    "FieldReach",
    "Observation",
    "ReachDrift",
    "Reachability",
    "build_report",
    "classify",
    "compare",
    "coverage",
    "observations_from_capture",
]

CAPTURE_FORMAT_VERSION: Final[str] = "1"
"""Bumped when the shape of a capture file changes.

Separate from any version of the rules, because a capture is a record of what happened on a date
and a reader has to be able to tell a re-read of the same bytes from a new visit to the web.
"""


class Reachability(Enum):
    """What happened when the address the federal record publishes was requested.

    Deliberately not ordered "most to least informative" the way :class:`Disclosure` is. There is
    no ordering here that survives contact with the data: ``UNCHECKED`` outranks ``GONE`` in
    honesty and ranks below it in usefulness, and a scale would invite somebody to average them.
    """

    READABLE = "readable"
    """A document came back from the host the record names, and it is not an error page."""

    MOVED = "moved"
    """A document came back, from a different host than the record names.

    Reported, never resolved. Whether the institution moved its calculator to a vendor or the
    record is stale is not something a redirect can tell you, and guessing would replace an
    observation with an inference.
    """

    GONE = "gone"
    """The server said the resource is not there: ``404`` or ``410``, more than once."""

    UNCHECKED = "unchecked"
    """Nobody looked, or nobody was allowed to.

    Never fetched, excluded by ``robots.txt``, refused by the host, or unreachable through every
    attempt. This is the bucket that keeps the project's own failures out of the institution's
    column, and it is expected to be large.
    """

    INDETERMINATE = "indeterminate"
    """Something answered and it settles nothing: a soft error page, a ``5xx``, an unfollowed
    redirect. The address is neither shown to work nor shown to be gone."""

    @property
    def retrieved(self) -> bool:
        """Whether a document actually came back.

        ``MOVED`` counts. The question this axis answers is whether the disclosure can be
        retrieved, and a document retrieved from somewhere else was retrieved; that the record
        names a different host is a separate finding, published separately.
        """
        return self in (Reachability.READABLE, Reachability.MOVED)

    @property
    def counts_against_publisher(self) -> bool:
        """Always ``False``, and it is a method so that the claim is testable.

        :attr:`Disclosure.counts_against_publisher` is how a classification becomes a finding
        against an institution. No reachability verdict may ever do that: this axis reports what
        one crawl saw on one day, and a day is not a compliance record. The grade is built from
        :mod:`disclosed.fields` and never reads this module.
        """
        return False


REACHABILITIES: Final[frozenset[str]] = frozenset(r.value for r in Reachability)
"""Every word this module will write into a capture, for a reader to check against."""

ABSENT_STATUSES: Final[frozenset[int]] = frozenset({404, 410})
"""The only statuses that mean the resource is not there.

``410`` as well as ``404`` because ``410`` is the stronger of the two: the server is saying the
resource was removed deliberately. Nothing else qualifies. A ``400`` means this client's request
was malformed, which is a fact about the request.
"""

REFUSED_STATUSES: Final[frozenset[int]] = frozenset({401, 403, 407, 429, 451})
"""Statuses where the host declined to serve this client, which is not evidence about the page.

``403`` is the load-bearing one. Bot filters in front of college websites answer an unfamiliar
user agent with ``403`` routinely, and in the first run of this capture 21 of the 89 addresses a
naive reading would have called dead were ``403``. Publishing those as ``GONE`` would have named
twenty-one institutions for a disclosure failure that was this crawl's user agent.
"""

# Reasons a capture can record for not having attempted a fetch at all. Stored as words rather
# than a boolean so that "robots.txt said no" and "the budget ran out" stay distinguishable in
# the committed file; both are UNCHECKED, and a reader is entitled to know which.
SKIP_ROBOTS: Final[str] = "robots"
SKIP_ROBOTS_UNREADABLE: Final[str] = "robots-unreadable"
SKIP_BUDGET: Final[str] = "budget"
SKIP_NO_URL: Final[str] = "no-url"


@dataclass(frozen=True, slots=True)
class Observation:
    """What one attempt -- or one refusal to attempt -- on one published address recorded.

    Every field is what was seen, never what it means. :func:`classify` is the only place an
    observation becomes a verdict, for the reason :func:`disclosed.disclosure.classify` is the
    only place a value becomes a classification: one place to get it wrong, one place to fix it.
    """

    unit_id: str
    name: str
    state: str
    field_key: str
    """The graded field whose address this is, e.g. ``ipeds.NPRICURL``."""

    requested_url: str
    attempts: int
    """How many times the address was requested. A non-``READABLE`` verdict published about a
    named institution requires more than one; see :mod:`disclosed.reachability_fetch`."""

    status: int | None
    """The final HTTP status, or ``None`` when no response ever arrived."""

    final_url: str
    error_kind: str
    """The transport failure that ended the last attempt, or empty when a response arrived."""

    skipped: str
    """Why no fetch was attempted, or empty when one was."""

    digest: str
    """``sha256`` of the bounded body read, or empty. Change detection only, never a gate."""

    body_bytes: int
    soft_error: bool
    """Whether a ``2xx`` body announces itself as an error page. See
    :func:`disclosed.reachability_fetch.looks_like_a_soft_error`."""

    def as_dict(self) -> dict[str, Any]:
        return {
            "unit_id": self.unit_id,
            "name": self.name,
            "state": self.state,
            "field_key": self.field_key,
            "requested_url": self.requested_url,
            "attempts": self.attempts,
            "status": self.status,
            "final_url": self.final_url,
            "error_kind": self.error_kind,
            "skipped": self.skipped,
            "digest": self.digest,
            "body_bytes": self.body_bytes,
            "soft_error": self.soft_error,
        }

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> Observation:
        """Read one row of a committed capture.

        Tolerant about missing keys and strict about their meaning: an absent ``status`` is
        ``None`` and never ``0``, because a capture written by an older version that did not
        record a status has not told us the server answered with one.
        """
        status = row.get("status")
        return cls(
            unit_id=str(row.get("unit_id") or ""),
            name=str(row.get("name") or ""),
            state=str(row.get("state") or ""),
            field_key=str(row.get("field_key") or ""),
            requested_url=str(row.get("requested_url") or ""),
            attempts=int(row.get("attempts") or 0),
            status=int(status) if isinstance(status, int) else None,
            final_url=str(row.get("final_url") or ""),
            error_kind=str(row.get("error_kind") or ""),
            skipped=str(row.get("skipped") or ""),
            digest=str(row.get("digest") or ""),
            body_bytes=int(row.get("body_bytes") or 0),
            soft_error=bool(row.get("soft_error")),
        )


def _host(url: str) -> str:
    """The comparable host of a URL: lowercased, port dropped, one leading ``www.`` dropped.

    ``www.`` is dropped because a host that redirects its bare name to its ``www`` alias has not
    moved anything, and reporting that as ``MOVED`` would fill the finding with noise that is
    about DNS rather than about disclosure.
    """
    netloc = urllib.parse.urlsplit(url).netloc.lower()
    host = netloc.rsplit("@", 1)[-1].split(":", 1)[0]
    return host.removeprefix("www.")


def classify(observation: Observation) -> Reachability:
    """Turn one observation into the one verdict it supports, and no stronger one.

    Never raises and never guesses. Every branch that is not a plain success ends in a bucket
    that says so, and the two buckets that could embarrass an institution -- ``GONE`` and
    ``MOVED`` -- are the two that require the server to have spoken clearly.
    """
    if observation.skipped:
        return Reachability.UNCHECKED
    status = observation.status
    if status is None:
        # Every attempt ended in a transport failure. That is one vantage point failing to
        # reach a host, which is a fact about the crawl; the address may be perfectly good.
        return Reachability.UNCHECKED
    if status in REFUSED_STATUSES:
        return Reachability.UNCHECKED
    if status in ABSENT_STATUSES:
        return Reachability.GONE
    if status >= 300 or observation.soft_error:
        return Reachability.INDETERMINATE
    if _host(observation.final_url) != _host(observation.requested_url):
        return Reachability.MOVED
    return Reachability.READABLE


def observations_from_capture(capture: Mapping[str, Any]) -> tuple[Observation, ...]:
    """Every observation in a committed capture, in the order the file carries them."""
    rows = capture.get("observations")
    if not isinstance(rows, list):
        return ()
    return tuple(Observation.from_dict(row) for row in rows if isinstance(row, Mapping))


@dataclass(frozen=True, slots=True)
class FieldReach:
    """One field's two numbers, and the composition behind them.

    The two numbers are the whole point of this module. ``published`` is how many institutions
    the federal record gives an address for; ``verified_retrievable`` is how many of those
    addresses actually served a document on the capture date. They are different claims and this
    dataclass refuses to let a reader see either one alone.
    """

    label: str
    key: str
    statute: str
    published: int
    """Addresses the graded population publishes. Read from the graded artifact, not the capture:
    a capture that covered a cohort must not be able to shrink the denominator it is measured
    against."""

    attempted: int
    """Addresses this capture tried. Everything between ``attempted`` and ``published`` is
    unverified, and unverified is stated as unverified rather than assumed good."""

    counts: Mapping[str, int]
    """Verdict word to count, over the attempted addresses."""

    @property
    def verified_retrievable(self) -> int:
        """Addresses that served a document: ``READABLE`` plus ``MOVED``."""
        return self.counts.get(Reachability.READABLE.value, 0) + self.counts.get(
            Reachability.MOVED.value, 0
        )

    @property
    def share_verified(self) -> float | None:
        """Verified share of published addresses, or ``None`` when none were published.

        ``None`` and never ``0.0``, for the reason :meth:`disclosed.drift.Snapshot.rate` is:
        a field nobody published an address for has no verified share, and a zero there would
        read as every address having failed.
        """
        if self.published == 0:
            return None
        return self.verified_retrievable / self.published

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "key": self.key,
            "statute": self.statute,
            "published": self.published,
            "attempted": self.attempted,
            "verified_retrievable": self.verified_retrievable,
            "share_verified": self.share_verified,
            "counts": {word: self.counts.get(word, 0) for word in sorted(REACHABILITIES)},
        }


def coverage(
    observations: Iterable[Observation],
    *,
    published: Mapping[str, int],
    labels: Mapping[str, str],
    statutes: Mapping[str, str],
) -> tuple[FieldReach, ...]:
    """Reduce observations to one :class:`FieldReach` per field the capture touched.

    Args:
        observations: Rows of a capture.
        published: Field key to how many addresses the graded population publishes. A field the
            capture touched but this mapping does not name gets ``0`` published, which makes its
            ``share_verified`` ``None`` rather than a rate over a denominator nobody supplied.
        labels: Field key to published label.
        statutes: Field key to the requirement behind it, empty for fields with none.

    Ordered by field key so that the artifact is byte-stable across runs.
    """
    counts: dict[str, dict[str, int]] = {}
    attempted: dict[str, int] = {}
    for observation in observations:
        bucket = counts.setdefault(observation.field_key, {})
        word = classify(observation).value
        bucket[word] = bucket.get(word, 0) + 1
        attempted[observation.field_key] = attempted.get(observation.field_key, 0) + 1
    return tuple(
        FieldReach(
            label=labels.get(key, key),
            key=key,
            statute=statutes.get(key, ""),
            published=published.get(key, 0),
            attempted=attempted.get(key, 0),
            counts=counts[key],
        )
        for key in sorted(counts)
    )


def build_report(
    capture: Mapping[str, Any],
    *,
    published: Mapping[str, int],
    labels: Mapping[str, str],
    statutes: Mapping[str, str],
) -> dict[str, Any]:
    """Reduce a capture to the committable artifact the site and the README are built from.

    The capture date is copied into the artifact rather than left to the filename, because a
    reachability figure without the day it was taken is a claim about the past presented as the
    present, and a filename is not carried by a reader who copies a number off a page. It is
    repeated on **every** verdict and not only at the top of the file, which looks like
    duplication and is not: a renderer that reads one verdict out of this list gets the date in
    the same object, so there is no way to put a verdict on a page without the day beside it.

    This is also the one place an observation becomes a verdict for publication. The site reads
    the words written here and never calls :func:`classify` itself, for the reason it reads
    classifications out of a report rather than regrading: a page must not be able to say
    anything the published data does not already contain.
    """
    seen = observations_from_capture(capture)
    fields = coverage(seen, published=published, labels=labels, statutes=statutes)
    captured_on = str(capture.get("captured_on") or "")
    verdicts = [
        {
            "unit_id": observation.unit_id or None,
            "name": observation.name or None,
            "state": observation.state or None,
            "field_key": observation.field_key,
            "label": labels.get(observation.field_key, observation.field_key),
            "verdict": classify(observation).value,
            "status": observation.status,
            "requested_url": observation.requested_url,
            "final_url": observation.final_url,
            "attempts": observation.attempts,
            "captured_on": captured_on,
        }
        for observation in sorted(seen, key=lambda row: (row.unit_id, row.field_key))
    ]
    return {
        "capture_format_version": CAPTURE_FORMAT_VERSION,
        "captured_on": captured_on,
        "cohort": str(capture.get("cohort") or ""),
        "politeness": capture.get("politeness", {}),
        "fields": [reach.as_dict() for reach in fields],
        "published_total": sum(reach.published for reach in fields),
        "attempted_total": sum(reach.attempted for reach in fields),
        "verified_retrievable_total": sum(reach.verified_retrievable for reach in fields),
        "verdicts": verdicts,
    }


@dataclass(frozen=True, slots=True)
class ReachDrift:
    """One field's change in verified retrievability between two captures."""

    field_label: str
    was_verified: int
    now_verified: int
    was_published: int
    now_published: int
    rate_change: float | None
    """Change in the verified share, or ``None`` when either capture could not measure one."""

    @property
    def direction(self) -> str:
        """Which way retrievability moved, read from the rate and never from the count.

        The identical argument :attr:`disclosed.drift.FieldDrift.direction` makes, for the
        identical reason: a field can shed verified addresses while the share rises, because the
        institutions publishing an address at all moved underneath it. A rate that did not move
        is ``unchanged`` and not ``lost``.
        """
        if self.rate_change is None:
            return "unmeasured"
        if self.rate_change == 0.0:
            return "unchanged"
        return "gained" if self.rate_change > 0 else "lost"

    @property
    def measured(self) -> bool:
        """Whether a verified share could be computed for both captures."""
        return self.rate_change is not None


def _rate(verified: int, published: int) -> float | None:
    return verified / published if published > 0 else None


def compare(before: Sequence[FieldReach], after: Sequence[FieldReach]) -> tuple[ReachDrift, ...]:
    """Put two captures side by side, field by field.

    A field present in only one of the two is skipped rather than compared against zero. A
    capture that did not cover a field has not established that nothing there was retrievable,
    and comparing against the absence would publish the cohort changing as the web changing.
    """
    by_key = {reach.key: reach for reach in before}
    drifts: list[ReachDrift] = []
    for now in after:
        was = by_key.get(now.key)
        if was is None:
            continue
        before_rate = _rate(was.verified_retrievable, was.published)
        after_rate = _rate(now.verified_retrievable, now.published)
        drifts.append(
            ReachDrift(
                field_label=now.label,
                was_verified=was.verified_retrievable,
                now_verified=now.verified_retrievable,
                was_published=was.published,
                now_published=now.published,
                rate_change=(
                    after_rate - before_rate
                    if before_rate is not None and after_rate is not None
                    else None
                ),
            )
        )
    return tuple(drifts)
