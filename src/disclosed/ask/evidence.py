"""The evidence store: every classification the project has made, addressable by record id.

This is the only thing the question-answering layer is allowed to narrate. It is built at
start-up, in about a second, from the committed artifacts and nothing else -- the Scorecard
census capture, the committed sample, the three IPEDS collection years, the snapshot series, and
the crosswalk -- by the same grading code that produced every published figure, so a record here
and a cell on the site cannot disagree.

What a record carries, and what it deliberately does not:

* The **classification** of one field for one institution in one snapshot of one source --
  ``reported``, ``implausible``, ``suppressed``, ``not_applicable``, ``missing`` -- and, for
  ``not_applicable`` under an IPEDS rule, which condition of the rule excluded the institution.
* The **published value** only when the classification is ``implausible``. A zero admission rate
  is a finding and the number is the evidence for it. A ``reported`` value is not carried at all:
  the model narrating these records is never shown a graduation rate, an earnings figure, or a
  tuition, so it cannot compare them, rank on them, or be talked into doing either. That is the
  one control in this layer that does not depend on a prompt, and it is made here.

Field-level drift and cross-source contradictions are records too, keyed the same way, because
"did anything change" and "do the two federal sources disagree about this school" are questions
the project already answers with numbers and should answer the same way through a model.

The store is not committed. Serialised it is tens of megabytes, every byte of it derivable from
inputs that are committed and already held to byte-for-byte replay; the test suite pins its
counts and spot-checks its records against the published artifacts instead.
"""

from __future__ import annotations

import itertools
import json
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from .. import crosswalk
from ..disclosure import Disclosure
from ..drift import Snapshot, compare
from ..fields import ALL_FIELDS, FIELDS, IPEDS_FIELDS, Field
from ..grading import grade_institution
from ..sources import college_scorecard, ipeds

__all__ = [
    "ClassificationRecord",
    "ContradictionRecord",
    "DriftRecord",
    "Evidence",
    "Institution",
    "build",
]

SCORECARD: Final[str] = "College Scorecard"
IPEDS: Final[str] = "IPEDS"

_SLUG: Final[dict[str, str]] = {SCORECARD: "scorecard", IPEDS: "ipeds"}

# The IPEDS collection years committed under data/, oldest first.
IPEDS_YEARS: Final[tuple[int, ...]] = (2021, 2022, 2023)


@dataclass(frozen=True, slots=True)
class ClassificationRecord:
    """One field, one institution, one snapshot, one source."""

    id: str
    unit_id: str
    institution: str
    source: str
    snapshot: str
    field_key: str
    field_label: str
    classification: str
    not_applicable_because: str | None = None
    """For IPEDS ``not_applicable`` only: which condition of the applicability rule excluded the
    institution, in the words :mod:`disclosed.fields` uses. ``None`` otherwise."""

    implausible_value: object = None
    """The published value, carried only when the classification is ``implausible``."""

    statute: str = ""

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "unit_id": self.unit_id,
            "institution": self.institution,
            "source": self.source,
            "snapshot": self.snapshot,
            "field_key": self.field_key,
            "field_label": self.field_label,
            "classification": self.classification,
        }
        if self.not_applicable_because is not None:
            out["not_applicable_because"] = self.not_applicable_because
        if self.classification == Disclosure.IMPLAUSIBLE.value:
            out["implausible_value"] = self.implausible_value
        if self.statute:
            out["statute"] = self.statute
        return out


@dataclass(frozen=True, slots=True)
class DriftRecord:
    """One field's change between two snapshots of one source, as the project measured it."""

    id: str
    source: str
    earlier: str
    later: str
    field_label: str
    was_reported: int
    now_reported: int
    was_applicable: int
    now_applicable: int
    rate_change: float | None
    direction: str
    is_systemic: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "earlier": self.earlier,
            "later": self.later,
            "field_label": self.field_label,
            "was_reported": self.was_reported,
            "now_reported": self.now_reported,
            "was_applicable": self.was_applicable,
            "now_applicable": self.now_applicable,
            "rate_change": self.rate_change,
            "direction": self.direction,
            "is_systemic": self.is_systemic,
        }


@dataclass(frozen=True, slots=True)
class ContradictionRecord:
    """The two federal sources describing the same institution differently."""

    id: str
    unit_id: str
    institution: str
    attribute: str
    scorecard_value: str
    ipeds_value: str
    snapshot: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "unit_id": self.unit_id,
            "institution": self.institution,
            "attribute": self.attribute,
            "scorecard_value": self.scorecard_value,
            "ipeds_value": self.ipeds_value,
            "snapshot": self.snapshot,
        }


@dataclass(slots=True)
class Institution:
    unit_id: str
    name: str
    state: str | None
    records: list[ClassificationRecord] = field(default_factory=list)
    contradictions: list[ContradictionRecord] = field(default_factory=list)

    def snapshots(self, source: str) -> list[str]:
        return sorted({r.snapshot for r in self.records if r.source == source})

    def fields(self, source: str) -> list[str]:
        return sorted({r.field_label for r in self.records if r.source == source})


# -- building --------------------------------------------------------------------------------


def _record_id(source: str, snapshot: str, unit_id: str, field_: Field) -> str:
    return f"{_SLUG[source]}:{snapshot}:{unit_id}:{field_.column}"


_NOT_TITLE_IV: Final[str] = (
    "the institution is not a Title IV participant (PSET4FLG), so the rule does not reach it"
)

# Per field, the conditions its applicability rule checks after the two every rule shares, in the
# order :mod:`disclosed.fields` checks them: (IPEDS code, required value, reason when it is not).
_RULE_CONDITIONS: Final[dict[str, tuple[tuple[str, str, str], ...]]] = {
    "ipeds.NPRICURL": (
        (
            "ipeds.UGOFFER",
            "1",
            "the institution offers no undergraduate programme (UGOFFER), so the rule does not "
            "reach it",
        ),
        ("ipeds.PSET4FLG", "1", _NOT_TITLE_IV),
    ),
    "ipeds.ATHURL": (
        ("ipeds.PSET4FLG", "1", _NOT_TITLE_IV),
        (
            "ipeds.ATHASSOC",
            "1",
            "the institution did not tell IPEDS it belongs to a national athletic association "
            "(ATHASSOC), so the rule does not reach it",
        ),
    ),
}


def _why_not_applicable(field_: Field, record: Mapping[str, object]) -> str | None:
    """Which condition of an IPEDS applicability rule excluded this institution.

    Re-derived from the same codes the rule reads, in the order the rule checks them, so the
    reason given is the first one the rule itself would have stopped at.
    """
    if field_.applies_when is None:
        return None

    def code(key: str) -> str:
        return str(record.get(key, "")).strip()

    if code("ipeds.INSTCAT") == "-2":
        return "IPEDS files this row as an administrative unit (INSTCAT -2), not an institution"
    if code("ipeds.CYACTIVE") != "1":
        return "IPEDS does not list the institution as active in the collection year (CYACTIVE)"
    for key, required, reason in _RULE_CONDITIONS.get(field_.key, ()):
        if code(key) != required:
            return reason
    return "the field's applicability rule excluded the institution"


def _classify_records(
    records: Sequence[Mapping[str, Any]],
    *,
    source: str,
    snapshot: str,
    fields: Sequence[Field],
) -> Iterator[tuple[Institution, ClassificationRecord]]:
    for raw in records:
        record = dict(raw)
        grade = grade_institution(record, fields=fields)
        if not grade.unit_id:
            continue
        institution = Institution(
            unit_id=grade.unit_id, name=grade.name or "(name not published)", state=grade.state
        )
        for result in grade.results:
            classification = result.disclosure.value
            reason = (
                _why_not_applicable(result.field, record)
                if result.disclosure is Disclosure.NOT_APPLICABLE
                else None
            )
            yield (
                institution,
                ClassificationRecord(
                    id=_record_id(source, snapshot, grade.unit_id, result.field),
                    unit_id=grade.unit_id,
                    institution=institution.name,
                    source=source,
                    snapshot=snapshot,
                    field_key=result.field.key,
                    field_label=result.field.label,
                    classification=classification,
                    not_applicable_because=reason,
                    implausible_value=(
                        result.raw if result.disclosure is Disclosure.IMPLAUSIBLE else None
                    ),
                    statute=result.field.statute,
                ),
            )


def _snapshot_from_file(path: Path) -> Snapshot:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return Snapshot(
        taken=str(raw["taken"]),
        institutions=int(raw["institutions"]),
        reported={str(k): int(v) for k, v in raw["reported"].items()},
        missing={str(k): int(v) for k, v in raw["missing"].items()},
        applicable={str(k): int(v) for k, v in raw.get("applicable", {}).items()},
        source=str(raw.get("source", "")),
    )


def _drift_records(snapshot_dir: Path, *, source: str) -> Iterator[DriftRecord]:
    """Every pair of committed snapshots of one source, compared as the project compares them.

    Consecutive pairs and the first-to-last pair, which is how the README argues the threshold.
    ``compare`` itself refuses a cross-source pair; here the directory is the source, so the
    refusal is structural rather than checked.
    """
    files = sorted(p for p in snapshot_dir.glob("*.json"))
    snapshots = [_snapshot_from_file(p) for p in files]
    pairs = list(itertools.pairwise(snapshots))
    if len(snapshots) > 2:
        pairs.append((snapshots[0], snapshots[-1]))
    for earlier, later in pairs:
        for drift in compare(earlier, later):
            yield DriftRecord(
                id=f"drift:{_SLUG[source]}:{earlier.taken}..{later.taken}:{_slug(drift.field_label)}",
                source=source,
                earlier=earlier.taken,
                later=later.taken,
                field_label=drift.field_label,
                was_reported=drift.was_reported,
                now_reported=drift.now_reported,
                was_applicable=drift.was_applicable,
                now_applicable=drift.now_applicable,
                rate_change=drift.rate_change,
                direction=drift.direction,
                is_systemic=drift.is_systemic,
            )


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


@dataclass(slots=True)
class Evidence:
    """The loaded store. Lookups are exact; nothing here searches by similarity."""

    institutions: dict[str, Institution]
    drift: list[DriftRecord]
    built_from: dict[str, Any]
    _records: dict[str, ClassificationRecord] = field(default_factory=dict)
    _drift_by_id: dict[str, DriftRecord] = field(default_factory=dict)
    _contradictions_by_id: dict[str, ContradictionRecord] = field(default_factory=dict)
    _by_name: dict[str, list[str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for institution in self.institutions.values():
            for record in institution.records:
                self._records[record.id] = record
            for contradiction in institution.contradictions:
                self._contradictions_by_id[contradiction.id] = contradiction
            self._by_name.setdefault(_normalise_name(institution.name), []).append(
                institution.unit_id
            )
        for drift in self.drift:
            self._drift_by_id[drift.id] = drift

    def record(
        self, record_id: str
    ) -> ClassificationRecord | DriftRecord | ContradictionRecord | None:
        return (
            self._records.get(record_id)
            or self._drift_by_id.get(record_id)
            or self._contradictions_by_id.get(record_id)
        )

    def find(self, text: str) -> list[Institution]:
        """Institutions matching a unit id or a name, exactly after normalisation.

        Exact first: a unit id, then a whole normalised name. Failing both, institutions whose
        normalised name contains every word of the query, so "Grand Canyon" finds Grand Canyon
        University and "University" alone finds far too many to be an answer -- the caller
        decides what to do with more than one.
        """
        query = text.strip()
        if query in self.institutions:
            return [self.institutions[query]]
        key = _normalise_name(query)
        if key in self._by_name:
            return [self.institutions[u] for u in self._by_name[key]]
        words = key.split()
        if not words:
            return []
        return [
            inst
            for inst in self.institutions.values()
            if all(w in _normalise_name(inst.name).split() for w in words)
        ]

    def for_institution(
        self,
        unit_id: str,
        *,
        source: str | None = None,
        field_labels: Sequence[str] | None = None,
    ) -> list[ClassificationRecord]:
        institution = self.institutions.get(unit_id)
        if institution is None:
            return []
        wanted = set(field_labels) if field_labels else None
        return [
            r
            for r in institution.records
            if (source is None or r.source == source)
            and (wanted is None or r.field_label in wanted)
        ]

    def drift_for(
        self, *, source: str | None = None, field_label: str | None = None
    ) -> list[DriftRecord]:
        return [
            d
            for d in self.drift
            if (source is None or d.source == source)
            and (field_label is None or d.field_label == field_label)
        ]

    def snapshots(self, source: str) -> list[str]:
        return sorted(
            {
                r.snapshot
                for inst in self.institutions.values()
                for r in inst.records
                if r.source == source
            }
        )


def _normalise_name(name: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", name.lower().replace("&", " and ")).strip()


def _scorecard_snapshot_date(provenance: Mapping[str, Any] | None, fallback: str) -> str:
    finished = (provenance or {}).get("finished_at")
    return str(finished)[:10] if isinstance(finished, str) and finished else fallback


def build(
    data_dir: Path,
    *,
    sample_taken: str = "2026-08-05",
) -> Evidence:
    """Build the store from the committed inputs under ``data/``. No network, no key.

    ``sample_taken`` is the date the committed 600-institution sample was captured; it carries no
    provenance envelope (it predates ``disclosed fetch``), so the date is supplied rather than
    read, and it is the commit date of ``data/sample.json``.
    """
    institutions: dict[str, Institution] = {}

    def absorb(pairs: Iterator[tuple[Institution, ClassificationRecord]]) -> None:
        for institution, record in pairs:
            kept = institutions.setdefault(institution.unit_id, institution)
            if kept.state is None and institution.state is not None:
                kept.state = institution.state
            kept.records.append(record)

    census_raw = json.loads((data_dir / "census" / "scorecard.json").read_text(encoding="utf-8"))
    census, provenance = college_scorecard.read_capture(census_raw)
    census_taken = _scorecard_snapshot_date(provenance, "unknown")
    absorb(_classify_records(census, source=SCORECARD, snapshot=census_taken, fields=FIELDS))

    sample_raw = json.loads((data_dir / "sample.json").read_text(encoding="utf-8"))
    sample, _ = college_scorecard.read_capture(sample_raw)
    absorb(_classify_records(sample, source=SCORECARD, snapshot=sample_taken, fields=FIELDS))

    latest_directory: list[dict[str, Any]] = []
    for year in IPEDS_YEARS:
        directory = ipeds.load_institutions(
            year=year,
            cache=data_dir / f"HD{year}.zip",
            characteristics_cache=data_dir / f"IC{year}.zip",
        )
        absorb(_classify_records(directory, source=IPEDS, snapshot=str(year), fields=IPEDS_FIELDS))
        latest_directory = directory

    latest_year = str(IPEDS_YEARS[-1])
    for found in crosswalk.contradictions(census, latest_directory):
        institution = institutions.get(found.unit_id)
        if institution is None:
            continue
        institution.contradictions.append(
            ContradictionRecord(
                id=f"contradiction:{census_taken}+{latest_year}:{found.unit_id}:{_slug(found.field_label)}",
                unit_id=found.unit_id,
                institution=found.name or institution.name,
                attribute=found.field_label,
                scorecard_value=found.scorecard_value,
                ipeds_value=found.ipeds_value,
                snapshot=f"Scorecard {census_taken} against IPEDS {latest_year}",
            )
        )

    drift: list[DriftRecord] = []
    drift.extend(_drift_records(data_dir / "snapshots" / "ipeds", source=IPEDS))
    drift.extend(_drift_records(data_dir / "snapshots" / "scorecard", source=SCORECARD))

    return Evidence(
        institutions=institutions,
        drift=drift,
        built_from={
            "scorecard_census": {"snapshot": census_taken, "institutions": len(census)},
            "scorecard_sample": {"snapshot": sample_taken, "institutions": len(sample)},
            "ipeds_years": list(IPEDS_YEARS),
            "fields": {f.key: f.label for f in ALL_FIELDS},
        },
    )
