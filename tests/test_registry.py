"""The Credential Registry adapter and the join it exists to measure.

Two things are being held here, and they are the two ways this measurement could go wrong.

The adapter must refuse to report a walk it cannot prove reached the end. The registry answers an
unmatched filter with HTTP 200 and ``x-total: 0`` -- the exact confusion the README records this
project falling for once, when a zero that meant "your filter matched nothing" was read as a
measurement of what is available -- so a walk that returns that has measured nothing and must say
so rather than publishing a join rate over an empty capture.

The join must not overstate itself. A host is not an identifier, an ``"IPEDS NCES Data Year"`` is
not a unit id, and an OPE id this project cannot resolve is neither a match nor a miss. Each of
those has a test here, because each of them is a way to publish a bigger join rate than the
registry supports, and a bigger join rate is exactly what an adapter would be designed around.
"""

from __future__ import annotations

import json
import urllib.error
from pathlib import Path
from typing import Any

import pytest

from disclosed import registry
from disclosed.sources import credential_registry

ROOT = Path(__file__).resolve().parent.parent


class _FakeResponse:
    """What ``urlopen`` hands back: a byte body, a status, and headers with ``.get``."""

    def __init__(
        self, body: str | bytes, *, status: int = 200, headers: dict[str, str] | None = None
    ) -> None:
        self._body = body.encode("utf-8") if isinstance(body, str) else body
        self.status = status
        self.headers = headers or {}

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def _envelope(ctid: str, **node: Any) -> dict[str, Any]:
    """A search-result envelope shaped the way the registry shapes one."""
    graph: dict[str, Any] = {"@type": "ceterms:CredentialOrganization", "ceterms:ctid": ctid}
    graph.update(node)
    return {
        "envelope_ceterms_ctid": ctid,
        "envelope_ctdl_type": "ceterms:CredentialOrganization",
        "decoded_resource": {"@id": f"https://example.test/graph/{ctid}", "@graph": [graph]},
    }


def _page(envelopes: list[dict[str, Any]], total: int | None) -> _FakeResponse:
    headers = {} if total is None else {"x-total": str(total)}
    return _FakeResponse(json.dumps(envelopes), headers=headers)


def _org(
    ctid: str,
    *,
    ipeds_id: str | None = None,
    ope_id: str | None = None,
    postsecondary: bool = True,
    host: str | None = None,
    state: str | None = "Ohio",
) -> credential_registry.Organization:
    return credential_registry.Organization(
        ctid=ctid,
        name=f"Organization {ctid}",
        ipeds_id=ipeds_id,
        ope_id=ope_id,
        org_types=(credential_registry.POSTSECONDARY,) if postsecondary else ("orgType:Business",),
        state=state,
        homepage_host=host,
    )


def _ipeds_row(unit_id: str, web: str) -> dict[str, Any]:
    return {"id": unit_id, "ipeds.UNITID": unit_id, "ipeds.WEBADDR": web}


def _exhausted(**overrides: Any) -> dict[str, Any]:
    provenance: dict[str, Any] = {
        "exhausted": True,
        "total_stated": 3,
        "walked_at": "2026-08-27T00:00:00Z",
        "duplicates": 0,
        "unreduced": 0,
        "calls": 1,
    }
    provenance.update(overrides)
    return provenance


@pytest.fixture(autouse=True)
def waits(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Every pause the adapter asked for, recorded instead of slept."""
    asked: list[float] = []
    monkeypatch.setattr(credential_registry, "_sleep", asked.append)
    return asked


def _serve(monkeypatch: pytest.MonkeyPatch, pages: list[Any]) -> list[str]:
    """Serve ``pages`` in order to successive ``urlopen`` calls; record the URLs asked for."""
    urls: list[str] = []
    queue = list(pages)

    def fake_urlopen(url: str, timeout: float = 0) -> _FakeResponse:
        urls.append(url)
        nxt = queue.pop(0) if queue else _page([], total=None)
        if isinstance(nxt, BaseException):
            raise nxt
        assert isinstance(nxt, _FakeResponse)
        return nxt

    monkeypatch.setattr(credential_registry.urllib.request, "urlopen", fake_urlopen)
    return urls


class TestTheWalkProvesItReachedTheEnd:
    """Exhaustion is proven from the registry's own ``x-total``, never from the row count."""

    def test_a_walk_that_reaches_the_stated_total_is_exhaustive(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _serve(
            monkeypatch,
            [
                _page([_envelope("ce-1"), _envelope("ce-2")], total=3),
                _page([_envelope("ce-3")], total=3),
                _page([], total=3),
            ],
        )
        capture = credential_registry.walk(per_page=2, pause=0)
        assert capture.exhausted is True
        assert [o.ctid for o in capture.organizations] == ["ce-1", "ce-2", "ce-3"]
        assert capture.total_stated == 3

    def test_a_walk_that_stops_short_of_the_stated_total_is_a_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _serve(monkeypatch, [_page([_envelope("ce-1")], total=9), _page([], total=9)])
        with pytest.raises(credential_registry.RegistryError) as raised:
            credential_registry.walk(pause=0)
        assert "stated total of 9" in str(raised.value)

    def test_a_stated_total_of_zero_is_not_evidence_the_registry_is_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The registry answers an unmatched filter 200-with-zero. The README records this
        project reading such a zero as a measurement once; the walk must not do it again."""
        _serve(monkeypatch, [_page([], total=0)])
        with pytest.raises(credential_registry.RegistryError) as raised:
            credential_registry.walk(pause=0)
        assert "evidence about the query and not about the registry" in str(raised.value)

    def test_a_walk_with_no_total_header_cannot_claim_exhaustion(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _serve(monkeypatch, [_page([_envelope("ce-1")], total=None), _page([], total=None)])
        with pytest.raises(credential_registry.RegistryError) as raised:
            credential_registry.walk(pause=0)
        assert "no page carried a usable x-total header" in str(raised.value)

    def test_a_limited_walk_stops_early_without_claiming_to_be_exhaustive(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _serve(monkeypatch, [_page([_envelope("ce-1"), _envelope("ce-2")], total=900)])
        capture = credential_registry.walk(limit=1, pause=0)
        assert capture.exhausted is False
        assert len(capture.organizations) == 1

    def test_a_repeated_ctid_is_counted_and_not_kept_twice(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Offset pagination over a set publishers are editing can serve one organization twice.
        Keeping both would put one organization into a join denominator twice."""
        _serve(
            monkeypatch,
            [
                _page([_envelope("ce-1"), _envelope("ce-1"), _envelope("ce-2")], total=3),
                _page([], total=3),
            ],
        )
        capture = credential_registry.walk(pause=0)
        assert capture.duplicates == 1
        assert [o.ctid for o in capture.organizations] == ["ce-1", "ce-2"]
        assert capture.exhausted is True

    def test_an_unreadable_envelope_is_counted_never_silently_dropped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _serve(
            monkeypatch,
            [_page([{"decoded_resource": {}}, _envelope("ce-1")], total=2), _page([], total=2)],
        )
        capture = credential_registry.walk(pause=0)
        assert capture.unreduced == 1
        assert len(capture.organizations) == 1

    def test_every_page_is_written_down(self, monkeypatch: pytest.MonkeyPatch) -> None:
        urls = _serve(monkeypatch, [_page([_envelope("ce-1")], total=1), _page([], total=1)])
        capture = credential_registry.walk(pause=0)
        assert len(capture.pages) == len(urls) == 2
        first = capture.pages[0]
        assert first.status == 200
        assert first.bytes > 0
        assert len(first.sha256) == 64
        assert first.total_stated == 1
        assert first.url.startswith(credential_registry.BASE_URL)

    def test_a_rejected_request_fails_rather_than_returning_what_arrived(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        error = urllib.error.HTTPError("https://example.test", 404, "gone", {}, None)  # type: ignore[arg-type]
        _serve(monkeypatch, [_page([_envelope("ce-1")], total=99), error])
        with pytest.raises(credential_registry.RegistryError) as raised:
            credential_registry.walk(pause=0)
        assert "HTTP 404" in str(raised.value)

    def test_a_rate_limit_is_retried_on_the_registrys_own_say_so(
        self, monkeypatch: pytest.MonkeyPatch, waits: list[float]
    ) -> None:
        limited = urllib.error.HTTPError(
            "https://example.test", 429, "slow down", {"Retry-After": "7"}, None
        )  # type: ignore[arg-type]
        _serve(monkeypatch, [limited, _page([_envelope("ce-1")], total=1), _page([], total=1)])
        capture = credential_registry.walk(pause=0)
        assert capture.pages[0].attempts == 2
        assert 7.0 in waits

    def test_a_dropped_connection_is_retried_and_then_fails(
        self, monkeypatch: pytest.MonkeyPatch, waits: list[float]
    ) -> None:
        """A walk here is hundreds of requests over minutes, so one dropped handshake must not
        throw the whole thing away. It is retried on the same bounded backoff as a 429, and when
        the retries run out it fails rather than reporting the pages that did arrive."""
        dropped = urllib.error.URLError("handshake timed out")
        _serve(monkeypatch, [dropped, _page([_envelope("ce-1")], total=1), _page([], total=1)])
        capture = credential_registry.walk(pause=0)
        assert capture.pages[0].attempts == 2
        assert capture.exhausted is True

        _serve(monkeypatch, [dropped, dropped, dropped, dropped])
        with pytest.raises(credential_registry.RegistryError) as raised:
            credential_registry.walk(pause=0, attempts=4)
        assert "still failing after 4 attempts" in str(raised.value)
        assert "handshake timed out" in str(raised.value)

    def test_a_cached_page_is_served_from_disk_and_says_so(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, waits: list[float]
    ) -> None:
        """The point of the cache is that a rerun after a dropped connection resumes. A cached
        page keeps the time it was originally fetched and the total the registry stated then,
        rather than being backdated to the rerun."""
        _serve(monkeypatch, [_page([_envelope("ce-1")], total=1), _page([], total=1)])
        first = credential_registry.walk(pause=0, cache_dir=tmp_path)
        assert all(not page.from_cache for page in first.pages)

        urls = _serve(monkeypatch, [])
        second = credential_registry.walk(pause=0, cache_dir=tmp_path)
        assert urls == [], "a fully cached walk must touch no network"
        assert all(page.from_cache for page in second.pages)
        assert second.pages[0].fetched_at == first.pages[0].fetched_at
        assert second.pages[0].total_stated == 1
        assert second.exhausted is True
        assert second.provenance()["calls"] == 0
        assert second.provenance()["pages_walked"] == 2

    def test_a_cached_body_without_its_provenance_is_refetched(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A body with no record of when it arrived cannot say when it arrived, and inventing a
        time would be exactly the kind of filling-in this project refuses elsewhere."""
        _serve(monkeypatch, [_page([_envelope("ce-1")], total=1), _page([], total=1)])
        credential_registry.walk(pause=0, cache_dir=tmp_path)
        (tmp_path / "page-0001.meta.json").unlink()
        urls = _serve(monkeypatch, [_page([_envelope("ce-1")], total=1)])
        credential_registry.walk(pause=0, cache_dir=tmp_path)
        assert len(urls) == 1

    def test_a_payload_that_is_not_a_list_of_envelopes_is_a_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _serve(monkeypatch, [_FakeResponse('{"error": "nope"}', headers={"x-total": "5"})])
        with pytest.raises(credential_registry.RegistryError) as raised:
            credential_registry.walk(pause=0)
        assert "not a list of envelopes" in str(raised.value)


class TestTheReduction:
    """What is kept from a CTDL organization, and what is deliberately not read as an identifier."""

    def test_the_typed_federal_identifiers_are_read(self) -> None:
        organization = credential_registry.reduce_record(
            _envelope(
                "ce-1",
                **{
                    "ceterms:name": {"en-US": "Example College"},
                    "ceterms:ipedsID": "201885",
                    "ceterms:opeID": "00308800",
                    "ceterms:subjectWebpage": "https://WWW.Example.edu/about",
                    "ceterms:agentType": [{"ceterms:targetNode": "orgType:Postsecondary"}],
                    "ceterms:address": [{"ceterms:addressRegion": {"en-US": "Ohio"}}],
                },
            )
        )
        assert organization is not None
        assert organization.ipeds_id == "201885"
        assert organization.ope_id == "00308800"
        assert organization.name == "Example College"
        assert organization.homepage_host == "example.edu"
        assert organization.state == "Ohio"
        assert organization.is_postsecondary is True

    def test_a_free_text_ipeds_data_year_is_never_read_as_a_unit_id(self) -> None:
        """``ceterms:identifier`` carries free-text pairs, and the most common IPEDS-shaped one in
        the registry is ``"IPEDS NCES Data Year": "2023"``. Reading that as a unit id is how a
        join rate gets overstated, so nothing in ``ceterms:identifier`` is read as one."""
        organization = credential_registry.reduce_record(
            _envelope(
                "ce-1",
                **{
                    "ceterms:identifier": [
                        {
                            "ceterms:identifierTypeName": {"en-US": "IPEDS NCES Data Year"},
                            "ceterms:identifierValueCode": "2023",
                        }
                    ]
                },
            )
        )
        assert organization is not None
        assert organization.ipeds_id is None

    def test_the_node_is_matched_on_ctid_not_taken_positionally(self) -> None:
        """An envelope's graph holds referenced nodes beside the resource it is about. Taking the
        first node would let a sub-organization's ipedsID answer for the organization's."""
        envelope = _envelope("ce-wanted", **{"ceterms:ipedsID": "111111"})
        graph = envelope["decoded_resource"]["@graph"]
        graph.insert(0, {"ceterms:ctid": "ce-other", "ceterms:ipedsID": "999999"})
        organization = credential_registry.reduce_record(envelope)
        assert organization is not None
        assert organization.ctid == "ce-wanted"
        assert organization.ipeds_id == "111111"

    def test_an_envelope_without_a_ctid_is_not_given_a_placeholder(self) -> None:
        assert credential_registry.reduce_record({"decoded_resource": {"@graph": [{}]}}) is None
        assert credential_registry.reduce_record("not an envelope") is None
        assert credential_registry.reduce_record({"decoded_resource": {"@graph": {}}}) is None

    @pytest.mark.parametrize(
        ("published", "expected"),
        [
            ("https://www.example.edu/x", "example.edu"),
            ("http://Example.EDU", "example.edu"),
            ("example.edu", "example.edu"),
            ("https://sub.example.edu:8443/", "sub.example.edu"),
            ("", None),
            ("   ", None),
            (None, None),
            (42, None),
        ],
    )
    def test_a_host_is_normalized_one_stated_way(
        self, published: Any, expected: str | None
    ) -> None:
        assert credential_registry.host_of(published) == expected

    def test_an_integer_identifier_is_read_and_a_boolean_is_not(self) -> None:
        assert credential_registry._identifier(201885) == "201885"
        assert credential_registry._identifier(True) is None
        assert credential_registry._identifier("  ") is None

    def test_a_name_in_another_language_is_kept_rather_than_discarded(self) -> None:
        assert credential_registry._english({"fr-CA": "Collège"}) == "Collège"
        assert credential_registry._english({"en": "College"}) == "College"
        assert credential_registry._english({}) is None
        assert credential_registry._english(7) is None


class TestTheCaptureRoundTrips:
    def test_a_capture_written_reads_back_identically(self, tmp_path: Path) -> None:
        capture = credential_registry.Capture(
            organizations=[_org("ce-1", ipeds_id="201885", host="example.edu")],
            pages=[],
            total_stated=1,
            exhausted=True,
            limit=None,
            walked_at="2026-08-27T00:00:00Z",
            finished_at="2026-08-27T00:01:00Z",
            duplicates=0,
            unreduced=0,
        )
        out = tmp_path / "capture.json"
        credential_registry.write_capture(capture, out)
        organizations, provenance = credential_registry.read_capture(
            json.loads(out.read_text(encoding="utf-8"))
        )
        assert organizations == capture.organizations
        assert provenance["exhausted"] is True

    def test_a_bare_list_is_not_a_capture(self) -> None:
        with pytest.raises(credential_registry.RegistryError) as raised:
            credential_registry.read_capture([{"ctid": "ce-1"}])
        assert "carries its own provenance" in str(raised.value)

    def test_a_capture_missing_its_parts_is_refused(self) -> None:
        with pytest.raises(credential_registry.RegistryError):
            credential_registry.read_capture(
                {"kind": credential_registry.CAPTURE_KIND, "provenance": {}}
            )

    def test_an_organization_without_a_ctid_cannot_be_keyed(self) -> None:
        with pytest.raises(credential_registry.RegistryError) as raised:
            credential_registry.read_capture(
                {
                    "kind": credential_registry.CAPTURE_KIND,
                    "provenance": {},
                    "organizations": [{"name": "no ctid"}],
                }
            )
        assert "cannot be keyed" in str(raised.value)


class TestTheJoinRefusesWhatItCannotMeasure:
    def test_a_partial_walk_cannot_produce_a_join_rate(self) -> None:
        with pytest.raises(ValueError) as raised:
            registry.build(
                [_org("ce-1", ipeds_id="201885")],
                _exhausted(exhausted=False),
                ipeds_rows=[_ipeds_row("201885", "example.edu")],
                scorecard_records=[{"id": "201885"}],
            )
        assert "did not reach the registry's own total" in str(raised.value)

    def test_an_empty_federal_corpus_cannot_be_a_denominator(self) -> None:
        with pytest.raises(ValueError) as raised:
            registry.build(
                [_org("ce-1", ipeds_id="201885")],
                _exhausted(),
                ipeds_rows=[],
                scorecard_records=[{"id": "201885"}],
            )
        assert "denominator of zero" in str(raised.value)


class TestTheJoinDoesNotOverstateItself:
    def test_the_identifier_join_matches_only_ids_that_are_in_the_corpora(self) -> None:
        payload = registry.build(
            [
                _org("ce-1", ipeds_id="201885"),
                _org("ce-2", ipeds_id="999999"),
                _org("ce-3"),
            ],
            _exhausted(),
            ipeds_rows=[
                _ipeds_row("201885", "https://a.edu"),
                _ipeds_row("100654", "https://b.edu"),
            ],
            scorecard_records=[{"id": "201885"}],
        )
        over_all = payload["identifier_join"]["over_all_organizations"]
        assert over_all["organizations_publishing_an_ipeds_id"] == 2
        assert over_all["matched_ipeds_directory"] == 1
        assert over_all["unmatched_ipeds_directory"] == 1
        assert over_all["ipeds_institutions"] == 2
        assert payload["identifier_join"]["share_of_ipeds_directory_reached"] == 0.5

    def test_a_leading_zero_is_the_same_unit_id_and_a_word_is_not(self) -> None:
        payload = registry.build(
            [_org("ce-1", ipeds_id="0201885"), _org("ce-2", ipeds_id="not-a-number")],
            _exhausted(),
            ipeds_rows=[_ipeds_row("201885", "https://a.edu")],
            scorecard_records=[{"id": "201885"}],
        )
        over_all = payload["identifier_join"]["over_all_organizations"]
        assert over_all["matched_ipeds_directory"] == 1
        assert over_all["ipeds_ids_not_readable_as_a_unit_id"] == 1

    def test_the_postsecondary_denominator_is_reported_beside_the_whole_registry(self) -> None:
        payload = registry.build(
            [
                _org("ce-1", ipeds_id="201885"),
                _org("ce-2", postsecondary=False),
                _org("ce-3", postsecondary=False),
            ],
            _exhausted(),
            ipeds_rows=[_ipeds_row("201885", "https://a.edu")],
            scorecard_records=[{"id": "201885"}],
        )
        identifier = payload["identifier_join"]
        assert payload["registry"]["postsecondary"] == 1
        assert identifier["share_of_organizations_publishing_an_ipeds_id"] == pytest.approx(1 / 3)
        assert identifier["share_of_postsecondary_publishing_an_ipeds_id"] == 1.0

    def test_a_host_shared_by_two_institutions_is_ambiguous_and_not_a_match(self) -> None:
        """Several institutions in one system publish one host. Resolving to whichever row came
        first would turn an ambiguity into a join, which is how a weak key gets to look strong."""
        payload = registry.build(
            [_org("ce-1", host="system.edu"), _org("ce-2", host="lone.edu")],
            _exhausted(),
            ipeds_rows=[
                _ipeds_row("1", "https://system.edu"),
                _ipeds_row("2", "https://www.system.edu/campus"),
                _ipeds_row("3", "https://lone.edu"),
            ],
            scorecard_records=[{"id": "1"}],
        )
        homepage = payload["homepage_join"]
        assert homepage["matched_more_than_one_institution"] == 1
        assert homepage["matched_one_institution"] == 1
        assert homepage["ipeds_institutions_reached"] == 1
        assert homepage["hosts_shared_by_more_than_one_institution"] == 1

    def test_what_the_weak_key_resolves_to_is_not_what_it_adds(self) -> None:
        """A second organization can carry the homepage of an institution the identifier join
        already reached. Counting that as new would inflate what the weaker key is worth."""
        payload = registry.build(
            [
                _org("ce-1", ipeds_id="1", host="a.edu"),
                _org("ce-2", host="a.edu"),
                _org("ce-3", host="b.edu"),
            ],
            _exhausted(),
            ipeds_rows=[_ipeds_row("1", "https://a.edu"), _ipeds_row("2", "https://b.edu")],
            scorecard_records=[{"id": "1"}],
        )
        homepage = payload["homepage_join"]
        assert homepage["matched_one_institution"] == 2
        assert homepage["ipeds_institutions_reached"] == 2
        assert homepage["ipeds_institutions_reached_beyond_the_identifier_join"] == 1
        assert homepage["share_of_ipeds_directory_reached_beyond_the_identifier_join"] == 0.5

    def test_the_weak_key_is_measured_only_where_the_strong_one_said_nothing(self) -> None:
        payload = registry.build(
            [_org("ce-1", ipeds_id="1", host="a.edu"), _org("ce-2", host="a.edu")],
            _exhausted(),
            ipeds_rows=[_ipeds_row("1", "https://a.edu")],
            scorecard_records=[{"id": "1"}],
        )
        assert payload["homepage_join"]["organizations_considered"] == 1

    def test_an_ope_id_is_counted_and_never_joined(self) -> None:
        """Neither committed corpus carries an OPE id. Reporting it as unmatched would understate
        the registry; reporting it as matched would invent a join."""
        payload = registry.build(
            [_org("ce-1", ope_id="00308800"), _org("ce-2")],
            _exhausted(),
            ipeds_rows=[_ipeds_row("1", "https://a.edu")],
            scorecard_records=[{"id": "1"}],
        )
        assert payload["ope_id"]["organizations_publishing_one"] == 1
        assert payload["ope_id"]["joined_to"] is None
        assert "joined" in payload["ope_id"]["note"]

    def test_a_rate_over_nothing_is_none_and_never_zero(self) -> None:
        payload = registry.build(
            [_org("ce-1", postsecondary=False)],
            _exhausted(),
            ipeds_rows=[_ipeds_row("1", "https://a.edu")],
            scorecard_records=[{"id": "1"}],
        )
        assert payload["identifier_join"]["share_of_postsecondary_publishing_an_ipeds_id"] is None

    def test_the_scope_says_national_and_carries_the_registrys_own_total(self) -> None:
        payload = registry.build(
            [_org("ce-1", ipeds_id="1", state="Ohio"), _org("ce-2", state="Iowa")],
            _exhausted(total_stated=2),
            ipeds_rows=[_ipeds_row("1", "https://a.edu")],
            scorecard_records=[{"id": "1"}],
        )
        scope = payload["scope"]
        assert scope["kind"] == "national"
        assert scope["source"] == "Credential Registry"
        assert scope["universe"] == 2
        assert scope["states"] == 2


class TestTheCommittedMeasurement:
    """The committed artifact replays byte-for-byte from the committed inputs.

    Same contract as ``tests/test_replay.py`` holds for the national artifact: if the generator
    changes, the artifact is regenerated in the same commit and the diff shows what moved. A join
    rate whose file and whose code disagree is a number nobody measured.
    """

    def test_the_join_replays_exactly_from_the_committed_inputs(self) -> None:
        from disclosed.sources import college_scorecard, ipeds

        organizations, provenance = credential_registry.read_capture(
            json.loads((ROOT / "data/registry/organizations.json").read_text(encoding="utf-8"))
        )
        directory = ipeds.parse_directory((ROOT / "data/HD2023.zip").read_bytes())
        records, _ = college_scorecard.read_capture(
            json.loads((ROOT / "data/census/scorecard.json").read_text(encoding="utf-8"))
        )
        rebuilt = registry.build(
            organizations, provenance, ipeds_rows=directory, scorecard_records=records
        )
        committed = json.loads((ROOT / "data/registry-join.json").read_text(encoding="utf-8"))
        assert rebuilt == committed

    def test_the_committed_capture_records_an_exhaustive_walk(self) -> None:
        payload = json.loads(
            (ROOT / "data/registry/organizations.json").read_text(encoding="utf-8")
        )
        provenance = payload["provenance"]
        assert provenance["exhausted"] is True
        assert provenance["total_stated"] > 0
        assert provenance["pages"], "a capture with no page records cannot prove anything"
        for page in provenance["pages"]:
            assert page["status"] == 200
            assert len(page["sha256"]) == 64
            assert page["url"].startswith(credential_registry.BASE_URL)
