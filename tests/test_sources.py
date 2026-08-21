"""The source adapter, including the failure it must not paper over.

The adapter's one job beyond fetching is to refuse to return partial data. A truncated fetch would
understate disclosure across every institution that never arrived, which on the published page looks
exactly like a real collapse in reporting. These tests exist mostly to hold that line.

The second job, added with the census, is to write down where every page came from. A capture
whose provenance names each call, with the key redacted, is the difference between a national
figure a reader can check and one they have to take on trust; the tests under
:class:`TestProvenance` hold that the record is complete, that the key never reaches it, and
that a rerun from the cache touches no network.
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from disclosed.fields import SCORECARD_API_FIELDS
from disclosed.sources import college_scorecard


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


def _page(
    results: list[dict[str, Any]], total: int, *, headers: dict[str, str] | None = None
) -> _FakeResponse:
    return _FakeResponse(
        json.dumps({"metadata": {"total": total}, "results": results}), headers=headers
    )


def _http_error(code: int, headers: dict[str, str] | None = None) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://example.test", code, "refused", headers or {}, None)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def waits(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Every pause the adapter asked for, recorded instead of slept.

    The politeness pause and the retry backoff are both real seconds in production and both
    would make this file slow for no reason; what matters here is that they are requested with
    the right durations, which the list says.
    """
    asked: list[float] = []
    monkeypatch.setattr(college_scorecard, "_sleep", asked.append)
    return asked


@pytest.fixture
def captured_urls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    urls: list[str] = []

    def fake_urlopen(url: str, timeout: float = 0) -> _FakeResponse:
        urls.append(url)
        return _page([{"id": len(urls), "school.name": f"School {len(urls)}"}], total=1)

    monkeypatch.setattr(college_scorecard.urllib.request, "urlopen", fake_urlopen)
    return urls


def _serve(monkeypatch: pytest.MonkeyPatch, responses: list[Any]) -> list[str]:
    """Answer successive requests from a list; an exception in the list is raised."""
    urls: list[str] = []
    it = iter(responses)

    def fake_urlopen(url: str, timeout: float = 0) -> _FakeResponse:
        urls.append(url)
        item = next(it)
        if isinstance(item, BaseException):
            raise item
        assert isinstance(item, _FakeResponse)
        return item

    monkeypatch.setattr(college_scorecard.urllib.request, "urlopen", fake_urlopen)
    return urls


class TestFetchPage:
    def test_requests_every_graded_field(self, captured_urls: list[str]) -> None:
        college_scorecard.fetch_page(0)
        (url,) = captured_urls
        for field in SCORECARD_API_FIELDS:
            assert field in url

    def test_uses_demo_key_by_default(
        self, captured_urls: list[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("DATA_GOV_API_KEY", raising=False)
        college_scorecard.fetch_page(0)
        assert "api_key=DEMO_KEY" in captured_urls[0]

    def test_prefers_env_key_when_present(
        self, captured_urls: list[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DATA_GOV_API_KEY", "realkey123")
        college_scorecard.fetch_page(0)
        assert "api_key=realkey123" in captured_urls[0]

    def test_transport_failure_raises_rather_than_returning_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A page we could not read must never look like a page with nothing in it."""

        def boom(url: str, timeout: float = 0) -> _FakeResponse:
            raise urllib.error.URLError("connection reset")

        monkeypatch.setattr(college_scorecard.urllib.request, "urlopen", boom)
        with pytest.raises(college_scorecard.ScorecardError, match="unreadable"):
            college_scorecard.fetch_page(3)

    def test_undecodable_body_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            college_scorecard.urllib.request,
            "urlopen",
            lambda url, timeout=0: _FakeResponse("not json at all"),
        )
        with pytest.raises(college_scorecard.ScorecardError):
            college_scorecard.fetch_page(0)

    def test_non_object_payload_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            college_scorecard.urllib.request,
            "urlopen",
            lambda url, timeout=0: _FakeResponse("[1, 2, 3]"),
        )
        with pytest.raises(college_scorecard.ScorecardError, match="non-object"):
            college_scorecard.fetch_page(0)


class TestRetries:
    """What the adapter does when api.data.gov says no, which is the part that meets its limits."""

    def test_retry_after_is_honoured_on_429(
        self, monkeypatch: pytest.MonkeyPatch, waits: list[float]
    ) -> None:
        """The API's own number wins over the backoff schedule when it sends one."""
        _serve(
            monkeypatch,
            [_http_error(429, {"Retry-After": "3"}), _page([{"id": 1}], total=1)],
        )
        capture = college_scorecard.walk()
        assert [r["id"] for r in capture.records] == [1]
        assert waits == [3.0]
        assert capture.pages[0].attempts == 2

    def test_an_absurd_retry_after_is_capped(
        self, monkeypatch: pytest.MonkeyPatch, waits: list[float]
    ) -> None:
        _serve(
            monkeypatch,
            [_http_error(429, {"Retry-After": "99999"}), _page([{"id": 1}], total=1)],
        )
        college_scorecard.walk()
        assert waits == [120.0]

    def test_backoff_doubles_when_no_retry_after_is_sent(
        self, monkeypatch: pytest.MonkeyPatch, waits: list[float]
    ) -> None:
        _serve(
            monkeypatch,
            [_http_error(503), _http_error(503), _page([{"id": 1}], total=1)],
        )
        capture = college_scorecard.walk()
        assert waits == [2.0, 4.0]
        assert capture.pages[0].attempts == 3

    def test_persistent_429_raises_rate_limited_with_the_remedy(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("DATA_GOV_API_KEY", raising=False)
        _serve(monkeypatch, [_http_error(429)] * 4)
        with pytest.raises(college_scorecard.RateLimited, match="DEMO_KEY") as caught:
            college_scorecard.walk()
        assert "after 4 attempts" in str(caught.value)

    def test_the_remedy_is_omitted_when_a_real_key_was_already_in_use(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DATA_GOV_API_KEY", "realkey123")
        _serve(monkeypatch, [_http_error(429)] * 4)
        with pytest.raises(college_scorecard.RateLimited) as caught:
            college_scorecard.walk()
        assert "DEMO_KEY" not in str(caught.value)

    def test_a_4xx_that_is_not_429_fails_at_once(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A request we got wrong will keep being wrong; retrying it burns the caller's budget
        proving so."""
        urls = _serve(monkeypatch, [_http_error(404), _page([{"id": 1}], total=1)])
        with pytest.raises(college_scorecard.ScorecardError, match="HTTP 404"):
            college_scorecard.walk()
        assert len(urls) == 1


class TestIterInstitutions:
    def test_pages_until_total_is_reached(self, monkeypatch: pytest.MonkeyPatch) -> None:
        pages = [
            _page([{"id": 1}, {"id": 2}], total=3),
            _page([{"id": 3}], total=3),
        ]
        it: Iterator[_FakeResponse] = iter(pages)
        monkeypatch.setattr(
            college_scorecard.urllib.request, "urlopen", lambda url, timeout=0: next(it)
        )
        assert [r["id"] for r in college_scorecard.iter_institutions()] == [1, 2, 3]

    def test_limit_stops_early(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            college_scorecard.urllib.request,
            "urlopen",
            lambda url, timeout=0: _page([{"id": 1}, {"id": 2}, {"id": 3}], total=99),
        )
        assert len(list(college_scorecard.iter_institutions(limit=2))) == 2

    def test_empty_results_ends_iteration(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            college_scorecard.urllib.request,
            "urlopen",
            lambda url, timeout=0: _page([], total=0),
        )
        assert list(college_scorecard.iter_institutions()) == []

    def test_non_dict_rows_are_skipped_not_yielded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        it = iter([_page([{"id": 1}, "junk", {"id": 2}], total=2)])  # type: ignore[list-item]
        monkeypatch.setattr(
            college_scorecard.urllib.request, "urlopen", lambda url, timeout=0: next(it)
        )
        assert [r["id"] for r in college_scorecard.iter_institutions()] == [1, 2]

    def test_missing_metadata_raises_rather_than_reporting_a_completed_walk(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A malformed payload must terminate rather than page indefinitely -- but a walk that
        cannot show it reached the end is a failure, not a quiet 1-institution success. This is
        the second shape from issue #1: a 200 with no metadata at all mid-walk."""
        pages = [
            _FakeResponse(json.dumps({"results": [{"id": 1}]})),
            _FakeResponse(json.dumps({"results": []})),
        ]
        it = iter(pages)
        monkeypatch.setattr(
            college_scorecard.urllib.request, "urlopen", lambda url, timeout=0: next(it)
        )
        with pytest.raises(college_scorecard.ScorecardError, match="page 1"):
            list(college_scorecard.iter_institutions())

    def test_missing_metadata_does_not_loop_forever_when_limited(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The same malformed payload, but the caller asked to stop early: a deliberate --limit
        run keeps whatever arrived and returns silently, exactly as before this fix."""
        pages = [
            _FakeResponse(json.dumps({"results": [{"id": 1}]})),
            _FakeResponse(json.dumps({"results": []})),
        ]
        it = iter(pages)
        monkeypatch.setattr(
            college_scorecard.urllib.request, "urlopen", lambda url, timeout=0: next(it)
        )
        assert [r["id"] for r in college_scorecard.iter_institutions(limit=5)] == [1]

    def test_results_run_out_before_total_raises_when_walking_to_exhaustion(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Issue #1's first reproduction: two good pages, then a well-formed HTTP 200 carrying an
        empty ``results`` list well short of ``metadata.total``. A 200 with nothing in it is not
        evidence the walk finished, so a national run must fail loudly rather than publish 3 of
        6,300 institutions as the whole country."""
        pages = [
            _page([{"id": 1}, {"id": 2}], total=6300),
            _page([{"id": 3}], total=6300),
            _page([], total=6300),
        ]
        it = iter(pages)
        monkeypatch.setattr(
            college_scorecard.urllib.request, "urlopen", lambda url, timeout=0: next(it)
        )
        with pytest.raises(college_scorecard.ScorecardError, match="page 2") as caught:
            list(college_scorecard.iter_institutions())
        assert "3" in str(caught.value)
        assert "6300" in str(caught.value)

    def test_error_payload_mid_walk_raises_when_walking_to_exhaustion(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Issue #1's second reproduction: a 200 carrying ``{"errors": [...]}`` instead of
        ``results``, partway through a national walk."""
        pages = [
            _page([{"id": 1}, {"id": 2}], total=6300),
            _FakeResponse(json.dumps({"errors": ["rate governor engaged"]})),
        ]
        it = iter(pages)
        monkeypatch.setattr(
            college_scorecard.urllib.request, "urlopen", lambda url, timeout=0: next(it)
        )
        with pytest.raises(college_scorecard.ScorecardError, match="page 1"):
            list(college_scorecard.iter_institutions())

    def test_limit_short_circuits_even_when_results_run_out_first(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A deliberate --limit is a sample by request. Running out of results before reaching it
        is not the anomaly this fix guards against, and must keep returning silently."""
        pages = [
            _page([{"id": 1}, {"id": 2}], total=6300),
            _page([], total=6300),
        ]
        it = iter(pages)
        monkeypatch.setattr(
            college_scorecard.urllib.request, "urlopen", lambda url, timeout=0: next(it)
        )
        assert [r["id"] for r in college_scorecard.iter_institutions(limit=50)] == [1, 2]


class TestProvenance:
    """Every page written down, the key never, and a rerun that touches nothing."""

    def _two_pages(self, headers: dict[str, str] | None = None) -> list[_FakeResponse]:
        return [
            _page([{"id": 1}, {"id": 2}], total=3, headers=headers),
            _page([{"id": 3}], total=3, headers=headers),
        ]

    def test_every_page_is_recorded_with_its_digest_and_a_redacted_url(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DATA_GOV_API_KEY", "realkey123")
        pages = self._two_pages()
        _serve(monkeypatch, pages)
        capture = college_scorecard.walk()

        assert [p.page for p in capture.pages] == [0, 1]
        for response, record in zip(pages, capture.pages, strict=True):
            body = response.read()
            assert record.sha256 == hashlib.sha256(body).hexdigest()
            assert record.bytes == len(body)
            assert record.status == 200
            assert record.attempts == 1
            assert record.from_cache is False
            assert "api_key=REDACTED" in record.url
            assert "page=" in record.url and "per_page=100" in record.url
            assert record.fetched_at.endswith("Z")
        assert "realkey123" not in json.dumps(capture.provenance())
        assert capture.exhausted is True
        assert capture.total_stated == 3
        assert capture.calls == 2
        assert capture.demo_key is False

    def test_the_key_is_redacted_by_parsing_the_query_not_by_matching_text(self) -> None:
        """A key that happened to appear inside another parameter would survive a string
        replace; parsing the query replaces the one parameter that is a credential."""
        url = f"{college_scorecard.BASE_URL}?api_key=abc&fields=abc,def&page=0"
        redacted = college_scorecard._redact(url)
        assert "api_key=REDACTED" in redacted
        assert "fields=abc%2Cdef" in redacted

    def test_rate_limit_headers_are_recorded_when_sent_and_none_when_not(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``None`` and not ``0``: an unreported budget is not an exhausted one."""
        _serve(
            monkeypatch,
            self._two_pages({"X-RateLimit-Limit": "1000", "X-RateLimit-Remaining": "998"}),
        )
        with_headers = college_scorecard.walk()
        assert with_headers.pages[0].ratelimit_limit == 1000
        assert with_headers.pages[0].ratelimit_remaining == 998

        _serve(monkeypatch, self._two_pages())
        without = college_scorecard.walk()
        assert without.pages[0].ratelimit_limit is None
        assert without.pages[0].ratelimit_remaining is None

    def test_a_pause_separates_network_fetches(
        self, monkeypatch: pytest.MonkeyPatch, waits: list[float]
    ) -> None:
        _serve(monkeypatch, self._two_pages())
        college_scorecard.walk()
        assert waits == [0.5]

    def test_the_cache_serves_a_rerun_without_the_network(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, waits: list[float]
    ) -> None:
        urls = _serve(monkeypatch, self._two_pages())
        first = college_scorecard.walk(cache_dir=tmp_path)
        assert len(urls) == 2

        def refuse(url: str, timeout: float = 0) -> _FakeResponse:
            raise AssertionError("the rerun reached the network")

        monkeypatch.setattr(college_scorecard.urllib.request, "urlopen", refuse)
        waits.clear()
        second = college_scorecard.walk(cache_dir=tmp_path)

        assert second.records == first.records
        assert second.calls == 0
        assert waits == [], "a cache hit is not a request and owes the API no pause"
        for before, after in zip(first.pages, second.pages, strict=True):
            assert after.from_cache is True
            assert after.sha256 == before.sha256
            assert after.fetched_at == before.fetched_at
            assert after.ratelimit_remaining == before.ratelimit_remaining

    def test_a_cached_body_without_its_provenance_is_fetched_again(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A page that cannot say when it was fetched is not served as though it could."""
        (tmp_path / "page-0000.json").write_text(json.dumps({"results": [], "metadata": {}}))
        urls = _serve(monkeypatch, [_page([{"id": 1}], total=1)])
        capture = college_scorecard.walk(cache_dir=tmp_path)
        assert len(urls) == 1
        assert capture.pages[0].from_cache is False
        assert (tmp_path / "page-0000.meta.json").is_file()

    def test_write_and_read_round_trip_with_one_record_per_line(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("DATA_GOV_API_KEY", "realkey123")
        _serve(monkeypatch, self._two_pages())
        capture = college_scorecard.walk()
        out = tmp_path / "capture.json"
        college_scorecard.write_capture(capture, out)

        text = out.read_text(encoding="utf-8")
        assert "realkey123" not in text
        assert text.count("api_key=REDACTED") == 2
        parsed = json.loads(text)
        assert parsed["kind"] == college_scorecard.CAPTURE_KIND
        records, provenance = college_scorecard.read_capture(parsed)
        assert records == capture.records
        assert provenance is not None
        assert provenance["calls"] == 2
        assert college_scorecard.is_exhaustive(provenance, len(records))
        # One institution per line, so a diff between two captures names institutions.
        assert sum(1 for line in text.splitlines() if line.startswith('    {"id":')) == 3

    def test_a_bare_record_list_reads_as_records_with_no_provenance(self) -> None:
        records, provenance = college_scorecard.read_capture([{"id": 1}, "junk", {"id": 2}])
        assert [r["id"] for r in records] == [1, 2]
        assert provenance is None

    def test_anything_else_is_refused_rather_than_read_as_zero_institutions(self) -> None:
        with pytest.raises(college_scorecard.ScorecardError, match="not a JSON array"):
            college_scorecard.read_capture({"results": []})
        with pytest.raises(college_scorecard.ScorecardError):
            college_scorecard.read_capture({"kind": college_scorecard.CAPTURE_KIND})

    @pytest.mark.parametrize(
        ("provenance", "count"),
        [
            ({"exhausted": True, "total_stated": 3, "records": 3}, 2),
            ({"exhausted": True, "total_stated": 3, "records": 2}, 3),
            ({"exhausted": False, "total_stated": 3, "records": 3}, 3),
            ({"exhausted": True, "total_stated": None, "records": 3}, 3),
            ({"exhausted": True, "total_stated": "3", "records": 3}, 3),
        ],
    )
    def test_a_capture_that_cannot_prove_exhaustion_is_not_a_census(
        self, provenance: dict[str, Any], count: int
    ) -> None:
        """Truncated after writing, edited, unstated, or merely claimed: each grades as a
        sample. The one thing that makes a replay national is three counts agreeing."""
        assert not college_scorecard.is_exhaustive(provenance, count)

    def test_a_limited_walk_that_still_reached_the_total_is_a_census(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The evidence is the counts, not the flag the caller passed. A limit above the
        population changes nothing about what arrived."""
        _serve(monkeypatch, self._two_pages())
        capture = college_scorecard.walk(limit=50)
        assert capture.exhausted is True
        assert college_scorecard.is_exhaustive(capture.provenance(), len(capture.records))

    def test_a_limited_walk_that_stopped_short_is_not(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _serve(monkeypatch, self._two_pages())
        capture = college_scorecard.walk(limit=2)
        assert capture.exhausted is False
        assert not college_scorecard.is_exhaustive(capture.provenance(), len(capture.records))

    def test_the_summary_digests_the_file_it_describes(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("DATA_GOV_API_KEY", "realkey123")
        _serve(
            monkeypatch,
            [
                _page(
                    [{"id": 1}, {"id": 2}],
                    total=3,
                    headers={"X-RateLimit-Limit": "1000", "X-RateLimit-Remaining": "990"},
                ),
                _page(
                    [{"id": 3}],
                    total=3,
                    headers={"X-RateLimit-Limit": "1000", "X-RateLimit-Remaining": "989"},
                ),
            ],
        )
        capture = college_scorecard.walk()
        out = tmp_path / "capture.json"
        college_scorecard.write_capture(capture, out)
        summary = college_scorecard.summarize_capture(capture, out)

        assert summary["capture_sha256"] == hashlib.sha256(out.read_bytes()).hexdigest()
        assert summary["capture_bytes"] == out.stat().st_size
        assert summary["calls"] == 2
        assert summary["ratelimit_limit"] == 1000
        assert summary["ratelimit_remaining_min"] == 989
        assert "page=N" in summary["url_template"]
        assert "REDACTED" in summary["url_template"]
        assert "realkey123" not in json.dumps(summary)
        assert [p["page"] for p in summary["pages"]] == [0, 1]
        assert set(summary["pages"][0]) == {"page", "status", "bytes", "sha256"}

    def test_an_unreported_rate_limit_is_none_in_the_summary_too(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _serve(monkeypatch, self._two_pages())
        capture = college_scorecard.walk()
        out = tmp_path / "capture.json"
        college_scorecard.write_capture(capture, out)
        summary = college_scorecard.summarize_capture(capture, out)
        assert summary["ratelimit_limit"] is None
        assert summary["ratelimit_remaining_min"] is None
