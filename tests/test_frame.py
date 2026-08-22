"""``disclosed.frame.composition`` and the census page it feeds.

Small synthetic fixtures here, in the same spirit as ``tests/test_national.py``: these test the
*shape* of the reduction and the rendering, independent of what the real 6,273-institution census
happens to say. ``tests/test_census_replay.py`` is where the real committed capture is held to
account.
"""

from __future__ import annotations

import re
from typing import Any

from disclosed.frame import composition
from disclosed.scope import NATIONAL
from disclosed.site import scorecard_census_page


def _flat(text: str) -> str:
    """Whitespace-collapsed, so a hard-wrapped template line still matches a one-line pattern."""
    return re.sub(r"\s+", " ", text)


class TestComposition:
    def test_every_record_lands_in_exactly_one_state_bucket_or_unstated(self) -> None:
        records = [
            {"school.state": "CA"},
            {"school.state": "CA"},
            {"school.state": "NY"},
            {"school.state": ""},
            {"school.state": None},
            {},
        ]
        comp = composition(records)
        assert comp["institutions"] == 6
        assert comp["states"] == {"CA": 2, "NY": 1}
        assert comp["states_unstated"] == 3

    def test_every_record_lands_in_exactly_one_sector_bucket_or_unstated(self) -> None:
        records = [
            {"school.ownership": 1},
            {"school.ownership": "1"},
            {"school.ownership": 2},
            {"school.ownership": 3},
            {"school.ownership": 99},  # unrecognized code
            {},  # no code at all
        ]
        comp = composition(records)
        assert comp["sectors"] == {"public": 2, "private nonprofit": 1, "private for-profit": 1}
        # The unrecognized code and the absent one are both unstated, but for different reasons;
        # this only asserts the count, because the reduction does not distinguish them either.
        assert comp["sectors_unstated"] == 2

    def test_the_state_and_sector_counts_are_independent_of_each_other(self) -> None:
        """A record can be placed by state and unplaced by sector, or the reverse."""
        records = [{"school.state": "TX", "school.ownership": 77}]
        comp = composition(records)
        assert comp["states"] == {"TX": 1}
        assert comp["states_unstated"] == 0
        assert comp["sectors"] == {}
        assert comp["sectors_unstated"] == 1

    def test_an_empty_corpus_composes_to_zeros_not_a_crash(self) -> None:
        comp = composition([])
        assert comp == {
            "institutions": 0,
            "states": {},
            "states_unstated": 0,
            "sectors": {},
            "sectors_unstated": 0,
        }

    def test_states_and_sectors_are_sorted_so_the_committed_artifact_is_stable(self) -> None:
        records = [{"school.state": s} for s in ("TX", "AK", "CA", "NY")]
        comp = composition(records)
        assert list(comp["states"]) == ["AK", "CA", "NY", "TX"]


_CENSUS_SCOPE: dict[str, Any] = {
    "kind": NATIONAL,
    "source": "College Scorecard",
    "institutions": 8,
    "states": 3,
    "universe": 8,
    "coverage": 1.0,
    "note": "The API was paged to exhaustion.",
}

_PAYLOAD: dict[str, Any] = {
    "scope": _CENSUS_SCOPE,
    "fields": [
        {
            "label": "Admission rate",
            "key": "latest.admissions.admission_rate.overall",
            "statute": "",
            "applicable": 8,
            "reported": 2,
            "missing": 6,
            "implausible": 0,
            "suppressed": 0,
            "not_applicable": 0,
            "share_reported": 0.25,
        }
    ],
    "gaps": {},
    "contradictions": [],
    "ungradeable": 0,
    "composition": {
        "institutions": 8,
        "states": {"CA": 6, "NY": 1, "TX": 1},
        "states_unstated": 0,
        "sectors": {"public": 8},
        "sectors_unstated": 0,
    },
    "sample_composition": {
        "institutions": 2,
        "states": {"CA": 2},
        "states_unstated": 0,
        "sectors": {"public": 1, "private nonprofit": 1},
        "sectors_unstated": 0,
    },
}


class TestScorecardCensusPage:
    def test_the_headline_is_the_missing_share_of_admission_rate(self) -> None:
        body = scorecard_census_page(_PAYLOAD).body
        assert "6 of 8, or 75.0%, publish no admission rate at all" in body

    def test_the_composition_sentence_states_both_frames_california_share(self) -> None:
        body = _flat(scorecard_census_page(_PAYLOAD).body)
        assert "2 of the sample's 2 institutions (100.0%)" in body
        assert "census, 8 institutions across 3 states" in body
        assert "California at 75.0%" in body

    def test_a_sector_in_one_frame_and_not_the_other_still_gets_a_row(self) -> None:
        """The sample has a private nonprofit institution the census composition here does not;
        the row must still appear, at zero, rather than being dropped from the union."""
        body = scorecard_census_page(_PAYLOAD).body
        assert "private nonprofit" in body

    def test_a_payload_with_no_admission_field_prints_no_headline_rather_than_crashing(
        self,
    ) -> None:
        payload = {**_PAYLOAD, "fields": []}
        body = scorecard_census_page(payload).body
        assert "publish no admission rate at all" not in body

    def test_a_payload_with_no_scope_says_so_rather_than_guessing(self) -> None:
        payload = {k: v for k, v in _PAYLOAD.items() if k != "scope"}
        body = scorecard_census_page(payload).body
        assert "This run did not state its coverage." in body

    def test_missing_composition_keys_render_as_zero_shares_not_a_division_error(self) -> None:
        omit = ("composition", "sample_composition")
        payload = {k: v for k, v in _PAYLOAD.items() if k not in omit}
        body = scorecard_census_page(payload).body
        assert "0 of the sample's 0 institutions (no institutions)" in body

    def test_the_page_path_and_title_are_stable(self) -> None:
        page = scorecard_census_page(_PAYLOAD)
        assert page.path == "census"
        assert "census" in page.title.lower()
