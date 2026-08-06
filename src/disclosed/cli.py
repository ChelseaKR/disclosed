"""Command line entry point.

Four verbs: ``grade`` runs the checks and writes a report, ``snapshot`` reduces a report to
per-field counts for later comparison, ``drift`` compares two snapshots, and ``site`` renders a
report as static HTML. Splitting snapshot out from grade keeps the drift history small enough to
commit, so the record of what stopped being published lives in git rather than in a bucket someone
has to trust.

``site`` reads the report rather than regrading, so the published pages cannot claim anything the
published dataset does not contain.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, Final

from . import crosswalk, dataset, national, site
from .drift import Snapshot, compare
from .fields import IPEDS_FIELDS
from .grading import InstitutionGrade, grade_institution, summarize
from .peers import peer_context
from .scope import NATIONAL, SAMPLE, Scope
from .sources import college_scorecard, ipeds

__all__ = ["main"]

# The College Scorecard's full universe, stated as an approximation because the count moves as
# institutions open and close and quoting it to the digit would imply a precision nobody has.
SCORECARD_UNIVERSE: Final[int] = 6_300


def _states_in(grades: list[InstitutionGrade]) -> int:
    """How many distinct states a run touched, not counting the institutions it could not place.

    A record with no state is not a fourteenth state. Counting it as one would inflate the
    coverage claim using an absence, which is the error this project is about.
    """
    return len({g.state for g in grades if g.state})


def _implausible_findings(
    grades: list[InstitutionGrade], corpus: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Every implausible value, with the peer evidence that supports or undermines it.

    The peer group is attached to the finding rather than computed by the reader, so an institution
    that wants to contest a grade is arguing with a stated comparison instead of guessing at one.
    Where the peers turn out to publish the same value, the verdict says so, and the finding
    effectively argues against itself. That is intended.

    Records without an id are excluded from the lookup instead of being keyed on ``""``. They used
    to be keyed on the string ``"None"``, so any two unidentified institutions collided and the
    second one's finding was published carrying the first one's peer group. Peer evidence attached
    to the wrong school is worse than no peer evidence, because it is citable.
    """
    by_id: dict[str, dict[str, Any]] = {}
    for row in corpus:
        ident = row.get("id")
        if ident is None:
            continue
        by_id.setdefault(str(ident), row)
    findings: list[dict[str, Any]] = []
    for grade in grades:
        record = by_id.get(grade.unit_id) if grade.unit_id is not None else None
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


def _scorecard_scope(grades: list[InstitutionGrade], *, walked_the_api: bool) -> Scope:
    """Describe what a College Scorecard run covered.

    ``walked_the_api`` is passed in by the caller rather than guessed from the row count. A run is
    national because the fetch paged the API to exhaustion, not because it came back large; a
    replay of a capture cannot know what the capture was and is a sample until somebody proves
    otherwise. Inferring from size would eventually promote a big sample to a national claim.
    """
    if walked_the_api:
        return Scope(
            kind=NATIONAL,
            source="College Scorecard",
            institutions=len(grades),
            states=_states_in(grades),
            universe=len(grades),
            note=(
                "The API was paged to exhaustion, so this is every institution the College "
                "Scorecard publishes rather than a slice of it."
            ),
        )
    return Scope(
        kind=SAMPLE,
        source="College Scorecard",
        institutions=len(grades),
        states=_states_in(grades),
        universe=SCORECARD_UNIVERSE,
        note=(
            "The first records the API returned, which arrive grouped by state, so some states "
            "are represented heavily and most not at all. This is a slice and not a random one, "
            "and percentages computed from it describe these institutions rather than the "
            "country."
        ),
    )


def _grade_payload(
    grades: list[InstitutionGrade], corpus: list[dict[str, Any]], *, scope: Scope
) -> dict[str, Any]:
    by_state: dict[str, list[InstitutionGrade]] = {}
    for grade in grades:
        by_state.setdefault(grade.state or "unknown", []).append(grade)

    return {
        "scope": scope.as_dict(),
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
    try:
        records = _load_records(args)
    except college_scorecard.ScorecardError as exc:
        # A failed fetch exits non-zero with the reason on stderr rather than unwinding a
        # traceback. The distinction matters in CI: a scheduled run that cannot reach the API must
        # be visibly broken, not quietly commit a snapshot built from however many pages arrived
        # before the failure. That snapshot would read as a nationwide collapse in reporting.
        print(f"fetch failed, no report written: {exc}", file=sys.stderr)
        return 1
    if not records:
        print("no institutions returned; refusing to write an empty report", file=sys.stderr)
        return 1
    grades = [grade_institution(r) for r in records]
    scope = _scorecard_scope(grades, walked_the_api=not args.source and not args.limit)
    payload = _grade_payload(grades, records, scope=scope)
    Path(args.out).write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    overall = payload["overall"]
    mean = overall["mean_score"]
    print(f"graded {payload['institutions']} institutions -> {args.out}")
    print(f"  coverage         {scope.kind}: {scope.sentence}")
    print(f"  mean disclosure  {mean:.1%}" if mean is not None else "  mean disclosure  n/a")
    print(f"  ungradeable      {payload['ungradeable']}")
    print(f"  implausible      {len(payload['implausible'])}")
    for label, count in overall["worst_fields"]:
        print(f"    {count:>5} institutions fail: {label}")
    return 0


def _cmd_snapshot(args: argparse.Namespace) -> int:
    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    # Counts are read straight out of the report rather than rebuilt into InstitutionGrade objects.
    # The rebuild was lossy in both directions: it dropped every FieldResult, and it ran the
    # identity fields back through ``str()``, which turned a JSON null id into the string "None"
    # and undid the fix one module over.
    reported: dict[str, int] = {}
    missing: dict[str, int] = {}
    applicable: dict[str, int] = {}
    for row in report["grades"]:
        for label, state in row["fields"].items():
            reported.setdefault(label, 0)
            missing.setdefault(label, 0)
            applicable.setdefault(label, 0)
            # The denominator every drift rate divides by, counted the same way the grade counts
            # it: suppressed and inapplicable institutions were never asked and leave it.
            if state not in ("suppressed", "not_applicable"):
                applicable[label] += 1
            if state == "reported":
                reported[label] += 1
            elif state == "missing":
                missing[label] += 1
    snap = Snapshot(
        taken=args.taken,
        institutions=len(report["grades"]),
        reported=reported,
        missing=missing,
        applicable=applicable,
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
        # An unmeasured rate prints as words. Through a percent format it would have printed as
        # "+0.00", which reads as "we checked and nothing moved" rather than "we could not check".
        rate = (
            "  rate unmeasured"
            if d.rate_change is None
            else f"{d.rate_change * 100:+7.2f} points"
        )
        print(
            f"  {flag} {d.field_label:34} {d.direction:>6} {rate}   "
            f"{d.was_reported}/{d.was_applicable} -> {d.now_reported}/{d.now_applicable}"
        )
        if d.applicability_moved:
            # Printed whenever it moves, because it is the explanation for every count that looks
            # alarming and is not. Three findings were published as systemic collapses before this
            # line existed, and all three were institutions closing.
            print(
                f"           {'':34} the field reached {d.applicability_moved:+} institutions, a "
                f"change in who it applies to rather than in who answered"
            )
    return 0


def _cmd_dataset(args: argparse.Namespace) -> int:
    """Write the CSV export and the Table Schema that describes it, from one report."""
    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    if not report.get("grades"):
        print(f"{args.report} contains no graded institutions; refusing to export", file=sys.stderr)
        return 1
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(dataset.to_csv(report), encoding="utf-8", newline="")
    schema_path = out.with_suffix(".schema.json")
    schema_path.write_text(dataset.to_schema_json(path=out.name), encoding="utf-8")
    print(f"exported {len(report['grades'])} rows -> {out}")
    print(f"                          schema -> {schema_path}")
    return 0


def _cmd_crosscheck(args: argparse.Namespace) -> int:
    """Grade IPEDS's own disclosures and report where it disagrees with the Scorecard."""
    try:
        directory = ipeds.load_institutions(
            year=args.year,
            cache=Path(args.cache) if args.cache else None,
            characteristics_cache=Path(args.characteristics) if args.characteristics else None,
        )
    except ipeds.IpedsError as exc:
        print(f"IPEDS unreadable, nothing written: {exc}", file=sys.stderr)
        return 1

    scorecard = _load_records(args) if args.source else []
    grades = [grade_institution(r, fields=IPEDS_FIELDS) for r in directory]
    overall = summarize(grades, label="all institutions")
    found = crosswalk.contradictions(scorecard, directory) if scorecard else []

    # The directory is a file, not a page of a file. Grading it grades every institution IPEDS
    # publishes, which is what makes national claims possible for the fields it carries, and it is
    # the one place in this project where a percentage describes the country.
    scope = Scope(
        kind=NATIONAL,
        source="IPEDS directory",
        institutions=len(grades),
        states=_states_in(grades),
        universe=len(grades),
        note=(
            f"The complete IPEDS institutional directory for {args.year}, joined to the "
            "institutional characteristics file for the same collection year. Both are downloaded "
            "whole rather than paged, so this is the population and not a sample of it."
        ),
    )
    payload = {
        "scope": scope.as_dict(),
        "institutions": len(grades),
        "ungradeable": sum(1 for g in grades if g.score is None),
        "overall": asdict(overall),
        "contradictions": [asdict(c) for c in found],
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
    Path(args.out).write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    print(f"graded {len(grades)} IPEDS records -> {args.out}")
    mean = overall.mean_score
    print(f"  mean disclosure  {mean:.1%}" if mean is not None else "  mean disclosure  n/a")
    print(f"  ungradeable      {payload['ungradeable']}")
    for label, count in overall.worst_fields:
        print(f"    {count:>5} institutions do not publish: {label}")
    if not scorecard:
        print("  (pass --source to cross-check against a College Scorecard capture)")
        return 0
    print(f"  cross-source disagreements  {len(found)}")
    for item in found:
        print(f"    {item.unit_id} {item.name or 'unnamed'}: {item.field_label}")
        print(f"      Scorecard says {item.scorecard_value}, IPEDS says {item.ipeds_value}")
    return 0


def _cmd_national(args: argparse.Namespace) -> int:
    """Reduce a national run to the committable artifact the site's national claims rest on.

    Split out from ``crosscheck`` for the same reason ``snapshot`` is split out from ``grade``:
    the full payload is megabytes and regenerable from two public archives, while the claims made
    about it are a few tens of kilobytes and belong in git where anyone can diff them.
    """
    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    try:
        payload = national.build(report)
    except ValueError as exc:
        print(f"{exc}", file=sys.stderr)
        return 1
    Path(args.out).write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(f"national coverage for {payload['scope']['institutions']} institutions -> {args.out}")
    for coverage in payload["fields"]:
        share = coverage["share_reported"]
        # An unmeasured share prints as words. Formatting None through a percent format is how it
        # would have become "0%", and a field nobody had to answer is not a field everybody failed.
        rendered = "no applicable institutions" if share is None else f"{share:.1%} reported"
        print(
            f"  {coverage['label']:34} {coverage['applicable']:>5} applicable, "
            f"{coverage['missing']:>4} absent, {rendered}"
        )
    return 0


def _cmd_site(args: argparse.Namespace) -> int:
    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    if not report.get("grades"):
        # Same rule as ``grade``: a site with no institutions in it is not a finding about higher
        # education, it is a broken build, and publishing it would look like one.
        print(f"{args.report} contains no graded institutions; refusing to build", file=sys.stderr)
        return 1
    national_payload = (
        json.loads(Path(args.national).read_text(encoding="utf-8")) if args.national else None
    )
    out = Path(args.out)
    pages = site.build(
        report,
        out,
        origin=args.origin,
        generated=args.generated,
        national=national_payload,
    )
    print(f"built {len(pages)} pages -> {out}")
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

    p_data = sub.add_parser("dataset", help="export a report as CSV plus a Table Schema")
    p_data.add_argument("--report", default="data/report.json")
    p_data.add_argument("--out", default="data/dataset.csv")
    p_data.set_defaults(func=_cmd_dataset)

    p_cross = sub.add_parser(
        "crosscheck",
        help="grade IPEDS disclosures and report where it disagrees with the Scorecard",
    )
    p_cross.add_argument("--year", type=int, default=ipeds.DEFAULT_YEAR)
    p_cross.add_argument(
        "--cache", default=None, help="path to hold the downloaded IPEDS directory archive"
    )
    p_cross.add_argument(
        "--characteristics",
        default=None,
        help="path to hold the downloaded IPEDS institutional characteristics archive",
    )
    p_cross.add_argument(
        "--source",
        default=None,
        help="College Scorecard capture to cross-check against; omit to grade IPEDS alone",
    )
    p_cross.add_argument("--limit", type=int, default=None)
    p_cross.add_argument("--out", default="data/crosscheck.json")
    p_cross.set_defaults(func=_cmd_crosscheck)

    p_nat = sub.add_parser(
        "national", help="reduce a national run to per-field counts and named findings"
    )
    p_nat.add_argument("--report", default="data/crosscheck.json")
    p_nat.add_argument("--out", default="data/national.json")
    p_nat.set_defaults(func=_cmd_national)

    p_site = sub.add_parser("site", help="render a report as static HTML")
    p_site.add_argument("--report", default="data/report.json")
    p_site.add_argument(
        "--national",
        default=None,
        help=(
            "national artifact from `disclosed national`. Without it the site publishes only the "
            "sample-scoped pages, and makes no national claim at all"
        ),
    )
    p_site.add_argument("--out", default="site")
    p_site.add_argument("--origin", default=site.DEFAULT_ORIGIN)
    p_site.add_argument(
        "--generated",
        default="an unrecorded run",
        help=(
            "run identifier shown in the footer, typically a date. Taken from the caller rather "
            "than the clock so that rebuilding the same report is byte-identical"
        ),
    )
    p_site.set_defaults(func=_cmd_site)

    args = parser.parse_args(argv)
    result: int = args.func(args)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
