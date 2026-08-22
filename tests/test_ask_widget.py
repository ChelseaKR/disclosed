"""The opt-in question form: absent by default, inert until pressed, proven from the built bytes.

Two builds of the same report. Without an endpoint the site is what it always was and the
resource-budget suite in ``test_accessibility.py`` still holds it to zero scripts. With one,
every institution page carries the form and exactly one inline script, and this file proves
from the HTML and the script text that the page makes no request of its own accord: the script
has no ``src``, its only network call sits inside the submit handler, and nothing else on the
page fetches anything.
"""

from __future__ import annotations

import html.parser
import re
from pathlib import Path
from typing import Any

import pytest

from disclosed import site

_ENDPOINT = "https://ask.example.test/ask"

_REPORT: dict[str, Any] = {
    "scope": {
        "kind": "sample",
        "source": "College Scorecard",
        "institutions": 2,
        "states": 1,
        "universe": 6300,
        "note": "two institutions, for the widget",
    },
    "institutions": 2,
    "overall": {
        "label": "All",
        "graded": 2,
        "ungradeable": 0,
        "mean_score": 0.75,
        "worst_fields": [],
    },
    "by_state": [
        {"label": "AL", "graded": 2, "ungradeable": 0, "mean_score": 0.75, "worst_fields": []}
    ],
    "implausible": [],
    "ungradeable": 0,
    "grades": [
        {
            "unit_id": "100654",
            "name": "Alabama A & M University",
            "state": "AL",
            "score": 1.0,
            "letter": "A",
            "fields": {"Admission rate": "reported", "Enrollment": "reported"},
        },
        {
            "unit_id": "100663",
            "name": "University of Alabama at Birmingham",
            "state": "AL",
            "score": 0.5,
            "letter": "F",
            "fields": {"Admission rate": "missing", "Enrollment": "reported"},
        },
    ],
}


class _Tags(html.parser.HTMLParser):
    """Every start tag with its attributes, so a test can ask what the page would fetch."""

    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str]]] = []

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        self.tags.append((tag, {k: (v or "") for k, v in attrs}))


_FETCHING = {"img", "picture", "source", "video", "audio", "track", "iframe", "embed", "object"}


def _build(tmp_path: Path, *, endpoint: str | None) -> Path:
    out = tmp_path / ("with" if endpoint else "without")
    site.build(
        _REPORT, out, origin="https://example.test", generated="2026-08-05", ask_endpoint=endpoint
    )
    return out


def _institution_pages(root: Path) -> list[Path]:
    return sorted((root / "institution").glob("*/index.html"))


def _other_pages(root: Path) -> list[Path]:
    return [p for p in root.rglob("index.html") if "institution" not in p.parts]


def _tags(page: Path) -> list[tuple[str, dict[str, str]]]:
    parser = _Tags()
    parser.feed(page.read_text(encoding="utf-8"))
    return parser.tags


def _script(page: Path) -> str:
    scripts = re.findall(r"<script>(.*?)</script>", page.read_text(encoding="utf-8"), re.S)
    assert len(scripts) == 1, page
    return scripts[0]


class TestWithoutAnEndpoint:
    def test_the_build_is_byte_identical_to_a_build_that_never_heard_of_the_widget(
        self, tmp_path: Path
    ) -> None:
        without = _build(tmp_path, endpoint=None)
        plain = tmp_path / "plain"
        site.build(_REPORT, plain, origin="https://example.test", generated="2026-08-05")
        for page in sorted(plain.rglob("*")):
            if page.is_file():
                assert page.read_bytes() == (without / page.relative_to(plain)).read_bytes()

    def test_no_page_carries_a_script_or_the_form(self, tmp_path: Path) -> None:
        without = _build(tmp_path, endpoint=None)
        for page in without.rglob("index.html"):
            tags = {tag for tag, _ in _tags(page)}
            assert "script" not in tags and "form" not in tags, page


class TestWithAnEndpoint:
    def test_every_institution_page_has_the_form_and_exactly_one_inline_script(
        self, tmp_path: Path
    ) -> None:
        with_ = _build(tmp_path, endpoint=_ENDPOINT)
        pages = _institution_pages(with_)
        assert len(pages) == 2
        for page in pages:
            tags = _tags(page)
            scripts = [attrs for tag, attrs in tags if tag == "script"]
            assert len(scripts) == 1 and "src" not in scripts[0], page
            forms = [attrs for tag, attrs in tags if tag == "form"]
            assert len(forms) == 1
            assert forms[0]["data-endpoint"] == _ENDPOINT
            assert forms[0]["data-unit-id"] in {"100654", "100663"}

    def test_no_other_page_gains_a_script(self, tmp_path: Path) -> None:
        with_ = _build(tmp_path, endpoint=_ENDPOINT)
        others = _other_pages(with_)
        assert len(others) >= 3
        for page in others:
            assert "script" not in {tag for tag, _ in _tags(page)}, page

    def test_nothing_on_the_page_fetches_anything_at_load(self, tmp_path: Path) -> None:
        """The only fetching tag is the inline script, and a script without ``src`` fetches
        nothing by being parsed. Every other fetching tag and every fetching ``<link>`` is still
        absent, and ``srcset``/``background`` never appear."""
        with_ = _build(tmp_path, endpoint=_ENDPOINT)
        for page in _institution_pages(with_):
            for tag, attrs in _tags(page):
                assert tag not in _FETCHING, (page, tag)
                assert "srcset" not in attrs and "background" not in attrs
                if tag == "link":
                    assert attrs.get("rel") in {"canonical", "alternate"}, (page, attrs)
                if tag == "script":
                    assert "src" not in attrs

    def test_the_scripts_only_network_call_is_inside_the_submit_handler(
        self, tmp_path: Path
    ) -> None:
        with_ = _build(tmp_path, endpoint=_ENDPOINT)
        script = _script(_institution_pages(with_)[0])
        marker = 'addEventListener("submit"'
        assert script.count(marker) == 1
        before, after = script.split(marker)
        assert "fetch(" not in before
        assert after.count("fetch(") == 1
        for forbidden in (
            "XMLHttpRequest",
            "sendBeacon",
            "new Image",
            "import(",
            "WebSocket",
            "EventSource",
        ):
            assert forbidden not in script, forbidden
        assert "form.dataset.endpoint" in after
        assert _ENDPOINT not in script, "the endpoint lives on the form, not in the script"

    def test_the_script_renders_with_text_content_and_never_markup(self, tmp_path: Path) -> None:
        with_ = _build(tmp_path, endpoint=_ENDPOINT)
        script = _script(_institution_pages(with_)[0])
        assert "innerHTML" not in script and "outerHTML" not in script
        assert "insertAdjacentHTML" not in script and "document.write" not in script
        assert "textContent" in script

    def test_the_widget_says_what_it_is_before_the_reader_types(self, tmp_path: Path) -> None:
        with_ = _build(tmp_path, endpoint=_ENDPOINT)
        text = _institution_pages(with_)[0].read_text(encoding="utf-8")
        assert "Nothing is sent anywhere until you press Ask" in text
        assert "AI-generated" in text and "unofficial" in text
        assert "never how it performs" in text
        assert "Questions about quality, rankings or whether to attend are refused" in text

    def test_the_form_is_labelled_and_the_answer_region_is_live(self, tmp_path: Path) -> None:
        with_ = _build(tmp_path, endpoint=_ENDPOINT)
        tags = _tags(_institution_pages(with_)[0])
        labels = [attrs for tag, attrs in tags if tag == "label"]
        inputs = [attrs for tag, attrs in tags if tag == "input"]
        assert labels[0]["for"] == inputs[0]["id"] == "ask-question"
        assert inputs[0]["maxlength"] == "600"
        answer = next(attrs for tag, attrs in tags if attrs.get("id") == "ask-answer")
        assert answer["aria-live"] == "polite"
        section = next(
            attrs for tag, attrs in tags if tag == "section" and "ask" in attrs.get("class", "")
        )
        assert section["aria-labelledby"] == "ask-heading"

    def test_an_endpoint_with_markup_is_escaped(self, tmp_path: Path) -> None:
        with_ = _build(tmp_path, endpoint='https://x.test/?a="<b>"')
        text = _institution_pages(with_)[0].read_text(encoding="utf-8")
        assert "<b>" not in text.split("ask-form")[1].split(">")[0]
        assert "&lt;b&gt;" in text

    def test_a_failed_request_leaves_the_page_unchanged_by_design(self, tmp_path: Path) -> None:
        with_ = _build(tmp_path, endpoint=_ENDPOINT)
        script = _script(_institution_pages(with_)[0])
        assert "This page is unchanged" in script
        assert ".catch(" in script


class TestTheSiteCommand:
    def test_ask_endpoint_threads_through_the_cli(self, tmp_path: Path) -> None:
        import json

        from disclosed.cli import main

        report = tmp_path / "report.json"
        report.write_text(json.dumps(_REPORT), encoding="utf-8")
        out = tmp_path / "site"
        assert (
            main(
                [
                    "site",
                    "--report",
                    str(report),
                    "--out",
                    str(out),
                    "--generated",
                    "2026-08-05",
                    "--ask-endpoint",
                    _ENDPOINT,
                ]
            )
            == 0
        )
        assert all("ask-form" in p.read_text("utf-8") for p in _institution_pages(out))
        plain = tmp_path / "plain"
        assert (
            main(
                ["site", "--report", str(report), "--out", str(plain), "--generated", "2026-08-05"]
            )
            == 0
        )
        assert not any("script" in {t for t, _ in _tags(p)} for p in plain.rglob("index.html"))


@pytest.mark.parametrize("colour", ["#555", "#a8421f", "#bbb", "#ffab7a", "#e3e3e3", "#333"])
def test_the_widget_uses_only_colours_the_contrast_suite_already_checks(colour: str) -> None:
    """The stylesheet's colour list is pinned in test_accessibility.py; the widget adds rules,
    not colours, so that suite keeps covering every pair on the page."""
    assert colour in site._STYLE
