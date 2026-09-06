"""Open an issue when the daily snapshot finds a systemic change in federal disclosure.

The snapshot workflow has printed drift into the job summary since it was written, and
committed the snapshot beside it. Nobody is told. A job summary on a green run is the most
reassuring possible way of saying nothing at all: the run is green because the fetch worked,
and the finding it carries -- four hundred colleges no longer publishing a field -- is one
click down a page nobody opens on a day nothing appeared to happen.

A policy change in federal disclosure is the event this project exists to notice. This turns
the finding into something that arrives.

**Two kinds of issue, and the second is the point.**

A field whose reporting rate moved by at least ``SYSTEMIC_THRESHOLD`` gets an issue naming the
field, the movement, the two snapshot dates and the applicability move. Straightforward.

A field whose rate *could not be computed* in one of the two runs gets a different issue,
saying so. It is not folded in with the systemic ones, because an unknown is not a large
change; and it is not dropped, because that is the failure this whole repository is about. A
snapshot pair the tool could not measure is a gap in the record of what the public is allowed
to know, and silence about it looks exactly like a quiet day. ``drift.FieldDrift.is_systemic``
already refuses to call an unmeasured field systemic, so without this second issue the
unmeasured case would be the one thing that could never produce a signal.

**Nothing is closed automatically.** A drift that stops appearing has not been resolved; it
means the two most recent snapshots agree, which is what the *first* day of any new steady
state looks like. Closing on that would erase the finding at the moment it became permanent. A
person closes these.

**Deduplication is by field and direction**, carried in a marker line in the body rather than
inferred from the title, so re-wording a title later cannot orphan an issue and open a second
one beside it. The same drift on consecutive days updates one issue; the same field moving the
other way is a different finding and gets its own.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

LABEL = "drift"
MARKER_PREFIX = "<!-- drift-key:"
API_ROOT = "https://api.github.com"


@dataclass(frozen=True)
class IssueSpec:
    """One issue this run wants to exist, and the key that decides whether it already does."""

    key: str
    title: str
    body: str

    @property
    def marked_body(self) -> str:
        """The body with its dedupe marker, which is what actually identifies the issue."""
        return f"{MARKER_PREFIX} {self.key} -->\n\n{self.body}"


def _points(rate_change: float) -> str:
    return f"{rate_change * 100:+.2f} points"


def _applicability_sentence(field: dict[str, Any]) -> str:
    moved = int(field["applicability_moved"])
    if not moved:
        return (
            f"The field reached the same number of institutions in both runs "
            f"({field['now_applicable']}), so the movement is in who answered."
        )
    return (
        f"The field reached {moved:+} institutions between the two runs "
        f"({field['was_applicable']} -> {field['now_applicable']}). That is a change in who "
        "the field applies to rather than in who answered, and it explains most movements "
        "that look alarming in the raw counts."
    )


def systemic_spec(field: dict[str, Any], earlier: str, later: str) -> IssueSpec:
    """The issue for a field whose reporting rate moved past the threshold."""
    label = str(field["field_label"])
    direction = str(field["direction"])
    rate = _points(float(field["rate_change"]))
    return IssueSpec(
        key=f"{label}|{direction}",
        title=f"Systemic drift: {label} {direction} {rate} ({earlier} to {later})",
        body=(
            f"Between the snapshots taken **{earlier}** and **{later}**, disclosure of "
            f"**{label}** {direction} by **{rate}** of the institutions it applies to.\n\n"
            f"| | earlier ({earlier}) | later ({later}) |\n"
            f"| --- | --- | --- |\n"
            f"| reporting | {field['was_reported']} | {field['now_reported']} |\n"
            f"| applicable | {field['was_applicable']} | {field['now_applicable']} |\n\n"
            f"{_applicability_sentence(field)}\n\n"
            f"The raw count moved by {int(field['delta']):+}, which is reported here because it "
            "is what a reader wants to see and is never what the direction is read from.\n\n"
            "This issue is not closed automatically. A drift that stops appearing means the two "
            "most recent snapshots agree, which is what the first day of a new steady state "
            "looks like; closing on that would erase the finding as it became permanent."
        ),
    )


def unmeasured_spec(fields: Sequence[dict[str, Any]], earlier: str, later: str) -> IssueSpec:
    """The issue for fields whose rate could not be computed in one of the two runs.

    One issue for all of them rather than one each: they share a cause far more often than not
    -- a run that recorded no applicable counts makes every field in it unmeasurable at once --
    and a hundred identical issues is its own way of being ignored.
    """
    names = "\n".join(
        f"- **{f['field_label']}** ({f['was_reported']}/{f['was_applicable']} -> "
        f"{f['now_reported']}/{f['now_applicable']})"
        for f in fields
    )
    return IssueSpec(
        key="unmeasured",
        title=f"Drift could not be measured for {len(fields)} field(s) ({earlier} to {later})",
        body=(
            f"Comparing the snapshots taken **{earlier}** and **{later}**, these fields have no "
            "reporting rate in one or both runs, so no drift could be computed for them:\n\n"
            f"{names}\n\n"
            "A rate is unmeasurable when the number of institutions a field applied to is zero "
            "or was never recorded. This is reported rather than passed over because an unknown "
            "is not a small change -- it is the absence of a measurement, and treating it as "
            "quiet is the failure this project exists to name. It is deliberately not filed as "
            "systemic drift: nothing here demonstrates that anything moved."
        ),
    )


def specs_from(payload: dict[str, Any]) -> list[IssueSpec]:
    """Every issue this comparison calls for, in a stable order.

    Sorted by key so that two runs over the same payload ask for the same things in the same
    sequence, which is what makes the dry-run output diffable.
    """
    earlier, later = str(payload["earlier"]), str(payload["later"])
    fields = list(payload.get("fields", []))
    specs = [systemic_spec(f, earlier, later) for f in fields if f.get("is_systemic")]
    unmeasured = [f for f in fields if not f.get("measured")]
    if unmeasured:
        specs.append(unmeasured_spec(unmeasured, earlier, later))
    return sorted(specs, key=lambda s: s.key)


class Issues(Protocol):
    """The three calls this script makes, so a test can supply them without a network."""

    def open_issues(self) -> list[dict[str, Any]]: ...

    def create(self, spec: IssueSpec) -> None: ...

    def update(self, number: int, spec: IssueSpec) -> None: ...


class GitHubIssues:
    """The real client. ``urllib`` because this project ships no runtime dependencies."""

    def __init__(self, repo: str, token: str) -> None:
        self._repo = repo
        self._token = token

    def _request(self, method: str, url: str, payload: dict[str, Any] | None = None) -> Any:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(url, data=data, method=method)  # noqa: S310
        request.add_header("Authorization", f"Bearer {self._token}")
        request.add_header("Accept", "application/vnd.github+json")
        request.add_header("X-GitHub-Api-Version", "2022-11-28")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))

    def open_issues(self) -> list[dict[str, Any]]:
        url = f"{API_ROOT}/repos/{self._repo}/issues?state=open&labels={LABEL}&per_page=100"
        found = self._request("GET", url)
        return [i for i in found if "pull_request" not in i]

    def create(self, spec: IssueSpec) -> None:
        self._request(
            "POST",
            f"{API_ROOT}/repos/{self._repo}/issues",
            {"title": spec.title, "body": spec.marked_body, "labels": [LABEL]},
        )

    def update(self, number: int, spec: IssueSpec) -> None:
        self._request(
            "PATCH",
            f"{API_ROOT}/repos/{self._repo}/issues/{number}",
            {"title": spec.title, "body": spec.marked_body},
        )


def existing_by_key(issues: Sequence[dict[str, Any]]) -> dict[str, int]:
    """Map each open drift issue's marker key to its number.

    Read out of the body rather than matched on the title, so that re-wording a title later
    cannot orphan an issue and open a second one beside it.
    """
    found: dict[str, int] = {}
    for issue in issues:
        body = str(issue.get("body") or "")
        if MARKER_PREFIX not in body:
            continue
        key = body.split(MARKER_PREFIX, 1)[1].split("-->", 1)[0].strip()
        found.setdefault(key, int(issue["number"]))
    return found


def reconcile(specs: Sequence[IssueSpec], client: Issues) -> list[str]:
    """Create or update one issue per spec, and say what was done. Closes nothing."""
    existing = existing_by_key(client.open_issues())
    done: list[str] = []
    for spec in specs:
        number = existing.get(spec.key)
        if number is None:
            client.create(spec)
            done.append(f"created: {spec.title}")
        else:
            client.update(number, spec)
            done.append(f"updated #{number}: {spec.title}")
    return done


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("payload", help="output of `disclosed drift --json`, or - for stdin")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print what would be opened or updated and call nothing",
    )
    args = parser.parse_args(argv)

    raw = sys.stdin.read() if args.payload == "-" else Path(args.payload).read_text("utf-8")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"drift-issue: payload is not JSON -- {exc}", file=sys.stderr)
        return 2

    specs = specs_from(payload)
    if not specs:
        print("drift-issue: nothing systemic and nothing unmeasured; no issue opened")
        return 0

    if args.dry_run:
        for spec in specs:
            print(f"would file [{spec.key}]: {spec.title}")
        return 0

    repo, token = os.environ.get("GITHUB_REPOSITORY", ""), os.environ.get("GITHUB_TOKEN", "")
    if not repo or not token:
        # Refused rather than skipped. A missing token means the findings were never filed,
        # and exiting 0 here would make "nobody was told" indistinguishable from "there was
        # nothing to tell" -- which is the exact failure this script was written against.
        print(
            "drift-issue: GITHUB_REPOSITORY and GITHUB_TOKEN are both required to file "
            f"{len(specs)} finding(s); refusing to exit quietly with them unfiled",
            file=sys.stderr,
        )
        return 2

    try:
        for line in reconcile(specs, GitHubIssues(repo, token)):
            print(line)
    except urllib.error.HTTPError as exc:
        print(f"drift-issue: GitHub refused the request -- {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
