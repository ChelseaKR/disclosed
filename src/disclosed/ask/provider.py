"""The model behind a thin, replaceable seam.

Two calls is all the question-answering layer makes, and both have the same shape: a frozen
system prompt, one user message, a JSON schema the reply must satisfy, and a token ceiling. So
that is the whole interface. The Anthropic SDK is behind it; so is a scripted fake the test suite
and the offline evaluation runs use, because the verifier, the refusal policy and the five-state
fidelity checks have to be gated on every push without a key in the environment.

Credentials come from the environment only. ``ANTHROPIC_API_KEY`` reaches the first-party API;
the Amazon Bedrock route signs with whatever AWS credentials the environment carries. Nothing
here reads a key from a file, and nothing here writes one.

Model and route are configuration, not code: ``DISCLOSED_ASK_MODEL`` (default
``claude-sonnet-5``) and ``DISCLOSED_ASK_PROVIDER`` (``anthropic`` or ``bedrock``). The
default is the model the owner chose; on the day this was written the only model this
repository's credentials could reach was ``global.anthropic.claude-sonnet-4-6`` on Bedrock, and
the committed evaluation results say which one they were measured on.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Protocol

__all__ = [
    "DEFAULT_MODEL",
    "AnthropicProvider",
    "Completion",
    "FakeProvider",
    "Provider",
    "ProviderError",
    "from_environment",
]

DEFAULT_MODEL: Final[str] = "claude-sonnet-5"
_MODEL_ENV: Final[str] = "DISCLOSED_ASK_MODEL"
_PROVIDER_ENV: Final[str] = "DISCLOSED_ASK_PROVIDER"
_REGION_ENVS: Final[tuple[str, ...]] = ("AWS_REGION", "AWS_DEFAULT_REGION")


class ProviderError(RuntimeError):
    """The model could not be reached or did not answer. Never carries the request body."""


@dataclass(frozen=True, slots=True)
class Completion:
    """What came back, plus enough of the usage to account for the cost of getting it."""

    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0

    def parsed(self) -> Any:
        """The reply as JSON. Raises ``ValueError`` if it is not, which the caller treats as the
        model having produced nothing verifiable rather than as something to repair."""
        try:
            return json.loads(self.text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"model reply was not JSON: {exc.msg}") from exc


class Provider(Protocol):
    """One structured completion. Implementations must not log or persist ``user``."""

    @property
    def model(self) -> str: ...

    def complete(
        self, *, system: str, user: str, schema: Mapping[str, Any], max_tokens: int
    ) -> Completion: ...


@dataclass(slots=True)
class FakeProvider:
    """Scripted replies, in order, for tests and offline evaluation runs.

    Each entry is either the JSON text to return or a callable given the user message that
    returns it, so a test can script "say X" and an evaluation can script "cite exactly what you
    were given". Running past the script is an error: a test that makes more calls than it
    planned for is a test that is not measuring what it thinks it is.
    """

    replies: Sequence[str | Callable[[str], str]]
    model_name: str = "fake"
    calls: list[dict[str, Any]] = field(default_factory=list)

    @property
    def model(self) -> str:
        return self.model_name

    def complete(
        self, *, system: str, user: str, schema: Mapping[str, Any], max_tokens: int
    ) -> Completion:
        index = len(self.calls)
        self.calls.append({"system": system, "user": user, "schema": dict(schema)})
        if index >= len(self.replies):
            raise ProviderError(
                f"fake provider scripted {len(self.replies)} replies, asked for more"
            )
        reply = self.replies[index]
        text = reply(user) if callable(reply) else reply
        return Completion(text=text, model=self.model_name)


class AnthropicProvider:
    """The public SDK, first-party or through Amazon Bedrock, behind the same call.

    The system prompt is sent as a cached block. It is the one thing that is identical across
    every request, it is long, and it is rendered before the user message, which is exactly the
    prefix prompt caching wants; the usage counters on :class:`Completion` say whether the cache
    is being hit, and the service surfaces them rather than assuming.
    """

    def __init__(self, *, model: str, client: Any) -> None:
        self._model = model
        self._client = client

    @property
    def model(self) -> str:
        return self._model

    def complete(
        self, *, system: str, user: str, schema: Mapping[str, Any], max_tokens: int
    ) -> Completion:
        import anthropic

        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user}],
                output_config={"format": {"type": "json_schema", "schema": dict(schema)}},
            )
        except anthropic.RateLimitError as exc:
            raise ProviderError("the model provider is rate limiting requests") from exc
        except anthropic.APIStatusError as exc:
            raise ProviderError(f"the model provider answered {exc.status_code}") from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderError("the model provider could not be reached") from exc
        if response.stop_reason == "refusal":
            raise ProviderError("the model declined to answer")
        text = "".join(block.text for block in response.content if block.type == "text")
        usage = response.usage
        return Completion(
            text=text,
            model=response.model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_input_tokens or 0,
            cache_creation_tokens=usage.cache_creation_input_tokens or 0,
        )


def from_environment(environ: Mapping[str, str] | None = None) -> Provider:
    """Build the configured provider. Raises :class:`ProviderError` when nothing is configured.

    ``anthropic`` (the default) needs ``ANTHROPIC_API_KEY``; the SDK reads it itself, so the key
    is never touched by this code. ``bedrock`` needs AWS credentials and a region. The model id is
    passed through as given: first-party ids are bare (``claude-sonnet-5``), Bedrock ids carry the
    provider's prefix (``global.anthropic.claude-sonnet-4-6``).
    """
    env = os.environ if environ is None else environ
    route = env.get(_PROVIDER_ENV, "anthropic").strip().lower()
    model = env.get(_MODEL_ENV, "").strip() or DEFAULT_MODEL
    import anthropic

    if route == "anthropic":
        if not env.get("ANTHROPIC_API_KEY"):
            raise ProviderError("ANTHROPIC_API_KEY is not set; no model is configured")
        return AnthropicProvider(model=model, client=anthropic.Anthropic())
    if route == "bedrock":
        region = next((env[k] for k in _REGION_ENVS if env.get(k)), None)
        if region is None:
            raise ProviderError("AWS_REGION is not set; the Bedrock route needs a region")
        return AnthropicProvider(model=model, client=anthropic.AnthropicBedrock(aws_region=region))
    raise ProviderError(f"unknown {_PROVIDER_ENV} {route!r}; use 'anthropic' or 'bedrock'")
