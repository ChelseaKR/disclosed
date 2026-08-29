"""Can the Credential Registry be joined to the two federal corpora, and how often.

``docs/ROADMAP.md`` names this as the question that comes before a Credential Registry adapter is
designed, and it names it for a reason a grader would otherwise learn the expensive way: an
institution that the join missed and an institution that disclosed nothing look identical on a
page. Everything in this module is a measurement of the join, and nothing in it grades anybody.

Three candidate keys, measured separately and reported separately, because they are not equally
good and averaging them would hide that:

1. **``ceterms:ipedsID``**, matched exactly against the IPEDS directory's ``UNITID`` and the
   College Scorecard census's unit id. This is the only key that is a federal identifier
   published as a federal identifier, and it is the one a real adapter would join on.
2. **``ceterms:opeID``**, counted but joined to nothing. Neither committed corpus carries an OPE
   id, so the honest report of this key is how many organizations publish one and that this
   project cannot currently resolve it. Counting it as an unmatched join would understate the
   registry; counting it as a match would invent one.
3. **The host of ``ceterms:subjectWebpage``**, matched against the host of the IPEDS
   ``WEBADDR`` this project already grades. This is a weaker key and is reported as weaker. A
   host is not an identifier: several institutions in one system publish one host, and a
   registry organization can be a department of an institution rather than the institution. So
   every host that resolves to more than one IPEDS institution is counted as **ambiguous** and
   excluded from the match, rather than being resolved to whichever row came first.

Two denominators, and the report carries both. Over all registry organizations the rate is
small, because most of the registry is training providers that were never in IPEDS. Over
organizations the registry itself types as ``orgType:Postsecondary`` the rate is the one an
adapter would live with. Neither is the "real" number on its own, which is why publishing one
without the other would be the more dishonest choice.

The direction that matters most is the one that is easy to forget to compute: not what share of
the registry joins, but what share of the *federal corpora* the registry reaches. A join rate of
100% over 400 organizations still leaves 5,700 IPEDS institutions the registry says nothing
about, and an adapter designed without that number in front of it would ship a third source that
covers a few percent of the population while looking like a third source.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any, Final

from .scope import NATIONAL, Scope
from .sources.credential_registry import Organization, host_of

__all__ = [
    "IPEDS_CORPUS",
    "SCORECARD_CORPUS",
    "build",
    "ipeds_index",
    "scorecard_index",
]

IPEDS_CORPUS: Final[str] = "IPEDS directory"
SCORECARD_CORPUS: Final[str] = "College Scorecard census"

_SOURCE: Final[str] = "Credential Registry"


def _unit_id(value: Any) -> str | None:
    """A unit id as a comparable string, or ``None``.

    Compared as text with leading zeros stripped rather than as an integer, so that ``"100654"``
    and ``100654`` are the same institution while a non-numeric id is not silently coerced into
    one. IPEDS unit ids are numeric; a value that is not is a value this project will not join.
    """
    if isinstance(value, bool) or value is None:
        return None
    text = str(value).strip()
    if not text.isdigit():
        return None
    return text.lstrip("0") or "0"


def ipeds_index(rows: Iterable[dict[str, Any]]) -> tuple[set[str], dict[str, set[str]]]:
    """The IPEDS directory as the two things a join needs: its unit ids, and hosts to unit ids.

    The host map keeps every unit id a host resolves to, not the first one. Collapsing them here
    would make an ambiguous host look like a clean match, and the ambiguity is a finding about
    the key rather than noise to be cleaned up.
    """
    unit_ids: set[str] = set()
    by_host: dict[str, set[str]] = {}
    for row in rows:
        unit_id = _unit_id(row.get("id") or row.get("ipeds.UNITID"))
        if unit_id is None:
            continue
        unit_ids.add(unit_id)
        host = host_of(row.get("ipeds.WEBADDR"))
        if host is not None:
            by_host.setdefault(host, set()).add(unit_id)
    return unit_ids, by_host


def scorecard_index(records: Iterable[dict[str, Any]]) -> set[str]:
    """The unit ids of a College Scorecard capture."""
    ids: set[str] = set()
    for record in records:
        unit_id = _unit_id(record.get("id"))
        if unit_id is not None:
            ids.add(unit_id)
    return ids


def _identifier_join(
    organizations: Sequence[Organization],
    ipeds_ids: set[str],
    scorecard_ids: set[str],
) -> dict[str, Any]:
    published = [o for o in organizations if o.ipeds_id is not None]
    resolvable = {o.ctid: _unit_id(o.ipeds_id) for o in published}
    unusable = sum(1 for value in resolvable.values() if value is None)
    ids = {value for value in resolvable.values() if value is not None}
    matched_ipeds = ids & ipeds_ids
    matched_scorecard = ids & scorecard_ids
    return {
        "organizations_publishing_an_ipeds_id": len(published),
        "distinct_ipeds_ids": len(ids),
        "ipeds_ids_not_readable_as_a_unit_id": unusable,
        "matched_ipeds_directory": len(matched_ipeds),
        "unmatched_ipeds_directory": len(ids - ipeds_ids),
        "matched_scorecard_census": len(matched_scorecard),
        "unmatched_scorecard_census": len(ids - scorecard_ids),
        "ipeds_institutions": len(ipeds_ids),
        "ipeds_institutions_reached": len(matched_ipeds),
        "scorecard_institutions": len(scorecard_ids),
        "scorecard_institutions_reached": len(matched_scorecard),
    }


def _homepage_join(
    organizations: Sequence[Organization],
    by_host: dict[str, set[str]],
    already_matched: set[str],
    already_reached: set[str],
) -> dict[str, Any]:
    """The weaker key, measured only where the strong one said nothing.

    ``already_matched`` is the set of ctids the identifier join resolved. A host match on an
    organization that already carries an IPEDS id adds nothing and would double-count it, so the
    host key is measured on exactly the organizations the identifier key left unresolved.

    ``already_reached`` is the set of IPEDS institutions the identifier join reached, and it is
    the difference between two numbers that are easy to confuse. The institutions this key
    resolves to are not the institutions it *adds*: a second registry organization can carry the
    homepage of an institution some other organization already joined by identifier, and counting
    those as new would inflate what the weaker key is worth. Both are reported, and the one named
    "beyond" is the one an adapter would actually gain.
    """
    candidates = [
        o for o in organizations if o.ctid not in already_matched and o.homepage_host is not None
    ]
    unique = 0
    ambiguous = 0
    unmatched = 0
    reached: set[str] = set()
    for organization in candidates:
        unit_ids = by_host.get(organization.homepage_host or "")
        if not unit_ids:
            unmatched += 1
        elif len(unit_ids) > 1:
            ambiguous += 1
        else:
            unique += 1
            reached |= unit_ids
    return {
        "organizations_considered": len(candidates),
        "matched_one_institution": unique,
        "matched_more_than_one_institution": ambiguous,
        "matched_no_institution": unmatched,
        "ipeds_institutions_reached": len(reached),
        "ipeds_institutions_reached_beyond_the_identifier_join": len(reached - already_reached),
        "hosts_in_ipeds": len(by_host),
        "hosts_shared_by_more_than_one_institution": sum(
            1 for unit_ids in by_host.values() if len(unit_ids) > 1
        ),
    }


def _share(part: int, whole: int) -> float | None:
    """``part / whole``, or ``None`` when the denominator is zero.

    ``None`` and never ``0.0``, the same rule :class:`disclosed.national.FieldCoverage` uses: a
    rate over nothing is not a rate of zero, and publishing one would say the join failed
    everywhere it was tried when it was never tried.
    """
    return part / whole if whole > 0 else None


def build(
    organizations: Sequence[Organization],
    provenance: dict[str, Any],
    *,
    ipeds_rows: Iterable[dict[str, Any]],
    scorecard_records: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Reduce a registry capture and the two federal corpora to the committable join measurement.

    Raises:
        ValueError: If the capture does not record an exhaustive walk. Every rate here is a rate
            over the registry, and a rate over an unknown fraction of the registry is not a
            smaller version of the same number, it is a different number with the same name.
            There is no correct way to relabel a partial walk, so the only safe answer is to
            refuse, exactly as ``disclosed national`` refuses a sample.
    """
    if not provenance.get("exhausted"):
        raise ValueError(
            "refusing to measure a join from a Credential Registry walk that did not reach the "
            "registry's own total; the rates would describe an unknown fraction of the registry "
            "while being named after the registry"
        )
    ipeds_ids, by_host = ipeds_index(ipeds_rows)
    scorecard_ids = scorecard_index(scorecard_records)
    if not ipeds_ids or not scorecard_ids:
        raise ValueError(
            "refusing to measure a join against an empty federal corpus; a denominator of zero "
            "would publish an unmeasurable join as a complete one"
        )

    postsecondary = [o for o in organizations if o.is_postsecondary]
    identifier = _identifier_join(organizations, ipeds_ids, scorecard_ids)
    identifier_postsecondary = _identifier_join(postsecondary, ipeds_ids, scorecard_ids)
    matched_ctids = set()
    reached_ids = set()
    for organization in organizations:
        unit_id = _unit_id(organization.ipeds_id) if organization.ipeds_id is not None else None
        if unit_id is not None and unit_id in ipeds_ids:
            matched_ctids.add(organization.ctid)
            reached_ids.add(unit_id)
    homepage = _homepage_join(organizations, by_host, matched_ctids, reached_ids)

    walked_at = str(provenance.get("walked_at") or "an unrecorded date")
    # ``ceterms:addressRegion`` is free text. It holds US state names, US state abbreviations and
    # regions of other countries, so what this counts is distinct address regions and not states.
    # Scope calls the field ``states`` and its sentence says "states and territories"; rather than
    # let the shape rename the measurement, the note below says what the number actually counts.
    states = len({o.state for o in organizations if o.state})
    scope = Scope(
        kind=NATIONAL,
        source=_SOURCE,
        institutions=len(organizations),
        states=states,
        universe=provenance.get("total_stated"),
        note=(
            "Every organization the Credential Registry publishes under "
            f"resource_type=organization, walked to the registry's own stated total on "
            f"{walked_at}. These are organizations of every kind, most of them training providers "
            "that were never in IPEDS, so the figures below carry two denominators and neither is "
            "the whole story on its own. The states figure is a count of distinct "
            "ceterms:addressRegion values, which the registry publishes as free text including "
            "regions outside the United States, so it counts regions rather than states."
        ),
    )
    return {
        "kind": "credential-registry-join",
        "scope": scope.as_dict(),
        "registry": {
            "organizations": len(organizations),
            "postsecondary": len(postsecondary),
            "publishing_an_ipeds_id": identifier["organizations_publishing_an_ipeds_id"],
            "publishing_an_ope_id": sum(1 for o in organizations if o.ope_id is not None),
            "publishing_a_homepage": sum(1 for o in organizations if o.homepage_host is not None),
            "repeated_ctids_dropped": provenance.get("duplicates", 0),
            "envelopes_unreadable": provenance.get("unreduced", 0),
            "distinct_address_regions": states,
            "walked_at": provenance.get("walked_at"),
            "pages": provenance.get("pages_walked"),
            "network_calls": provenance.get("calls"),
        },
        "identifier_join": {
            "over_all_organizations": identifier,
            "over_postsecondary_organizations": identifier_postsecondary,
            "share_of_organizations_publishing_an_ipeds_id": _share(
                identifier["organizations_publishing_an_ipeds_id"], len(organizations)
            ),
            "share_of_postsecondary_publishing_an_ipeds_id": _share(
                identifier_postsecondary["organizations_publishing_an_ipeds_id"],
                len(postsecondary),
            ),
            "share_of_ipeds_directory_reached": _share(
                identifier["ipeds_institutions_reached"], len(ipeds_ids)
            ),
            "share_of_scorecard_census_reached": _share(
                identifier["scorecard_institutions_reached"], len(scorecard_ids)
            ),
        },
        "ope_id": {
            "organizations_publishing_one": sum(1 for o in organizations if o.ope_id is not None),
            "joined_to": None,
            "note": (
                "Neither committed corpus carries an OPE id, so this key is counted and not "
                "joined. Reporting it as unmatched would understate the registry and reporting it "
                "as matched would invent a join; the number here is how many organizations "
                "publish one, and nothing more."
            ),
        },
        "homepage_join": {
            **homepage,
            "share_of_ipeds_directory_reached_beyond_the_identifier_join": _share(
                homepage["ipeds_institutions_reached_beyond_the_identifier_join"], len(ipeds_ids)
            ),
            "note": (
                "A weaker key than an identifier, reported separately and never added to the "
                "identifier join. A host that resolves to more than one IPEDS institution is "
                "counted as ambiguous and excluded rather than resolved to whichever row came "
                "first, and organizations the identifier join already matched are left out so "
                "this number answers only what the weaker key would add."
            ),
        },
    }
