"""Narrating the evidence pack, which is the second and last of the model's two jobs.

The model is shown the pack and nothing else, and asked for claims, each citing the records it
rests on by id, plus verbatim quotes from the federal passages it was given. It is told what
each of the five classifications means and that they are five different facts. It is told it
holds no reported values and must not invent one. It is told a disclosure grade is not a quality
grade and that it will not be asked to say which institution is better, because by the time a
question reaches this step the policy layer has already refused every question that wanted that.

None of that is trusted. Every claim goes through :mod:`disclosed.ask.verify` before a reader
sees it. The prompt is here to make the model's first draft mostly survive the verifier, not to
be the control.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Final

from .lookup import Pack
from .provider import Provider
from .structure import PROMPT_VERSION

__all__ = [
    "NARRATE_SCHEMA",
    "NARRATE_SYSTEM",
    "PROMPT_VERSION",
    "Claim",
    "Narration",
    "Quote",
    "narrate",
]

NARRATE_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "cites": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["text", "cites"],
                "additionalProperties": False,
            },
        },
        "quotes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "passage_id": {"type": "string"},
                    "quote": {"type": "string"},
                },
                "required": ["passage_id", "quote"],
                "additionalProperties": False,
            },
        },
        "could_not_answer": {"type": "string"},
    },
    "required": ["claims", "quotes", "could_not_answer"],
    "additionalProperties": False,
}

NARRATE_SYSTEM: Final[
    str
] = """You narrate records from a dataset that grades what US higher-education institutions DISCLOSE
to two federal sources, the College Scorecard and IPEDS. You are given a reader's question and an
evidence pack. The pack is the only thing you know. You answer in JSON: a list of claims, each
citing the record ids it rests on; a list of verbatim quotes from the passages in the pack; and a
could_not_answer note when the pack does not answer the question.

The five classifications are five different facts, and you never collapse them:
- reported: the institution disclosed a credible value. The value itself is NOT in the pack and
  you must never state, estimate, or characterise it.
- implausible: the institution disclosed a value that falls outside the credible range for the
  field (for example an admission rate of exactly 0). It is a reporting artifact, not a
  measurement. The published value is in the record as implausible_value and you may state it,
  always as an artifact and never as a real rate.
- suppressed: withheld deliberately, usually to protect a small cohort. The publisher is not at
  fault and it is never held against them.
- not_applicable: the field's rule does not reach this institution; the record's
  not_applicable_because says which condition excluded it. Say that condition. This is not a gap.
- missing: no value and no stated reason. This is the one that counts against a publisher.
Never say "has no X", "does not have X", "no data" or "unavailable" for a field: say which of the
five states it is in, in that word, and what that state means. Never say suppressed when the
record says missing, or missing when it says not_applicable.

Rules:
- Every claim cites at least one id from the pack: a record id, a drift id, a contradiction id, a
  passage_id from definitions, or a note id. A claim you cannot cite, you do not make.
- Say nothing the pack does not say. No facts about the institution from memory. No numbers
  except those in the cited records: snapshots, years, unit ids, implausible_value, and the
  counts and rate_change in drift records.
- No judgement of quality, outcomes, selectivity or prestige; no advice or recommendation; no
  comparison between institutions. A disclosure grade is not a quality grade. Do not use the
  words better, best, worse, worst, good, bad, recommend, or should, about any institution.
- Drift records: state the source, the two snapshots, the field, and the direction word the record
  carries ("gained", "lost", or "unchanged"). Never infer direction from the counts, never compare
  IPEDS with the College Scorecard, and never call an unmeasured field unchanged. rate_change is a
  change in the share of applicable institutions reporting; express it in percentage points.
- Per-institution change: compare the same field's classification across snapshots of the same
  source, citing each record. One institution's change is that institution's record, not a policy
  finding; the drift records are the national measurement.
- Definitions: quote the passage verbatim in quotes[], never paraphrase a federal definition in a
  claim. If a definition's role is "related", say what its note says: it defines a different
  measure than the one this dataset grades.
- When asked why a field is absent, explain the state the record is in and what that state means;
  if a quoted definition bears on it (for example that open-admissions institutions do not report
  an acceptance rate), quote it and cite it.
- The notes in the pack are facts about the pack (which snapshots exist, that no record is
  suppressed); cite them by their id when you use them.
- Write for a prospective student or a family member: plain, short sentences, no jargon left
  unexplained, two to six claims. Name the institution. Say which source and which snapshot each
  fact comes from.
- If the pack holds nothing that answers the question, leave claims empty and say why in
  could_not_answer. Do not fill the gap.
Reply with JSON only."""


@dataclass(frozen=True, slots=True)
class Claim:
    text: str
    cites: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Quote:
    passage_id: str
    quote: str


@dataclass(frozen=True, slots=True)
class Narration:
    claims: tuple[Claim, ...]
    quotes: tuple[Quote, ...]
    could_not_answer: str
    model: str
    usage: dict[str, int] = field(default_factory=dict)
    malformed: str = ""
    """Why the reply could not be read as a narration, or empty. A malformed reply is an empty
    narration with this set, so the verifier shows nothing and the service counts it."""


def _read(parsed: Any) -> tuple[tuple[Claim, ...], tuple[Quote, ...], str]:
    if not isinstance(parsed, dict) or set(parsed) != set(NARRATE_SCHEMA["required"]):
        raise ValueError("narration is not the expected object")
    claims: list[Claim] = []
    for item in parsed["claims"]:
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            raise ValueError("a claim is not an object with text")
        cites = item.get("cites")
        if not isinstance(cites, list) or any(not isinstance(c, str) for c in cites):
            raise ValueError("a claim's cites are not strings")
        claims.append(Claim(text=item["text"].strip(), cites=tuple(dict.fromkeys(cites))))
    quotes: list[Quote] = []
    for item in parsed["quotes"]:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("passage_id"), str)
            or not isinstance(item.get("quote"), str)
        ):
            raise ValueError("a quote is not an object with passage_id and quote")
        quotes.append(Quote(passage_id=item["passage_id"], quote=item["quote"].strip()))
    note = parsed["could_not_answer"]
    if not isinstance(note, str):
        raise ValueError("could_not_answer is not a string")
    return tuple(claims), tuple(quotes), note.strip()


def narrate(pack: Pack, provider: Provider, *, max_tokens: int = 1600) -> Narration:
    """Ask the model to narrate the pack. Never called for a refused pack."""
    user = json.dumps(
        {"question": pack.question.text, "pack": pack.for_prompt()},
        sort_keys=True,
        ensure_ascii=False,
    )
    completion = provider.complete(
        system=NARRATE_SYSTEM, user=user, schema=NARRATE_SCHEMA, max_tokens=max_tokens
    )
    usage = {
        "input_tokens": completion.input_tokens,
        "output_tokens": completion.output_tokens,
        "cache_read_tokens": completion.cache_read_tokens,
        "cache_creation_tokens": completion.cache_creation_tokens,
    }
    try:
        claims, quotes, note = _read(completion.parsed())
    except ValueError as exc:
        return Narration(
            claims=(),
            quotes=(),
            could_not_answer="",
            model=completion.model,
            usage=usage,
            malformed=str(exc),
        )
    return Narration(
        claims=claims, quotes=quotes, could_not_answer=note, model=completion.model, usage=usage
    )
