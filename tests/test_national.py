"""Coverage claims, and the rule that a sample figure never gets published as a national one.

Everything else in this project guards against an absence being rendered as a number. This module
guards against the other scale error: a percentage computed from 600 institutions in 13 states
being read as a fact about the country. The two failures have the same shape — a qualifier that
was true where the number was computed and got lost on the way to the page.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from disclosed import cli, national, site
from disclosed.fields import IPEDS_FIELDS
from disclosed.scope import NATIONAL, SAMPLE, Scope, scope_from_payload
from disclosed.site import national_page

_NATIONAL_SCOPE: dict[str, Any] = {
    "kind": NATIONAL,
    "source": "IPEDS directory",
    "institutions": 4,
    "states": 2,
    "universe": 4,
    "coverage": 1.0,
    "note": "The complete IPEDS institutional directory.",
}

_GRADES: list[dict[str, Any]] = [
    {
        "unit_id": "1",
        "name": "Publishes Everything College",
        "state": "CA",
        "fields": {
            "Net price calculator": "reported",
            "Equity in athletics disclosure": "reported",
            "Financial aid information": "reported",
        },
    },
    {
        "unit_id": "2",
        "name": "No Calculator University",
        "state": "CA",
        "fields": {
            "Net price calculator": "missing",
            "Equity in athletics disclosure": "not_applicable",
            "Financial aid information": "missing",
        },
    },
    {
        "unit_id": "3",
        "name": "Graduate Only Institute",
        "state": "NY",
        "fields": {
            "Net price calculator": "not_applicable",
            "Equity in athletics disclosure": "not_applicable",
            "Financial aid information": "reported",
        },
    },
    {
        "unit_id": None,
        "name": None,
        "state": None,
        "fields": {
            "Net price calculator": "suppressed",
            "Equity in athletics disclosure": "missing",
            "Financial aid information": "reported",
        },
    },
]

_REPORT: dict[str, Any] = {
    "scope": _NATIONAL_SCOPE,
    "ungradeable": 1,
    "contradictions": [],
    "grades": _GRADES,
}


class TestScope:
    def test_a_sample_says_it_is_a_sample(self) -> None:
        scope = Scope(
            kind=SAMPLE,
            source="College Scorecard",
            institutions=600,
            states=13,
            universe=6300,
            note="",
        )
        assert not scope.is_national
        assert "not national" in scope.sentence
        assert scope.coverage == pytest.approx(600 / 6300)

    def test_an_unknown_universe_is_not_full_coverage(self) -> None:
        """A run that cannot say how much it holds has not established that it holds all of it."""
        scope = Scope(kind=SAMPLE, source="x", institutions=600, states=13, universe=None, note="")
        assert scope.coverage is None

    def test_a_universe_of_zero_yields_no_coverage_rather_than_a_division(self) -> None:
        scope = Scope(kind=SAMPLE, source="x", institutions=0, states=0, universe=0, note="")
        assert scope.coverage is None

    def test_a_report_with_no_scope_reads_back_as_no_claim(self) -> None:
        """Not as a default scope. A default is a claim about coverage that nobody made."""
        assert scope_from_payload({}) is None
        assert scope_from_payload({"scope": "national"}) is None
        assert scope_from_payload({"scope": {"kind": "everything", "source": "x"}}) is None
        assert scope_from_payload({"scope": {"kind": NATIONAL}}) is None

    def test_a_scope_survives_the_round_trip_through_json(self) -> None:
        scope = scope_from_payload(json.loads(json.dumps({"scope": _NATIONAL_SCOPE})))
        assert scope is not None
        assert scope.is_national
        assert scope.institutions == 4

    def test_a_missing_universe_reads_back_as_unknown_not_as_zero(self) -> None:
        raw = {"scope": {**_NATIONAL_SCOPE, "universe": None}}
        scope = scope_from_payload(raw)
        assert scope is not None
        assert scope.universe is None
        assert scope.coverage is None


class TestScorecardScopeIsDeclaredNotGuessed:
    def _scope(self, argv: list[str], tmp_path: Path) -> dict[str, Any]:
        out = tmp_path / "report.json"
        assert cli.main([*argv, "--out", str(out)]) == 0
        payload: dict[str, Any] = json.loads(out.read_text())
        scope: dict[str, Any] = payload["scope"]
        return scope

    def test_a_replayed_capture_is_a_sample_however_large_it_is(self, tmp_path: Path) -> None:
        """A capture cannot say what it captured. Size is not evidence of completeness, and
        inferring from it would eventually promote a big sample to a national claim."""
        source = tmp_path / "capture.json"
        source.write_text(json.dumps([{"id": i, "school.state": "CA"} for i in range(500)]))
        scope = self._scope(["grade", "--source", str(source)], tmp_path)
        assert scope["kind"] == SAMPLE
        assert scope["institutions"] == 500

    def test_an_unplaced_institution_is_not_an_extra_state(self, tmp_path: Path) -> None:
        source = tmp_path / "capture.json"
        source.write_text(
            json.dumps([{"id": 1, "school.state": "CA"}, {"id": 2, "school.state": None}])
        )
        assert self._scope(["grade", "--source", str(source)], tmp_path)["states"] == 1


class TestNationalArtifact:
    def test_a_sample_cannot_be_reduced_into_a_national_artifact(self) -> None:
        """There is no correct way to relabel a sample, so the only safe answer is to refuse."""
        sample = {**_REPORT, "scope": {**_NATIONAL_SCOPE, "kind": SAMPLE}}
        with pytest.raises(ValueError, match="did not cover the population"):
            national.build(sample)

    def test_a_report_with_no_scope_at_all_is_refused_too(self) -> None:
        with pytest.raises(ValueError, match="did not cover the population"):
            national.build({"grades": _GRADES})

    def test_suppressed_and_inapplicable_leave_the_denominator(self) -> None:
        (calculator, athletics, aid) = national.coverage_for(
            _GRADES,
            fields=tuple(
                f
                for f in IPEDS_FIELDS
                if f.label
                in {
                    "Net price calculator",
                    "Equity in athletics disclosure",
                    "Financial aid information",
                }
            ),
        )
        # One reported, one missing, one not applicable, one suppressed.
        assert (calculator.applicable, calculator.reported, calculator.missing) == (2, 1, 1)
        assert calculator.suppressed == 1 and calculator.not_applicable == 1
        # Two of four institutions had no athletics programme at all.
        assert (athletics.applicable, athletics.missing) == (2, 1)
        assert (aid.applicable, aid.reported, aid.missing) == (4, 3, 1)

    def test_a_field_nobody_had_to_answer_has_no_rate_rather_than_a_zero(self) -> None:
        """0% says every institution it reached failed. That is the opposite claim."""
        rows = [{"unit_id": "1", "fields": {"Net price calculator": "not_applicable"}}]
        (coverage,) = national.coverage_for(
            rows, fields=tuple(f for f in IPEDS_FIELDS if f.label == "Net price calculator")
        )
        assert coverage.applicable == 0
        assert coverage.share_reported is None
        assert coverage.as_dict()["share_reported"] is None

    def test_a_field_absent_from_a_row_is_not_counted_as_a_failure(self) -> None:
        rows = [{"unit_id": "1", "fields": {}}]
        (coverage,) = national.coverage_for(
            rows, fields=tuple(f for f in IPEDS_FIELDS if f.label == "Net price calculator")
        )
        assert (coverage.applicable, coverage.missing, coverage.reported) == (0, 0, 0)

    def test_only_statute_backed_fields_name_the_institutions(self) -> None:
        """Naming a college for falling short of a standard nobody enacted is a pillory."""
        gaps = national.named_gaps(_GRADES)
        assert set(gaps) == {"Net price calculator", "Equity in athletics disclosure"}
        assert [g.name for g in gaps["Net price calculator"]] == ["No Calculator University"]

    def test_an_unnamed_institution_stays_unnamed_rather_than_becoming_the_word_none(self) -> None:
        gap = national.named_gaps(_GRADES)["Equity in athletics disclosure"][0]
        assert gap.unit_id is None
        assert gap.name is None
        assert gap.state is None

    def test_the_artifact_is_stable_enough_to_commit_and_diff(self) -> None:
        assert json.dumps(national.build(_REPORT), sort_keys=True) == json.dumps(
            national.build(_REPORT), sort_keys=True
        )


class TestTheNationalPage:
    def _build(self, tmp_path: Path, *, with_national: bool) -> Path:
        report = {
            "scope": {
                "kind": SAMPLE,
                "source": "College Scorecard",
                "institutions": 1,
                "states": 1,
                "universe": 6300,
                "coverage": 1 / 6300,
                "note": "A slice.",
            },
            "institutions": 1,
            "ungradeable": 0,
            "overall": {"label": "all", "graded": 1, "ungradeable": 0, "mean_score": 1.0,
                        "worst_fields": []},
            "by_state": [],
            "implausible": [],
            "grades": [{"unit_id": "1", "name": "A College", "state": "CA", "score": 1.0,
                        "letter": "A", "fields": {}}],
        }
        site.build(
            report,
            tmp_path,
            generated="test",
            national=national.build(_REPORT) if with_national else None,
        )
        return tmp_path

    def test_without_a_national_corpus_the_site_makes_no_national_claim(
        self, tmp_path: Path
    ) -> None:
        """The absence of a national corpus shows up as the absence of national figures, not as
        sample figures with the qualifier quietly dropped."""
        out = self._build(tmp_path, with_national=False)
        assert not (out / "national").exists()
        assert "national page" not in (out / "index.html").read_text(encoding="utf-8")

    def test_the_national_page_states_its_own_coverage(self, tmp_path: Path) -> None:
        out = self._build(tmp_path, with_national=True)
        page = (out / "national" / "index.html").read_text(encoding="utf-8")
        assert "Figures on this page are national" in page
        assert "Every institution in the IPEDS directory" in page

    def test_the_home_page_keeps_saying_it_is_a_sample_and_points_elsewhere(
        self, tmp_path: Path
    ) -> None:
        home = (self._build(tmp_path, with_national=True) / "index.html").read_text(
            encoding="utf-8"
        )
        assert "are not national" in home
        assert 'href="national/"' in home

    def test_the_paragraph_explaining_the_table_reads_its_numbers_off_the_table(
        self, tmp_path: Path
    ) -> None:
        """The two denominators it names are 2023's, and 2023 is not a permanent condition.

        This paragraph used to cite 1,998 and 5,988 as literals sitting directly under a table
        rendered from whatever run was passed in. Every collection year moves both, so the prose
        was one regeneration away from explaining a table using another year's numbers, which is
        the same species of error as a caveat hardcoded into a template.
        """
        labels = [f.label for f in IPEDS_FIELDS]
        athletics = "Equity in athletics disclosure"
        payload = national.build(
            {
                **_REPORT,
                "grades": [
                    {"unit_id": "1", "fields": dict.fromkeys(labels, "reported")},
                    {
                        "unit_id": "2",
                        "fields": {
                            label: "not_applicable" if label == athletics else "reported"
                            for label in labels
                        },
                    },
                ],
            }
        )
        page = national_page(payload).body
        assert "A disclosure that reaches 1 institutions and a disclosure that reaches 2" in page
        assert "1,998" not in page and "5,988" not in page

    def test_when_every_disclosure_reaches_the_same_number_the_sentence_names_none(self) -> None:
        """There is no spread to point at, so the paragraph makes the point without figures
        rather than printing the same number twice as though it were a contrast."""
        labels = [f.label for f in IPEDS_FIELDS]
        payload = national.build(
            {**_REPORT, "grades": [{"unit_id": "1", "fields": dict.fromkeys(labels, "reported")}]}
        )
        page = national_page(payload).body
        assert "Two disclosures that reach different numbers of institutions produce" in page

    def test_a_field_that_reached_nobody_prints_words_not_a_percentage(
        self, tmp_path: Path
    ) -> None:
        payload = national.build(
            {**_REPORT, "grades": [{"unit_id": "1",
                                    "fields": {"Net price calculator": "not_applicable"}}]}
        )
        site.build(
            {"institutions": 0, "ungradeable": 0, "overall": {}, "by_state": [],
             "implausible": [], "grades": [{"unit_id": "1", "name": "x", "state": "CA",
                                            "score": None, "letter": None, "fields": {}}]},
            tmp_path,
            generated="test",
            national=payload,
        )
        page = (tmp_path / "national" / "index.html").read_text(encoding="utf-8")
        main = page.split('id="content">')[1].split("</main>")[0]
        assert "no applicable institutions" in main
        assert "0%" not in main


class TestTheNationalCommand:
    def test_it_refuses_a_sample_and_writes_nothing(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        report = tmp_path / "report.json"
        report.write_text(json.dumps({**_REPORT, "scope": {**_NATIONAL_SCOPE, "kind": SAMPLE}}))
        out = tmp_path / "national.json"
        assert cli.main(["national", "--report", str(report), "--out", str(out)]) == 1
        assert not out.exists()
        assert "national figure for a sample" in capsys.readouterr().err

    def test_it_reduces_a_national_run(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        report = tmp_path / "crosscheck.json"
        report.write_text(json.dumps(_REPORT))
        out = tmp_path / "national.json"
        assert cli.main(["national", "--report", str(report), "--out", str(out)]) == 0
        written = json.loads(out.read_text())
        assert written["scope"]["kind"] == NATIONAL
        assert "Net price calculator" in written["gaps"]
        assert "applicable" in capsys.readouterr().out
