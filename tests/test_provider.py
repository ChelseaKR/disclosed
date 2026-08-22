"""The provider seam: the SDK behind it, the fake beside it, the environment in front of it."""

from __future__ import annotations

import json
from typing import Any

import anthropic
import httpx2
import pytest

from disclosed.ask import provider as p


class TestFakeProvider:
    def test_replies_in_order_and_records_every_call(self) -> None:
        fake = p.FakeProvider(['{"a": 1}', lambda user: json.dumps({"echo": user})])
        first = fake.complete(system="s", user="u1", schema={"type": "object"}, max_tokens=10)
        second = fake.complete(system="s", user="u2", schema={"type": "object"}, max_tokens=10)
        assert first.parsed() == {"a": 1}
        assert second.parsed() == {"echo": "u2"}
        assert [c["user"] for c in fake.calls] == ["u1", "u2"]
        assert fake.model == "fake"

    def test_running_past_the_script_is_an_error(self) -> None:
        fake = p.FakeProvider([])
        with pytest.raises(p.ProviderError, match="scripted 0 replies"):
            fake.complete(system="s", user="u", schema={}, max_tokens=1)

    def test_a_non_json_reply_is_a_value_error_not_a_repair(self) -> None:
        with pytest.raises(ValueError, match="not JSON"):
            p.Completion(text="not json", model="x").parsed()


class _Block:
    def __init__(self, type_: str, text: str = "") -> None:
        self.type = type_
        self.text = text


class _Usage:
    input_tokens = 100
    output_tokens = 20
    cache_read_input_tokens: int | None = 90
    cache_creation_input_tokens: int | None = None


class _Response:
    def __init__(self, stop_reason: str = "end_turn") -> None:
        self.stop_reason = stop_reason
        self.content = [_Block("thinking"), _Block("text", '{"ok": true}')]
        self.usage = _Usage()
        self.model = "model-as-served"


class _Client:
    def __init__(self, outcome: Any) -> None:
        self.outcome = outcome
        self.kwargs: dict[str, Any] = {}
        self.messages = self

    def create(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def _status_error(cls: type[anthropic.APIStatusError], status: int) -> anthropic.APIStatusError:
    response = httpx2.Response(status, request=httpx2.Request("POST", "https://example.invalid"))
    return cls("boom", response=response, body=None)


class TestAnthropicProvider:
    def test_sends_a_cached_system_block_and_a_json_schema(self) -> None:
        client = _Client(_Response())
        provider = p.AnthropicProvider(model="claude-sonnet-5", client=client)
        completion = provider.complete(
            system="SYSTEM", user="USER", schema={"type": "object"}, max_tokens=50
        )
        assert provider.model == "claude-sonnet-5"
        sent = client.kwargs
        assert sent["model"] == "claude-sonnet-5"
        assert sent["system"] == [
            {"type": "text", "text": "SYSTEM", "cache_control": {"type": "ephemeral"}}
        ]
        assert sent["messages"] == [{"role": "user", "content": "USER"}]
        assert sent["output_config"] == {
            "format": {"type": "json_schema", "schema": {"type": "object"}}
        }
        assert completion.text == '{"ok": true}'
        assert completion.model == "model-as-served"
        assert (completion.input_tokens, completion.output_tokens) == (100, 20)
        assert (completion.cache_read_tokens, completion.cache_creation_tokens) == (90, 0)

    def test_a_refusal_stop_reason_is_a_provider_error(self) -> None:
        provider = p.AnthropicProvider(model="m", client=_Client(_Response("refusal")))
        with pytest.raises(p.ProviderError, match="declined"):
            provider.complete(system="s", user="u", schema={}, max_tokens=1)

    @pytest.mark.parametrize(
        ("error", "message"),
        [
            (_status_error(anthropic.RateLimitError, 429), "rate limiting"),
            (_status_error(anthropic.InternalServerError, 500), "answered 500"),
            (anthropic.APIConnectionError(request=httpx2.Request("POST", "https://x")), "reached"),
        ],
    )
    def test_sdk_errors_map_to_provider_errors_without_the_request_body(
        self, error: Exception, message: str
    ) -> None:
        provider = p.AnthropicProvider(model="m", client=_Client(error))
        with pytest.raises(p.ProviderError, match=message) as caught:
            provider.complete(system="s", user="SECRET QUESTION", schema={}, max_tokens=1)
        assert "SECRET QUESTION" not in str(caught.value)


class TestFromEnvironment:
    def test_default_route_needs_a_key_and_never_reads_it_here(self) -> None:
        with pytest.raises(p.ProviderError, match="ANTHROPIC_API_KEY is not set"):
            p.from_environment({})

    def test_default_route_and_default_model(self) -> None:
        provider = p.from_environment({"ANTHROPIC_API_KEY": "sk-test-not-real"})
        assert isinstance(provider, p.AnthropicProvider)
        assert provider.model == p.DEFAULT_MODEL == "claude-sonnet-5"

    def test_model_is_configurable(self) -> None:
        provider = p.from_environment(
            {"ANTHROPIC_API_KEY": "sk-test-not-real", "DISCLOSED_ASK_MODEL": " claude-opus-5 "}
        )
        assert provider.model == "claude-opus-5"

    def test_bedrock_route_needs_a_region(self) -> None:
        with pytest.raises(p.ProviderError, match="AWS_REGION"):
            p.from_environment({"DISCLOSED_ASK_PROVIDER": "bedrock"})

    def test_bedrock_route_builds_the_bedrock_client(self) -> None:
        provider = p.from_environment(
            {
                "DISCLOSED_ASK_PROVIDER": "Bedrock",
                "AWS_DEFAULT_REGION": "us-west-2",
                "DISCLOSED_ASK_MODEL": "global.anthropic.claude-sonnet-4-6",
            }
        )
        assert isinstance(provider, p.AnthropicProvider)
        assert provider.model == "global.anthropic.claude-sonnet-4-6"
        assert isinstance(provider._client, anthropic.AnthropicBedrock)

    def test_an_unknown_route_is_refused(self) -> None:
        with pytest.raises(p.ProviderError, match="unknown DISCLOSED_ASK_PROVIDER"):
            p.from_environment({"DISCLOSED_ASK_PROVIDER": "vertex"})

    def test_reads_the_real_environment_when_not_given_one(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("DISCLOSED_ASK_PROVIDER", "anthropic")
        with pytest.raises(p.ProviderError):
            p.from_environment()
