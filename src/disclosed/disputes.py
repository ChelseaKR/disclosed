"""The channel by which a graded institution can say, on its own page, that we are wrong.

The README's contract is one sentence: *a scorecard that cannot be disputed line by line is not a
scorecard, it is an accusation.* Every credible range carries a written rationale so that an
institution arguing with a grade is arguing with a stated rule. What was missing was the other
half — somewhere for the argument to land that a reader of the page would ever see. The only
channel was a GitHub issue, which is invisible from the finding it is about.

A dispute is a committed file. ``disputes/<unit_id>.json`` names the field, the classification
being disputed, the institution's own statement, a URL to whatever it is pointing at, the date,
and who filed it. It arrives by pull request through an issue template, is reviewed like any
other change, and is rendered beside the finding with the statement quoted **verbatim**.

Four rules, and each of them is the reason this is a module rather than a paragraph in the site
generator.

**A dispute never changes a classification.** It is rendered beside the grade, not folded into
it. Report bytes, grade bytes and every published figure are identical with and without disputes,
and a test asserts that over the committed report. A channel that silently moved a grade would be
a scoring input wearing a comment's clothes, and the institution best at filing paperwork would
score highest. If a finding is wrong, the fix is a change to the rule or to the data, in a commit
that says so.

**A dispute naming something that was never graded is refused.** A statement attached to an
institution this project did not grade, or to a field it does not check, would render as a
rebuttal of a finding that does not exist. That is an absence rendered as a value with the sign
flipped, and it fails the build rather than rendering.

**The statement is quoted, never summarised.** It is somebody else's account of their own
disclosure. Paraphrasing it here would make this project the author of the other side of its own
argument.

**Nothing is fetched.** ``evidence_url`` is rendered as a link and never followed. Whether the
page behind it says what the statement says is a judgement, and this module's job is to carry the
claim, attributed, not to adjudicate it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from .disclosure import CLASSIFICATIONS

__all__ = [
    "DIRECTORY",
    "SCHEMA_PATH",
    "Dispute",
    "DisputeError",
    "by_institution",
    "check_against",
    "load",
    "schema",
]

#: Where committed disputes live, relative to the repository root.
DIRECTORY: Final[str] = "disputes"

#: Where the dispute schema is published, relative to the site root. Written by
#: :func:`disclosed.site.build` alongside the classification schema, because an ``$id`` that does
#: not resolve is a 404 with a version number on it — a lesson this repository learned once
#: already, and not one worth learning twice in the same directory.
SCHEMA_PATH: Final[str] = "schema/dispute.v1.schema.json"
SCHEMA_ORIGIN: Final[str] = "https://chelseakr.github.io/disclosed"

#: The keys a dispute file may carry. Every one is required. There is no optional field, because
#: each of them is part of what makes the statement attributable: without a date it cannot be read
#: against the grade it disputes, and without a filer it is an anonymous assertion on somebody
#: else's page.
_KEYS: Final[tuple[str, ...]] = (
    "unit_id",
    "field",
    "disputed_classification",
    "statement",
    "evidence_url",
    "filed",
    "filed_by",
)

#: ISO calendar dates only. Not parsed into a datetime and not compared against a clock: the date
#: is a fact the filer states, it is rendered next to their statement, and this module has no
#: business deciding that a dispute is too old to show.
_DATE: Final[re.Pattern[str]] = re.compile(r"^\d{4}-\d{2}-\d{2}$")

#: A file name that is a unit id and nothing else. The name becomes part of a rendered page and is
#: matched against a graded record, and a third party writes it.
_FILENAME: Final[re.Pattern[str]] = re.compile(r"^[0-9]+$")

#: Schemes a statement may cite. ``javascript:`` and ``data:`` are the two that turn a link on
#: somebody else's institution page into something else entirely, and an allowlist is the only
#: form of this check that does not need updating every time a new scheme is invented.
_SCHEMES: Final[frozenset[str]] = frozenset({"https", "http"})


class DisputeError(ValueError):
    """A dispute this build will not render, with the reason in the message.

    Raised rather than skipped. A dispute that failed to load and was quietly dropped would leave
    an institution believing its statement was published when the page never carried it, which is
    a worse failure than refusing the build.
    """


@dataclass(frozen=True, slots=True)
class Dispute:
    """One institution's stated objection to one finding about it."""

    unit_id: str
    field: str
    disputed_classification: str
    statement: str
    evidence_url: str
    filed: str
    filed_by: str

    def as_dict(self) -> dict[str, str]:
        return {key: str(getattr(self, key)) for key in _KEYS}


def _load_object(path: Path) -> dict[str, str]:
    """Read the file and check its shape, before anything looks at what it says."""
    if not _FILENAME.match(path.stem):
        raise DisputeError(
            f"{path.name} is not named for a unit id. A dispute file is keyed on the institution "
            "it is about, so the name is how it is matched to a graded record."
        )
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DisputeError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise DisputeError(f"{path} is not a dispute object")

    missing = sorted(set(_KEYS) - set(payload))
    unknown = sorted(set(payload) - set(_KEYS))
    if missing:
        raise DisputeError(f"{path} is missing {', '.join(missing)}")
    if unknown:
        # Refused rather than ignored, for the same reason the rule-file parser refuses an unknown
        # key: a filer who wrote one believed they had said something, and dropping it publishes a
        # statement that is missing the part they cared about.
        raise DisputeError(f"{path} carries keys this build does not know: {', '.join(unknown)}")
    for key in _KEYS:
        if not isinstance(payload[key], str) or not payload[key].strip():
            raise DisputeError(f"{path}: {key} must be a non-empty string")
    return {key: str(payload[key]) for key in _KEYS}


def _read(path: Path) -> Dispute:
    payload = _load_object(path)
    if payload["unit_id"] != path.stem:
        raise DisputeError(
            f"{path}: the file is named for unit {path.stem} and its unit_id says "
            f"{payload['unit_id']}. One of the two is wrong and there is no way to tell which."
        )
    if payload["disputed_classification"] not in CLASSIFICATIONS:
        raise DisputeError(
            f"{path}: disputed_classification is {payload['disputed_classification']!r}, which is "
            f"not one of the five states ({', '.join(sorted(CLASSIFICATIONS))})"
        )
    if not _DATE.match(payload["filed"]):
        raise DisputeError(f"{path}: filed is {payload['filed']!r}, not an ISO calendar date")
    scheme = payload["evidence_url"].split(":", 1)[0].lower()
    if scheme not in _SCHEMES:
        raise DisputeError(
            f"{path}: evidence_url uses the {scheme!r} scheme. Only "
            f"{', '.join(sorted(_SCHEMES))} are rendered, because this URL becomes a link on a "
            "page about somebody else and the other schemes are how that stops being a link."
        )
    return Dispute(**{key: payload[key] for key in _KEYS})


def load(directory: Path) -> tuple[Dispute, ...]:
    """Read every committed dispute in ``directory``, in a fixed order.

    Sorted by unit id and then by field, so that adding a dispute changes the page it is about and
    nothing else. An empty or absent directory yields no disputes, which is the honest state of a
    channel nobody has used yet — and it is why the render is absent rather than an empty heading.

    Raises:
        DisputeError: If any file in the directory cannot be read as a dispute.
    """
    if not directory.is_dir():
        return ()
    found = [_read(path) for path in sorted(directory.glob("*.json"))]
    return tuple(sorted(found, key=lambda d: (d.unit_id, d.field)))


def check_against(disputes: Sequence[Dispute], report: Mapping[str, Any]) -> None:
    """Refuse a dispute that argues with a finding this report does not make.

    Two ways that happens and both are refused by name: an institution the report did not grade,
    and a field it does not carry for that institution. Either would render as a rebuttal of a
    finding that does not exist, which is this project's own defect class with the sign flipped —
    an absence published as a fact, in the voice of the party being graded.

    The classification is checked too. A dispute that says "you marked this missing" against a
    record now classified ``reported`` is stale rather than wrong, and rendering it beside a state
    it does not name would put words in the institution's mouth.

    Raises:
        DisputeError: Naming the unit, the field and what the report actually says.
    """
    graded: dict[str, dict[str, Any]] = {}
    for record in report.get("grades", []):
        unit_id = record.get("unit_id")
        if isinstance(unit_id, str) and unit_id:
            graded[unit_id] = dict(record.get("fields", {}))
    for dispute in disputes:
        if dispute.unit_id not in graded:
            raise DisputeError(
                f"dispute for unit {dispute.unit_id} names an institution this report does not "
                "grade, so there is no finding for it to be beside"
            )
        fields = graded[dispute.unit_id]
        if dispute.field not in fields:
            raise DisputeError(
                f"dispute for unit {dispute.unit_id} names the field {dispute.field!r}, which "
                f"this report does not carry. It grades: {', '.join(sorted(fields))}"
            )
        actual = fields[dispute.field]
        if actual != dispute.disputed_classification:
            raise DisputeError(
                f"dispute for unit {dispute.unit_id} disputes a "
                f"{dispute.disputed_classification!r} classification of {dispute.field!r}, and "
                f"the report classifies it {actual!r}. The dispute is stale, not wrong: rendering "
                "it beside a state it does not name would put words in the institution's mouth."
            )


def by_institution(disputes: Iterable[Dispute]) -> dict[str, list[Dispute]]:
    """Disputes keyed on the unit id the page is for, each list in field order."""
    grouped: dict[str, list[Dispute]] = {}
    for dispute in disputes:
        grouped.setdefault(dispute.unit_id, []).append(dispute)
    return grouped


def schema() -> dict[str, Any]:
    """The published JSON Schema for a dispute file.

    Built from the same constants the parser uses, so a key added to one cannot be absent from the
    other. The five states come from :data:`~disclosed.disclosure.CLASSIFICATIONS` for the same
    reason the classification schema's do: there is no sixth, and a schema that could disagree
    about that is a second definition of the contract.
    """
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"{SCHEMA_ORIGIN}/{SCHEMA_PATH}",
        "title": "disclosed dispute",
        "description": (
            "A graded institution's stated objection to one finding about it, committed as "
            "disputes/<unit_id>.json and rendered verbatim beside the finding. A dispute never "
            "changes a classification; it is published next to one."
        ),
        "type": "object",
        "additionalProperties": False,
        "required": list(_KEYS),
        "properties": {
            "unit_id": {
                "description": (
                    "IPEDS unit id, as the College Scorecard publishes it, and the same as the "
                    "file name."
                ),
                "type": "string",
                "pattern": _FILENAME.pattern,
            },
            "field": {
                "description": (
                    "The graded field label this dispute is about, exactly as the report writes "
                    "it. A field the report does not carry is refused."
                ),
                "type": "string",
                "minLength": 1,
            },
            "disputed_classification": {
                "description": (
                    "The state the report gives that field. Carried so a dispute that has been "
                    "overtaken by a regrading is refused as stale rather than rendered beside a "
                    "finding it does not name."
                ),
                "type": "string",
                "enum": sorted(CLASSIFICATIONS),
            },
            "statement": {
                "description": (
                    "The institution's own words. Quoted verbatim on the page and never "
                    "summarised: paraphrasing would make this project the author of the other "
                    "side of its own argument."
                ),
                "type": "string",
                "minLength": 1,
            },
            "evidence_url": {
                "description": (
                    "Where the institution says the disclosure is. Rendered as a link and never "
                    "fetched: whether the page says what the statement says is a judgement, and "
                    "this channel carries the claim rather than adjudicating it."
                ),
                "type": "string",
                "pattern": "^https?://",
            },
            "filed": {
                "description": "ISO calendar date the dispute was filed.",
                "type": "string",
                "pattern": _DATE.pattern,
            },
            "filed_by": {
                "description": (
                    "Who filed it, in their own words. An anonymous statement on somebody else's "
                    "page is not attributable, so this is required."
                ),
                "type": "string",
                "minLength": 1,
            },
        },
    }
