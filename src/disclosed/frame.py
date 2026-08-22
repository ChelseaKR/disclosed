"""How skewed a College Scorecard corpus is, answered with counts rather than asserted away.

``data/sample.json`` -- the first 600 records the API returned, back before :func:`walk` paged to
exhaustion -- is 51% Californian because the API returns institutions grouped by state and the run
that produced it stopped partway through the alphabet. That fact lived in a sentence
(:data:`disclosed.scope.SAMPLE`'s note) with no table behind it: a reader had to take "California
51%" on faith, or count the 600 records themselves.

This module counts them, and counts the full census the same way, so the comparison a sceptical
reader would want -- *how much less skewed is the frame that replaced it* -- is answered from
committed bytes rather than from a paragraph asserting the replacement is better.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Final

from .crosswalk import OWNERSHIP

__all__ = ["composition"]

_STATE_KEY: Final[str] = "school.state"
_OWNERSHIP_KEY: Final[str] = "school.ownership"


def _sector_of(record: Mapping[str, Any]) -> str | None:
    """The sector name for one record, or ``None`` if the code is absent or unrecognized.

    Looked up through :data:`disclosed.crosswalk.OWNERSHIP` rather than a second mapping, so a
    sector named here and a sector named in a crosswalk disagreement can never drift apart.
    """
    value = record.get(_OWNERSHIP_KEY)
    if value is None:
        return None
    return OWNERSHIP.get(str(value).strip())


def _state_of(record: Mapping[str, Any]) -> str | None:
    value = record.get(_STATE_KEY)
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def composition(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Count a Scorecard corpus by state and by sector.

    Every record is counted in exactly one of "named" or "unidentified" for each axis, so the two
    always sum to the corpus size and neither count can be produced by inference. A record with no
    state is not folded into an existing one and a record whose ownership code this project does
    not recognise is not silently dropped from the sector table -- both are counted and named as
    what they are, for the same reason a missing field is never rendered as a zero.
    """
    states: dict[str, int] = {}
    sectors: dict[str, int] = {}
    unstated_state = 0
    unstated_sector = 0
    for record in records:
        state = _state_of(record)
        if state is None:
            unstated_state += 1
        else:
            states[state] = states.get(state, 0) + 1
        sector = _sector_of(record)
        if sector is None:
            unstated_sector += 1
        else:
            sectors[sector] = sectors.get(sector, 0) + 1
    return {
        "institutions": len(records),
        "states": dict(sorted(states.items())),
        "states_unstated": unstated_state,
        "sectors": dict(sorted(sectors.items())),
        "sectors_unstated": unstated_sector,
    }
