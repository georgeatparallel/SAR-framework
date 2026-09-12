"""OpenAIClient tests with a faked SDK: no network, no openai package.

The client imports ``openai`` lazily inside ``__init__``, so injecting a
stub module into sys.modules exercises the real formatting, dispatch, and
parsing code paths regardless of whether the [openai] extra is installed.
"""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace

import pytest

from sar.contracts.llm import LLMRequest, ToolDefinition
from sar.llm.client import LLMClientError, OpenAIClient, _is_reasoning_model


class FakeCompletions:
    def __init__(self):
        self.kwargs = None
        self.response = None
        self.raise_exc: Exception | None = None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        if self.raise_exc:
            raise self.raise_exc
        return self.response


def _sdk_response(content="hello", tool_calls=None, usage=(10, 5, 15),
                  model="gpt-4o-mini"):
    return SimpleNamespace(
        model=model,
        usage=SimpleNamespace(
            prompt_tokens=usage[0], completion_tokens=usage[1],
            total_tokens=usage[2],
        ) if usage else None,
        choices=[SimpleNamespace(message=SimpleNamespace(
            content=content, tool_calls=tool_calls,
        ))],
    )


@pytest.fixture
def fake_openai(monkeypatch):
    """Install a stub openai module; return the FakeCompletions for asserts."""
    completions = FakeCompletions()

    class AsyncOpenAI:
        def __init__(self, api_key):
            self.api_key = api_key
            self.chat = SimpleNamespace(completions=completions)

    module = types.ModuleType("openai")
    module.AsyncOpenAI = AsyncOpenAI
    monkeypatch.setitem(sys.modules, "openai", module)
    return completions


class TestDispatch:
    async def test_plain_text_round_trip(self, fake_openai):
        fake_openai.response = _sdk_response(content="hi")
        client = OpenAIClient(api_key="k", default_model="gpt-4o-mini")
        resp = await client.call(LLMRequest(
            messages=[{"role": "user", "content": "q"}]))
        assert resp.content == "hi"
        assert resp.tool_calls == []
        assert resp.usage == {"prompt_tokens": 10, "completion_tokens": 5,
                              "total_tokens": 15}
        assert fake_openai.kwargs["model"] == "gpt-4o-mini"
        assert fake_openai.kwargs["temperature"] == 0.3
        assert fake_openai.kwargs["max_tokens"] == 4096

    async def test_reasoning_model_param_switch(self, fake_openai):
        fake_openai.response = _sdk_response(model="gpt-5.5")
        client = OpenAIClient(api_key="k", default_model="gpt-5.5")
        await client.call(LLMRequest(messages=[{"role": "user", "content": "q"}]))
        k = fake_openai.kwargs
        assert "temperature" not in k
        assert "max_tokens" not in k
        assert k["max_completion_tokens"] >= 8192

    async def test_request_model_overrides_default(self, fake_openai):
        fake_openai.response = _sdk_response()
        client = OpenAIClient(api_key="k", default_model="gpt-4o-mini")
        await client.call(LLMRequest(
            messages=[{"role": "user", "content": "q"}], model="gpt-4o"))
        assert fake_openai.kwargs["model"] == "gpt-4o"

    async def test_sdk_failure_wrapped(self, fake_openai):
        fake_openai.raise_exc = RuntimeError("boom")
        client = OpenAIClient(api_key="k")
        with pytest.raises(LLMClientError, match="OpenAI API call failed"):
            await client.call(LLMRequest(
                messages=[{"role": "user", "content": "q"}]))


class TestToolPayload:
    async def test_tooldefinition_converts_to_function_shape(self, fake_openai):
        fake_openai.response = _sdk_response()
        client = OpenAIClient(api_key="k")
        await client.call(LLMRequest(
            messages=[{"role": "user", "content": "q"}],
            tools=[ToolDefinition(name="t", description="d",
                                  parameters={"type": "object"})],
            tool_choice={"type": "function", "function": {"name": "t"}},
        ))
        assert fake_openai.kwargs["tools"] == [{
            "type": "function",
            "function": {"name": "t", "description": "d",
                         "parameters": {"type": "object"}},
        }]
        assert fake_openai.kwargs["tool_choice"]["function"]["name"] == "t"

    async def test_raw_openai_dict_passes_through(self, fake_openai):
        fake_openai.response = _sdk_response()
        client = OpenAIClient(api_key="k")
        raw = {"type": "function",
               "function": {"name": "r", "description": "", "parameters": {}}}
        await client.call(LLMRequest(
            messages=[{"role": "user", "content": "q"}], tools=[raw]))
        assert fake_openai.kwargs["tools"] == [raw]

    async def test_bare_contract_dict_is_wrapped(self, fake_openai):
        fake_openai.response = _sdk_response()
        client = OpenAIClient(api_key="k")
        await client.call(LLMRequest(
            messages=[{"role": "user", "content": "q"}],
            tools=[{"name": "b", "description": "d", "parameters": {}}]))
        assert fake_openai.kwargs["tools"][0]["function"]["name"] == "b"

    async def test_unrecognised_tool_dict_raises(self, fake_openai):
        fake_openai.response = _sdk_response()
        client = OpenAIClient(api_key="k")
        with pytest.raises(ValueError, match="Unrecognised tool dict"):
            await client.call(LLMRequest(
                messages=[{"role": "user", "content": "q"}],
                tools=[{"bogus": True}]))


class TestResponseParsing:
    async def test_tool_calls_parse(self, fake_openai):
        sdk_call = SimpleNamespace(
            id="call_1",
            function=SimpleNamespace(name="submit", arguments='{"a": 1}'),
        )
        fake_openai.response = _sdk_response(content=None, tool_calls=[sdk_call])
        client = OpenAIClient(api_key="k")
        resp = await client.call(LLMRequest(
            messages=[{"role": "user", "content": "q"}]))
        assert len(resp.tool_calls) == 1
        assert resp.tool_calls[0].name == "submit"
        assert resp.tool_calls[0].arguments == {"a": 1}

    async def test_missing_usage_is_zero_filled(self, fake_openai):
        fake_openai.response = _sdk_response(usage=None)
        client = OpenAIClient(api_key="k")
        resp = await client.call(LLMRequest(
            messages=[{"role": "user", "content": "q"}]))
        assert resp.usage == {"prompt_tokens": 0, "completion_tokens": 0,
                              "total_tokens": 0}


class TestReasoningModelDetection:
    @pytest.mark.parametrize("name,expected", [
        ("gpt-5.5", True), ("gpt-5.6-terra", True), ("o1-mini", True),
        ("o3", True), ("gpt-4o-mini", False), ("gpt-3.5-turbo", False),
    ])
    def test_matrix(self, name, expected):
        assert _is_reasoning_model(name) is expected
