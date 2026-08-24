"""The evidence store agrees with every artifact the project already publishes.

It is built by the same grading code from the same committed inputs, so the agreement is not a
coincidence to be hoped for; it is a property to be pinned. The census figures in the README,
the IPEDS grades in ``data/crosscheck.json``, the drift series in ``data/snapshots/``, and the
one contradiction the README names all have to be findable here, under the ids the narration
will cite.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from disclosed.ask import evidence as ev
from disclosed.fields import IPEDS_FIELDS, field_by_key

ROOT = Path(__file__).resolve().parent.parent


class TestTheFrame:
    def test_both_scorecard_snapshots_and_all_three_ipeds_years_are_present(
        self, evidence: ev.Evidence
    ) -> None:
        assert evidence.snapshots(ev.SCORECARD) == ["2026-08-05", "2026-08-21"]
        assert evidence.snapshots(ev.IPEDS) == ["2021", "2022", "2023"]

    def test_the_census_snapshot_covers_every_institution_the_readme_counts(
        self, evidence: ev.Evidence
    ) -> None:
        census = {
            r.unit_id
            for inst in evidence.institutions.values()
            for r in inst.records
            if r.source == ev.SCORECARD and r.snapshot == "2026-08-21"
        }
        assert len(census) == 6273
        assert evidence.built_from["scorecard_census"]["institutions"] == 6273
        assert evidence.built_from["scorecard_sample"]["institutions"] == 600

    def test_the_readme_admission_rate_figure_is_re_derived(self, evidence: ev.Evidence) -> None:
        """4,363 of 6,273 publish no admission rate at all, per README and PR #29."""
        missing = sum(
            1
            for inst in evidence.institutions.values()
            for r in inst.records
            if r.source == ev.SCORECARD
            and r.snapshot == "2026-08-21"
            and r.field_label == "Admission rate"
            and r.classification == "missing"
        )
        assert missing == 4363

    def test_implausible_records_carry_their_value_and_nothing_else_does(
        self, evidence: ev.Evidence
    ) -> None:
        seen_implausible = False
        for inst in evidence.institutions.values():
            for r in inst.records:
                if r.classification == "implausible":
                    seen_implausible = True
                    assert "implausible_value" in r.as_dict()
                else:
                    assert "implausible_value" not in r.as_dict()
                    assert r.implausible_value is None
        assert seen_implausible

    def test_no_reported_value_is_carried_anywhere(self, evidence: ev.Evidence) -> None:
        """The one control that does not depend on a prompt: a reported value is not in the
        store, so it cannot be narrated, compared, or ranked."""
        for inst in evidence.institutions.values():
            for r in inst.records:
                as_dict = r.as_dict()
                assert "raw" not in as_dict and "value" not in as_dict

    def test_the_committed_data_has_no_suppressed_record(self, evidence: ev.Evidence) -> None:
        """Stated, because the narration says so and the fidelity evaluation has to construct
        its suppressed cases. If a future capture carries one, this test and that sentence in
        lookup._notes move together."""
        assert not any(
            r.classification == "suppressed"
            for inst in evidence.institutions.values()
            for r in inst.records
        )


class TestAgreementWithTheCrosscheck:
    def test_every_ipeds_year_reproduces_the_committed_snapshot_counts(
        self, evidence: ev.Evidence
    ) -> None:
        """``data/crosscheck.json`` itself is regenerable and not committed; the per-field counts
        it reduces to are, three times over, and every one has to come out of this store."""
        for year in ev.IPEDS_YEARS:
            committed = json.loads(
                (ROOT / "data" / "snapshots" / "ipeds" / f"{year}.json").read_text("utf-8")
            )
            reported: dict[str, int] = {}
            missing: dict[str, int] = {}
            applicable: dict[str, int] = {}
            institutions: set[str] = set()
            for inst in evidence.institutions.values():
                for r in inst.records:
                    if r.source != ev.IPEDS or r.snapshot != str(year):
                        continue
                    institutions.add(r.unit_id)
                    reported.setdefault(r.field_label, 0)
                    missing.setdefault(r.field_label, 0)
                    applicable.setdefault(r.field_label, 0)
                    if r.classification == "reported":
                        reported[r.field_label] += 1
                    elif r.classification == "missing":
                        missing[r.field_label] += 1
                    if r.classification not in ("suppressed", "not_applicable"):
                        applicable[r.field_label] += 1
            assert len(institutions) == committed["institutions"], year
            assert reported == committed["reported"], year
            assert missing == committed["missing"], year
            assert applicable == committed["applicable"], year

    def test_the_national_artifact_counts_come_out_of_the_store(
        self, evidence: ev.Evidence
    ) -> None:
        national = json.loads((ROOT / "data" / "national.json").read_text("utf-8"))
        for row in national["fields"]:
            records = [
                r
                for inst in evidence.institutions.values()
                for r in inst.records
                if r.source == ev.IPEDS and r.snapshot == "2023" and r.field_label == row["label"]
            ]
            assert sum(1 for r in records if r.classification == "missing") == row["missing"]
            assert (
                sum(1 for r in records if r.classification not in ("suppressed", "not_applicable"))
                == row["applicable"]
            )

    def test_the_sample_snapshot_matches_data_report_json(self, evidence: ev.Evidence) -> None:
        report = json.loads((ROOT / "data" / "report.json").read_text(encoding="utf-8"))
        for row in report["grades"]:
            ours = {
                r.field_label: r.classification
                for r in evidence.for_institution(row["unit_id"], source=ev.SCORECARD)
                if r.snapshot == "2026-08-05"
            }
            assert ours == row["fields"], row["unit_id"]

    def test_the_readme_contradiction_is_a_record(self, evidence: ev.Evidence) -> None:
        gcu = evidence.institutions["104717"]
        (found,) = gcu.contradictions
        assert found.attribute == "Sector"
        assert found.scorecard_value == "private nonprofit (2)"
        assert found.ipeds_value == "private for-profit (3)"
        assert evidence.record(found.id) is found

    def test_contradictions_are_computed_over_the_census_not_the_sample(
        self, evidence: ev.Evidence
    ) -> None:
        """The README names one disagreement in 600; the census finds more. The count is pinned
        so a change in either source shows up here rather than being discovered in an answer."""
        total = sum(len(i.contradictions) for i in evidence.institutions.values())
        assert total == 15


class TestNotApplicableReasons:
    def test_every_ipeds_not_applicable_record_names_the_condition(
        self, evidence: ev.Evidence
    ) -> None:
        for inst in evidence.institutions.values():
            for r in inst.records:
                if r.source == ev.IPEDS and r.classification == "not_applicable":
                    assert r.not_applicable_because, r.id
                else:
                    assert r.not_applicable_because is None, r.id

    def test_the_reason_follows_the_rule_in_the_order_the_rule_checks(self) -> None:
        npc = field_by_key("ipeds.NPRICURL")
        ath = field_by_key("ipeds.ATHURL")
        office = {"ipeds.INSTCAT": "-2", "ipeds.CYACTIVE": "1"}
        assert "administrative unit" in (ev._why_not_applicable(npc, office) or "")
        closed = {"ipeds.INSTCAT": "2", "ipeds.CYACTIVE": "3"}
        assert "active" in (ev._why_not_applicable(npc, closed) or "")
        graduate_only = {"ipeds.INSTCAT": "1", "ipeds.CYACTIVE": "1", "ipeds.UGOFFER": "2"}
        assert "undergraduate" in (ev._why_not_applicable(npc, graduate_only) or "")
        no_aid = {"ipeds.CYACTIVE": "1", "ipeds.UGOFFER": "1", "ipeds.PSET4FLG": "2"}
        assert "Title IV" in (ev._why_not_applicable(npc, no_aid) or "")
        no_team = {"ipeds.CYACTIVE": "1", "ipeds.PSET4FLG": "1", "ipeds.ATHASSOC": "2"}
        assert "athletic association" in (ev._why_not_applicable(ath, no_team) or "")

    def test_a_field_without_a_rule_has_no_reason(self) -> None:
        assert ev._why_not_applicable(field_by_key("latest.student.size"), {}) is None

    def test_a_rule_that_excluded_for_an_unlisted_reason_still_says_so(self) -> None:
        """Unreachable through the rules as written, but the fallback must not be silence."""
        unlisted = next(f for f in IPEDS_FIELDS if f.key == "ipeds.WEBADDR")
        active = {"ipeds.INSTCAT": "2", "ipeds.CYACTIVE": "1"}
        assert ev._why_not_applicable(unlisted, active) == (
            "the field's applicability rule excluded the institution"
        )


class TestDrift:
    def test_the_one_systemic_movement_in_three_years_is_the_athletics_disclosure(
        self, evidence: ev.Evidence
    ) -> None:
        systemic = [d for d in evidence.drift if d.is_systemic]
        assert [(d.field_label, d.earlier, d.later) for d in systemic] == [
            ("Equity in athletics disclosure", "2021", "2023")
        ]
        assert systemic[0].direction == "gained"
        assert evidence.record(systemic[0].id) is systemic[0]

    def test_direction_is_the_projects_not_recomputed(self, evidence: ev.Evidence) -> None:
        for d in evidence.drift:
            if d.rate_change is not None:
                if d.rate_change > 0:
                    expected = "gained"
                elif d.rate_change < 0:
                    expected = "lost"
                else:
                    expected = "unchanged"
                assert d.direction == expected

    def test_sources_are_never_mixed(self, evidence: ev.Evidence) -> None:
        for d in evidence.drift:
            assert d.source in (ev.IPEDS, ev.SCORECARD)
            assert d.id.startswith(f"drift:{'ipeds' if d.source == ev.IPEDS else 'scorecard'}:")
        assert evidence.drift_for(source=ev.IPEDS)
        # One daily Scorecard snapshot is committed, so there is no Scorecard pair yet.
        assert evidence.drift_for(source=ev.SCORECARD) == []

    def test_drift_for_filters_by_field(self, evidence: ev.Evidence) -> None:
        only = evidence.drift_for(field_label="Net price calculator")
        assert only and all(d.field_label == "Net price calculator" for d in only)

    def test_two_snapshots_make_one_pair_and_three_make_three(self, tmp_path: Path) -> None:
        def write(taken: str, reported: int) -> None:
            (tmp_path / f"{taken}.json").write_text(
                json.dumps(
                    {
                        "taken": taken,
                        "institutions": 10,
                        "reported": {"F": reported},
                        "missing": {"F": 10 - reported},
                        "applicable": {"F": 10},
                        "source": "IPEDS directory",
                    }
                )
            )

        write("2001", 5)
        write("2002", 6)
        assert len(list(ev._drift_records(tmp_path, source=ev.IPEDS))) == 1
        write("2003", 7)
        assert len(list(ev._drift_records(tmp_path, source=ev.IPEDS))) == 3


class TestLookup:
    def test_find_by_unit_id_exact_name_and_words(self, evidence: ev.Evidence) -> None:
        assert [i.unit_id for i in evidence.find("166027")] == ["166027"]
        assert [i.name for i in evidence.find("harvard university")] == ["Harvard University"]
        assert [i.name for i in evidence.find("Grand Canyon")] == ["Grand Canyon University"]
        assert evidence.find("Hogwarts") == []
        assert evidence.find("   ") == []

    def test_a_common_word_matches_many_and_that_is_the_callers_problem(
        self, evidence: ev.Evidence
    ) -> None:
        assert len(evidence.find("University of Alabama")) > 1

    def test_for_institution_filters_and_unknown_is_empty(self, evidence: ev.Evidence) -> None:
        assert evidence.for_institution("no-such-id") == []
        only = evidence.for_institution(
            "166027", source=ev.IPEDS, field_labels=["Net price calculator"]
        )
        assert {r.snapshot for r in only} == {"2021", "2022", "2023"}
        assert all(r.field_label == "Net price calculator" for r in only)

    def test_record_resolves_every_kind_and_nothing_else(self, evidence: ev.Evidence) -> None:
        inst = evidence.institutions["166027"]
        assert evidence.record(inst.records[0].id) is inst.records[0]
        assert evidence.record("made:up") is None
        assert inst.fields(ev.IPEDS) == sorted(f.label for f in IPEDS_FIELDS)

    def test_a_record_without_a_unit_id_is_dropped(self) -> None:
        pairs = list(
            ev._classify_records(
                [{"school.name": "Nameless"}], source=ev.SCORECARD, snapshot="x", fields=()
            )
        )
        assert pairs == []

    def test_snapshot_date_falls_back_when_provenance_has_none(self) -> None:
        assert ev._scorecard_snapshot_date(None, "fallback") == "fallback"
        assert ev._scorecard_snapshot_date({"finished_at": ""}, "fallback") == "fallback"
        assert ev._scorecard_snapshot_date({"finished_at": "2026-08-21T11:14:50Z"}, "x") == (
            "2026-08-21"
        )


@pytest.mark.parametrize("unit_id", ["104717", "110468"])
def test_named_institutions_have_records_in_every_snapshot(
    evidence: ev.Evidence, unit_id: str
) -> None:
    inst = evidence.institutions[unit_id]
    assert inst.snapshots(ev.SCORECARD) == ["2026-08-05", "2026-08-21"]
    assert inst.snapshots(ev.IPEDS) == ["2021", "2022", "2023"]
