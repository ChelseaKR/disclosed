"""Fail the build when the rendered site's URLs disagree with where it is being deployed.

Issue #2 was that 616 canonical links and every sitemap entry named an origin that served a
404. The origin was correct in shape and simply hardcoded, so nothing in the build could
notice it had stopped being true. This check closes that: it is given the deploy target the
Pages API actually reported, and it refuses the build unless every URL the site emits agrees
with it.

Six separate promises are checked, because they can break independently:

1. Every page's ``<link rel="canonical">`` is the deploy target plus that page's own path. A
   page that self-canonicalises somewhere else tells crawlers to index the other place.
2. ``og:url`` says the same thing the canonical says. They are two claims about which page
   this is, and a page that answers the question twice must not answer it differently.
3. No page makes a reference that escapes the site's own path, in either of the two forms it
   can take. This site is served at a *path* under an origin five sibling projects also publish
   under, and the bare origin is a 404, so an ``href="/methodology/"`` resolves against the
   origin and lands on another project or on nothing. The relative form does the same thing
   without a leading slash: ``href="../methodology/"`` on a page at the site root resolves one
   directory above it and lands in exactly the same place. Both are the same failure as issue #2
   pointed one level down, and both are just as invisible in a browser, which renders the page it
   was already handed. Only the rooted form used to be checked here, which is how the home page
   came to emit five rationale links that 404 on the deployed site (issue #69) while this check
   passed the build; each reference is now resolved against the directory its page is really
   served from.
4. Every page has a non-empty title and description, and no two pages share either. 617 pages
   describing themselves identically is 617 pages a result list cannot tell apart.
5. ``sitemap.xml`` lists exactly the pages that were built, no more and no fewer. A sitemap is
   a promise that the URLs in it exist; an entry with no file behind it is a 404 with an
   invitation attached, and a built page missing from the sitemap goes unlisted.
6. ``robots.txt`` advertises the sitemap under the same origin.

On (6), and said here rather than left to look like more than it is: a ``robots.txt`` is only
read at an origin root. Crawlers fetch ``https://chelseakr.github.io/robots.txt``, which this
repository does not own and which serves a 404, and never
``https://chelseakr.github.io/disclosed/robots.txt``. So the file this build writes is correct
for anyone who fetches it and is discovered by nobody. Getting the sitemap seen is a
submission, not a file, and that is the owner's action. This check holds the file honest; it
cannot make it load-bearing.

Usage: check_site_origin.py <site-dir> <base-url>
"""

from __future__ import annotations

import posixpath
import re
import sys
from pathlib import Path

CANONICAL = re.compile(r'<link rel="canonical" href="([^"]*)"')
OG_URL = re.compile(r'<meta property="og:url" content="([^"]*)"')
LOC = re.compile(r"<loc>([^<]*)</loc>")
TITLE = re.compile(r"<title>(.*?)</title>", re.S)
DESCRIPTION = re.compile(r'<meta name="description" content="([^"]*)"')
# Root-relative only. A protocol-relative ``//host/path`` is a different thing and is not the
# mistake being looked for, so the negative lookahead excludes it rather than leaving it to be
# caught by accident.
ROOT_RELATIVE = re.compile(r'(?:href|src|content)="(/(?!/)[^"]*)"')
# The relative half of promise 3. ``content`` is excluded deliberately: og:url and the other
# metadata this site writes there are absolute, so including it would only add false positives.
HREF_OR_SRC = re.compile(r'(?:href|src)="([^"]*)"')
SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:")
MAX_REPORTED = 20


def _escaping_references(site_dir: Path, page: Path, text: str) -> list[str]:
    """References that resolve above the site root once read from the page's real directory.

    A ``../`` prefix is only wrong relative to where the page is actually served, so the depth
    cannot be judged from the reference alone: ``../methodology/`` is correct on a page in
    ``national/`` and lands outside the site on the one at the root. Each reference is resolved
    against its page's own directory, which is the same thing a browser does.
    """
    rel = page.parent.relative_to(site_dir).as_posix()
    base = "" if rel == "." else rel
    escaping: list[str] = []
    for reference in HREF_OR_SRC.findall(text):
        target = reference.split("#", 1)[0].split("?", 1)[0]
        # Rooted references are promise 3's other half and are reported there; absolute URLs,
        # ``mailto:`` and fragment-only links do not resolve against the page's directory at all.
        if not target or target.startswith("/") or SCHEME.match(target):
            continue
        resolved = posixpath.normpath(posixpath.join(base, target))
        if resolved == ".." or resolved.startswith("../"):
            escaping.append(reference)
    return escaping


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


def _check_self_description(site_dir: Path, pages: list[Path]) -> list[str]:
    """Each page says which page it is, once, in its own words, with no escape to the origin."""
    problems: list[str] = []
    titles: dict[str, Path] = {}
    descriptions: dict[str, Path] = {}
    for page in pages:
        text = page.read_text(encoding="utf-8")

        canonical = CANONICAL.search(text)
        og_url = OG_URL.search(text)
        if og_url is None:
            problems.append(f"{page}: no og:url")
        elif canonical is not None and og_url.group(1) != canonical.group(1):
            problems.append(
                f"{page}: og:url is {og_url.group(1)!r} but the canonical is "
                f"{canonical.group(1)!r}; the page names itself twice and disagrees"
            )

        rooted = ROOT_RELATIVE.findall(text)
        if rooted:
            problems.append(
                f"{page}: root-relative reference {rooted[0]!r} resolves against the origin, "
                f"not against this site's path"
            )

        escaping = _escaping_references(site_dir, page, text)
        if escaping:
            problems.append(
                f"{page}: relative reference {escaping[0]!r} resolves above the site root, "
                f"so it lands outside this site exactly as a root-relative one would"
            )

        title = TITLE.search(text)
        if title is None or not title.group(1).strip():
            problems.append(f"{page}: no title")
        else:
            first = titles.setdefault(title.group(1).strip(), page)
            if first != page:
                problems.append(f"{page}: title is the same as {first}'s")

        description = DESCRIPTION.search(text)
        if description is None or not description.group(1).strip():
            problems.append(f"{page}: no meta description")
        else:
            first = descriptions.setdefault(description.group(1).strip(), page)
            if first != page:
                problems.append(f"{page}: description is the same as {first}'s")
    return problems


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
    problems += _check_self_description(site_dir, pages)
    problems += _check_sitemap(site_dir, expected)
    problems += _check_robots(site_dir, base)

    if problems:
        _report(problems, base)
        return 1

    print(f"{len(pages)} pages, sitemap and robots.txt all agree with {base}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
