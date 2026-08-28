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
nothing at all. :class:`TestTheResourceBudget` says how that was measured, and
:class:`TestEveryBudgetLineIsAccountedFor` is the check that keeps any line of that file from
going back to being a declaration nothing reads.
"""

from __future__ import annotations

import html.parser
import json
import re
from itertools import pairwise
from pathlib import Path
from typing import Any, ClassVar

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

# The committed report and the committed national artifact: the exact bytes `pages.yml` renders
# and uploads. Rendered whole in TestTheResourceBudgetOverThePublishedSite below.
_ROOT = Path(__file__).resolve().parent.parent
_DATA = _ROOT / "data"
_WORKFLOWS = _ROOT / ".github" / "workflows"
_BUDGET_FILE = _ROOT / "lighthouse-budget.json"

# Lighthouse states resource budgets in KiB.
_KIB = 1024


def _budget() -> dict[str, Any]:
    """The one path entry in ``lighthouse-budget.json``, refusing a file it cannot map to pages.

    A Lighthouse budget is a list of per-path entries, and this project has exactly one, matching
    everything. The checks below apply its numbers to every generated page, which is only the
    same statement while that stays true: a second entry, or a narrower path, would mean some
    pages are budgeted differently and these loops would be quietly holding them to the wrong
    line. Read once here and asserted, rather than assumed at every call site.
    """
    entries = json.loads(_BUDGET_FILE.read_text(encoding="utf-8"))
    assert len(entries) == 1, (
        f"lighthouse-budget.json carries {len(entries)} path entries; every check in this module "
        "applies one entry's numbers to every page, so a second entry needs the checks to learn "
        "which pages it governs before it can mean anything"
    )
    entry: dict[str, Any] = entries[0]
    assert entry["path"] == "/*", (
        f"lighthouse-budget.json budgets the path {entry['path']!r} rather than every page. The "
        "checks below hold every generated page to these numbers, which would then be a claim "
        "about pages the budget file does not make."
    )
    return entry


def _size_budgets() -> dict[str, int]:
    """The ``resourceSizes`` lines of that entry, in bytes."""
    return {line["resourceType"]: line["budget"] * _KIB for line in _budget()["resourceSizes"]}


@pytest.fixture(scope="module")
def published(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The published site, built from the committed artifacts, once for the whole module.

    Same three arguments ``pages.yml`` passes to ``disclosed site``, so a page this fixture never
    renders is a page the real published site was never audited over either.
    """
    out = tmp_path_factory.mktemp("published")
    site.build(
        json.loads((_DATA / "report.json").read_text(encoding="utf-8")),
        out,
        origin="https://example.test",
        generated="2026-08-05",
        national=json.loads((_DATA / "national.json").read_text(encoding="utf-8")),
        scorecard_census=json.loads((_DATA / "scorecard-census.json").read_text(encoding="utf-8")),
    )
    return out


def _pages(root: Path) -> list[Path]:
    """Every generated page, refusing to return a set too small to have proved anything.

    Every check in this module is a loop over this list with the assertion inside the loop, so a
    build that rendered nothing would satisfy all of them without a single page being read, and
    the suite would go green over a site that does not exist. That is precisely the failure the
    Lighthouse gate in ``.github/workflows/accessibility.yml`` already refuses — "a pass over a
    partial set is not a pass" — and it is worse here, because the browser job at least names the
    six pages it expects while this one took whatever the glob happened to find.

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
        """617 pages each open with a breadcrumb. Tabbing past it on every one is the bypass-blocks
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
    third-party requests. It is deliberately stricter than the budget file rather than derived
    from it: the file names five non-document types and this refuses **every** request, so a
    resource of a type nobody thought to budget is a failure here too. The ``resourceSizes``
    lines are enforced by :class:`TestTheTransferSizeBudget`, and
    :class:`TestEveryBudgetLineIsAccountedFor` is what stops a line of that file from going back
    to being enforced by nobody.
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


class TestTheResourceBudgetOverThePublishedSite:
    """The same budget, over the site that is actually published rather than a fixture of it.

    ``.github/workflows/accessibility.yml`` said the budget was "enforced statically over every
    one of the 616 generated pages in ``make verify``". It was not, and the gap was not a
    quibble about wording. ``make verify`` never built 616 pages: :class:`TestTheResourceBudget`
    runs over the six-page fixture at the top of this module, whose report carries two
    institutions, no implausible finding, and therefore none of the markup the home page and the
    institution pages render around a finding -- the ``<code>`` block holding a published value,
    the peer verdict, the rationale. A subresource introduced into that branch would have shipped
    past a suite claiming to have checked every page.

    So the committed report is rendered here, with the committed national artifact beside it, and
    every page of the result is parsed. It is the same bytes ``pages.yml`` uploads. It costs about
    half a second, which is a poor reason to have been checking a fixture instead.

    The page count itself is a published figure -- two workflows and the README cite it -- so it
    is checked against the build rather than trusted, the same way ``tests/test_doc_counts.py``
    treats every number in the README.
    """

    def test_the_build_renders_the_finding_markup_the_fixture_never_reaches(self) -> None:
        """The reason this class exists, asserted rather than assumed.

        If the committed report ever stops carrying an implausible value, the pages below stop
        differing from the fixture in the way that motivated auditing them, and this should say
        so rather than going on quietly passing over a build that no longer proves anything.
        """
        report = json.loads((_DATA / "report.json").read_text(encoding="utf-8"))
        assert report["implausible"], (
            "data/report.json carries no implausible finding, so the published build no longer "
            "renders the findings markup that the fixture at the top of this module omits."
        )

    def test_every_page_of_the_published_site_is_audited(self, published: Path) -> None:
        """The count the prose cites, recomputed from the report the prose is about."""
        pages = sorted(published.rglob("index.html"))
        assert len(pages) > _EXPECTED_PAGES
        for stating in (
            _WORKFLOWS / "accessibility.yml",
            _WORKFLOWS / "pages.yml",
            _WORKFLOWS.parent.parent / "README.md",
        ):
            text = stating.read_text(encoding="utf-8")
            assert re.search(rf"\b{len(pages)}\b", text), (
                f"{stating.name} no longer states the page count as {len(pages)}. The committed "
                "report renders that many pages; prose explaining itself with another number is "
                "describing a build that does not exist."
            )

    def test_no_published_page_fetches_anything_but_itself(self, published: Path) -> None:
        budget = TestTheResourceBudget()
        offenders = {
            page.relative_to(published).as_posix(): found
            for page in sorted(published.rglob("index.html"))
            if (found := budget._requests(page))
        }
        assert not offenders, (
            f"the published site requests these: {offenders}. There are no scripts, no "
            "external stylesheets, no fonts, no images and no third-party requests, and the "
            "README says adding one is a build failure, not a decision nobody noticed."
        )


class TestTheTransferSizeBudget:
    """The ``resourceSizes`` half of ``lighthouse-budget.json``, enforced from the built bytes.

    The counts half moved into :class:`TestTheResourceBudget` when the budget file turned out to
    be enforced by nothing. The size half did not move with it, and the metrics ledger has said
    so in as many words ever since: "Resource transfer sizes and timings ... **nothing** ...
    Gate: NONE". A budget line nothing reads is the same defect as a badge nothing can turn red,
    and this project had already found that one in its own repository once.

    What this can honestly prove, and what it cannot, because the two are different numbers.
    Lighthouse's ``transferSize`` is what crossed the wire: the response body after any
    content-encoding, plus the response headers. This reads the bytes the generator wrote to
    disk. Measured on 2026-08-27 against ``lighthouse@12`` (12.8.2) serving the built site over
    ``python -m http.server``, which compresses nothing, the state/CA page was 67,061 bytes on
    disk and 67,250 bytes of ``transferSize`` -- the difference is the header block. Under any
    server that does compress, which is every server this site would be published from, the
    on-disk figure is far the larger of the two. So this check is stricter than the wire in the
    normal case and looser by a few hundred bytes in the pathological one, and it is stated that
    way rather than described as the same measurement. What it holds to the budget is the size of
    the document this project generates, which is the thing this project controls.

    The zero-budgeted non-document lines are enforced through the same fact the counts rest on:
    a page that fetches nothing transfers nothing of any type but its own document. A page that
    does fetch something has a weight that is not on disk, and :meth:`over_budget` reports that
    as a broken line rather than returning a zero it made up.
    """

    def over_budget(self, page: Path) -> list[str]:
        """Every ``resourceSizes`` line this page breaks, in words. Empty means within budget."""
        budgets = _size_budgets()
        document = page.stat().st_size
        requests = TestTheResourceBudget()._requests(page)
        broken = [
            f"{resource_type} is {document} bytes against a budget of {budgets[resource_type]}"
            for resource_type in ("document", "total")
            if document > budgets[resource_type]
        ]
        if requests:
            broken.append(
                f"the page makes {len(requests)} request(s) whose transferred bytes are not on "
                f"disk ({requests}); every non-document line is budgeted at zero and the total "
                "line is the document plus these, so neither can be read off the built file"
            )
        return broken

    def test_no_page_of_the_fixture_build_exceeds_the_budget(self, built: Path) -> None:
        broken = {
            page.relative_to(built).as_posix(): found
            for page in _pages(built)
            if (found := self.over_budget(page))
        }
        assert not broken, f"pages over the lighthouse-budget.json resourceSizes lines: {broken}"

    def test_no_published_page_exceeds_the_budget(self, published: Path) -> None:
        """The same lines over the 617 pages ``pages.yml`` uploads, not a six-page fixture.

        The fixture's pages are a few kilobytes each and would stay inside an 80 KiB budget no
        matter what happened to the templates. The published state pages are the ones with a row
        per institution in them, and they are where a budget is a real constraint.
        """
        broken = {
            page.relative_to(published).as_posix(): found
            for page in sorted(published.rglob("index.html"))
            if (found := self.over_budget(page))
        }
        assert not broken, f"pages over the lighthouse-budget.json resourceSizes lines: {broken}"

    def test_the_readme_states_the_budget_the_file_carries(self) -> None:
        """A budget derived from a file is only a gate while the file cannot be quietly widened.

        Every number this project publishes is re-derived from the artifact it is a number about
        (``tests/test_doc_counts.py``), and the budget is now one of those numbers: raising the
        document line from 80 KiB to 800 would weaken the two tests above without changing a word
        anywhere a reader looks. So the README states the figure and this recomputes it, which
        makes widening the budget an edit to the prose that argues for it.
        """
        budgets = _size_budgets()
        prose = re.sub(r"\s+", " ", (_ROOT / "README.md").read_text(encoding="utf-8"))
        for resource_type in ("document", "total"):
            stated = f"{budgets[resource_type] // _KIB} KiB"
            assert stated in prose, (
                f"lighthouse-budget.json budgets the {resource_type} at {stated} and the README "
                "does not say so. The budget is a published figure now, and a figure the prose "
                "does not carry is one that can be widened without anybody arguing for it."
            )

    def test_the_readme_states_the_largest_published_page(self, published: Path) -> None:
        """The headroom, as a number, so "within budget" is not doing unexamined work.

        A gate with a hundredfold of slack passes for the same reason a gate that cannot fail
        does. This states how close the largest real page actually is, recomputed from the
        committed report, so the day a template change eats the headroom the prose says it.
        """
        largest = max(sorted(published.rglob("index.html")), key=lambda page: page.stat().st_size)
        prose = re.sub(r"\s+", " ", (_ROOT / "README.md").read_text(encoding="utf-8"))
        stated = f"{largest.stat().st_size / _KIB:.1f} KiB"
        assert stated in prose, (
            f"the largest page the committed report renders is {largest.name} at {stated}, and "
            "the README states another figure. Bring the prose with the build, in the commit "
            "that moved it."
        )

    def test_a_page_over_the_document_budget_is_reported(self, tmp_path: Path) -> None:
        """The check can fail, asserted rather than hoped.

        Every assertion above is "this set is empty", which is what a check that reads nothing
        also reports. This is the same argument ``_pages`` makes about vacuous loops, one level
        down: the reason to believe the two tests above are gates is that this one watches the
        function say no.
        """
        budget = _size_budgets()["document"]
        page = tmp_path / "index.html"
        page.write_bytes(b"x" * (budget + 1))
        assert self.over_budget(page) == [
            f"document is {budget + 1} bytes against a budget of {budget}",
            f"total is {budget + 1} bytes against a budget of {budget}",
        ]

    def test_a_page_exactly_at_the_budget_is_within_it(self, tmp_path: Path) -> None:
        """A budget is a ceiling, not a limit one byte below itself."""
        page = tmp_path / "index.html"
        page.write_bytes(b"x" * _size_budgets()["document"])
        assert self.over_budget(page) == []

    def test_a_page_that_fetches_something_is_not_weighed_as_if_it_had_not(
        self, tmp_path: Path
    ) -> None:
        """The zero lines are zero because nothing is fetched, not because nothing was checked."""
        page = tmp_path / "index.html"
        page.write_text('<html><body><img src="/logo.png"></body></html>', encoding="utf-8")
        broken = self.over_budget(page)
        assert len(broken) == 1, broken
        assert "not on disk" in broken[0]
        assert "<img>" in broken[0]


class TestEveryBudgetLineIsAccountedFor:
    """No line of ``lighthouse-budget.json`` is enforced by nobody without the file saying so.

    The reason this class exists rather than a comment: the budget file spent months being cited
    in the README, in ``.github/workflows/accessibility.yml`` and in this project's own metrics
    ledger as though every line of it were a gate, while Lighthouse enforced none of them and
    nothing anywhere would have noticed. That was fixed one line at a time -- the counts, then
    the sizes -- and fixing instances is not the same as closing the class. A seventh
    ``resourceSizes`` line, or a fourth timing, added by somebody who assumed the file was wired
    up, would be exactly the original defect again.

    So every line has to be in one of two registers: enforced by a check named here, or declared
    unenforceable by a static checker with the reason written out. Both directions are checked,
    because a register naming a line the file no longer carries is a claim about nothing.
    """

    # The check that actually holds each line, named the way a reader can go and read it.
    _SIZES = "TestTheTransferSizeBudget.over_budget"
    _COUNTS = "TestTheResourceBudget.test_no_page_fetches_anything_but_itself"

    _ENFORCED_BY: ClassVar[dict[tuple[str, str], str]] = {
        ("resourceSizes", "document"): _SIZES,
        ("resourceSizes", "total"): _SIZES,
        ("resourceSizes", "script"): _SIZES,
        ("resourceSizes", "stylesheet"): _SIZES,
        ("resourceSizes", "font"): _SIZES,
        ("resourceSizes", "image"): _SIZES,
        ("resourceSizes", "third-party"): _SIZES,
        ("resourceCounts", "script"): _COUNTS,
        ("resourceCounts", "stylesheet"): _COUNTS,
        ("resourceCounts", "font"): _COUNTS,
        ("resourceCounts", "image"): _COUNTS,
        ("resourceCounts", "third-party"): _COUNTS,
        ("resourceCounts", "total"): _COUNTS,
    }

    # Lines no static checker can hold, each with the reason and the measurement behind it. These
    # are three timings, and the honest answer is that they need a rendering engine: layout shift
    # and blocking time are facts about a browser's main thread, and a paint time is a fact about
    # a machine and a network as much as about a document. Measured on 2026-08-27 with
    # `lighthouse@12` (12.8.2, simulated throttling, mobile form factor) against the built site
    # served locally: the home page reported LCP 752 ms, CLS 0, TBT 0, and the largest page in the
    # site, state/CA, reported LCP 1052 ms, CLS 0, TBT 0. Both are inside the 1500 ms line, and
    # neither is a runner: `accessibility.yml` runs on ubuntu-latest with a 4x CPU slowdown
    # applied to a machine this project has never measured, and 1052 of 1500 is not the headroom
    # a gate wants to be calibrated on somebody's laptop. `docs/adr/0008` records the decision to
    # measure the runner before gating rather than the other way round, which is the rule
    # `docs/adr/0007` had just finished writing down for a different question.
    _NOT_STATICALLY_ENFORCEABLE: ClassVar[dict[tuple[str, str], str]] = {
        ("timings", "largest-contentful-paint"): (
            "a paint time needs a rendering engine, and the number depends on the machine and "
            "the simulated network as much as on the document; the static proxy is the document "
            "size, which TestTheTransferSizeBudget holds to the resourceSizes line"
        ),
        ("timings", "cumulative-layout-shift"): (
            "layout shift is a fact about what a browser did while painting; that this site "
            "fetches nothing makes a shift unlikely, which is an argument and not a measurement"
        ),
        ("timings", "total-blocking-time"): (
            "blocking time is main-thread time in a browser; the published build ships no "
            "script, which is enforced as a count and is not the same claim as a measured zero"
        ),
    }

    def _lines(self) -> set[tuple[str, str]]:
        """Every budget line in the file, as (section, name)."""
        entry = _budget()
        lines = {
            (section, line["resourceType"])
            for section in ("resourceSizes", "resourceCounts")
            for line in entry.get(section, [])
        }
        return lines | {("timings", line["metric"]) for line in entry.get("timings", [])}

    def test_every_line_of_the_budget_file_is_in_one_of_the_two_registers(self) -> None:
        registered = set(self._ENFORCED_BY) | set(self._NOT_STATICALLY_ENFORCEABLE)
        unaccounted = self._lines() - registered
        assert not unaccounted, (
            f"lighthouse-budget.json carries {sorted(unaccounted)}, which no check in this suite "
            "claims and which nothing declares unenforceable. That is the state the whole file "
            "was in until it was found: a budget nothing reads, cited in three places as a gate. "
            "Add the line to _ENFORCED_BY with the check that holds it, or to "
            "_NOT_STATICALLY_ENFORCEABLE with the reason it cannot be held."
        )

    def test_neither_register_names_a_line_the_file_does_not_carry(self) -> None:
        """A register entry for a deleted line is a claim about a budget nobody set."""
        lines = self._lines()
        for register, name in (
            (self._ENFORCED_BY, "_ENFORCED_BY"),
            (self._NOT_STATICALLY_ENFORCEABLE, "_NOT_STATICALLY_ENFORCEABLE"),
        ):
            stale = set(register) - lines
            assert not stale, (
                f"{name} names {sorted(stale)}, which lighthouse-budget.json no longer carries"
            )

    def test_no_line_is_both_enforced_and_declared_unenforceable(self) -> None:
        both = set(self._ENFORCED_BY) & set(self._NOT_STATICALLY_ENFORCEABLE)
        assert not both, f"{sorted(both)} is registered as enforced and as unenforceable"

    def test_every_unenforceable_line_carries_a_reason_and_not_a_shrug(self) -> None:
        """A shrug is not a reason: the sentence has to say what a static checker cannot see."""
        for line, reason in self._NOT_STATICALLY_ENFORCEABLE.items():
            assert len(reason) > 60, f"{line} declares itself unenforceable without saying why"

    def test_the_ledger_still_names_the_lines_nothing_enforces(self) -> None:
        """The declaration is only honest while the documents a reader reads carry it too.

        ``docs/ROADMAP.md`` is where this project states its gates as AUTO, REVIEW or NONE, and
        the timing row is the one that is still NONE. If that row disappears while these three
        lines are still enforced by nothing, the ledger has started overstating the project, and
        overstating a gate is the defect that produced this whole class.
        """
        ledger = re.sub(r"\s+", " ", (_ROOT / "docs" / "ROADMAP.md").read_text(encoding="utf-8"))
        for _, metric in self._NOT_STATICALLY_ENFORCEABLE:
            assert metric in ledger, (
                f"the metrics ledger no longer names {metric}, which is enforced by nothing. A "
                "gate this project does not have has to be visible in the file that lists the "
                "gates it does."
            )
