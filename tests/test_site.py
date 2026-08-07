"""The rendered page, which is where the null-versus-zero error would finally reach the public.

Every other module can get this wrong privately. The site is the only place where a confusion
between "did not report" and "reported zero" becomes a sentence a person reads and believes, so
these tests are mostly about what must never appear on a page.
"""

from __future__ import annotations

import html.parser
import json
from pathlib import Path
from typing import Any

import pytest

from disclosed import cli, site
from disclosed.fields import ALL_FIELDS, FIELDS
from disclosed.peers import MIN_PEERS

_REPORT: dict[str, Any] = {
    "scope": {
        "kind": "sample",
        "source": "College Scorecard",
        "institutions": 3,
        "states": 1,
        "universe": 6300,
        "coverage": 3 / 6300,
        "note": "The first records the API returned, which arrive grouped by state.",
    },
    "institutions": 3,
    "ungradeable": 1,
    "overall": {
        "label": "all institutions",
        "graded": 2,
        "ungradeable": 1,
        "mean_score": 0.5,
        "worst_fields": [["Admission rate", 2], ["In-state tuition", 1]],
    },
    "by_state": [
        {
            "label": "CA",
            "graded": 2,
            "ungradeable": 1,
            "mean_score": 0.5,
            "worst_fields": [["Admission rate", 2]],
        }
    ],
    "implausible": [
        {
            "unit_id": "2",
            "name": "Zero College",
            "state": "CA",
            "field": "Admission rate",
            "value": 0,
            "rationale": "An exact zero would mean the institution admitted no applicant at all.",
            "peers": {
                "group": "public associate-predominant institutions in CA",
                "size": 78,
                "reporting": 77,
                "publishing_same_value": 0,
                "median": 1270.0,
                "verdict": "0 of 77 comparable institutions publish this value",
            },
        }
    ],
    "grades": [
        {
            "unit_id": "1",
            "name": "Complete College",
            "state": "CA",
            "score": 1.0,
            "letter": "A",
            "fields": {f.label: "reported" for f in FIELDS},
        },
        {
            "unit_id": "2",
            "name": "Zero College",
            "state": "CA",
            "score": 0.0,
            "letter": "F",
            "fields": {f.label: "missing" for f in FIELDS},
        },
        {
            "unit_id": "3",
            "name": "Suppressed College",
            "state": "CA",
            "score": None,
            "letter": None,
            "fields": {f.label: "suppressed" for f in FIELDS},
        },
    ],
}


def _build(tmp_path: Path, report: dict[str, Any] | None = None) -> Path:
    out = tmp_path / "site"
    site.build(report or _REPORT, out, origin="https://example.test", generated="2026-08-05")
    return out


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestAbsenceIsNeverRenderedAsAValue:
    def test_ungradeable_says_so_and_never_shows_a_zero_or_an_f(self, tmp_path: Path) -> None:
        """The invariant the whole project exists for, at the only point the public sees it."""
        page = _text(_build(tmp_path) / "institution" / "3" / "index.html")
        assert "not gradeable" in page
        assert ">0%<" not in page
        # Matched on the badge markup, not the substring: the stylesheet defines .grade-f on
        # every page, so a bare "grade-f" check passes for the wrong reason.
        assert 'class="grade grade-f"' not in page
        assert 'class="grade grade-none"' in page

    def test_a_real_zero_still_renders_as_zero(self, tmp_path: Path) -> None:
        """The mirror invariant. An institution that was gradeable and reported nothing earned a
        0%, and softening that to "not gradeable" would be the same lie in the other direction."""
        page = _text(_build(tmp_path) / "institution" / "2" / "index.html")
        assert ">0%<" in page
        assert "not gradeable" not in page

    def test_an_unnamed_institution_is_not_called_none(self, tmp_path: Path) -> None:
        report = json.loads(json.dumps(_REPORT))
        report["grades"][0]["name"] = None
        page = _text(_build(tmp_path, report) / "state" / "CA" / "index.html")
        assert ">None<" not in page
        assert "None</a>" not in page
        assert "Unnamed institution (unit id 1)" in page

    def test_an_institution_with_no_id_is_listed_but_given_no_page(self, tmp_path: Path) -> None:
        """A URL invented from a name or a row number silently points at a different school the
        next time the corpus changes, and a citable page that changes subject is worse than none."""
        report = json.loads(json.dumps(_REPORT))
        report["grades"][0]["unit_id"] = None
        report["grades"][0]["name"] = None
        out = _build(tmp_path, report)
        assert "no unit id published" in _text(out / "state" / "CA" / "index.html")
        assert not (out / "institution" / "None").exists()
        assert not (out / "institution" / "1").exists()

    def test_no_page_anywhere_contains_the_bare_word_none_as_content(self, tmp_path: Path) -> None:
        out = _build(tmp_path)
        for page in out.rglob("index.html"):
            body = _text(page)
            assert ">None<" not in body, page
            assert ">nan<" not in body, page


class TestFindingsLinkToTheirReasoning:
    def test_every_field_row_links_to_a_rationale_anchor(self, tmp_path: Path) -> None:
        page = _text(_build(tmp_path) / "institution" / "1" / "index.html")
        for field in FIELDS:
            assert f"methodology/#{field.anchor}" in page

    def test_every_anchor_linked_to_actually_exists_on_the_methodology_page(
        self, tmp_path: Path
    ) -> None:
        """A link into a rationale that is not there is worse than no link: it looks answered.

        Asserted over ALL_FIELDS, not just the Scorecard set. `_rationale_link` resolves labels
        against every field this project knows about, so an IPEDS label appearing in any report
        would have produced a link to an anchor the methodology page did not render.
        """
        out = _build(tmp_path)
        methodology = _text(out / "methodology" / "index.html")
        for field in ALL_FIELDS:
            assert f'id="{field.anchor}"' in methodology, field.label

    def test_anchors_are_unique_so_a_link_lands_somewhere_definite(self) -> None:
        anchors = [f.anchor for f in ALL_FIELDS]
        assert len(anchors) == len(set(anchors))

    def test_a_url_field_states_its_applicability_instead_of_a_credible_range(
        self, tmp_path: Path
    ) -> None:
        """A URL column has no numeric range to quote; what a reader needs is who it applies to."""
        methodology = _text(_build(tmp_path) / "methodology" / "index.html")
        assert "no page is ever fetched" in methodology
        assert "leave the denominator entirely" in methodology

    def test_an_implausible_value_carries_its_peer_verdict(self, tmp_path: Path) -> None:
        page = _text(_build(tmp_path) / "institution" / "2" / "index.html")
        assert "0 of 77 comparable institutions publish this value" in page
        assert "public associate-predominant institutions in CA" in page

    def test_a_finding_without_peers_says_no_comparison_is_claimed(self, tmp_path: Path) -> None:
        report = json.loads(json.dumps(_REPORT))
        del report["implausible"][0]["peers"]
        page = _text(_build(tmp_path, report) / "institution" / "2" / "index.html")
        assert "no comparison is claimed" in page

    def test_the_methodology_states_every_credible_range(self, tmp_path: Path) -> None:
        methodology = _text(_build(tmp_path) / "methodology" / "index.html")
        for field in FIELDS:
            assert field.rationale[:60] in methodology

    def test_the_methodology_states_the_rule_behind_a_withheld_peer_comparison(
        self, tmp_path: Path
    ) -> None:
        """ "Too few to draw a conclusion" is a judgement, and every judgement here is published.

        A reader who follows a finding's link expecting to find why no peer comparison was made
        used to find nothing: the page described the peer group in full and never mentioned that
        a claim is withheld below a threshold, or what the threshold was.
        """
        methodology = _text(_build(tmp_path) / "methodology" / "index.html")
        assert f"at least {MIN_PEERS} comparable institutions exist" in methodology
        assert f"at least {MIN_PEERS} of them published the field" in methodology


class TestDeterminism:
    def test_rebuilding_the_same_report_is_byte_identical(self, tmp_path: Path) -> None:
        """No clock anywhere in the build, so a diff in the output means the data changed."""
        first = _build(tmp_path / "a")
        second = _build(tmp_path / "b")
        for page in sorted(first.rglob("*")):
            if page.is_file():
                assert page.read_bytes() == (second / page.relative_to(first)).read_bytes()

    def test_no_network_import_at_build_time(self) -> None:
        """A site generator that reaches the network cannot be rebuilt from an archive."""
        source = Path(site.__file__).read_text(encoding="utf-8")
        for forbidden in ("urllib", "requests", "socket", "http.client"):
            assert forbidden not in source


class TestHostileInput:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("../../etc/passwd", "etc-passwd"), ("..", ""), ("", ""), ("100654", "100654")],
    )
    def test_slug_cannot_escape_the_output_directory(self, raw: str, expected: str) -> None:
        """Unit ids come from a third party and decide where a file lands."""
        assert site.slug(raw) == expected

    def test_a_name_containing_markup_is_escaped(self, tmp_path: Path) -> None:
        report = json.loads(json.dumps(_REPORT))
        report["grades"][0]["name"] = '<script>alert("x")</script> & Sons'
        page = _text(_build(tmp_path, report) / "institution" / "1" / "index.html")
        assert "<script>alert" not in page
        assert "&lt;script&gt;" in page
        assert "&amp; Sons" in page

    def test_a_traversing_unit_id_writes_inside_the_output_directory(self, tmp_path: Path) -> None:
        report = json.loads(json.dumps(_REPORT))
        report["grades"][0]["unit_id"] = "../../escaped"
        out = _build(tmp_path, report)
        assert not (tmp_path.parent / "escaped").exists()
        assert (out / "institution" / "escaped" / "index.html").exists()

    def test_an_unrecognized_classification_is_stated_not_guessed(self, tmp_path: Path) -> None:
        """A report from a newer version must not be silently reinterpreted as something known."""
        report = json.loads(json.dumps(_REPORT))
        report["grades"][0]["fields"]["Admission rate"] = "embargoed"
        page = _text(_build(tmp_path, report) / "institution" / "1" / "index.html")
        assert "unrecognized classification embargoed" in page


class TestStructure:
    def test_every_page_is_well_formed(self, tmp_path: Path) -> None:
        class Checker(html.parser.HTMLParser):
            void = frozenset({"meta", "link", "br", "hr", "img", "input"})

            def __init__(self) -> None:
                super().__init__()
                self.stack: list[str] = []
                self.errors: list[str] = []

            def handle_starttag(self, tag: str, attrs: Any) -> None:
                if tag not in self.void:
                    self.stack.append(tag)

            def handle_endtag(self, tag: str) -> None:
                if not self.stack or self.stack[-1] != tag:
                    self.errors.append(f"</{tag}>")
                else:
                    self.stack.pop()

        for page in _build(tmp_path).rglob("index.html"):
            checker = Checker()
            checker.feed(_text(page))
            assert not checker.errors, f"{page}: {checker.errors}"
            assert not checker.stack, f"{page}: unclosed {checker.stack}"

    def test_one_page_per_institution_state_plus_home_and_methodology(self, tmp_path: Path) -> None:
        pages = site.build(_REPORT, tmp_path / "s", origin="https://example.test", generated="x")
        paths = {p.path for p in pages}
        assert paths == {
            "",
            "methodology",
            "state/CA",
            "institution/1",
            "institution/2",
            "institution/3",
        }

    def test_sitemap_and_robots_are_written(self, tmp_path: Path) -> None:
        out = _build(tmp_path)
        assert "https://example.test/institution/1/" in _text(out / "sitemap.xml")
        assert "Sitemap: https://example.test/sitemap.xml" in _text(out / "robots.txt")

    def test_the_sample_caveat_names_its_own_limits(self, tmp_path: Path) -> None:
        """A project about undisclosed information should not be coy about its own coverage."""
        home = _text(_build(tmp_path) / "index.html")
        assert "are not national" in home
        assert "arrive grouped by state" in home

    def test_a_report_that_never_stated_its_coverage_is_not_promoted_to_national(
        self, tmp_path: Path
    ) -> None:
        """Older reports carry no scope. The page must say so rather than assume the best."""
        report = {k: v for k, v in _REPORT.items() if k != "scope"}
        site.build(report, tmp_path, generated="test")
        home = _text(tmp_path / "index.html")
        assert "does not say how much of the College Scorecard it holds" in home
        assert "nothing wider has been established" in home


class TestSiteCommand:
    def test_builds_from_a_report_on_disk(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        report = tmp_path / "report.json"
        report.write_text(json.dumps(_REPORT))
        out = tmp_path / "out"
        assert (
            cli.main(
                ["site", "--report", str(report), "--out", str(out), "--generated", "2026-08-05"]
            )
            == 0
        )
        assert (out / "index.html").exists()
        assert "built 6 pages" in capsys.readouterr().out

    def test_refuses_to_build_a_site_with_no_institutions_in_it(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """An empty site is a broken build, not a finding about higher education."""
        report = tmp_path / "report.json"
        report.write_text(json.dumps({"institutions": 0, "grades": []}))
        out = tmp_path / "out"
        assert cli.main(["site", "--report", str(report), "--out", str(out)]) == 1
        assert "refusing to build" in capsys.readouterr().err
        assert not out.exists()
