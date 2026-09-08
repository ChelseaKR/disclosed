"""The dispute channel, and the one property it must never acquire.

The README's contract is that a scorecard which cannot be disputed line by line is an accusation.
The rationales made the finding arguable; this makes the argument land somewhere a reader sees.

The property that matters most here is a negative one. **A dispute must not move a grade.** Every
other test in this file is about refusing a statement that would be published beside a finding
nobody made; this one is about what happens when the statement is perfectly valid, and the answer
has to be that the report, the score, the letter and every published figure are byte-identical.
A channel that quietly changed a score would be a scoring input wearing a comment's clothes, and
the institution best at filing paperwork would score highest.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from disclosed import cli, dataset, disputes, site
from disclosed.disclosure import CLASSIFICATIONS
from disclosed.fields import FIELDS

_ROOT = Path(__file__).resolve().parent.parent

_FIELD = "Admission rate"

_REPORT: dict[str, Any] = {
    "scope": {
        "kind": "sample",
        "source": "College Scorecard",
        "institutions": 2,
        "states": 1,
        "universe": 6300,
        "coverage": 2 / 6300,
        "note": "The first records the API returned.",
    },
    "institutions": 2,
    "ungradeable": 0,
    "overall": {
        "label": "all institutions",
        "graded": 2,
        "ungradeable": 0,
        "mean_score": 0.75,
        "worst_fields": [[_FIELD, 1]],
    },
    "by_state": [
        {"label": "CA", "graded": 2, "ungradeable": 0, "mean_score": 0.75, "worst_fields": []}
    ],
    "implausible": [],
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
            "name": "Quiet College",
            "state": "CA",
            "score": 0.5,
            "letter": "C",
            "fields": {f.label: ("missing" if f.label == _FIELD else "reported") for f in FIELDS},
        },
    ],
}

_VALID: dict[str, str] = {
    "unit_id": "2",
    "field": _FIELD,
    "disputed_classification": "missing",
    "statement": "We publish our admission rate at the address below and reported it for the "
    "same year.",
    "evidence_url": "https://example.edu/about/admissions",
    "filed": "2026-09-07",
    "filed_by": "Office of Institutional Research, Quiet College",
}


def _write(directory: Path, payload: dict[str, str], name: str | None = None) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name or payload['unit_id']}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.fixture
def filed(tmp_path: Path) -> Path:
    """A directory holding one valid dispute."""
    directory = tmp_path / "disputes"
    _write(directory, _VALID)
    return directory


class TestAGradeDoesNotMove:
    """The negative property, first, because everything else is in service of it."""

    def test_the_pages_are_identical_but_for_the_one_section(
        self, tmp_path: Path, filed: Path
    ) -> None:
        without, with_ = tmp_path / "a", tmp_path / "b"
        site.build(_REPORT, without, generated="2026-09-07")
        site.build(_REPORT, with_, generated="2026-09-07", disputes=disputes.load(filed))

        changed = [
            page.relative_to(without).as_posix()
            for page in sorted(without.rglob("index.html"))
            if page.read_bytes() != (with_ / page.relative_to(without)).read_bytes()
        ]

        assert changed == ["institution/2/index.html"]

    def test_the_grade_the_score_and_the_letter_are_unchanged_on_that_page(
        self, tmp_path: Path, filed: Path
    ) -> None:
        """The one page that does change must change only by gaining the section.

        Asserted on the numbers themselves rather than on the diff, because "the bytes differ"
        is exactly what a grade moving would also look like.
        """
        out = tmp_path / "site"
        site.build(_REPORT, out, generated="2026-09-07", disputes=disputes.load(filed))
        page = (out / "institution" / "2" / "index.html").read_text(encoding="utf-8")

        assert ">C<" in page
        assert "50%" in page
        assert "Disputed by this institution" in page

    def test_the_report_the_dataset_reads_is_never_touched(self, filed: Path) -> None:
        before = json.dumps(_REPORT, sort_keys=True)
        loaded = disputes.load(filed)
        disputes.check_against(loaded, _REPORT)
        site_report = json.dumps(_REPORT, sort_keys=True)

        assert site_report == before
        assert loaded

    def test_the_csv_classification_column_is_unchanged_and_only_the_flag_moves(
        self, filed: Path
    ) -> None:
        loaded = disputes.load(filed)
        plain = dataset.to_csv(_REPORT).splitlines()
        flagged = dataset.to_csv(
            _REPORT, disputed=frozenset((d.unit_id, d.field) for d in loaded)
        ).splitlines()
        header = plain[0].split(",")
        column = next(f.column for f in FIELDS if f.label == _FIELD)

        assert len(plain) == len(flagged)
        for before, after in zip(plain[1:], flagged[1:], strict=True):
            first = dict(zip(header, before.split(","), strict=True))
            second = dict(zip(header, after.split(","), strict=True))
            assert first[column] == second[column]
            differing = {key for key in first if first[key] != second[key]}
            assert differing <= {column + dataset.DISPUTED_COLUMN_SUFFIX}

    def test_the_flag_is_set_for_exactly_the_pair_that_was_filed(self, filed: Path) -> None:
        loaded = disputes.load(filed)
        rows = dataset.to_csv(
            _REPORT, disputed=frozenset((d.unit_id, d.field) for d in loaded)
        ).splitlines()
        header = rows[0].split(",")
        column = next(f.column for f in FIELDS if f.label == _FIELD)
        flag = column + dataset.DISPUTED_COLUMN_SUFFIX
        parsed = [dict(zip(header, row.split(","), strict=True)) for row in rows[1:]]

        assert {row["unit_id"]: row[flag] for row in parsed} == {"1": "false", "2": "true"}

    def test_no_dispute_column_is_ever_empty(self, filed: Path) -> None:
        """A blank would mean "no dispute", "not checked" and "this row predates the column"
        identically, and this file exists because those are three different facts."""
        rows = dataset.to_csv(_REPORT).splitlines()
        header = rows[0].split(",")
        flags = [name for name in header if name.endswith(dataset.DISPUTED_COLUMN_SUFFIX)]

        assert len(flags) == len(FIELDS)
        for row in rows[1:]:
            parsed = dict(zip(header, row.split(","), strict=True))
            assert {parsed[flag] for flag in flags} <= {"true", "false"}


class TestNothingIsRenderedWhenNothingWasFiled:
    def test_a_build_shown_no_disputes_carries_no_section(self, tmp_path: Path) -> None:
        """Absent rather than empty. A "Disputed" heading reading "none" would invite the reading
        that the institution was asked and declined, and nobody asked."""
        out = tmp_path / "site"
        site.build(_REPORT, out, generated="2026-09-07")

        for page in sorted(out.rglob("index.html")):
            assert "Disputed by this institution" not in page.read_text(encoding="utf-8")

    def test_an_absent_directory_is_no_disputes_and_not_an_error(self, tmp_path: Path) -> None:
        assert disputes.load(tmp_path / "nothing-here") == ()

    def test_an_empty_directory_is_no_disputes(self, tmp_path: Path) -> None:
        (tmp_path / "disputes").mkdir()

        assert disputes.load(tmp_path / "disputes") == ()

    def test_the_committed_directory_holds_no_fabricated_dispute(self) -> None:
        """The published set is whatever institutions have actually filed.

        A fixture dispute attributed to a real college, committed as though it were real, would
        be exactly the kind of plausible unfounded statement this project exists to object to.
        The fixtures are in this file; the register is theirs.
        """
        committed = disputes.load(_ROOT / disputes.DIRECTORY)

        assert committed == ()
        assert (_ROOT / disputes.DIRECTORY / "README.md").is_file()


class TestTheCommittedDisputesAreValid:
    """A gate over whatever is in the register, whether that is nothing or a hundred."""

    def test_every_committed_dispute_names_a_finding_the_committed_report_makes(self) -> None:
        report = json.loads((_ROOT / "data" / "report.json").read_text(encoding="utf-8"))

        disputes.check_against(disputes.load(_ROOT / disputes.DIRECTORY), report)

    def test_that_check_is_not_vacuous(self) -> None:
        """The test above passes over an empty register, which is where it will spend most of its
        life. This is the assertion that it would fail if the register were wrong."""
        report = json.loads((_ROOT / "data" / "report.json").read_text(encoding="utf-8"))
        planted = disputes.Dispute(**{**_VALID, "unit_id": "99999999"})

        with pytest.raises(disputes.DisputeError, match="99999999"):
            disputes.check_against([planted], report)


class TestItRefusesAStatementItCannotAttribute:
    """Every refusal, exercised. A loader that cannot say no is a fallback with extra steps."""

    def test_a_file_not_named_for_a_unit_id_is_refused(self, tmp_path: Path) -> None:
        _write(tmp_path / "d", _VALID, name="quiet-college")

        with pytest.raises(disputes.DisputeError, match="not named for a unit id"):
            disputes.load(tmp_path / "d")

    def test_a_file_whose_name_and_unit_id_disagree_is_refused(self, tmp_path: Path) -> None:
        """There is no way to tell from here which of the two is the mistake."""
        _write(tmp_path / "d", _VALID, name="3")

        with pytest.raises(disputes.DisputeError, match="named for unit 3"):
            disputes.load(tmp_path / "d")

    def test_invalid_json_is_refused(self, tmp_path: Path) -> None:
        (tmp_path / "d").mkdir()
        (tmp_path / "d" / "2.json").write_text("{not json", encoding="utf-8")

        with pytest.raises(disputes.DisputeError, match="not valid JSON"):
            disputes.load(tmp_path / "d")

    def test_a_document_that_is_not_an_object_is_refused(self, tmp_path: Path) -> None:
        (tmp_path / "d").mkdir()
        (tmp_path / "d" / "2.json").write_text("[]", encoding="utf-8")

        with pytest.raises(disputes.DisputeError, match="not a dispute object"):
            disputes.load(tmp_path / "d")

    @pytest.mark.parametrize("key", sorted(_VALID))
    def test_a_missing_key_is_refused(self, tmp_path: Path, key: str) -> None:
        """Every one of them is part of what makes the statement attributable."""
        _write(
            tmp_path / "d", {k: v for k, v in _VALID.items() if k != key}, name=_VALID["unit_id"]
        )

        with pytest.raises(disputes.DisputeError, match=key):
            disputes.load(tmp_path / "d")

    def test_an_unknown_key_is_refused_rather_than_ignored(self, tmp_path: Path) -> None:
        """A filer who wrote one believed they had said something, and dropping it publishes a
        statement missing the part they cared about."""
        _write(tmp_path / "d", {**_VALID, "remedy": "regrade us"})

        with pytest.raises(disputes.DisputeError, match="remedy"):
            disputes.load(tmp_path / "d")

    @pytest.mark.parametrize("value", ["", "   "])
    def test_an_empty_statement_is_refused(self, tmp_path: Path, value: str) -> None:
        _write(tmp_path / "d", {**_VALID, "statement": value})

        with pytest.raises(disputes.DisputeError, match="statement"):
            disputes.load(tmp_path / "d")

    def test_a_non_string_value_is_refused(self, tmp_path: Path) -> None:
        (tmp_path / "d").mkdir()
        (tmp_path / "d" / "2.json").write_text(
            json.dumps({**_VALID, "filed_by": 7}), encoding="utf-8"
        )

        with pytest.raises(disputes.DisputeError, match="filed_by"):
            disputes.load(tmp_path / "d")

    def test_a_classification_that_is_not_one_of_the_five_states_is_refused(
        self, tmp_path: Path
    ) -> None:
        _write(tmp_path / "d", {**_VALID, "disputed_classification": "disputed"})

        with pytest.raises(disputes.DisputeError, match="five states"):
            disputes.load(tmp_path / "d")

    @pytest.mark.parametrize("value", ["last Tuesday", "2026-9-7", "20260907", ""])
    def test_a_date_that_is_not_an_iso_date_is_refused(self, tmp_path: Path, value: str) -> None:
        _write(tmp_path / "d", {**_VALID, "filed": value})

        with pytest.raises(disputes.DisputeError, match="filed"):
            disputes.load(tmp_path / "d")

    @pytest.mark.parametrize(
        "url",
        [
            "javascript:alert(1)",
            "data:text/html,<script>alert(1)</script>",
            "file:///etc/passwd",
        ],
    )
    def test_an_evidence_url_that_is_not_a_web_address_is_refused(
        self, tmp_path: Path, url: str
    ) -> None:
        """This URL becomes a link on a page about somebody else, and the other schemes are how
        that stops being a link."""
        _write(tmp_path / "d", {**_VALID, "evidence_url": url})

        with pytest.raises(disputes.DisputeError, match="scheme"):
            disputes.load(tmp_path / "d")


class TestItRefusesARebuttalOfAFindingNobodyMade:
    def test_an_institution_this_report_does_not_grade_is_refused(self) -> None:
        planted = disputes.Dispute(**{**_VALID, "unit_id": "404"})

        with pytest.raises(disputes.DisputeError, match="does not grade"):
            disputes.check_against([planted], _REPORT)

    def test_a_field_this_report_does_not_carry_is_refused_and_says_what_it_does(self) -> None:
        planted = disputes.Dispute(**{**_VALID, "field": "Endowment per student"})

        with pytest.raises(disputes.DisputeError, match="does not carry"):
            disputes.check_against([planted], _REPORT)

    def test_a_dispute_overtaken_by_a_regrading_is_refused_as_stale(self) -> None:
        """It disputes a classification the report no longer gives. Rendering it beside a state
        it does not name would put words in the institution's mouth."""
        planted = disputes.Dispute(**{**_VALID, "disputed_classification": "suppressed"})

        with pytest.raises(disputes.DisputeError, match="stale"):
            disputes.check_against([planted], _REPORT)

    def test_a_valid_dispute_passes(self) -> None:
        disputes.check_against([disputes.Dispute(**_VALID)], _REPORT)


class TestWhatThePageSays:
    def _page(self, tmp_path: Path, filed: Path) -> str:
        out = tmp_path / "site"
        site.build(_REPORT, out, generated="2026-09-07", disputes=disputes.load(filed))
        return (out / "institution" / "2" / "index.html").read_text(encoding="utf-8")

    def test_the_statement_is_quoted_verbatim(self, tmp_path: Path, filed: Path) -> None:
        page = self._page(tmp_path, filed)

        assert _VALID["statement"] in page

    def test_the_filer_and_the_date_are_named(self, tmp_path: Path, filed: Path) -> None:
        """An anonymous statement on somebody else's page is not attributable to anyone."""
        page = self._page(tmp_path, filed)

        assert _VALID["filed_by"] in page
        assert _VALID["filed"] in page

    def test_the_evidence_is_a_link_and_the_page_fetches_nothing(
        self, tmp_path: Path, filed: Path
    ) -> None:
        page = self._page(tmp_path, filed)

        assert f'<a href="{_VALID["evidence_url"]}"' in page
        assert "<script" not in page
        assert "<img" not in page

    def test_a_statement_carrying_markup_is_escaped(self, tmp_path: Path) -> None:
        """A third party writes this and it lands in someone else's page."""
        directory = tmp_path / "disputes"
        _write(directory, {**_VALID, "statement": '<script>alert(1)</script> & "quoted"'})

        out = tmp_path / "site"
        site.build(_REPORT, out, generated="2026-09-07", disputes=disputes.load(directory))
        page = (out / "institution" / "2" / "index.html").read_text(encoding="utf-8")

        assert "<script>" not in page
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page

    def test_the_section_says_the_grade_did_not_move(self, tmp_path: Path, filed: Path) -> None:
        """A reader must not have to infer it from the unchanged number above."""
        page = self._page(tmp_path, filed)

        assert "never folded into one" in page

    def test_only_the_institution_that_filed_carries_the_section(
        self, tmp_path: Path, filed: Path
    ) -> None:
        out = tmp_path / "site"
        site.build(_REPORT, out, generated="2026-09-07", disputes=disputes.load(filed))
        other = (out / "institution" / "1" / "index.html").read_text(encoding="utf-8")

        assert "Disputed by this institution" not in other

    def test_the_methodology_page_points_at_the_channel(self, tmp_path: Path) -> None:
        out = tmp_path / "site"
        site.build(_REPORT, out, generated="2026-09-07")
        page = (out / "methodology" / "index.html").read_text(encoding="utf-8")

        assert "you can say so on your own page" in page
        assert "<code>disputes/</code>" in page


class TestThePublishedSchema:
    def test_the_committed_schema_is_what_the_code_generates(self) -> None:
        committed = json.loads((_ROOT / disputes.SCHEMA_PATH).read_text(encoding="utf-8"))

        assert committed == disputes.schema()

    def test_the_schema_requires_exactly_the_keys_the_parser_requires(self) -> None:
        """Two definitions of the same contract is one too many."""
        schema = disputes.schema()

        assert set(schema["required"]) == set(schema["properties"])
        assert set(schema["required"]) == set(_VALID)

    def test_the_schema_enumerates_the_five_states_and_no_others(self) -> None:
        enum = disputes.schema()["properties"]["disputed_classification"]["enum"]

        assert enum == sorted(CLASSIFICATIONS)
        assert len(enum) == 5

    def test_the_schema_refuses_extra_properties_the_way_the_parser_does(self) -> None:
        assert disputes.schema()["additionalProperties"] is False

    def test_the_build_serves_it_at_the_address_its_identifier_names(self, tmp_path: Path) -> None:
        out = tmp_path / "site"
        site.build(_REPORT, out, origin="https://example.test", generated="x")

        published = json.loads((out / disputes.SCHEMA_PATH).read_text(encoding="utf-8"))

        assert published["$id"] == f"https://example.test/{disputes.SCHEMA_PATH}"


class TestTheCommandRefusesRatherThanPartRendering:
    def _report_file(self, tmp_path: Path) -> Path:
        path = tmp_path / "report.json"
        path.write_text(json.dumps(_REPORT), encoding="utf-8")
        return path

    def test_the_site_build_refuses_a_dispute_it_cannot_read(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        directory = tmp_path / "disputes"
        _write(directory, {**_VALID, "filed": "whenever"})

        code = cli.main(
            [
                "site",
                "--report",
                str(self._report_file(tmp_path)),
                "--disputes-from",
                str(directory),
                "--out",
                str(tmp_path / "site"),
                "--generated",
                "x",
            ]
        )

        assert code == 1
        assert "refusing to build" in capsys.readouterr().err
        assert not (tmp_path / "site").exists()

    def test_the_site_build_refuses_a_dispute_about_a_finding_it_does_not_make(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        directory = tmp_path / "disputes"
        _write(directory, {**_VALID, "unit_id": "9"}, name="9")

        code = cli.main(
            [
                "site",
                "--report",
                str(self._report_file(tmp_path)),
                "--disputes-from",
                str(directory),
                "--out",
                str(tmp_path / "site"),
                "--generated",
                "x",
            ]
        )

        assert code == 1
        assert "does not grade" in capsys.readouterr().err

    def test_the_site_build_renders_a_valid_one(
        self, tmp_path: Path, filed: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        out = tmp_path / "site"

        code = cli.main(
            [
                "site",
                "--report",
                str(self._report_file(tmp_path)),
                "--disputes-from",
                str(filed),
                "--out",
                str(out),
                "--generated",
                "x",
            ]
        )
        capsys.readouterr()

        assert code == 0
        assert "Disputed by this institution" in (
            out / "institution" / "2" / "index.html"
        ).read_text(encoding="utf-8")

    def test_the_export_refuses_a_dispute_about_a_finding_it_does_not_make(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        directory = tmp_path / "disputes"
        _write(directory, {**_VALID, "unit_id": "9"}, name="9")
        out = tmp_path / "dataset.csv"

        code = cli.main(
            [
                "dataset",
                "--report",
                str(self._report_file(tmp_path)),
                "--out",
                str(out),
                "--disputes-from",
                str(directory),
            ]
        )

        assert code == 1
        assert "refusing to export" in capsys.readouterr().err
        assert not out.exists()


class TestTheOrderIsAPropertyOfTheData:
    def test_disputes_load_in_a_fixed_order(self, tmp_path: Path) -> None:
        """Adding one changes the page it is about and nothing else."""
        directory = tmp_path / "disputes"
        _write(directory, {**_VALID, "unit_id": "2", "field": "In-state tuition"}, name="2")
        _write(directory, {**_VALID, "unit_id": "1", "disputed_classification": "reported"})

        loaded = disputes.load(directory)

        assert [(d.unit_id, d.field) for d in loaded] == [
            ("1", _FIELD),
            ("2", "In-state tuition"),
        ]

    def test_grouping_keys_on_the_unit_the_page_is_for(self) -> None:
        one = disputes.Dispute(**_VALID)
        two = disputes.Dispute(**{**_VALID, "field": "In-state tuition"})

        assert disputes.by_institution([one, two]) == {"2": [one, two]}
