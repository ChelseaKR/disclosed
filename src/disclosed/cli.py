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

from . import (
    crosswalk,
    dataset,
    frame,
    messages,
    national,
    registry,
    registry_properties,
    site,
)
from .disclosure import CLASSIFICATIONS
from .drift import Snapshot, as_payload, compare
from .fields import FIELDS, IPEDS_FIELDS
from .grading import InstitutionGrade, grade_institution, summarize
from .peers import peer_context
from .scope import NATIONAL, SAMPLE, Scope, scope_from_payload
from .sources import college_scorecard, credential_registry, ipeds

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


def _cmd_registry_fetch(args: argparse.Namespace) -> int:
    """Walk the Credential Registry and write a capture of what a join to the federal corpora needs.

    Separate from ``fetch`` because it is a different source with a different contract: no key, no
    quota, and a set the registry's publishers edit while the walk runs. The capture is committed
    for the reason the Scorecard census capture is: rerunning it does not reproduce it, so the
    file is the only durable record of what the registry held on the day the join was measured.
    """
    try:
        cache_dir = Path(args.cache_dir) if args.cache_dir else None
        capture = credential_registry.walk(limit=args.limit, cache_dir=cache_dir)
    except credential_registry.RegistryError as exc:
        print(f"registry fetch failed, nothing written: {exc}", file=sys.stderr)
        return 1
    if not capture.organizations:
        print("no organizations returned; refusing to write an empty capture", file=sys.stderr)
        return 1
    out = Path(args.out)
    credential_registry.write_capture(capture, out)
    stated = "unstated" if capture.total_stated is None else f"{capture.total_stated:,}"
    postsecondary = sum(1 for o in capture.organizations if o.is_postsecondary)
    cached = sum(1 for page in capture.pages if page.from_cache)
    fetched = len(capture.pages) - cached
    print(f"captured {len(capture.organizations):,} organizations -> {out}")
    print(f"  exhausted        {'yes' if capture.exhausted else 'no'}; registry stated {stated}")
    print(f"  pages            {len(capture.pages)}, {cached} from cache, {fetched} fetched")
    print(f"  postsecondary    {postsecondary:,}")
    print(
        f"  duplicates       {capture.duplicates:,} repeated ctids, {capture.unreduced} unreadable"
    )
    return 0


def _cmd_registry_properties(args: argparse.Namespace) -> int:
    """Walk the registry and capture which CTDL properties each organization publishes.

    A second capture rather than two more columns in the join capture: written per organization
    the property names add about 8.5 MB to a 7.9 MB file, and aggregated to distinct property
    sets they are 245 KiB that answer every question about the population. ``docs/adr/0009``
    records why the question needed asking, which is that ``docs/adr/0007`` measured the join and
    then said in as many words that a join does not tell you what there is to grade.
    """
    try:
        cache_dir = Path(args.cache_dir) if args.cache_dir else None
        capture = credential_registry.walk(limit=args.limit, cache_dir=cache_dir)
        payload = registry_properties.census(capture)
    except (credential_registry.RegistryError, ValueError) as exc:
        print(f"registry property census failed, nothing written: {exc}", file=sys.stderr)
        return 1
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    cached = sum(1 for page in capture.pages if page.from_cache)
    print(f"property census over {payload['organizations']:,} organizations -> {out}")
    print(f"  publishing an ipedsID  {payload['publishing_an_ipeds_id']:,}")
    print(f"  distinct property sets {len(payload['signatures']):,}")
    print(f"  pages                  {len(capture.pages)}, {cached} from cache")
    return 0


def _cmd_registry_property_report(args: argparse.Namespace) -> int:
    """Reduce the property census to the figures the README and the ADR are read from.

    Offline and keyless, like ``registry-join``: the census is committed, so every rate replays
    from the repository. Nothing here grades an organization; it counts property names.
    """
    raw = json.loads(Path(args.census).read_text(encoding="utf-8"))
    try:
        payload = registry_properties.report(raw)
    except (KeyError, ValueError) as exc:
        print(f"{args.census} is not a property census this can reduce: {exc}", file=sys.stderr)
        return 1
    Path(args.out).write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    largest = payload["largest_joined_property_set"]
    print(f"property report over {payload['organizations']:,} organizations -> {args.out}")
    print(f"  joined organizations    {payload['publishing_an_ipeds_id']:,}")
    print(f"  distinct property names {payload['distinct_property_names']}")
    print(f"  universal over joined   {len(payload['universal_over_joined'])} properties")
    if largest is not None:
        print(
            f"  largest joined set      {largest['organizations']:,} organizations share the "
            f"same {len(largest['properties'])} properties"
        )
    return 0


def _cmd_registry_join(args: argparse.Namespace) -> int:
    """Measure whether Credential Registry organizations join to the two federal corpora.

    Offline and keyless: the registry capture, the IPEDS directory archive and the Scorecard
    census capture are all committed, so the measurement replays from the repository rather than
    from three live sources whose contents move. The output is a measurement and not a grade;
    ``docs/ROADMAP.md`` names it as the thing that comes before an adapter, and ``docs/adr/0007``
    records what the number licenses.
    """
    raw = json.loads(Path(args.capture).read_text(encoding="utf-8"))
    try:
        organizations, provenance = credential_registry.read_capture(raw)
    except credential_registry.RegistryError as exc:
        print(f"{args.capture} is {exc}", file=sys.stderr)
        return 1
    try:
        directory = ipeds.parse_directory(Path(args.cache).read_bytes())
    except (OSError, ipeds.IpedsError) as exc:
        print(f"IPEDS directory unreadable: {exc}", file=sys.stderr)
        return 1
    source = json.loads(Path(args.source).read_text(encoding="utf-8"))
    try:
        records, _ = college_scorecard.read_capture(source)
    except college_scorecard.ScorecardError as exc:
        print(f"{args.source} is {exc}", file=sys.stderr)
        return 1
    try:
        payload = registry.build(
            organizations, provenance, ipeds_rows=directory, scorecard_records=records
        )
    except ValueError as exc:
        print(f"{exc}", file=sys.stderr)
        return 1
    Path(args.out).write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    reg = payload["registry"]
    ident = payload["identifier_join"]
    over_all = ident["over_all_organizations"]
    over_post = ident["over_postsecondary_organizations"]
    home = payload["homepage_join"]
    print(f"registry join over {reg['organizations']:,} organizations -> {args.out}")
    print(f"  postsecondary    {reg['postsecondary']:,}")
    print(
        f"  ipedsID          {over_all['organizations_publishing_an_ipeds_id']:,} publish one "
        f"({over_post['organizations_publishing_an_ipeds_id']:,} of the postsecondary ones)"
    )
    print(
        f"  identifier join  {over_all['matched_ipeds_directory']:,} of "
        f"{over_all['ipeds_institutions']:,} IPEDS institutions reached, "
        f"{over_all['matched_scorecard_census']:,} of "
        f"{over_all['scorecard_institutions']:,} Scorecard census institutions"
    )
    print(
        f"  homepage join    {home['matched_one_institution']:,} unique, "
        f"{home['matched_more_than_one_institution']:,} ambiguous, "
        f"{home['matched_no_institution']:,} unmatched (weaker key, reported separately)"
    )
    print(
        f"  opeID            {payload['ope_id']['organizations_publishing_one']:,} publish one, "
        "joined to nothing"
    )
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
        earlier, later = load(args.earlier), load(args.later)
        drifts = compare(earlier, later)
    except ValueError as exc:
        print(f"{exc}", file=sys.stderr)
        return 1
    if args.json:
        # Sorted keys and a fixed indent, so two runs over the same pair are byte-identical and
        # a diff of the output is a diff of the finding.
        print(json.dumps(as_payload(earlier, later, drifts), indent=2, sort_keys=True))
        return 0
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

    Three committed inputs, kept apart on purpose: ``--report`` is a graded payload (from
    ``disclosed grade --source <capture> --out ...``) and carries per-field disclosure; ``--source``
    is the capture itself and carries ``school.state``/``school.ownership`` on every raw record,
    neither of which a grade keeps; ``--sample`` is the 600-institution capture the site's home
    page already describes, read here only for its own composition so the artifact can state both
    frames' skew side by side without a reader needing to load two files and compare them by hand.
    The reduction needs all three, because a reader asking "how skewed is this frame" is asking
    about the captures and a reader asking "who disclosed what" is asking about the grade, and #17
    was opened because the answer to the first question was a sentence instead of a table.
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
    sample_raw = json.loads(Path(args.sample).read_text(encoding="utf-8"))
    try:
        sample_records, _ = college_scorecard.read_capture(sample_raw)
    except college_scorecard.ScorecardError as exc:
        print(f"{args.sample} is {exc}", file=sys.stderr)
        return 1
    payload = {
        **coverage,
        "composition": frame.composition(records),
        "sample_composition": frame.composition(sample_records),
    }
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
        ask_endpoint=args.ask_endpoint,
        locale=args.locale,
    )
    print(f"built {len(pages)} pages -> {out}")
    return 0


def _cmd_corpus(args: argparse.Namespace) -> int:
    """Fetch or re-extract the federal definitions the question-answering layer may quote.

    ``--fetch`` is the only thing in this command that touches the network, and it rewrites the
    manifest's retrieval dates and hashes, so it is an explicit flag rather than the default. The
    extraction is re-run by the test suite against the committed raw bytes; this command is how
    ``corpus/passages.json`` gets regenerated when the extractor changes.
    """
    from .ask import corpus

    corpus_dir = Path(args.dir)
    if args.fetch:
        manifest = corpus.fetch(corpus_dir)
        for entry in manifest["documents"]:
            print(f"fetched {entry['id']}: {entry['bytes']} bytes, sha256 {entry['sha256'][:12]}")
    passages = corpus.extract(corpus_dir)
    corpus.write_passages(corpus_dir, passages)
    print(f"extracted {len(passages)} passages -> {corpus_dir / 'passages.json'}")
    return 0


def _cmd_ask(args: argparse.Namespace) -> int:
    """Ask one question from the command line and print the verified answer as JSON.

    Needs a configured provider (``ANTHROPIC_API_KEY``, or ``DISCLOSED_ASK_PROVIDER=bedrock``
    with AWS credentials and a region); without one it says so and exits non-zero rather than
    pretending. The question is not written anywhere by this command.
    """
    from .ask import service
    from .ask.provider import ProviderError

    root = Path(args.root)
    try:
        svc = service.Service.from_environment(data_dir=root / "data", corpus_dir=root / "corpus")
    except ProviderError as exc:
        print(f"no model is configured: {exc}", file=sys.stderr)
        return 1
    answer = svc.ask(args.question, institution_hint=args.institution, client="cli")
    print(json.dumps(answer, indent=2, ensure_ascii=False))
    return 0 if answer.get("status") == 200 else 1


def _cmd_serve(args: argparse.Namespace) -> int:
    """Run the development server. Not a deployment; see deploy/ for the prepared shape."""
    from .ask import service
    from .ask.provider import ProviderError

    root = Path(args.root)
    try:
        svc = service.Service.from_environment(data_dir=root / "data", corpus_dir=root / "corpus")
    except ProviderError as exc:
        print(f"no model is configured: {exc}", file=sys.stderr)
        return 1
    server = service.serve(svc, host=args.host, port=args.port, allowed_origin=args.origin)
    print(f"serving on http://{args.host}:{server.server_address[1]} for origin {args.origin}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:  # pragma: no cover -- interactive
        pass
    finally:
        server.server_close()
    return 0


def _cmd_evals(args: argparse.Namespace) -> int:
    """Run the evaluation suites and write provenance-stamped results.

    ``--kind oracle`` and ``--kind adversary`` need no key and run in the test suite; ``--kind
    live`` needs a configured provider and is the only run that says anything about a model. A
    live run that cannot reach a provider writes ``not_run`` results with the reason rather than
    nothing, so the absence of a number is itself on the record.
    """
    from .ask import corpus, evals, evidence
    from .ask.provider import ProviderError, from_environment

    root = Path(args.root)
    suites = evals.SUITES if args.suite == "all" else (args.suite,)
    if args.kind == "oracle":
        provider: Any = evals.OracleProvider()
    elif args.kind == "adversary":
        provider = evals.AdversaryProvider()
    else:
        try:
            provider = from_environment()
        except ProviderError as exc:
            for suite in suites:
                path = evals.write_result(
                    evals.not_run(suite, kind="live", reason=str(exc)), root / "evals" / "results"
                )
                print(f"{suite}: not run ({exc}) -> {path}")
            return 1
    store = evidence.build(root / "data")
    loaded = corpus.load(root / "corpus")
    for suite in suites:
        result = evals.run_suite(
            suite,
            kind=args.kind,
            provider=provider,
            evidence=store,
            corpus=loaded,
            cases_dir=root / "evals" / "cases",
        )
        path = evals.write_result(result, root / "evals" / "results")
        shown = {k: v for k, v in result.scores.items() if k != "per_state"}
        print(f"{suite} [{result.provenance['model']}]: {shown} -> {path}")
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

    p_reg_fetch = sub.add_parser(
        "registry-fetch",
        help="walk the Credential Registry and write a capture with provenance (no key needed)",
    )
    p_reg_fetch.add_argument("--limit", type=int, default=None, help="stop after N organizations")
    p_reg_fetch.add_argument("--out", required=True, help="capture envelope to write")
    p_reg_fetch.add_argument(
        "--cache-dir",
        default=None,
        help="directory of per-page bodies; pages found there are served without the network",
    )
    p_reg_fetch.set_defaults(func=_cmd_registry_fetch)

    p_reg_join = sub.add_parser(
        "registry-join",
        help="measure whether Credential Registry organizations join to the federal corpora",
    )
    p_reg_join.add_argument("--capture", required=True, help="Credential Registry capture")
    p_reg_join.add_argument("--cache", required=True, help="IPEDS HD archive (zip)")
    p_reg_join.add_argument("--source", required=True, help="College Scorecard census capture")
    p_reg_join.add_argument("--out", required=True, help="join measurement to write")
    p_reg_join.set_defaults(func=_cmd_registry_join)

    p_reg_props = sub.add_parser(
        "registry-properties",
        help="capture which CTDL properties Credential Registry organizations publish",
    )
    p_reg_props.add_argument("--limit", type=int, default=None, help="stop after N organizations")
    p_reg_props.add_argument("--out", required=True, help="property census to write")
    p_reg_props.add_argument(
        "--cache-dir",
        default=None,
        help="directory of per-page bodies; pages found there are served without the network",
    )
    p_reg_props.set_defaults(func=_cmd_registry_properties)

    p_reg_prop_report = sub.add_parser(
        "registry-property-report",
        help="reduce a property census to the rates the published figures are read from",
    )
    p_reg_prop_report.add_argument("--census", required=True, help="property census to reduce")
    p_reg_prop_report.add_argument("--out", required=True, help="property report to write")
    p_reg_prop_report.set_defaults(func=_cmd_registry_property_report)

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
    p_drift.add_argument(
        "--json",
        action="store_true",
        help="print the comparison as JSON, for a consumer that is not a terminal",
    )
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
    p_census.add_argument(
        "--sample",
        default="data/sample.json",
        help="the 600-institution sample, read only for its own composition to compare against",
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
    p_site.add_argument(
        "--ask-endpoint",
        default=None,
        help=(
            "URL of a running disclosed.ask service. With it, institution pages carry the opt-in "
            "question form and one inline script; without it the site has no script at all"
        ),
    )
    p_site.add_argument(
        "--locale",
        default=messages.SOURCE_LOCALE,
        choices=messages.available(),
        help=(
            "which message catalog to render the pages from. A locale is offered here only once "
            "its catalog covers every message the site renders; an incomplete one is refused "
            "rather than filled in with English, because a page that claims to be in a language "
            "it is only half in is an absence published as a fact"
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

    p_corpus = sub.add_parser(
        "corpus", help="re-extract (or, with --fetch, re-download) the federal definitions corpus"
    )
    p_corpus.add_argument("--dir", default="corpus", help="corpus directory")
    p_corpus.add_argument(
        "--fetch",
        action="store_true",
        help="download every document again and rewrite manifest.json; the only network step",
    )
    p_corpus.set_defaults(func=_cmd_corpus)

    p_ask = sub.add_parser(
        "ask", help="ask the disclosure question-answering layer one question (needs a model)"
    )
    p_ask.add_argument("question")
    p_ask.add_argument("--institution", default=None, help="IPEDS unit id the question is about")
    p_ask.add_argument("--root", default=".", help="repository root holding data/ and corpus/")
    p_ask.set_defaults(func=_cmd_ask)

    p_serve = sub.add_parser("serve", help="run the question-answering development server")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8765)
    p_serve.add_argument(
        "--origin",
        default="https://chelseakr.github.io",
        help="the one page origin allowed to call the service from a browser",
    )
    p_serve.add_argument("--root", default=".", help="repository root holding data/ and corpus/")
    p_serve.set_defaults(func=_cmd_serve)

    p_evals = sub.add_parser("evals", help="run the question-answering evaluation suites")
    p_evals.add_argument("--suite", default="all", help="a suite name, or all")
    p_evals.add_argument(
        "--kind",
        choices=("oracle", "adversary", "live"),
        default="oracle",
        help="oracle (scripted, faithful), adversary (scripted, hostile), or live (a real model)",
    )
    p_evals.add_argument("--root", default=".", help="repository root")
    p_evals.set_defaults(func=_cmd_evals)

    args = parser.parse_args(argv)
    result: int = args.func(args)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
