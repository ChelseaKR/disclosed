"""Where two federal sources describe the same institution differently.

Everything else in this project measures one publisher against a stated rule. This module measures
two publishers against each other, which produces a different and stronger kind of finding: when
the College Scorecard and IPEDS disagree about an institution, no threshold of ours is involved
and there is nothing to argue with except the records themselves. One of them is wrong, or the two
are answering questions that are not the same question, and either way a reader relying on one of
them does not know it.

Only fields where both sources use the same controlled vocabulary are compared, and *which* those
are is no longer decided here. :mod:`disclosed.correspondence` holds the register: every candidate
pair with each publisher's own definition quoted from the committed corpus, a verdict, and a
reason. :data:`COMPARED` is derived from the register's ``comparable`` entries, so widening the
comparison is an edit to a cited artifact rather than to a tuple in this file, and a pair the
register calls ``not_comparable`` is a published finding instead of a silent absence.

A disagreement is reported as a disagreement and never resolved. Deciding which federal source is
correct is not something this project is in a position to do, and quietly preferring one would
throw away the only interesting part of the observation.

**Disagreements are counted by kind and never summed.** Two sources publishing different sectors
for one college, one publishing a sector where the other suppresses it, and one publishing where
the other says the question does not apply are three different facts about the pair of
collections. The third is not even a disagreement about an answer; it is a disagreement about who
the question reaches. A single "disagreements" total would add them together and lose all three,
so :func:`frame` publishes them separately and writes no total anywhere.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from .correspondence import COMPARABLE, REGISTER, Correspondence
from .disclosure import Disclosure, classify
from .fields import IPEDS_SENTINELS

__all__ = [
    "COMPARED",
    "KINDS",
    "OWNERSHIP",
    "ComparedField",
    "Contradiction",
    "PairFrame",
    "classify_code",
    "contradictions",
    "frame",
]

OWNERSHIP: Final[Mapping[str, str]] = {
    "1": "public",
    "2": "private nonprofit",
    "3": "private for-profit",
}
"""IPEDS ``CONTROL`` and Scorecard ``school.ownership`` are the same three codes, string-keyed
because the two adapters type this column differently -- the Scorecard sends JSON ``2`` and
IPEDS sends the CSV string ``"3"``. Public so :mod:`disclosed.frame` can describe a census
capture's sector mix with the same three names this module uses to describe a disagreement,
rather than a second table that could name the same code differently."""


def _describe_ownership(value: object) -> str:
    """Name a sector code, looking it up as text.

    An int-keyed lookup silently rendered every IPEDS side of every disagreement as
    "unrecognized", which made the one real finding in the corpus look like a parsing bug in this
    tool rather than a disagreement between two federal collections.
    """
    return f"{OWNERSHIP.get(str(value).strip(), 'unrecognized')} ({value})"


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


_DESCRIBERS: Final[Mapping[str, Callable[[object], str]]] = {
    "Sector": _describe_ownership,
    "State": _describe_plainly,
}
"""How each comparable pair's raw code is rendered for a reader, by the register's label.

A comparable pair with no describer here is refused at import rather than rendered with ``str``.
A sector code shown as the bare character ``3`` beside another bare character is a disagreement
a reader cannot check, and the default that produced it would be invisible.
"""


def _compared_from(entry: Correspondence) -> ComparedField:
    describe = _DESCRIBERS.get(entry.label)
    if describe is None:  # pragma: no cover - guarded at import, see the module test
        raise KeyError(
            f"the register calls {entry.label!r} comparable and no describer is registered for it"
        )
    return ComparedField(
        scorecard_key=entry.scorecard.key,
        ipeds_key=entry.ipeds.key,
        label=entry.label,
        describe=describe,
        note=entry.note,
    )


COMPARED: Final[tuple[ComparedField, ...]] = tuple(
    _compared_from(entry) for entry in REGISTER if entry.verdict == COMPARABLE
)
"""The pairs this module compares, read off the cited register and never written here.

One source of truth, so that a pair cannot be compared without a published definition on each
side and a verdict somebody can argue with. Adding a pair is an edit to
:mod:`disclosed.correspondence`, where the citations are.
"""


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


# The kinds of disagreement, kept apart because they are different facts. Never summed: see the
# module docstring. The words are data, like the five classifications, and a reader of a published
# frame is entitled to check them against this set.
KIND_VALUE: Final[str] = "value_disagreement"
"""Both sources published a value for the same institution and the values differ."""

KIND_SUPPRESSED: Final[str] = "one_side_suppressed"
"""One published a value; the other withheld it deliberately. Nobody is contradicting anybody."""

KIND_NOT_APPLICABLE: Final[str] = "one_side_not_applicable"
"""One published a value; the other says the question does not reach this institution.

The most interesting of the kinds and the easiest to lose. It is not a disagreement about an
answer, it is a disagreement about who is being asked, and summed into a total it would read as
two collections contradicting each other about a fact.
"""

KIND_MISSING: Final[str] = "one_side_missing"
"""One published a value; the other left the cell empty with no stated reason."""

KIND_IMPLAUSIBLE: Final[str] = "one_side_implausible"
"""One published a credible value; the other published one outside the credible range."""

AGREED: Final[str] = "agreed"
"""Both published, and the values match."""

NEITHER: Final[str] = "neither_reported"
"""Neither published a value, so there is nothing here to agree or disagree about."""

KINDS: Final[tuple[str, ...]] = (
    AGREED,
    KIND_VALUE,
    KIND_SUPPRESSED,
    KIND_NOT_APPLICABLE,
    KIND_MISSING,
    KIND_IMPLAUSIBLE,
    NEITHER,
)

_KIND_BY_CLASSIFICATION: Final[Mapping[Disclosure, str]] = {
    Disclosure.SUPPRESSED: KIND_SUPPRESSED,
    Disclosure.NOT_APPLICABLE: KIND_NOT_APPLICABLE,
    Disclosure.MISSING: KIND_MISSING,
    Disclosure.IMPLAUSIBLE: KIND_IMPLAUSIBLE,
}


def _as_token(value: object) -> object:
    """Render a numeric code as the string form the other collection sends.

    The two adapters type these columns differently -- the Scorecard sends JSON ``2`` and IPEDS
    sends the CSV string ``"3"`` -- and a type difference is not a statement about the world. The
    conversion happens here rather than in :func:`disclosed.disclosure.classify`, because that
    function's text path exists for URL columns where a number genuinely is not a value, and
    widening it would make an integer in an address column read as a published address.
    """
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return value


def classify_code(value: object) -> Disclosure:
    """How a controlled-vocabulary code from either source is absent, if it is.

    One call into the project's single classifier, with IPEDS's own sentinels supplied so that
    ``-3`` arrives as a suppression rather than as a sector no institution has. The Scorecard
    does not use those codes, and handing them over anyway costs nothing: a source that never
    sends ``-2`` cannot be mis-read by a rule about ``-2``.
    """
    return classify(_as_token(value), sentinels=IPEDS_SENTINELS, text_is_a_value=True)


def kind_of(scorecard: object, ipeds: object) -> str:
    """What kind of thing the two sources did with one column for one institution.

    Order matters and is deliberate. A classification that is not ``REPORTED`` on either side
    ends the question before any value is looked at, because comparing a value against an absence
    is how an absence becomes a value. Only when both sides published is the comparison a
    comparison of answers.
    """
    ours, theirs = classify_code(scorecard), classify_code(ipeds)
    if ours is Disclosure.REPORTED and theirs is Disclosure.REPORTED:
        # Compared as text so that 3 and "3" agree. The two adapters type these columns
        # differently, and a type difference is not a disagreement about the world.
        return AGREED if _code_text(scorecard) == _code_text(ipeds) else KIND_VALUE
    if ours is not Disclosure.REPORTED and theirs is not Disclosure.REPORTED:
        return NEITHER
    absent = theirs if ours is Disclosure.REPORTED else ours
    return _KIND_BY_CLASSIFICATION[absent]


def _code_text(value: object) -> str:
    """The comparable text of a raw code, for the one place two published values are compared."""
    return str(_as_token(value)).strip()


@dataclass(frozen=True, slots=True)
class PairFrame:
    """One register entry's whole picture over a population."""

    label: str
    verdict: str
    reason: str
    scorecard_key: str | None
    ipeds_key: str | None
    counts: Mapping[str, int]
    """Institutions by kind, over the institutions both sources carry. Empty for a pair the
    register does not call comparable, because nothing was compared."""

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "verdict": self.verdict,
            "reason": self.reason,
            "scorecard_key": self.scorecard_key,
            "ipeds_key": self.ipeds_key,
            "counts": (
                {kind: self.counts.get(kind, 0) for kind in KINDS}
                if self.verdict == COMPARABLE
                else None
            ),
        }


def frame(
    scorecard: Sequence[Mapping[str, Any]], ipeds: Sequence[Mapping[str, Any]]
) -> tuple[tuple[PairFrame, ...], dict[str, int]]:
    """Every register entry over a population, and how the two populations overlap.

    Returns the per-pair frames in register order and a small composition mapping: how many
    institutions each source carries, how many both carry, and how many only one does. The
    overlap is published rather than assumed because every count in the frames has it as its
    denominator, and an institution missing from one collection has not disagreed with anything.

    ``not_comparable`` entries are returned with no counts at all rather than with zeros. A row of
    zeros beside a pair nobody compared would read as two collections agreeing perfectly, which is
    the most misleading thing this frame could say.
    """
    by_id: dict[str, Mapping[str, Any]] = {}
    for record in ipeds:
        unit_id = str(record.get("id") or "").strip()
        if unit_id:
            by_id.setdefault(unit_id, record)
    scorecard_ids = {
        str(record.get("id") or "").strip()
        for record in scorecard
        if str(record.get("id") or "").strip()
    }
    both = scorecard_ids & set(by_id)

    counts: dict[str, dict[str, int]] = {entry.label: {} for entry in REGISTER}
    for record in scorecard:
        unit_id = str(record.get("id") or "").strip()
        counterpart = by_id.get(unit_id)
        if counterpart is None:
            continue
        for entry in REGISTER:
            if entry.verdict != COMPARABLE:
                continue
            kind = kind_of(record.get(entry.scorecard.key), counterpart.get(entry.ipeds.key))
            bucket = counts[entry.label]
            bucket[kind] = bucket.get(kind, 0) + 1

    frames = tuple(
        PairFrame(
            label=entry.label,
            verdict=entry.verdict,
            reason=entry.reason,
            scorecard_key=entry.scorecard.key or None,
            ipeds_key=entry.ipeds.key or None,
            counts=counts[entry.label],
        )
        for entry in REGISTER
    )
    composition = {
        "scorecard_institutions": len(scorecard_ids),
        "ipeds_institutions": len(by_id),
        "in_both": len(both),
        "scorecard_only": len(scorecard_ids - set(by_id)),
        "ipeds_only": len(set(by_id) - scorecard_ids),
    }
    return frames, composition
