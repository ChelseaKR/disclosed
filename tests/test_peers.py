"""Peer comparison, including the case it was built to settle."""

from __future__ import annotations

from typing import Any

from disclosed.peers import MIN_PEERS, peer_context, peer_group_for

_TUITION = "latest.cost.tuition.in_state"


def _college(
    id_: int, *, state: str = "CA", ownership: int = 1, level: int = 2, tuition: Any = 1300
) -> dict[str, Any]:
    return {
        "id": id_,
        "school.name": f"College {id_}",
        "school.state": state,
        "school.ownership": ownership,
        "school.degrees_awarded.predominant": level,
        _TUITION: tuition,
    }


class TestPeerGroupDefinition:
    def test_description_names_sector_level_and_state(self) -> None:
        description, _ = peer_group_for(_college(1))
        assert description == "public associate-predominant institutions in CA"

    def test_unknown_codes_degrade_rather_than_raise(self) -> None:
        description, _ = peer_group_for({"school.ownership": 99, "school.state": "OR"})
        assert "unknown sector" in description
        assert "unknown level" in description

    def test_key_separates_different_sectors(self) -> None:
        _, public = peer_group_for(_college(1, ownership=1))
        _, private = peer_group_for(_college(2, ownership=3))
        assert public != private


class TestPeerContext:
    def test_the_west_valley_case(self) -> None:
        """The finding this module exists to settle: one zero among peers that all charge."""
        corpus = [_college(i, tuition=1200 + i) for i in range(1, 80)]
        subject = _college(999, tuition=0)
        corpus.append(subject)

        group = peer_context(subject, _TUITION, corpus)
        assert group.is_usable
        assert group.matching_value == 0
        assert group.minimum is not None and group.minimum >= 1200
        assert "the rest range from" in group.verdict

    def test_a_shared_value_reads_as_convention_not_error(self) -> None:
        """If most peers publish the same value, the rule is wrong and the report should say so."""
        corpus = [_college(i, tuition=0) for i in range(1, 30)]
        subject = _college(999, tuition=0)
        corpus.append(subject)

        group = peer_context(subject, _TUITION, corpus)
        assert group.matching_value == 29
        assert "reporting convention" in group.verdict

    def test_institution_is_excluded_from_its_own_peer_group(self) -> None:
        """A value must not be allowed to help justify itself."""
        subject = _college(1, tuition=0)
        group = peer_context(subject, _TUITION, [subject])
        assert group.size == 0

    def test_small_peer_group_refuses_to_conclude(self) -> None:
        corpus = [_college(i) for i in range(1, MIN_PEERS)]
        subject = _college(999, tuition=0)
        corpus.append(subject)
        group = peer_context(subject, _TUITION, corpus)
        assert not group.is_usable
        assert "too few" in group.verdict

    def test_peers_in_another_state_are_not_peers(self) -> None:
        corpus = [_college(i, state="TX") for i in range(1, 40)]
        subject = _college(999, state="CA", tuition=0)
        corpus.append(subject)
        assert peer_context(subject, _TUITION, corpus).size == 0

    def test_peers_that_report_nothing_are_counted_but_not_averaged(self) -> None:
        corpus = [_college(i, tuition=None) for i in range(1, 30)]
        subject = _college(999, tuition=0)
        corpus.append(subject)
        group = peer_context(subject, _TUITION, corpus)
        assert group.size == 29
        assert group.reporting == 0
        assert not group.is_usable
        assert group.median is None

    def test_booleans_are_not_treated_as_peer_values(self) -> None:
        """bool subclasses int and would otherwise contribute a 0 or 1 to the distribution."""
        corpus = [_college(i, tuition=True) for i in range(1, 30)]
        subject = _college(999, tuition=0)
        corpus.append(subject)
        assert peer_context(subject, _TUITION, corpus).reporting == 0
