"""A per-institution receipt, and the replay that checks it against the capture it names.

Every classification this project publishes about a college is derived from one row of a
committed federal capture by rules that are in this repository. A receipt is that derivation
written down: which capture, which rules, which record, and what each field was classified as.
``verify`` takes the receipt and the capture and regrades the row, so the institution -- or
anyone else -- can check the claim without cloning the repository, reading the grader, or
trusting the person who published the page.

Three rules keep a receipt from becoming something it is not.

**A receipt never carries a reported value.** Only :attr:`~disclosed.disclosure.Disclosure.
IMPLAUSIBLE` fields carry the number, because there the number *is* the finding and an
institution cannot argue with a bound it has not been shown. Everywhere else the value is
withheld, and not for tidiness: a file listing every college's tuition, earnings and completion
rate beside a letter grade is a performance record, and this project grades disclosure. The
distinction survives only if it is enforced rather than remembered, so
``tests/test_receipts.py`` asserts it over the whole committed census rather than over a
fixture.

**A receipt states the capture it was made from, and the verifier says when it is handed a
different one.** A digest that does not match is not, on its own, a disagreement about grading:
replaying an August receipt against a September capture is a legitimate thing to want to do, and
answering it with "disagrees" would tell the reader the grader had changed its mind when what
changed was the input. So the mismatch is reported in its own field and stated in the first line
of the output, and the exit code stays reserved for the grader's own verdict.

**A receipt states the rules version, and a verifier holding a different one refuses.** This is
:mod:`disclosed.report_diff`'s rule one level down. An institution whose admission rate moved
from ``reported`` to ``implausible`` between two runs either published a different number or was
graded against a different credible range, and those are opposite findings. A comparison that
cannot tell them apart should say so instead of picking one.

The date on a receipt is the capture's walk date, never the clock: a receipt regenerated from the
same capture is byte-identical, so a diff in one means the data or the rules moved. A source that
carries no provenance -- ``data/sample.json`` is a bare array of records -- has no walk date, and
the receipt says so in words rather than substituting the day it was built.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from .disclosure import Disclosure
from .fields import FIELDS, Field
from .grading import RULES_VERSION, grade_institution
from .peers import peer_group_for

__all__ = [
    "AGREES",
    "DISAGREES",
    "NOT_IN_SOURCE",
    "RECEIPT_KIND",
    "RECEIPT_SCHEMA_VERSION",
    "UNREADABLE",
    "Disagreement",
    "ReceiptError",
    "ReceiptSource",
    "SourceIdentity",
    "Verification",
    "dumps",
    "identify_source",
    "load_source",
    "read_receipt",
    "receipt_for",
    "records_by_unit_id",
    "verify",
]

#: What a receipt says it is. Read before anything else in it, so a JSON file that happens to
#: have the right keys cannot be verified as a receipt by accident.
RECEIPT_KIND: Final[str] = "disclosed-institution-receipt"

#: The receipt format's own version, separate from :data:`disclosed.grading.RULES_VERSION` and
#: from the package version. The rules version says how the record was graded; this says how the
#: file is shaped. A reader pinning one needs to know which of the two moved.
RECEIPT_SCHEMA_VERSION: Final[int] = 1

#: ``verify-receipt`` exit codes. Named here rather than written as integers at the call site,
#: because they are the interface a caller scripts against.
AGREES: Final[int] = 0
DISAGREES: Final[int] = 1
NOT_IN_SOURCE: Final[int] = 2
UNREADABLE: Final[int] = 3

#: How much of a digest a citation prints. Enough to identify the capture in a footnote, and
#: short enough that a reader will actually copy it; the receipt itself carries all 64.
_SHORT_DIGEST: Final[int] = 12


class ReceiptError(ValueError):
    """A file that cannot be trusted to be a receipt.

    Raised rather than defaulted around. Every case -- unparseable JSON, a missing kind, a
    schema version this build does not implement, no unit id -- would otherwise be verified as
    though it were a receipt saying nothing, and a verifier that agrees with a file it could not
    read is the "gate that cannot fail" this project keeps finding in other people's work.
    """


@dataclass(frozen=True, slots=True)
class SourceIdentity:
    """Which bytes a receipt was made from, and when they were walked.

    ``walked_at`` is ``None`` for a source carrying no provenance, and stays ``None``. A receipt
    that filled it in from the clock would date the federal record to the day somebody happened
    to render a page.
    """

    name: str
    """The file's basename. Deliberately not its path: where a capture sits on the machine that
    rendered the site is not part of the claim, and publishing it would put a local directory
    layout into every institution page."""

    sha256: str

    walked_at: str | None

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "sha256": self.sha256, "walked_at": self.walked_at}

    @property
    def short_sha256(self) -> str:
        return self.sha256[:_SHORT_DIGEST]


def identify_source(path: Path, provenance: Mapping[str, Any] | None) -> SourceIdentity:
    """Digest a source file and read its walk date, if it has one.

    The digest is over the file as it sits on disk, not over the records parsed out of it, so a
    reader can check it with ``sha256sum`` and nothing else.
    """
    walked = provenance.get("walked_at") if provenance is not None else None
    return SourceIdentity(
        name=path.name,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        walked_at=str(walked) if isinstance(walked, str) and walked.strip() else None,
    )


def records_by_unit_id(records: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    """Index records by their unit id, dropping the ones that have none.

    Records without an id are excluded rather than keyed on ``""`` or on the string ``"None"``.
    Two unidentified rows sharing a key is not hypothetical here: it is the bug that once served
    one college its neighbour's peer evidence, and a receipt is a citable artifact, so the same
    collision would publish one institution's grade under another's name.
    """
    indexed: dict[str, Mapping[str, Any]] = {}
    for record in records:
        ident = record.get("id")
        if ident is None:
            continue
        indexed.setdefault(str(ident), record)
    return indexed


def _field_entry(field: Field, disclosure: Disclosure, raw: object) -> dict[str, Any]:
    """One field's line in a receipt.

    The value appears for exactly one classification. See the module docstring: an implausible
    value is the finding and has to be quotable for the institution to argue with it; every other
    value is a measurement this project has no business republishing beside a grade.
    """
    entry: dict[str, Any] = {
        "key": field.key,
        "label": field.label,
        "classification": disclosure.value,
        "rationale_anchor": field.anchor,
        "in_denominator": disclosure not in (Disclosure.SUPPRESSED, Disclosure.NOT_APPLICABLE),
        "weight": field.weight,
    }
    if disclosure is Disclosure.IMPLAUSIBLE:
        entry["value"] = raw
        entry["credible_min"] = field.credible_min
        entry["credible_max"] = field.credible_max
        entry["zero_is_credible"] = field.zero_is_credible
        entry["rationale"] = field.rationale
    return entry


def receipt_for(
    record: Mapping[str, Any],
    *,
    source: SourceIdentity,
    fields: Sequence[Field] = FIELDS,
) -> dict[str, Any]:
    """Build the receipt for one institution record.

    Deterministic in the record and the rules alone: nothing here reads the clock, the
    filesystem beyond the digest already taken, or the network.
    """
    grade = grade_institution(dict(record), fields=fields)
    peer_group, _ = peer_group_for(dict(record))
    return {
        "kind": RECEIPT_KIND,
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "rules_version": RULES_VERSION,
        "source": source.as_dict(),
        "unit_id": grade.unit_id,
        "name": grade.name,
        "state": grade.state,
        "peer_group": peer_group,
        "score": grade.score,
        "letter": grade.letter,
        "fields": [
            _field_entry(result.field, result.disclosure, result.raw) for result in grade.results
        ],
    }


@dataclass(frozen=True, slots=True)
class ReceiptSource:
    """The records a build may issue receipts from, and the identity of the file they came from.

    Held as one object so that a caller cannot hand the site records from one capture and the
    digest of another, which would publish a receipt naming bytes it was not made from.
    """

    identity: SourceIdentity
    records: Mapping[str, Mapping[str, Any]]

    def receipt(self, unit_id: str, *, fields: Sequence[Field] = FIELDS) -> dict[str, Any] | None:
        """This institution's receipt, or ``None`` when the source does not carry its record.

        ``None`` rather than a receipt with empty fields. A receipt for a record nobody holds
        would state a grade derived from nothing, and a page linking one would be citing an
        absence as evidence.
        """
        record = self.records.get(unit_id)
        return None if record is None else receipt_for(record, source=self.identity, fields=fields)


def load_source(path: Path, records: Sequence[Mapping[str, Any]], provenance: Any) -> ReceiptSource:
    """A :class:`ReceiptSource` from an already-parsed source file and its provenance."""
    return ReceiptSource(
        identity=identify_source(path, provenance if isinstance(provenance, dict) else None),
        records=records_by_unit_id(records),
    )


def read_receipt(raw: object) -> dict[str, Any]:
    """Check that a parsed JSON value really is a receipt this build can verify.

    Refuses rather than coerces at every step, including the schema version: a receipt written by
    a later format is a file whose fields may mean something else, and regrading it against this
    build's reading of them would produce a confident verdict about a claim nobody made.
    """
    if not isinstance(raw, dict):
        raise ReceiptError("not a JSON object")
    if raw.get("kind") != RECEIPT_KIND:
        raise ReceiptError(f"kind is {raw.get('kind')!r}, not {RECEIPT_KIND!r}")
    version = raw.get("schema_version")
    if version != RECEIPT_SCHEMA_VERSION:
        raise ReceiptError(
            f"schema_version {version!r} is not the version this build writes "
            f"({RECEIPT_SCHEMA_VERSION}); a later receipt is not read as an earlier one"
        )
    if not isinstance(raw.get("unit_id"), str) or not raw["unit_id"]:
        raise ReceiptError("carries no unit id, so there is no record to replay it against")
    if not isinstance(raw.get("fields"), list):
        raise ReceiptError("carries no field list")
    return raw


@dataclass(frozen=True, slots=True)
class Disagreement:
    """One thing the replay says differently from the receipt."""

    what: str
    """The field key, or ``score``, ``letter`` or ``rules_version``."""

    on_the_receipt: object
    replayed: object

    def sentence(self) -> str:
        return f"{self.what}: receipt says {self.on_the_receipt!r}, replay says {self.replayed!r}"


@dataclass(frozen=True, slots=True)
class Verification:
    """What a replay found, and the exit code it justifies."""

    unit_id: str
    found: bool
    same_source: bool
    disagreements: tuple[Disagreement, ...]

    @property
    def agreed(self) -> bool | None:
        """``True``/``False`` once the record was found, and ``None`` when it was not.

        Not ``False`` for an absent record. "The grader disagrees with this receipt" and "the
        capture you handed me does not contain this institution" are different answers, and
        collapsing the second into the first would report a missing row as a refuted claim.
        """
        return None if not self.found else not self.disagreements

    @property
    def exit_code(self) -> int:
        if not self.found:
            return NOT_IN_SOURCE
        return DISAGREES if self.disagreements else AGREES

    def as_dict(self) -> dict[str, Any]:
        return {
            "unit_id": self.unit_id,
            "found": self.found,
            "agreed": self.agreed,
            "same_source": self.same_source,
            "disagreements": [
                {"what": d.what, "on_the_receipt": d.on_the_receipt, "replayed": d.replayed}
                for d in self.disagreements
            ],
        }


def _receipt_classifications(receipt: Mapping[str, Any]) -> dict[str, object]:
    """The receipt's field classifications, keyed on the source field key.

    Keyed on ``key`` and not on ``label`` because the label is prose written for a reader and
    will be reworded; a comparison keyed on it would report every field as disagreeing the first
    time somebody improved a sentence.
    """
    stated: dict[str, object] = {}
    for entry in receipt["fields"]:
        if isinstance(entry, dict) and isinstance(entry.get("key"), str):
            stated[entry["key"]] = entry.get("classification")
    return stated


def verify(
    receipt: Mapping[str, Any],
    records: Sequence[Mapping[str, Any]],
    *,
    source: SourceIdentity,
    fields: Sequence[Field] = FIELDS,
) -> Verification:
    """Regrade the record the receipt names, and report every difference.

    A rules-version mismatch is reported as a disagreement in its own right and the field
    comparison still runs, so the output shows both: that the rules moved, and which fields moved
    with them. What it deliberately does not do is quietly decide the fields agree "under the old
    rules" -- this build only has the rules it has.
    """
    unit_id = str(receipt["unit_id"])
    record = records_by_unit_id(records).get(unit_id)
    stated_source = receipt.get("source")
    stated_digest = stated_source.get("sha256") if isinstance(stated_source, dict) else None
    same_source = stated_digest == source.sha256
    if record is None:
        return Verification(unit_id=unit_id, found=False, same_source=same_source, disagreements=())

    replayed = receipt_for(record, source=source, fields=fields)
    found: list[Disagreement] = []

    stated_rules = receipt.get("rules_version")
    if stated_rules != replayed["rules_version"]:
        found.append(Disagreement("rules_version", stated_rules, replayed["rules_version"]))

    stated_fields = _receipt_classifications(receipt)
    replayed_fields = _receipt_classifications(replayed)
    for key in sorted(set(stated_fields) | set(replayed_fields)):
        if stated_fields.get(key) != replayed_fields.get(key):
            found.append(Disagreement(key, stated_fields.get(key), replayed_fields.get(key)))

    for what in ("score", "letter"):
        if receipt.get(what) != replayed[what]:
            found.append(Disagreement(what, receipt.get(what), replayed[what]))

    return Verification(
        unit_id=unit_id, found=True, same_source=same_source, disagreements=tuple(found)
    )


def dumps(receipt: Mapping[str, Any]) -> str:
    """Serialise a receipt the one way this project writes JSON: sorted keys, two-space indent.

    One function rather than a `json.dumps` call at each site, so a receipt written beside a page
    and a receipt written by ``disclosed receipt`` are the same bytes and can be diffed.
    """
    return json.dumps(receipt, indent=2, sort_keys=True) + "\n"
