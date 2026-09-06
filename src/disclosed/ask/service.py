"""The service: one question in, one verified answer out, with the cost bounded before the model.

The whole request path, in order: rate limits (per client, and a hard daily cap for the
deployment as a whole) -> the model structures the question -> the policy layer resolves,
refuses, or gathers the pack -> the model narrates the pack -> the verifier keeps what it can
prove -> the answer is rendered with its label, its withheld counts, and the provenance of every
quote. A refusal ends the path before the second model call; a rate limit ends it before the
first.

Every answer carries the same label: AI-generated, unofficial, about disclosure and not quality.
The service keeps no request body. It logs nothing a reader typed. The in-memory limits are per
process, which is a stated limitation of a single-container deployment rather than a guarantee;
the deployment template carries the hard bound (reserved concurrency and a budget alarm) that
does not depend on this process remembering anything.

Two thin adapters sit on top of :class:`Service` and share its body: a Lambda Function URL
handler and a standard-library development server. Neither is a framework, because the path above
is the product and a framework would be a second thing to keep honest.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Final

from . import lookup, narrate, structure, verify
from .corpus import Corpus
from .corpus import load as load_corpus
from .evidence import Evidence
from .evidence import build as build_evidence
from .provider import Provider, ProviderError, from_environment

__all__ = ["LABEL", "Limits", "Service", "lambda_handler", "serve"]

LABEL: Final[str] = (
    "AI-generated and unofficial. This describes what the institution disclosed to federal "
    "sources, not how it performs; a disclosure grade is not a quality grade. Every statement "
    "shown was checked against the project's own records; statements that could not be checked "
    "were withheld and are counted below."
)

DEFAULT_ORIGIN: Final[str] = "https://chelseakr.github.io"
_MAX_QUESTION_CHARS: Final[int] = 600
_MAX_BODY_BYTES: Final[int] = 8_192


@dataclass(slots=True)
class Limits:
    """Per-client and whole-service caps, counted in memory, reset by the clock.

    ``per_client_per_hour`` is a fixed window per client key (the caller's address); ``per_day``
    is the service's own ceiling, the number past which no question is answered until the UTC
    day turns, whatever the client. Both default low on purpose: a public page with a model
    behind it is a cost dial somebody else can turn.
    """

    per_client_per_hour: int = 20
    per_day: int = 400
    clock: Callable[[], float] = time.time
    _clients: dict[str, tuple[int, int]] = field(default_factory=dict)
    _day: tuple[int, int] = (0, 0)

    def take(self, client: str) -> int | None:
        """Consume one request for ``client``; ``None`` if allowed, else seconds to wait."""
        now = self.clock()
        day = int(now // 86_400)
        day_count = self._day[1] if self._day[0] == day else 0
        if day_count >= self.per_day:
            return int(86_400 - now % 86_400) + 1
        hour = int(now // 3_600)
        hour_started, count = self._clients.get(client, (hour, 0))
        if hour_started != hour:
            count = 0
        if count >= self.per_client_per_hour:
            return int(3_600 - now % 3_600) + 1
        self._clients[client] = (hour, count + 1)
        self._day = (day, day_count + 1)
        return None


@dataclass(slots=True)
class Service:
    provider: Provider
    evidence: Evidence
    corpus: Corpus
    limits: Limits = field(default_factory=Limits)

    @classmethod
    def from_environment(
        cls, *, data_dir: Path, corpus_dir: Path, environ: Mapping[str, str] | None = None
    ) -> Service:
        env = os.environ if environ is None else environ
        limits = Limits(
            per_client_per_hour=int(env.get("DISCLOSED_ASK_PER_CLIENT_PER_HOUR", "20")),
            per_day=int(env.get("DISCLOSED_ASK_PER_DAY", "400")),
        )
        return cls(
            provider=from_environment(env),
            evidence=build_evidence(data_dir),
            corpus=load_corpus(corpus_dir),
            limits=limits,
        )

    def ask(
        self, question: str, *, institution_hint: str | None = None, client: str = "anonymous"
    ) -> dict[str, Any]:
        """Answer one question. Always returns a body; ``status`` says what kind."""
        text = question.strip()
        if not text:
            return _error(400, "a question is required")
        if len(text) > _MAX_QUESTION_CHARS:
            return _error(400, f"questions are limited to {_MAX_QUESTION_CHARS} characters")
        wait = self.limits.take(client)
        if wait is not None:
            body = _error(
                429, "this service has reached its limit; the page still works without it"
            )
            body["retry_after"] = wait
            return body
        try:
            return self._answer(text, institution_hint)
        except ProviderError as exc:
            return _error(503, str(exc))

    def _answer(self, text: str, institution_hint: str | None) -> dict[str, Any]:
        question = structure.structure(text, self.provider, institution_hint=institution_hint)
        pack = lookup.assemble(question, self.evidence, self.corpus)
        usage = {"structure": question.usage}
        if pack.refusal is not None:
            return _render(
                pack, verified=None, model=self.provider.model, usage=usage, corpus=self.corpus
            )
        narration = narrate.narrate(pack, self.provider)
        usage["narrate"] = narration.usage
        verified = verify.verify(narration, pack, self.corpus)
        return _render(
            pack, verified=verified, model=narration.model, usage=usage, corpus=self.corpus
        )


def _error(status: int, message: str) -> dict[str, Any]:
    return {"status": status, "label": LABEL, "error": message}


def _render(
    pack: lookup.Pack,
    *,
    verified: verify.Verified | None,
    model: str,
    usage: dict[str, dict[str, int]],
    corpus: Corpus,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "status": 200,
        "label": LABEL,
        "question": structure.as_dict(pack.question),
        "institution": (
            {
                "unit_id": pack.institution.unit_id,
                "name": pack.institution.name,
                "state": pack.institution.state,
            }
            if pack.institution
            else None
        ),
        "refusal": None,
        "claims": [],
        "quotes": [],
        "withheld": {"claims": 0, "quotes": 0, "note": 0, "reasons": {}},
        "could_not_answer": "",
        "evidence": pack.for_prompt(),
        "model": {"model": model, "prompt_version": narrate.PROMPT_VERSION, "usage": usage},
    }
    if pack.refusal is not None:
        body["refusal"] = {
            "code": pack.refusal.code,
            "message": pack.refusal.message,
            "known": list(pack.refusal.known),
        }
        return body
    assert verified is not None  # noqa: S101 -- the two branches above are exhaustive
    provenance = {q.passage.id: q for q in pack.quotables}
    body["claims"] = [{"text": c.text, "cites": list(c.cites)} for c in verified.claims]
    body["quotes"] = [
        {
            "quote": q.quote,
            "passage_id": q.passage_id,
            "field_label": provenance[q.passage_id].field_label,
            "role": provenance[q.passage_id].definition.role,
            "note": provenance[q.passage_id].definition.note,
            "source": corpus.provenance(q.passage_id),
        }
        for q in verified.quotes
    ]
    body["withheld"] = {
        "claims": len(verified.withheld_claims),
        "quotes": len(verified.withheld_quotes),
        # The note is a third channel to the reader and is counted like the other two. LABEL
        # promises everything shown was checked and everything withheld is counted below; before
        # this, a note that failed its screen was neither.
        "note": verified.withheld_note,
        "reasons": dict(verified.reasons),
    }
    # Already screened by ``verify``: either the model's own note, which passed, or this
    # project's fixed replacement text. Never unscreened model prose.
    body["could_not_answer"] = verified.could_not_answer
    if verified.malformed:
        body["could_not_answer"] = (
            "The model did not produce a narration that could be checked, so nothing is shown."
        )
    return body


# -- HTTP adapters -------------------------------------------------------------------------------


def _cors(origin: str | None, allowed: str) -> dict[str, str]:
    """CORS headers for a request from ``origin``, only when it is the allowed one.

    A page on any other origin gets no ``Access-Control-Allow-Origin`` and the browser drops the
    response. The service is still reachable with a plain HTTP client, which is why the limits
    above and the deployment's own bound exist; CORS is a courtesy to the page, not a lock.
    """
    headers = {"Content-Type": "application/json; charset=utf-8", "Vary": "Origin"}
    if origin is not None and origin.rstrip("/") == allowed.rstrip("/"):
        headers["Access-Control-Allow-Origin"] = allowed.rstrip("/")
        headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        headers["Access-Control-Allow-Headers"] = "Content-Type"
        headers["Access-Control-Max-Age"] = "600"
    return headers


def _parse_request(raw_body: bytes) -> tuple[str, str | None] | None:
    if len(raw_body) > _MAX_BODY_BYTES:
        return None
    try:
        body = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(body, dict) or not isinstance(body.get("question"), str):
        return None
    hint = body.get("institution")
    if hint is not None and not isinstance(hint, str):
        return None
    return body["question"], hint


def handle(
    service: Service,
    *,
    method: str,
    path: str,
    origin: str | None,
    client: str,
    raw_body: bytes,
    allowed_origin: str,
) -> tuple[int, dict[str, str], bytes]:
    """The one request handler both adapters call. Returns status, headers, body."""
    headers = _cors(origin, allowed_origin)
    if method == "OPTIONS":
        return 204, headers, b""
    if path.rstrip("/") == "/health" and method == "GET":
        return 200, headers, json.dumps({"ok": True, "label": LABEL}).encode("utf-8")
    if method != "POST" or path.rstrip("/") not in ("", "/ask"):
        return 404, headers, json.dumps(_error(404, "not found")).encode("utf-8")
    parsed = _parse_request(raw_body)
    if parsed is None:
        return 400, headers, json.dumps(_error(400, "send JSON: {question, institution?}")).encode()
    question, hint = parsed
    answer = service.ask(question, institution_hint=hint, client=client)
    status = int(answer.get("status", 200))
    if status == 429:
        headers["Retry-After"] = str(answer.get("retry_after", 60))
    return status, headers, json.dumps(answer, ensure_ascii=False).encode("utf-8")


_SERVICE: Service | None = None


def _service() -> Service:
    """The process-wide service, built on first use so a cold Lambda pays it once."""
    global _SERVICE
    if _SERVICE is None:
        root = Path(os.environ.get("DISCLOSED_ROOT", "."))
        _SERVICE = Service.from_environment(data_dir=root / "data", corpus_dir=root / "corpus")
    return _SERVICE


def lambda_handler(event: Mapping[str, Any], context: object = None) -> dict[str, Any]:
    """AWS Lambda Function URL (payload format 2.0) adapter."""
    import base64

    http = event.get("requestContext", {}).get("http", {})
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    body = event.get("body") or ""
    raw = base64.b64decode(body) if event.get("isBase64Encoded") else body.encode("utf-8")
    status, out_headers, out_body = handle(
        _service(),
        method=str(http.get("method", "GET")).upper(),
        path=str(event.get("rawPath", "/")),
        origin=headers.get("origin"),
        client=str(http.get("sourceIp", "unknown")),
        raw_body=raw,
        allowed_origin=os.environ.get("DISCLOSED_ASK_ORIGIN", DEFAULT_ORIGIN),
    )
    return {
        "statusCode": status,
        "headers": out_headers,
        "body": out_body.decode("utf-8"),
        "isBase64Encoded": False,
    }


def serve(service: Service, *, host: str, port: int, allowed_origin: str) -> ThreadingHTTPServer:
    """A development server on the standard library. Not a deployment."""

    class _Handler(BaseHTTPRequestHandler):
        def _respond(self, method: str) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            status, headers, body = handle(
                service,
                method=method,
                path=self.path,
                origin=self.headers.get("Origin"),
                client=self.client_address[0],
                raw_body=raw,
                allowed_origin=allowed_origin,
            )
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            self._respond("GET")

        def do_POST(self) -> None:
            self._respond("POST")

        def do_OPTIONS(self) -> None:
            self._respond("OPTIONS")

        def log_message(self, format: str, *args: object) -> None:
            """Silent: the default logger prints the request line, and the request line is
            the one thing this service promises not to keep."""

    return ThreadingHTTPServer((host, port), _Handler)
