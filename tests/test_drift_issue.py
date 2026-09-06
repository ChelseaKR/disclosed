"""The daily snapshot's finding has to arrive somewhere, and the unmeasured case most of all.

A job summary on a green run is the most reassuring possible way of saying nothing. These
tests hold the filing script to the two things that make it worth having: it files a systemic
movement, and it files a pair it could not measure rather than treating an unknown as a quiet
day. The negative control is the one that keeps the rest honest -- a non-systemic pair must
open nothing, or "it filed an issue" stops being information.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from disclosed.drift import SYSTEMIC_THRESHOLD, Snapshot, as_payload, compare

_SCRIPT = Path(__file__).resolve().parent.parent / ".github" / "scripts" / "drift_issue.py"


def _load() -> ModuleType:
    """Import the filer from its path, since it is a script rather than a package module."""
    assert _SCRIPT.is_file(), (
        f"{_SCRIPT} is gone. It is the only thing that turns a systemic drift into something "
        "anybody is told about; without it the finding stays in a job summary on a green run."
    )
    spec = importlib.util.spec_from_file_location("drift_issue", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


filer = _load()

FIELD = "Admission rate"


def _snapshot(taken: str, *, reported: int, applicable: int, label: str = FIELD) -> Snapshot:
    return Snapshot(
        taken=taken,
        institutions=applicable,
        reported={label: reported},
        missing={label: applicable - reported},
        applicable={label: applicable},
        source="College Scorecard",
    )


def _payload(earlier: Snapshot, later: Snapshot) -> dict[str, Any]:
    return as_payload(earlier, later, compare(earlier, later))


class FakeIssues:
    """Records what the script asked for, so the tests assert on calls and not on a network."""

    def __init__(self, existing: list[dict[str, Any]] | None = None) -> None:
        self.existing = existing or []
        self.created: list[Any] = []
        self.updated: list[tuple[int, Any]] = []

    def open_issues(self) -> list[dict[str, Any]]:
        return self.existing

    def create(self, spec: Any) -> None:
        self.created.append(spec)

    def update(self, number: int, spec: Any) -> None:
        self.updated.append((number, spec))


class TestASystemicPairIsFiled:
    def test_a_pair_over_the_threshold_produces_the_expected_issue(self) -> None:
        payload = _payload(
            _snapshot("2026-01-01", reported=900, applicable=1000),
            _snapshot("2026-01-02", reported=800, applicable=1000),
        )
        specs = filer.specs_from(payload)
        assert len(specs) == 1
        spec = specs[0]
        assert spec.key == f"{FIELD}|lost"
        assert FIELD in spec.title
        assert "-10.00 points" in spec.title
        assert "2026-01-01" in spec.title and "2026-01-02" in spec.title
        assert "900" in spec.body and "800" in spec.body
        assert "not closed automatically" in spec.body

    def test_the_movement_is_stated_in_points_of_the_applicable_population(self) -> None:
        payload = _payload(
            _snapshot("2026-01-01", reported=500, applicable=1000),
            _snapshot("2026-01-02", reported=560, applicable=1000),
        )
        spec = filer.specs_from(payload)[0]
        assert "+6.00 points" in spec.title
        assert spec.key == f"{FIELD}|gained"

    def test_an_applicability_move_is_explained_in_the_body(self) -> None:
        """The line that stopped three closures being published as systemic collapses."""
        payload = _payload(
            _snapshot("2026-01-01", reported=900, applicable=1000),
            _snapshot("2026-01-02", reported=700, applicable=800),
        )
        specs = filer.specs_from(payload)
        assert specs, "a 2.5 point move should be systemic"
        assert "change in who the field applies to" in specs[0].body


class TestTheNegativeControl:
    def test_a_non_systemic_pair_opens_nothing(self) -> None:
        """Without this, "an issue was filed" carries no information at all."""
        payload = _payload(
            _snapshot("2026-01-01", reported=900, applicable=1000),
            _snapshot("2026-01-02", reported=899, applicable=1000),
        )
        assert payload["fields"], "the comparison itself should still report the field"
        assert payload["fields"][0]["is_systemic"] is False
        assert filer.specs_from(payload) == []

    def test_the_control_really_is_below_the_threshold(self) -> None:
        """Proves the test above passes because the movement is small, not because the
        fixture produced no comparison at all."""
        payload = _payload(
            _snapshot("2026-01-01", reported=900, applicable=1000),
            _snapshot("2026-01-02", reported=899, applicable=1000),
        )
        rate = payload["fields"][0]["rate_change"]
        assert rate is not None
        assert 0 < abs(rate) < SYSTEMIC_THRESHOLD


class TestAnUnmeasuredPairIsFiledSeparately:
    def test_an_unmeasurable_rate_produces_a_could_not_measure_issue(self) -> None:
        payload = _payload(
            _snapshot("2026-01-01", reported=0, applicable=0),
            _snapshot("2026-01-02", reported=5, applicable=0),
        )
        specs = filer.specs_from(payload)
        assert [s.key for s in specs] == ["unmeasured"]
        assert "could not be measured" in specs[0].title
        assert FIELD in specs[0].body
        assert "not filed as systemic" in specs[0].body

    def test_an_unmeasured_field_is_never_filed_as_systemic(self) -> None:
        """`is_systemic` already refuses this; asserted here because the filer is the place
        the refusal would be quietly undone."""
        payload = _payload(
            _snapshot("2026-01-01", reported=0, applicable=0),
            _snapshot("2026-01-02", reported=5, applicable=0),
        )
        assert payload["fields"][0]["measured"] is False
        assert payload["fields"][0]["is_systemic"] is False
        assert all("Systemic drift" not in s.title for s in filer.specs_from(payload))


class TestDeduplication:
    def _spec(self) -> Any:
        payload = _payload(
            _snapshot("2026-01-01", reported=900, applicable=1000),
            _snapshot("2026-01-02", reported=800, applicable=1000),
        )
        return filer.specs_from(payload)[0]

    def test_the_same_drift_twice_updates_one_issue_rather_than_opening_two(self) -> None:
        spec = self._spec()
        client = FakeIssues([{"number": 7, "body": spec.marked_body}])
        done = filer.reconcile([spec], client)
        assert client.created == []
        assert [n for n, _ in client.updated] == [7]
        assert done == [f"updated #7: {spec.title}"]

    def test_with_no_existing_issue_it_creates_one(self) -> None:
        spec = self._spec()
        client = FakeIssues([])
        filer.reconcile([spec], client)
        assert len(client.created) == 1
        assert client.updated == []

    def test_the_same_field_moving_the_other_way_is_a_different_finding(self) -> None:
        lost = self._spec()
        gained = filer.specs_from(
            _payload(
                _snapshot("2026-01-02", reported=800, applicable=1000),
                _snapshot("2026-01-03", reported=900, applicable=1000),
            )
        )[0]
        assert lost.key != gained.key
        client = FakeIssues([{"number": 7, "body": lost.marked_body}])
        filer.reconcile([gained], client)
        assert len(client.created) == 1

    def test_the_key_is_read_from_the_body_and_not_the_title(self) -> None:
        """So that re-wording a title later cannot orphan an issue and open a second one."""
        spec = self._spec()
        client = FakeIssues(
            [{"number": 9, "title": "something a person renamed it to", "body": spec.marked_body}]
        )
        filer.reconcile([spec], client)
        assert [n for n, _ in client.updated] == [9]
        assert client.created == []

    def test_an_issue_without_a_marker_is_ignored(self) -> None:
        spec = self._spec()
        client = FakeIssues([{"number": 3, "body": "a hand-written issue about drift"}])
        filer.reconcile([spec], client)
        assert len(client.created) == 1

    def test_reconcile_closes_nothing(self) -> None:
        """There is deliberately no close path: a drift that stops appearing means the two
        most recent snapshots agree, which is a new steady state, not a resolution."""
        assert not hasattr(filer.GitHubIssues, "close")
        assert "close" not in filer.reconcile.__doc__.lower().replace("closes nothing", "")


class TestTheCommandLine:
    def _write(self, tmp_path: Path, payload: dict[str, Any]) -> str:
        path = tmp_path / "drift.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return str(path)

    def test_a_non_systemic_payload_exits_zero_and_files_nothing(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        payload = _payload(
            _snapshot("2026-01-01", reported=900, applicable=1000),
            _snapshot("2026-01-02", reported=899, applicable=1000),
        )
        assert filer.main([self._write(tmp_path, payload)]) == 0
        assert "no issue opened" in capsys.readouterr().out

    def test_dry_run_names_what_it_would_file_and_calls_nothing(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        payload = _payload(
            _snapshot("2026-01-01", reported=900, applicable=1000),
            _snapshot("2026-01-02", reported=800, applicable=1000),
        )
        assert filer.main([self._write(tmp_path, payload), "--dry-run"]) == 0
        assert "would file" in capsys.readouterr().out

    def test_a_missing_token_refuses_rather_than_exiting_quietly(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Exiting 0 with findings unfiled would make "nobody was told" look exactly like
        "there was nothing to tell", which is the failure this script was written against."""
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
        payload = _payload(
            _snapshot("2026-01-01", reported=900, applicable=1000),
            _snapshot("2026-01-02", reported=800, applicable=1000),
        )
        assert filer.main([self._write(tmp_path, payload)]) == 2
        assert "refusing to exit quietly" in capsys.readouterr().err

    def test_a_payload_that_is_not_json_is_refused(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        path = tmp_path / "bad.json"
        path.write_text("not json", encoding="utf-8")
        assert filer.main([str(path)]) == 2
        assert "not JSON" in capsys.readouterr().err

    def test_the_payload_can_come_from_stdin(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        payload = _payload(
            _snapshot("2026-01-01", reported=900, applicable=1000),
            _snapshot("2026-01-02", reported=800, applicable=1000),
        )
        monkeypatch.setattr("sys.stdin", __import__("io").StringIO(json.dumps(payload)))
        assert filer.main(["-", "--dry-run"]) == 0
        assert "would file" in capsys.readouterr().out


class TestTheJsonThePayloadComesFrom:
    def test_an_unmeasured_rate_stays_null_and_never_becomes_zero(self) -> None:
        payload = _payload(
            _snapshot("2026-01-01", reported=0, applicable=0),
            _snapshot("2026-01-02", reported=5, applicable=0),
        )
        field = payload["fields"][0]
        assert field["rate_change"] is None
        assert field["measured"] is False

    def test_the_payload_names_both_snapshot_dates(self) -> None:
        payload = _payload(
            _snapshot("2026-01-01", reported=900, applicable=1000),
            _snapshot("2026-01-02", reported=800, applicable=1000),
        )
        assert payload["earlier"] == "2026-01-01"
        assert payload["later"] == "2026-01-02"
        assert payload["systemic_threshold"] == SYSTEMIC_THRESHOLD

    def test_the_same_pair_serialises_identically_twice(self) -> None:
        earlier = _snapshot("2026-01-01", reported=900, applicable=1000)
        later = _snapshot("2026-01-02", reported=800, applicable=1000)
        first = json.dumps(_payload(earlier, later), sort_keys=True)
        second = json.dumps(_payload(earlier, later), sort_keys=True)
        assert first == second


class TestTheGitHubClient:
    """Exercised against a stubbed opener rather than left as the one untested part.

    The client is where a wrong verb or a dropped label would send the finding nowhere while
    every other test still passed, so it gets the same treatment as the logic above.
    """

    def _stub(
        self, monkeypatch: pytest.MonkeyPatch, body: Any
    ) -> list[tuple[str, str, dict[str, Any] | None]]:
        calls: list[tuple[str, str, dict[str, Any] | None]] = []

        class _Response:
            def __enter__(self) -> _Response:
                return self

            def __exit__(self, *_: object) -> None:
                return None

            def read(self) -> bytes:
                return json.dumps(body).encode("utf-8")

        def fake_urlopen(request: Any, timeout: int = 0) -> _Response:
            payload = json.loads(request.data.decode("utf-8")) if request.data else None
            calls.append((request.get_method(), request.full_url, payload))
            return _Response()

        monkeypatch.setattr(filer.urllib.request, "urlopen", fake_urlopen)
        return calls

    def test_open_issues_asks_for_open_drift_labelled_issues(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = self._stub(monkeypatch, [{"number": 1, "body": "x"}])
        found = filer.GitHubIssues("owner/repo", "t").open_issues()
        assert found == [{"number": 1, "body": "x"}]
        method, url, _ = calls[0]
        assert method == "GET"
        assert "state=open" in url and f"labels={filer.LABEL}" in url

    def test_open_issues_drops_pull_requests(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The issues endpoint returns pull requests too, and a PR is not a drift finding."""
        self._stub(
            monkeypatch,
            [{"number": 1, "body": "x"}, {"number": 2, "body": "y", "pull_request": {}}],
        )
        assert [i["number"] for i in filer.GitHubIssues("o/r", "t").open_issues()] == [1]

    def test_create_posts_the_marked_body_and_the_label(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = self._stub(monkeypatch, {"number": 5})
        spec = filer.IssueSpec(key="k", title="T", body="B")
        filer.GitHubIssues("owner/repo", "t").create(spec)
        method, url, payload = calls[0]
        assert method == "POST"
        assert url.endswith("/repos/owner/repo/issues")
        assert payload is not None
        assert payload["labels"] == [filer.LABEL]
        assert filer.MARKER_PREFIX in payload["body"]

    def test_update_patches_the_numbered_issue(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = self._stub(monkeypatch, {"number": 5})
        filer.GitHubIssues("owner/repo", "t").update(5, filer.IssueSpec("k", "T", "B"))
        method, url, payload = calls[0]
        assert method == "PATCH"
        assert url.endswith("/repos/owner/repo/issues/5")
        assert payload is not None and "labels" not in payload

    def test_a_refused_request_fails_the_step_rather_than_passing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A 403 means the finding was not filed. Exiting 0 there would put it back in the
        category of things nobody is told about."""

        def boom(request: Any, timeout: int = 0) -> Any:
            raise filer.urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, None)  # type: ignore[arg-type]

        monkeypatch.setattr(filer.urllib.request, "urlopen", boom)
        monkeypatch.setenv("GITHUB_TOKEN", "t")
        monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
        payload = _payload(
            _snapshot("2026-01-01", reported=900, applicable=1000),
            _snapshot("2026-01-02", reported=800, applicable=1000),
        )
        path = tmp_path / "d.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        assert filer.main([str(path)]) == 1
        assert "GitHub refused" in capsys.readouterr().err

    def test_a_full_run_creates_the_issue(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        calls = self._stub(monkeypatch, [])
        monkeypatch.setenv("GITHUB_TOKEN", "t")
        monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
        payload = _payload(
            _snapshot("2026-01-01", reported=900, applicable=1000),
            _snapshot("2026-01-02", reported=800, applicable=1000),
        )
        path = tmp_path / "d.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        assert filer.main([str(path)]) == 0
        assert [m for m, _, _ in calls] == ["GET", "POST"]
        assert "created:" in capsys.readouterr().out
