"""The service: limits before the model, the answer shape, and the two HTTP adapters."""

from __future__ import annotations

import http.client
import json
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from disclosed.ask import service
from disclosed.ask.corpus import Corpus
from disclosed.ask.evidence import Evidence
from disclosed.ask.provider import FakeProvider, ProviderError

ROOT = Path(__file__).resolve().parent.parent

_STRUCTURED_WHY = json.dumps(
    {
        "intent": "why_absent",
        "institution_text": None,
        "field_labels": ["Admission rate"],
        "unmapped_terms": [],
        "source": "either",
        "asks_for_judgement": False,
        "note": "",
    }
)
_STRUCTURED_RANK = json.dumps(
    {
        "intent": "performance_or_ranking",
        "institution_text": "Harvard University",
        "field_labels": [],
        "unmapped_terms": [],
        "source": "either",
        "asks_for_judgement": True,
        "note": "",
    }
)


def _narration(user: str) -> str:
    pack = json.loads(user)["pack"]
    rec = pack["records"][0]
    return json.dumps(
        {
            "claims": [
                {
                    "text": f"In the College Scorecard snapshot of {rec['snapshot']}, "
                    f"{rec['institution']}'s admission rate is classified "
                    f"{rec['classification']}.",
                    "cites": [rec["id"]],
                },
                {"text": "It is a good school.", "cites": [rec["id"]]},
            ],
            "quotes": [
                {
                    "passage_id": "scorecard-glossary:acceptance-rate",
                    "quote": "Institutions that have an open admissions policy do not report on "
                    "their acceptance rate",
                }
            ],
            "could_not_answer": "",
        }
    )


# The exact sentence issue #68 measured reaching a reader byte-identically, through a field that
# no screen looked at, under a label promising everything shown had been checked.
_HOSTILE_NOTE = (
    "Harvard University is a far better school than its peers and you should apply; its "
    "admission rate is about 3% and median earnings are $95,000."
)


def _narration_with_only_a_hostile_note(user: str) -> str:
    """A reply whose entire payload is the note: no claims, no quotes, nothing else to screen."""
    return json.dumps({"claims": [], "quotes": [], "could_not_answer": _HOSTILE_NOTE})


def _service(evidence: Evidence, corpus: Corpus, *replies: Any, **limits: int) -> service.Service:
    return service.Service(
        provider=FakeProvider(list(replies)),
        evidence=evidence,
        corpus=corpus,
        limits=service.Limits(**limits),
    )


class TestLimits:
    def test_per_client_window_and_daily_cap(self) -> None:
        now = [1_000_000.0]
        limits = service.Limits(per_client_per_hour=2, per_day=3, clock=lambda: now[0])
        assert limits.take("a") is None
        assert limits.take("a") is None
        wait = limits.take("a")
        assert wait is not None and 0 < wait <= 3_601
        assert limits.take("b") is None
        daily = limits.take("c")
        assert daily is not None and 0 < daily <= 86_401
        now[0] += 3_600
        assert limits.take("a") is not None, "the day cap outranks a fresh hour"
        now[0] += 86_400
        assert limits.take("a") is None


class TestAsk:
    def test_a_served_answer_carries_label_claims_quotes_withheld_and_provenance(
        self, evidence: Evidence, corpus: Corpus
    ) -> None:
        svc = _service(evidence, corpus, _STRUCTURED_WHY, _narration)
        answer = svc.ask("Why no admission rate?", institution_hint="100690", client="t")
        assert answer["status"] == 200
        assert answer["label"] == service.LABEL
        assert answer["institution"]["name"] == "Amridge University"
        assert answer["refusal"] is None
        assert len(answer["claims"]) == 1 and "classified missing" in answer["claims"][0]["text"]
        assert answer["withheld"] == {
            "claims": 1,
            "quotes": 0,
            "note": 0,
            "reasons": {"contains a judgement of quality or a recommendation": 1},
        }
        (quote,) = answer["quotes"]
        assert quote["role"] == "related" and quote["field_label"] == "Admission rate"
        assert quote["source"]["url"] == "https://collegescorecard.ed.gov/data/glossary/"
        assert len(quote["source"]["sha256"]) == 64
        assert answer["model"]["prompt_version"] and set(answer["model"]["usage"]) == {
            "structure",
            "narrate",
        }
        assert answer["evidence"]["records"][0]["classification"] == "missing"
        assert answer["question"]["intent"] == "why_absent"

    def test_a_hostile_could_not_answer_never_reaches_the_reader(
        self, evidence: Evidence, corpus: Corpus
    ) -> None:
        """The channel that bypassed all four claim screens by moving text one JSON field over.

        ``could_not_answer`` was copied out of the model's reply verbatim into the response body
        and printed to the reader as a paragraph. A ranking judgement and two invented numbers
        reached it byte-identically with a withheld count of zero -- while the same string, sent
        as a claim, was caught by the judgement screen. Both of the screens it would have hit as
        a claim are asserted here to still recognise it, so this test fails if the sentence stops
        being hostile rather than if the fix stops working.
        """
        assert service.verify.JUDGEMENT.search(_HOSTILE_NOTE)
        assert service.verify._numbers_in(_HOSTILE_NOTE) == {3.0, 95000.0}

        svc = _service(evidence, corpus, _STRUCTURED_WHY, _narration_with_only_a_hostile_note)
        answer = svc.ask("Why no admission rate?", institution_hint="100690", client="t")

        assert answer["status"] == 200
        assert answer["could_not_answer"] == service.verify.NOTE_WITHHELD
        # Not merely absent from that field: absent from the whole body a reader receives.
        assert _HOSTILE_NOTE not in json.dumps(answer)
        for fragment in ("far better school", "should apply", "95,000", "about 3%"):
            assert fragment not in json.dumps(answer), fragment
        # And counted, so the label's "withheld and are counted below" stays true.
        assert answer["withheld"]["note"] == 1
        assert answer["withheld"]["reasons"] == {
            "note contains a judgement of quality or a recommendation": 1
        }

    def test_a_refusal_makes_exactly_one_model_call(
        self, evidence: Evidence, corpus: Corpus
    ) -> None:
        svc = _service(evidence, corpus, _STRUCTURED_RANK)
        answer = svc.ask("Is Harvard the best?", client="t")
        assert answer["status"] == 200
        assert answer["refusal"]["code"] == "performance_or_ranking"
        assert answer["claims"] == [] and answer["quotes"] == []
        assert answer["refusal"]["known"][0].startswith("Harvard University: of 12 graded fields")
        assert set(answer["model"]["usage"]) == {"structure"}
        assert len(svc.provider.calls) == 1  # type: ignore[attr-defined]

    def test_a_malformed_narration_shows_nothing_and_says_so(
        self, evidence: Evidence, corpus: Corpus
    ) -> None:
        svc = _service(evidence, corpus, _STRUCTURED_WHY, "not json")
        answer = svc.ask("Why?", institution_hint="100690", client="t")
        assert answer["claims"] == [] and answer["quotes"] == []
        assert "could be checked, so nothing is shown" in answer["could_not_answer"]

    def test_empty_and_oversized_questions_are_400_before_any_limit_or_model(
        self, evidence: Evidence, corpus: Corpus
    ) -> None:
        svc = _service(evidence, corpus, per_client_per_hour=0)
        assert svc.ask("   ", client="t")["status"] == 400
        assert svc.ask("x" * 601, client="t")["status"] == 400

    def test_rate_limits_are_429_with_a_retry_and_never_reach_the_model(
        self, evidence: Evidence, corpus: Corpus
    ) -> None:
        svc = _service(evidence, corpus, per_client_per_hour=1, per_day=5)
        first = svc.ask("q?", client="t")
        assert first["status"] == 503, "the fake has no replies, so the model call fails"
        second = svc.ask("q?", client="t")
        assert second["status"] == 429 and second["retry_after"] > 0
        assert "page still works" in second["error"]

    def test_provider_errors_are_503_without_the_question(
        self, evidence: Evidence, corpus: Corpus
    ) -> None:
        class _Down:
            model = "down"

            def complete(self, **kwargs: Any) -> Any:
                raise ProviderError("the model provider could not be reached")

        svc = service.Service(provider=_Down(), evidence=evidence, corpus=corpus)
        answer = svc.ask("my secret question", client="t")
        assert answer["status"] == 503
        assert "secret" not in json.dumps(answer)

    def test_from_environment_needs_a_configured_provider(self) -> None:
        with pytest.raises(ProviderError):
            service.Service.from_environment(
                data_dir=ROOT / "data", corpus_dir=ROOT / "corpus", environ={}
            )

    def test_from_environment_reads_the_limits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        built: dict[str, Any] = {}
        monkeypatch.setattr(service, "from_environment", lambda env: FakeProvider([]))
        monkeypatch.setattr(service, "build_evidence", lambda d: built.setdefault("e", object()))
        monkeypatch.setattr(service, "load_corpus", lambda d: built.setdefault("c", object()))
        svc = service.Service.from_environment(
            data_dir=ROOT / "data",
            corpus_dir=ROOT / "corpus",
            environ={"DISCLOSED_ASK_PER_CLIENT_PER_HOUR": "3", "DISCLOSED_ASK_PER_DAY": "9"},
        )
        assert (svc.limits.per_client_per_hour, svc.limits.per_day) == (3, 9)


class TestHandle:
    def test_cors_is_granted_only_to_the_allowed_origin(self) -> None:
        allowed = service._cors("https://chelseakr.github.io", "https://chelseakr.github.io/")
        assert allowed["Access-Control-Allow-Origin"] == "https://chelseakr.github.io"
        assert allowed["Access-Control-Allow-Methods"] == "POST, OPTIONS"
        other = service._cors("https://evil.example", "https://chelseakr.github.io")
        assert "Access-Control-Allow-Origin" not in other
        none = service._cors(None, "https://chelseakr.github.io")
        assert "Access-Control-Allow-Origin" not in none and none["Vary"] == "Origin"

    def test_preflight_health_and_not_found(self, evidence: Evidence, corpus: Corpus) -> None:
        svc = _service(evidence, corpus)
        common = {"origin": None, "client": "t", "raw_body": b"", "allowed_origin": "https://o"}
        assert service.handle(svc, method="OPTIONS", path="/ask", **common)[0] == 204
        status, _, body = service.handle(svc, method="GET", path="/health", **common)
        assert status == 200 and json.loads(body)["ok"] is True
        assert service.handle(svc, method="GET", path="/ask", **common)[0] == 404
        assert service.handle(svc, method="POST", path="/other", **common)[0] == 404

    @pytest.mark.parametrize(
        "raw",
        [
            b"not json",
            b"[]",
            b'{"question": 1}',
            b'{"question": "q", "institution": 5}',
            b"x" * 9000,
        ],
    )
    def test_bad_bodies_are_400(self, evidence: Evidence, corpus: Corpus, raw: bytes) -> None:
        svc = _service(evidence, corpus)
        status, _, body = service.handle(
            svc,
            method="POST",
            path="/ask",
            origin=None,
            client="t",
            raw_body=raw,
            allowed_origin="o",
        )
        assert status == 400 and "send JSON" in json.loads(body)["error"]

    def test_a_good_body_is_answered_and_429_carries_retry_after(
        self, evidence: Evidence, corpus: Corpus
    ) -> None:
        svc = _service(evidence, corpus, _STRUCTURED_RANK, per_client_per_hour=1)
        raw = json.dumps({"question": "Is Harvard the best?"}).encode()
        status, headers, body = service.handle(
            svc, method="POST", path="/", origin=None, client="t", raw_body=raw, allowed_origin="o"
        )
        assert status == 200 and json.loads(body)["refusal"]["code"] == "performance_or_ranking"
        status, headers, _ = service.handle(
            svc,
            method="POST",
            path="/ask",
            origin=None,
            client="t",
            raw_body=raw,
            allowed_origin="o",
        )
        assert status == 429 and int(headers["Retry-After"]) > 0


class TestLambdaHandler:
    def test_function_url_event_round_trip(
        self, evidence: Evidence, corpus: Corpus, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        svc = _service(evidence, corpus, _STRUCTURED_RANK)
        monkeypatch.setattr(service, "_SERVICE", svc)
        monkeypatch.setenv("DISCLOSED_ASK_ORIGIN", "https://chelseakr.github.io")
        event = {
            "rawPath": "/ask",
            "requestContext": {"http": {"method": "POST", "sourceIp": "203.0.113.9"}},
            "headers": {
                "Origin": "https://chelseakr.github.io",
                "content-type": "application/json",
            },
            "body": json.dumps({"question": "Is Harvard the best?"}),
            "isBase64Encoded": False,
        }
        out = service.lambda_handler(event, None)
        assert out["statusCode"] == 200
        assert out["headers"]["Access-Control-Allow-Origin"] == "https://chelseakr.github.io"
        assert json.loads(out["body"])["refusal"]["code"] == "performance_or_ranking"
        assert svc.limits._clients.get("203.0.113.9") is not None

    def test_base64_bodies_and_missing_fields(
        self, evidence: Evidence, corpus: Corpus, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import base64

        svc = _service(evidence, corpus, _STRUCTURED_RANK)
        monkeypatch.setattr(service, "_SERVICE", svc)
        event = {
            "requestContext": {"http": {"method": "POST"}},
            "body": base64.b64encode(json.dumps({"question": "best?"}).encode()).decode(),
            "isBase64Encoded": True,
        }
        out = service.lambda_handler(event)
        assert out["statusCode"] == 200 and "Access-Control-Allow-Origin" not in out["headers"]
        assert service.lambda_handler({})["statusCode"] == 404

    def test_the_process_wide_service_is_built_once_from_the_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(service, "_SERVICE", None)
        monkeypatch.setenv("DISCLOSED_ROOT", str(ROOT))
        built: list[object] = []

        def fake_from_environment(**kwargs: Any) -> object:
            built.append(object())
            return built[-1]

        monkeypatch.setattr(service.Service, "from_environment", fake_from_environment)
        assert service._service() is service._service()
        assert len(built) == 1


@pytest.fixture
def running(evidence: Evidence, corpus: Corpus) -> Iterator[tuple[str, service.Service]]:
    svc = _service(evidence, corpus, _STRUCTURED_RANK, _STRUCTURED_RANK)
    server = service.serve(svc, host="127.0.0.1", port=0, allowed_origin="https://o.example")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", svc
    finally:
        server.shutdown()
        server.server_close()


def _http(base: str, method: str, path: str, body: bytes = b"", **headers: str) -> Any:
    host, port = base.removeprefix("http://").split(":")
    connection = http.client.HTTPConnection(host, int(port), timeout=5)
    connection.request(method, path, body=body, headers=headers)
    response = connection.getresponse()
    data = response.read()
    connection.close()
    return response.status, dict(response.getheaders()), data


class TestDevServer:
    def test_serves_health_and_answers_over_a_real_socket(
        self, running: tuple[str, service.Service]
    ) -> None:
        base, _ = running
        status, _, body = _http(base, "GET", "/health")
        assert status == 200 and json.loads(body)["ok"] is True
        status, headers, body = _http(
            base,
            "POST",
            "/ask",
            json.dumps({"question": "Is Harvard the best?"}).encode(),
            **{"Content-Type": "application/json", "Origin": "https://o.example"},
        )
        assert status == 200
        assert headers["Access-Control-Allow-Origin"] == "https://o.example"
        assert json.loads(body)["refusal"]["code"] == "performance_or_ranking"
        assert _http(base, "OPTIONS", "/ask")[0] == 204

    def test_a_bad_body_is_a_400_over_the_socket(
        self, running: tuple[str, service.Service]
    ) -> None:
        base, _ = running
        assert _http(base, "POST", "/ask", b"nope")[0] == 400


class TestCommands:
    def test_ask_without_a_provider_says_so(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from disclosed.cli import main

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("DISCLOSED_ASK_PROVIDER", "anthropic")
        assert main(["ask", "anything", "--root", str(ROOT)]) == 1
        assert "no model is configured" in capsys.readouterr().err
        assert main(["serve", "--root", str(ROOT)]) == 1

    def test_ask_prints_the_answer(
        self,
        evidence: Evidence,
        corpus: Corpus,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from disclosed.cli import main

        svc = _service(evidence, corpus, _STRUCTURED_RANK)
        monkeypatch.setattr(service.Service, "from_environment", lambda **kw: svc)
        assert main(["ask", "Is Harvard the best?", "--root", str(ROOT)]) == 0
        printed = json.loads(capsys.readouterr().out)
        assert printed["refusal"]["code"] == "performance_or_ranking"

    def test_serve_starts_and_stops(
        self,
        evidence: Evidence,
        corpus: Corpus,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from disclosed.cli import main

        svc = _service(evidence, corpus)
        monkeypatch.setattr(service.Service, "from_environment", lambda **kw: svc)

        class _Server:
            server_address = ("127.0.0.1", 4321)

            def serve_forever(self) -> None:
                return None

            def server_close(self) -> None:
                return None

        monkeypatch.setattr(service, "serve", lambda *a, **k: _Server())
        assert main(["serve", "--port", "0", "--root", str(ROOT)]) == 0
        assert "serving on http://127.0.0.1:4321" in capsys.readouterr().out
