"""The message catalog, and the line the five classifications are not allowed to cross.

The load-bearing test in this module is :class:`TestTheExportKeepsMachineKeys`. Everything else
here is about a catalog being complete and consistent; that class is about the product.

``reported``, ``implausible``, ``suppressed``, ``not_applicable`` and ``missing`` are this
project's contract. They are five different facts, they are what the CSV export and its Table
Schema publish, and a consumer joins on them. Translating them in the data would keep the file
looking correct while quietly making it a different dataset -- a Spanish export whose cells said
``no_reportado`` would not merge with an English one, and nothing about the file would say so.
They are translated at the presentation layer and nowhere else.

Proving that needs a locale that actually translates them, and this repository has exactly one
catalog, in English, where "translated" and "untranslated" look the same. So these tests build a
**pseudolocale**: a complete catalog whose every message is wrapped in brackets. It is not a
language and is never shipped; it is the negative control, and it is checked to have actually
taken effect (``test_the_pseudolocale_really_does_translate_the_labels``) before the export is
asserted to be unmoved by it. A sabotage that silently fails to apply reads as a pass.
"""

from __future__ import annotations

import html
import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from disclosed import dataset, messages, site
from disclosed.disclosure import CLASSIFICATIONS, Disclosure
from disclosed.fields import FIELDS
from disclosed.messages import Catalog, CatalogError

_ROOT = Path(__file__).resolve().parents[1]
_SOURCE = _ROOT / "src" / "disclosed"

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
            "fields": {
                "Admission rate": "missing",
                "Enrollment": "reported",
                "In-state tuition": "suppressed",
                "Median debt at completion": "not_applicable",
                "Completion rate, 150% of normal time": "implausible",
            },
        },
        {
            "unit_id": "2",
            "name": "Ungradeable College",
            "state": "CA",
            "score": None,
            "letter": None,
            "fields": {"Admission rate": "suppressed"},
        },
    ],
}


def _quoted(message: str) -> str:
    """One catalog message as a ``.po`` string body."""
    return message.replace("\\", "\\\\").replace('"', '\\"')


def _pseudolocale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, locale: str = "qq"
) -> Catalog:
    """A complete catalog in which every message is visibly not English.

    Built from the English one so it can never fall behind it, and written to a real ``.po`` file
    parsed by the real loader, so the completeness and placeholder checks run over it exactly as
    they would over a translation somebody submitted. The wrapper goes around the whole message
    and leaves ``{placeholders}`` alone, because a placeholder that did not survive would fail the
    loader rather than the test it is here to support.

    ``locale`` is ``qq`` by default, a code no language uses. Passing ``en`` installs the
    pseudolocale as the source catalog instead, which is how a test proves that some piece of
    code does not read the catalog at all rather than merely that it did not read *this* one.
    """
    source = messages.load()
    lines = [
        'msgid ""',
        'msgstr ""',
        f'"Language: {locale}\\n"',
        '"MIME-Version: 1.0\\n"',
        '"Content-Type: text/plain; charset=UTF-8\\n"',
        '"Plural-Forms: nplurals=2; plural=(n != 1);\\n"',
        f'"X-OG-Locale: {locale}_QQ\\n"',
        "",
    ]

    def wrapped(message: str) -> str:
        return "[[" + _quoted(message) + "]]"

    for key, message in sorted(source.singular.items()):
        lines += [f'msgid "{key}"', f'msgstr "{wrapped(message)}"', ""]
    for key, forms in sorted(source.plural.items()):
        lines += [f'msgid "{key}"', f'msgid_plural "{key}"']
        lines += [f'msgstr[{n}] "{wrapped(form)}"' for n, form in enumerate(forms)]
        lines += [""]

    catalogs = tmp_path / "locales"
    written = catalogs / locale / "LC_MESSAGES"
    written.mkdir(parents=True)
    (written / "disclosed.po").write_text("\n".join(lines), encoding="utf-8")
    if locale != messages.SOURCE_LOCALE:
        # The English catalog stays reachable: every other locale is checked against it.
        (catalogs / messages.SOURCE_LOCALE).symlink_to(messages._LOCALES / messages.SOURCE_LOCALE)
    monkeypatch.setattr(messages, "_LOCALES", catalogs)
    messages.load.cache_clear()
    return messages.load(locale)


@pytest.fixture
def pseudolocale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Catalog]:
    catalog = _pseudolocale(tmp_path / "catalogs", monkeypatch)
    yield catalog
    messages.load.cache_clear()


class TestTheExportKeepsMachineKeys:
    """The CSV export and its schema are data, and data is not translated.

    This is the constraint ``docs/I18N.md`` recorded and issue #64 turns on. If one of these
    fails, the export has started carrying prose where it carried keys, and every consumer that
    joins on a classification is broken by a change nobody would see in a page.
    """

    def test_the_pseudolocale_really_does_translate_the_labels(
        self, pseudolocale: Catalog, tmp_path: Path
    ) -> None:
        """The negative control, controlled.

        Every assertion below is "the export did not change". So is the result of a pseudolocale
        that failed to apply. This asserts the sabotage landed: under ``qq`` the rendered page
        says something English does not.
        """
        out = tmp_path / "site"
        site.build(_REPORT, out, generated="2026-09-05", locale="qq")
        page = (out / "institution" / "1" / "index.html").read_text(encoding="utf-8")

        for disclosure in Disclosure:
            label, _ = site._classification_copy(disclosure, pseudolocale)
            assert label.startswith("[["), label
        assert "[[Not reported]]" in page
        assert "[[Suppressed]]" in page
        assert '<html lang="qq">' in page

    def test_the_csv_carries_the_five_tokens_and_not_their_translations(
        self, pseudolocale: Catalog
    ) -> None:
        csv = dataset.to_csv(_REPORT)
        cells = {cell for line in csv.splitlines()[1:] for cell in line.split(",")}

        for classification in CLASSIFICATIONS:
            assert classification in cells, (
                f"{classification!r} is missing from the export. The five classifications are "
                "machine keys and every one of them has to survive as itself."
            )
        for classification in CLASSIFICATIONS:
            translated = pseudolocale.text(f"classification.{classification}.label")
            assert translated not in csv, (
                f"the CSV export carries {translated!r}. A classification is data: translating "
                "it in the export makes a file that no longer joins to any other run of this "
                "project, and nothing in the file says so. Translate it in "
                "disclosed.site._classification_copy, which is the presentation layer, and "
                "nowhere else."
            )
        assert "[[" not in csv, "the export carries catalog prose"

    def test_the_table_schema_enumerates_the_tokens_untranslated(
        self, pseudolocale: Catalog
    ) -> None:
        schema = dataset.to_schema_json()

        for classification in CLASSIFICATIONS:
            assert f'"{classification}"' in schema
            assert pseudolocale.text(f"classification.{classification}.label") not in schema
        assert "[[" not in schema

    def test_the_export_is_byte_identical_under_a_translated_locale(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Not just the tokens: the whole file, its header row and its schema.

        The pseudolocale is installed here as ``en`` itself, so that a catalog lookup anywhere
        under :mod:`disclosed.dataset` comes back bracketed however the module reached it --
        through :mod:`disclosed.messages`, through an imported ``load``, or through a default
        argument bound at import. The export has to be the same bytes either way.
        """
        english_csv = dataset.to_csv(_REPORT)
        english_schema = dataset.to_schema_json()
        _pseudolocale(tmp_path / "catalogs", monkeypatch, locale=messages.SOURCE_LOCALE)

        assert dataset.to_csv(_REPORT) == english_csv
        assert dataset.to_schema_json() == english_schema

    def test_the_export_module_does_not_reach_the_catalog(self) -> None:
        """The structural half of the same rule, so it fails at review rather than at runtime.

        A future edit that translated a column description would pass every assertion above and
        still be the beginning of this defect. ``disclosed.dataset`` names no catalog at all.
        """
        source = (_SOURCE / "dataset.py").read_text(encoding="utf-8")

        assert "messages" not in source, (
            "disclosed.dataset imports the message catalog. The CSV export is data and has no "
            "presentation layer; anything it renders through a catalog is a value that stops "
            "meaning the same thing in another language."
        )
        assert "Catalog" not in source


class TestTheCatalogCoversTheSite:
    def test_every_classification_has_a_label_and_a_meaning(self) -> None:
        catalog = messages.load()

        for classification in CLASSIFICATIONS:
            assert catalog.text(f"classification.{classification}.label")
            assert catalog.text(f"classification.{classification}.meaning")

    def test_every_letter_band_has_a_summary(self) -> None:
        catalog = messages.load()

        for letter in site._LETTERS:
            assert catalog.text(f"letter.{letter}.summary")
        assert catalog.text("letter.none")

    def test_no_page_of_a_built_site_is_missing_a_message(self, tmp_path: Path) -> None:
        """A key the catalog does not carry raises; this is the whole site asserting it does not.

        ``Catalog.text`` refuses an unknown key rather than rendering an empty string, so a
        template that outgrew its catalog cannot publish a blank where a sentence was.
        """
        pages = site.build(_REPORT, tmp_path, generated="2026-09-05")

        assert len(pages) > 1
        for page in tmp_path.rglob("index.html"):
            text = page.read_text(encoding="utf-8")
            assert "{" not in re.sub(r"<style>.*?</style>", "", text, flags=re.DOTALL), page

    def test_the_source_catalog_satisfies_its_own_translation_rules(self) -> None:
        """The checks a translation has to pass, run against the catalog they are defined by.

        A rule the source catalog itself could not satisfy would reject every honest translation,
        and nothing would notice until somebody wrote one.
        """
        catalog = messages.load()

        messages._check_against_source(catalog, catalog)

    def test_english_is_the_only_catalog_and_the_readme_does_not_claim_otherwise(self) -> None:
        """Stated as a fact rather than left implied.

        The seam exists; the translations do not. If a locale is added, this test is where the
        claim gets updated, in the same commit that adds the catalog.
        """
        assert messages.available() == ("en",)


class TestTheCatalogRefusesWhatItCannotTrust:
    """Every refusal, exercised. A loader that cannot say no is a fallback with extra steps."""

    def _write(self, tmp_path: Path, locale: str, body: str) -> Path:
        target = tmp_path / locale / "LC_MESSAGES"
        target.mkdir(parents=True)
        (target / "disclosed.po").write_text(body, encoding="utf-8")
        return tmp_path

    def _load(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, locale: str) -> Catalog:
        monkeypatch.setattr(messages, "_LOCALES", tmp_path)
        messages.load.cache_clear()
        try:
            return messages.load(locale)
        finally:
            messages.load.cache_clear()

    _HEADER = (
        'msgid ""\nmsgstr ""\n'
        '"Language: en\\n"\n'
        '"Plural-Forms: nplurals=2; plural=(n != 1);\\n"\n\n'
    )

    def test_a_locale_name_that_is_a_path_is_refused(self) -> None:
        with pytest.raises(CatalogError, match="not a locale name"):
            messages.load("../../etc")

    def test_a_locale_with_no_catalog_is_refused_and_says_what_there_is(self) -> None:
        with pytest.raises(CatalogError, match="available: en"):
            messages.load("zz")

    def test_an_untranslated_message_is_refused_rather_than_left_in_english(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._write(tmp_path, "en", self._HEADER + 'msgid "a.key"\nmsgstr ""\n')

        with pytest.raises(CatalogError, match="untranslated"):
            self._load(tmp_path, monkeypatch, "en")

    def test_a_duplicated_key_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        body = self._HEADER + 'msgid "a.key"\nmsgstr "one"\n\nmsgid "a.key"\nmsgstr "two"\n'
        self._write(tmp_path, "en", body)

        with pytest.raises(CatalogError, match="appears twice"):
            self._load(tmp_path, monkeypatch, "en")

    def test_a_plural_rule_this_project_does_not_know_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        body = (
            'msgid ""\nmsgstr ""\n"Language: cy\\n"\n'
            '"Plural-Forms: nplurals=6; plural=(n==0) ? 0 : 5;\\n"\n\n'
            'msgid "a.key"\nmsgstr "un"\n'
        )
        self._write(tmp_path, "cy", body)

        with pytest.raises(CatalogError, match="not a rule this project knows"):
            self._load(tmp_path, monkeypatch, "cy")

    def test_a_header_that_is_not_a_header_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._write(tmp_path, "en", 'msgid ""\nmsgstr "not a field"\n')

        with pytest.raises(CatalogError, match="not a header field"):
            self._load(tmp_path, monkeypatch, "en")

    def test_a_catalog_with_no_header_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._write(tmp_path, "en", 'msgid "a.key"\nmsgstr "one"\n')

        with pytest.raises(CatalogError, match="no header entry"):
            self._load(tmp_path, monkeypatch, "en")

    def test_a_plural_entry_with_one_form_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        body = self._HEADER + 'msgid "a.key"\nmsgid_plural "a.key"\nmsgstr[0] "one"\n'
        self._write(tmp_path, "en", body)

        with pytest.raises(CatalogError, match="carries 1 plural forms"):
            self._load(tmp_path, monkeypatch, "en")

    def test_plural_forms_on_a_singular_entry_are_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        body = self._HEADER + 'msgid "a.key"\nmsgstr[0] "one"\nmsgstr[1] "two"\n'
        self._write(tmp_path, "en", body)

        with pytest.raises(CatalogError, match="no msgid_plural"):
            self._load(tmp_path, monkeypatch, "en")

    def test_an_unknown_key_raises_rather_than_rendering_nothing(self) -> None:
        catalog = messages.load()

        with pytest.raises(CatalogError, match="no message"):
            catalog.text("nothing.is.here")
        with pytest.raises(CatalogError, match="no plural message"):
            catalog.count("nothing.is.here", 2)


class TestTranslationsAreCheckedAgainstTheSource:
    """What a submitted translation has to survive before a page is rendered from it."""

    def _catalogs(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str) -> None:
        target = tmp_path / "xx" / "LC_MESSAGES"
        target.mkdir(parents=True)
        (target / "disclosed.po").write_text(body, encoding="utf-8")
        (tmp_path / "en").symlink_to(messages._LOCALES / "en")
        monkeypatch.setattr(messages, "_LOCALES", tmp_path)
        messages.load.cache_clear()

    _HEADER = (
        'msgid ""\nmsgstr ""\n'
        '"Language: xx\\n"\n'
        '"Plural-Forms: nplurals=2; plural=(n != 1);\\n"\n\n'
    )

    def test_a_partial_translation_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._catalogs(
            tmp_path, monkeypatch, self._HEADER + 'msgid "nav.all_institutions"\nmsgstr "Todas"\n'
        )
        try:
            with pytest.raises(CatalogError, match="A partly translated locale is not offered"):
                messages.load("xx")
        finally:
            messages.load.cache_clear()

    def _complete(
        self,
        *,
        replace: dict[str, str] | None = None,
        extra: dict[str, str] | None = None,
        demote: str | None = None,
    ) -> str:
        """A translation covering every message, optionally spoiled in one specific way.

        Complete by construction, so each test below fails on the one rule it is about rather
        than on the coverage check that would otherwise catch everything first.
        """
        source = messages.load()
        replace = replace or {}
        lines = [self._HEADER.rstrip("\n")]
        for key, message in sorted(source.singular.items()):
            lines += ["", f'msgid "{key}"', f'msgstr "{_quoted(replace.get(key, message))}"']
        for key, forms in sorted(source.plural.items()):
            lines += ["", f'msgid "{key}"']
            if key == demote:
                lines += [f'msgstr "{_quoted(forms[1])}"']
                continue
            lines += [f'msgid_plural "{key}"']
            lines += [f'msgstr[{n}] "{_quoted(form)}"' for n, form in enumerate(forms)]
        for key, message in sorted((extra or {}).items()):
            lines += ["", f'msgid "{key}"', f'msgstr "{_quoted(message)}"']
        return "\n".join(lines) + "\n"

    def test_a_translation_that_drops_a_placeholder_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The one that would still look like a finished page."""
        self._catalogs(
            tmp_path, monkeypatch, self._complete(replace={"institution.lede": "sin numero"})
        )

        try:
            with pytest.raises(CatalogError, match="loses a placeholder"):
                messages.load("xx")
        finally:
            messages.load.cache_clear()

    def test_a_message_the_site_no_longer_renders_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A leftover message is a translator working from a page that no longer exists."""
        self._catalogs(tmp_path, monkeypatch, self._complete(extra={"home.removed": "viejo"}))

        try:
            with pytest.raises(CatalogError, match="the site does not render"):
                messages.load("xx")
        finally:
            messages.load.cache_clear()

    def test_a_plural_message_translated_as_a_singular_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One form where the site will ask for two is a sentence that cannot agree."""
        self._catalogs(tmp_path, monkeypatch, self._complete(demote="state.worst.item"))

        try:
            with pytest.raises(CatalogError, match="is a plural message in en"):
                messages.load("xx")
        finally:
            messages.load.cache_clear()


class TestTheParserRefusesWhatItCannotRead:
    """A parser that skips a line it does not understand is a catalog that loses messages."""

    def test_a_line_that_is_not_a_catalog_line_is_refused(self) -> None:
        with pytest.raises(CatalogError, match="not a catalog line"):
            messages._parse('msgid "a"\nmsgstr "b"\nwhat is this\n')

    def test_a_continued_string_with_nothing_above_it_is_refused(self) -> None:
        with pytest.raises(CatalogError, match="nothing above it"):
            messages._parse('# a comment\n"orphan"\n')

    def test_a_translation_before_any_key_is_refused(self) -> None:
        with pytest.raises(CatalogError, match="before any msgid"):
            messages._parse('msgstr "b"\n')

    def test_plural_forms_out_of_order_are_refused(self) -> None:
        with pytest.raises(CatalogError, match="where msgstr\\[0\\] was expected"):
            messages._parse('msgid "a"\nmsgid_plural "a"\nmsgstr[1] "b"\n')

    def test_an_unknown_escape_is_refused(self) -> None:
        with pytest.raises(CatalogError, match="unknown escape"):
            messages._parse('msgid "a"\nmsgstr "b\\q"\n')

    def test_a_string_ending_in_a_backslash_is_refused(self) -> None:
        with pytest.raises(CatalogError, match="ends in a backslash"):
            messages._parse('msgid "a"\nmsgstr "b\\"\n')

    def test_the_escapes_a_catalog_may_carry_round_trip(self) -> None:
        entries = messages._parse('msgid "a"\nmsgstr "one\\ntwo\\tthree \\"quoted\\" \\\\ end"\n')

        assert entries[0].forms[0] == 'one\ntwo\tthree "quoted" \\ end'

    def test_adjacent_strings_are_one_message(self) -> None:
        entries = messages._parse('msgid "a"\nmsgstr ""\n"one "\n"two"\n')

        assert entries[0].forms[0] == "one two"


class TestPluralsAgreeWithTheirNumbers:
    """The published site said "1 institutions" on six state pages before the catalog existed."""

    def test_one_is_singular_and_two_is_plural(self) -> None:
        catalog = messages.load()

        assert catalog.count("state.worst.item", 1, field="Admission rate").endswith("institution")
        assert catalog.count("state.worst.item", 2, field="Admission rate").endswith("institutions")

    def test_zero_is_plural_in_english(self) -> None:
        catalog = messages.load()

        assert catalog.count("state.worst.item", 0, field="X") == "X: 0 institutions"

    def test_no_state_page_says_one_institutions(self, tmp_path: Path) -> None:
        site.build(_REPORT, tmp_path, generated="2026-09-05")

        for page in tmp_path.rglob("index.html"):
            assert "1 institutions" not in page.read_text(encoding="utf-8"), page


class TestTheSiteRendersFromTheCatalog:
    def test_a_locale_with_no_catalog_fails_the_build_rather_than_rendering_english(
        self, tmp_path: Path
    ) -> None:
        with pytest.raises(CatalogError):
            site.build(_REPORT, tmp_path, generated="2026-09-05", locale="zz")

    def test_the_page_language_and_og_locale_come_from_the_catalog(
        self, tmp_path: Path, pseudolocale: Catalog
    ) -> None:
        """A page whose prose is translated and whose ``lang`` is not is lying about itself."""
        site.build(_REPORT, tmp_path, generated="2026-09-05", locale="qq")
        home = (tmp_path / "index.html").read_text(encoding="utf-8")

        assert '<html lang="qq">' in home
        assert '<meta property="og:locale" content="qq_QQ">' in home

    def test_the_cli_refuses_a_locale_with_no_catalog(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """``--locale`` is offered from the catalogs on disk, not from a list kept beside them."""
        from disclosed import cli

        report = tmp_path / "report.json"
        report.write_text(json.dumps(_REPORT), encoding="utf-8")

        with pytest.raises(SystemExit):
            cli.main(
                [
                    "site",
                    "--report",
                    str(report),
                    "--out",
                    str(tmp_path / "site"),
                    "--generated",
                    "2026-09-05",
                    "--locale",
                    "es",
                ]
            )
        assert "invalid choice" in capsys.readouterr().err

    def test_the_field_rationales_are_not_translated(self, tmp_path: Path) -> None:
        """The grader's own wording, published identically on the page and in the schema.

        A rationale that read one way on the methodology page and another in ``dataset.json``
        would be two different rules for the same field, which is the defect this project spends
        its time finding in other people's publications.
        """
        page = site.methodology_page().body
        schema = json.loads(dataset.to_schema_json())
        described = {field["name"]: field["description"] for field in schema["schema"]["fields"]}

        for field in FIELDS:
            assert html.escape(field.rationale) in page
            assert field.rationale in described[field.column]
