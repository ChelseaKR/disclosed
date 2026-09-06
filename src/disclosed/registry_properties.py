"""What the Credential Registry publishes about an organization, counted rather than assumed.

``docs/adr/0007`` measured whether the registry can be joined to the two federal corpora and
answered yes: 4,818 organizations publish a typed ``ceterms:ipedsID``, reaching 77.8% of the
IPEDS directory. It then said, in as many words, that the join does not answer the question that
decides whether an adapter should exist:

    The second open question is what CTDL would be graded *on*: this project grades published
    disclosures against duties, and whether the registry carries a duty worth grading is not
    answered by the join.

This module answers that one the same way: by counting, over the whole walk, and publishing the
number whichever way it comes out.

**Presence, not values.** What is captured is which CTDL property names appear on an
organization's node, never what is inside them. That is deliberate and it is not squeamishness:
this project grades institutions on whether a required disclosure is present, so presence is the
shape of fact that decides whether there is anything here to grade. A property nobody publishes
cannot be a disclosure anybody is failing to make.

**Two denominators, neither of them the answer alone**, the same rule ADR 0007 set. Over the
whole registry a property rate describes a population that is mostly training providers who were
never in IPEDS. Over the organizations that publish an IPEDS id it describes the ones this
project could in principle grade. Both are published; neither is summed with the other.

**Signatures rather than rows.** The capture records each distinct set of property names once,
with the number of organizations carrying it, split by whether the organization publishes an
IPEDS id. That is lossless for every rate this module publishes and it is 245 KiB instead of
8.5 MB. What it gives up is the ability to say which organization had which set, and it gives it
up on purpose: nothing here is about a named institution, and a capture that cannot name one
cannot be misread as a grade.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Final

from .scope import NATIONAL, Scope
from .sources.credential_registry import Capture, Organization

__all__ = ["CENSUS_KIND", "REPORT_KIND", "census", "report"]

CENSUS_KIND: Final[str] = "credential-registry-property-census"
REPORT_KIND: Final[str] = "credential-registry-property-report"

_SOURCE: Final[str] = "Credential Registry"


def _signature_rows(organizations: list[Organization]) -> list[dict[str, Any]]:
    counted: Counter[tuple[bool, tuple[str, ...]]] = Counter()
    for organization in organizations:
        counted[(organization.ipeds_id is not None, organization.properties)] += 1
    return [
        {"joined": joined, "organizations": count, "properties": list(properties)}
        for (joined, properties), count in sorted(
            counted.items(), key=lambda item: (-item[1], not item[0][0], item[0][1])
        )
    ]


def _identifier_rows(organizations: list[Organization]) -> list[dict[str, Any]]:
    counted: Counter[tuple[bool, str]] = Counter()
    for organization in organizations:
        for name in organization.identifier_type_names:
            counted[(organization.ipeds_id is not None, name)] += 1
    return [
        {"joined": joined, "name": name, "organizations": count}
        for (joined, name), count in sorted(
            counted.items(), key=lambda item: (-item[1], not item[0][0], item[0][1])
        )
    ]


def census(capture: Capture) -> dict[str, Any]:
    """The capture: every distinct property set the walk saw, with how many organizations held it.

    Raises:
        ValueError: the walk was not exhaustive. A property rate over part of the registry would
            be a rate over whichever organizations happened to arrive first, and this project has
            already had to publish a census once to correct a sample it quoted as a population.
    """
    if not capture.exhausted:
        raise ValueError(
            "the Credential Registry walk did not reach the registry's own stated total, so a "
            "property census over it would describe the pages that arrived rather than the "
            "registry. A partial walk is a failure, not data."
        )
    organizations = capture.organizations
    # ``ceterms:addressRegion`` is free text holding US state names, US state abbreviations and
    # regions of other countries, so this counts distinct address regions and not states -- the
    # same count ``registry.measure`` takes over the same walk, and named the same way. It is
    # carried here because ``report`` below is handed this payload and nothing else: a field the
    # capture drops is a field the report can only guess at, and its guess would be zero.
    regions = {o.state for o in organizations if o.state}
    return {
        "kind": CENSUS_KIND,
        "organizations": len(organizations),
        "publishing_an_ipeds_id": sum(1 for o in organizations if o.ipeds_id is not None),
        "distinct_address_regions": len(regions),
        "organizations_without_an_address_region": sum(1 for o in organizations if not o.state),
        "signatures": _signature_rows(organizations),
        "identifier_type_names": _identifier_rows(organizations),
        "provenance": capture.provenance(),
    }


def _walk_date(provenance: dict[str, Any]) -> str:
    """When the pages were actually fetched, not when this reduction last ran.

    ``walked_at`` is stamped with the clock at the top of :func:`credential_registry.walk`, and
    that walk serves from a page cache, so re-reducing a committed capture moves ``walked_at``
    while every page in it still carries the day it really arrived. The note below says "walked
    ... on", so it takes the date from the pages and falls back to ``walked_at`` only when there
    are none to read. A rerun's clock published as an observation date is the same error as an
    unmeasured region count published as zero, one field over.
    """
    pages = provenance.get("pages")
    if isinstance(pages, list):
        fetched = [
            str(page["fetched_at"])
            for page in pages
            if isinstance(page, dict) and page.get("fetched_at")
        ]
        if fetched:
            return max(fetched)
    return str(provenance.get("walked_at", ""))


def _rate(part: int, whole: int) -> float | None:
    """``None`` rather than zero when there is nothing to divide by, per :mod:`disclosed.scope`."""
    return None if whole <= 0 else part / whole


def _totals(rows: list[dict[str, Any]]) -> tuple[int, int]:
    everywhere = sum(int(row["organizations"]) for row in rows)
    joined = sum(int(row["organizations"]) for row in rows if row["joined"])
    return everywhere, joined


def _property_rows(
    signatures: list[dict[str, Any]], everywhere: int, joined_total: int
) -> list[dict[str, Any]]:
    everywhere_counts: Counter[str] = Counter()
    joined_counts: Counter[str] = Counter()
    for row in signatures:
        count = int(row["organizations"])
        for name in row["properties"]:
            everywhere_counts[name] += count
            if row["joined"]:
                joined_counts[name] += count
    return [
        {
            "property": name,
            "organizations": everywhere_counts[name],
            "rate": _rate(everywhere_counts[name], everywhere),
            "joined_organizations": joined_counts[name],
            "joined_rate": _rate(joined_counts[name], joined_total),
        }
        for name in sorted(everywhere_counts, key=lambda n: (-everywhere_counts[n], n))
    ]


def _identifier_report(rows: list[dict[str, Any]], joined_total: int) -> list[dict[str, Any]]:
    return [
        {
            "name": row["name"],
            "joined": row["joined"],
            "organizations": row["organizations"],
            "joined_rate": _rate(int(row["organizations"]), joined_total)
            if row["joined"]
            else None,
        }
        for row in rows
    ]


def _note(everywhere: int, joined_total: int, walked_at: str, regions: int | None) -> str:
    counted = (
        "The states figure is a count of distinct ceterms:addressRegion values, which the "
        "registry publishes as free text including regions outside the United States, so it "
        "counts regions rather than states."
        if regions is not None
        else "This capture did not carry an address region through, so the states figure is "
        "null rather than zero: the regions were not counted, which is a different fact from "
        "there being none."
    )
    return (
        f"Which CTDL property names appear on each of the {everywhere} organizations the "
        f"Credential Registry publishes under resource_type=organization, walked to the "
        f"registry's own stated total on {walked_at}. Names only: no value is read, and no "
        "organization is graded, named or scored. Rates are given over two denominators that are "
        f"never added together, the whole walk and the {joined_total} organizations that publish "
        "a typed ceterms:ipedsID, because the second is the only population this project could "
        "grade and the first is mostly training providers that were never in IPEDS. "
        f"{counted}"
    )


def report(census_payload: dict[str, Any]) -> dict[str, Any]:
    """The reduction every published figure about registry properties is read from.

    Raises:
        ValueError: the payload is not a census this module wrote. A report built from something
            else would carry a ``scope`` block describing a walk that did not happen.
    """
    kind = census_payload.get("kind")
    if kind != CENSUS_KIND:
        raise ValueError(
            f"expected a {CENSUS_KIND} payload and got {kind!r}. The scope block below would "
            "otherwise describe a walk this report was not built from."
        )
    signatures: list[dict[str, Any]] = list(census_payload["signatures"])
    everywhere, joined_total = _totals(signatures)
    provenance = census_payload.get("provenance") or {}
    walked_at = _walk_date(provenance)
    # Absent rather than zero. A census written before this field existed did not measure the
    # regions, and ``Scope.states`` is typed to say so; reading a missing key as 0 is precisely
    # the defect this field was changed to make unsayable.
    raw_regions = census_payload.get("distinct_address_regions")
    regions = int(raw_regions) if isinstance(raw_regions, int) else None
    joined_signatures = [row for row in signatures if row["joined"]]
    largest = max(joined_signatures, key=lambda row: int(row["organizations"]), default=None)
    properties = _property_rows(signatures, everywhere, joined_total)
    return {
        "kind": REPORT_KIND,
        "scope": Scope(
            kind=NATIONAL,
            source=_SOURCE,
            institutions=everywhere,
            states=regions,
            universe=everywhere,
            note=_note(everywhere, joined_total, walked_at, regions),
        ).as_dict(),
        "organizations": everywhere,
        "publishing_an_ipeds_id": joined_total,
        "distinct_property_names": len(properties),
        "distinct_property_sets": len(signatures),
        "distinct_property_sets_among_joined": len(joined_signatures),
        "largest_joined_property_set": None
        if largest is None
        else {
            "organizations": int(largest["organizations"]),
            "share_of_joined": _rate(int(largest["organizations"]), joined_total),
            "properties": list(largest["properties"]),
        },
        "universal_over_joined": [
            row["property"] for row in properties if row["joined_organizations"] == joined_total
        ],
        "properties": properties,
        "identifier_type_names": _identifier_report(
            list(census_payload["identifier_type_names"]), joined_total
        ),
    }
