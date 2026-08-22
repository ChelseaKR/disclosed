"""Question structuring: the model says what is asked, in the project's vocabulary, or it is
``unclear``. Nothing outside the schema is ever accepted as a question."""

from __future__ import annotations

import json
from typing import Any

import pytest

from disclosed.ask import structure as s
from disclosed.ask.provider import FakeProvider
from disclosed.fields import ALL_FIELDS

_BASE: dict[str, Any] = {
    "intent": "what_is_not_reported",
    "institution_text": None,
    "field_labels": [],
    "unmapped_terms": [],
    "source": "either",
    "asks_for_judgement": False,
    "note": "",
}


def _structured(reply: Any, **kwargs: Any) -> s.Question:
    text = reply if isinstance(reply, str) else json.dumps(reply)
    return s.structure("the question", FakeProvider([text]), **kwargs)


class TestTheSchemaIsTheVocabulary:
    def test_every_graded_label_and_every_intent_is_in_the_schema(self) -> None:
        labels = s.QUESTION_SCHEMA["properties"]["field_labels"]["items"]["enum"]
        assert set(labels) == {f.label for f in ALL_FIELDS}
        assert set(s.QUESTION_SCHEMA["properties"]["intent"]["enum"]) == set(s.INTENTS)
        assert s.QUESTION_SCHEMA["additionalProperties"] is False

    def test_the_system_prompt_names_every_field_and_the_refusal_intent(self) -> None:
        for field in ALL_FIELDS:
            assert f'"{field.label}"' in s.STRUCTURE_SYSTEM
        assert "performance_or_ranking" in s.STRUCTURE_SYSTEM
        assert "When in doubt" in s.STRUCTURE_SYSTEM
        assert s.PROMPT_VERSION


class TestStructuring:
    def test_a_valid_reply_becomes_a_question(self) -> None:
        fake = FakeProvider(
            [
                json.dumps(
                    {
                        **_BASE,
                        "intent": "is_value_real",
                        "institution_text": "Alliant",
                        "field_labels": ["Admission rate", "Admission rate"],
                        "unmapped_terms": ["yield", "yield"],
                        "note": "n",
                    }
                )
            ]
        )
        question = s.structure("Is the 0% real?", fake, institution_hint="110468")
        assert question.intent == "is_value_real"
        assert question.institution_text == "Alliant"
        assert question.field_labels == ("Admission rate",)
        assert question.unmapped_terms == ("yield",)
        assert question.institution_hint == "110468"
        assert question.text == "Is the 0% real?"
        assert not question.refuses_judgement
        sent = json.loads(fake.calls[0]["user"])
        assert sent == {"question": "Is the 0% real?", "institution_known_from_page": True}
        assert fake.calls[0]["system"] == s.STRUCTURE_SYSTEM
        assert fake.calls[0]["schema"] == s.QUESTION_SCHEMA

    def test_judgement_is_refused_by_intent_or_by_flag(self) -> None:
        assert _structured({**_BASE, "intent": "performance_or_ranking"}).refuses_judgement
        assert _structured({**_BASE, "asks_for_judgement": True}).refuses_judgement

    @pytest.mark.parametrize(
        "reply",
        [
            "not json",
            "[]",
            {**_BASE, "extra": 1},
            {k: v for k, v in _BASE.items() if k != "note"},
            {**_BASE, "intent": "rank_them"},
            {**_BASE, "source": "Niche"},
            {**_BASE, "field_labels": ["Graduation rate"]},
            {**_BASE, "field_labels": "Admission rate"},
            {**_BASE, "unmapped_terms": [1]},
            {**_BASE, "institution_text": 42},
            {**_BASE, "asks_for_judgement": "yes"},
        ],
    )
    def test_anything_outside_the_schema_is_unclear(self, reply: Any) -> None:
        question = _structured(reply)
        assert question.intent == "unclear"
        assert question.field_labels == ()
        assert "not usable" in question.note

    def test_usage_is_carried_and_the_reader_text_is_not_in_as_dict(self) -> None:
        question = _structured(_BASE)
        assert set(question.usage) == {
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_creation_tokens",
        }
        rendered = s.as_dict(question)
        assert "text" not in rendered and rendered["intent"] == "what_is_not_reported"

    def test_describe_is_stable_json(self) -> None:
        assert s.describe({"b": 1, "a": "é"}) == '{"a": "é", "b": 1}'
