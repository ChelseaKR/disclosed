"""What a run covers, travelling with the run rather than being remembered by the reader.

Every percentage this project publishes is a percentage of something, and the something is not
always the country. The committed College Scorecard capture is 600 institutions across 13 states
with California at 51%, because it is the first records the API returned and they arrive grouped
by state. The IPEDS directory is a different animal: it is a file, not a page of a file, so
grading it grades every institution there is.

Those two facts have to reach the page together with the numbers they qualify, and the way to
guarantee that is to make the coverage part of the report rather than part of the prose. The site
generator reads :class:`Scope` out of the payload it renders and cannot print an aggregate without
it. Before this existed, the caveat was a paragraph in the home page template, and a paragraph in
a template is a thing that stays true only until somebody renders a different report through it.

The kind is never inferred from the size of the result. A 6,000-institution capture is not
national because it is large; it is national because the fetch walked the source to exhaustion,
and that is something only the caller knows. Guessing would eventually promote a big sample to a
national claim on the strength of its row count, which is the failure this module exists to make
impossible.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

__all__ = ["NATIONAL", "SAMPLE", "Scope", "scope_from_payload"]

SAMPLE: Final[str] = "sample"
"""Some of the institutions there are. Figures describe the rows present and nothing else."""

NATIONAL: Final[str] = "national"
"""Every institution the source publishes. Figures describe the population."""


@dataclass(frozen=True, slots=True)
class Scope:
    """The coverage of one run, and the sentence a reader needs before believing its percentages."""

    kind: str
    """:data:`SAMPLE` or :data:`NATIONAL`. Declared by the caller, never inferred from row count."""

    source: str
    """The publisher these records came from, named as a reader would name it."""

    institutions: int

    states: int | None
    """How many states or regions the institutions sit in, or ``None`` when nobody counted.

    ``None`` and not ``0``, for the same reason :attr:`universe` is. A run that did not carry an
    address through to this field has not established that its institutions sit in no states, and
    a reader who saw ``0`` here would reasonably conclude the source covers nowhere. Zero is a
    measurement; this field's absence is not one, and the two must not print the same.
    """

    universe: int | None
    """How many institutions exist in this source, or ``None`` when that is not known.

    ``None`` and not ``0``, for the same reason an unmeasured rate is ``None``. A universe of zero
    would make :attr:`coverage` either a crash or an infinity, and a reader who saw ``0`` in this
    field would reasonably conclude the source is empty.
    """

    note: str
    """Why the coverage is what it is, in the words a sceptical reader deserves."""

    @property
    def is_national(self) -> bool:
        return self.kind == NATIONAL

    @property
    def coverage(self) -> float | None:
        """Share of the source's institutions present, or ``None`` if the universe is unknown.

        Unknown is returned as ``None`` rather than as ``1.0``. A run that cannot say how much of
        the source it holds has not established that it holds all of it, and defaulting to "all"
        is how a sample gets published as a census.
        """
        if self.universe is None or self.universe <= 0:
            return None
        return self.institutions / self.universe

    @property
    def sentence(self) -> str:
        """One line stating the coverage, safe to put next to any figure from this run."""
        if self.is_national:
            across = (
                "across a number of states and territories this run did not measure"
                if self.states is None
                else f"across {self.states} states and territories"
            )
            return (
                f"Every institution in the {self.source}: {self.institutions:,} {across}. "
                f"Figures on this page are national."
            )
        of_universe = f" of roughly {self.universe:,}" if self.universe is not None else ""
        across = (
            "across a number of states this run did not measure"
            if self.states is None
            else f"across {self.states} states"
        )
        return (
            f"{self.institutions:,} institutions{of_universe} in the {self.source}, {across}. "
            f"Figures on this page describe these institutions and are not national."
        )

    def as_dict(self) -> dict[str, Any]:
        """Serialize for the report payload, including the derived share.

        ``coverage`` is written out even though it is computable, so that a consumer reading the
        JSON without this code cannot derive it wrongly by dividing by a universe of zero.
        """
        return {
            "kind": self.kind,
            "source": self.source,
            "institutions": self.institutions,
            "states": self.states,
            "universe": self.universe,
            "coverage": self.coverage,
            "note": self.note,
        }


def scope_from_payload(payload: dict[str, Any]) -> Scope | None:
    """Read a :class:`Scope` back out of a report, or ``None`` if the report predates it.

    Returns ``None`` rather than a default scope. A default would be a claim about coverage that
    nobody made, and the site treats an absent scope as "this run did not say", which is the
    truth. Older reports exist and regrading them is not always possible.
    """
    raw = payload.get("scope")
    if not isinstance(raw, dict):
        return None
    kind = raw.get("kind")
    source = raw.get("source")
    if kind not in (SAMPLE, NATIONAL) or not isinstance(source, str):
        return None
    universe = raw.get("universe")
    # ``states`` is read the way ``universe`` is: an absent or null field is carried through as
    # ``None``. Coercing it to 0 here would manufacture the same false measurement the field's
    # own docstring forbids, one layer further from where anybody could see it.
    states = raw.get("states")
    return Scope(
        kind=kind,
        source=source,
        institutions=int(raw.get("institutions", 0)),
        states=int(states) if isinstance(states, int) else None,
        universe=int(universe) if isinstance(universe, int) else None,
        note=str(raw.get("note", "")),
    )
