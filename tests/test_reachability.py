"""The reachability axis, and the wall between it and the five states.

Two claims are under test here and they are not the same claim. That the classifier turns an
observation into the weakest verdict the observation supports is the first; that no verdict it
produces can ever reach a grade is the second, and it is the one worth breaking the build over.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from disclosed import reachability as reach
from disclosed import reachability_fetch as fetch
from disclosed.disclosure import CLASSIFICATIONS, Disclosure
from disclosed.fields import IPEDS_FIELDS, field_by_key
from disclosed.grading import grade_institution

ROOT = Path(__file__).resolve().parent.parent
CAPTURE = ROOT / "data" / "reachability" / "2026-09-13.json"
REPORT = ROOT / "data" / "reachability-report.json"
NATIONAL = ROOT / "data" / "national.json"


def observation(**overrides: Any) -> reach.Observation:
    """An observation with everything stated, so a test overrides only what it is about."""
    base: dict[str, Any] = {
        "unit_id": "100654",
        "name": "A College",
        "state": "AL",
        "field_key": "ipeds.NPRICURL",
        "requested_url": "https://example.edu/npc",
        "attempts": 2,
        "status": 200,
        "final_url": "https://example.edu/npc",
        "error_kind": "",
        "skipped": "",
        "digest": "d" * 64,
        "body_bytes": 1024,
        "soft_error": False,
    }
    base.update(overrides)
    return reach.Observation(**base)


class TestTheVocabularyIsSeparate:
    """A sixth state by another name would be a sixth state."""

    def test_no_word_is_shared_with_the_five_classifications(self) -> None:
        assert reach.REACHABILITIES.isdisjoint(CLASSIFICATIONS)

    def test_no_reachability_verdict_counts_against_a_publisher(self) -> None:
        # The property that turns a classification into a finding against an institution.
        # Every reachability verdict answers False, including GONE.
        for verdict in reach.Reachability:
            assert verdict.counts_against_publisher is False

    def test_no_observation_can_produce_a_disclosure(self) -> None:
        """Over all five reachability states, nothing here returns a Disclosure member."""
        cases = [
            observation(status=200),
            observation(status=200, final_url="https://vendor.example/npc"),
            observation(status=404),
            observation(skipped=reach.SKIP_ROBOTS),
            observation(status=500),
        ]
        produced = {reach.classify(case) for case in cases}
        assert produced == set(reach.Reachability)
        for verdict in produced:
            assert not isinstance(verdict, Disclosure)
            assert verdict.value not in CLASSIFICATIONS

    def test_a_grade_is_identical_with_and_without_a_capture_present(self) -> None:
        """The load-bearing one: a reachability verdict may not move a five-state grade.

        Graded twice over the same record, once with every reachability word written into it
        under the keys a capture uses. If the grader ever started reading this module, the second
        grade would differ and this is where it would show.
        """
        record: dict[str, Any] = {
            "id": "1",
            "school.name": "A College",
            "school.state": "AL",
            "ipeds.INSTCAT": "1",
            "ipeds.CYACTIVE": "1",
            "ipeds.UGOFFER": "1",
            "ipeds.PSET4FLG": "1",
            "ipeds.ATHASSOC": "1",
            "ipeds.NPRICURL": "https://example.edu/npc",
            "ipeds.ATHURL": "",
            "ipeds.FAIDURL": "https://example.edu/aid",
            "ipeds.DISAURL": "https://example.edu/disability",
            "ipeds.ADMINURL": "https://example.edu/apply",
            "ipeds.WEBADDR": "https://example.edu/",
        }
        before = grade_institution(record, fields=IPEDS_FIELDS)
        polluted = dict(record)
        polluted["reachability"] = {f.key: reach.Reachability.GONE.value for f in IPEDS_FIELDS}
        after = grade_institution(polluted, fields=IPEDS_FIELDS)
        assert {r.field.label: r.disclosure for r in before.results} == {
            r.field.label: r.disclosure for r in after.results
        }
        assert before.score == after.score


class TestWhatTheClassifierRefusesToConclude:
    """Every branch where a lazier reading would have produced a finding."""

    def test_a_soft_404_is_indeterminate_and_not_gone(self) -> None:
        assert reach.classify(observation(status=200, soft_error=True)) is (
            reach.Reachability.INDETERMINATE
        )

    def test_a_connection_failure_is_unchecked_and_not_gone(self) -> None:
        assert reach.classify(observation(status=None, error_kind=fetch.KIND_DNS)) is (
            reach.Reachability.UNCHECKED
        )

    @pytest.mark.parametrize("status", sorted(reach.REFUSED_STATUSES))
    def test_a_host_refusing_this_client_is_unchecked_and_not_gone(self, status: int) -> None:
        assert reach.classify(observation(status=status)) is reach.Reachability.UNCHECKED

    @pytest.mark.parametrize("status", [500, 502, 503, 400, 405, 302])
    def test_nothing_but_404_and_410_is_gone(self, status: int) -> None:
        assert reach.classify(observation(status=status)) is reach.Reachability.INDETERMINATE

    @pytest.mark.parametrize("status", sorted(reach.ABSENT_STATUSES))
    def test_the_server_saying_it_is_not_there_is_gone(self, status: int) -> None:
        assert reach.classify(observation(status=status)) is reach.Reachability.GONE

    @pytest.mark.parametrize(
        "reason",
        [reach.SKIP_ROBOTS, reach.SKIP_ROBOTS_UNREADABLE, reach.SKIP_BUDGET, reach.SKIP_NO_URL],
    )
    def test_every_reason_for_not_looking_is_unchecked(self, reason: str) -> None:
        assert reach.classify(observation(skipped=reason)) is reach.Reachability.UNCHECKED

    def test_a_www_alias_has_not_moved(self) -> None:
        moved = observation(
            requested_url="https://example.edu/npc", final_url="https://www.example.edu/npc"
        )
        assert reach.classify(moved) is reach.Reachability.READABLE

    def test_a_different_path_on_the_same_host_has_not_moved(self) -> None:
        same = observation(
            requested_url="https://example.edu/npc", final_url="https://example.edu/admissions/npc"
        )
        assert reach.classify(same) is reach.Reachability.READABLE

    def test_a_different_host_has_moved(self) -> None:
        elsewhere = observation(
            requested_url="https://example.edu/npc", final_url="https://npc.vendor.com/example"
        )
        assert reach.classify(elsewhere) is reach.Reachability.MOVED


class TestTheTwoNumbers:
    """`published` and `verified_retrievable` are different claims and are counted separately."""

    def coverage(self, *cases: reach.Observation, published: int = 10) -> reach.FieldReach:
        rows = reach.coverage(
            cases,
            published={"ipeds.NPRICURL": published},
            labels={"ipeds.NPRICURL": "Net price calculator"},
            statutes={"ipeds.NPRICURL": "20 U.S.C. 1015a(h)(3)"},
        )
        assert len(rows) == 1
        return rows[0]

    def test_verified_is_readable_plus_moved(self) -> None:
        row = self.coverage(
            observation(status=200),
            observation(status=200, final_url="https://vendor.example/x"),
            observation(status=404),
        )
        assert row.verified_retrievable == 2
        assert row.attempted == 3

    def test_the_denominator_is_what_was_published_not_what_was_attempted(self) -> None:
        row = self.coverage(observation(status=200), published=100)
        assert row.published == 100
        assert row.attempted == 1
        assert row.share_verified == pytest.approx(0.01)

    def test_a_field_nobody_published_has_no_verified_share(self) -> None:
        row = self.coverage(observation(status=200), published=0)
        assert row.share_verified is None, "a zero here would read as every address having failed"

    def test_every_verdict_word_appears_in_the_counts_even_at_zero(self) -> None:
        row = self.coverage(observation(status=200))
        assert set(row.as_dict()["counts"]) == reach.REACHABILITIES


class TestDrift:
    """Two captures, and the direction word read from the rate."""

    def field(self, *, verified: int, published: int) -> reach.FieldReach:
        return reach.FieldReach(
            label="Net price calculator",
            key="ipeds.NPRICURL",
            statute="20 U.S.C. 1015a(h)(3)",
            published=published,
            attempted=verified,
            counts={reach.Reachability.READABLE.value: verified},
        )

    def test_the_direction_is_read_from_the_rate_and_not_from_the_count(self) -> None:
        # Fewer verified addresses, over a denominator that shrank further: the share rose.
        was = [self.field(verified=100, published=200)]
        now = [self.field(verified=80, published=100)]
        (drift,) = reach.compare(was, now)
        assert drift.now_verified < drift.was_verified
        assert drift.direction == "gained"

    def test_a_rate_that_did_not_move_is_unchanged_and_not_lost(self) -> None:
        (drift,) = reach.compare(
            [self.field(verified=50, published=100)], [self.field(verified=25, published=50)]
        )
        assert drift.direction == "unchanged"

    def test_an_unmeasurable_rate_is_unmeasured_and_not_a_movement(self) -> None:
        (drift,) = reach.compare(
            [self.field(verified=0, published=0)], [self.field(verified=5, published=10)]
        )
        assert drift.rate_change is None
        assert drift.direction == "unmeasured"
        assert drift.measured is False

    def test_a_field_only_one_capture_covered_is_skipped_not_compared_to_zero(self) -> None:
        other = reach.FieldReach(
            label="Equity in athletics disclosure",
            key="ipeds.ATHURL",
            statute="20 U.S.C. 1092(g)",
            published=10,
            attempted=10,
            counts={reach.Reachability.READABLE.value: 10},
        )
        assert reach.compare([self.field(verified=1, published=2)], [other]) == ()


class TestTheCommittedCapture:
    """The replay contract the other committed artifacts already hold, applied to this one."""

    def test_the_capture_exists_and_states_the_day_it_was_taken(self) -> None:
        capture = json.loads(CAPTURE.read_text(encoding="utf-8"))
        assert capture["captured_on"], "a reachability figure without its date is a claim about now"
        assert capture["capture_format_version"] == reach.CAPTURE_FORMAT_VERSION
        assert capture["politeness"]["robots_txt"]
        assert capture["politeness"]["attempts_before_a_negative_verdict"] >= 2

    def test_the_report_replays_byte_for_byte_from_the_committed_capture(self) -> None:
        capture = json.loads(CAPTURE.read_text(encoding="utf-8"))
        graded = json.loads(NATIONAL.read_text(encoding="utf-8"))
        published = {str(row["key"]): int(row["reported"]) for row in graded["fields"]}
        rebuilt = reach.build_report(
            capture,
            published=published,
            labels={f.key: f.label for f in IPEDS_FIELDS},
            statutes={f.key: f.statute for f in IPEDS_FIELDS},
        )
        assert json.dumps(rebuilt, indent=2, sort_keys=True) == REPORT.read_text(
            encoding="utf-8"
        ).rstrip("\n")

    def test_no_negative_verdict_rests_on_a_single_attempt(self) -> None:
        """The rule that makes a named finding publishable at all."""
        capture = json.loads(CAPTURE.read_text(encoding="utf-8"))
        for row in reach.observations_from_capture(capture):
            if reach.classify(row) is reach.Reachability.GONE:
                assert row.attempts >= 2, f"{row.unit_id} was called gone after one attempt"

    def test_every_address_visited_was_one_the_grader_counted_as_published(self) -> None:
        capture = json.loads(CAPTURE.read_text(encoding="utf-8"))
        keys = {row.field_key for row in reach.observations_from_capture(capture)}
        for key in keys:
            assert field_by_key(key).text_is_a_value


class TestThePoliteFetcher:
    """Every toll, exercised without a socket."""

    def session(
        self, pages: dict[str, fetch.Attempt], *, requests: int = 50
    ) -> tuple[fetch.Session, list[str]]:
        asked: list[str] = []

        def fake(url: str, **_: Any) -> fetch.Attempt:
            asked.append(url)
            return pages.get(url, fetch.Attempt(url, 200, url, "<html>ok</html>", 15, "", ""))

        session = fetch.Session(
            budget=fetch.Budget(requests=requests),
            fetch=fake,
            sleep=lambda _: None,
            clock=lambda: 0.0,
        )
        return session, asked

    def robots(self, url: str, body: str, status: int = 200) -> fetch.Attempt:
        return fetch.Attempt(url, status, url, body, len(body), "", "")

    def test_a_disallowed_address_is_never_requested(self) -> None:
        session, asked = self.session(
            {
                "https://x.edu/robots.txt": self.robots(
                    "https://x.edu/robots.txt", "User-agent: *\nDisallow: /npc\n"
                )
            }
        )
        attempt, skip = session.get("https://x.edu/npc")
        assert attempt is None
        assert skip == reach.SKIP_ROBOTS
        assert asked == ["https://x.edu/robots.txt"]

    def test_an_unreadable_robots_txt_disallows_the_whole_host(self) -> None:
        session, asked = self.session(
            {"https://x.edu/robots.txt": self.robots("https://x.edu/robots.txt", "", status=503)}
        )
        _, skip = session.get("https://x.edu/npc")
        assert skip == reach.SKIP_ROBOTS_UNREADABLE
        _, second = session.get("https://x.edu/other")
        assert second == reach.SKIP_ROBOTS_UNREADABLE
        assert asked == ["https://x.edu/robots.txt"], "the verdict is cached, not re-probed"

    def test_a_missing_robots_txt_permits_the_fetch(self) -> None:
        session, asked = self.session(
            {"https://x.edu/robots.txt": self.robots("https://x.edu/robots.txt", "", status=404)}
        )
        attempt, skip = session.get("https://x.edu/npc")
        assert skip == ""
        assert attempt is not None and attempt.status == 200
        assert asked == ["https://x.edu/robots.txt", "https://x.edu/npc"]

    def test_a_robots_probe_that_never_connected_is_not_cached_as_a_policy(self) -> None:
        session, asked = self.session(
            {
                "https://x.edu/robots.txt": fetch.Attempt(
                    "https://x.edu/robots.txt", None, "", "", 0, fetch.KIND_DNS, "no route"
                )
            }
        )
        _, skip = session.get("https://x.edu/npc")
        assert skip == "", "a dead host must not masquerade as a crawling restriction"
        session.get("https://x.edu/other")
        assert asked.count("https://x.edu/robots.txt") == 2

    def test_the_budget_is_a_ceiling_the_robots_probes_also_pay(self) -> None:
        session, asked = self.session({}, requests=1)
        attempt, skip = session.get("https://x.edu/npc")
        assert (attempt, skip) == (None, reach.SKIP_BUDGET)
        assert len(asked) == 1, "the one request went on the robots probe, and nothing followed"

    def test_a_second_request_to_one_host_waits(self) -> None:
        slept: list[float] = []
        session = fetch.Session(
            budget=fetch.Budget(requests=10),
            fetch=lambda url, **_: fetch.Attempt(url, 200, url, "", 0, "", ""),
            sleep=slept.append,
            clock=lambda: 0.0,
            per_host_interval=2.0,
        )
        session.get("https://x.edu/npc")
        assert slept == [2.0], "robots then page on one host is two requests and one wait"


class TestTheRetryRule:
    """Nothing about a named institution rests on one failed attempt."""

    def observe(self, page: fetch.Attempt) -> reach.Observation:
        def fake(url: str, **_: Any) -> fetch.Attempt:
            if url.endswith("/robots.txt"):
                return fetch.Attempt(url, 404, url, "", 0, "", "")
            return page

        session = fetch.Session(
            budget=fetch.Budget(requests=50),
            fetch=fake,
            sleep=lambda _: None,
            clock=lambda: 0.0,
        )
        target = fetch.Target("1", "A College", "AL", "ipeds.NPRICURL", "https://x.edu/npc")
        return fetch.observe(session, target)

    def test_a_404_is_asked_again_before_it_is_written_down(self) -> None:
        seen = self.observe(
            fetch.Attempt("https://x.edu/npc", 404, "https://x.edu/npc", "", 0, "", "")
        )
        assert seen.attempts == 2
        assert reach.classify(seen) is reach.Reachability.GONE

    def test_a_document_is_not_asked_for_twice(self) -> None:
        seen = self.observe(
            fetch.Attempt("https://x.edu/npc", 200, "https://x.edu/npc", "<html/>", 7, "", "")
        )
        assert seen.attempts == 1

    def test_an_address_with_no_fetchable_form_is_skipped_not_requested(self) -> None:
        session = fetch.Session(
            budget=fetch.Budget(requests=5),
            fetch=lambda url, **_: pytest.fail(f"nothing should have been requested, got {url}"),
            sleep=lambda _: None,
            clock=lambda: 0.0,
        )
        target = fetch.Target("1", "A College", "AL", "ipeds.NPRICURL", "mailto:aid@x.edu")
        seen = fetch.observe(session, target)
        assert seen.skipped == reach.SKIP_NO_URL
        assert reach.classify(seen) is reach.Reachability.UNCHECKED


class TestNormalizingAnAddressAsTheRecordWroteIt:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("", None),
            ("   ", None),
            ("mailto:aid@x.edu", None),
            ("tel:5551234", None),
            ("ftp://x.edu/npc", None),
            ("x.edu/npc", "https://x.edu/npc"),
            ("x.edu:8080/npc", "https://x.edu:8080/npc"),
            ("https://x.edu/a%20b", "https://x.edu/a%20b"),
            ("https://x.edu/College Name", "https://x.edu/College%20Name"),
            ("https://x.edu/npc#anchor", "https://x.edu/npc"),
        ],
    )
    def test_normalize(self, raw: str, expected: str | None) -> None:
        assert fetch.normalize_url(raw) == expected


class TestTheSoftErrorHeuristic:
    @pytest.mark.parametrize(
        "title",
        ["404 - Page Not Found", "Page Not Found", "Access Denied", "Service Unavailable"],
    )
    def test_an_error_title_is_a_soft_error(self, title: str) -> None:
        assert fetch.looks_like_a_soft_error(f"<html><title>{title}</title></html>")

    @pytest.mark.parametrize(
        "body",
        [
            "<html><title>Net Price Calculator</title>the page was not found in our records</html>",
            "<html><body>404</body></html>",
            "<html/>",
        ],
    )
    def test_prose_is_not_a_title_and_does_not_count(self, body: str) -> None:
        assert not fetch.looks_like_a_soft_error(body)


class TestBuildingTheTargetList:
    def test_only_cells_the_grader_counted_as_published_become_targets(self) -> None:
        records = [
            {
                "id": "1",
                "school.name": "Reaches the duty and published",
                "school.state": "AL",
                "ipeds.INSTCAT": "1",
                "ipeds.CYACTIVE": "1",
                "ipeds.UGOFFER": "1",
                "ipeds.PSET4FLG": "1",
                "ipeds.NPRICURL": "https://x.edu/npc",
            },
            {
                "id": "2",
                "school.name": "Graduate only, published anyway",
                "school.state": "AL",
                "ipeds.INSTCAT": "1",
                "ipeds.CYACTIVE": "1",
                "ipeds.UGOFFER": "2",
                "ipeds.PSET4FLG": "1",
                "ipeds.NPRICURL": "https://y.edu/npc",
            },
            {
                "id": "3",
                "school.name": "Reaches the duty, published nothing",
                "school.state": "AL",
                "ipeds.INSTCAT": "1",
                "ipeds.CYACTIVE": "1",
                "ipeds.UGOFFER": "1",
                "ipeds.PSET4FLG": "1",
                "ipeds.NPRICURL": "",
            },
        ]
        targets = fetch.targets_from_records(records, fields=(field_by_key("ipeds.NPRICURL"),))
        assert [t.unit_id for t in targets] == ["1"]
