"""The generated pages, checked against the bar they are published to hold.

A page nobody can read has not disclosed anything, which makes accessibility the same argument as
the rest of this project rather than a separate one bolted on. These run in ``make verify`` with
no browser and no network, so a regression fails the build rather than waiting for somebody to
remember to audit the site.

They do not replace a real audit. Contrast ratios, landmark structure, table semantics and the
resource budget are what a static checker can prove; whether the prose makes sense to a screen
reader user is not, and the Lighthouse job in ``.github/workflows/accessibility.yml`` is what
carries the part that needs a rendering engine.

The resource budget moved in here from ``lighthouse-budget.json``, which turned out to enforce
nothing at all. :class:`TestTheResourceBudget` says how that was measured.
"""

from __future__ import annotations

import html.parser
import re
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest

from disclosed import national, site

_REPORT: dict[str, Any] = {
    "scope": {
        "kind": "sample",
        "source": "College Scorecard",
        "institutions": 2,
        "states": 1,
        "universe": 6300,
        "coverage": 2 / 6300,
        "note": "A slice.",
    },
    "institutions": 2,
    "ungradeable": 1,
    "overall": {
        "label": "all institutions",
        "graded": 1,
        "ungradeable": 1,
        "mean_score": 0.5,
        "worst_fields": [["Admission rate", 1]],
    },
    "by_state": [
        {
            "label": "CA",
            "graded": 1,
            "ungradeable": 1,
            "mean_score": 0.5,
            "worst_fields": [["Admission rate", 1]],
        }
    ],
    "implausible": [],
    "grades": [
        {
            "unit_id": "1",
            "name": "Graded College",
            "state": "CA",
            "score": 0.5,
            "letter": "D",
            "fields": {"Admission rate": "missing", "Enrollment": "reported"},
        },
        {
            "unit_id": "2",
            "name": "Suppressed College",
            "state": "CA",
            "score": None,
            "letter": None,
            "fields": {"Admission rate": "suppressed"},
        },
    ],
}

_NATIONAL: dict[str, Any] = {
    "scope": {
        "kind": "national",
        "source": "IPEDS directory",
        "institutions": 1,
        "states": 1,
        "universe": 1,
        "coverage": 1.0,
        "note": "The whole directory.",
    },
    "ungradeable": 0,
    "contradictions": [],
    "grades": [
        {
            "unit_id": "1",
            "name": "Gap College",
            "state": "CA",
            "fields": {"Net price calculator": "missing"},
        }
    ],
}


@pytest.fixture
def built(tmp_path: Path) -> Path:
    site.build(
        _REPORT,
        tmp_path,
        origin="https://example.test",
        generated="2026-08-05",
        national=national.build(_NATIONAL),
    )
    return tmp_path


# The fixture renders one page of every kind the generator produces: home, methodology, national,
# one state, and one institution page per graded record. A floor rather than an exact count, so
# that adding a page kind does not fail here, but a build that rendered nothing does.
_EXPECTED_PAGES: int = 6


def _pages(root: Path) -> list[Path]:
    """Every generated page, refusing to return a set too small to have proved anything.

    Every check in this module is a loop over this list with the assertion inside the loop, so a
    build that rendered nothing would satisfy all of them without a single page being read, and
    the suite would go green over a site that does not exist. That is precisely the failure the
    Lighthouse gate in ``.github/workflows/accessibility.yml`` already refuses — "a pass over a
    partial set is not a pass" — and it is worse here, because the browser job at least names the
    five pages it expects while this one took whatever the glob happened to find.

    Asserting in a helper rather than in each test is deliberate: the alternative is remembering to
    add a guard to every future test, and a check that depends on being remembered is the kind this
    project keeps finding other people's bugs in.
    """
    found = sorted(root.rglob("index.html"))
    assert len(found) >= _EXPECTED_PAGES, (
        f"the fixture built {len(found)} pages, fewer than the {_EXPECTED_PAGES} this suite exists "
        "to audit; every assertion below is inside a loop over them and would pass vacuously"
    )
    return found


def _relative_luminance(hex_colour: str) -> float:
    value = hex_colour.lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    channels = [int(value[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(foreground: str, background: str) -> float:
    """WCAG 2.x contrast ratio between two colours, lighter over darker."""
    a, b = _relative_luminance(foreground), _relative_luminance(background)
    lighter, darker = max(a, b), min(a, b)
    return (lighter + 0.05) / (darker + 0.05)


class TestContrast:
    """Every colour pair the stylesheet actually puts together, against WCAG AA.

    Written as a table of pairs rather than parsed out of the CSS, because the pairing is the
    thing being asserted and a parser would only tell us the colours exist.
    """

    _LIGHT_BACKGROUND = "#ffffff"
    _DARK_BACKGROUND = "#131313"

    @pytest.mark.parametrize(
        "colour",
        ["#1a1a1a", "#0b5cad", "#333333", "#555555", "#14691f", "#96110f", "#a8421f"],
    )
    def test_light_mode_text_meets_aa(self, colour: str) -> None:
        assert contrast(colour, self._LIGHT_BACKGROUND) >= 4.5

    @pytest.mark.parametrize(
        "colour", ["#e9e9e9", "#79b8ff", "#cfcfcf", "#bbbbbb", "#6fbf73", "#ff8a80", "#ffab7a"]
    )
    def test_dark_mode_text_meets_aa(self, colour: str) -> None:
        assert contrast(colour, self._DARK_BACKGROUND) >= 4.5

    @pytest.mark.parametrize(
        "badge", ["#14691f", "#3f7d20", "#8a5a00", "#a8421f", "#96110f", "#555555"]
    )
    def test_white_text_on_every_grade_badge_meets_aa(self, badge: str) -> None:
        """Including the F badge. An institution graded badly still has to be able to read it."""
        assert contrast("#ffffff", badge) >= 4.5

    def test_the_focus_ring_is_visible_against_both_backgrounds(self) -> None:
        assert contrast("#0b5cad", self._LIGHT_BACKGROUND) >= 3.0
        assert contrast("#79b8ff", self._DARK_BACKGROUND) >= 3.0

    def test_every_colour_in_the_stylesheet_is_covered_by_a_case_above(self) -> None:
        """A colour added to the stylesheet and not to the table above fails here rather than
        shipping unchecked. The whole point of a contrast test is that it notices new colours."""
        declared = set(re.findall(r"#[0-9a-fA-F]{3,6}", site._STYLE))
        checked = {
            "#ffffff",
            "#fff",
            "#131313",
            "#1a1a1a",
            "#0b5cad",
            "#333",
            "#333333",
            "#555",
            "#555555",
            "#14691f",
            "#3f7d20",
            "#8a5a00",
            "#a8421f",
            "#96110f",
            "#e9e9e9",
            "#79b8ff",
            "#cfcfcf",
            "#bbb",
            "#bbbbbb",
            "#6fbf73",
            "#ff8a80",
            "#ffab7a",
            "#e3e3e3",
        }
        assert declared <= checked, f"unchecked colours: {sorted(declared - checked)}"


class TestTheSuiteActuallyAuditsSomething:
    """That the checks below have subjects, asserted once rather than assumed everywhere.

    ``all([])`` is ``True`` and a ``for`` loop over an empty list runs no assertions, so most of
    this module is capable of reporting success over nothing at all. :func:`_pages` stops the
    whole-site version of that; these stop the per-feature version, where the pages exist but no
    longer contain the element a check was written for.
    """

    def test_one_page_of_every_kind_is_audited(self, built: Path) -> None:
        relative = {p.parent.relative_to(built).as_posix() for p in _pages(built)}
        assert {".", "methodology", "national", "state/CA", "institution/1", "institution/2"} <= (
            relative
        )

    def test_at_least_one_data_table_is_audited(self, built: Path) -> None:
        """Otherwise the caption and row-header checks are ``all([])`` on every page."""
        tables = sum(len(TestTableSemantics()._parse(page).captions) for page in _pages(built))
        assert tables > 0

    def test_at_least_one_nav_and_one_column_header_are_audited(self, built: Path) -> None:
        text = "".join(page.read_text(encoding="utf-8") for page in _pages(built))
        assert re.findall(r"<nav[^>]*>", text)
        assert re.findall(r"<th\b[^>]*>", text)


class TestLandmarksAndNavigation:
    def test_every_page_offers_a_skip_link_that_lands_somewhere(self, built: Path) -> None:
        """616 pages each open with a breadcrumb. Tabbing past it on every one is the bypass-blocks
        failure, and the target has to exist or the link is worse than none."""
        for page in _pages(built):
            text = page.read_text(encoding="utf-8")
            assert '<a class="skip" href="#content">' in text, page
            assert 'id="content"' in text, page

    def test_every_page_has_exactly_one_main_and_one_h1(self, built: Path) -> None:
        for page in _pages(built):
            text = page.read_text(encoding="utf-8")
            assert text.count("<main") == 1, page
            assert text.count("<h1>") == 1, page

    def test_every_page_declares_a_language(self, built: Path) -> None:
        for page in _pages(built):
            assert '<html lang="en">' in page.read_text(encoding="utf-8"), page

    def test_the_viewport_does_not_forbid_zooming(self, built: Path) -> None:
        for page in _pages(built):
            text = page.read_text(encoding="utf-8")
            assert "user-scalable=no" not in text, page
            assert "maximum-scale" not in text, page

    def test_navigation_landmarks_are_named(self, built: Path) -> None:
        """Two unlabelled navs on a page are indistinguishable in a landmark list."""
        for page in _pages(built):
            text = page.read_text(encoding="utf-8")
            for nav in re.findall(r"<nav[^>]*>", text):
                assert "aria-label=" in nav, f"{page}: {nav}"


class TestHeadingOrder:
    def test_no_page_skips_a_heading_level(self, built: Path) -> None:
        for page in _pages(built):
            levels = [int(m) for m in re.findall(r"<h([1-6])[ >]", page.read_text("utf-8"))]
            assert levels and levels[0] == 1, page
            for previous, current in pairwise(levels):
                assert current <= previous + 1, f"{page}: h{previous} then h{current}"


class TestTableSemantics:
    class _Tables(html.parser.HTMLParser):
        """Collects, per table, whether it had a caption and whether its body rows are headed."""

        def __init__(self) -> None:
            super().__init__()
            self.depth = 0
            self.captions: list[bool] = []
            self.row_headers: list[bool] = []
            self._in_body = False
            self._row_has_header = True

        def handle_starttag(self, tag: str, attrs: Any) -> None:
            if tag == "table":
                self.depth += 1
                self.captions.append(False)
                self.row_headers.append(True)
            elif tag == "caption" and self.captions:
                self.captions[-1] = True
            elif tag == "tbody":
                self._in_body = True
            elif tag == "tr" and self._in_body:
                self._row_has_header = False
            elif tag == "th" and self._in_body:
                self._row_has_header = True

        def handle_endtag(self, tag: str) -> None:
            if tag == "tr" and self._in_body and not self._row_has_header and self.row_headers:
                self.row_headers[-1] = False
            elif tag == "tbody":
                self._in_body = False

    def _parse(self, page: Path) -> _Tables:
        parser = self._Tables()
        parser.feed(page.read_text(encoding="utf-8"))
        return parser

    def test_every_data_table_has_a_caption(self, built: Path) -> None:
        for page in _pages(built):
            parsed = self._parse(page)
            assert all(parsed.captions), f"{page}: {parsed.captions}"

    def test_every_data_row_starts_with_a_row_header(self, built: Path) -> None:
        """Without one, a screen reader reading the third cell of the four hundredth row announces
        a classification with nothing attached to say whose it is."""
        for page in _pages(built):
            parsed = self._parse(page)
            assert all(parsed.row_headers), f"{page}: {parsed.row_headers}"

    def test_column_headers_declare_their_scope(self, built: Path) -> None:
        for page in _pages(built):
            text = page.read_text(encoding="utf-8")
            for header in re.findall(r"<th\b[^>]*>", text):
                assert "scope=" in header, f"{page}: {header}"


class TestTheResourceBudget:
    """Zero subresources of any kind, on every page, checked from the built bytes.

    ``lighthouse-budget.json`` budgets every non-document resource type at zero, and the README
    said that made adding one "a build failure rather than a decision nobody noticed". It did
    not. Lighthouse's ``--budget-path`` produces a ``performance-budget`` audit and never a
    non-zero exit, the scoring step in ``.github/workflows/accessibility.yml`` reads only
    ``categories.accessibility.score``, and Lighthouse 12 does not emit the budget audits at all:
    a run of ``lighthouse@12`` (12.8.2) against a budget file with every line set to zero exited
    **0**, scored accessibility **1**, and its report contained **no audit whose key mentions
    "budget"**. Four of the five audited pages ask for ``--only-categories=accessibility``, which
    does not even collect ``resource-summary``. The budget file was enforced by nothing.

    So it is enforced here instead, and more completely: this runs over every page the generator
    produces rather than one of each of five kinds, needs no browser, and fails in ``make
    verify`` where the person who added the tracker is still looking at the diff.

    Scope, stated rather than implied. This checks the resource *counts*, which are the claim the
    README actually makes: no scripts, no external stylesheets, no fonts, no images, no
    third-party requests. It does not check the transfer-size or timing budgets, which need a
    browser and a network to mean anything. Those are still unenforced.
    """

    # Anything that makes the browser go and fetch something. `<a>` is deliberately absent: the
    # pages link out to Cornell's US Code and to the source agencies, and an outbound link is not
    # a request the page makes.
    _FETCHING_TAGS = frozenset(
        {
            "script",
            "img",
            "picture",
            "source",
            "video",
            "audio",
            "track",
            "iframe",
            "embed",
            "object",
            "frame",
            "applet",
        }
    )

    # `<link>` fetches for every relation except these. `canonical` and `alternate` are metadata:
    # they name a URL, they do not retrieve one.
    _INERT_LINK_RELS = frozenset({"canonical", "alternate"})

    class _Resources(html.parser.HTMLParser):
        def __init__(self, fetching: frozenset[str], inert_rels: frozenset[str]) -> None:
            super().__init__()
            self._fetching = fetching
            self._inert_rels = inert_rels
            self.requests: list[str] = []

        def handle_starttag(self, tag: str, attrs: Any) -> None:
            attributes = {name: (value or "") for name, value in attrs}
            if tag in self._fetching:
                self.requests.append(f"<{tag}>")
            elif tag == "link":
                rel = attributes.get("rel", "").strip().lower()
                if rel not in self._inert_rels:
                    self.requests.append(f'<link rel="{rel}">')
            if "srcset" in attributes or "background" in attributes:
                self.requests.append(
                    f"<{tag} {'srcset' if 'srcset' in attributes else 'background'}>"
                )

    def _requests(self, page: Path) -> list[str]:
        parser = self._Resources(self._FETCHING_TAGS, self._INERT_LINK_RELS)
        parser.feed(page.read_text(encoding="utf-8"))
        return parser.requests

    def test_no_page_fetches_anything_but_itself(self, built: Path) -> None:
        offenders = {
            page.relative_to(built).as_posix(): found
            for page in _pages(built)
            if (found := self._requests(page))
        }
        assert not offenders, (
            "lighthouse-budget.json budgets every non-document resource type at zero and the "
            f"site now requests these: {offenders}. A page about undisclosed information should "
            "not be quietly shipping a tracker. If the resource is genuinely wanted, change the "
            "budget file and this test in the same commit, so the decision is visible."
        )

    def test_no_stylesheet_reaches_off_the_page(self, built: Path) -> None:
        """The CSS is inline, so the budget's zero-stylesheet line is only true while nothing
        inside it fetches. ``@import`` and ``url()`` are both requests the count would miss."""
        for page in _pages(built):
            for block in re.findall(r"<style\b[^>]*>(.*?)</style>", page.read_text("utf-8"), re.S):
                assert "@import" not in block, page
                assert "url(" not in block, page
                assert "@font-face" not in block, page


class TestMeaningIsNeverCarriedByColourAlone:
    def test_the_ungradeable_badge_says_so_in_text(self, built: Path) -> None:
        """It used to carry its meaning in a title attribute, which a screen reader may not read
        and a keyboard user cannot reach. "n a" alone is the audible version of printing an
        absence as a bare number."""
        page = (built / "institution" / "2" / "index.html").read_text(encoding="utf-8")
        assert "not gradeable, no field applied" in page
        assert 'title="No gradeable fields"' not in page

    def test_a_disclosure_state_is_always_a_word_and_not_only_a_colour(self, built: Path) -> None:
        page = (built / "institution" / "1" / "index.html").read_text(encoding="utf-8")
        assert "Not reported" in page
        assert "Reported" in page
