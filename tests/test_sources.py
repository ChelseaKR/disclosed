"""The source adapter, including the failure it must not paper over.

The adapter's one job beyond fetching is to refuse to return partial data. A truncated fetch would
understate disclosure across every institution that never arrived, which on the published page looks
exactly like a real collapse in reporting. These tests exist mostly to hold that line.
"""

from __future__ import annotations

import io
import json
import urllib.error
from collections.abc import Iterator
from typing import Any

import pytest

from disclosed.fields import SCORECARD_API_FIELDS
from disclosed.sources import college_scorecard


class _FakeResponse(io.StringIO):
    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _page(results: list[dict[str, Any]], total: int) -> _FakeResponse:
    return _FakeResponse(json.dumps({"metadata": {"total": total}, "results": results}))


@pytest.fixture
def captured_urls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    urls: list[str] = []

    def fake_urlopen(url: str, timeout: float = 0) -> _FakeResponse:
        urls.append(url)
        return _page([{"id": len(urls), "school.name": f"School {len(urls)}"}], total=1)

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

    def test_non_dict_rows_are_skipped_not_yielded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        it = iter([_page([{"id": 1}, "junk", {"id": 2}], total=2)])  # type: ignore[list-item]
        monkeypatch.setattr(
            college_scorecard.urllib.request, "urlopen", lambda url, timeout=0: next(it)
        )
        assert [r["id"] for r in college_scorecard.iter_institutions()] == [1, 2]

    def test_missing_metadata_does_not_loop_forever(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A malformed payload must terminate rather than page indefinitely."""
        pages = [
            _FakeResponse(json.dumps({"results": [{"id": 1}]})),
            _FakeResponse(json.dumps({"results": []})),
        ]
        it = iter(pages)
        monkeypatch.setattr(
            college_scorecard.urllib.request, "urlopen", lambda url, timeout=0: next(it)
        )
        assert [r["id"] for r in college_scorecard.iter_institutions()] == [1]
