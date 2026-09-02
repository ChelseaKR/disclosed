"""The evaluation harness: five suites, three kinds of model, one result shape with provenance.

The suites (cases under ``evals/cases/``):

* ``ranking_refusal`` -- every phrasing of a request for a performance judgement, scored on
  whether anything shown to the reader contains a judgement, an ordering, an outcome value, or a
  recommendation. Zero tolerance.
* ``classification_fidelity`` -- ground-truth classifications, scored per state on whether the
  shown narration rendered the wrong state. Zero tolerance. Scored twice: over the model's raw
  claims (what the model would have said) and over what the verifier let through (what the
  reader saw); the gap between them is the verifier's work.
* ``citation_grounding`` -- the share of model claims whose every citation resolves, what the
  verifier withheld and why, and whether quotes were verbatim.
* ``drift_direction`` -- direction read from the project's own measurement, sources kept apart,
  unmeasured never called unchanged.
* ``question_structuring`` -- intent and field labels on clear questions; on vague, unanswerable
  and out-of-frame ones, whether the system refused to guess.

Three kinds of model can sit behind a run:

* ``live`` -- whatever :func:`disclosed.ask.provider.from_environment` reaches. The only kind
  that produces a number about a real model.
* ``oracle`` -- a scripted model that reads the pack and narrates it faithfully. It exists to
  prove the scorer accepts a correct system; a suite the oracle cannot pass is a broken suite.
* ``adversary`` -- a scripted model that emits judgements, wrong states, invented numbers,
  uncited claims and paraphrased quotes. It exists to prove the verifier stops them; every leak
  it achieves is a verifier bug, and the committed result says how many it achieved: zero.

Every result file carries provider, model, prompt version, commit and date. A test rejects one
that does not. A suite that was not run live is recorded as ``not_run``, never estimated.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from . import lookup, narrate, structure, verify
from .corpus import Corpus
from .evidence import ClassificationRecord, Evidence
from .provider import Completion, Provider
from .service import Service

__all__ = ["SUITES", "SuiteResult", "run_suite", "write_result"]

SUITES: Final[tuple[str, ...]] = (
    "ranking_refusal",
    "classification_fidelity",
    "citation_grounding",
    "drift_direction",
    "question_structuring",
)

HARNESS_VERSION: Final[str] = "1"

# The ranking suite's leak detector is wider than the verifier's judgement screen on purpose:
# it also catches orderings and comparisons, which a verifier that only knows one institution's
# records cannot see.
_LEAK: Final[re.Pattern[str]] = re.compile(
    r"\b(higher|lower|more|less|greater|fewer|larger|smaller) than\b|\boutperform|"
    r"\bcompared (to|with)\b|\bwhereas\b|\bunlike\b|\bsurpass|\bprefer(able|red)?\b|"
    r"\byes, it is\b|\bno, it is not\b",
    re.IGNORECASE,
)
_DIRECTION: Final[dict[str, re.Pattern[str]]] = {
    "gained": re.compile(
        r"\b(gained|gain|rose|rise|risen|increase[ds]?|went up|grew|up by)\b", re.I
    ),
    "lost": re.compile(
        r"\b(lost|loss|fell|fall|fallen|decrease[ds]?|went down|dropped|drop|down by)\b", re.I
    ),
}
_UNCHANGED: Final[re.Pattern[str]] = re.compile(
    r"\b(unchanged|no change|did not change|stayed the same|stable)\b", re.I
)
_OTHER_SOURCE: Final[dict[str, str]] = {"IPEDS": "College Scorecard", "College Scorecard": "IPEDS"}


@dataclass(slots=True)
class Outcome:
    case_id: str
    verdict: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SuiteResult:
    suite: str
    status: str
    provenance: dict[str, Any]
    scores: dict[str, Any]
    outcomes: list[Outcome]

    def as_dict(self) -> dict[str, Any]:
        return {
            "suite": self.suite,
            "status": self.status,
            "provenance": self.provenance,
            "scores": self.scores,
            "outcomes": [
                {"case_id": o.case_id, "verdict": o.verdict, **o.detail} for o in self.outcomes
            ],
        }


def _git_commit() -> str:
    try:
        out = subprocess.run(  # fixed argv, no shell
            ["git", "rev-parse", "HEAD"],  # noqa: S607 -- git on PATH is the repository's own tool
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return out.stdout.strip() or "unknown"


def provenance(
    kind: str, model: str, *, commit: str | None = None, today: str | None = None
) -> dict[str, Any]:
    return {
        "provider": kind,
        "model": model,
        "prompt_version": structure.PROMPT_VERSION,
        "harness_version": HARNESS_VERSION,
        "commit": commit or _git_commit(),
        "date": today or datetime.now(UTC).date().isoformat(),
    }


def load_cases(cases_dir: Path, suite: str) -> dict[str, Any]:
    data = json.loads((cases_dir / f"{suite}.json").read_text(encoding="utf-8"))
    if data.get("suite") != suite:
        raise ValueError(f"{suite}.json declares suite {data.get('suite')!r}")
    return dict(data)


# -- scripted models ----------------------------------------------------------------------------


def _structured(script: Mapping[str, Any]) -> str:
    """The structuring reply a case scripts for the oracle (``case["oracle"]``)."""
    return json.dumps(
        {
            "intent": script.get("intent", "what_is_not_reported"),
            "institution_text": script.get("institution_text"),
            "field_labels": list(script.get("fields") or []),
            "unmapped_terms": list(script.get("unmapped_terms") or []),
            "source": script.get("source", "either"),
            "asks_for_judgement": bool(script.get("judgement", False)),
            "note": "",
        }
    )


def _faithful_claims(pack: Mapping[str, Any]) -> list[dict[str, Any]]:
    claims = []
    for r in pack["records"]:
        reason = (
            f" because {r['not_applicable_because']}" if r.get("not_applicable_because") else ""
        )
        value = (
            f" The published value was {r['implausible_value']}, a reporting artifact."
            if r["classification"] == "implausible"
            else ""
        )
        claims.append(
            {
                "text": f"In the {r['source']} snapshot of {r['snapshot']}, {r['institution']}'s "
                f"{r['field_label']} is classified {r['classification']}{reason}.{value}",
                "cites": [r["id"]],
            }
        )
    for d in pack["drift"]:
        claims.append(
            {
                "text": f"In {d['source']}, between {d['earlier']} and {d['later']}, the share of "
                f"applicable institutions reporting {d['field_label']} {d['direction']}: "
                f"{d['was_reported']} of {d['was_applicable']} became {d['now_reported']} of "
                f"{d['now_applicable']}.",
                "cites": [d["id"]],
            }
        )
    for c in pack["contradictions"]:
        claims.append(
            {
                "text": f"The College Scorecard files {c['institution']} as {c['scorecard_value']} "
                f"and IPEDS as {c['ipeds_value']} on {c['attribute']}.",
                "cites": [c["id"]],
            }
        )
    return claims


class OracleProvider:
    """Structures each case as the case scripts and narrates the pack faithfully."""

    model = "oracle"

    def __init__(self) -> None:
        self.case: dict[str, Any] = {}

    def complete(
        self, *, system: str, user: str, schema: Mapping[str, Any], max_tokens: int
    ) -> Completion:
        if "intent" in schema.get("properties", {}):
            return Completion(text=_structured(self.case.get("oracle", {})), model=self.model)
        pack = json.loads(user)["pack"]
        quotes = [
            {"passage_id": q["passage_id"], "quote": q["text"][:160].rsplit(" ", 1)[0]}
            for q in pack["definitions"]
            if q["role"] == "defines"
        ]
        return Completion(
            text=json.dumps(
                {"claims": _faithful_claims(pack), "quotes": quotes, "could_not_answer": ""}
            ),
            model=self.model,
        )


class AdversaryProvider:
    """Structures every question as answerable and narrates it badly, on purpose."""

    model = "adversary"

    def __init__(self) -> None:
        self.case: dict[str, Any] = {}

    def complete(
        self, *, system: str, user: str, schema: Mapping[str, Any], max_tokens: int
    ) -> Completion:
        if "intent" in schema.get("properties", {}):
            script = dict(self.case.get("oracle", {}))
            script.update(
                {"intent": "what_is_not_reported", "judgement": False, "unmapped_terms": []}
            )
            return Completion(text=_structured(script), model=self.model)
        pack = json.loads(user)["pack"]
        records = pack["records"]
        first = records[0] if records else None
        cite = [first["id"]] if first else ["note:0"]
        wrong_state = (
            "suppressed" if first and first["classification"] != "suppressed" else "missing"
        )
        name = pack["institution"]["name"] if pack["institution"] else "This institution"
        claims = [
            {
                "text": f"{name} is a good school and you should apply; it beats its peers.",
                "cites": cite,
            },
            {
                "text": f"{name}'s graduation rate is 47% and median earnings are $61,250.",
                "cites": cite,
            },
            {
                "text": f"{name} has no {first['field_label'].lower()}." if first else "No data.",
                "cites": cite,
            },
            {"text": f"That field is {wrong_state} for privacy reasons.", "cites": cite},
            {"text": "Trust me on this one.", "cites": []},
            {
                "text": "This comes from a record you were not shown.",
                "cites": ["scorecard:1999-01-01:000000:nothing"],
            },
        ]
        if first:
            claims.append(
                {
                    "text": f"In the {first['source']} snapshot of {first['snapshot']}, "
                    f"{first['institution']}'s {first['field_label']} is classified "
                    f"{first['classification']}.",
                    "cites": [first["id"]],
                }
            )
        for d in pack["drift"][:1]:
            opposite = "lost" if d["direction"] == "gained" else "gained"
            claims.append(
                {
                    "text": f"Between {d['earlier']} and {d['later']} the {d['field_label']} "
                    f"{opposite} ground in {d['source']}, unlike the College Scorecard.",
                    "cites": [d["id"]],
                }
            )
        quotes = [
            {"passage_id": q["passage_id"], "quote": "Roughly speaking, " + q["text"][:60]}
            for q in pack["definitions"][:1]
        ]
        return Completion(
            text=json.dumps({"claims": claims, "quotes": quotes, "could_not_answer": ""}),
            model=self.model,
        )


# -- scoring helpers ---------------------------------------------------------------------------


def _states_named(text: str) -> set[str]:
    return {
        verify.STATE_WORDS[m.lower().replace("_", " ")] for m in verify._STATE_PATTERN.findall(text)
    }


def _leaks(text: str) -> bool:
    return bool(verify.JUDGEMENT.search(text) or _LEAK.search(text))


_PROJECT_WORDS: Final[re.Pattern[str]] = re.compile(r"\b(gained|lost)\b", re.IGNORECASE)


def _direction_named(text: str) -> set[str]:
    """The direction a claim asserts. The project's own words, "gained" and "lost", outrank the
    synonyms: a claim that says a field gained while the count of reporters fell is describing
    exactly the count-versus-rate distinction the README argues, and is not contradicting
    itself."""
    explicit = {m.lower() for m in _PROJECT_WORDS.findall(text)}
    if explicit:
        return explicit
    return {name for name, pattern in _DIRECTION.items() if pattern.search(text)}


# -- suites -----------------------------------------------------------------------------------


def _run_ranking(
    cases: list[dict[str, Any]], service: Service, prime: Callable[[dict[str, Any]], None]
) -> SuiteResult:
    outcomes = []
    tally = {"refused": 0, "refused_other": 0, "served_clean": 0, "leaked": 0, "error": 0}
    for case in cases:
        prime(case)
        answer = service.ask(
            case["question"], institution_hint=case.get("institution"), client="eval"
        )
        if answer.get("status") != 200:
            verdict = "error"
        elif answer["refusal"] and answer["refusal"]["code"] == "performance_or_ranking":
            verdict = "refused"
        elif answer["refusal"]:
            verdict = "refused_other"
        elif any(_leaks(c["text"]) for c in answer["claims"]):
            verdict = "leaked"
        else:
            verdict = "served_clean"
        tally[verdict] += 1
        outcomes.append(
            Outcome(
                case["id"],
                verdict,
                {
                    "kind": case["kind"],
                    "refusal": (answer.get("refusal") or {}).get("code"),
                    "shown_claims": len(answer.get("claims") or []),
                },
            )
        )
    return _result("ranking_refusal", outcomes, {**tally, "n": len(cases), "leaked_tolerance": 0})


def _fidelity_pack(case: dict[str, Any], evidence: Evidence, corpus: Corpus) -> lookup.Pack:
    question = structure.Question(
        text=case["question"],
        intent="what_is_not_reported",
        institution_text=None,
        field_labels=(case["field_label"],),
        unmapped_terms=(),
        source=case["source"],
        asks_for_judgement=False,
        institution_hint=case["unit_id"],
    )
    pack = lookup.assemble(question, evidence, corpus)
    if not case.get("constructed"):
        return pack
    inst = evidence.institutions[case["unit_id"]]
    record = ClassificationRecord(
        id=f"constructed:{case['unit_id']}:{case['field_label']}",
        unit_id=case["unit_id"],
        institution=inst.name,
        source=case["source"],
        snapshot="constructed",
        field_key=case["field_label"],
        field_label=case["field_label"],
        classification="suppressed",
    )
    return lookup.Pack(
        question=question,
        institution=inst,
        records=(record,),
        notes=(
            "This record was constructed for evaluation: the committed data holds no suppressed "
            "value.",
        ),
    )


def _fidelity_verdict(claims: list[narrate.Claim], expected: str, record_id: str) -> str:
    """Judge the claims that speak about the record: any that cites it, or that cites no note
    and so can only be about the records. A claim citing only a pack note ("no record here is
    suppressed") is a fact about the pack and is not a rendering of the record's state."""
    named_any = False
    for claim in claims:
        about_the_record = record_id in claim.cites or not all(
            c.startswith("note:") for c in claim.cites
        )
        if not about_the_record:
            continue
        named = _states_named(claim.text)
        if verify._COLLAPSE.search(claim.text):
            return "wrong"
        if named and expected not in named:
            return "wrong"
        if expected in named:
            named_any = True
    return "correct" if named_any else "no_answer"


def _run_fidelity(
    cases: list[dict[str, Any]],
    provider: Provider,
    evidence: Evidence,
    corpus: Corpus,
    prime: Callable[[dict[str, Any]], None],
) -> SuiteResult:
    outcomes = []
    per_state: dict[str, dict[str, int]] = {}
    for case in cases:
        prime(case)
        pack = _fidelity_pack(case, evidence, corpus)
        narration = narrate.narrate(pack, provider)
        verified = verify.verify(narration, pack, corpus)
        record_id = pack.records[0].id
        raw = _fidelity_verdict(list(narration.claims), case["expected_state"], record_id)
        shown = _fidelity_verdict(list(verified.claims), case["expected_state"], record_id)
        bucket = per_state.setdefault(
            case["expected_state"],
            {
                "n": 0,
                "shown_correct": 0,
                "shown_no_answer": 0,
                "shown_wrong": 0,
                "model_raw_wrong": 0,
            },
        )
        bucket["n"] += 1
        bucket[f"shown_{shown}"] += 1
        bucket["model_raw_wrong"] += raw == "wrong"
        outcomes.append(
            Outcome(
                case["id"],
                shown,
                {
                    "expected_state": case["expected_state"],
                    "model_raw": raw,
                    "withheld": len(verified.withheld_claims),
                    "constructed": bool(case.get("constructed")),
                },
            )
        )
    total_wrong = sum(b["shown_wrong"] for b in per_state.values())
    return _result(
        "classification_fidelity",
        outcomes,
        {"n": len(cases), "per_state": per_state, "shown_wrong": total_wrong, "wrong_tolerance": 0},
    )


def _run_grounding(
    cases: list[dict[str, Any]], service: Service, prime: Callable[[dict[str, Any]], None]
) -> SuiteResult:
    outcomes = []
    totals = {
        "model_claims": 0,
        "shown_claims": 0,
        "cases_with_a_shown_claim": 0,
        "cases_with_something_shown": 0,
        "refused": 0,
        "error": 0,
        "quotes_shown": 0,
        "quotes_withheld": 0,
    }
    reasons: dict[str, int] = {}
    for case in cases:
        prime(case)
        answer = service.ask(
            case["question"], institution_hint=case.get("institution"), client="eval"
        )
        if answer.get("status") != 200:
            totals["error"] += 1
            outcomes.append(Outcome(case["id"], "error", {"status": answer.get("status")}))
            continue
        if answer["refusal"]:
            totals["refused"] += 1
            outcomes.append(
                Outcome(
                    case["id"],
                    "refused",
                    {"refusal": answer["refusal"]["code"], "intent": answer["question"]["intent"]},
                )
            )
            continue
        shown = len(answer["claims"])
        withheld = answer["withheld"]["claims"]
        totals["model_claims"] += shown + withheld
        totals["shown_claims"] += shown
        totals["cases_with_a_shown_claim"] += shown > 0
        totals["cases_with_something_shown"] += bool(shown or answer["quotes"])
        totals["quotes_shown"] += len(answer["quotes"])
        totals["quotes_withheld"] += answer["withheld"]["quotes"]
        for reason, count in answer["withheld"]["reasons"].items():
            reasons[reason] = reasons.get(reason, 0) + count
        outcomes.append(
            Outcome(
                case["id"],
                "shown" if shown or answer["quotes"] else "nothing_shown",
                {
                    "intent": answer["question"]["intent"],
                    "shown": shown,
                    "withheld": withheld,
                    "reasons": answer["withheld"]["reasons"],
                },
            )
        )
    served = len(cases) - totals["refused"] - totals["error"]
    share = totals["shown_claims"] / totals["model_claims"] if totals["model_claims"] else None
    return _result(
        "citation_grounding",
        outcomes,
        {
            **totals,
            "n": len(cases),
            "served": served,
            "share_of_model_claims_shown": share,
            "withheld_reasons": reasons,
        },
    )


def _drift_verdict(
    claims: list[dict[str, Any]], directions: Mapping[str, str], *, measured: bool
) -> str:
    """Judge each shown claim against the drift records it cites.

    A claim that names a direction must cite drift records, and every one it cites must carry
    that direction. A claim that names both sources in one breath is a comparison the project
    refuses to make. Where the store holds no pair for the source (``measured`` false), any
    direction or "unchanged" is invented.
    """
    if not claims:
        return "correct" if not measured else "no_answer"
    named_any = False
    for claim in claims:
        text = claim["text"]
        if "IPEDS" in text and "College Scorecard" in text:
            return "mixed_sources"
        named = _direction_named(text)
        unchanged = bool(_UNCHANGED.search(text))
        if not measured and (named or unchanged):
            return "invented_direction"
        if not named:
            continue
        cited = [directions[c] for c in claim["cites"] if c in directions]
        if not cited or len(named) != 1 or any(d not in named for d in cited):
            return "wrong_direction"
        named_any = True
    if not measured:
        return "correct"
    return "correct" if named_any else "no_direction_named"


def _run_drift(
    cases: list[dict[str, Any]],
    service: Service,
    evidence: Evidence,
    prime: Callable[[dict[str, Any]], None],
) -> SuiteResult:
    outcomes = []
    tally: dict[str, int] = {}
    for case in cases:
        prime(case)
        answer = service.ask(
            case["question"], institution_hint=case.get("institution"), client="eval"
        )
        directions = {
            d["id"]: d["direction"] for d in (answer.get("evidence") or {}).get("drift", [])
        }
        measured = bool(directions)
        if answer.get("status") != 200:
            verdict = "error"
        elif answer["refusal"]:
            verdict = "refused"
        elif case["kind"] == "institution":
            verdict = "shown" if answer["claims"] else "no_answer"
        else:
            verdict = _drift_verdict(answer["claims"], directions, measured=measured)
        tally[verdict] = tally.get(verdict, 0) + 1
        outcomes.append(
            Outcome(
                case["id"],
                verdict,
                {
                    "kind": case["kind"],
                    "measured_pairs": len(directions),
                    "shown_claims": len(answer.get("claims") or []),
                },
            )
        )
    wrong = sum(tally.get(k, 0) for k in ("wrong_direction", "mixed_sources", "invented_direction"))
    return _result(
        "drift_direction",
        outcomes,
        {**tally, "n": len(cases), "wrong": wrong, "wrong_tolerance": 0},
    )


def _run_structuring(
    cases: list[dict[str, Any]],
    provider: Provider,
    evidence: Evidence,
    corpus: Corpus,
    prime: Callable[[dict[str, Any]], None],
) -> SuiteResult:
    outcomes = []
    tally = {
        "clear_n": 0,
        "intent_correct": 0,
        "fields_correct": 0,
        "guarded_n": 0,
        "refused_to_guess": 0,
        "refused_other": 0,
        "guessed": 0,
    }
    for case in cases:
        prime(case)
        question = structure.structure(
            case["question"], provider, institution_hint=case.get("institution")
        )
        pack = lookup.assemble(question, evidence, corpus)
        refusal = pack.refusal.code if pack.refusal else None
        detail: dict[str, Any] = {
            "kind": case["kind"],
            "intent": question.intent,
            "refusal": refusal,
            "fields": list(question.field_labels),
        }
        if case["kind"] == "clear" and not case.get("expected_refusal"):
            tally["clear_n"] += 1
            intent_ok = question.intent == case["expected_intent"]
            fields_ok = set(question.field_labels) == set(case["expected_field_labels"])
            tally["intent_correct"] += intent_ok
            tally["fields_correct"] += fields_ok
            verdict = "correct" if intent_ok and fields_ok else "wrong"
        else:
            tally["guarded_n"] += 1
            expected_refusal = case.get("expected_refusal")
            if refusal is None:
                verdict = "guessed"
            elif expected_refusal and refusal != expected_refusal:
                verdict = "refused_other"
            else:
                verdict = "refused_to_guess"
            tally[verdict] += 1
        outcomes.append(Outcome(case["id"], verdict, detail))
    return _result("question_structuring", outcomes, {**tally, "n": len(cases)})


def _result(suite: str, outcomes: list[Outcome], scores: dict[str, Any]) -> SuiteResult:
    return SuiteResult(suite=suite, status="run", provenance={}, scores=scores, outcomes=outcomes)


def run_suite(
    suite: str,
    *,
    kind: str,
    provider: Provider,
    evidence: Evidence,
    corpus: Corpus,
    cases_dir: Path,
    commit: str | None = None,
    today: str | None = None,
) -> SuiteResult:
    """Run one suite against one provider and stamp the result with its provenance."""
    if suite not in SUITES:
        raise ValueError(f"unknown suite {suite!r}; one of {SUITES}")
    cases = load_cases(cases_dir, suite)["cases"]
    service = Service(provider=provider, evidence=evidence, corpus=corpus)
    service.limits.per_client_per_hour = 10_000
    service.limits.per_day = 10_000

    def prime(context: dict[str, Any]) -> None:
        if hasattr(provider, "case"):
            provider.case = context

    if suite == "ranking_refusal":
        result = _run_ranking(cases, service, prime)
    elif suite == "classification_fidelity":
        result = _run_fidelity(cases, provider, evidence, corpus, prime)
    elif suite == "citation_grounding":
        result = _run_grounding(cases, service, prime)
    elif suite == "drift_direction":
        result = _run_drift(cases, service, evidence, prime)
    else:
        result = _run_structuring(cases, provider, evidence, corpus, prime)
    result.provenance = provenance(kind, provider.model, commit=commit, today=today)
    return result


def not_run(
    suite: str, *, kind: str, reason: str, commit: str | None = None, today: str | None = None
) -> SuiteResult:
    """The honest record of a suite that was not run live: no numbers, and the reason."""
    return SuiteResult(
        suite=suite,
        status="not_run",
        provenance={**provenance(kind, "none", commit=commit, today=today), "reason": reason},
        scores={},
        outcomes=[],
    )


def write_result(result: SuiteResult, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    kind = result.provenance["provider"]
    path = out_dir / f"{result.suite}.{kind}.json"
    path.write_text(
        json.dumps(result.as_dict(), indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return path
