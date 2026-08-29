#!/usr/bin/env python3
"""Fail when the site GitHub Pages serves is not the site this checkout builds.

publish-site renders the whole scorecard from the committed data on every push
and uploads it. The render is pure apart from the date it stamps, so the same
commit gives byte-identical pages. What has never been checked is whether the
pages a reader receives are that render. A pages run that failed, never fired,
or published an older commit would leave every gate green while the published
grade for a named institution differed from the one this repository computes,
and nothing here could tell.

That is worth a daily check rather than a note. These pages grade real colleges
by name, and a stale one misinforms somebody choosing where to enrol.

This is the check for the deployment. It reads the deploy's own generated-on
date off the live front page, rebuilds every page from the checkout stamped with
that date, and fails naming every byte-level difference.

    python3 tools/verify_live_site.py

The date is the single thing a rebuild cannot reproduce, because the deploy
stamps the day it ran. Reading it from the live page rather than guessing it
keeps every other byte under exact comparison, and the date itself is bounded
rather than trusted: it must parse, it must not be in the future, and it must
not predate the commit this check ran against, which is the case where the
deployment is older than the code.

Vacuity is the failure mode a check like this is most exposed to, so four
things are refused outright instead of being reported as a pass:

  * a rebuild that produces fewer files than the floor, because a sentinel that
    compares nothing and prints OK is worse than no sentinel at all;
  * any fetch that does not return HTTP 200, an unreachable host included;
  * an origin that answers a guaranteed-missing path with anything but 404,
    which is how a catch-all would make every matching comparison meaningless;
  * a live page with no generated-on line to read a date from, since rebuilding
    against a date nobody published would be comparing this checkout to itself.

Exit codes: 0 the live surface is the published surface, 1 it is not, 4 the
check could not run.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import http.client
import re
import secrets
import ssl
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[1]

LIVE_URL = "https://chelseakr.github.io/disclosed/"

# The one thing about the published site that a rebuild cannot reproduce: the
# deploy stamps the date it ran. Everything else is a pure function of the
# checkout, so the date is read off the live page, bounded, and then fed back
# into the rebuild, which leaves every other byte under exact comparison.
GENERATED_PATTERN = re.compile(rb"Generated ([0-9]{4}-[0-9]{2}-[0-9]{2}) from public federal data")

# The floor under the comparison set. A sentinel that finds nothing to compare
# and prints OK is worse than no sentinel, so a set smaller than this is a
# failure and not a pass.
MINIMUM_FILES = 500

MAXIMUM_FILE_BYTES = 16 * 1024 * 1024
EXIT_DIFFERS = 1
EXIT_CANNOT_RUN = 4


class LiveSiteError(RuntimeError):
    """The live surface could not be verified against this checkout."""


@dataclass(frozen=True)
class Response:
    status: int
    body: bytes


class Origin:
    """Bounded HTTPS reads from one fixed public origin. Redirects are not followed."""

    def __init__(self, url: str, *, timeout_seconds: float) -> None:
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.hostname or parts.query or parts.fragment:
            raise LiveSiteError(f"live URL {url!r} is not a canonical HTTPS origin")
        if not 1.0 <= timeout_seconds <= 60.0:
            raise LiveSiteError("timeout must be between 1 and 60 seconds")
        self.host = parts.hostname
        self.base = parts.path.rstrip("/")
        self.url = url
        self._timeout = timeout_seconds

    def target(self, relative: str, nonce: str) -> str:
        if relative.startswith("/") or "?" in relative or "#" in relative:
            raise LiveSiteError(f"relative path {relative!r} is not canonical")
        return f"{self.base}/{relative}?live-integrity={nonce}"

    def get(
        self,
        relative: str,
        *,
        nonce: str,
        maximum_bytes: int = MAXIMUM_FILE_BYTES,
    ) -> Response:
        target = self.target(relative, nonce)
        # The audit rule below is about HTTPSConnection used without certificate
        # verification: Python before 3.4.3 did not verify by default. This call
        # passes ssl.create_default_context(), which verifies both the chain and
        # the hostname, and is the condition the rule exists to require.
        # nosemgrep: httpsconnection-detected
        connection = http.client.HTTPSConnection(
            self.host, timeout=self._timeout, context=ssl.create_default_context()
        )
        try:
            connection.request(
                "GET",
                target,
                headers={
                    "Accept-Encoding": "identity",
                    "Cache-Control": "no-cache, no-store, max-age=0",
                    "Pragma": "no-cache",
                    "User-Agent": "disclosed-live-integrity/1",
                },
            )
            response = connection.getresponse()
            encoding = response.getheader("Content-Encoding")
            if encoding not in {None, "identity"}:
                raise LiveSiteError(f"{target} came back {encoding}-encoded, not identity")
            body = response.read(maximum_bytes + 1)
            if len(body) > maximum_bytes:
                raise LiveSiteError(f"{target} exceeds the {maximum_bytes} byte read limit")
            return Response(status=response.status, body=body)
        except (OSError, http.client.HTTPException) as exc:
            raise LiveSiteError(f"GET https://{self.host}{target} failed: {exc}") from exc
        finally:
            connection.close()


def short(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()[:16]


def head_commit_date() -> dt.date:
    """The UTC date of the commit under test, which no deploy of it can precede."""
    # A fixed argument vector, no shell, resolved from PATH like every other git
    # call this repository makes.
    result = subprocess.run(
        ["git", "log", "-1", "--format=%cI"],  # noqa: S607
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise LiveSiteError(f"git could not date HEAD: {result.stderr.strip()}")
    stamped = result.stdout.strip()
    try:
        return dt.datetime.fromisoformat(stamped).astimezone(dt.UTC).date()
    except ValueError as exc:
        raise LiveSiteError(f"git dated HEAD as {stamped!r}") from exc


def live_generated_date(origin: Origin, nonce: str) -> tuple[str, bytes]:
    """Read the deploy's own date off the live page, and refuse an implausible one."""
    response = origin.get("index.html", nonce=nonce)
    if response.status != 200:
        raise LiveSiteError(f"the live index returned HTTP {response.status}")
    found = GENERATED_PATTERN.search(response.body)
    if found is None:
        raise LiveSiteError(
            "the live page does not carry the generated-on line this check reads its "
            "date from, so there is no date to rebuild against. Either the deployment "
            "is not this site, or the line was renamed and this pattern needs updating."
        )
    stamped = found.group(1).decode("ascii")
    try:
        generated = dt.date.fromisoformat(stamped)
    except ValueError as exc:
        raise LiveSiteError(f"the live page is stamped {stamped!r}, not a date") from exc
    today = dt.datetime.now(dt.UTC).date()
    if generated > today:
        raise LiveSiteError(f"the live page is stamped {stamped}, which is in the future")
    committed = head_commit_date()
    if generated < committed:
        raise LiveSiteError(
            f"the live page was generated on {stamped}, before the commit it should "
            f"have been built from was made ({committed.isoformat()}). The deployment "
            f"is older than the commit this check ran against."
        )
    return stamped, response.body


def build_expected(generated: str) -> dict[str, bytes]:
    """Render the whole site from this checkout, stamped with the live date."""
    with tempfile.TemporaryDirectory(prefix="live-integrity-") as directory:
        out = Path(directory) / "site"
        command = [
            sys.executable,
            "-m",
            "disclosed.cli",
            "site",
            "--report",
            "data/report.json",
            "--national",
            "data/national.json",
            "--scorecard-census",
            "data/scorecard-census.json",
            "--out",
            str(out),
            "--origin",
            LIVE_URL.rstrip("/"),
            "--generated",
            generated,
        ]
        # `command` is built here from constants and the date read off the live
        # page, which is already bounded to an ISO date. No shell, no user input.
        result = subprocess.run(  # noqa: S603
            command,
            cwd=REPO,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise LiveSiteError(
                f"`{' '.join(command)}` failed, so there is nothing to compare the live "
                f"surface with:\n{result.stdout}{result.stderr}"
            )
        inventory: dict[str, bytes] = {}
        for path in sorted(out.rglob("*")):
            if not path.is_file():
                continue
            payload = path.read_bytes()
            if not payload:
                raise LiveSiteError(f"the rebuild wrote {path.name} empty")
            inventory[path.relative_to(out).as_posix()] = payload
    return inventory


def prove_the_origin_discriminates(origin: Origin, nonce: str) -> None:
    """A host that answers everything with 200 makes every comparison vacuous."""
    missing = f".live-integrity-guaranteed-missing-{nonce}"
    response = origin.get(missing, nonce=nonce, maximum_bytes=1024 * 1024)
    if response.status != 404:
        raise LiveSiteError(
            f"the origin answered a guaranteed-missing path with HTTP {response.status} "
            f"instead of 404, so a matching fetch would prove nothing: /{missing}"
        )


def compare(
    origin: Origin,
    inventory: dict[str, bytes],
    nonce: str,
    live_index: bytes,
) -> list[str]:
    differences: list[str] = []
    for relative, expected in sorted(inventory.items()):
        if relative == "index.html":
            # Already fetched, to read the deploy date the rebuild was stamped with.
            live = live_index
        else:
            response = origin.get(relative, nonce=nonce)
            if response.status != 200:
                differences.append(
                    f"{relative}: the live origin returned HTTP {response.status}; "
                    f"this checkout publishes {len(expected)} bytes"
                )
                continue
            live = response.body
        if live != expected:
            differences.append(
                f"{relative}: live sha256 {short(live)} ({len(live)} bytes) is not "
                f"the rebuilt {short(expected)} ({len(expected)} bytes)"
            )
    root = origin.get("", nonce=nonce)
    index = inventory.get("index.html")
    if index is None:
        differences.append("the rebuild produced no index.html")
    elif root.status != 200:
        differences.append(f"/: the live origin returned HTTP {root.status}")
    elif root.body != index:
        differences.append(
            f"/: live sha256 {short(root.body)} is not the rebuilt index.html {short(index)}"
        )
    return differences


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=LIVE_URL, help=f"live site root (default {LIVE_URL})")
    parser.add_argument("--timeout-seconds", type=float, default=20.0)
    parser.add_argument(
        "--minimum",
        type=int,
        default=MINIMUM_FILES,
        help="refuse to pass on fewer rebuilt files than this",
    )
    args = parser.parse_args(argv)

    try:
        origin = Origin(args.url, timeout_seconds=args.timeout_seconds)
        nonce = secrets.token_hex(16)
        prove_the_origin_discriminates(origin, nonce)
        generated, live_index = live_generated_date(origin, nonce)
        inventory = build_expected(generated)
        if len(inventory) < args.minimum:
            raise LiveSiteError(
                f"the rebuild produced {len(inventory)} file(s), below the floor of "
                f"{args.minimum}. A check that compares nothing must fail, not pass."
            )
        differences = compare(origin, inventory, nonce, live_index)
    except LiveSiteError as exc:
        print(f"live integrity check could not run: {exc}", file=sys.stderr)
        return EXIT_CANNOT_RUN

    if differences:
        print(
            f"The live surface at {origin.url} is not what this checkout builds "
            f"(rebuilt with the live page's own generated date, {generated}).",
            file=sys.stderr,
        )
        for difference in differences:
            print(f"  {difference}", file=sys.stderr)
        print(
            "\nRe-run publish-site, or find out why the deployment is behind the default branch.",
            file=sys.stderr,
        )
        return EXIT_DIFFERS

    total = sum(len(payload) for payload in inventory.values())
    print(
        f"{origin.url} serves exactly what this checkout builds: {len(inventory)} "
        f"file(s), {total} bytes, generated {generated}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
