"""The verifier: what stands between a narration and a reader.

Every claim is checked against the pack that was actually sent to the model, and the pack alone.
A claim is withheld -- not softened, not footnoted, withheld, and counted -- when any of these
fails:

1. **Citation.** It cites nothing, or cites an id that is not in the pack.
2. **Classification fidelity.** It names a classification state that none of the records it
   cites is in. "Suppressed" over a ``missing`` record is the defect this project exists to name,
   and it is caught here by the word. A claim that names several states (a contrast: "missing,
   not suppressed") passes if at least one of them is a cited record's state.
3. **Collapse.** It renders an absence as a non-state -- "has no", "no data", "unavailable",
   "not available", "did not provide" -- about a field, instead of saying which of the five
   states the record is in.
4. **Numbers.** It contains a number that cannot be found in the cited records: anything but a
   snapshot, a year, a unit id, an ``implausible_value``, the counts and rate change of a cited
   drift record, or a count of the cited records themselves (how many fields, how many in each
   state). A graduation rate in a claim is a number the model was never given, and this is where
   it is stopped.
5. **Judgement.** It contains a quality or recommendation word about an institution.

Quotes are checked verbatim against the corpus passage they name, and only passages in the pack
count. The reader sees the surviving claims and quotes, the count of each that was withheld, and
why, so that silence is never mistaken for completeness.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Final

from .corpus import Corpus
from .evidence import ClassificationRecord, ContradictionRecord, DriftRecord
from .lookup import Pack
from .narrate import Claim, Narration, Quote

__all__ = ["JUDGEMENT", "STATE_WORDS", "Verified", "verify"]

STATE_WORDS: Final[dict[str, str]] = {
    "reported": "reported",
    "implausible": "implausible",
    "suppressed": "suppressed",
    "not applicable": "not_applicable",
    "not_applicable": "not_applicable",
    "missing": "missing",
}

_STATE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\b(not[ _]applicable|reported|implausible|suppressed|missing)\b", re.IGNORECASE
)

# Renderings of an absence that are not one of the five states. Each is the sentence this
# project's README warns about, and none is allowed to reach a reader as a description of a field.
_COLLAPSE: Final[re.Pattern[str]] = re.compile(
    r"\b(has no |have no |no data|unavailable|not available|did not provide|does not have|"
    r"doesn't have|don't have|lacks?\b|is blank|left blank|no information)",
    re.IGNORECASE,
)

JUDGEMENT: Final[re.Pattern[str]] = re.compile(
    r"\b(best|better|worse|worst|good (school|college|university|choice|option)|"
    r"bad (school|college|university|choice|option)|recommend(ed|s|ation)?|should (you |they )?"
    r"(apply|attend|go|choose|enrol|enroll|pick|avoid)|worth (it|attending|applying)|"
    r"prestigious|selective|top[- ]tier|top[- ]ranked|rank(ed|ing|s)?|superior|inferior|"
    r"excellent|poor quality|high[- ]quality|low[- ]quality)\b",
    re.IGNORECASE,
)

_NUMBER: Final[re.Pattern[str]] = re.compile(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?")


@dataclass(frozen=True, slots=True)
class Withheld:
    text: str
    reason: str


@dataclass(frozen=True, slots=True)
class Verified:
    claims: tuple[Claim, ...]
    quotes: tuple[Quote, ...]
    withheld_claims: tuple[Withheld, ...]
    withheld_quotes: tuple[Withheld, ...]
    could_not_answer: str
    malformed: str = ""
    reasons: dict[str, int] = field(default_factory=dict)

    @property
    def shown_anything(self) -> bool:
        return bool(self.claims or self.quotes)


def _numbers_in(text: str) -> set[float]:
    out: set[float] = set()
    for token in _NUMBER.findall(text):
        try:
            out.add(float(token.replace(",", "")))
        except ValueError:
            continue
    return out


def _allowed_numbers(
    records: Iterable[ClassificationRecord],
    drifts: Iterable[DriftRecord],
    contradictions: Iterable[ContradictionRecord] = (),
) -> set[float]:
    """Every number a claim may carry, from the records it cites: identifiers, snapshots, the
    digits in a field label ("150% of normal time") or an applicability reason ("INSTCAT -2"),
    an implausible value, a drift record's counts, and a contradiction's coded values."""
    allowed: set[float] = set()
    for r in records:
        allowed |= _numbers_in(r.snapshot)
        allowed |= _numbers_in(r.unit_id)
        allowed |= _numbers_in(r.field_label)
        allowed |= _numbers_in(r.institution)
        allowed |= _numbers_in(r.not_applicable_because or "")
        if r.implausible_value is not None:
            allowed |= _numbers_in(str(r.implausible_value))
            if isinstance(r.implausible_value, int | float):
                allowed |= _numbers_in(f"{float(r.implausible_value) * 100:g}")
    for d in drifts:
        for value in (
            d.was_reported,
            d.now_reported,
            d.was_applicable,
            d.now_applicable,
            d.now_reported - d.was_reported,
            d.now_applicable - d.was_applicable,
        ):
            allowed.add(float(value))
            allowed.add(float(abs(value)))
        allowed |= _numbers_in(d.earlier) | _numbers_in(d.later)
        if d.rate_change is not None:
            points = d.rate_change * 100
            for digits in (0, 1, 2):
                allowed.add(round(points, digits))
                allowed.add(abs(round(points, digits)))
    for c in contradictions:
        allowed |= _numbers_in(c.scorecard_value) | _numbers_in(c.ipeds_value)
        allowed |= _numbers_in(c.snapshot) | _numbers_in(c.unit_id) | _numbers_in(c.institution)
    return allowed


def _countable(pack: Pack, records: list[ClassificationRecord]) -> set[float]:
    """Counts a claim may state because they are arithmetic over records the model saw: how many
    records, fields, snapshots, and how many are in each state, over the cited records and over
    the whole pack. "11 of 12 fields are reported" is checkable against the pack even when the
    claim cites only the twelfth."""
    counts = {
        len(records),
        len(pack.records),
        len({r.field_label for r in records}),
        len({r.field_label for r in pack.records}),
        len({r.snapshot for r in records}),
        len({(r.source, r.snapshot) for r in pack.records}),
    }
    for group in (records, pack.records):
        per_state: dict[str, int] = {}
        for r in group:
            per_state[r.classification] = per_state.get(r.classification, 0) + 1
        counts.update(per_state.values())
    return {float(c) for c in counts}


def _cited(
    pack: Pack, cites: tuple[str, ...]
) -> tuple[list[ClassificationRecord], list[DriftRecord], list[ContradictionRecord]]:
    records = [r for r in pack.records if r.id in cites]
    drifts = [d for d in pack.drift if d.id in cites]
    contradictions = [c for c in pack.contradictions if c.id in cites]
    return records, drifts, contradictions


def _check_claim(claim: Claim, pack: Pack) -> str | None:
    """The first reason to withhold a claim, or ``None`` when it stands."""
    citable = pack.citable_ids()
    if not claim.cites:
        return "uncited"
    if any(c not in citable for c in claim.cites):
        return "cites a record not in the pack"
    records, drifts, contradictions = _cited(pack, claim.cites)
    named = {STATE_WORDS[m.lower().replace("_", " ")] for m in _STATE_PATTERN.findall(claim.text)}
    cited_states = {r.classification for r in records}
    if named and records and not (named & cited_states):
        return "names a classification none of its cited records is in"
    if _COLLAPSE.search(claim.text):
        return "renders an absence as a non-state"
    if JUDGEMENT.search(claim.text):
        return "contains a judgement of quality or a recommendation"
    noted = {c for c in claim.cites if c.startswith("note:")}
    in_notes: set[float] = set()
    for index, text in enumerate(pack.notes):
        if f"note:{index}" in noted:
            in_notes |= _numbers_in(text)
    stray = (
        _numbers_in(claim.text)
        - _allowed_numbers(records, drifts, contradictions)
        - _countable(pack, records)
        - in_notes
    )
    if stray:
        return f"contains a number not in its cited records: {sorted(stray)[0]:g}"
    return None


def _check_quote(quote: Quote, pack: Pack, corpus: Corpus) -> str | None:
    if quote.passage_id not in {q.passage.id for q in pack.quotables}:
        return "quotes a passage not in the pack"
    if not corpus.verify_quote(quote.quote, quote.passage_id):
        return "is not a verbatim quote of the passage"
    return None


def verify(narration: Narration, pack: Pack, corpus: Corpus) -> Verified:
    """Check every claim and quote against the pack; keep what stands, count what does not."""
    kept: list[Claim] = []
    withheld: list[Withheld] = []
    reasons: dict[str, int] = {}
    for claim in narration.claims:
        reason = _check_claim(claim, pack)
        if reason is None:
            kept.append(claim)
        else:
            withheld.append(Withheld(text=claim.text, reason=reason))
            key = reason.split(":")[0]
            reasons[key] = reasons.get(key, 0) + 1
    kept_quotes: list[Quote] = []
    withheld_quotes: list[Withheld] = []
    for quote in narration.quotes:
        reason = _check_quote(quote, pack, corpus)
        if reason is None:
            kept_quotes.append(quote)
        else:
            withheld_quotes.append(Withheld(text=quote.quote, reason=reason))
            reasons[reason] = reasons.get(reason, 0) + 1
    return Verified(
        claims=tuple(kept),
        quotes=tuple(kept_quotes),
        withheld_claims=tuple(withheld),
        withheld_quotes=tuple(withheld_quotes),
        could_not_answer=narration.could_not_answer,
        malformed=narration.malformed,
        reasons=reasons,
    )
