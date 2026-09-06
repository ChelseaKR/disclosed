"""What the registry publishes, counted, and the counting held to the same rules as the join.

``docs/adr/0007`` measured the join and then said the join does not answer the question that
decides whether an adapter should exist. ``docs/adr/0009`` answers that one, and this file is
what stops the answer from drifting away from the data it was read off.

Three things are checked here and they are different from each other: that the aggregation is
arithmetic a reader could redo by hand, that the two denominators are never mixed, and that the
committed report replays byte-for-byte from the committed census, which is the contract every
other artifact in this repository is already held to.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from disclosed import registry_properties
from disclosed.scope import scope_from_payload
from disclosed.sources import credential_registry
from disclosed.sources.credential_registry import Capture, Organization, PageRecord

ROOT = Path(__file__).resolve().parent.parent


def _organization(
    ctid: str,
    *,
    ipeds_id: str | None = None,
    properties: tuple[str, ...] = (),
    identifier_type_names: tuple[str, ...] = (),
    state: str | None = None,
) -> Organization:
    return Organization(
        ctid=ctid,
        name=f"Organization {ctid}",
        ipeds_id=ipeds_id,
        ope_id=None,
        org_types=(),
        state=state,
        homepage_host=None,
        properties=properties,
        identifier_type_names=identifier_type_names,
    )


def _page() -> PageRecord:
    return PageRecord(
        page=1,
        url=credential_registry.BASE_URL,
        fetched_at="2026-08-27T19:21:43Z",
        status=200,
        bytes=10,
        sha256="0" * 64,
        attempts=1,
        records=1,
        from_cache=True,
        total_stated=3,
    )


def _capture(organizations: list[Organization], *, exhausted: bool = True) -> Capture:
    return Capture(
        organizations=organizations,
        pages=[_page()],
        total_stated=len(organizations),
        exhausted=exhausted,
        limit=None,
        walked_at="2026-08-27T19:21:43Z",
        finished_at="2026-08-27T19:41:02Z",
        duplicates=0,
        unreduced=0,
    )


class TestReadingPropertiesOffAnEnvelope:
    """Names, never values, and never the JSON-LD keywords that are there by construction."""

    def _envelope(self, node: dict[str, Any]) -> dict[str, Any]:
        return {
            "envelope_ceterms_ctid": node["ceterms:ctid"],
            "decoded_resource": {"@graph": [node]},
        }

    def test_property_names_are_sorted_and_exclude_the_keywords(self) -> None:
        organization = credential_registry.reduce_record(
            self._envelope(
                {
                    "@id": "https://example.test/x",
                    "@type": "ceterms:CredentialOrganization",
                    "ceterms:ctid": "ce-1",
                    "ceterms:name": {"en-US": "X"},
                    "ceterms:address": [],
                }
            )
        )
        assert organization is not None
        assert organization.properties == ("ceterms:address", "ceterms:ctid", "ceterms:name")

    def test_a_property_this_adapter_does_not_understand_is_still_counted(self) -> None:
        """A census of what a publisher published, not of what this code knows about."""
        organization = credential_registry.reduce_record(
            self._envelope({"ceterms:ctid": "ce-2", "ceterms:somethingNobodyModelledHere": 1})
        )
        assert organization is not None
        assert "ceterms:somethingNobodyModelledHere" in organization.properties

    def test_identifier_type_names_are_read_as_the_free_text_they_are(self) -> None:
        """ADR 0007 refuses to read anything here as a federal identifier. Nothing here does."""
        organization = credential_registry.reduce_record(
            self._envelope(
                {
                    "ceterms:ctid": "ce-3",
                    "ceterms:identifier": [
                        {"ceterms:identifierTypeName": {"en-US": "IPEDS NCES Data Year"}},
                        {"ceterms:identifierTypeName": "Provider ID"},
                        {"ceterms:identifierTypeName": {"en-US": "IPEDS NCES Data Year"}},
                        "not a dict",
                    ],
                }
            )
        )
        assert organization is not None
        assert organization.identifier_type_names == ("IPEDS NCES Data Year", "Provider ID")
        assert organization.ipeds_id is None, "a data year is not a unit id"

    def test_the_committed_capture_does_not_carry_the_property_inventory(self) -> None:
        """The omission is deliberate, documented in ``as_dict`` and asserted here.

        Written per organization these two fields add about 8.5 MB to a 7.9 MB file. Leaving them
        out is a size decision; leaving them out without a test would be the silent kind of
        omission this project spends its time finding in other people's data.
        """
        organization = _organization(
            "ce-4", properties=("ceterms:name",), identifier_type_names=("X",)
        )
        assert set(organization.as_dict()) == {
            "ctid",
            "name",
            "ipeds_id",
            "ope_id",
            "org_types",
            "state",
            "homepage_host",
        }


class TestTheCensus:
    def test_a_walk_that_did_not_reach_the_end_is_refused(self) -> None:
        """A rate over the pages that happened to arrive is a rate over the front of the set."""
        capture = _capture([_organization("ce-1")], exhausted=False)
        with pytest.raises(ValueError, match="did not reach"):
            registry_properties.census(capture)

    def test_identical_property_sets_collapse_to_one_row_carrying_the_count(self) -> None:
        capture = _capture(
            [
                _organization("ce-1", properties=("ceterms:name",)),
                _organization("ce-2", properties=("ceterms:name",)),
                _organization("ce-3", properties=("ceterms:name", "ceterms:email")),
            ]
        )
        payload = registry_properties.census(capture)
        assert payload["organizations"] == 3
        counts = {tuple(row["properties"]): row["organizations"] for row in payload["signatures"]}
        assert counts == {("ceterms:name",): 2, ("ceterms:name", "ceterms:email"): 1}

    def test_the_same_property_set_is_two_rows_when_one_side_joins_and_the_other_does_not(
        self,
    ) -> None:
        """The two denominators are kept apart in the capture, not reconstructed later."""
        capture = _capture(
            [
                _organization("ce-1", properties=("ceterms:name",), ipeds_id="100654"),
                _organization("ce-2", properties=("ceterms:name",)),
            ]
        )
        payload = registry_properties.census(capture)
        assert payload["publishing_an_ipeds_id"] == 1
        assert sorted(row["joined"] for row in payload["signatures"]) == [False, True]

    def test_the_address_regions_are_counted_rather_than_discarded(self) -> None:
        """The count ``report`` needs has to survive the reduction, or it can only guess at it."""
        capture = _capture(
            [
                _organization("ce-1", state="CA"),
                _organization("ce-2", state="CA"),
                _organization("ce-3", state="Ontario"),
                _organization("ce-4", state=None),
            ]
        )
        payload = registry_properties.census(capture)
        assert payload["distinct_address_regions"] == 2
        assert payload["organizations_without_an_address_region"] == 1

    def test_the_provenance_of_every_page_travels_with_the_census(self) -> None:
        payload = registry_properties.census(_capture([_organization("ce-1")]))
        assert payload["provenance"]["exhausted"] is True
        assert payload["provenance"]["pages"][0]["sha256"] == "0" * 64


class TestTheReport:
    def _census(self) -> dict[str, Any]:
        return registry_properties.census(
            _capture(
                [
                    _organization("ce-1", properties=("a", "b"), ipeds_id="100654"),
                    _organization("ce-2", properties=("a", "b"), ipeds_id="100663"),
                    _organization("ce-3", properties=("a",)),
                    _organization("ce-4", properties=("a", "c"), identifier_type_names=("Year",)),
                ]
            )
        )

    def test_the_scope_states_the_measured_region_count(self) -> None:
        census = registry_properties.census(
            _capture(
                [
                    _organization("ce-1", properties=("a",), state="CA"),
                    _organization("ce-2", properties=("a",), state="NY"),
                ]
            )
        )
        report = registry_properties.report(census)
        assert report["scope"]["states"] == 2
        assert "counts regions rather than states" in report["scope"]["note"]

    def test_a_census_that_never_counted_the_regions_reports_null_and_not_zero(self) -> None:
        """The defect this field was changed to make unsayable.

        A census written before the count existed did not measure the regions. Publishing that as
        ``0`` tells a reader the registry's organizations sit in no states at all, which is a
        measurement nobody took -- the same error as an unmeasured rate published as zero.
        """
        census = self._census()
        del census["distinct_address_regions"]
        report = registry_properties.report(census)
        assert report["scope"]["states"] is None
        assert "null rather than zero" in report["scope"]["note"]

    def test_a_payload_that_is_not_a_census_is_refused(self) -> None:
        with pytest.raises(ValueError, match="property-census"):
            registry_properties.report({"kind": "credential-registry-capture"})

    def test_rates_are_given_over_both_denominators_and_never_summed(self) -> None:
        report = registry_properties.report(self._census())
        rates = {row["property"]: row for row in report["properties"]}
        assert rates["a"]["organizations"] == 4
        assert rates["a"]["rate"] == 1.0
        assert rates["a"]["joined_organizations"] == 2
        assert rates["a"]["joined_rate"] == 1.0
        assert rates["c"]["joined_organizations"] == 0
        assert rates["c"]["joined_rate"] == 0.0

    def test_a_property_universal_over_the_joined_set_is_named_as_such(self) -> None:
        report = registry_properties.report(self._census())
        assert report["universal_over_joined"] == ["a", "b"]

    def test_the_largest_joined_property_set_is_reported_with_its_share(self) -> None:
        report = registry_properties.report(self._census())
        largest = report["largest_joined_property_set"]
        assert largest["organizations"] == 2
        assert largest["share_of_joined"] == 1.0
        assert largest["properties"] == ["a", "b"]

    def test_a_census_with_no_joined_organization_reports_no_largest_set_rather_than_zero(
        self,
    ) -> None:
        """``None`` and not an empty set with a zero count: they are different facts."""
        census = registry_properties.census(_capture([_organization("ce-1", properties=("a",))]))
        report = registry_properties.report(census)
        assert report["largest_joined_property_set"] is None
        assert report["properties"][0]["joined_rate"] is None

    def test_the_scope_block_says_names_only_and_names_both_denominators(self) -> None:
        report = registry_properties.report(self._census())
        note = report["scope"]["note"]
        assert "Names only" in note
        assert "no organization is graded" in note
        assert report["scope"]["kind"] == "national"


class TestTheCommittedPropertyCensus:
    """The committed report replays byte-for-byte from the committed census.

    Same contract as ``tests/test_registry.py::TestTheCommittedMeasurement`` holds for the join:
    change the reducer and the artifact is regenerated in the same commit, or this fails and the
    diff says what moved. A published rate whose file and whose code disagree is a rate nobody
    measured.
    """

    def _census(self) -> dict[str, Any]:
        return json.loads((ROOT / "data/registry/properties.json").read_text(encoding="utf-8"))

    def test_the_report_replays_exactly_from_the_committed_census(self) -> None:
        rebuilt = registry_properties.report(self._census())
        committed = json.loads((ROOT / "data/registry-properties.json").read_text(encoding="utf-8"))
        assert rebuilt == committed

    def test_the_committed_census_records_an_exhaustive_walk(self) -> None:
        provenance = self._census()["provenance"]
        assert provenance["exhausted"] is True
        assert provenance["total_stated"] > 0
        assert provenance["pages"], "a census with no page records cannot prove anything"
        for page in provenance["pages"]:
            assert page["status"] == 200
            assert len(page["sha256"]) == 64
            assert page["url"].startswith(credential_registry.BASE_URL)

    def test_the_census_and_the_join_capture_describe_the_same_walk(self) -> None:
        """Two captures of one walk, or the figures in the ADR are about two different registries.

        The join capture and this census are separate files because putting the property
        inventory in the first one costs 8.5 MB. Separate files can drift, so the thing that
        makes them one measurement is asserted rather than assumed.
        """
        census = self._census()
        join = json.loads((ROOT / "data/registry/organizations.json").read_text(encoding="utf-8"))
        assert census["organizations"] == join["provenance"]["organizations"]
        assert census["provenance"]["total_stated"] == join["provenance"]["total_stated"]
        assert census["provenance"]["pages_walked"] == join["provenance"]["pages_walked"]

    def test_both_committed_registry_artifacts_state_the_same_region_count(self) -> None:
        """One walk, so one region count, and neither of them zero.

        These two artifacts describe the same 33,809-organization walk -- the test above asserts
        that -- and both carry a ``scope`` block whose ``sentence`` is documented as safe to print
        next to any figure from the run. For a while the property report's said "across 0 states
        and territories" while the join's said 153, because nothing carried the address through
        the property reduction and ``Scope.states`` had no way to say so. Read them the way the
        site reads them and require that they agree.
        """
        properties = json.loads(
            (ROOT / "data/registry-properties.json").read_text(encoding="utf-8")
        )
        join = json.loads((ROOT / "data/registry-join.json").read_text(encoding="utf-8"))
        from_properties = scope_from_payload(properties)
        from_join = scope_from_payload(join)
        assert from_properties is not None and from_join is not None
        assert from_properties.states == from_join.states
        # Not zero, and not the absence of a measurement either: this walk really was counted.
        assert from_properties.states is not None and from_properties.states > 0
        assert from_properties.sentence == from_join.sentence

    def test_the_note_names_the_day_the_pages_were_fetched_not_the_day_it_was_reduced(
        self,
    ) -> None:
        """The capture serves from a page cache, so re-reducing it must not restamp the walk."""
        census = self._census()
        report = registry_properties.report(census)
        latest_page = max(str(page["fetched_at"]) for page in census["provenance"]["pages"])
        assert latest_page in report["scope"]["note"]

    def test_the_signature_counts_add_up_to_the_organizations_walked(self) -> None:
        """The one arithmetic error that would let every published rate be wrong together."""
        census = self._census()
        rows = census["signatures"]
        assert sum(row["organizations"] for row in rows) == census["organizations"]
        assert (
            sum(row["organizations"] for row in rows if row["joined"])
            == census["publishing_an_ipeds_id"]
        )
