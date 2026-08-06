"""The IPEDS adapter, its sentinel codes, and the cross-source comparison built on top of it.

IPEDS states absence three different ways with three different meanings, all of them negative
integers. Read carelessly, ``-2`` is a perfectly good float, so these tests exist mostly to hold
the line that a code meaning "this question does not apply to us" never becomes a measurement of
minus two.
"""

from __future__ import annotations

import io
import urllib.error
import zipfile
from pathlib import Path
from typing import Any

import pytest

from disclosed.crosswalk import contradictions
from disclosed.disclosure import Disclosure
from disclosed.fields import IPEDS_FIELDS, IPEDS_SENTINELS, field_by_key
from disclosed.grading import grade_institution
from disclosed.sources import ipeds

_HEADER = (
    "UNITID,INSTNM,STABBR,CONTROL,ICLEVEL,SECTOR,INSTCAT,UGOFFER,CYACTIVE,PSET4FLG,"
    "WEBADDR,NPRICURL,FAIDURL,ADMINURL,DISAURL,ATHURL"
)
_CAMPUS = (
    '100654,"Alabama A & M University",AL,1,1,1,2,1,1,1,'
    "www.aamu.edu/,www.aamu.edu/npc,www.aamu.edu/aid,www.aamu.edu/admit,"
    "www.aamu.edu/disability,www.aamu.edu/athletics"
)
# A system office: INSTCAT -2. It admits nobody, so no student-facing disclosure applies to it.
_SYSTEM_OFFICE = '100733,"University of Alabama System Office",AL,1,-3,0,-2,1,1,1, , , , , , '
# Graduate-only: UGOFFER 2. Outside the reach of the net price calculator statute.
_GRADUATE_ONLY = (
    '110699,"University of California-San Francisco",CA,1,1,1,1,2,1,1,'
    "www.ucsf.edu/, ,www.ucsf.edu/aid,www.ucsf.edu/admit,www.ucsf.edu/disability, "
)

_IC_HEADER = "UNITID,ATHASSOC,SPORT1,SPORT2,SPORT3,SPORT4"


def _archive(*rows: str, name: str = "hd2023.csv") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr(name, "﻿" + "\n".join((_HEADER, *rows)) + "\n")
    return buffer.getvalue()


def _characteristics(*rows: str, name: str = "ic2023.csv") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr(name, "﻿" + "\n".join((_IC_HEADER, *rows)) + "\n")
    return buffer.getvalue()


def _joined(directory_rows: tuple[str, ...], characteristics_rows: tuple[str, ...]) -> list[
    dict[str, Any]
]:
    """A directory parsed and joined to a characteristics file, as the grader sees it."""
    return ipeds.merge_characteristics(
        ipeds.parse_directory(_archive(*directory_rows)),
        ipeds.parse_characteristics(_characteristics(*characteristics_rows)),
    )


class TestSentinelCodes:
    def test_the_three_negative_codes_mean_three_different_things(self) -> None:
        """-1, -2 and -3 are not interchangeable, and only one of them counts against anyone."""
        assert IPEDS_SENTINELS["-1"] is Disclosure.MISSING
        assert IPEDS_SENTINELS["-2"] is Disclosure.NOT_APPLICABLE
        assert IPEDS_SENTINELS["-3"] is Disclosure.SUPPRESSED
        assert Disclosure.MISSING.counts_against_publisher
        assert not Disclosure.NOT_APPLICABLE.counts_against_publisher
        assert not Disclosure.SUPPRESSED.counts_against_publisher

    def test_a_sentinel_is_never_graded_as_a_measurement(self) -> None:
        field = field_by_key("ipeds.WEBADDR")
        record: dict[str, object] = {
            "ipeds.INSTCAT": "2", "ipeds.CYACTIVE": "1", "ipeds.WEBADDR": "-2"
        }
        assert field.classify(record) is Disclosure.NOT_APPLICABLE

    def test_minus_two_does_not_normalize_into_a_positive_two(self) -> None:
        """Normalization strips the minus sign, so -2 would collide with a real value of 2."""
        from disclosed.disclosure import classify

        assert classify("-2", sentinels=IPEDS_SENTINELS) is Disclosure.NOT_APPLICABLE
        assert classify("2", sentinels=IPEDS_SENTINELS) is Disclosure.REPORTED
        assert classify(-2, sentinels=IPEDS_SENTINELS) is Disclosure.NOT_APPLICABLE
        assert classify(-2.0, sentinels=IPEDS_SENTINELS) is Disclosure.NOT_APPLICABLE

    def test_without_a_sentinel_map_a_negative_code_stays_a_number(self) -> None:
        """The map is opt-in per field. A -1 in a source that does not use sentinels is data."""
        from disclosed.disclosure import classify

        assert classify(-1, credible_min=-10.0) is Disclosure.REPORTED

    @pytest.mark.parametrize("value", [True, False, [], {}, None, 1.5])
    def test_values_that_cannot_be_a_sentinel_fall_through_to_normal_grading(
        self, value: object
    ) -> None:
        """A bool is not the integer it subclasses, and a non-integral float is a measurement."""
        from disclosed.disclosure import classify

        assert classify(value, sentinels=IPEDS_SENTINELS) is not Disclosure.NOT_APPLICABLE


class TestTextClassification:
    def test_a_non_string_in_a_text_column_is_an_absence(self) -> None:
        from disclosed.disclosure import classify

        assert classify(None, text_is_a_value=True) is Disclosure.MISSING
        assert classify(12, text_is_a_value=True) is Disclosure.MISSING

    def test_an_explicit_suppression_marker_still_suppresses(self) -> None:
        from disclosed.disclosure import classify

        assert classify("PrivacySuppressed", text_is_a_value=True) is Disclosure.SUPPRESSED

    def test_an_explicit_not_applicable_marker_still_leaves_the_denominator(self) -> None:
        from disclosed.disclosure import classify

        assert classify("N/A", text_is_a_value=True) is Disclosure.NOT_APPLICABLE


class TestUrlFieldsAreNotNumericFields:
    def test_a_url_is_a_reported_value_not_an_unparseable_number(self) -> None:
        """Graded on the numeric path, every institution in IPEDS would fail every URL field."""
        field = field_by_key("ipeds.WEBADDR")
        record: dict[str, object] = {
            "ipeds.INSTCAT": "2", "ipeds.CYACTIVE": "1", "ipeds.WEBADDR": "https://www.aamu.edu/"
        }
        assert field.classify(record) is Disclosure.REPORTED

    def test_a_blank_url_column_is_missing(self) -> None:
        field = field_by_key("ipeds.WEBADDR")
        record: dict[str, object] = {
            "ipeds.INSTCAT": "2", "ipeds.CYACTIVE": "1", "ipeds.WEBADDR": "  "
        }
        assert field.classify(record) is Disclosure.MISSING

    def test_a_url_containing_a_marker_word_is_not_read_as_suppression(self) -> None:
        """Substring matching is for numeric columns carrying "PrivacySuppressed" in place of a
        number. A college with a page at /withheld-records has not suppressed anything."""
        field = field_by_key("ipeds.WEBADDR")
        record: dict[str, object] = {
            "ipeds.INSTCAT": "2",
            "ipeds.CYACTIVE": "1",
            "ipeds.WEBADDR": "https://college.edu/withheld-records",
        }
        assert field.classify(record) is Disclosure.REPORTED


class TestApplicability:
    def _graded(self, record: dict[str, Any]) -> dict[str, Disclosure]:
        grade = grade_institution(record, fields=IPEDS_FIELDS)
        return {r.field.label: r.disclosure for r in grade.results}

    def test_a_system_office_is_not_held_to_student_facing_disclosures(self) -> None:
        """"University of Alabama System Office" is a real row that admits nobody. Grading it as
        failing to publish a net price calculator would invent a violation."""
        record = ipeds.parse_directory(_archive(_SYSTEM_OFFICE))[0]
        results = self._graded(record)
        assert all(d is Disclosure.NOT_APPLICABLE for d in results.values())
        assert grade_institution(record, fields=IPEDS_FIELDS).score is None

    def test_an_ungradeable_record_gets_no_grade_rather_than_a_zero(self) -> None:
        record = ipeds.parse_directory(_archive(_SYSTEM_OFFICE))[0]
        grade = grade_institution(record, fields=IPEDS_FIELDS)
        assert grade.score is None
        assert grade.letter is None

    def test_a_graduate_only_institution_owes_no_net_price_calculator(self) -> None:
        """20 U.S.C. 1015a(h)(3) reaches institutions enrolling undergraduates. UCSF has none."""
        record = ipeds.parse_directory(_archive(_GRADUATE_ONLY))[0]
        results = self._graded(record)
        assert results["Net price calculator"] is Disclosure.NOT_APPLICABLE
        # It is still answerable for the disclosures that do apply to it.
        assert results["Institution web address"] is Disclosure.REPORTED

    def test_an_undergraduate_title_iv_campus_is_held_to_all_of_them(self) -> None:
        record = _joined((_CAMPUS,), ("100654,1,1,1,1,1",))[0]
        results = self._graded(record)
        assert all(d is Disclosure.REPORTED for d in results.values())
        assert grade_institution(record, fields=IPEDS_FIELDS).score == 1.0

    def test_a_missing_calculator_at_a_covered_institution_is_a_failure(self) -> None:
        row = _CAMPUS.replace("www.aamu.edu/npc", " ")
        record = ipeds.parse_directory(_archive(row))[0]
        assert self._graded(record)["Net price calculator"] is Disclosure.MISSING

    def test_dropping_the_title_iv_condition_would_inflate_the_finding(self) -> None:
        """An institution taking no federal student aid is outside the statute entirely."""
        row = _CAMPUS.replace(",1,1,1,www.aamu.edu/", ",1,1,2,www.aamu.edu/").replace(
            "www.aamu.edu/npc", " "
        )
        record = ipeds.parse_directory(_archive(row))[0]
        assert record["ipeds.PSET4FLG"] == "2"
        assert self._graded(record)["Net price calculator"] is Disclosure.NOT_APPLICABLE


class TestAthleticsApplicability:
    """The rule that turned an ungradeable column into a finding, and its failure mode.

    Graded against the directory alone, 4,469 of 6,163 institutions have no athletics address and
    almost every one of them simply has no team. The characteristics file supplies the
    institution's own answer about whether it competes, and that answer is the only thing allowed
    to put anyone in this denominator.
    """

    _NO_REPORT = (_CAMPUS.replace("www.aamu.edu/athletics", " "),)

    def _athletics(self, record: dict[str, Any]) -> Disclosure:
        grade = grade_institution(record, fields=IPEDS_FIELDS)
        return next(r.disclosure for r in grade.results if r.field.key == "ipeds.ATHURL")

    def test_a_college_with_no_athletics_programme_owes_nothing(self) -> None:
        """ATHASSOC 2 is a stated "no". It leaves the denominator rather than being marked down."""
        record = _joined(self._NO_REPORT, ("100654,2,2,2,2,2",))[0]
        assert self._athletics(record) is Disclosure.NOT_APPLICABLE

    def test_a_competing_college_with_no_published_report_is_a_gap(self) -> None:
        record = _joined(self._NO_REPORT, ("100654,1,1,1,1,1",))[0]
        assert self._athletics(record) is Disclosure.MISSING

    def test_an_institution_absent_from_the_characteristics_file_is_never_marked_down(self) -> None:
        """Silence about athletics is not a claim to have any. Reading an absent join as a yes
        would put four thousand colleges with no team into the denominator, which is the exact
        shape of the null-versus-zero bug one level up."""
        record = ipeds.merge_characteristics(
            ipeds.parse_directory(_archive(*self._NO_REPORT)), {}
        )[0]
        assert "ipeds.ATHASSOC" not in record
        assert self._athletics(record) is Disclosure.NOT_APPLICABLE

    def test_an_unanswered_athletics_question_is_not_a_yes(self) -> None:
        """-1 is "not reported". It is an absence of an answer, not an answer."""
        record = _joined(self._NO_REPORT, ("100654,-1,-1,-1,-1,-1",))[0]
        assert self._athletics(record) is Disclosure.NOT_APPLICABLE

    def test_an_institution_outside_title_iv_is_outside_the_statute(self) -> None:
        row = _CAMPUS.replace(",1,1,1,www.aamu.edu/", ",1,1,2,www.aamu.edu/").replace(
            "www.aamu.edu/athletics", " "
        )
        record = _joined((row,), ("100654,1,1,1,1,1",))[0]
        assert self._athletics(record) is Disclosure.NOT_APPLICABLE

    def test_a_closed_institution_maintains_no_pages(self) -> None:
        row = _CAMPUS.replace(",2,1,1,1,www.aamu.edu/", ",2,1,0,1,www.aamu.edu/").replace(
            "www.aamu.edu/athletics", " "
        )
        record = _joined((row,), ("100654,1,1,1,1,1",))[0]
        assert record["ipeds.CYACTIVE"] == "0"
        assert self._athletics(record) is Disclosure.NOT_APPLICABLE


class TestTheJoin:
    def test_the_merge_leaves_an_unmatched_record_exactly_as_it_was(self) -> None:
        directory = ipeds.parse_directory(_archive(_CAMPUS))
        merged = ipeds.merge_characteristics(directory, {"999999": {"ipeds.ATHASSOC": "1"}})
        assert merged[0] == directory[0]

    def test_a_characteristics_row_with_no_unit_id_is_dropped_not_keyed_on_blank(self) -> None:
        """Two of them would collide on "" and one would answer for the other."""
        parsed = ipeds.parse_characteristics(_characteristics("100654,1,1,1,1,1", " ,2,2,2,2,2"))
        assert set(parsed) == {"100654"}

    def test_an_empty_characteristics_file_is_a_failure_not_zero_institutions(self) -> None:
        with pytest.raises(ipeds.IpedsError, match="zero institutions"):
            ipeds.parse_characteristics(_characteristics())

    def test_a_characteristics_file_without_unitid_raises(self) -> None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as bundle:
            bundle.writestr("ic.csv", "ATHASSOC\n1\n")
        with pytest.raises(ipeds.IpedsError, match="UNITID"):
            ipeds.parse_characteristics(buffer.getvalue())

    def test_an_unreadable_characteristics_file_fails_the_whole_load(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Falling back to directory-only records would drop the athletics disclosure out of every
        denominator in the country, which on the page is indistinguishable from every institution
        in the country suddenly publishing one."""
        cache = tmp_path / "HD.zip"
        cache.write_bytes(_archive(_CAMPUS))

        def boom(url: str, timeout: float = 0) -> Any:
            raise urllib.error.URLError("connection reset")

        monkeypatch.setattr(ipeds.urllib.request, "urlopen", boom)
        with pytest.raises(ipeds.IpedsError, match="characteristics"):
            ipeds.load_institutions(cache=cache)

    def test_a_cached_characteristics_archive_is_used_without_the_network(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        directory = tmp_path / "HD.zip"
        directory.write_bytes(_archive(_CAMPUS))
        characteristics = tmp_path / "IC.zip"
        characteristics.write_bytes(_characteristics("100654,1,1,1,1,1"))

        def forbidden(url: str, timeout: float = 0) -> Any:
            raise AssertionError("cache hit must not reach the network")

        monkeypatch.setattr(ipeds.urllib.request, "urlopen", forbidden)
        joined = ipeds.load_institutions(cache=directory, characteristics_cache=characteristics)
        assert joined[0]["ipeds.ATHASSOC"] == "1"

    def test_a_characteristics_download_populates_the_cache(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payload = _characteristics("100654,1,1,1,1,1")

        class _Response(io.BytesIO):
            def __enter__(self) -> _Response:
                return self

            def __exit__(self, *exc: object) -> None:
                self.close()

        monkeypatch.setattr(
            ipeds.urllib.request, "urlopen", lambda url, timeout=0: _Response(payload)
        )
        cache = tmp_path / "nested" / "IC2023.zip"
        assert ipeds.load_characteristics(cache=cache)["100654"]["ipeds.ATHASSOC"] == "1"
        assert cache.read_bytes() == payload

    def test_characteristics_url_names_the_collection_year(self) -> None:
        assert ipeds.characteristics_url(2023).endswith("/IC2023.zip")


class TestParsing:
    def test_identity_aliases_join_to_the_scorecard_vocabulary(self) -> None:
        record = ipeds.parse_directory(_archive(_CAMPUS))[0]
        assert record["id"] == "100654"
        assert record["school.name"] == "Alabama A & M University"
        assert record["school.state"] == "AL"
        assert record["school.ownership"] == 1

    def test_an_alias_is_a_rename_and_never_a_coercion(self) -> None:
        """A suppressed control code must stay suppressed rather than becoming a sector."""
        row = _CAMPUS.replace(',AL,1,1,1,2,1,1,1,', ',AL,-3,1,1,2,1,1,1,')
        record = ipeds.parse_directory(_archive(row))[0]
        assert record["school.ownership"] == "-3"
        assert record["ipeds.CONTROL"] == "-3"

    def test_the_bom_does_not_end_up_in_the_first_column_name(self) -> None:
        assert ipeds.parse_directory(_archive(_CAMPUS))[0]["id"] == "100654"

    def test_undecodable_bytes_do_not_fail_the_whole_run(self) -> None:
        """One accented character in one college's name must not cost 6,163 records."""
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as bundle:
            bundle.writestr("hd.csv", (_HEADER + "\n" + _CAMPUS + "\n").encode() + b"\xff\xfe")
        assert len(ipeds.parse_directory(buffer.getvalue())) >= 1

    def test_a_revised_file_does_not_silently_replace_the_original(self) -> None:
        """NCES ships hd2023_rv.csv alongside hd2023.csv; picking by sort keeps reruns stable."""
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as bundle:
            bundle.writestr("hd2023_rv.csv", _HEADER + "\n" + _SYSTEM_OFFICE + "\n")
            bundle.writestr("hd2023.csv", _HEADER + "\n" + _CAMPUS + "\n")
        assert ipeds.parse_directory(buffer.getvalue())[0]["id"] == "100654"

    def test_directory_url_names_the_collection_year(self) -> None:
        assert ipeds.directory_url(2023).endswith("/HD2023.zip")


class TestHonestFailure:
    def test_a_corrupt_archive_raises_rather_than_returning_nothing(self) -> None:
        with pytest.raises(ipeds.IpedsError, match="not a readable zip"):
            ipeds.parse_directory(b"this is not a zip file")

    def test_an_archive_with_no_csv_raises(self) -> None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as bundle:
            bundle.writestr("readme.txt", "nothing here")
        with pytest.raises(ipeds.IpedsError, match="no CSV"):
            ipeds.parse_directory(buffer.getvalue())

    def test_a_directory_without_unitid_cannot_be_joined_and_raises(self) -> None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as bundle:
            bundle.writestr("hd.csv", "INSTNM,STABBR\nSomewhere,CA\n")
        with pytest.raises(ipeds.IpedsError, match="UNITID"):
            ipeds.parse_directory(buffer.getvalue())

    def test_an_empty_directory_is_a_failure_not_zero_institutions(self) -> None:
        with pytest.raises(ipeds.IpedsError, match="zero institutions"):
            ipeds.parse_directory(_archive())

    def test_a_transport_failure_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(url: str, timeout: float = 0) -> Any:
            raise urllib.error.URLError("connection reset")

        monkeypatch.setattr(ipeds.urllib.request, "urlopen", boom)
        with pytest.raises(ipeds.IpedsError, match="unreadable"):
            ipeds.load_directory()


class TestCaching:
    def test_a_cached_archive_is_used_without_touching_the_network(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cache = tmp_path / "HD2023.zip"
        cache.write_bytes(_archive(_CAMPUS))

        def forbidden(url: str, timeout: float = 0) -> Any:
            raise AssertionError("cache hit must not reach the network")

        monkeypatch.setattr(ipeds.urllib.request, "urlopen", forbidden)
        assert ipeds.load_directory(cache=cache)[0]["id"] == "100654"

    def test_a_download_populates_the_cache(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payload = _archive(_CAMPUS)

        class _Response(io.BytesIO):
            def __enter__(self) -> _Response:
                return self

            def __exit__(self, *exc: object) -> None:
                self.close()

        monkeypatch.setattr(
            ipeds.urllib.request, "urlopen", lambda url, timeout=0: _Response(payload)
        )
        cache = tmp_path / "nested" / "HD2023.zip"
        assert ipeds.load_directory(cache=cache)[0]["id"] == "100654"
        assert cache.read_bytes() == payload

    def test_the_iterator_honours_a_limit(self, tmp_path: Path) -> None:
        cache = tmp_path / "HD2023.zip"
        cache.write_bytes(_archive(_CAMPUS, _SYSTEM_OFFICE, _GRADUATE_ONLY))
        assert len(list(ipeds.iter_institutions(cache=cache, limit=2))) == 2
        assert len(list(ipeds.iter_institutions(cache=cache))) == 3


class TestCrossSourceContradictions:
    def _ipeds(self, *rows: str) -> list[dict[str, Any]]:
        return ipeds.parse_directory(_archive(*rows))

    def test_two_federal_sources_disagreeing_is_the_finding(self) -> None:
        """The live case: the Scorecard files Grand Canyon University as private nonprofit and
        IPEDS files it as private for-profit, which is a documented classification dispute rather
        than a rounding difference."""
        scorecard = [
            {"id": 100654, "school.name": "Alabama A & M University",
             "school.state": "AL", "school.ownership": 2}
        ]
        (found,) = contradictions(scorecard, self._ipeds(_CAMPUS))
        assert found.unit_id == "100654"
        assert found.field_label == "Sector"
        assert found.scorecard_value == "private nonprofit (2)"
        assert found.ipeds_value == "public (1)"
        assert found.note

    def test_agreement_across_types_is_not_a_disagreement(self) -> None:
        """The Scorecard sends JSON 1 and IPEDS sends the string "1". Same claim about the world."""
        scorecard = [{"id": 100654, "school.state": "AL", "school.ownership": 1}]
        assert contradictions(scorecard, self._ipeds(_CAMPUS)) == []

    def test_a_sentinel_never_contradicts_anything(self) -> None:
        """29 institutions carry CONTROL -3. Reporting those as disagreements would be this
        project inventing findings out of absences, which is what it exists to stop."""
        row = _CAMPUS.replace(",AL,1,1,1,2,1,1,1,", ",AL,-3,1,1,2,1,1,1,")
        scorecard = [{"id": 100654, "school.state": "AL", "school.ownership": 1}]
        assert contradictions(scorecard, self._ipeds(row)) == []

    def test_an_institution_in_only_one_source_is_not_a_contradiction(self) -> None:
        scorecard = [{"id": 999999, "school.state": "ZZ", "school.ownership": 1}]
        assert contradictions(scorecard, self._ipeds(_CAMPUS)) == []

    def test_a_state_disagreement_is_reported_separately(self) -> None:
        scorecard = [{"id": 100654, "school.state": "CA", "school.ownership": 1}]
        (found,) = contradictions(scorecard, self._ipeds(_CAMPUS))
        assert found.field_label == "State"
        assert (found.scorecard_value, found.ipeds_value) == ("CA", "AL")

    def test_records_with_no_id_are_skipped_rather_than_joined_on_a_placeholder(self) -> None:
        scorecard = [{"id": None, "school.state": "CA", "school.ownership": 3}]
        assert contradictions(scorecard, self._ipeds(_CAMPUS)) == []

    def test_an_ipeds_row_with_no_id_is_left_out_of_the_lookup(self) -> None:
        directory = self._ipeds(_CAMPUS)
        directory[0]["id"] = ""
        scorecard = [{"id": 100654, "school.state": "CA", "school.ownership": 3}]
        assert contradictions(scorecard, directory) == []

    def test_a_source_that_said_nothing_has_not_contradicted_anything(self) -> None:
        scorecard = [{"id": 100654, "school.state": None, "school.ownership": None}]
        assert contradictions(scorecard, self._ipeds(_CAMPUS)) == []

    def test_output_is_sorted_so_it_can_be_committed_and_diffed(self) -> None:
        scorecard = [{"id": 100654, "school.state": "CA", "school.ownership": 3}]
        found = contradictions(scorecard, self._ipeds(_CAMPUS))
        assert [f.field_label for f in found] == ["Sector", "State"]
