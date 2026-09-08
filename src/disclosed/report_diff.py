"""What changed for one institution between two reports, stated as five-state transitions.

:mod:`disclosed.drift` answers the population question: four hundred institutions stopped
publishing a field. This module answers the question that immediately follows it. Which four
hundred, and did an institution's grade move because it disclosed less or because this project
changed the rules underneath it?

The unit is the transition and never the value. ``reported -> missing`` is a fact about what a
publisher told the public. "The admission rate changed" is a fact about the institution, and this
project does not grade institutions on their numbers; a field whose value moved while its
classification held still therefore produces no transition at all, and a report diffed against
itself is empty.

Four absences are carried rather than resolved, for the same reason the rest of this package
carries them.

A report that does not say which rules graded it has not established that the same rules graded
both runs. The comparison records that it could not confirm it, instead of printing the
transitions under a heading that implies it did.

A grade with no ``unit_id`` cannot be matched to anything. It is counted as unmatchable and named
in the payload rather than keyed on the empty string, which is how two unidentified records once
collided one module over and a finding was published carrying its neighbour's peer group.

A classification word this build does not know is reported as unreadable rather than guessed at.
The tempting reading -- an unknown word is not ``reported``, so something must have moved --
manufactures a transition out of a version gap between the report and the code reading it.

A field graded in one report and not the other is not a transition either. Adding a field to the
graded set is a change in this project, not in the publisher, and :func:`disclosed.drift.compare`
skips such fields for exactly this reason. The two directions are skipped alike and **reported
apart**: a field graded earlier and not later is one this project stopped grading, a field graded
later and not earlier is one it started, and they send a reader to opposite places. Reported in a
single sentence, as they were, the sentence was true of both and named neither.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .disclosure import CLASSIFICATIONS
from .scope import scope_from_payload

__all__ = [
    "FrameMove",
    "InstitutionChange",
    "ReportDiff",
    "Transition",
    "as_payload",
    "compare_reports",
    "read_rules_version",
]

NOT_GRADEABLE = "not gradeable"
"""How a missing letter prints. Never ``"F"`` and never the empty string.

An institution whose every graded field was suppressed or inapplicable has no grade, which is a
different fact from having the worst one. :class:`disclosed.grading.InstitutionGrade` keeps that
distinction as ``None``; this is the only word allowed to stand in for it in output.
"""


def read_rules_version(report: Mapping[str, Any]) -> str:
    """The grading rules a report says it was produced under, or ``""`` when it does not say.

    Empty means unstated and is never treated as matching anything, which is the rule
    :attr:`disclosed.drift.Snapshot.source` already follows. Reports written before
    :data:`disclosed.grading.RULES_VERSION` existed are the reason this returns a word rather
    than raising: they are readable, they are just not self-describing, and a diff over them has
    to say so rather than assume the grader stood still.
    """
    stated = report.get("rules_version")
    return stated.strip() if isinstance(stated, str) else ""


def _source_of(report: Mapping[str, Any]) -> str:
    scope = scope_from_payload(dict(report))
    return scope.source if scope else ""


@dataclass(frozen=True, slots=True)
class Transition:
    """One field's move between two of the five states, for one institution."""

    field_label: str
    was: str
    now: str

    @property
    def label(self) -> str:
        """The transition as the product's own unit, e.g. ``reported -> missing``."""
        return f"{self.was} -> {self.now}"

    def as_dict(self) -> dict[str, Any]:
        return {"field_label": self.field_label, "was": self.was, "now": self.now}


@dataclass(frozen=True, slots=True)
class FrameMove:
    """An institution present in one report and absent from the other.

    Reported separately from every transition, because an institution that left the frame did not
    stop disclosing anything. It is the institution-level form of the denominator lesson in
    :mod:`disclosed.drift`: a population that moved is not a publisher that changed.
    """

    unit_id: str
    name: str | None
    state: str | None

    def as_dict(self) -> dict[str, Any]:
        return {"unit_id": self.unit_id, "name": self.name, "state": self.state}


@dataclass(frozen=True, slots=True)
class InstitutionChange:
    """Everything that moved for one institution, and nothing that did not."""

    unit_id: str
    name: str | None
    state: str | None
    transitions: tuple[Transition, ...]
    was_score: float | None
    now_score: float | None
    was_letter: str | None
    now_letter: str | None

    unreadable_fields: tuple[str, ...] = ()
    """Fields whose classification word this build cannot read, in either run.

    Carried on the institution rather than dropped, so a report from a newer version is visibly
    partially unread instead of quietly reading as unchanged.
    """

    @property
    def score_change(self) -> float | None:
        """Change in disclosure score, or ``None`` when either run produced no score.

        ``None`` and never ``0.0``. An institution that had no grade in one of the two runs has
        not held its score steady; there was no score to hold, and printing a zero movement would
        be the same error as printing an unmeasured rate as no drift.
        """
        if self.was_score is None or self.now_score is None:
            return None
        return self.now_score - self.was_score

    @property
    def letter_moved(self) -> bool:
        """Whether both runs produced a letter and the letters differ."""
        return (
            self.was_letter is not None
            and self.now_letter is not None
            and self.was_letter != self.now_letter
        )

    @property
    def gradeability_changed(self) -> bool:
        """Whether one run produced a grade and the other produced none.

        Distinct from :attr:`letter_moved` on purpose. Becoming ungradeable is not a drop to F,
        and the two must not be reported in the same sentence.
        """
        return (self.was_letter is None) != (self.now_letter is None)

    @property
    def moved(self) -> bool:
        """Whether anything about this institution is worth printing."""
        return bool(self.transitions) or bool(self.unreadable_fields) or self.letter_moved

    def as_dict(self) -> dict[str, Any]:
        return {
            "unit_id": self.unit_id,
            "name": self.name,
            "state": self.state,
            "transitions": [t.as_dict() for t in self.transitions],
            "was_score": self.was_score,
            "now_score": self.now_score,
            "score_change": self.score_change,
            "was_letter": self.was_letter,
            "now_letter": self.now_letter,
            "letter_moved": self.letter_moved,
            "gradeability_changed": self.gradeability_changed,
            "unreadable_fields": list(self.unreadable_fields),
        }


@dataclass(frozen=True, slots=True)
class ReportDiff:
    """Two reports, compared institution by institution and field by field."""

    earlier_rules_version: str
    later_rules_version: str
    compared: int
    """Institutions carrying an id in both reports. The denominator of every count below."""

    changed: tuple[InstitutionChange, ...]
    entered: tuple[FrameMove, ...]
    left: tuple[FrameMove, ...]
    unmatchable_earlier: int
    unmatchable_later: int
    """Grades with no ``unit_id``. Counted, never keyed on a placeholder, never silently dropped."""

    fields_only_in_earlier: tuple[str, ...] = ()
    fields_only_in_later: tuple[str, ...] = ()
    unreadable_rows_earlier: int = 0
    unreadable_rows_later: int = 0
    """Entries in ``grades`` that are not objects at all.

    Kept apart from :attr:`unmatchable_earlier`, which counts a *grade* that carries no id. A
    ``null`` or a bare string in ``grades`` is not a grade this build failed to identify; it is
    a row it could not read, and the two want different remedies -- the first is a gap in the
    grader's identity handling, the second is a damaged or hand-edited file. Both are counted,
    because the alternative is the one this module already refuses two attributes up: a
    comparison that silently drops rows reports a smaller population as a stable one.
    """

    @property
    def rules_confirmed(self) -> bool:
        """Whether both reports named the same grading rules.

        False when either report is silent. Silence is not agreement, and a transition table
        printed under an unconfirmed rules version has to say so.
        """
        return bool(self.earlier_rules_version) and (
            self.earlier_rules_version == self.later_rules_version
        )

    @property
    def matrix(self) -> tuple[tuple[str, int], ...]:
        """Every observed transition with its count, largest first then alphabetical."""
        counts: dict[str, int] = {}
        for change in self.changed:
            for transition in change.transitions:
                counts[transition.label] = counts.get(transition.label, 0) + 1
        return tuple(sorted(counts.items(), key=lambda pair: (-pair[1], pair[0])))

    @property
    def transition_count(self) -> int:
        return sum(len(change.transitions) for change in self.changed)

    def as_dict(self) -> dict[str, Any]:
        return {
            "earlier_rules_version": self.earlier_rules_version,
            "later_rules_version": self.later_rules_version,
            "rules_confirmed": self.rules_confirmed,
            "compared": self.compared,
            "transitions": self.transition_count,
            "matrix": [{"transition": label, "institutions": n} for label, n in self.matrix],
            "changed": [c.as_dict() for c in self.changed],
            "entered": [m.as_dict() for m in self.entered],
            "left": [m.as_dict() for m in self.left],
            "unmatchable_earlier": self.unmatchable_earlier,
            "unmatchable_later": self.unmatchable_later,
            "fields_only_in_earlier": list(self.fields_only_in_earlier),
            "fields_only_in_later": list(self.fields_only_in_later),
            "unreadable_rows_earlier": self.unreadable_rows_earlier,
            "unreadable_rows_later": self.unreadable_rows_later,
        }


def as_payload(diff: ReportDiff) -> dict[str, Any]:
    """The whole comparison, in the shape ``diff-report --json`` prints."""
    return diff.as_dict()


def _identity(row: Mapping[str, Any], key: str) -> str | None:
    """Read an identity value, distinguishing an absent label from one that says nothing.

    The same rule :func:`disclosed.grading._identity` applies at the other end of the pipeline,
    applied again here because a report is read by this module without going back through the
    grader: ``str(None)`` is the four-character string ``"None"``, which is a perfectly good
    institution name as far as any renderer is concerned.
    """
    value = row.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _score_of(row: Mapping[str, Any]) -> float | None:
    value = row.get("score")
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _index(report: Mapping[str, Any]) -> tuple[dict[str, dict[str, Any]], int, int]:
    """Grades keyed by unit id, how many carried no id, and how many were not objects.

    A grade with no id is excluded from the index rather than keyed on ``""``. Two of them would
    otherwise share a key, so the second would shadow the first and be reported as though the
    first institution's fields had all moved at once.

    A row that is not an object at all is excluded too, and **counted separately**. It used to be
    dropped in silence, which made a truncated or hand-edited report read as a smaller population
    that had held perfectly still -- the failure this function's neighbour already names in the
    line that prints the unmatchable count.
    """
    by_id: dict[str, dict[str, Any]] = {}
    unmatchable = 0
    unreadable = 0
    rows = report.get("grades")
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            unreadable += 1
            continue
        unit_id = _identity(row, "unit_id")
        if unit_id is None:
            unmatchable += 1
            continue
        by_id.setdefault(unit_id, row)
    return by_id, unmatchable, unreadable


def _states(row: Mapping[str, Any]) -> dict[str, str]:
    fields = row.get("fields")
    if not isinstance(fields, dict):
        return {}
    return {str(label): str(word) for label, word in fields.items()}


def _field_labels(by_id: Mapping[str, dict[str, Any]]) -> set[str]:
    labels: set[str] = set()
    for row in by_id.values():
        labels.update(_states(row))
    return labels


def _transitions(
    was: Mapping[str, str], now: Mapping[str, str]
) -> tuple[tuple[Transition, ...], tuple[str, ...]]:
    """Field moves between two grades, and the fields whose words could not be read."""
    moves: list[Transition] = []
    unreadable: list[str] = []
    for label in sorted(was):
        if label not in now:
            continue
        before, after = was[label], now[label]
        if before not in CLASSIFICATIONS or after not in CLASSIFICATIONS:
            unreadable.append(label)
            continue
        if before == after:
            continue
        moves.append(Transition(field_label=label, was=before, now=after))
    return tuple(moves), tuple(unreadable)


def _refuse_mismatched_sources(earlier: Mapping[str, Any], later: Mapping[str, Any]) -> None:
    was, now = _source_of(earlier), _source_of(later)
    if was and now and was != now:
        raise ValueError(
            f"refusing to compare a {was} run against a {now} run; "
            "these are different populations and the difference between them is not drift"
        )


def _refuse_mismatched_rules(earlier_rules: str, later_rules: str) -> None:
    if earlier_rules and later_rules and earlier_rules != later_rules:
        raise ValueError(
            f"refusing to attribute a transition between rules version {earlier_rules} and "
            f"rules version {later_rules}; a classification that moved because the grader "
            "changed is not a change in what the institution disclosed"
        )


def compare_reports(
    earlier: Mapping[str, Any],
    later: Mapping[str, Any],
    *,
    institution: str | None = None,
) -> ReportDiff:
    """Compare two grade reports, institution by institution.

    Args:
        earlier: A report payload as ``disclosed grade`` writes it.
        later: A later report over the same source and the same rules.
        institution: Restrict the comparison to one ``unit_id``. Frame moves and unmatchable
            counts are restricted with it, so a single-institution diff never reports the
            population's arrivals as that institution's.

    Raises:
        ValueError: If the two reports name different sources, in the wording
            :func:`disclosed.drift.compare` uses, or if both name a rules version and the versions
            differ. The second refusal is the point of the verb: a state that flipped because this
            project rewrote a credible range is not a state the publisher flipped, and attributing
            it to the publisher would be the most confident possible way of being wrong.
    """
    _refuse_mismatched_sources(earlier, later)
    earlier_rules, later_rules = read_rules_version(earlier), read_rules_version(later)
    _refuse_mismatched_rules(earlier_rules, later_rules)

    before, unmatchable_earlier, unreadable_earlier = _index(earlier)
    after, unmatchable_later, unreadable_later = _index(later)
    if institution is not None:
        before = {k: v for k, v in before.items() if k == institution}
        after = {k: v for k, v in after.items() if k == institution}
        unmatchable_earlier = unmatchable_later = 0
        unreadable_earlier = unreadable_later = 0

    changed: list[InstitutionChange] = []
    for unit_id in sorted(before.keys() & after.keys()):
        was_row, now_row = before[unit_id], after[unit_id]
        moves, unreadable = _transitions(_states(was_row), _states(now_row))
        change = InstitutionChange(
            unit_id=unit_id,
            name=_identity(now_row, "name"),
            state=_identity(now_row, "state"),
            transitions=moves,
            was_score=_score_of(was_row),
            now_score=_score_of(now_row),
            was_letter=_identity(was_row, "letter"),
            now_letter=_identity(now_row, "letter"),
            unreadable_fields=unreadable,
        )
        if change.moved:
            changed.append(change)

    earlier_labels, later_labels = _field_labels(before), _field_labels(after)
    return ReportDiff(
        earlier_rules_version=earlier_rules,
        later_rules_version=later_rules,
        compared=len(before.keys() & after.keys()),
        changed=tuple(changed),
        entered=_frame_moves(after, before.keys()),
        left=_frame_moves(before, after.keys()),
        unmatchable_earlier=unmatchable_earlier,
        unmatchable_later=unmatchable_later,
        fields_only_in_earlier=tuple(sorted(earlier_labels - later_labels)),
        fields_only_in_later=tuple(sorted(later_labels - earlier_labels)),
        unreadable_rows_earlier=unreadable_earlier,
        unreadable_rows_later=unreadable_later,
    )


def _frame_moves(
    rows: Mapping[str, dict[str, Any]], present_in_other: Iterable[str]
) -> tuple[FrameMove, ...]:
    other = set(present_in_other)
    return tuple(
        FrameMove(
            unit_id=unit_id,
            name=_identity(rows[unit_id], "name"),
            state=_identity(rows[unit_id], "state"),
        )
        for unit_id in sorted(set(rows) - other)
    )
