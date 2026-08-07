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

import statistics
from dataclasses import dataclass
from typing import Any

__all__ = ["MIN_PEERS", "PeerGroup", "peer_context", "peer_group_for"]

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


def peer_context(
    record: dict[str, Any], field_key: str, corpus: list[dict[str, Any]]
) -> PeerGroup:
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
        and (r.get("school.ownership"), r.get("school.degrees_awarded.predominant"),
             r.get("school.state")) == key
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
