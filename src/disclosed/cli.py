"""Command line entry point.

``fetch`` walks the College Scorecard and writes a capture with the provenance of every page.
``grade`` runs the checks against the College Scorecard, live or from a capture, and writes a
report. ``crosscheck`` does the same against the whole IPEDS directory and reports where the two
federal sources disagree. ``snapshot`` reduces either to per-field counts, ``drift`` compares two
of those, ``national`` reduces a population-wide run to the artifact the site's national claims
rest on, ``dataset`` exports CSV, and ``site`` renders a report as static HTML.

The reductions exist because the full runs are large and regenerable while the claims made about
them are small and worth committing. A snapshot is a few hundred bytes, so the record of what
stopped being published lives in git rather than in a bucket someone has to trust; the national
artifact is 100 KB against a 2.5 MB run.

``site`` reads the reports rather than regrading, so the published pages cannot claim anything the
published data does not contain. Every run records its own coverage, and the page prints that
rather than a constant, so a sample can never be rendered through a template that says national.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, Final

from . import crosswalk, dataset, frame, national, site
from .disclosure import CLASSIFICATIONS
from .drift import Snapshot, compare
from .fields import FIELDS, IPEDS_FIELDS
from .grading import InstitutionGrade, grade_institution, summarize
from .peers import peer_context
from .scope import NATIONAL, SAMPLE, Scope, scope_from_payload
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


def _scorecard_scope(
    grades: list[InstitutionGrade], *, walked_the_api: bool, captured: str | None = None
) -> Scope:
    """Describe what a College Scorecard run covered.

    ``walked_the_api`` is passed in by the caller rather than guessed from the row count. A run is
    national because the fetch paged the API to exhaustion, not because it came back large; a
    bare replay of a capture cannot know what the capture was and is a sample until somebody
    proves otherwise. Inferring from size would eventually promote a big sample to a national
    claim. ``captured`` is the proof for a replay: the date a capture envelope records for a walk
    whose own provenance confirms exhaustion (:func:`college_scorecard.is_exhaustive`).
    """
    if walked_the_api:
        note = (
            f"Replayed from a capture that paged the API to exhaustion on {captured[:10]}, so "
            "this is every institution the College Scorecard published that day, graded from "
            "the committed file with no key."
            if captured
            else (
                "The API was paged to exhaustion, so this is every institution the College "
                "Scorecard publishes rather than a slice of it."
            )
        )
        return Scope(
            kind=NATIONAL,
            source="College Scorecard",
            institutions=len(grades),
            states=_states_in(grades),
            universe=len(grades),
            note=note,
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


def _load_records(
    args: argparse.Namespace,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Read institution records, from a captured file if given and from the API otherwise.

    Replay exists for two reasons that both matter to a project about data integrity. It makes a
    run reproducible: the same capture regrades to the same report, so a change in output is
    attributable to a change in the rules rather than to the day it was fetched. And it lets CI
    exercise the real shape of the data without spending rate limit on an API that gives DEMO_KEY
    about three pages an hour.

    Returns the records and, for a capture envelope written by ``fetch``, its provenance; a bare
    record list and a live fetch carry ``None``.
    """
    if args.source:
        raw = json.loads(Path(args.source).read_text(encoding="utf-8"))
        try:
            records, provenance = college_scorecard.read_capture(raw)
        except college_scorecard.ScorecardError as exc:
            print(f"{args.source} is {exc}", file=sys.stderr)
            return [], None
        return (records[: args.limit] if args.limit else records), provenance
    return list(college_scorecard.iter_institutions(limit=args.limit)), None


def _cmd_grade(args: argparse.Namespace) -> int:
    try:
        records, provenance = _load_records(args)
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
    # Safe to read off the flags alone: a fetch with no --source and no --limit that has reached
    # this line did not stop until iter_institutions confirmed metadata.total was met. Any earlier
    # stop it could not confirm raised ScorecardError above instead of returning, so there is no
    # longer a code path where an unexhausted walk reaches _scorecard_scope claiming otherwise.
    # A replayed capture is national on one condition only: its own provenance proves the walk
    # reached the API's stated total and the file still holds every record it says it holds.
    walked = not args.source and not args.limit
    captured: str | None = None
    if (
        provenance is not None
        and not args.limit
        and college_scorecard.is_exhaustive(provenance, len(records))
    ):
        walked = True
        captured = str(provenance.get("walked_at") or "an unrecorded day")
    scope = _scorecard_scope(grades, walked_the_api=walked, captured=captured)
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


def _cmd_fetch(args: argparse.Namespace) -> int:
    """Walk the API and write a capture envelope: records plus the provenance of every page.

    Split from ``grade`` so the expensive, keyed, network-bound step happens once and everything
    after it -- grading, reducing, rendering -- runs from the file with no key. A capture is the
    one Scorecard artifact that cannot be regenerated without a key, which is what makes it
    worth committing and worth recording page by page.
    """
    cache_dir = Path(args.cache_dir) if args.cache_dir else None
    try:
        capture = college_scorecard.walk(limit=args.limit, cache_dir=cache_dir)
    except college_scorecard.ScorecardError as exc:
        print(f"fetch failed, nothing written: {exc}", file=sys.stderr)
        return 1
    if not capture.records:
        print("no institutions returned; refusing to write an empty capture", file=sys.stderr)
        return 1
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    college_scorecard.write_capture(capture, out)
    summary = college_scorecard.summarize_capture(capture, out)
    if args.provenance_out:
        sidecar = Path(args.provenance_out)
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

    cached = sum(1 for p in capture.pages if p.from_cache)
    stated = "unstated" if capture.total_stated is None else f"{capture.total_stated:,}"
    # Unreported headers print as words. Through a format they would print as 0 remaining,
    # which reads as a budget exhausted rather than a budget not mentioned.
    remaining = summary["ratelimit_remaining_min"]
    budget = (
        "not reported by the API"
        if remaining is None
        else f"{remaining:,} of {summary['ratelimit_limit']:,} requests left at the lowest point"
    )
    print(f"captured {len(capture.records):,} institutions -> {out}")
    print(f"  exhausted        {'yes' if capture.exhausted else 'no'}; API stated {stated}")
    print(f"  pages            {len(capture.pages)}, {cached} from cache, {capture.calls} fetched")
    print(f"  rate limit       {budget}")
    print(f"  key              {'DEMO_KEY' if capture.demo_key else 'DATA_GOV_API_KEY'}")
    print(f"  sha256           {summary['capture_sha256']}")
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
            if state not in CLASSIFICATIONS:
                # A word this version does not know. Reports outlive the code that wrote them, so
                # one will arrive eventually, and the tempting reading -- "not suppressed, so it
                # counts" -- puts it in the denominator without ever putting it in the numerator.
                # Every drift rate for the field then falls, by an amount that has nothing to do
                # with what any institution published, and the drift module's whole argument is
                # about denominators that move for reasons the measurement cannot see.
                continue
            # The denominator every drift rate divides by, counted the same way the grade counts
            # it: suppressed and inapplicable institutions were never asked and leave it.
            if state not in ("suppressed", "not_applicable"):
                applicable[label] += 1
            if state == "reported":
                reported[label] += 1
            elif state == "missing":
                missing[label] += 1
    # Taken from the report's own scope rather than from a flag, so a snapshot cannot be labelled
    # with a source it did not come from. Reports written before scope existed leave it empty, and
    # an empty source is never treated as matching anything.
    scope = scope_from_payload(report)
    snap = Snapshot(
        taken=args.taken,
        institutions=len(report["grades"]),
        reported=reported,
        missing=missing,
        applicable=applicable,
        source=scope.source if scope else "",
    )
    Path(args.out).write_text(json.dumps(asdict(snap), indent=2, sort_keys=True), encoding="utf-8")
    print(f"snapshot {args.taken}: {snap.institutions} institutions -> {args.out}")
    return 0


def _cmd_drift(args: argparse.Namespace) -> int:
    def load(path: str) -> Snapshot:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return Snapshot(**raw)

    try:
        drifts = compare(load(args.earlier), load(args.later))
    except ValueError as exc:
        print(f"{exc}", file=sys.stderr)
        return 1
    if not drifts:
        print("no change in per-field disclosure between the two snapshots")
        return 0
    for d in drifts:
        flag = "SYSTEMIC" if d.is_systemic else "        "
        # An unmeasured rate prints as words. Through a percent format it would have printed as
        # "+0.00", which reads as "we checked and nothing moved" rather than "we could not check".
        rate = (
            "  rate unmeasured" if d.rate_change is None else f"{d.rate_change * 100:+7.2f} points"
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

    scorecard = _load_records(args)[0] if args.source else []
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


def _cmd_census_report(args: argparse.Namespace) -> int:
    """Reduce a full College Scorecard census to the artifact the site's census page rests on.

    Two committed inputs, kept apart on purpose: ``--report`` is a graded payload (from
    ``disclosed grade --source <capture> --out ...``) and carries per-field disclosure; ``--source``
    is the capture itself and carries ``school.state``/``school.ownership`` on every raw record,
    neither of which a grade keeps. The reduction needs both, because a reader asking "how skewed
    is this frame" is asking about the capture and a reader asking "who disclosed what" is asking
    about the grade, and #17 was opened because the answer to the first question was a sentence
    instead of a table.
    """
    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    try:
        coverage = national.build(report, fields=FIELDS)
    except ValueError as exc:
        print(f"{exc}", file=sys.stderr)
        return 1
    raw = json.loads(Path(args.source).read_text(encoding="utf-8"))
    try:
        records, _ = college_scorecard.read_capture(raw)
    except college_scorecard.ScorecardError as exc:
        print(f"{args.source} is {exc}", file=sys.stderr)
        return 1
    payload = {**coverage, "composition": frame.composition(records)}
    Path(args.out).write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    comp = payload["composition"]
    print(f"census coverage for {payload['scope']['institutions']} institutions -> {args.out}")
    print(
        f"  composition      {len(comp['states'])} states "
        f"({comp['states_unstated']} unstated), {len(comp['sectors'])} sectors "
        f"({comp['sectors_unstated']} unstated)"
    )
    for coverage_row in payload["fields"]:
        share = coverage_row["share_reported"]
        rendered = "no applicable institutions" if share is None else f"{share:.1%} reported"
        print(
            f"  {coverage_row['label']:34} {coverage_row['applicable']:>5} applicable, "
            f"{coverage_row['missing']:>4} absent, {rendered}"
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
    census_payload = (
        json.loads(Path(args.scorecard_census).read_text(encoding="utf-8"))
        if args.scorecard_census
        else None
    )
    out = Path(args.out)
    pages = site.build(
        report,
        out,
        origin=args.origin,
        generated=args.generated,
        national=national_payload,
        scorecard_census=census_payload,
    )
    print(f"built {len(pages)} pages -> {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="disclosed",
        description="Grade institutions on what they disclose, not on how they perform.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_fetch = sub.add_parser(
        "fetch", help="walk the College Scorecard and write a capture with provenance"
    )
    p_fetch.add_argument("--limit", type=int, default=None, help="stop after N institutions")
    p_fetch.add_argument("--out", required=True, help="capture envelope to write")
    p_fetch.add_argument(
        "--cache-dir",
        default=None,
        help="directory of per-page bodies; pages found there are served without the network",
    )
    p_fetch.add_argument(
        "--provenance-out",
        default=None,
        help="also write the committable provenance summary (calls, digests, rate limit) here",
    )
    p_fetch.set_defaults(func=_cmd_fetch)

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

    p_census = sub.add_parser(
        "census-report",
        help="reduce a full Scorecard census to per-field coverage plus the frame's composition",
    )
    p_census.add_argument(
        "--report", required=True, help="graded payload from `grade --source <capture>`"
    )
    p_census.add_argument(
        "--source", required=True, help="the capture itself, for school.state/school.ownership"
    )
    p_census.add_argument("--out", default="data/scorecard-census.json")
    p_census.set_defaults(func=_cmd_census_report)

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
    p_site.add_argument(
        "--scorecard-census",
        default=None,
        help=(
            "census artifact from `disclosed census-report`. Without it the site's Scorecard "
            "figures describe the committed sample only, exactly as before #17"
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
