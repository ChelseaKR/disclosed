"""The policy layer: what is served, what is refused, and that the refusals are fixed text."""

from __future__ import annotations

from typing import Any

import pytest

from disclosed.ask import lookup
from disclosed.ask.corpus import Corpus
from disclosed.ask.evidence import IPEDS, SCORECARD, Evidence
from disclosed.ask.structure import Question
from disclosed.fields import ALL_FIELDS


def _q(**overrides: Any) -> Question:
    base: dict[str, Any] = {
        "text": "q",
        "intent": "what_is_not_reported",
        "institution_text": None,
        "field_labels": (),
        "unmapped_terms": (),
        "source": "either",
        "asks_for_judgement": False,
    }
    base.update(overrides)
    return Question(**base)


class TestRefusals:
    def test_every_refusal_is_fixed_text_about_disclosure(self) -> None:
        assert set(lookup.REFUSALS) == {
            "performance_or_ranking",
            "outside_disclosure",
            "institution_not_named",
            "institution_not_in_frame",
            "institution_ambiguous",
            "field_not_classified",
            "unclear",
        }
        assert "not how they perform" in lookup.REFUSALS["performance_or_ranking"]
        assert "disclosure grade" in lookup.REFUSALS["performance_or_ranking"]

    def test_judgement_is_refused_with_no_records_but_a_true_pointer(
        self, evidence: Evidence, corpus: Corpus
    ) -> None:
        pack = lookup.assemble(
            _q(intent="performance_or_ranking", institution_text="Grand Canyon University"),
            evidence,
            corpus,
        )
        assert pack.refusal is not None and pack.refusal.code == "performance_or_ranking"
        assert pack.records == () and pack.quotables == ()
        assert pack.institution is not None and pack.institution.unit_id == "104717"
        (pointer,) = pack.refusal.known
        assert pointer.startswith("Grand Canyon University: of 12 graded fields")
        assert "reported" in pointer

    def test_judgement_embedded_in_a_served_intent_is_still_refused(
        self, evidence: Evidence, corpus: Corpus
    ) -> None:
        pack = lookup.assemble(
            _q(intent="what_is_not_reported", institution_hint="104717", asks_for_judgement=True),
            evidence,
            corpus,
        )
        assert pack.refusal is not None and pack.refusal.code == "performance_or_ranking"

    def test_judgement_about_an_unknown_institution_has_no_pointer(
        self, evidence: Evidence, corpus: Corpus
    ) -> None:
        pack = lookup.assemble(
            _q(intent="performance_or_ranking", institution_text="Hogwarts"), evidence, corpus
        )
        assert pack.refusal is not None and pack.refusal.known == ()

    @pytest.mark.parametrize("intent", ["outside_disclosure", "unclear"])
    def test_out_of_scope_and_unclear_are_refused_before_resolution(
        self, intent: str, evidence: Evidence, corpus: Corpus
    ) -> None:
        pack = lookup.assemble(
            _q(intent=intent, institution_text="Harvard University"), evidence, corpus
        )
        assert pack.refusal is not None and pack.refusal.code == intent
        assert pack.institution is None

    def test_an_institution_question_with_no_institution(
        self, evidence: Evidence, corpus: Corpus
    ) -> None:
        pack = lookup.assemble(_q(intent="did_anything_change"), evidence, corpus)
        assert pack.refusal is not None and pack.refusal.code == "institution_not_named"

    def test_not_in_frame_by_name_and_by_hint(self, evidence: Evidence, corpus: Corpus) -> None:
        by_name = lookup.assemble(_q(institution_text="Hogwarts"), evidence, corpus)
        by_hint = lookup.assemble(_q(institution_hint="000000"), evidence, corpus)
        for pack in (by_name, by_hint):
            assert pack.refusal is not None and pack.refusal.code == "institution_not_in_frame"

    def test_ambiguous_names_list_candidates_with_unit_ids(
        self, evidence: Evidence, corpus: Corpus
    ) -> None:
        pack = lookup.assemble(_q(institution_text="University of Alabama"), evidence, corpus)
        assert pack.refusal is not None and pack.refusal.code == "institution_ambiguous"
        assert any("100751" in line for line in pack.refusal.known)
        assert len(pack.refusal.known) <= lookup._MAX_CANDIDATES + 1

    def test_a_very_common_name_says_how_many_more(
        self, evidence: Evidence, corpus: Corpus
    ) -> None:
        pack = lookup.assemble(_q(institution_text="college"), evidence, corpus)
        assert pack.refusal is not None and pack.refusal.known[-1].startswith("and ")

    def test_an_unclassified_measure_is_refused_with_the_list(
        self, evidence: Evidence, corpus: Corpus
    ) -> None:
        pack = lookup.assemble(
            _q(institution_hint="166027", unmapped_terms=("retention rate",)), evidence, corpus
        )
        assert pack.refusal is not None and pack.refusal.code == "field_not_classified"
        assert pack.refusal.known == tuple(f.label for f in ALL_FIELDS)

    def test_an_unclassified_term_beside_a_classified_one_is_served(
        self, evidence: Evidence, corpus: Corpus
    ) -> None:
        pack = lookup.assemble(
            _q(
                institution_hint="166027",
                field_labels=("Admission rate",),
                unmapped_terms=("retention rate",),
            ),
            evidence,
            corpus,
        )
        assert pack.refusal is None
        assert {r.field_label for r in pack.records} == {"Admission rate"}


class TestServedPacks:
    def test_what_is_not_reported_is_the_latest_snapshot_of_each_source(
        self, evidence: Evidence, corpus: Corpus
    ) -> None:
        pack = lookup.assemble(_q(institution_hint="104717"), evidence, corpus)
        assert pack.refusal is None
        assert {(r.source, r.snapshot) for r in pack.records} == {
            (SCORECARD, "2026-08-21"),
            (IPEDS, "2023"),
        }
        assert len(pack.records) == 12
        assert len(pack.notes) == 3  # two snapshot notes, one "no suppressed" note

    def test_a_source_restriction_is_honoured(self, evidence: Evidence, corpus: Corpus) -> None:
        pack = lookup.assemble(_q(institution_hint="104717", source=IPEDS), evidence, corpus)
        assert {r.source for r in pack.records} == {IPEDS}
        pack = lookup.assemble(_q(institution_hint="104717", source=SCORECARD), evidence, corpus)
        assert {r.source for r in pack.records} == {SCORECARD}

    def test_changes_carry_every_snapshot_and_national_drift_for_named_fields(
        self, evidence: Evidence, corpus: Corpus
    ) -> None:
        pack = lookup.assemble(
            _q(
                intent="did_anything_change",
                institution_hint="104717",
                field_labels=("Equity in athletics disclosure",),
            ),
            evidence,
            corpus,
        )
        assert {r.snapshot for r in pack.records} == {"2021", "2022", "2023"}
        assert pack.drift and all(
            d.field_label == "Equity in athletics disclosure" for d in pack.drift
        )

    def test_is_value_real_without_a_field_is_the_implausible_records(
        self, evidence: Evidence, corpus: Corpus
    ) -> None:
        pack = lookup.assemble(
            _q(intent="is_value_real", institution_hint="110468"), evidence, corpus
        )
        assert pack.records and all(r.classification == "implausible" for r in pack.records)
        assert all(r.implausible_value == 0 for r in pack.records)

    def test_is_value_real_with_a_field_carries_its_definitions(
        self, evidence: Evidence, corpus: Corpus
    ) -> None:
        pack = lookup.assemble(
            _q(intent="is_value_real", institution_hint="110468", field_labels=("Admission rate",)),
            evidence,
            corpus,
        )
        assert [q.definition.role for q in pack.quotables] == ["defines", "related"]

    def test_why_absent_works_with_and_without_an_institution(
        self, evidence: Evidence, corpus: Corpus
    ) -> None:
        general = lookup.assemble(
            _q(intent="why_absent", field_labels=("Net price calculator",)), evidence, corpus
        )
        assert general.refusal is None and general.records == () and general.quotables
        specific = lookup.assemble(
            _q(
                intent="why_absent",
                institution_hint="104717",
                field_labels=("Net price calculator",),
            ),
            evidence,
            corpus,
        )
        assert len(specific.records) == 1 and specific.quotables

    def test_field_definition_quotes_the_defining_passage_first(
        self, evidence: Evidence, corpus: Corpus
    ) -> None:
        pack = lookup.assemble(
            _q(intent="field_definition", field_labels=("Completion rate, 150% of normal time",)),
            evidence,
            corpus,
        )
        assert pack.quotables[0].definition.role == "defines"
        assert pack.quotables[0].passage.id == "scorecard-data-dictionary:C150_4"
        assert pack.quotables[1].definition.note

    def test_drift_in_a_field_is_national_and_source_scoped(
        self, evidence: Evidence, corpus: Corpus
    ) -> None:
        pack = lookup.assemble(
            _q(
                intent="drift_in_a_field",
                field_labels=("Equity in athletics disclosure",),
                source=IPEDS,
            ),
            evidence,
            corpus,
        )
        assert pack.institution is None and pack.records == ()
        assert len(pack.drift) == 3 and {d.source for d in pack.drift} == {IPEDS}
        everything = lookup.assemble(_q(intent="drift_in_a_field"), evidence, corpus)
        assert len(everything.drift) == len(evidence.drift)

    def test_sources_disagree_is_the_contradiction_records(
        self, evidence: Evidence, corpus: Corpus
    ) -> None:
        pack = lookup.assemble(
            _q(intent="sources_disagree", institution_hint="104717"), evidence, corpus
        )
        assert len(pack.contradictions) == 1 and pack.contradictions[0].attribute == "Sector"
        none = lookup.assemble(
            _q(intent="sources_disagree", institution_hint="166027"), evidence, corpus
        )
        assert none.refusal is None and none.contradictions == ()

    def test_the_prompt_view_carries_only_citable_things(
        self, evidence: Evidence, corpus: Corpus
    ) -> None:
        pack = lookup.assemble(
            _q(intent="is_value_real", institution_hint="110468", field_labels=("Admission rate",)),
            evidence,
            corpus,
        )
        shown = pack.for_prompt()
        ids = {r["id"] for r in shown["records"]}
        ids.update(d["passage_id"] for d in shown["definitions"])
        ids.update(n["id"] for n in shown["notes"])
        assert ids == pack.citable_ids()
        assert shown["institution"] == {
            "unit_id": "110468",
            "name": "Alliant International University-San Diego",
            "state": "CA",
        }

    def test_a_pack_with_nothing_has_no_institution_in_the_prompt(self) -> None:
        pack = lookup.Pack(question=_q())
        assert pack.for_prompt()["institution"] is None
        assert pack.citable_ids() == frozenset()
