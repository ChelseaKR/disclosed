"""The message catalog the pages render from, and the line the classifications never cross.

Every sentence the site shows a reader is stored in ``locales/<locale>/LC_MESSAGES/disclosed.po``
and looked up by a stable key. The templates in :mod:`disclosed.site` hold structure -- tables,
headings, links, the order of the argument -- and no prose.

Three rules make this a seam rather than a decoration, and all three are the same rule this
project applies to everybody else's data.

**A classification is data, not prose.** ``reported``, ``implausible``, ``suppressed``,
``not_applicable`` and ``missing`` are the product's contract: five facts that must stay five
facts wherever the file lands. They are translated *here*, at the moment a page is rendered, and
nowhere else. :mod:`disclosed.dataset`, which writes the CSV export and its Table Schema, does not
import this module and must never import it -- a Spanish CSV whose cells said ``no_reportado``
would be a different dataset wearing the same column names, and every consumer joining on those
tokens would break silently. ``tests/test_i18n.py`` asserts the export is unchanged under a locale
that translates all five.

**Untranslated is not English.** A page served as ``lang="es"`` with English paragraphs in it is
an absence rendered as a value, which is the defect this whole project exists to name. So a
catalog that is missing a message, or carries an empty one, is refused at load: a locale either
covers the site or it is not offered. Nothing silently falls back.

**A translation that drops a number is refused too.** Every message's placeholders are compared
against the source catalog's, per message and per plural form. A translated sentence that lost its
``{count}`` would read as a confident claim with the quantity removed, and it would look fine.

The format is GNU gettext ``.po`` because that is what translation tooling reads and what
``docs/I18N.md`` recorded, and it is parsed here rather than compiled to ``.mo`` because a binary
blob in the repository is a claim nobody can review in a diff. The parser is deliberately strict:
it refuses a line it does not understand rather than skipping it, since a skipped line in a
catalog is a message that quietly goes missing.

What this does not do yet is stated in ``docs/I18N.md``: numbers are still formatted with
Python's ``,`` and ``%`` conventions, which are English ones, and the only catalog that exists is
the English source.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from types import MappingProxyType
from typing import Final

__all__ = ["SOURCE_LOCALE", "Catalog", "CatalogError", "available", "load"]

#: The language the site's prose is written and reviewed in. Every other catalog is checked
#: against this one, so it is the definition of "complete" rather than merely the first locale.
SOURCE_LOCALE: Final[str] = "en"

_LOCALES: Final[Path] = Path(__file__).resolve().parent / "locales"
_CATALOG_FILE: Final[str] = "LC_MESSAGES/disclosed.po"

#: A locale name this module will look up on disk. Narrow on purpose: the locale is a caller's
#: string and it is about to become a path, and ``../../etc`` is not a language.
_LOCALE_NAME: Final[re.Pattern[str]] = re.compile(r"^[a-z]{2}(_[A-Z]{2})?$")

#: Plural rules this implementation actually knows how to apply. A catalog whose ``Plural-Forms``
#: is not one of these is refused rather than approximated with the English rule: guessing that
#: some other language pluralises the way English does is exactly the kind of unexamined default
#: that produces "1 institutions" on six published pages, which is the bug this table fixes.
_PLURAL_RULES: Final[Mapping[str, Callable[[int], int]]] = MappingProxyType(
    {
        "nplurals=2; plural=(n != 1);": lambda n: 0 if n == 1 else 1,
    }
)

_PLACEHOLDER: Final[re.Pattern[str]] = re.compile(r"\{([a-z_][a-z0-9_]*)[^{}]*\}")
_KEYWORD: Final[re.Pattern[str]] = re.compile(r'^(msgid_plural|msgid|msgstr(?:\[(\d+)\])?) "(.*)"$')
_CONTINUATION: Final[re.Pattern[str]] = re.compile(r'^"(.*)"$')
_HEADER_FIELD: Final[re.Pattern[str]] = re.compile(r"^([A-Za-z][A-Za-z0-9-]*): ?(.*)$")

_ESCAPES: Final[Mapping[str, str]] = MappingProxyType(
    {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}
)


class CatalogError(ValueError):
    """A catalog that cannot be trusted to render a page.

    Raised rather than worked around. Every case this covers -- an unparseable line, a missing
    message, an empty translation, a placeholder that did not survive translation -- would
    otherwise show up as a page that looks finished and is not.
    """


@dataclass(frozen=True, slots=True)
class _Entry:
    """One catalog entry as it was written, before anything is checked about it."""

    key: str
    plural_key: str | None
    forms: tuple[str, ...]


def _unescape(raw: str, *, line: int) -> str:
    """Resolve the backslash escapes a ``.po`` string may carry.

    An unknown escape is an error, not a literal backslash. ``\\n`` and ``\\t`` are the only two
    that appear in practice, and silently passing through anything else would let a typo become
    two characters of a published sentence.
    """
    out: list[str] = []
    index = 0
    while index < len(raw):
        character = raw[index]
        if character != "\\":
            out.append(character)
            index += 1
            continue
        if index + 1 == len(raw):
            raise CatalogError(f"line {line}: the string ends in a backslash")
        following = raw[index + 1]
        if following not in _ESCAPES:
            raise CatalogError(f"line {line}: unknown escape \\{following}")
        out.append(_ESCAPES[following])
        index += 2
    return "".join(out)


class _Reader:
    """The half-read entry a ``.po`` parser is always in the middle of.

    Split out of :func:`_parse` so that the loop reads as "comment, keyword, continuation, or
    refuse" and the bookkeeping about which string a continued line belongs to lives in one place.
    """

    def __init__(self) -> None:
        self.entries: list[_Entry] = []
        self._key: list[str] | None = None
        self._plural_key: list[str] | None = None
        self._forms: list[list[str]] = []
        self._current: list[str] | None = None

    def flush(self) -> None:
        """Close the entry being read, if there is one."""
        if self._key is not None:
            self.entries.append(
                _Entry(
                    key="".join(self._key),
                    plural_key=None if self._plural_key is None else "".join(self._plural_key),
                    forms=tuple("".join(form) for form in self._forms),
                )
            )
        self._key = self._plural_key = self._current = None
        self._forms = []

    def end_run(self) -> None:
        """A comment or a blank line: whatever string was being continued is finished."""
        self._current = None

    def keyword(self, word: str, index: str | None, value: str, *, line: int) -> None:
        if word == "msgid":
            self.flush()
            self._key = [value]
            self._current = self._key
        elif self._key is None:
            raise CatalogError(f"line {line}: {word} before any msgid")
        elif word == "msgid_plural":
            self._plural_key = [value]
            self._current = self._plural_key
        else:
            slot = 0 if index is None else int(index)
            if slot != len(self._forms):
                raise CatalogError(
                    f"line {line}: msgstr[{slot}] where msgstr[{len(self._forms)}] was expected"
                )
            self._forms.append([value])
            self._current = self._forms[-1]

    def keep_reading(self, value: str, *, line: int) -> None:
        if self._current is None:
            raise CatalogError(f"line {line}: a continued string with nothing above it")
        self._current.append(value)


def _parse(text: str) -> list[_Entry]:
    """Read a ``.po`` file into entries, refusing anything it does not understand.

    Comments and blank lines separate entries; a bare quoted line continues the string above it,
    which is how a paragraph is written in this format. A line that is neither is an error: a
    parser that skips what it cannot read is a catalog that loses messages quietly.
    """
    reader = _Reader()
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            reader.end_run()
        elif (keyword := _KEYWORD.match(line)) is not None:
            reader.keyword(
                keyword.group(1),
                keyword.group(2),
                _unescape(keyword.group(3), line=number),
                line=number,
            )
        elif (continuation := _CONTINUATION.match(line)) is not None:
            reader.keep_reading(_unescape(continuation.group(1), line=number), line=number)
        else:
            raise CatalogError(f"line {number}: not a catalog line: {line!r}")
    reader.flush()
    return reader.entries


def _headers(entries: Iterable[_Entry], *, locale: str) -> dict[str, str]:
    """The metadata block, which a ``.po`` file carries as the entry with an empty key."""
    for entry in entries:
        if entry.key == "":
            fields: dict[str, str] = {}
            for line in entry.forms[0].splitlines() if entry.forms else []:
                field = _HEADER_FIELD.match(line)
                if field is None:
                    raise CatalogError(f"{locale}: not a header field: {line!r}")
                fields[field.group(1)] = field.group(2).strip()
            return fields
    raise CatalogError(f"{locale}: the catalog has no header entry")


def _placeholders(message: str) -> frozenset[str]:
    """The names a message expects to be given. Compared across locales, never guessed at."""
    return frozenset(_PLACEHOLDER.findall(message))


@dataclass(frozen=True, eq=False, slots=True)
class Catalog:
    """One locale's messages, already checked to be complete and consistent.

    Rendering is :meth:`text` and :meth:`count`. Neither escapes anything: catalog prose is
    reviewed source that may legitimately carry markup, and the values substituted into it are the
    caller's to escape, exactly as they were when these sentences were f-strings in the template.
    """

    locale: str
    html_lang: str
    og_locale: str
    plural_forms: str
    singular: Mapping[str, str]
    plural: Mapping[str, tuple[str, ...]]

    def text(self, key: str, /, **fields: object) -> str:
        """One message, with its placeholders filled in.

        A key the catalog does not carry raises rather than rendering an empty string, so a
        template that outgrows its catalog fails a build instead of publishing a gap.
        """
        message = self.singular.get(key)
        if message is None:
            raise CatalogError(f"{self.locale}: no message {key!r}")
        return message.format(**fields)

    def count(self, key: str, number: int, /, **fields: object) -> str:
        """The form of a message that agrees with ``number``, which is passed in as ``count``."""
        forms = self.plural.get(key)
        if forms is None:
            raise CatalogError(f"{self.locale}: no plural message {key!r}")
        return forms[_PLURAL_RULES[self.plural_forms](number)].format(count=number, **fields)

    @property
    def keys(self) -> frozenset[str]:
        """Every message this catalog carries, singular and plural alike."""
        return frozenset(self.singular) | frozenset(self.plural)


def _catalog(locale: str, entries: list[_Entry]) -> Catalog:
    """Assemble a catalog from parsed entries, refusing a message that would render as nothing."""
    headers = _headers(entries, locale=locale)
    plural_forms = headers.get("Plural-Forms", "")
    if plural_forms not in _PLURAL_RULES:
        raise CatalogError(
            f"{locale}: Plural-Forms {plural_forms!r} is not a rule this project knows how to "
            "apply. Add it to _PLURAL_RULES with the language it is for, rather than letting the "
            "catalog load and pluralise by the English rule."
        )
    singular: dict[str, str] = {}
    plural: dict[str, tuple[str, ...]] = {}
    for entry in entries:
        if entry.key == "":
            continue
        if entry.key in singular or entry.key in plural:
            raise CatalogError(f"{locale}: {entry.key!r} appears twice")
        if not all(form for form in entry.forms) or not entry.forms:
            raise CatalogError(
                f"{locale}: {entry.key!r} is untranslated. A message with no translation would be "
                "published in another language on a page that claims to be in this one."
            )
        if entry.plural_key is None:
            if len(entry.forms) != 1:
                raise CatalogError(f"{locale}: {entry.key!r} has plural forms but no msgid_plural")
            singular[entry.key] = entry.forms[0]
        else:
            plural[entry.key] = entry.forms
    for key, forms in plural.items():
        if len(forms) != 2:
            raise CatalogError(
                f"{locale}: {key!r} carries {len(forms)} plural forms; {plural_forms} needs 2"
            )
    return Catalog(
        locale=locale,
        html_lang=headers.get("Language", locale),
        og_locale=headers.get("X-OG-Locale", locale),
        plural_forms=plural_forms,
        singular=MappingProxyType(singular),
        plural=MappingProxyType(plural),
    )


def _check_against_source(catalog: Catalog, source: Catalog) -> None:
    """Refuse a translation that does not say the same things the source catalog says.

    Coverage and placeholders, both directions. A missing message is a page half in English; an
    extra one is a message the site stopped rendering and nobody told the translator; a lost
    placeholder is a sentence that quietly drops the number it was about.
    """
    missing = sorted(source.keys - catalog.keys)
    if missing:
        raise CatalogError(
            f"{catalog.locale}: {len(missing)} message(s) the site renders are not in this "
            f"catalog, starting with {missing[0]!r}. A partly translated locale is not offered."
        )
    extra = sorted(catalog.keys - source.keys)
    if extra:
        raise CatalogError(
            f"{catalog.locale}: {len(extra)} message(s) the site does not render, starting with "
            f"{extra[0]!r}. Remove them, or the catalog is describing a page that does not exist."
        )
    for key in sorted(source.plural):
        if key not in catalog.plural:
            raise CatalogError(f"{catalog.locale}: {key!r} is a plural message in {source.locale}")
    for key, message in sorted(source.singular.items()):
        _check_placeholders(catalog, key, catalog.singular[key], _placeholders(message))
    for key, forms in sorted(source.plural.items()):
        expected = _placeholders(forms[0]) | _placeholders(forms[1])
        for form in catalog.plural[key]:
            _check_placeholders(catalog, key, form, expected)


def _check_placeholders(catalog: Catalog, key: str, message: str, expected: frozenset[str]) -> None:
    """Every value the source message reports must survive into the translated one.

    ``count`` is the one name a translation may add that the source did not use: it is passed to
    every plural message whether or not English needed to print it, and a language that
    inflects on the number may have to. Losing one is never allowed in either direction, because
    a sentence with the quantity removed still reads as a finished claim.
    """
    found = _placeholders(message)
    if not expected <= found <= expected | {"count"}:
        raise CatalogError(
            f"{catalog.locale}: {key!r} uses {sorted(found)} where the source uses "
            f"{sorted(expected)}. A translation that loses a placeholder loses the value it "
            "was reporting, and the sentence still reads as a finished claim."
        )


@cache
def load(locale: str = SOURCE_LOCALE) -> Catalog:
    """The catalog for one locale, parsed and checked, or ``CatalogError``.

    Cached, because a 617-page build should read the file once and because a catalog is immutable
    by construction. Every locale other than the source is checked against the source before it is
    handed back, so an incomplete translation cannot reach a page.
    """
    if _LOCALE_NAME.match(locale) is None:
        raise CatalogError(f"{locale!r} is not a locale name")
    path = _LOCALES / locale / _CATALOG_FILE
    if not path.is_file():
        raise CatalogError(f"no catalog for {locale!r}; available: {', '.join(available())}")
    catalog = _catalog(locale, _parse(path.read_text(encoding="utf-8")))
    if locale != SOURCE_LOCALE:
        _check_against_source(catalog, load(SOURCE_LOCALE))
    return catalog


def available() -> tuple[str, ...]:
    """Every locale with a catalog on disk, in sorted order."""
    return tuple(
        sorted(
            directory.name
            for directory in _LOCALES.iterdir()
            if (directory / _CATALOG_FILE).is_file()
        )
    )
