"""A portable rule file for the five-state classifier, and the CSV verb that applies it.

:mod:`disclosed.disclosure` is the careful part of this project: the one place that decides
whether a value is a measurement, a withheld measurement, a question that was never asked, a gap,
or a number nobody should believe. :mod:`disclosed.fields` then binds those rules to the twelve
federal columns this repository grades.

Everything in this module exists so that the first of those can be used without the second. A
sibling project reading a different CSV needs the distinction and not the College Scorecard, and
until now the only way to get it was to copy :func:`~disclosed.disclosure.classify`'s five keyword
arguments into a new call site and hope nobody forgot ``sentinels``. Forgetting it is not a
hypothetical: it is precisely how ``-1`` becomes a measurement of minus one.

So a rule file states the rules as data, this module refuses the ones it cannot honour, and
``disclosed classify-csv`` writes a state column beside every value column it was given rules for.

Two refusals here are the whole point of the module and are worth reading before changing them.

A rule naming a column the CSV does not have is an error, not an empty result. Classifying an
absent column would mark every row ``missing`` and produce a file saying that nobody reported it,
when what happened is that this file does not carry the question. That is the project's own
headline failure mode -- absence rendered as a value -- reproduced by the tool built to prevent it.

A rule naming an applicability predicate this build does not implement is an error, not a field
that applies to everyone. Treating an unknown predicate as "always applies" moves rows the rule
never reached into the denominator, which manufactures violations out of a rule that was never
written down.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from pathlib import Path
from typing import Any, Final

from .disclosure import CLASSIFICATIONS, Disclosure
from .fields import APPLICABILITY_PREDICATES, Field, predicate_name

__all__ = [
    "RULES_FORMAT_VERSION",
    "STATE_COLUMN_SUFFIX",
    "Rule",
    "RuleFileError",
    "classify_rows",
    "classify_table",
    "load_rules",
    "rules_from_payload",
    "rules_to_payload",
    "schema",
]

# The rule file's own version, which is the format's and not the project's. It is separate from
# the package version on purpose: ADR 0001 says this repository has cut no release, and a
# consumer pinning a rule file needs to know whether their file still parses, not what the site
# was rendered from that week.
RULES_FORMAT_VERSION: Final[int] = 1

# Appended to a value column's name to make the column that states how that value is absent.
# One suffix, in one place, because a consumer joining on it should not have to guess.
STATE_COLUMN_SUFFIX: Final[str] = "_disclosure"

_SCHEMA_ID: Final[str] = (
    "https://chelseakr.github.io/disclosed/schema/classification.v1.schema.json"
)

# Every key a rule object may carry. Unknown keys are refused rather than ignored: a rule file
# with ``credible_maximum`` in it is a file whose author believed they had set an upper bound, and
# silently dropping the key would classify every implausibly large value as a real measurement.
_RULE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "column",
        "label",
        "credible_min",
        "credible_max",
        "zero_is_credible",
        "sentinels",
        "text_is_a_value",
        "applies_when",
    }
)
_TOP_LEVEL_KEYS: Final[frozenset[str]] = frozenset({"version", "rules"})


class RuleFileError(ValueError):
    """A rule file this build will not act on, with the reason in the message.

    Every raise in this module is a refusal to guess. Nothing here falls back to a default,
    because every available default -- no bounds, no sentinels, applies to everyone -- is the
    permissive one, and a permissive default in a classifier is how an absence becomes a value.
    """


@dataclass(frozen=True, slots=True)
class Rule:
    """One column's classification rules, in the form a rule file can carry.

    A thin projection of :class:`~disclosed.fields.Field`: the parts that can be written down,
    without the ``rationale``, ``weight`` and ``statute`` that only mean something inside this
    project's grading.
    """

    column: str
    label: str = ""
    credible_min: float | None = None
    credible_max: float | None = None
    zero_is_credible: bool = True
    sentinels: Mapping[str, Disclosure] = dataclass_field(default_factory=dict)
    text_is_a_value: bool = False
    applies_when: str | None = None

    @property
    def state_column(self) -> str:
        """Name of the column stating how this column's value is absent."""
        return self.column + STATE_COLUMN_SUFFIX

    @property
    def predicate(self) -> Callable[[Mapping[str, object]], bool] | None:
        """The applicability predicate, already known to exist because parsing checked."""
        if self.applies_when is None:
            return None
        return APPLICABILITY_PREDICATES[self.applies_when]

    def classify(self, record: Mapping[str, object]) -> Disclosure:
        """Classify this column's value in one row.

        Delegates to :meth:`disclosed.fields.Field.classify` rather than reimplementing the
        argument spread, so that a rule applied from a file and a field graded in this repository
        cannot drift apart: there is one classifier and one caller of it.
        """
        return self.as_field().classify(record)

    def as_field(self) -> Field:
        """This rule as a :class:`~disclosed.fields.Field`, for the one classifier to consume."""
        return Field(
            key=self.column,
            label=self.label or self.column,
            credible_min=self.credible_min,
            credible_max=self.credible_max,
            zero_is_credible=self.zero_is_credible,
            rationale="",
            sentinels=dict(self.sentinels),
            text_is_a_value=self.text_is_a_value,
            applies_when=self.predicate,
        )


def _require_mapping(value: object, what: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RuleFileError(f"{what} must be an object, not {type(value).__name__}")
    for key in value:
        if not isinstance(key, str):
            raise RuleFileError(f"{what} has a non-string key {key!r}")
    return value


def _bound(raw: object, what: str) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise RuleFileError(f"{what} must be a number or null, not {type(raw).__name__}")
    return float(raw)


def _flag(raw: object, what: str, default: bool) -> bool:
    if raw is None:
        return default
    if not isinstance(raw, bool):
        raise RuleFileError(f"{what} must be true or false, not {type(raw).__name__}")
    return raw


def _sentinels(raw: object, column: str) -> dict[str, Disclosure]:
    """Parse one rule's sentinel map, refusing any state the classifier cannot produce.

    A sentinel is the source's own code for an absence, and the value it maps to is one of the
    five states. A sixth word here would be a state nothing downstream knows how to render, so it
    is refused at the door rather than carried into a report where the site would have to say
    "unrecognized classification" about a value this tool wrote itself.
    """
    if raw is None:
        return {}
    mapping = _require_mapping(raw, f"rule {column!r}: sentinels")
    parsed: dict[str, Disclosure] = {}
    for token, state in mapping.items():
        if not isinstance(state, str) or state not in CLASSIFICATIONS:
            raise RuleFileError(
                f"rule {column!r}: sentinel {token!r} maps to {state!r}, which is not one of the "
                f"five classifications ({', '.join(sorted(CLASSIFICATIONS))})"
            )
        parsed[token] = Disclosure(state)
    return parsed


def _rule_from_payload(raw: object, seen: set[str]) -> Rule:
    entry = _require_mapping(raw, "each entry of 'rules'")
    unknown = set(entry) - _RULE_KEYS
    if unknown:
        raise RuleFileError(
            f"unknown key(s) in a rule: {', '.join(sorted(unknown))}. Known keys are "
            f"{', '.join(sorted(_RULE_KEYS))}"
        )

    column = entry.get("column")
    if not isinstance(column, str) or not column.strip():
        raise RuleFileError("every rule needs a non-empty 'column'")
    if column in seen:
        raise RuleFileError(f"rule {column!r} appears twice; a column gets one set of rules")
    seen.add(column)

    label = entry.get("label", "")
    if not isinstance(label, str):
        raise RuleFileError(f"rule {column!r}: 'label' must be a string")

    credible_min = _bound(entry.get("credible_min"), f"rule {column!r}: credible_min")
    credible_max = _bound(entry.get("credible_max"), f"rule {column!r}: credible_max")
    if credible_min is not None and credible_max is not None and credible_min > credible_max:
        raise RuleFileError(
            f"rule {column!r}: credible_min {credible_min} is above credible_max {credible_max}, "
            "so no value could ever be credible"
        )

    applies_when = entry.get("applies_when")
    if applies_when is not None:
        if not isinstance(applies_when, str):
            raise RuleFileError(f"rule {column!r}: 'applies_when' must be a name or null")
        if applies_when not in APPLICABILITY_PREDICATES:
            raise RuleFileError(
                f"rule {column!r}: no applicability predicate named {applies_when!r} is "
                f"implemented here. This build knows "
                f"{', '.join(sorted(APPLICABILITY_PREDICATES))}. Refusing rather than treating "
                "the field as applying to every row, which would grade rows the rule never "
                "reached."
            )

    return Rule(
        column=column,
        label=label,
        credible_min=credible_min,
        credible_max=credible_max,
        zero_is_credible=_flag(
            entry.get("zero_is_credible"), f"rule {column!r}: zero_is_credible", True
        ),
        sentinels=_sentinels(entry.get("sentinels"), column),
        text_is_a_value=_flag(
            entry.get("text_is_a_value"), f"rule {column!r}: text_is_a_value", False
        ),
        applies_when=applies_when,
    )


def rules_from_payload(payload: object) -> tuple[Rule, ...]:
    """Parse a rule file, refusing anything this build cannot honour exactly as written.

    Raises:
        RuleFileError: With a message naming what was wrong and, where a permissive reading
            existed, why it was not taken.
    """
    document = _require_mapping(payload, "a rule file")
    unknown = set(document) - _TOP_LEVEL_KEYS
    if unknown:
        raise RuleFileError(
            f"unknown top-level key(s): {', '.join(sorted(unknown))}. Known keys are "
            f"{', '.join(sorted(_TOP_LEVEL_KEYS))}"
        )

    version = document.get("version")
    if version != RULES_FORMAT_VERSION:
        raise RuleFileError(
            f"rule file declares version {version!r}; this build reads version "
            f"{RULES_FORMAT_VERSION}"
        )

    raw_rules = document.get("rules")
    if not isinstance(raw_rules, list) or not raw_rules:
        raise RuleFileError("'rules' must be a non-empty list")

    seen: set[str] = set()
    return tuple(_rule_from_payload(entry, seen) for entry in raw_rules)


def rules_to_payload(fields: Iterable[Field]) -> dict[str, Any]:
    """This repository's own fields written out in the portable rule format.

    Every field this project grades round-trips through :func:`rules_from_payload`, which is the
    only honest test that the format can express the rules it claims to. A field whose
    applicability predicate has no registered name is refused here rather than written out
    without it: a rule file that silently dropped ``applies_when`` would apply the field to every
    row it met.
    """
    rules: list[dict[str, Any]] = []
    for field in fields:
        name = predicate_name(field.applies_when)
        if field.applies_when is not None and name is None:
            raise RuleFileError(
                f"field {field.key!r} has an applicability predicate with no registered name, so "
                "it cannot be written to a rule file without losing it. Register it in "
                "fields.APPLICABILITY_PREDICATES."
            )
        rules.append(
            {
                "column": field.key,
                "label": field.label,
                "credible_min": field.credible_min,
                "credible_max": field.credible_max,
                "zero_is_credible": field.zero_is_credible,
                "sentinels": {
                    token: state.value for token, state in sorted(field.sentinels.items())
                },
                "text_is_a_value": field.text_is_a_value,
                "applies_when": name,
            }
        )
    return {"version": RULES_FORMAT_VERSION, "rules": rules}


def schema() -> dict[str, Any]:
    """The JSON Schema for the five-state enum and the rule-file shape.

    Built from :data:`~disclosed.disclosure.CLASSIFICATIONS` and
    :data:`~disclosed.fields.APPLICABILITY_PREDICATES` rather than written out by hand, so that a
    sixth state or a new predicate cannot exist in the code and be absent from the published
    contract. The rendered form is committed at ``schema/classification.v1.schema.json`` and a
    test holds the two against each other; the committed file is what a consumer downloads, and
    the point of committing it is that it can be found to disagree.
    """
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": _SCHEMA_ID,
        "title": "disclosed classification rules",
        "description": (
            "How a value is absent, which is not the same as what the value is. Five states: a "
            "credible reported value, a disclosed value outside its credible range, a value "
            "withheld on purpose, a question that does not apply, and a gap with no stated "
            "reason. Rendered naively the last four all become 0, and a reader cannot tell a "
            "college that admits nobody from a college that declined to say."
        ),
        "type": "object",
        "additionalProperties": False,
        "required": ["version", "rules"],
        "properties": {
            "version": {
                "description": "Version of this rule-file format, not of the disclosed package.",
                "const": RULES_FORMAT_VERSION,
            },
            "rules": {
                "description": "One entry per column to classify.",
                "type": "array",
                "minItems": 1,
                "items": {"$ref": "#/$defs/rule"},
            },
        },
        "$defs": {
            "disclosure": {
                "title": "disclosure state",
                "description": "The five states. There is no sixth.",
                "type": "string",
                "enum": sorted(CLASSIFICATIONS),
            },
            "rule": {
                "type": "object",
                "additionalProperties": False,
                "required": ["column"],
                "properties": {
                    "column": {
                        "description": "Column header in the CSV being classified.",
                        "type": "string",
                        "minLength": 1,
                    },
                    "label": {
                        "description": "Plain-language name for the column.",
                        "type": "string",
                    },
                    "credible_min": {
                        "description": (
                            "Inclusive lower bound of the credible range; null disables it."
                        ),
                        "type": ["number", "null"],
                    },
                    "credible_max": {
                        "description": (
                            "Inclusive upper bound of the credible range; null disables it."
                        ),
                        "type": ["number", "null"],
                    },
                    "zero_is_credible": {
                        "description": (
                            "Whether an exact zero is a believable measurement here. False for "
                            "rates and prices, where zero is nearly always a reporting artifact."
                        ),
                        "type": "boolean",
                        "default": True,
                    },
                    "sentinels": {
                        "description": (
                            "The source's own missing-data codes, mapped to what each one means. "
                            "IPEDS encodes three different absences as -1, -2 and -3; without "
                            "this they are graded as measurements of minus one, two and three."
                        ),
                        "type": "object",
                        "additionalProperties": {"$ref": "#/$defs/disclosure"},
                    },
                    "text_is_a_value": {
                        "description": (
                            "Whether a non-numeric string is the measurement rather than noise. "
                            "True for URL columns."
                        ),
                        "type": "boolean",
                        "default": False,
                    },
                    "applies_when": {
                        "description": (
                            "Name of an applicability predicate implemented by the reader, or "
                            "null for a column that applies to every row. A name the reader does "
                            "not implement is refused, never treated as 'applies to everyone'."
                        ),
                        "type": ["string", "null"],
                        "enum": [*sorted(APPLICABILITY_PREDICATES), None],
                    },
                },
            },
        },
    }


def classify_rows(
    rows: Sequence[Mapping[str, object]], rules: Sequence[Rule]
) -> list[dict[str, str]]:
    """Classify each row against each rule, returning the state column values only.

    Raises:
        RuleFileError: If a rule names a column no row carries. See the module docstring: an
            absent column is a question this file does not ask, and marking every row ``missing``
            would publish that as a finding about the publisher.
    """
    present: set[str] = set()
    for row in rows:
        present.update(row)
    absent = [rule.column for rule in rules if rule.column not in present]
    if absent:
        raise RuleFileError(
            f"the input has no column named {', '.join(repr(c) for c in sorted(absent))}. "
            "Refusing: classifying a column that is not there would mark every row 'missing' and "
            "publish a gap in this file as a gap in what the publisher disclosed."
        )
    return [{rule.state_column: rule.classify(row).value for rule in rules} for row in rows]


def classify_table(text: str, rules: Sequence[Rule]) -> str:
    """Read a CSV and return it with a state column beside every column that has rules.

    Column order is preserved and each state column is written immediately after the value column
    it describes, so that a reader scanning the file sees the value and its state together.
    Columns with no rules pass through untouched.

    Raises:
        RuleFileError: If the input carries no header, or a rule names a column it does not have.
    """
    reader = csv.DictReader(io.StringIO(text))
    header = reader.fieldnames
    if not header:
        raise RuleFileError("the input has no header row, so no column can be named")

    rows = [dict(row) for row in reader]
    states = classify_rows(rows, rules)

    by_column = {rule.column: rule for rule in rules}
    out_header: list[str] = []
    for column in header:
        out_header.append(column)
        rule = by_column.get(column)
        if rule is not None:
            out_header.append(rule.state_column)

    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=out_header, lineterminator="\n")
    writer.writeheader()
    for row, state in zip(rows, states, strict=True):
        writer.writerow({**row, **state})
    return buffer.getvalue()


def load_rules(path: Path) -> tuple[Rule, ...]:
    """Read and parse a rule file from disk.

    Raises:
        RuleFileError: If the file is not JSON, or is JSON this build will not act on.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuleFileError(f"{path} is not valid JSON: {exc}") from exc
    return rules_from_payload(payload)
