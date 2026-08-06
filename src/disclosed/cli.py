"""Command line entry point.

Three verbs: ``grade`` runs the checks and writes a report, ``snapshot`` reduces a report to
per-field counts for later comparison, and ``drift`` compares two snapshots. Splitting snapshot out
from grade keeps the drift history small enough to commit, so the record of what stopped being
published lives in git rather than in a bucket someone has to trust.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .drift import Snapshot, compare
from .grading import InstitutionGrade, grade_institution, summarize
from .peers import peer_context
from .sources import college_scorecard

__all__ = ["main"]


def _implausible_findings(
    grades: list[InstitutionGrade], corpus: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Every implausible value, with the peer evidence that supports or undermines it.

    The peer group is attached to the finding rather than computed by the reader, so an institution
    that wants to contest a grade is arguing with a stated comparison instead of guessing at one.
    Where the peers turn out to publish the same value, the verdict says so, and the finding
    effectively argues against itself. That is intended.
    """
    by_id = {str(r.get("id", "")): r for r in corpus}
    findings: list[dict[str, Any]] = []
    for grade in grades:
        record = by_id.get(grade.unit_id)
        for result in grade.implausible:
            finding: dict[str, Any] = {
                "unit_id": grade.unit_id,
                "name": grade.name,
                "state": grade.state,
                "field": result.field.label,
                "value": result.raw,
                "rationale": result.field.rationale,
            }
            if record is not None:
                group = peer_context(record, result.field.key, corpus)
                finding["peers"] = {
                    "group": group.description,
                    "size": group.size,
                    "reporting": group.reporting,
                    "publishing_same_value": group.matching_value,
                    "median": group.median,
                    "verdict": group.verdict,
                }
            findings.append(finding)
    return findings


def _grade_payload(
    grades: list[InstitutionGrade], corpus: list[dict[str, Any]]
) -> dict[str, Any]:
    by_state: dict[str, list[InstitutionGrade]] = {}
    for grade in grades:
        by_state.setdefault(grade.state or "unknown", []).append(grade)

    return {
        "institutions": len(grades),
        "ungradeable": sum(1 for g in grades if g.score is None),
        "overall": asdict(summarize(grades, label="all institutions")),
        "by_state": [
            asdict(summarize(rows, label=state)) for state, rows in sorted(by_state.items())
        ],
        "implausible": _implausible_findings(grades, corpus),
        "grades": [
            {
                "unit_id": g.unit_id,
                "name": g.name,
                "state": g.state,
                "score": g.score,
                "letter": g.letter,
                "fields": {r.field.label: r.disclosure.value for r in g.results},
            }
            for g in grades
        ],
    }


def _load_records(args: argparse.Namespace) -> list[dict[str, Any]]:
    """Read institution records, from a captured file if given and from the API otherwise.

    Replay exists for two reasons that both matter to a project about data integrity. It makes a
    run reproducible: the same capture regrades to the same report, so a change in output is
    attributable to a change in the rules rather than to the day it was fetched. And it lets CI
    exercise the real shape of the data without spending rate limit on an API that gives DEMO_KEY
    about three pages an hour.
    """
    if args.source:
        raw = json.loads(Path(args.source).read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            print(f"{args.source} is not a JSON array of records", file=sys.stderr)
            return []
        records = [r for r in raw if isinstance(r, dict)]
        return records[: args.limit] if args.limit else records
    return list(college_scorecard.iter_institutions(limit=args.limit))


def _cmd_grade(args: argparse.Namespace) -> int:
    records = _load_records(args)
    if not records:
        print("no institutions returned; refusing to write an empty report", file=sys.stderr)
        return 1
    grades = [grade_institution(r) for r in records]
    payload = _grade_payload(grades, records)
    Path(args.out).write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    overall = payload["overall"]
    mean = overall["mean_score"]
    print(f"graded {payload['institutions']} institutions -> {args.out}")
    print(f"  mean disclosure  {mean:.1%}" if mean is not None else "  mean disclosure  n/a")
    print(f"  ungradeable      {payload['ungradeable']}")
    print(f"  implausible      {len(payload['implausible'])}")
    for label, count in overall["worst_fields"]:
        print(f"    {count:>5} institutions fail: {label}")
    return 0


def _cmd_snapshot(args: argparse.Namespace) -> int:
    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    grades = [
        InstitutionGrade(
            unit_id=str(row["unit_id"]),
            name=str(row["name"]),
            state=str(row["state"]),
            results=(),
            score=row["score"],
            letter=row["letter"],
        )
        for row in report["grades"]
    ]
    # Field-level detail lives in the report, not in the reconstructed grades, so counts are read
    # straight from it rather than recomputed from a lossy round trip.
    reported: dict[str, int] = {}
    missing: dict[str, int] = {}
    for row in report["grades"]:
        for label, state in row["fields"].items():
            reported.setdefault(label, 0)
            missing.setdefault(label, 0)
            if state == "reported":
                reported[label] += 1
            elif state == "missing":
                missing[label] += 1
    snap = Snapshot(
        taken=args.taken, institutions=len(grades), reported=reported, missing=missing
    )
    Path(args.out).write_text(json.dumps(asdict(snap), indent=2, sort_keys=True), encoding="utf-8")
    print(f"snapshot {args.taken}: {snap.institutions} institutions -> {args.out}")
    return 0


def _cmd_drift(args: argparse.Namespace) -> int:
    def load(path: str) -> Snapshot:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return Snapshot(**raw)

    drifts = compare(load(args.earlier), load(args.later))
    if not drifts:
        print("no change in per-field disclosure between the two snapshots")
        return 0
    for d in drifts:
        flag = "SYSTEMIC" if d.is_systemic else "        "
        print(
            f"  {flag} {d.field_label:42} {d.direction} {abs(d.delta):>5} "
            f"({d.share_of_institutions:+.1%})  {d.was_reported} -> {d.now_reported}"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="disclosed",
        description="Grade institutions on what they disclose, not on how they perform.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_grade = sub.add_parser("grade", help="fetch and grade institutions")
    p_grade.add_argument("--limit", type=int, default=None, help="stop after N institutions")
    p_grade.add_argument(
        "--source",
        default=None,
        help="replay from a captured JSON array of records instead of calling the API",
    )
    p_grade.add_argument("--out", default="data/report.json")
    p_grade.set_defaults(func=_cmd_grade)

    p_snap = sub.add_parser("snapshot", help="reduce a report to per-field counts")
    p_snap.add_argument("--report", default="data/report.json")
    p_snap.add_argument("--taken", required=True, help="run identifier, typically a date")
    p_snap.add_argument("--out", required=True)
    p_snap.set_defaults(func=_cmd_snapshot)

    p_drift = sub.add_parser("drift", help="compare two snapshots")
    p_drift.add_argument("earlier")
    p_drift.add_argument("later")
    p_drift.set_defaults(func=_cmd_drift)

    args = parser.parse_args(argv)
    result: int = args.func(args)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
