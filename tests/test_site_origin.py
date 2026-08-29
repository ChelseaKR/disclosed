"""The guard that decides whether the published site may name the host it names.

``.github/scripts/check_site_origin.py`` is the only executable in this repository that was held
to none of its standards. It is not under ``src``, so ``ruff check src tests`` never linted it,
``mypy`` with ``files = ["src"]`` never typed it, ``--cov=disclosed`` never measured a line of
it, and no test ever ran it. The one thing standing between the published site and issue #2 --
616 canonical links naming a host that served a 404 -- was the least-checked file here.

That is the coverage-gate failure in its purest form: not a module scoring badly, but a module
outside the denominator, so the 98% the coverage report prints was 98% of the code it looked at.

Each test below runs the real script over a real rendered site and asserts a specific way it
must refuse. The three checks it makes can break independently -- a page can self-canonicalise
elsewhere while the sitemap is fine, a sitemap can list a page that was never built -- so they
are broken independently here, one at a time, against a site that is otherwise correct.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from disclosed import site

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _ROOT / ".github" / "scripts" / "check_site_origin.py"
_ORIGIN = "https://example.test/disclosed"


def _load() -> ModuleType:
    """Import the checker from its path, since it is a script rather than a package module."""
    assert _SCRIPT.is_file(), (
        f"{_SCRIPT} is gone. It is the only thing comparing the origin stamped into every "
        "canonical link against the origin Pages actually serves; issue #2 was exactly that "
        "comparison not existing."
    )
    spec = importlib.util.spec_from_file_location("check_site_origin", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


check = _load()

_REPORT: dict[str, Any] = {
    "scope": {
        "kind": "sample",
        "source": "College Scorecard",
        "institutions": 1,
        "states": 1,
        "universe": 6300,
        "coverage": 1 / 6300,
        "note": "A slice.",
    },
    "institutions": 1,
    "ungradeable": 0,
    "overall": {
        "label": "all institutions",
        "graded": 1,
        "ungradeable": 0,
        "mean_score": 1.0,
        "worst_fields": [],
    },
    "by_state": [
        {"label": "CA", "graded": 1, "ungradeable": 0, "mean_score": 1.0, "worst_fields": []}
    ],
    "implausible": [],
    "grades": [
        {
            "unit_id": "1",
            "name": "A College",
            "state": "CA",
            "score": 1.0,
            "letter": "A",
            "fields": {"Admission rate": "reported"},
        }
    ],
}


@pytest.fixture
def built(tmp_path: Path) -> Path:
    """A real site rendered by the real generator, at the origin the checker will be given.

    Rendered rather than hand-written, because the property under test is that the renderer and
    the checker agree about the shape of a URL. A fixture of hand-typed HTML would agree with
    whichever of the two it was copied from and prove nothing about the other.
    """
    out = tmp_path / "site"
    site.build(_REPORT, out, origin=_ORIGIN, generated="2026-08-05")
    return out


def _run(site_dir: Path, base: str = _ORIGIN) -> int:
    result: int = check.main(["check_site_origin.py", str(site_dir), base])
    return result


class TestASiteThatAgreesWithItsDeployTarget:
    def test_a_correctly_rendered_site_passes(self, built: Path) -> None:
        assert _run(built) == 0

    def test_every_page_kind_is_actually_examined(self, built: Path) -> None:
        """The nested paths are the ones a URL-shape bug would hide in.

        ``state/CA`` and ``institution/1`` are two directories deep; the home page is zero. If
        the checker only ever agreed with the renderer about the root, this would still pass, so
        the page set is asserted rather than assumed.
        """
        pages = sorted(p.parent.relative_to(built).as_posix() for p in built.rglob("index.html"))
        assert pages == [".", "institution/1", "methodology", "state/CA"]

    def test_a_trailing_slash_on_the_deploy_target_is_not_a_disagreement(self, built: Path) -> None:
        """The Pages API reports the base URL with a trailing slash on some repositories, and the
        workflow strips it before rendering. A checker that did not would reject every page."""
        assert _run(built, _ORIGIN + "/") == 0


class TestEachCheckCanFail:
    """One broken promise at a time, against a site that is otherwise correct."""

    def test_a_page_that_self_canonicalises_elsewhere_is_refused(self, built: Path) -> None:
        """Issue #2 itself: a link telling crawlers to index somewhere that is not here."""
        page = built / "institution" / "1" / "index.html"
        page.write_text(
            page.read_text(encoding="utf-8").replace(
                f'<link rel="canonical" href="{_ORIGIN}/institution/1/"',
                '<link rel="canonical" href="https://elsewhere.test/institution/1/"',
            ),
            encoding="utf-8",
        )
        assert _run(built) == 1

    def test_a_page_with_no_canonical_at_all_is_refused(self, built: Path) -> None:
        page = built / "index.html"
        page.write_text(
            page.read_text(encoding="utf-8").replace('<link rel="canonical"', "<link rel='x'"),
            encoding="utf-8",
        )
        assert _run(built) == 1

    def test_a_page_whose_og_url_disagrees_with_its_canonical_is_refused(self, built: Path) -> None:
        """The page names itself twice. Answering the question differently is worse than not
        answering it, because each answer looks authoritative on its own."""
        page = built / "methodology" / "index.html"
        page.write_text(
            page.read_text(encoding="utf-8").replace(
                f'<meta property="og:url" content="{_ORIGIN}/methodology/">',
                '<meta property="og:url" content="https://elsewhere.test/methodology/">',
            ),
            encoding="utf-8",
        )
        assert _run(built) == 1

    def test_a_root_relative_reference_is_refused(self, built: Path) -> None:
        """The origin hazard one level down from issue #2.

        This site is served at a path on an origin five sibling projects also publish under, and
        the bare origin is a 404. ``href="/methodology/"`` therefore does not point at this
        site's methodology page: it points at another project or at nothing. A browser shows
        nothing wrong, because the browser already has the page it is rendering.
        """
        page = built / "index.html"
        page.write_text(
            page.read_text(encoding="utf-8").replace(
                '<a class="skip" href="#content">', '<a class="skip" href="/#content">'
            ),
            encoding="utf-8",
        )
        assert _run(built) == 1

    def test_two_pages_sharing_a_description_are_refused(self, built: Path) -> None:
        """617 pages describing themselves identically is 617 pages a result list cannot tell
        apart, and it is the state the sibling documentation site was actually found in."""
        home = built / "index.html"
        other = built / "methodology" / "index.html"
        described = re.search(
            r'<meta name="description" content="([^"]*)"', home.read_text(encoding="utf-8")
        )
        assert described is not None
        text = other.read_text(encoding="utf-8")
        replaced = re.sub(
            r'<meta name="description" content="[^"]*"',
            f'<meta name="description" content="{described.group(1)}"',
            text,
            count=1,
        )
        assert replaced != text
        other.write_text(replaced, encoding="utf-8")
        assert _run(built) == 1

    def test_two_pages_sharing_a_title_are_refused(self, built: Path) -> None:
        home = built / "index.html"
        other = built / "state" / "CA" / "index.html"
        titled = re.search(r"<title>(.*?)</title>", home.read_text(encoding="utf-8"), re.S)
        assert titled is not None
        other.write_text(
            re.sub(
                r"<title>.*?</title>",
                f"<title>{titled.group(1)}</title>",
                other.read_text(encoding="utf-8"),
                count=1,
                flags=re.S,
            ),
            encoding="utf-8",
        )
        assert _run(built) == 1

    def test_a_page_with_an_empty_description_is_refused(self, built: Path) -> None:
        """``content=""`` reads as "described" to everything that looks for the attribute."""
        page = built / "state" / "CA" / "index.html"
        page.write_text(
            re.sub(
                r'<meta name="description" content="[^"]*"',
                '<meta name="description" content=""',
                page.read_text(encoding="utf-8"),
                count=1,
            ),
            encoding="utf-8",
        )
        assert _run(built) == 1

    def test_a_sitemap_promising_a_page_that_was_never_built_is_refused(self, built: Path) -> None:
        """A sitemap is a promise the URLs in it exist. An entry with no file behind it is a 404
        with an invitation attached."""
        sitemap = built / "sitemap.xml"
        sitemap.write_text(
            sitemap.read_text(encoding="utf-8").replace(
                "</urlset>", f"<url><loc>{_ORIGIN}/institution/999/</loc></url></urlset>"
            ),
            encoding="utf-8",
        )
        assert _run(built) == 1

    def test_a_sitemap_that_omits_a_built_page_is_refused(self, built: Path) -> None:
        sitemap = built / "sitemap.xml"
        sitemap.write_text(
            sitemap.read_text(encoding="utf-8").replace(
                f"<url><loc>{_ORIGIN}/institution/1/</loc></url>", ""
            ),
            encoding="utf-8",
        )
        assert _run(built) == 1

    def test_a_missing_sitemap_is_refused(self, built: Path) -> None:
        (built / "sitemap.xml").unlink()
        assert _run(built) == 1

    def test_robots_advertising_another_origin_is_refused(self, built: Path) -> None:
        robots = built / "robots.txt"
        robots.write_text(
            robots.read_text(encoding="utf-8").replace(_ORIGIN, "https://elsewhere.test"),
            encoding="utf-8",
        )
        assert _run(built) == 1

    def test_a_missing_robots_is_refused(self, built: Path) -> None:
        (built / "robots.txt").unlink()
        assert _run(built) == 1


class TestItRefusesRatherThanCertifyingNothing:
    """The three ways this script could have passed over nothing at all.

    A checker that exits 0 because it found no pages, or because it was handed an empty origin
    to compare against, is the failure the site it guards exists to describe. Each of these
    exits 2 -- distinct from the 1 that means "checked and disagreed" -- so a reader of the CI
    log can tell a refusal from a finding.
    """

    def test_an_empty_deploy_target_is_refused_rather_than_matched(self, built: Path) -> None:
        """Every canonical would compare against ``""``, and a site rendered with an empty origin
        would agree with it. Passing here would certify a site that self-canonicalises to ``/``."""
        assert _run(built, "") == 2
        assert _run(built, "/") == 2

    def test_a_directory_with_no_pages_in_it_is_refused(self, tmp_path: Path) -> None:
        """The whole script is loops over the page list. Over an empty list they all pass."""
        empty = tmp_path / "nothing"
        empty.mkdir()
        assert _run(empty) == 2
        assert _run(tmp_path / "does-not-exist") == 2

    def test_the_wrong_number_of_arguments_is_refused(self) -> None:
        assert check.main(["check_site_origin.py"]) == 2
        assert check.main(["check_site_origin.py", "site", "https://x.test", "extra"]) == 2


class TestTheReportIsReadable:
    """The failure message, which is the only part of this a person actually reads.

    One wrong origin breaks all 616 pages identically, so the report has to be bounded or the
    sitemap and robots failures -- the two that say something the first line does not -- are
    pushed off the end of the log by 600 copies of one sentence. Bounded output is only honest
    if it says it is bounded, so the count of what was dropped is asserted too.
    """

    @pytest.fixture
    def many(self, tmp_path: Path) -> Path:
        """A site with comfortably more disagreements than the report will print."""
        out = tmp_path / "many"
        site.build(
            {
                **_REPORT,
                "grades": [
                    {**_REPORT["grades"][0], "unit_id": str(i), "name": f"College {i}"}
                    for i in range(check.MAX_REPORTED + 5)
                ],
            },
            out,
            origin=_ORIGIN,
            generated="2026-08-05",
        )
        return out

    def _stderr(self, captured: pytest.CaptureFixture[str]) -> list[str]:
        return captured.readouterr().err.splitlines()

    def test_a_wrong_origin_reports_a_bounded_number_of_lines(
        self, many: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert _run(many, "https://elsewhere.test") == 1
        reported = self._stderr(capsys)
        assert "https://elsewhere.test" in reported[0]
        # One header, MAX_REPORTED problems, one line saying how many were not printed.
        assert len(reported) == check.MAX_REPORTED + 2, reported

    def test_more_problems_than_it_prints_are_counted_rather_than_dropped(
        self, many: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A truncated list that does not say it is truncated is a count nobody can check.

        Matched as a whole line rather than by searching for the word. The first version of this
        test looked for "more" anywhere in the last line and passed against a build with the
        truncation notice deleted, because pytest's temporary directory is named after the test
        and the test has the word "more" in its name. A gate that its own fixture satisfies is
        the thing this file is about.
        """
        assert _run(many, "https://elsewhere.test") == 1
        assert re.fullmatch(r"\s*\.\.\. and \d+ more", self._stderr(capsys)[-1])

    def test_a_report_small_enough_to_print_whole_is_not_truncated(
        self, built: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The other side of the bound: a four-page site must not claim anything was dropped."""
        assert _run(built, "https://elsewhere.test") == 1
        reported = self._stderr(capsys)
        assert len(reported) < check.MAX_REPORTED + 2
        assert not [line for line in reported if "more" in line.split()]
