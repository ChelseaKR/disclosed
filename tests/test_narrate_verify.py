"""Narration is read strictly; the verifier withholds everything it cannot prove.

The verifier is the control. Each of its five reasons is exercised with a claim written to trip
exactly that reason over real records, and each has a counterpart that should stand, because a
verifier that withholds everything is as useless as one that withholds nothing.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from disclosed.ask import lookup, narrate, verify
from disclosed.ask.corpus import Corpus
from disclosed.ask.evidence import Evidence
from disclosed.ask.provider import FakeProvider
from disclosed.ask.structure import Question


def _q(**overrides: Any) -> Question:
    base: dict[str, Any] = {
        "text": "why?",
        "intent": "why_absent",
        "institution_text": None,
        "field_labels": ("Admission rate",),
        "unmapped_terms": (),
        "source": "either",
        "asks_for_judgement": False,
        "institution_hint": "100690",
    }
    base.update(overrides)
    return Question(**base)


@pytest.fixture
def pack(evidence: Evidence, corpus: Corpus) -> lookup.Pack:
    return lookup.assemble(_q(), evidence, corpus)


def _narration(claims: list[dict[str, Any]], quotes: list[dict[str, Any]] | None = None) -> str:
    return json.dumps({"claims": claims, "quotes": quotes or [], "could_not_answer": ""})


class TestNarrate:
    def test_sends_the_question_and_the_pack_and_reads_the_reply(self, pack: lookup.Pack) -> None:
        rec = pack.records[0].id
        fake = FakeProvider(
            [_narration([{"text": "x", "cites": [rec, rec]}], [{"passage_id": "p", "quote": "q"}])]
        )
        narration = narrate.narrate(pack, fake)
        sent = json.loads(fake.calls[0]["user"])
        assert sent["question"] == "why?"
        assert sent["pack"] == pack.for_prompt()
        assert fake.calls[0]["schema"] == narrate.NARRATE_SCHEMA
        assert narration.claims == (narrate.Claim(text="x", cites=(rec,)),)
        assert narration.quotes == (narrate.Quote(passage_id="p", quote="q"),)
        assert narration.malformed == "" and narration.model == "fake"
        assert set(narration.usage) == {
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_creation_tokens",
        }

    @pytest.mark.parametrize(
        "reply",
        [
            "nope",
            json.dumps({"claims": []}),
            json.dumps(
                {"claims": [{"text": 1, "cites": []}], "quotes": [], "could_not_answer": ""}
            ),
            json.dumps(
                {"claims": [{"text": "t", "cites": "x"}], "quotes": [], "could_not_answer": ""}
            ),
            json.dumps({"claims": [], "quotes": [{"passage_id": 1}], "could_not_answer": ""}),
            json.dumps({"claims": [], "quotes": [], "could_not_answer": None}),
        ],
    )
    def test_anything_outside_the_schema_is_a_malformed_empty_narration(
        self, pack: lookup.Pack, reply: str
    ) -> None:
        narration = narrate.narrate(pack, FakeProvider([reply]))
        assert narration.claims == () and narration.quotes == ()
        assert narration.malformed

    def test_the_system_prompt_states_the_five_states_and_the_refusals(self) -> None:
        for state in ("reported", "implausible", "suppressed", "not_applicable", "missing"):
            assert f"- {state}:" in narrate.NARRATE_SYSTEM
        assert "disclosure grade is not a quality grade" in narrate.NARRATE_SYSTEM
        assert "Never infer direction from the counts" in narrate.NARRATE_SYSTEM
        assert "never paraphrase a federal definition" in narrate.NARRATE_SYSTEM


class TestVerifyClaims:
    def _verify(
        self, pack: lookup.Pack, corpus: Corpus, *claims: dict[str, Any]
    ) -> verify.Verified:
        narration = narrate.narrate(pack, FakeProvider([_narration(list(claims))]))
        return verify.verify(narration, pack, corpus)

    def test_a_grounded_claim_stands(self, pack: lookup.Pack, corpus: Corpus) -> None:
        rec = pack.records[0]
        assert rec.classification == "missing"
        out = self._verify(
            pack,
            corpus,
            {
                "text": f"In the 2026-08-21 snapshot, {rec.institution}'s admission rate is "
                "missing: no value and no stated reason.",
                "cites": [rec.id],
            },
        )
        assert len(out.claims) == 1 and out.withheld_claims == () and out.reasons == {}
        assert out.shown_anything

    def test_uncited_and_foreign_citations_are_withheld(
        self, pack: lookup.Pack, corpus: Corpus
    ) -> None:
        out = self._verify(
            pack,
            corpus,
            {"text": "Something.", "cites": []},
            {"text": "Something else.", "cites": ["scorecard:2026-08-21:166027:enrollment"]},
        )
        assert out.claims == ()
        assert [w.reason for w in out.withheld_claims] == [
            "uncited",
            "cites a record not in the pack",
        ]

    def test_the_wrong_state_word_is_withheld_and_a_contrast_is_not(
        self, pack: lookup.Pack, corpus: Corpus
    ) -> None:
        rec = pack.records[0].id
        out = self._verify(
            pack,
            corpus,
            {"text": "The admission rate is suppressed to protect a small cohort.", "cites": [rec]},
            {"text": "The admission rate is not applicable here.", "cites": [rec]},
            {"text": "The admission rate is missing, not suppressed.", "cites": [rec]},
        )
        assert [c.text for c in out.claims] == ["The admission rate is missing, not suppressed."]
        assert out.reasons == {"names a classification none of its cited records is in": 2}

    @pytest.mark.parametrize(
        "text",
        [
            "Amridge University has no admission rate.",
            "The admission rate is unavailable.",
            "There is no data on admissions.",
            "The institution did not provide an admission rate.",
            "The school lacks an admission rate.",
            "The admission rate is not available for this school.",
        ],
    )
    def test_an_absence_rendered_as_a_non_state_is_withheld(
        self, pack: lookup.Pack, corpus: Corpus, text: str
    ) -> None:
        out = self._verify(pack, corpus, {"text": text, "cites": [pack.records[0].id]})
        assert out.claims == ()
        assert out.withheld_claims[0].reason == "renders an absence as a non-state"

    @pytest.mark.parametrize(
        "text",
        [
            "It is a good school.",
            "You should apply here.",
            "This is the best option in Alabama.",
            "It is a highly selective institution.",
            "It ranks well nationally.",
            "I recommend it.",
        ],
    )
    def test_a_judgement_is_withheld(self, pack: lookup.Pack, corpus: Corpus, text: str) -> None:
        out = self._verify(pack, corpus, {"text": text, "cites": [pack.records[0].id]})
        assert out.claims == ()
        assert out.withheld_claims[0].reason == (
            "contains a judgement of quality or a recommendation"
        )

    def test_a_number_the_model_was_never_given_is_withheld(
        self, pack: lookup.Pack, corpus: Corpus
    ) -> None:
        rec = pack.records[0].id
        out = self._verify(
            pack,
            corpus,
            {"text": "Their admission rate is about 45% and it is missing.", "cites": [rec]},
            {"text": "Median earnings are $52,000.", "cites": [rec]},
            {"text": "In 2026-08-21 (unit id 100690) 1 of 1 fields is missing.", "cites": [rec]},
        )
        assert [c.text for c in out.claims] == [
            "In 2026-08-21 (unit id 100690) 1 of 1 fields is missing."
        ]
        assert out.reasons == {"contains a number not in its cited records": 2}
        assert "45" in out.withheld_claims[0].reason

    def test_an_implausible_value_may_be_stated_as_the_artifact_it_is(
        self, evidence: Evidence, corpus: Corpus
    ) -> None:
        zero = lookup.assemble(
            _q(intent="is_value_real", institution_hint="110468"), evidence, corpus
        )
        rec = next(r for r in zero.records if r.snapshot == "2026-08-21")
        narration = narrate.narrate(
            zero,
            FakeProvider(
                [
                    _narration(
                        [
                            {
                                "text": "The published admission rate of 0 (0%) is classified "
                                "implausible: a reporting artifact, not a measurement.",
                                "cites": [rec.id],
                            }
                        ]
                    )
                ]
            ),
        )
        out = verify.verify(narration, zero, corpus)
        assert len(out.claims) == 1

    def test_drift_numbers_and_direction_words_are_allowed_from_the_cited_record(
        self, evidence: Evidence, corpus: Corpus
    ) -> None:
        drift_pack = lookup.assemble(
            _q(
                intent="drift_in_a_field",
                institution_hint=None,
                field_labels=("Equity in athletics disclosure",),
                source="IPEDS",
            ),
            evidence,
            corpus,
        )
        systemic = next(d for d in drift_pack.drift if d.is_systemic)
        narration = narrate.narrate(
            drift_pack,
            FakeProvider(
                [
                    _narration(
                        [
                            {
                                "text": "In IPEDS, between 2021 and 2023 the share of applicable "
                                "institutions publishing the athletics disclosure gained 2.3 "
                                "percentage points: 1134 of 1986 to 1186 of 1998, a rise of 52 "
                                "reporters.",
                                "cites": [systemic.id],
                            },
                            {"text": "That is a change of 7 points.", "cites": [systemic.id]},
                        ]
                    )
                ]
            ),
        )
        out = verify.verify(narration, drift_pack, corpus)
        assert len(out.claims) == 1 and out.withheld_claims[0].reason.startswith(
            "contains a number"
        )

    def test_a_claim_citing_only_a_note_may_name_a_state(
        self, pack: lookup.Pack, corpus: Corpus
    ) -> None:
        out = self._verify(
            pack, corpus, {"text": "No record here is suppressed.", "cites": ["note:2"]}
        )
        assert len(out.claims) == 1


class TestVerifyQuotes:
    def test_verbatim_quotes_stand_and_paraphrases_do_not(
        self, pack: lookup.Pack, corpus: Corpus
    ) -> None:
        narration = narrate.narrate(
            pack,
            FakeProvider(
                [
                    _narration(
                        [],
                        [
                            {
                                "passage_id": "scorecard-glossary:acceptance-rate",
                                "quote": "Institutions that have an open admissions policy do not "
                                "report on their acceptance rate",
                            },
                            {
                                "passage_id": "scorecard-glossary:acceptance-rate",
                                "quote": "Open-admissions schools never report a rate",
                            },
                            {
                                "passage_id": "ipeds-hd2023-dictionary:NPRICURL",
                                "quote": "Net price calculator web address",
                            },
                        ],
                    )
                ]
            ),
        )
        out = verify.verify(narration, pack, corpus)
        assert len(out.quotes) == 1
        assert [w.reason for w in out.withheld_quotes] == [
            "is not a verbatim quote of the passage",
            "quotes a passage not in the pack",
        ]
        assert out.reasons == {
            "is not a verbatim quote of the passage": 1,
            "quotes a passage not in the pack": 1,
        }

    def test_a_malformed_narration_verifies_to_nothing_and_says_so(
        self, pack: lookup.Pack, corpus: Corpus
    ) -> None:
        out = verify.verify(narrate.narrate(pack, FakeProvider(["garbage"])), pack, corpus)
        assert not out.shown_anything and out.malformed
