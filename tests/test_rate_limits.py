"""Retry behaviour: which failures are worth waiting out, and which are ours to fix."""

from __future__ import annotations

import io
import json
import urllib.error
from typing import Any

import pytest

from disclosed.sources import college_scorecard


class _FakeResponse(io.StringIO):
    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _ok() -> _FakeResponse:
    return _FakeResponse(json.dumps({"metadata": {"total": 1}, "results": [{"id": 1}]}))


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("url", code, "boom", {}, None)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def no_real_sleeping(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record backoff durations instead of serving them, so the suite stays fast."""
    slept: list[float] = []
    monkeypatch.setattr(college_scorecard, "_sleep", slept.append)
    return slept


class TestRetries:
    def test_recovers_when_a_429_clears(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[int] = []

        def flaky(url: str, timeout: float = 0) -> _FakeResponse:
            calls.append(1)
            if len(calls) < 3:
                raise _http_error(429)
            return _ok()

        monkeypatch.setattr(college_scorecard.urllib.request, "urlopen", flaky)
        assert college_scorecard.fetch_page(0)["results"] == [{"id": 1}]
        assert len(calls) == 3

    def test_backoff_grows(
        self, monkeypatch: pytest.MonkeyPatch, no_real_sleeping: list[float]
    ) -> None:
        calls: list[int] = []

        def flaky(url: str, timeout: float = 0) -> _FakeResponse:
            calls.append(1)
            if len(calls) < 3:
                raise _http_error(429)
            return _ok()

        monkeypatch.setattr(college_scorecard.urllib.request, "urlopen", flaky)
        college_scorecard.fetch_page(0)
        assert no_real_sleeping == [2.0, 4.0]

    def test_server_errors_are_retried_too(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[int] = []

        def flaky(url: str, timeout: float = 0) -> _FakeResponse:
            calls.append(1)
            if len(calls) < 2:
                raise _http_error(503)
            return _ok()

        monkeypatch.setattr(college_scorecard.urllib.request, "urlopen", flaky)
        college_scorecard.fetch_page(0)
        assert len(calls) == 2

    def test_persistent_429_raises_rate_limited(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            college_scorecard.urllib.request,
            "urlopen",
            lambda url, timeout=0: (_ for _ in ()).throw(_http_error(429)),
        )
        with pytest.raises(college_scorecard.RateLimited, match="after 4 attempts"):
            college_scorecard.fetch_page(0)

    def test_demo_key_gets_the_specific_remedy(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The error must name the fix, because 'rate limited' alone leaves the user stuck."""
        monkeypatch.delenv("DATA_GOV_API_KEY", raising=False)
        monkeypatch.setattr(
            college_scorecard.urllib.request,
            "urlopen",
            lambda url, timeout=0: (_ for _ in ()).throw(_http_error(429)),
        )
        with pytest.raises(college_scorecard.RateLimited, match="DATA_GOV_API_KEY"):
            college_scorecard.fetch_page(0)

    def test_real_key_omits_the_demo_key_hint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DATA_GOV_API_KEY", "realkey")
        monkeypatch.setattr(
            college_scorecard.urllib.request,
            "urlopen",
            lambda url, timeout=0: (_ for _ in ()).throw(_http_error(429)),
        )
        with pytest.raises(college_scorecard.RateLimited) as caught:
            college_scorecard.fetch_page(0)
        assert "DEMO_KEY" not in str(caught.value)


class TestNoPointRetrying:
    @pytest.mark.parametrize("code", [400, 401, 403, 404])
    def test_client_errors_fail_immediately(
        self, code: int, monkeypatch: pytest.MonkeyPatch, no_real_sleeping: list[float]
    ) -> None:
        """A malformed request will stay malformed. Retrying only wastes the caller's time."""
        calls: list[int] = []

        def failing(url: str, timeout: float = 0) -> Any:
            calls.append(1)
            raise _http_error(code)

        monkeypatch.setattr(college_scorecard.urllib.request, "urlopen", failing)
        with pytest.raises(college_scorecard.ScorecardError, match=f"HTTP {code}"):
            college_scorecard.fetch_page(0)
        assert len(calls) == 1
        assert no_real_sleeping == []

    def test_rate_limited_is_catchable_as_scorecard_error(self) -> None:
        """Callers that only care that the fetch failed should not need the subclass."""
        assert issubclass(college_scorecard.RateLimited, college_scorecard.ScorecardError)
