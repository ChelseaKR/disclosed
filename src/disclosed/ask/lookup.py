"""From a typed question to the evidence it can be answered from, or to an honest refusal.

This is the policy layer, and it is deliberately free of the model. Given a
:class:`~disclosed.ask.structure.Question` it resolves the institution (exactly, from the evidence
store), decides whether the question can be served at all, and gathers the records and federal
definitions the narration step is allowed to see. Everything it refuses, it refuses with fixed
text written here, so a reader asking "which college is better" gets the same answer on every
day, on every model, and with no model involved.

What the pack carries is the whole of what the model will be shown. Nothing outside it reaches
the narration prompt, and the verifier resolves every citation against it and nothing else.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from ..disclosure import Disclosure
from ..fields import ALL_FIELDS
from .corpus import Corpus, Passage
from .definitions import Definition, definitions_for
from .evidence import (
    IPEDS,
    SCORECARD,
    ClassificationRecord,
    ContradictionRecord,
    DriftRecord,
    Evidence,
    Institution,
)
from .structure import Question

__all__ = ["REFUSALS", "Pack", "Quotable", "Refusal", "assemble"]

_LABEL_TO_KEY: Final[dict[str, str]] = {f.label: f.key for f in ALL_FIELDS}
_MAX_CANDIDATES: Final[int] = 8

_NEEDS_AN_INSTITUTION: Final[frozenset[str]] = frozenset(
    {"what_is_not_reported", "did_anything_change", "is_value_real", "sources_disagree"}
)

REFUSALS: Final[dict[str, str]] = {
    "performance_or_ranking": (
        "This tool grades what institutions disclose, not how they perform. It will not say "
        "whether an institution is good, rank institutions, compare their outcomes, or recommend "
        "one, and it does not hold the values it would need to: a disclosure grade is a measure "
        "of what was published, and a school that reports everything and performs badly scores "
        "higher here than a good school that reports nothing. What it can tell you is which "
        "fields an institution reported, which it did not, and why an absence might be there."
    ),
    "outside_disclosure": (
        "That is outside what this tool knows. It holds one thing: whether each of twelve "
        "federally collected fields was reported, implausible, suppressed, not applicable, or "
        "missing for each institution, in dated snapshots. It has nothing on campus safety, "
        "housing, cost of living, accreditation, programmes, deadlines, or anyone's rankings, and "
        "it will not guess."
    ),
    "institution_not_named": (
        "That question is about a particular institution, and none was named. Name one, or ask "
        "from its page, and the answer will come from its own records."
    ),
    "institution_not_in_frame": (
        "No institution by that name or unit id is in the frame this tool grades: the College "
        "Scorecard census of 6,273 institutions and the IPEDS directory of 6,163. Nothing is "
        "said about an institution that is not in it."
    ),
    "institution_ambiguous": (
        "More than one institution in the frame matches that name. Pick one by its IPEDS unit "
        "id and ask again."
    ),
    "field_not_classified": (
        "This tool does not classify that measure, so it cannot say whether any institution "
        "reported it. It classifies twelve fields and only those; the list is below."
    ),
    "unclear": (
        "It was not clear what was being asked, and this tool does not guess. It can say what an "
        "institution does and does not report, whether that changed between snapshots, whether "
        "a published zero is a real measurement, why a field might be absent, and what a field "
        "means in the federal source that collects it."
    ),
}


@dataclass(frozen=True, slots=True)
class Refusal:
    code: str
    message: str
    known: tuple[str, ...] = ()
    """Pointers at what is known instead: field names, candidate institutions, counts. Fixed
    text assembled from the evidence, never from a model."""


@dataclass(frozen=True, slots=True)
class Quotable:
    """A federal passage the narration may quote, with the role the mapping gives it."""

    definition: Definition
    passage: Passage
    field_label: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "passage_id": self.passage.id,
            "field_label": self.field_label,
            "role": self.definition.role,
            "locator": self.passage.locator,
            "text": self.passage.text,
            "note": self.definition.note,
        }


@dataclass(frozen=True, slots=True)
class Pack:
    question: Question
    institution: Institution | None = None
    candidates: tuple[Institution, ...] = ()
    records: tuple[ClassificationRecord, ...] = ()
    drift: tuple[DriftRecord, ...] = ()
    contradictions: tuple[ContradictionRecord, ...] = ()
    quotables: tuple[Quotable, ...] = ()
    refusal: Refusal | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)
    """Facts about the pack the narration should state: snapshots available, fields the
    institution has no record for, that no suppressed record exists. Written here, as text the
    narration may cite under the id ``note:N``."""

    def citable_ids(self) -> frozenset[str]:
        ids = {r.id for r in self.records}
        ids.update(d.id for d in self.drift)
        ids.update(c.id for c in self.contradictions)
        ids.update(q.passage.id for q in self.quotables)
        ids.update(f"note:{i}" for i in range(len(self.notes)))
        return frozenset(ids)

    def for_prompt(self) -> dict[str, Any]:
        """Exactly what the narration model is shown. Stable ordering; no reported values."""
        return {
            "institution": (
                {
                    "unit_id": self.institution.unit_id,
                    "name": self.institution.name,
                    "state": self.institution.state,
                }
                if self.institution
                else None
            ),
            "records": [r.as_dict() for r in self.records],
            "drift": [d.as_dict() for d in self.drift],
            "contradictions": [c.as_dict() for c in self.contradictions],
            "definitions": [q.as_dict() for q in self.quotables],
            "notes": [{"id": f"note:{i}", "text": t} for i, t in enumerate(self.notes)],
        }


# -- helpers ----------------------------------------------------------------------------------


def _sources_for(question: Question) -> tuple[str, ...]:
    if question.source == SCORECARD:
        return (SCORECARD,)
    if question.source == IPEDS:
        return (IPEDS,)
    return (SCORECARD, IPEDS)


def _latest(records: Iterable[ClassificationRecord]) -> list[ClassificationRecord]:
    """The most recent snapshot's records, per source."""
    latest: dict[str, str] = {}
    for r in records:
        if r.snapshot > latest.get(r.source, ""):
            latest[r.source] = r.snapshot
    return [r for r in records if r.snapshot == latest.get(r.source)]


def _quotables(corpus: Corpus, labels: Sequence[str]) -> tuple[Quotable, ...]:
    out: list[Quotable] = []
    for label in labels:
        key = _LABEL_TO_KEY.get(label)
        if key is None:
            continue
        for definition in definitions_for(key):
            passage = corpus.passages.get(definition.passage_id)
            if passage is not None:
                out.append(Quotable(definition=definition, passage=passage, field_label=label))
    return tuple(out)


def _resolve(question: Question, evidence: Evidence) -> tuple[Institution | None, Refusal | None]:
    if question.institution_hint is not None:
        found = evidence.institutions.get(question.institution_hint)
        if found is None:
            return None, Refusal("institution_not_in_frame", REFUSALS["institution_not_in_frame"])
        return found, None
    if not question.institution_text:
        return None, None
    matches = evidence.find(question.institution_text)
    if not matches:
        return None, Refusal("institution_not_in_frame", REFUSALS["institution_not_in_frame"])
    if len(matches) > 1:
        shown = sorted(matches, key=lambda i: i.name)[:_MAX_CANDIDATES]
        return None, Refusal(
            "institution_ambiguous",
            REFUSALS["institution_ambiguous"],
            known=tuple(
                f"{i.name} ({i.state or 'state not published'}, {i.unit_id})" for i in shown
            )
            + ((f"and {len(matches) - len(shown)} more",) if len(matches) > len(shown) else ()),
        )
    return matches[0], None


def _disclosure_pointers(institution: Institution, evidence: Evidence) -> tuple[str, ...]:
    """What IS known about an institution, as fixed text: counts per state in the latest
    snapshots. Used by refusals so a refused reader is pointed somewhere true."""
    latest = _latest(evidence.for_institution(institution.unit_id))
    counts: dict[str, int] = {}
    for r in latest:
        counts[r.classification] = counts.get(r.classification, 0) + 1
    parts = [
        f"{counts[d.value]} {d.value.replace('_', ' ')}" for d in Disclosure if d.value in counts
    ]
    return (
        f"{institution.name}: of {len(latest)} graded fields in the latest snapshots, "
        + ", ".join(parts)
        + ".",
    )


def _notes(institution: Institution | None, records: Sequence[ClassificationRecord]) -> list[str]:
    notes: list[str] = []
    if institution is not None:
        for source in (SCORECARD, IPEDS):
            snaps = institution.snapshots(source)
            if snaps:
                notes.append(
                    f"{institution.name} has {source} records in snapshots {', '.join(snaps)}."
                )
            else:
                notes.append(f"{institution.name} has no {source} records in the frame.")
    if records and not any(r.classification == Disclosure.SUPPRESSED.value for r in records):
        notes.append(
            "No record in this pack is classified suppressed. In the committed federal data no "
            "institution has any field classified suppressed, so an absence here is missing, not "
            "applicable, or implausible, and never a privacy suppression."
        )
    return notes


# -- per-intent assembly -----------------------------------------------------------------------


def _records_for(
    question: Question,
    institution: Institution,
    evidence: Evidence,
    *,
    latest_only: bool,
) -> tuple[ClassificationRecord, ...]:
    labels = question.field_labels or None
    chosen: list[ClassificationRecord] = []
    for source in _sources_for(question):
        chosen.extend(
            evidence.for_institution(institution.unit_id, source=source, field_labels=labels)
        )
    if latest_only:
        chosen = _latest(chosen)
    return tuple(sorted(chosen, key=lambda r: (r.source, r.snapshot, r.field_label)))


_Gathered = tuple[
    tuple[ClassificationRecord, ...],
    tuple[DriftRecord, ...],
    tuple[ContradictionRecord, ...],
    tuple[Quotable, ...],
]

_NOTHING: Final[_Gathered] = ((), (), (), ())


def _gather_not_reported(
    q: Question, inst: Institution | None, ev: Evidence, corpus: Corpus
) -> _Gathered:
    if inst is None:
        return _NOTHING
    return _records_for(q, inst, ev, latest_only=True), (), (), ()


def _gather_changes(
    q: Question, inst: Institution | None, ev: Evidence, corpus: Corpus
) -> _Gathered:
    if inst is None:
        return _NOTHING
    drift: tuple[DriftRecord, ...] = ()
    for label in q.field_labels:
        drift += tuple(ev.drift_for(field_label=label))
    return _records_for(q, inst, ev, latest_only=False), drift, (), ()


def _gather_value_real(
    q: Question, inst: Institution | None, ev: Evidence, corpus: Corpus
) -> _Gathered:
    if inst is None:
        return _NOTHING
    records = _records_for(q, inst, ev, latest_only=False)
    if not q.field_labels:
        records = tuple(r for r in records if r.classification == "implausible")
    return records, (), (), _quotables(corpus, q.field_labels)


def _gather_definitions(
    q: Question, inst: Institution | None, ev: Evidence, corpus: Corpus
) -> _Gathered:
    """``why_absent`` and ``field_definition``: the federal words, plus the institution's own
    latest records for those fields when one was named."""
    records = _records_for(q, inst, ev, latest_only=True) if inst else ()
    return records, (), (), _quotables(corpus, q.field_labels)


def _gather_drift(q: Question, inst: Institution | None, ev: Evidence, corpus: Corpus) -> _Gathered:
    drift: tuple[DriftRecord, ...] = ()
    for source in _sources_for(q):
        for label in q.field_labels or (None,):
            drift += tuple(ev.drift_for(source=source, field_label=label))
    return (), drift, (), ()


def _gather_contradictions(
    q: Question, inst: Institution | None, ev: Evidence, corpus: Corpus
) -> _Gathered:
    if inst is None:
        return _NOTHING
    return (), (), tuple(inst.contradictions), ()


_GATHER: Final[dict[str, Callable[[Question, Institution | None, Evidence, Corpus], _Gathered]]] = {
    "what_is_not_reported": _gather_not_reported,
    "did_anything_change": _gather_changes,
    "is_value_real": _gather_value_real,
    "why_absent": _gather_definitions,
    "field_definition": _gather_definitions,
    "drift_in_a_field": _gather_drift,
    "sources_disagree": _gather_contradictions,
}


def _assemble_served(
    question: Question, institution: Institution | None, evidence: Evidence, corpus: Corpus
) -> Pack:
    records, drift, contradictions, quotables = _GATHER[question.intent](
        question, institution, evidence, corpus
    )
    return Pack(
        question=question,
        institution=institution,
        records=records,
        drift=drift,
        contradictions=contradictions,
        quotables=quotables,
        notes=tuple(_notes(institution, records)),
    )


def assemble(question: Question, evidence: Evidence, corpus: Corpus) -> Pack:
    """Resolve, refuse, or gather. The only function the service calls."""
    if question.refuses_judgement:
        institution, _ = _resolve(question, evidence)
        known = _disclosure_pointers(institution, evidence) if institution else ()
        return Pack(
            question=question,
            institution=institution,
            refusal=Refusal("performance_or_ranking", REFUSALS["performance_or_ranking"], known),
        )
    if question.intent in ("outside_disclosure", "unclear"):
        return Pack(
            question=question,
            refusal=Refusal(question.intent, REFUSALS[question.intent]),
        )

    institution, refusal = _resolve(question, evidence)
    if refusal is not None:
        return Pack(question=question, candidates=(), refusal=refusal)
    if institution is None and question.intent in _NEEDS_AN_INSTITUTION:
        return Pack(
            question=question,
            refusal=Refusal("institution_not_named", REFUSALS["institution_not_named"]),
        )
    if question.unmapped_terms and not question.field_labels:
        known = tuple(f.label for f in ALL_FIELDS)
        return Pack(
            question=question,
            institution=institution,
            refusal=Refusal("field_not_classified", REFUSALS["field_not_classified"], known),
        )
    return _assemble_served(question, institution, evidence, corpus)
