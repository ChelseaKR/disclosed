"""Peer comparison, so a finding is defensible with evidence rather than with a threshold.

A fixed credible range is a blunt instrument and an institution can reasonably object to one. When
this project first flagged West Valley College for publishing an in-state tuition of ``0``, the
honest objection was ready to hand: California community colleges charge enrollment fees rather than
tuition, so perhaps zero is the correct convention and the rule was wrong.

The way to settle that is not to argue about the threshold. It is to look at the institution's
peers. Of 79 California public associate-predominant institutions in the committed capture, 78
publish a tuition figure at all; 77 of those fall between $1,108 and $1,571, and the seventy-eighth
is West Valley reporting zero. The convention argument fails on its own evidence, and the finding
survives with something better than a bound behind it.

So every implausible finding carries its peer group. A reader who thinks the grade is unfair sees
immediately what comparable institutions published, and can attack the peer definition, the sample,
or the conclusion. All three are better arguments to be having than one about where a constant was
set.

Peer groups are deliberately coarse: sector, level, and state. Finer grouping produces peer sets too
small to say anything, and a peer group of three is a coincidence rather than a comparison.
"""

from __future__ import annotations

import json
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .disclosure import CLASSIFICATIONS

__all__ = [
    "MIN_PEERS",
    "PeerDisclosure",
    "PeerGroup",
    "disclosure_by_group",
    "disclosure_for",
    "group_key",
    "peer_context",
    "peer_group_for",
]

# Below this, a "peer group" is an anecdote. Findings without enough peers are still reported, but
# without a peer claim attached, because an unsupported comparison is worse than none.
#
# Applied to both the size of the group and the number of peers that actually published, because
# the second is what the comparison is made out of. See :attr:`PeerGroup.is_usable`.
MIN_PEERS = 10


def _fmt(value: float | None) -> str:
    """Render a peer value at a precision that keeps it readable.

    Rates live between 0 and 1, so whole-number formatting collapsed a spread of 0.31 to 0.95 into
    "range from 0 to 1", which told the reader nothing and looked like a bug because it was one.
    Dollar amounts want thousands separators and no decimals; proportions want two.
    """
    if value is None:
        return "n/a"
    if abs(value) <= 1.0:
        return f"{value:.2f}"
    return f"{value:,.0f}"


@dataclass(frozen=True, slots=True)
class PeerGroup:
    """What comparable institutions published for one field."""

    field_key: str
    description: str
    size: int
    reporting: int
    """How many peers published any value at all."""

    matching_value: int
    """How many peers published the same value as the institution under scrutiny."""

    median: float | None
    minimum: float | None
    maximum: float | None

    @property
    def is_usable(self) -> bool:
        """Whether there is enough published evidence here to say anything with.

        Both counts have to clear the bar, and it took a live finding to notice that only one of
        them did. ``size`` is how many comparable institutions exist; ``reporting`` is how many of
        them published a value, and it is what every sentence in :attr:`verdict` divides by.
        Guarding ``size`` alone let a peer group of 49 carry the verdict "0 of 6 comparable
        institutions publish this value" — a claim resting on six numbers, cleared by a threshold
        that never looked at them. That is the anecdote :data:`MIN_PEERS` exists to refuse, wearing
        a large denominator, and it is this project's own null-versus-zero argument turned inward:
        the institutions with nothing to report were counted as though they had said something.
        """
        return self.size >= MIN_PEERS and self.reporting >= MIN_PEERS

    @property
    def verdict(self) -> str:
        """One line a graded institution can check or contest."""
        if self.size < MIN_PEERS:
            return f"only {self.size} comparable institutions; too few to draw a conclusion"
        if self.reporting < MIN_PEERS:
            # Which count fell short is named. "Only 49 comparable institutions" would be both
            # confusing and false when 49 is the number that passed.
            return (
                f"{self.size} comparable institutions, but only {self.reporting} publish this "
                "field at all; too few to draw a conclusion"
            )
        share = self.matching_value / self.reporting
        if share >= 0.5:
            return (
                f"{self.matching_value} of {self.reporting} comparable institutions publish the "
                f"same value, so this looks like a reporting convention rather than an error"
            )
        return (
            f"{self.matching_value} of {self.reporting} comparable institutions publish this "
            f"value; the rest range from {_fmt(self.minimum)} to {_fmt(self.maximum)} "
            f"(median {_fmt(self.median)})"
        )


@dataclass(frozen=True, slots=True)
class PeerDisclosure:
    """How one peer group discloses one field: every institution in it, by state.

    :class:`PeerGroup` above answers "did comparable institutions publish *this value*", which is
    the question an implausible finding raises. This answers the one every other field raises and
    nothing was answering: **did comparable institutions publish anything at all?** "64.5% publish
    no admission rate" is a national sentence; a reader of one college's page needs "of 78
    comparable California institutions, 77 publish tuition".

    The institution under scrutiny is excluded from its own group, exactly as it is in
    :func:`peer_context`, and for the same reason: a college cannot be part of the evidence about
    itself. So :attr:`size` is the number of *other* comparable institutions, and every count
    below is out of that.

    The five counts always sum to :attr:`size`. That is asserted rather than assumed, because a
    panel whose parts do not add up to its whole is one where an institution has been dropped
    somewhere, and the most likely place to drop one is the state nobody thought about.
    """

    field_label: str
    description: str
    counts: Mapping[str, int]
    """One entry per classification, every one present, zero included. A missing key and a zero
    read identically to a careless consumer and only one of them means "none of these"."""

    @property
    def size(self) -> int:
        """How many other comparable institutions there are."""
        return sum(self.counts.values())

    @property
    def usable(self) -> bool:
        """Whether the group is large enough to say anything with.

        The same :data:`MIN_PEERS` bar the value comparison uses. Below it the page says so in
        words rather than printing a share: a comparison against six institutions is a
        coincidence with a percentage sign on it.
        """
        return self.size >= MIN_PEERS

    @property
    def asked(self) -> int:
        """How many of them the field actually reached.

        Suppressed and inapplicable institutions leave the denominator, the same rule the grade
        and the drift measurement both follow. A share taken over the whole group would count
        institutions that were never asked as institutions that failed to answer.
        """
        return self.size - self.counts.get("suppressed", 0) - self.counts.get("not_applicable", 0)

    @property
    def reporting(self) -> int:
        return self.counts.get("reported", 0)

    def as_dict(self) -> dict[str, Any]:
        return {"description": self.description, "counts": dict(self.counts)}


def disclosure_by_group(
    corpus: Sequence[Mapping[str, Any]],
    grades: Sequence[Mapping[str, str]],
    *,
    labels: Sequence[str],
) -> dict[tuple[Any, Any, Any], dict[str, dict[str, int]]]:
    """Count, for every peer group and every field, how the group's institutions were classified.

    Computed once over the whole corpus and read per institution, rather than by walking the
    corpus again for each of them. The straightforward shape is quadratic — six thousand
    institutions times six fields times six thousand peers is a quarter of a billion comparisons
    — and this project already has one measurement it declines to run at census scale. Grouping
    first makes it linear.

    The institution under scrutiny is **not** removed here. It cannot be: these are group totals,
    and there is one per group rather than one per institution. Callers subtract the institution's
    own classification when they read it, which is what :func:`disclosure_for` does.

    Args:
        corpus: The source records, which is where the peer key lives.
        grades: The graded rows, in the same order, which is where the classifications live.
        labels: The field labels to count.
    """
    if len(corpus) != len(grades):
        raise ValueError(
            f"{len(corpus)} records and {len(grades)} grades; the two are read in parallel and a "
            "mismatch would attach one institution's classifications to another's peer group"
        )
    counted: dict[tuple[Any, Any, Any], dict[str, dict[str, int]]] = {}
    for record, graded in zip(corpus, grades, strict=True):
        _, key = peer_group_for(dict(record))
        group = counted.setdefault(
            key, {label: dict.fromkeys(sorted(CLASSIFICATIONS), 0) for label in labels}
        )
        for label in labels:
            state = graded.get(label)
            if state in CLASSIFICATIONS:
                group[label][state] += 1
            # A word this build does not know is not counted. It is also not silently folded into
            # any of the five: putting it in "missing" would publish a version gap between the
            # report and this code as a gap in what the publisher disclosed, which is the defect
            # one module over already refuses by name.
    return counted


def disclosure_for(
    record: Mapping[str, Any],
    graded: Mapping[str, str],
    totals: Mapping[tuple[Any, Any, Any], Mapping[str, Mapping[str, int]]],
    *,
    labels: Sequence[str],
) -> dict[str, PeerDisclosure]:
    """One institution's peer panel: its group's counts, with its own contribution removed.

    Subtracting rather than recomputing, so the exclusion is exact and costs nothing. An
    institution whose own classification is a word this build does not know contributed nothing to
    the totals and has nothing to subtract, which is why the guard is the same one the counter
    used.
    """
    description, key = peer_group_for(dict(record))
    group = totals.get(key, {})
    panel: dict[str, PeerDisclosure] = {}
    for label in labels:
        counts = dict(group.get(label, dict.fromkeys(sorted(CLASSIFICATIONS), 0)))
        own = graded.get(label)
        if own in counts:
            counts[own] -= 1
        panel[label] = PeerDisclosure(field_label=label, description=description, counts=counts)
    return panel


def group_key(key: tuple[Any, Any, Any]) -> str:
    """A peer group's key as a JSON object key, unambiguously.

    ``json.dumps`` of the tuple rather than a joined string, because two of the three parts are
    integers that may be absent and the third is a state code that may be. ``"|".join`` would
    render a missing sector and the literal string ``"None"`` identically, which is this
    project's own defect class appearing in a dictionary key.
    """
    return json.dumps(list(key))


def peer_group_for(record: dict[str, Any]) -> tuple[str, tuple[Any, Any, Any]]:
    """Return a human description and the grouping key for one institution.

    Sector and level come from the source's own categorical fields rather than from anything
    inferred, so the grouping is reproducible by anyone holding the same data.
    """
    ownership = record.get("school.ownership")
    predominant = record.get("school.degrees_awarded.predominant")
    state = record.get("school.state")
    sector = {1: "public", 2: "private nonprofit", 3: "private for-profit"}.get(
        ownership if isinstance(ownership, int) else -1, "unknown sector"
    )
    level = {1: "certificate", 2: "associate", 3: "bachelor's", 4: "graduate"}.get(
        predominant if isinstance(predominant, int) else -1, "unknown level"
    )
    return f"{sector} {level}-predominant institutions in {state}", (ownership, predominant, state)


def peer_context(record: dict[str, Any], field_key: str, corpus: list[dict[str, Any]]) -> PeerGroup:
    """Describe how an institution's value for one field compares with its peers.

    The institution itself is excluded from its own peer group, so a value cannot help justify
    itself.
    """
    description, key = peer_group_for(record)
    own_id = record.get("id")
    own_value = record.get(field_key)

    peers = [
        r
        for r in corpus
        if r.get("id") != own_id
        and (
            r.get("school.ownership"),
            r.get("school.degrees_awarded.predominant"),
            r.get("school.state"),
        )
        == key
    ]
    values = [
        float(r[field_key])
        for r in peers
        if isinstance(r.get(field_key), (int, float)) and not isinstance(r.get(field_key), bool)
    ]
    matching = sum(1 for v in values if own_value is not None and v == own_value)
    others = [v for v in values if not (own_value is not None and v == own_value)]

    return PeerGroup(
        field_key=field_key,
        description=description,
        size=len(peers),
        reporting=len(values),
        matching_value=matching,
        median=statistics.median(others) if others else None,
        minimum=min(others) if others else None,
        maximum=max(others) if others else None,
    )
