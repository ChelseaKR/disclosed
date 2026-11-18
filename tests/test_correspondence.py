"""The cited correspondence register, and the disagreement frame built on it.

The register's whole claim is that the correspondence is checkable. So the tests that matter here
are the ones that would fail if a citation stopped resolving, if a comparable pair lost a
definition, or if the kinds of disagreement were ever added together.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from disclosed import correspondence, crosswalk
from disclosed.disclosure import Disclosure

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "corpus"
FRAME = ROOT / "data" / "disagreement.json"


class TestTheRegisterIsCited:
    def test_every_entry_has_a_verdict_this_project_recognises(self) -> None:
        for entry in correspondence.REGISTER:
            assert entry.verdict in correspondence.VERDICTS

    def test_every_comparable_pair_has_a_definition_on_both_sides(self) -> None:
        """The rule that makes the register more than a list of opinions."""
        definitions = correspondence.load_definitions(CORPUS)
        for entry in correspondence.comparable_entries():
            for side in (entry.scorecard, entry.ipeds):
                assert side.key, f"{entry.label}: a comparable pair needs a column on both sides"
                assert side.passage_id in definitions, (
                    f"{entry.label}: {side.publisher} is compared with no citable definition. "
                    "A pair with no definition on one side is not_comparable, by the register's "
                    "own rule."
                )

    def test_every_citation_resolves_to_a_passage_in_the_committed_corpus(self) -> None:
        passages = {
            str(row["id"])
            for row in json.loads((CORPUS / "passages.json").read_text(encoding="utf-8"))
        }
        for entry in correspondence.REGISTER:
            for side in (entry.scorecard, entry.ipeds):
                if side.passage_id:
                    assert side.passage_id in passages, f"{entry.label}: {side.passage_id}"

    def test_a_definition_is_quoted_verbatim_and_not_paraphrased(self) -> None:
        """The published text is the publisher's, character for character."""
        raw = {
            str(row["id"]): str(row["text"])
            for row in json.loads((CORPUS / "passages.json").read_text(encoding="utf-8"))
        }
        payload = correspondence.register_payload(CORPUS)
        for entry, published in zip(correspondence.REGISTER, payload["entries"], strict=True):
            for name, side in (("scorecard", entry.scorecard), ("ipeds", entry.ipeds)):
                if not side.passage_id:
                    continue
                assert published[name]["definition"] == raw[side.passage_id]

    def test_every_citation_carries_the_document_it_came_from_and_when(self) -> None:
        payload = correspondence.register_payload(CORPUS)
        for published in payload["entries"]:
            for name in ("scorecard", "ipeds"):
                citation = published[name]["citation"]
                if citation is None:
                    continue
                for required in ("publisher", "title", "url", "retrieved", "sha256", "locator"):
                    assert citation[required], f"{published['label']}/{name}: {required}"

    def test_a_side_with_no_column_says_so_rather_than_publishing_an_empty_definition(
        self,
    ) -> None:
        payload = correspondence.register_payload(CORPUS)
        level = next(row for row in payload["entries"] if row["label"] == "Institutional level")
        assert level["verdict"] == correspondence.NOT_COMPARABLE
        assert level["scorecard"]["key"] is None
        assert level["scorecard"]["definition"] is None, "None, never an empty string"
        assert level["scorecard"]["citation"] is None

    def test_at_least_one_not_comparable_pair_is_published_with_two_definitions(self) -> None:
        """The requirement the issue set, as a test: a not_comparable pair is a finding."""
        payload = correspondence.register_payload(CORPUS)
        both_sides_defined = [
            row
            for row in payload["entries"]
            if row["verdict"] == correspondence.NOT_COMPARABLE
            and row["scorecard"]["definition"]
            and row["ipeds"]["definition"]
        ]
        assert both_sides_defined, (
            "a not_comparable pair where both publishers do define a column is the interesting "
            "case, and the register is supposed to publish at least one"
        )
        for row in both_sides_defined:
            assert row["reason"], "a verdict with no reason is an assertion"

    def test_the_compared_set_is_exactly_the_registers_comparable_entries(self) -> None:
        assert [field.label for field in crosswalk.COMPARED] == [
            entry.label for entry in correspondence.comparable_entries()
        ]

    def test_the_register_version_is_stated_and_not_empty(self) -> None:
        assert correspondence.REGISTER_VERSION
        assert correspondence.register_payload(CORPUS)["register_version"] == (
            correspondence.REGISTER_VERSION
        )


class TestCountingByKind:
    """Three different facts, counted three different ways, and never summed."""

    def kind(self, scorecard: object, ipeds: object) -> str:
        return crosswalk.kind_of(scorecard, ipeds)

    def test_two_published_values_that_differ_is_a_value_disagreement(self) -> None:
        assert self.kind(2, "3") == crosswalk.KIND_VALUE

    def test_the_same_value_typed_differently_is_agreement(self) -> None:
        assert self.kind(3, "3") == crosswalk.AGREED

    def test_reported_against_suppressed_is_counted_apart_from_a_value_disagreement(self) -> None:
        """The requirement the issue set, as a test."""
        suppressed = self.kind(2, "-3")
        differing = self.kind(2, "3")
        assert suppressed == crosswalk.KIND_SUPPRESSED
        assert differing == crosswalk.KIND_VALUE
        assert suppressed != differing

    def test_reported_against_not_applicable_is_a_third_kind(self) -> None:
        assert self.kind(2, "-2") == crosswalk.KIND_NOT_APPLICABLE

    def test_reported_against_a_blank_is_a_fourth_kind(self) -> None:
        assert self.kind(2, "") == crosswalk.KIND_MISSING
        assert self.kind(2, "-1") == crosswalk.KIND_MISSING

    def test_neither_publishing_is_not_a_disagreement(self) -> None:
        assert self.kind("", "-3") == crosswalk.NEITHER

    def test_the_side_that_published_does_not_change_the_kind(self) -> None:
        assert self.kind("-3", 2) == crosswalk.KIND_SUPPRESSED

    def test_an_ipeds_sentinel_is_never_read_as_a_sector(self) -> None:
        assert crosswalk.classify_code("-3") is Disclosure.SUPPRESSED
        assert crosswalk.classify_code("-2") is Disclosure.NOT_APPLICABLE
        assert crosswalk.classify_code("-1") is Disclosure.MISSING
        assert crosswalk.classify_code(3) is Disclosure.REPORTED


class TestTheFrame:
    def records(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        scorecard = [
            {"id": "1", "school.name": "Agrees", "school.ownership": 2, "school.state": "CA"},
            {"id": "2", "school.name": "Differs", "school.ownership": 2, "school.state": "CA"},
            {"id": "3", "school.name": "Suppressed", "school.ownership": 2, "school.state": "CA"},
            {"id": "4", "school.name": "Scorecard only", "school.ownership": 1},
        ]
        ipeds = [
            {"id": "1", "ipeds.CONTROL": "2", "ipeds.STABBR": "CA"},
            {"id": "2", "ipeds.CONTROL": "3", "ipeds.STABBR": "CA"},
            {"id": "3", "ipeds.CONTROL": "-3", "ipeds.STABBR": "CA"},
            {"id": "5", "ipeds.CONTROL": "1", "ipeds.STABBR": "NY"},
        ]
        return scorecard, ipeds

    def test_the_kinds_are_counted_separately(self) -> None:
        frames, _ = crosswalk.frame(*self.records())
        sector = next(row for row in frames if row.label == "Sector")
        assert sector.counts[crosswalk.AGREED] == 1
        assert sector.counts[crosswalk.KIND_VALUE] == 1
        assert sector.counts[crosswalk.KIND_SUPPRESSED] == 1

    def test_the_overlap_is_published_because_it_is_the_denominator(self) -> None:
        _, composition = crosswalk.frame(*self.records())
        assert composition == {
            "scorecard_institutions": 4,
            "ipeds_institutions": 4,
            "in_both": 3,
            "scorecard_only": 1,
            "ipeds_only": 1,
        }

    def test_a_not_comparable_pair_carries_no_counts_rather_than_zeros(self) -> None:
        """Zeros beside an uncompared pair would read as two collections agreeing perfectly."""
        frames, _ = crosswalk.frame(*self.records())
        for row in frames:
            if row.verdict == correspondence.COMPARABLE:
                continue
            assert row.counts == {}
            assert row.as_dict()["counts"] is None

    def test_an_institution_only_one_source_carries_disagrees_with_nothing(self) -> None:
        frames, _ = crosswalk.frame(*self.records())
        sector = next(row for row in frames if row.label == "Sector")
        assert sum(sector.counts.values()) == 3


@pytest.fixture(scope="module")
def payload() -> dict[str, Any]:
    return json.loads(FRAME.read_text(encoding="utf-8"))


class TestTheCommittedFrame:
    """The census-scale artifact, and the contract the other committed artifacts hold."""

    def test_it_describes_the_population_and_says_so(self, payload: dict[str, Any]) -> None:
        assert payload["scope"]["kind"] == "national"
        assert payload["composition"]["in_both"] == payload["scope"]["institutions"]

    def test_the_grand_canyon_finding_reproduces_over_the_census(
        self, payload: dict[str, Any]
    ) -> None:
        (found,) = [row for row in payload["named"] if row["unit_id"] == "104717"]
        assert found["name"] == "Grand Canyon University"
        assert found["field_label"] == "Sector"
        assert found["scorecard_value"] == "private nonprofit (2)"
        assert found["ipeds_value"] == "private for-profit (3)"

    def test_no_total_is_published_anywhere_in_the_artifact(self, payload: dict[str, Any]) -> None:
        """Adding the kinds is the one thing this frame must not do, at any level."""
        flat = json.dumps(payload)
        for forbidden in ('"disagreements"', '"total_disagreements"', '"total"'):
            assert forbidden not in flat

    def test_the_artifact_states_the_register_version_it_was_built_from(
        self, payload: dict[str, Any]
    ) -> None:
        assert payload["register_version"] == correspondence.REGISTER_VERSION

    def test_the_frame_replays_byte_for_byte_from_the_committed_captures(
        self, payload: dict[str, Any]
    ) -> None:
        """No network and no key: both captures are in the repository."""
        from disclosed.sources import college_scorecard
        from disclosed.sources import ipeds as ipeds_source

        directory = ipeds_source.load_institutions(
            year=2023,
            cache=ROOT / "data" / "HD2023.zip",
            characteristics_cache=ROOT / "data" / "IC2023.zip",
        )
        raw = json.loads((ROOT / "data" / "census" / "scorecard.json").read_text(encoding="utf-8"))
        records, _ = college_scorecard.read_capture(raw)
        frames, composition = crosswalk.frame(records, directory)
        assert composition == payload["composition"]
        assert [row.as_dict() for row in frames] == payload["pairs"]
