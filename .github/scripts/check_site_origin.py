"""Fail the build when the rendered site's URLs disagree with where it is being deployed.

Issue #2 was that 616 canonical links and every sitemap entry named an origin that served a
404. The origin was correct in shape and simply hardcoded, so nothing in the build could
notice it had stopped being true. This check closes that: it is given the deploy target the
Pages API actually reported, and it refuses the build unless every URL the site emits agrees
with it.

Three separate promises are checked, because they can break independently:

1. Every page's ``<link rel="canonical">`` is the deploy target plus that page's own path. A
   page that self-canonicalises somewhere else tells crawlers to index the other place.
2. ``sitemap.xml`` lists exactly the pages that were built, no more and no fewer. A sitemap is
   a promise that the URLs in it exist; an entry with no file behind it is a 404 with an
   invitation attached, and a built page missing from the sitemap goes unlisted.
3. ``robots.txt`` advertises the sitemap under the same origin.

Usage: check_site_origin.py <site-dir> <base-url>
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

CANONICAL = re.compile(r'<link rel="canonical" href="([^"]*)"')
LOC = re.compile(r"<loc>([^<]*)</loc>")
MAX_REPORTED = 20


def _url_for(site_dir: Path, page: Path, base: str) -> str:
    """The URL a built ``index.html`` is served at, matching how the renderer forms it."""
    rel = page.parent.relative_to(site_dir).as_posix()
    return f"{base}/" if rel == "." else f"{base}/{rel}/"


def _check_canonicals(site_dir: Path, pages: list[Path], base: str) -> tuple[set[str], list[str]]:
    """Each page must self-canonicalise to the deploy target. Returns the URLs built."""
    expected: set[str] = set()
    problems: list[str] = []
    for page in pages:
        want = _url_for(site_dir, page, base)
        expected.add(want)
        found = CANONICAL.search(page.read_text(encoding="utf-8"))
        if found is None:
            problems.append(f"{page}: no canonical link")
        elif found.group(1) != want:
            problems.append(f"{page}: canonical is {found.group(1)!r}, deploy target says {want!r}")
    return expected, problems


def _check_sitemap(site_dir: Path, expected: set[str]) -> list[str]:
    """The sitemap must list exactly the pages this build produced."""
    sitemap = site_dir / "sitemap.xml"
    if not sitemap.exists():
        return [f"{sitemap}: missing"]
    listed = set(LOC.findall(sitemap.read_text(encoding="utf-8")))
    return [
        f"sitemap.xml lists {url!r}, which is not a page this build produced"
        for url in sorted(listed - expected)
    ] + [
        f"sitemap.xml omits {url!r}, which this build did produce"
        for url in sorted(expected - listed)
    ]


def _check_robots(site_dir: Path, base: str) -> list[str]:
    """robots.txt must point at the sitemap under the same origin."""
    robots = site_dir / "robots.txt"
    if not robots.exists():
        return [f"{robots}: missing"]
    want = f"Sitemap: {base}/sitemap.xml"
    if want not in robots.read_text(encoding="utf-8"):
        return [f"robots.txt does not advertise {want!r}"]
    return []


def _report(problems: list[str], base: str) -> None:
    print(f"The rendered site disagrees with its deploy target {base!r}:", file=sys.stderr)
    # Bound the output: one wrong origin breaks every page identically, and 616 copies of the
    # same line would bury the other two failures underneath it.
    for problem in problems[:MAX_REPORTED]:
        print(f"  {problem}", file=sys.stderr)
    if len(problems) > MAX_REPORTED:
        print(f"  ... and {len(problems) - MAX_REPORTED} more", file=sys.stderr)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    site_dir, base = Path(argv[1]), argv[2].rstrip("/")
    if not base:
        print("error: empty base URL; refusing to certify the site's URLs", file=sys.stderr)
        return 2

    pages = sorted(site_dir.rglob("index.html"))
    if not pages:
        print(f"error: no pages found under {site_dir}", file=sys.stderr)
        return 2

    expected, problems = _check_canonicals(site_dir, pages, base)
    problems += _check_sitemap(site_dir, expected)
    problems += _check_robots(site_dir, base)

    if problems:
        _report(problems, base)
        return 1

    print(f"{len(pages)} pages, sitemap and robots.txt all agree with {base}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
