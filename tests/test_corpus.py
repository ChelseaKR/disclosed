"""The federal definitions corpus: committed bytes, committed hashes, committed extraction.

Three replay-style invariants, the same discipline ``data/national.json`` is held to:

* every raw file still hashes to what the manifest recorded when it was fetched;
* ``passages.json`` is exactly what the extractor produces from those raw bytes today;
* every field the project grades has a passage that *defines* it, not merely one that is related.

And the quote verifier, which is the thing the rest of the layer depends on: a quote is in the
corpus or it is not, and "close" is not.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from disclosed.ask import corpus, definitions
from disclosed.fields import ALL_FIELDS

_ROOT = Path(__file__).resolve().parent.parent
_CORPUS = _ROOT / "corpus"


@pytest.fixture(scope="module")
def loaded() -> corpus.Corpus:
    return corpus.load(_CORPUS)


class TestTheCommittedCorpus:
    def test_every_document_in_the_manifest_is_one_the_module_knows(self) -> None:
        manifest = json.loads((_CORPUS / "manifest.json").read_text(encoding="utf-8"))
        assert {e["id"] for e in manifest["documents"]} == {d.id for d in corpus.DOCUMENTS}

    def test_every_raw_file_hashes_to_what_the_manifest_recorded(self) -> None:
        assert all(corpus.check_hashes(_CORPUS).values()), corpus.check_hashes(_CORPUS)

    def test_every_manifest_entry_carries_a_retrieval_date_a_url_and_both_hashes(self) -> None:
        manifest = json.loads((_CORPUS / "manifest.json").read_text(encoding="utf-8"))
        for entry in manifest["documents"]:
            assert entry["retrieved"][:4].isdigit() and len(entry["retrieved"]) == 10, entry
            assert entry["url"].startswith("https://"), entry
            assert entry["bytes"] > 0 and entry["stored_bytes"] > 0
            assert len(entry["sha256"]) == 64 and len(entry["stored_sha256"]) == 64
            if entry["stored"] == "as-fetched":
                assert entry["sha256"] == entry["stored_sha256"], entry["id"]
                assert entry["bytes"] == entry["stored_bytes"], entry["id"]
            else:
                assert entry["stored"] == "without-scripts"
                assert entry["stored_bytes"] < entry["bytes"], entry["id"]

    def test_the_glossary_is_stored_without_its_scripts_or_the_websites_keys(self) -> None:
        """The page's bundle embeds the Scorecard website's own client-side keys. They are
        public, they are not ours to republish, and a secret scanner objected. The prose the
        corpus needs is server-rendered and survives."""
        stored = (_CORPUS / "raw" / "scorecard-glossary.html").read_text(encoding="utf-8")
        assert "<script" not in stored.lower()
        assert not re.search(r'(?i)key\s*:\s*"[A-Za-z0-9_\-]{20,}"', stored)
        assert "open admissions policy do not report" in stored

    def test_without_scripts_removes_every_script_element_and_nothing_else(self) -> None:
        page = b"<html><script src=x></script><p>keep</p><SCRIPT>\nvar k = 1;\n</SCRIPT></html>"
        assert corpus._without_scripts(page) == b"<html><p>keep</p></html>"

    def test_passages_replay_byte_for_byte_from_the_raw_documents(self, tmp_path: Path) -> None:
        corpus.write_passages(tmp_path, corpus.extract(_CORPUS))
        assert (tmp_path / "passages.json").read_bytes() == (
            _CORPUS / "passages.json"
        ).read_bytes(), "run `disclosed corpus` and commit the result"

    def test_each_document_yields_passages(self, loaded: corpus.Corpus) -> None:
        counted = {d.id: 0 for d in corpus.DOCUMENTS}
        for passage in loaded.passages.values():
            counted[passage.document] += 1
        assert all(counted.values()), counted
        # The glossary is the one document a redesign could silently empty.
        assert counted["scorecard-glossary"] >= 30

    def test_passage_ids_are_unique_and_self_describing(self, loaded: corpus.Corpus) -> None:
        for passage_id, passage in loaded.passages.items():
            assert passage.id == passage_id
            assert passage_id.startswith(passage.document + ":")
            assert passage.text and passage.locator


class TestEveryGradedFieldIsDefined:
    @pytest.mark.parametrize("field", ALL_FIELDS, ids=lambda f: f.key)
    def test_has_a_defining_passage_that_exists(self, field: Any, loaded: corpus.Corpus) -> None:
        defs = definitions.definitions_for(field.key)
        assert defs, f"{field.key} has no federal definition mapped"
        assert defs[0].role == "defines", f"{field.key}'s first definition is not the variable"
        for definition in defs:
            assert definition.passage_id in loaded.passages, definition.passage_id
            assert definition.role in {"defines", "related"}

    def test_related_definitions_of_a_different_measure_carry_a_note(
        self, loaded: corpus.Corpus
    ) -> None:
        for key in (
            "latest.completion.completion_rate_4yr_150nt",
            "latest.earnings.10_yrs_after_entry.median",
        ):
            related = [d for d in definitions.definitions_for(key) if d.role == "related"]
            assert related and all(d.note for d in related), key

    def test_applicability_flags_are_defined(self, loaded: corpus.Corpus) -> None:
        for key, definition in definitions.APPLICABILITY_FLAGS.items():
            assert definition.passage_id in loaded.passages, key

    def test_an_ungraded_key_has_no_definition(self) -> None:
        assert definitions.definitions_for("latest.made.up") == ()

    def test_the_scorecard_glossary_says_open_admissions_do_not_report(
        self, loaded: corpus.Corpus
    ) -> None:
        """The federal source itself explains the largest absence in the dataset. Pinned,
        because the whole point of the corpus is to be able to quote this sentence."""
        passage = loaded.passages["scorecard-glossary:acceptance-rate"]
        assert (
            "Institutions that have an open admissions policy do not report on their "
            "acceptance rate" in passage.text
        )

    def test_the_athletics_field_quotes_the_federal_title_as_published(
        self, loaded: corpus.Corpus
    ) -> None:
        """IPEDS titles ATHURL as a Student-Right-to-Know address; the project labels it an
        Equity in Athletics disclosure. The corpus carries the federal words and the mapping
        carries a note; neither silently resolves it."""
        passage = loaded.passages["ipeds-hd2023-dictionary:ATHURL"]
        assert "Student-Right-to-Know" in passage.text
        (definition,) = definitions.definitions_for("ipeds.ATHURL")
        assert "Student-Right-to-Know" in definition.note


class TestQuoteVerification:
    def test_a_verbatim_quote_verifies(self, loaded: corpus.Corpus) -> None:
        assert loaded.verify_quote(
            "Net price calculator web address", "ipeds-hd2023-dictionary:NPRICURL"
        )

    def test_whitespace_is_forgiven_and_nothing_else_is(self, loaded: corpus.Corpus) -> None:
        pid = "scorecard-glossary:acceptance-rate"
        assert loaded.verify_quote(
            "Institutions that have an open\n   admissions policy do not report", pid
        )
        assert not loaded.verify_quote(
            "Institutions that have an open admissions policy do report", pid
        )
        assert not loaded.verify_quote("Institutions that have an open-admissions policy", pid)

    def test_an_empty_quote_or_unknown_passage_never_verifies(self, loaded: corpus.Corpus) -> None:
        assert not loaded.verify_quote("", "ipeds-hd2023-dictionary:NPRICURL")
        assert not loaded.verify_quote("   ", "ipeds-hd2023-dictionary:NPRICURL")
        assert not loaded.verify_quote("Net price calculator", "no-such-document:X")

    def test_provenance_names_the_publisher_url_date_and_hash(self, loaded: corpus.Corpus) -> None:
        prov = loaded.provenance("ipeds-hd2023-dictionary:NPRICURL")
        assert prov["publisher"].startswith("National Center for Education Statistics")
        assert prov["url"].startswith("https://nces.ed.gov/")
        assert len(prov["sha256"]) == 64
        assert prov["locator"] == "NPRICURL"
        assert prov["stored"] == "as-fetched"
        assert loaded.provenance("scorecard-glossary:acceptance-rate")["stored"] == (
            "without-scripts"
        )
        assert loaded.document("scorecard-glossary").publisher.endswith("College Scorecard")

    def test_provenance_of_a_passage_with_no_manifest_entry_raises(
        self, loaded: corpus.Corpus
    ) -> None:
        orphan = corpus.Passage(id="x:y", document="x", locator="y", text="z")
        lonely = corpus.Corpus(manifest={"documents": []}, passages={"x:y": orphan})
        with pytest.raises(KeyError):
            lonely.provenance("x:y")


class TestFetchAndExtractWithoutTheNetwork:
    """``fetch`` is exercised with the downloader replaced, so the manifest shape, the hashing
    and the retrieval date are covered without a byte leaving the machine."""

    def test_fetch_writes_raw_bytes_and_a_manifest(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        served = {d.url: (d.id + "-bytes").encode() for d in corpus.DOCUMENTS}
        monkeypatch.setattr(corpus, "_get", lambda url: served[url])
        manifest = corpus.fetch(tmp_path, today="2026-01-02")
        assert [e["id"] for e in manifest["documents"]] == [d.id for d in corpus.DOCUMENTS]
        for entry in manifest["documents"]:
            assert entry["retrieved"] == "2026-01-02"
            assert (tmp_path / "raw" / entry["file"]).read_bytes() == served[entry["url"]]
        assert all(corpus.check_hashes(tmp_path).values())

    def test_fetch_dates_from_the_clock_when_not_pinned(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(corpus, "_get", lambda url: b"x")
        manifest = corpus.fetch(tmp_path)
        assert len(manifest["documents"][0]["retrieved"]) == 10

    def test_get_refuses_anything_but_https(self) -> None:
        with pytest.raises(ValueError, match="non-https"):
            corpus._get("file:///etc/hosts")
        with pytest.raises(ValueError, match="non-https"):
            corpus._get("http://nces.ed.gov/x")

    def test_get_retries_and_then_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[str] = []

        def failing(request: Any, timeout: float) -> Any:
            calls.append(request.full_url)
            raise OSError("reset")

        monkeypatch.setattr(corpus.urllib.request, "urlopen", failing)
        with pytest.raises(RuntimeError, match="after 3 attempts"):
            corpus._get("https://example.invalid/x")
        assert len(calls) == 3

    def test_get_returns_the_body_on_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _Response:
            def __enter__(self) -> _Response:
                return self

            def __exit__(self, *exc: object) -> None:
                return None

            def read(self) -> bytes:
                return b"body"

        monkeypatch.setattr(corpus.urllib.request, "urlopen", lambda req, timeout: _Response())
        assert corpus._get("https://example.invalid/x") == b"body"

    def test_an_archive_without_exactly_one_workbook_is_refused(self, tmp_path: Path) -> None:
        import io
        import zipfile

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("a.txt", "not a workbook")
        with pytest.raises(ValueError, match="expected one workbook"):
            corpus._inner_workbook(buffer.getvalue())

    def test_the_glossary_parser_survives_an_unrelated_page(self) -> None:
        parser = corpus._GlossaryParser()
        parser.feed("<html><body><h3>Not a glossary</h3><p>text</p></body></html>")
        assert parser.entries == []

    def test_dictionary_rows_without_a_variable_or_name_are_skipped(self) -> None:
        """A blank row in the institution dictionary is not a field."""
        passages = list(
            corpus._scorecard_dictionary_passages(
                corpus.DOCUMENTS[1], (_CORPUS / "raw" / corpus.DOCUMENTS[1].file).read_bytes()
            )
        )
        assert all(p.locator.split(" ")[0] for p in passages)
        assert any(p.id.endswith(":ADM_RATE") for p in passages)


class TestTheCorpusCommand:
    def test_extract_only_rewrites_passages(self, tmp_path: Path) -> None:
        import shutil

        from disclosed.cli import main

        shutil.copytree(_CORPUS, tmp_path / "corpus")
        (tmp_path / "corpus" / "passages.json").write_text("[]", encoding="utf-8")
        assert main(["corpus", "--dir", str(tmp_path / "corpus")]) == 0
        assert (tmp_path / "corpus" / "passages.json").read_bytes() == (
            _CORPUS / "passages.json"
        ).read_bytes()

    def test_fetch_flag_calls_the_fetcher(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import shutil

        from disclosed.cli import main

        shutil.copytree(_CORPUS, tmp_path / "corpus")
        served = {
            d.url: (tmp_path / "corpus" / "raw" / d.file).read_bytes() for d in corpus.DOCUMENTS
        }
        monkeypatch.setattr(corpus, "_get", lambda url: served[url])
        assert main(["corpus", "--dir", str(tmp_path / "corpus"), "--fetch"]) == 0
        out = capsys.readouterr().out
        assert out.count("fetched ") == len(corpus.DOCUMENTS)
        assert "extracted " in out
