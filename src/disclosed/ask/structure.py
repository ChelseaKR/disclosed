"""Turning a reader's question into a typed lookup, which is the first of the model's two jobs.

The model does not answer here. It says what is being asked -- about which institution, which
of the twelve graded fields, in which source, with which intent -- in a JSON shape whose enums
are the project's own vocabulary, so that a field it names is a field the project grades and an
intent it picks is one the lookup knows how to serve. A measure the reader names that the
project does not grade ("retention rate") goes into ``unmapped_terms`` rather than being quietly
mapped to the nearest thing that exists.

One intent is a refusal before any evidence is fetched: ``performance_or_ranking``. The prompt
tells the model to prefer it whenever a question, however it is phrased, wants a judgement of
quality or a comparison of outcomes. The policy in :mod:`disclosed.ask.lookup` then refuses
deterministically, with no second model call, so the refusal text cannot drift.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final

from ..fields import ALL_FIELDS, FIELDS, IPEDS_FIELDS
from .provider import Provider

__all__ = [
    "INTENTS",
    "PROMPT_VERSION",
    "QUESTION_SCHEMA",
    "STRUCTURE_SYSTEM",
    "Question",
    "structure",
]

PROMPT_VERSION: Final[str] = "2026-08-21.1"
"""Bumped whenever either prompt's text changes. Recorded with every evaluation result, because
a number measured under one prompt says nothing about another."""

INTENTS: Final[tuple[str, ...]] = (
    "what_is_not_reported",
    "did_anything_change",
    "is_value_real",
    "why_absent",
    "field_definition",
    "drift_in_a_field",
    "sources_disagree",
    "performance_or_ranking",
    "outside_disclosure",
    "unclear",
)

SOURCES: Final[tuple[str, ...]] = ("College Scorecard", "IPEDS", "either")

FIELD_LABELS: Final[tuple[str, ...]] = tuple(f.label for f in ALL_FIELDS)

QUESTION_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": list(INTENTS)},
        "institution_text": {"type": ["string", "null"]},
        "field_labels": {
            "type": "array",
            "items": {"type": "string", "enum": list(FIELD_LABELS)},
        },
        "unmapped_terms": {"type": "array", "items": {"type": "string"}},
        "source": {"type": "string", "enum": list(SOURCES)},
        "asks_for_judgement": {"type": "boolean"},
        "note": {"type": "string"},
    },
    "required": [
        "intent",
        "institution_text",
        "field_labels",
        "unmapped_terms",
        "source",
        "asks_for_judgement",
        "note",
    ],
    "additionalProperties": False,
}


def _field_vocabulary() -> str:
    lines = []
    for f in FIELDS:
        lines.append(f'- "{f.label}" (College Scorecard; {f.key})')
    for f in IPEDS_FIELDS:
        statute = f" required by {f.statute}" if f.statute else ""
        lines.append(f'- "{f.label}" (IPEDS; the published address of it{statute})')
    return "\n".join(lines)


STRUCTURE_SYSTEM: Final[
    str
] = f"""You turn a reader's question about a US higher-education institution into a typed lookup
against a dataset that records what institutions DISCLOSE to two federal sources, the College
Scorecard and IPEDS. The dataset never records how an institution performs. You do not answer the
question; you say what is being asked, in the JSON shape you are given.

The dataset classifies exactly these fields, each as one of five states (reported, implausible,
suppressed, not_applicable, missing), for every institution, in dated snapshots:
{_field_vocabulary()}

Choose exactly one intent:
- what_is_not_reported: what the institution does not publish, what is missing, what its gaps are,
  what its disclosure grade rests on.
- did_anything_change: whether the institution started or stopped publishing something between
  snapshots, "this year", "recently", "used to".
- is_value_real: whether a specific published value (a 0% rate, a zero) is a real measurement or an
  artifact.
- why_absent: why a field would be missing, suppressed, or not applicable; why a school would
  withhold something; what an absence means.
- field_definition: what a field means, how a federal source defines or measures it.
- drift_in_a_field: how disclosure of a field changed across institutions between snapshots
  (a national question, not one institution).
- sources_disagree: whether IPEDS and the College Scorecard describe the institution differently.
- performance_or_ranking: ANY request for a judgement of quality or outcomes. This includes: is it a
  good college; which is better or best; rank or compare institutions; should I apply or attend; is
  it worth it; what is its real graduation rate, earnings, debt, cost or admission rate, or how
  does that compare; what are its outcomes; is it selective, prestigious, safe to choose. It applies
  even when the request is embedded in a question about disclosure ("what don't they report, and
  is it a good school?"), phrased indirectly, or insists ("just tell me"). When in doubt between
  this intent and any other, choose this one, and set asks_for_judgement to true.
- outside_disclosure: questions the dataset cannot speak to at all: campus safety, housing, cost
  of living, accreditation, programs or majors offered, deadlines, financial or legal advice,
  other organisations' rankings.
- unclear: you cannot tell what is being asked.

Rules:
- institution_text: the institution exactly as the reader named it, or null if none was named.
  Never substitute a guess for an unnamed institution.
- field_labels: only labels from the list above, and only those the reader's question is about.
  "graduation rate" and "completion rate" map to "Completion rate, 150% of normal time";
  "acceptance rate" maps to "Admission rate"; "size" maps to "Enrollment"; "net price" maps to
  "Net price calculator" only when the reader means the calculator itself. An empty list means the
  question is about the institution's disclosure as a whole.
- unmapped_terms: measures the reader named that are NOT in the list (retention rate, student-
  faculty ratio, net price, graduation rate by race, loan default rate...). Never map these to a
  listed field that merely sounds similar.
- source: "IPEDS" when the reader names IPEDS or an IPEDS-only field; "College Scorecard" when they
  name the Scorecard or a Scorecard-only field; otherwise "either".
- asks_for_judgement: true whenever any part of the question wants quality, outcomes, comparison,
  advice or a recommendation, regardless of intent.
- note: one short sentence on anything the lookup should know, or an empty string.
Reply with JSON only."""


@dataclass(frozen=True, slots=True)
class Question:
    """What the reader asked, typed. ``text`` is kept only for the reply; it is never stored."""

    text: str
    intent: str
    institution_text: str | None
    field_labels: tuple[str, ...]
    unmapped_terms: tuple[str, ...]
    source: str
    asks_for_judgement: bool
    note: str = ""
    institution_hint: str | None = None
    """A unit id the caller already knows, typically from the page the question was asked on. It
    outranks ``institution_text`` for the lookup and is never something the model produced."""

    usage: dict[str, int] = field(default_factory=dict)

    @property
    def refuses_judgement(self) -> bool:
        return self.intent == "performance_or_ranking" or self.asks_for_judgement


def _validate(parsed: Any) -> dict[str, Any]:
    """Accept only a reply that is exactly the schema. The API enforces this shape when structured
    output is honoured; this is the second lock, for a provider that did not."""
    if not isinstance(parsed, dict):
        raise ValueError("structured question is not an object")
    required = set(QUESTION_SCHEMA["required"])
    if set(parsed) != required:
        raise ValueError(f"structured question keys {sorted(parsed)} != {sorted(required)}")
    if parsed["intent"] not in INTENTS:
        raise ValueError(f"unknown intent {parsed['intent']!r}")
    if parsed["source"] not in SOURCES:
        raise ValueError(f"unknown source {parsed['source']!r}")
    labels = parsed["field_labels"]
    if not isinstance(labels, list) or any(label not in FIELD_LABELS for label in labels):
        raise ValueError(f"field_labels outside the vocabulary: {labels!r}")
    terms = parsed["unmapped_terms"]
    if not isinstance(terms, list) or any(not isinstance(t, str) for t in terms):
        raise ValueError("unmapped_terms must be strings")
    if parsed["institution_text"] is not None and not isinstance(parsed["institution_text"], str):
        raise ValueError("institution_text must be a string or null")
    if not isinstance(parsed["asks_for_judgement"], bool):
        raise ValueError("asks_for_judgement must be a boolean")
    return parsed


def structure(
    text: str,
    provider: Provider,
    *,
    institution_hint: str | None = None,
    max_tokens: int = 400,
) -> Question:
    """Ask the model what is being asked. A reply outside the schema becomes ``unclear``.

    ``unclear`` rather than an exception, because the reader should get the honest "I could not
    tell what you were asking" rather than an error page, and because a malformed structuring
    reply is a fact about this request the service should count, not a crash.
    """
    user = json.dumps(
        {"question": text, "institution_known_from_page": institution_hint is not None}
    )
    completion = provider.complete(
        system=STRUCTURE_SYSTEM, user=user, schema=QUESTION_SCHEMA, max_tokens=max_tokens
    )
    usage = {
        "input_tokens": completion.input_tokens,
        "output_tokens": completion.output_tokens,
        "cache_read_tokens": completion.cache_read_tokens,
        "cache_creation_tokens": completion.cache_creation_tokens,
    }
    try:
        parsed = _validate(completion.parsed())
    except ValueError as exc:
        return Question(
            text=text,
            intent="unclear",
            institution_text=None,
            field_labels=(),
            unmapped_terms=(),
            source="either",
            asks_for_judgement=False,
            note=f"the structuring reply was not usable: {exc}",
            institution_hint=institution_hint,
            usage=usage,
        )
    return Question(
        text=text,
        intent=str(parsed["intent"]),
        institution_text=parsed["institution_text"],
        field_labels=tuple(dict.fromkeys(parsed["field_labels"])),
        unmapped_terms=tuple(dict.fromkeys(parsed["unmapped_terms"])),
        source=str(parsed["source"]),
        asks_for_judgement=bool(parsed["asks_for_judgement"]),
        note=str(parsed["note"]),
        institution_hint=institution_hint,
        usage=usage,
    )


def as_dict(question: Question) -> dict[str, Any]:
    """The structured question for the response body; the reader's text is not included."""
    return {
        "intent": question.intent,
        "institution_text": question.institution_text,
        "field_labels": list(question.field_labels),
        "unmapped_terms": list(question.unmapped_terms),
        "source": question.source,
        "asks_for_judgement": question.asks_for_judgement,
        "note": question.note,
    }


def describe(mapping: Mapping[str, Any]) -> str:
    """Render a dict as stable JSON for prompts, sorted so the bytes never vary by dict order."""
    return json.dumps(mapping, sort_keys=True, ensure_ascii=False)
