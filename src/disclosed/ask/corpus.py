"""The federal definitions the question-answering layer is allowed to quote.

A reader who asks "what does IPEDS mean by a net price calculator" deserves the words IPEDS used,
not a paraphrase that sounds like them. So the documentation behind every field this project
classifies is fetched from its publisher, kept as the bytes that arrived (hash and retrieval date
in ``manifest.json``), reduced to passages by code that is re-run in the test suite, and quoted
verbatim: :func:`Corpus.verify_quote` decides whether a quote is in the corpus, and a quote that is
not is withheld like any other unverified claim.

Four documents, two publishers:

* The College Scorecard glossary page ("Technical Definitions"), which defines the Scorecard's
  published measures in prose and names the variables behind each.
* The College Scorecard data dictionary workbook, whose ``Institution_Data_Dictionary`` sheet
  carries one row per API field with the element's official name, source and notes.
* The IPEDS ``HD2023`` and ``IC2023`` dictionary workbooks, whose ``Varlist`` and ``Description``
  sheets carry each variable's title and long description. These are the survey-component
  documentation for the six public-disclosure addresses and the four applicability flags this
  project reads.

Nothing here fetches at import, at test time, or at request time. ``fetch`` is a deliberate act
that rewrites the manifest; everything else reads what is committed.
"""

from __future__ import annotations

import hashlib
import html
import html.parser
import io
import json
import re
import urllib.request
import zipfile
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from defusedxml.ElementTree import fromstring as _parse_xml

__all__ = ["DOCUMENTS", "Corpus", "Document", "Passage", "extract", "fetch", "load"]

_USER_AGENT: Final[str] = "disclosed-corpus-fetch/0.1 (+https://github.com/ChelseaKR/disclosed)"
_TIMEOUT_SECONDS: Final[float] = 60.0
_ATTEMPTS: Final[int] = 3


@dataclass(frozen=True, slots=True)
class Document:
    """One federal document the corpus keeps, as it was published."""

    id: str
    publisher: str
    title: str
    url: str
    file: str
    """File name under ``raw/``. The bytes are committed so the hash can be re-checked."""

    stored: str = "as-fetched"
    """``as-fetched``, or ``without-scripts`` for an HTML page committed with every ``<script>``
    element removed. The Scorecard glossary is a rendered single-page application whose bundle
    embeds the website's own client-side keys (an api.data.gov key, reCAPTCHA site keys) and
    analytics tags. Those are public by construction, but they are not this project's to
    republish, and a secret scanner is right to object to them. The glossary prose is
    server-rendered in the body and survives the removal intact. The manifest records the hash
    of the bytes as fetched *and* of the bytes as stored, so a re-fetch can still be compared."""


DOCUMENTS: Final[tuple[Document, ...]] = (
    Document(
        id="scorecard-glossary",
        publisher="U.S. Department of Education, College Scorecard",
        title="College Scorecard Technical Definitions (glossary)",
        url="https://collegescorecard.ed.gov/data/glossary/",
        file="scorecard-glossary.html",
        stored="without-scripts",
    ),
    Document(
        id="scorecard-data-dictionary",
        publisher="U.S. Department of Education, College Scorecard",
        title="College Scorecard Data Dictionary (Institution_Data_Dictionary sheet)",
        url="https://collegescorecard.ed.gov/assets/CollegeScorecardDataDictionary.xlsx",
        file="CollegeScorecardDataDictionary.xlsx",
    ),
    Document(
        id="ipeds-hd2023-dictionary",
        publisher="National Center for Education Statistics, IPEDS",
        title="IPEDS Directory Information (HD2023) data dictionary",
        url="https://nces.ed.gov/ipeds/datacenter/data/HD2023_Dict.zip",
        file="HD2023_Dict.zip",
    ),
    Document(
        id="ipeds-ic2023-dictionary",
        publisher="National Center for Education Statistics, IPEDS",
        title="IPEDS Institutional Characteristics (IC2023) data dictionary",
        url="https://nces.ed.gov/ipeds/datacenter/data/IC2023_Dict.zip",
        file="IC2023_Dict.zip",
    ),
)

_BY_ID: Final[dict[str, Document]] = {d.id: d for d in DOCUMENTS}


@dataclass(frozen=True, slots=True)
class Passage:
    """One quotable unit of a document: a glossary entry or a dictionary row."""

    id: str
    document: str
    locator: str
    """Where in the document this came from, in the document's own terms: a glossary heading, a
    variable name. Shown beside a quote so a reader can find it in the source."""

    text: str
    """The passage as published, with whitespace collapsed. Quotes are verified against this."""

    def as_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "document": self.document,
            "locator": self.locator,
            "text": self.text,
        }


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# -- fetching -------------------------------------------------------------------------------


def _get(url: str) -> bytes:
    """Fetch one URL, retrying transient failures. Raises after the last attempt.

    Only ``https://`` is ever opened. The URLs are the constants in :data:`DOCUMENTS`, but the
    check is made here, on the value, so that the scheme guard does not depend on every caller
    remembering it; that is also the argument for the scanner waiver on the call below.
    """
    if not url.startswith("https://"):
        raise ValueError(f"refusing to fetch a non-https URL: {url}")
    last: Exception | None = None
    for _ in range(_ATTEMPTS):
        request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})  # noqa: S310
        try:
            # The scheme is checked to be https above and the URL set is the DOCUMENTS
            # constant; the same waiver the two adapters carry for the same reason.
            # nosemgrep: dynamic-urllib-use-detected
            with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:  # noqa: S310
                return bytes(response.read())
        except OSError as exc:  # connection resets and timeouts; both seen from nces.ed.gov
            last = exc
    raise RuntimeError(f"could not fetch {url} after {_ATTEMPTS} attempts: {last}")


def _without_scripts(data: bytes) -> bytes:
    """An HTML page with every ``<script>`` element removed; see :attr:`Document.stored`."""
    text = data.decode("utf-8")
    return re.sub(r"<script\b[^>]*>.*?</script>", "", text, flags=re.S | re.I).encode("utf-8")


def fetch(corpus_dir: Path, *, today: str | None = None) -> dict[str, Any]:
    """Download every document, write the bytes under ``raw/``, and rewrite ``manifest.json``.

    ``today`` is the retrieval date recorded in the manifest, UTC, and is a parameter so a test
    can pin it; left ``None`` it is read from the clock, because a retrieval date is exactly the
    one place the clock belongs.
    """
    raw_dir = corpus_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    retrieved = today or datetime.now(UTC).date().isoformat()
    entries = []
    for document in DOCUMENTS:
        data = _get(document.url)
        stored = _without_scripts(data) if document.stored == "without-scripts" else data
        (raw_dir / document.file).write_bytes(stored)
        entries.append(
            {
                "id": document.id,
                "publisher": document.publisher,
                "title": document.title,
                "url": document.url,
                "file": document.file,
                "retrieved": retrieved,
                "bytes": len(data),
                "sha256": _sha256(data),
                "stored": document.stored,
                "stored_bytes": len(stored),
                "stored_sha256": _sha256(stored),
            }
        )
    manifest = {"documents": entries}
    (corpus_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


# -- extraction: the Scorecard glossary page ---------------------------------------------


class _GlossaryParser(html.parser.HTMLParser):
    """Walks the glossary page's repeating unit: anchor, ``<h3>`` term, text block, variables.

    The page is a rendered single-page app, so the structure is regular and class-named. The
    parser keys on those class names and nothing else; a redesign that renames them yields zero
    entries, which the test suite treats as a failure rather than an empty corpus.
    """

    def __init__(self) -> None:
        super().__init__()
        self.entries: list[tuple[str, str, str]] = []
        self._term: list[str] | None = None
        self._text: list[str] | None = None
        self._variables: list[str] | None = None
        self._depth = 0
        self._in_h3 = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = dict(attrs).get("class") or ""
        if tag == "h3":
            self._in_h3 = True
            self._term = []
        elif "glossary-text" in classes:
            self._text = []
            self._depth = 1
        elif self._text is not None and self._depth:
            self._depth += 1
        elif "glossary-variables" in classes:
            self._variables = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "h3":
            self._in_h3 = False
        elif self._text is not None and self._depth:
            self._depth -= 1
            if self._depth == 0:
                self._finish_text()

    def _finish_text(self) -> None:
        term = _collapse(" ".join(self._term or []))
        text = _collapse(" ".join(self._text or []))
        self.entries.append((term, text, ""))
        self._text = None

    def handle_data(self, data: str) -> None:
        if self._in_h3 and self._term is not None:
            self._term.append(data)
        elif self._text is not None:
            self._text.append(" " + data)
        elif self._variables is not None:
            self._variables.append(data)
            self._attach_variables()

    def _attach_variables(self) -> None:
        if self._variables is None or not self.entries:
            return
        term, text, _ = self.entries[-1]
        self.entries[-1] = (term, text, _collapse(" ".join(self._variables)))


def _glossary_passages(document: Document, data: bytes) -> Iterator[Passage]:
    parser = _GlossaryParser()
    parser.feed(data.decode("utf-8"))
    for term, text, variables in parser.entries:
        slug = re.sub(r"[^a-z0-9]+", "-", term.lower()).strip("-")
        body = text if not variables else f"{text} {variables}"
        yield Passage(
            id=f"{document.id}:{slug}",
            document=document.id,
            locator=term,
            text=body,
        )


# -- extraction: xlsx workbooks, read with the standard library ----------------------------

_NS: Final[dict[str, str]] = {
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}


def _shared_strings(book: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in book.namelist():
        return []
    root = _parse_xml(book.read("xl/sharedStrings.xml"))
    return [
        "".join(t.text or "" for t in item.iter(f"{{{_NS['m']}}}t"))
        for item in root.findall("m:si", _NS)
    ]


def _cell_text(cell: Any, strings: list[str]) -> str:
    """``cell`` is an ElementTree element, typed ``Any`` so that ``xml.etree`` is never imported
    here: the parse goes through defusedxml, and a scanner reads the import as a parse."""
    value = cell.find("m:v", _NS)
    if value is None:
        inline = cell.find("m:is", _NS)
        if inline is None:
            return ""
        return "".join(t.text or "" for t in inline.iter(f"{{{_NS['m']}}}t"))
    if cell.get("t") == "s":
        return strings[int(value.text or "0")]
    return value.text or ""


def _sheet_rows(book: zipfile.ZipFile, target: str, strings: list[str]) -> list[dict[str, str]]:
    root = _parse_xml(book.read(target))
    rows = []
    for row in root.iter(f"{{{_NS['m']}}}row"):
        cells: dict[str, str] = {}
        for cell in row.findall("m:c", _NS):
            column = re.match(r"[A-Z]+", cell.get("r", ""))
            cells[column.group(0) if column else ""] = _cell_text(cell, strings)
        rows.append(cells)
    return rows


def _workbook(data: bytes) -> dict[str, list[dict[str, str]]]:
    """Every sheet of an ``.xlsx``, as rows of ``{column letter: text}``.

    Read with ``zipfile`` and ``ElementTree`` rather than a spreadsheet library, because the
    project has no runtime dependencies and a federal data dictionary is a zip of XML.
    """
    with zipfile.ZipFile(io.BytesIO(data)) as book:
        workbook = _parse_xml(book.read("xl/workbook.xml"))
        relationships = _parse_xml(book.read("xl/_rels/workbook.xml.rels"))
        targets = {rel.get("Id"): rel.get("Target") or "" for rel in relationships}
        strings = _shared_strings(book)
        sheets: dict[str, list[dict[str, str]]] = {}
        listed = workbook.find("m:sheets", _NS)
        for sheet in list(listed) if listed is not None else []:
            target = targets[sheet.get(f"{{{_NS['r']}}}id")]
            path = target if target.startswith("xl/") else "xl/" + target.lstrip("/")
            sheets[sheet.get("name") or ""] = _sheet_rows(book, path, strings)
    return sheets


def _inner_workbook(data: bytes) -> bytes:
    """The single ``.xlsx`` inside an IPEDS ``*_Dict.zip``."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = [n for n in archive.namelist() if n.lower().endswith(".xlsx")]
        if len(names) != 1:
            raise ValueError(f"expected one workbook in the archive, found {names}")
        return archive.read(names[0])


def _scorecard_dictionary_passages(document: Document, data: bytes) -> Iterator[Passage]:
    """One passage per row of the institution dictionary, keyed by the API field path.

    Columns, from the sheet's own header row: A name of data element, B dev-category, C
    developer-friendly name, F variable name, I source, K notes. The passage text is the
    element's name, its source, and its notes when it has any, joined as the dictionary states
    them; a note is the closest thing the dictionary has to a definition in prose.
    """
    rows = _workbook(data)["Institution_Data_Dictionary"]
    for row in rows[1:]:
        variable = row.get("F", "").strip()
        category = row.get("B", "").strip()
        friendly = row.get("C", "").strip()
        if not variable or not friendly:
            continue
        key = f"{category}.{friendly}" if category else friendly
        parts = [f"{row.get('A', '').strip()}."]
        if row.get("I", "").strip():
            parts.append(f"Source: {row['I'].strip()}.")
        if row.get("K", "").strip():
            parts.append(row["K"].strip())
        yield Passage(
            id=f"{document.id}:{variable}",
            document=document.id,
            locator=f"{variable} ({key})",
            text=_collapse(" ".join(parts)),
        )


def _ipeds_dictionary_passages(document: Document, data: bytes) -> Iterator[Passage]:
    """One passage per IPEDS variable: the ``Varlist`` title, then the ``Description`` text."""
    sheets = _workbook(_inner_workbook(data))
    titles = {row.get("B", "").strip(): row.get("G", "").strip() for row in sheets["Varlist"][1:]}
    descriptions = {
        row.get("B", "").strip(): row.get("C", "").strip() for row in sheets["Description"][1:]
    }
    for variable, title in titles.items():
        if not variable:
            continue
        description = descriptions.get(variable, "")
        text = title if not description or description == title else f"{title}. {description}"
        yield Passage(
            id=f"{document.id}:{variable}",
            document=document.id,
            locator=variable,
            text=_collapse(text),
        )


def _passages_of(document: Document, data: bytes) -> Iterator[Passage]:
    if document.id == "scorecard-glossary":
        return _glossary_passages(document, data)
    if document.id == "scorecard-data-dictionary":
        return _scorecard_dictionary_passages(document, data)
    return _ipeds_dictionary_passages(document, data)


def extract(corpus_dir: Path) -> list[Passage]:
    """Reduce the committed raw documents to passages. Deterministic; no network."""
    passages: list[Passage] = []
    for document in DOCUMENTS:
        data = (corpus_dir / "raw" / document.file).read_bytes()
        passages.extend(_passages_of(document, data))
    return passages


def write_passages(corpus_dir: Path, passages: Iterable[Passage]) -> None:
    (corpus_dir / "passages.json").write_text(
        json.dumps([p.as_dict() for p in passages], indent=1, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


# -- the loaded corpus ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Corpus:
    """The committed passages plus the manifest that says where and when they came from."""

    manifest: Mapping[str, Any]
    passages: Mapping[str, Passage]

    def document(self, document_id: str) -> Document:
        return _BY_ID[document_id]

    def provenance(self, passage_id: str) -> dict[str, str]:
        """Publisher, title, URL, retrieval date and hash for the document behind a passage."""
        passage = self.passages[passage_id]
        for entry in self.manifest["documents"]:
            if entry["id"] == passage.document:
                return {
                    "publisher": str(entry["publisher"]),
                    "title": str(entry["title"]),
                    "url": str(entry["url"]),
                    "retrieved": str(entry["retrieved"]),
                    "sha256": str(entry["sha256"]),
                    "stored": str(entry.get("stored", "as-fetched")),
                    "locator": passage.locator,
                }
        raise KeyError(passage.document)

    def verify_quote(self, quote: str, passage_id: str) -> bool:
        """Whether ``quote`` appears verbatim in the passage, up to whitespace.

        Whitespace is collapsed on both sides because a model wraps lines and a spreadsheet
        cell does not; nothing else is normalised. A quote with a changed word is not a quote.
        """
        passage = self.passages.get(passage_id)
        if passage is None:
            return False
        needle = _collapse(quote)
        return bool(needle) and needle in passage.text


def load(corpus_dir: Path) -> Corpus:
    manifest = json.loads((corpus_dir / "manifest.json").read_text(encoding="utf-8"))
    raw = json.loads((corpus_dir / "passages.json").read_text(encoding="utf-8"))
    passages = {
        item["id"]: Passage(
            id=item["id"],
            document=item["document"],
            locator=item["locator"],
            text=item["text"],
        )
        for item in raw
    }
    return Corpus(manifest=manifest, passages=passages)


def check_hashes(corpus_dir: Path) -> dict[str, bool]:
    """Whether each committed raw file still hashes to what the manifest recorded for it.

    ``stored_sha256`` is the hash of the committed bytes; ``sha256`` is the hash of the bytes as
    fetched, which differ only for a document stored ``without-scripts``.
    """
    manifest = json.loads((corpus_dir / "manifest.json").read_text(encoding="utf-8"))
    return {
        str(entry["id"]): _sha256((corpus_dir / "raw" / entry["file"]).read_bytes())
        == entry["stored_sha256"]
        for entry in manifest["documents"]
    }
