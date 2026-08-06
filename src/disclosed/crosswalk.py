"""Where two federal sources describe the same institution differently.

Everything else in this project measures one publisher against a stated rule. This module measures
two publishers against each other, which produces a different and stronger kind of finding: when
the College Scorecard and IPEDS disagree about an institution, no threshold of ours is involved
and there is nothing to argue with except the records themselves. One of them is wrong, or the two
are answering questions that are not the same question, and either way a reader relying on one of
them does not know it.

Only fields where both sources use the same controlled vocabulary are compared. Sector and state
qualify: IPEDS ``CONTROL`` and Scorecard ``school.ownership`` are the same three codes with the
same meanings, and a two-letter state is a two-letter state. Institution names are deliberately
not compared, because the two sources punctuate and abbreviate differently and a comparison there
would produce hundreds of findings that are all about typography.

A disagreement is reported as a disagreement and never resolved. Deciding which federal source is
correct is not something this project is in a position to do, and quietly preferring one would
throw away the only interesting part of the observation.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

__all__ = ["COMPARED", "Contradiction", "contradictions"]

_OWNERSHIP: Final[Mapping[str, str]] = {
    "1": "public",
    "2": "private nonprofit",
    "3": "private for-profit",
}


def _describe_ownership(value: object) -> str:
    """Name a sector code, looking it up as text.

    The two adapters type this column differently: the Scorecard sends JSON ``2`` and IPEDS sends
    the CSV string ``"3"``. An int-keyed lookup silently rendered every IPEDS side of every
    disagreement as "unrecognized", which made the one real finding in the corpus look like a
    parsing bug in this tool rather than a disagreement between two federal collections.
    """
    return f"{_OWNERSHIP.get(str(value).strip(), 'unrecognized')} ({value})"


def _describe_plainly(value: object) -> str:
    return str(value)


@dataclass(frozen=True, slots=True)
class ComparedField:
    """One attribute both sources publish, and how to say what each of them said."""

    scorecard_key: str
    ipeds_key: str
    label: str
    describe: Callable[[object], str]
    """Renders a raw code as something a reader can check. Stored per instance rather than as a
    class default, so it stays a plain function instead of binding as a method."""

    note: str
    """Why a disagreement here is worth a reader's attention rather than being a data-entry slip."""


COMPARED: Final[tuple[ComparedField, ...]] = (
    ComparedField(
        scorecard_key="school.ownership",
        ipeds_key="ipeds.CONTROL",
        label="Sector",
        describe=_describe_ownership,
        note=(
            "Sector decides which rules an institution answers to and which peer group it is "
            "compared against, here and in most other analyses of federal education data. Two "
            "arms of the Department of Education classifying the same school differently is a "
            "fact about the data, not a rounding difference."
        ),
    ),
    ComparedField(
        scorecard_key="school.state",
        ipeds_key="ipeds.STABBR",
        label="State",
        describe=_describe_plainly,
        note=(
            "State is the coarsest grouping either source publishes and is used to build peer "
            "groups. A multi-campus institution can reasonably be filed under different states by "
            "different collections, so this is reported as a discrepancy to check rather than as "
            "an error by either source."
        ),
    ),
)


@dataclass(frozen=True, slots=True)
class Contradiction:
    """One attribute on which two federal sources disagree about one institution."""

    unit_id: str
    name: str | None
    field_label: str
    scorecard_value: str
    ipeds_value: str
    note: str


def _comparable(value: object) -> bool:
    """Whether a value is a statement rather than one of the many ways of saying nothing.

    A source that did not answer has not contradicted anything. Without this, every institution
    IPEDS suppresses (``CONTROL`` of -3, of which there are 29) would be published as disagreeing
    with the Scorecard, which would be this project inventing findings out of absences: the exact
    thing it exists to stop other people doing.
    """
    if value is None:
        return False
    text = str(value).strip()
    return bool(text) and text not in {"-1", "-2", "-3"}


def contradictions(
    scorecard: Sequence[Mapping[str, Any]], ipeds: Sequence[Mapping[str, Any]]
) -> list[Contradiction]:
    """Find institutions the two sources describe differently.

    Args:
        scorecard: Records from the College Scorecard adapter.
        ipeds: Records from the IPEDS directory adapter.

    Returns:
        One entry per disagreeing attribute per institution, sorted by unit id then attribute so
        the output is stable enough to commit and diff.

    Institutions present in only one source are not reported. Absence from a collection is a
    different finding with different causes (branch campuses, closures, a Scorecard run that
    predates an opening) and folding it in here would bury the disagreements under thousands of
    rows that are mostly about collection timing.
    """
    by_id: dict[str, Mapping[str, Any]] = {}
    for record in ipeds:
        unit_id = record.get("id")
        if unit_id is not None and str(unit_id).strip():
            by_id.setdefault(str(unit_id).strip(), record)

    found: list[Contradiction] = []
    for record in scorecard:
        unit_id = record.get("id")
        if unit_id is None or not str(unit_id).strip():
            continue
        counterpart = by_id.get(str(unit_id).strip())
        if counterpart is None:
            continue
        for compared in COMPARED:
            ours = record.get(compared.scorecard_key)
            theirs = counterpart.get(compared.ipeds_key)
            if not _comparable(ours) or not _comparable(theirs):
                continue
            # Compared as text so that 3 and "3" agree. The two adapters type these columns
            # differently, and a type difference is not a disagreement about the world.
            if str(ours).strip() == str(theirs).strip():
                continue
            name = counterpart.get("school.name") or record.get("school.name")
            found.append(
                Contradiction(
                    unit_id=str(unit_id).strip(),
                    name=str(name).strip() if name and str(name).strip() else None,
                    field_label=compared.label,
                    scorecard_value=compared.describe(ours),
                    ipeds_value=compared.describe(theirs),
                    note=compared.note,
                )
            )
    return sorted(found, key=lambda c: (c.unit_id, c.field_label))
