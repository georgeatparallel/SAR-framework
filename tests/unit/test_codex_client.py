"""CodexClient tests against a scriptable fake codex binary.

The fake (tests/fixtures/fake_codex.py) honours the client's real contract:
prompt on stdin, handoff path named in the prompt, --output-last-message
flag. Every recovery path, the retry, the timeout kill, and the prompt-size
guard run offline with no real codex and no network.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

from sar.contracts.llm import LLMRequest, ToolDefinition
from sar.llm.client import LLMClientError
from sar.llm.codex_client import (
    MAX_PROMPT_BYTES,
    CodexClient,
    CodexProcessError,
    _extract_json,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.fixture
def fake_codex(tmp_path, monkeypatch):
    """A codex shim on PATH, scripted via FAKE_CODEX_* env vars."""
    shim = tmp_path / "codex"
    shim.write_text(
        f"#!/bin/sh\nexec {sys.executable} {FIXTURES / 'fake_codex.py'} \"$@\"\n"
    )
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.delenv("FAKE_CODEX_MODE", raising=False)
    monkeypatch.delenv("FAKE_CODEX_PAYLOAD", raising=False)
    return shim


def make_client(tmp_path, **kwargs) -> CodexClient:
    kwargs.setdefault("workdir", tmp_path)
    kwargs.setdefault("timeout", 30.0)
    kwargs.setdefault("retry_delay", 0.05)
    return CodexClient(**kwargs)


def simple_request(text: str = "hello") -> LLMRequest:
    return LLMRequest(messages=[{"role": "user", "content": text}])


class TestRecoveryPaths:
    async def test_handoff_file_is_the_primary_path(self, fake_codex, tmp_path):
        client = make_client(tmp_path)
        resp = await client.call(simple_request())
        assert resp.content == "ok"
        assert resp.tool_calls == []
        assert resp.raw["source"] == "handoff-file"

    async def test_fenced_handoff_is_repaired(self, fake_codex, tmp_path, monkeypatch):
        monkeypatch.setenv("FAKE_CODEX_MODE", "handoff-fenced")
        client = make_client(tmp_path)
        resp = await client.call(simple_request())
        assert resp.content == "ok"
        assert resp.raw["source"] == "handoff-file(repaired)"

    async def test_double_encoded_handoff_unwraps(
        self, fake_codex, tmp_path, monkeypatch
    ):
        # A model that writes the object as a JSON string (double-encoded)
        # killed a live run; the client now unwraps one level.
        monkeypatch.setenv("FAKE_CODEX_MODE", "handoff-double")
        client = make_client(tmp_path)
        resp = await client.call(simple_request())
        assert resp.content == "ok"
        assert resp.raw["source"] == "handoff-file(unwrapped)"

    async def test_last_message_fallback(self, fake_codex, tmp_path, monkeypatch):
        monkeypatch.setenv("FAKE_CODEX_MODE", "last-message")
        client = make_client(tmp_path)
        resp = await client.call(simple_request())
        assert resp.content == "ok"
        assert resp.raw["source"] == "last-message"

    async def test_stdout_scrape_is_the_last_resort(
        self, fake_codex, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("FAKE_CODEX_MODE", "stdout")
        client = make_client(tmp_path)
        resp = await client.call(simple_request())
        assert resp.content == "ok"
        assert resp.raw["source"] == "stdout"

    async def test_garbage_everywhere_raises_with_context(
        self, fake_codex, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("FAKE_CODEX_MODE", "garbage")
        client = make_client(tmp_path)
        with pytest.raises(LLMClientError, match="no parseable JSON"):
            await client.call(simple_request())


class TestToolCalls:
    async def test_tool_call_payload_parses(self, fake_codex, tmp_path, monkeypatch):
        monkeypatch.setenv("FAKE_CODEX_PAYLOAD", json.dumps({
            "content": None,
            "tool_calls": [
                {"name": "submit_queries", "arguments": {"queries": ["a", "b"]}}
            ],
        }))
        client = make_client(tmp_path)
        resp = await client.call(LLMRequest(
            messages=[{"role": "user", "content": "expand"}],
            tools=[ToolDefinition(
                name="submit_queries",
                description="Return queries",
                parameters={"type": "object",
                            "properties": {"queries": {"type": "array"}}},
            )],
        ))
        assert resp.content is None
        assert len(resp.tool_calls) == 1
        assert resp.tool_calls[0].name == "submit_queries"
        assert resp.tool_calls[0].arguments == {"queries": ["a", "b"]}

    async def test_string_arguments_are_parsed_or_wrapped(
        self, fake_codex, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("FAKE_CODEX_PAYLOAD", json.dumps({
            "content": None,
            "tool_calls": [
                {"name": "a", "arguments": '{"x": 1}'},
                {"name": "b", "arguments": "not json"},
                {"name": "", "arguments": {}},          # dropped: no name
                "not even a dict",                       # dropped
            ],
        }))
        client = make_client(tmp_path)
        resp = await client.call(simple_request())
        assert [c.name for c in resp.tool_calls] == ["a", "b"]
        assert resp.tool_calls[0].arguments == {"x": 1}
        assert resp.tool_calls[1].arguments == {"_raw": "not json"}

    async def test_raw_dict_tool_schema_renders_into_prompt(
        self, fake_codex, tmp_path, monkeypatch
    ):
        # OpenAI-format dict instead of ToolDefinition: both must normalise.
        client = make_client(tmp_path)
        req = LLMRequest(
            messages=[{"role": "user", "content": "x"}],
            tools=[{"type": "function", "function": {
                "name": "emit", "description": "d",
                "parameters": {"type": "object"}}}],
        )
        out_probe = tmp_path / "probe.json"
        prompt = client._compose(req, out_probe)
        assert '"name": "emit"' in prompt
        assert str(out_probe) in prompt


class TestProcessFailures:
    async def test_nonzero_exit_raises_process_error(
        self, fake_codex, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("FAKE_CODEX_MODE", "exit-2")
        client = make_client(tmp_path, max_attempts=1)
        with pytest.raises(CodexProcessError, match="exited 2"):
            await client.call(simple_request())

    async def test_timeout_kills_and_raises(self, fake_codex, tmp_path, monkeypatch):
        monkeypatch.setenv("FAKE_CODEX_MODE", "sleep")
        monkeypatch.setenv("FAKE_CODEX_SLEEP", "5")
        client = make_client(tmp_path, timeout=0.5, max_attempts=1)
        with pytest.raises(CodexProcessError, match="timed out"):
            await client.call(simple_request())

    async def test_flaky_process_is_retried_once(
        self, fake_codex, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("FAKE_CODEX_MODE", "flaky")
        monkeypatch.setenv("FAKE_CODEX_STATE", str(tmp_path / "attempts"))
        client = make_client(tmp_path, max_attempts=2)
        resp = await client.call(simple_request())
        assert resp.content == "ok"
        assert (tmp_path / "attempts").read_text() == "2"

    async def test_parse_failure_is_not_retried(
        self, fake_codex, tmp_path, monkeypatch
    ):
        # garbage parses nowhere; with retries available the client must NOT
        # spend another call on it (a model that answered badly will answer
        # badly again).
        monkeypatch.setenv("FAKE_CODEX_MODE", "garbage")
        monkeypatch.setenv("FAKE_CODEX_ARGS_LOG", str(tmp_path / "args.log"))
        client = make_client(tmp_path, max_attempts=3)
        with pytest.raises(LLMClientError):
            await client.call(simple_request())
        invocations = (tmp_path / "args.log").read_text().strip().split("\n")
        assert len(invocations) == 1

    async def test_missing_binary_fails_at_construction(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PATH", str(tmp_path))  # nothing on PATH
        with pytest.raises(LLMClientError, match="not found"):
            CodexClient(workdir=tmp_path)


class TestGuardsAndComposition:
    async def test_prompt_size_guard(self, fake_codex, tmp_path):
        client = make_client(tmp_path)
        big = "x" * (MAX_PROMPT_BYTES + 1)
        with pytest.raises(LLMClientError, match="over the"):
            await client.call(simple_request(big))

    async def test_add_dir_passed_when_out_path_outside_workdir(
        self, fake_codex, tmp_path, monkeypatch
    ):
        # The handoff dir is a mkdtemp outside workdir, so --add-dir must be
        # present in the argv the binary receives.
        monkeypatch.setenv("FAKE_CODEX_ARGS_LOG", str(tmp_path / "args.log"))
        client = make_client(tmp_path)
        await client.call(simple_request())
        argv = json.loads((tmp_path / "args.log").read_text().strip())
        assert "--add-dir" in argv
        assert "--output-last-message" in argv
        assert "--model" in argv

    def test_multimodal_content_flattens(self, fake_codex, tmp_path):
        client = make_client(tmp_path)
        req = LLMRequest(messages=[{"role": "user", "content": [
            {"type": "text", "text": "part one"},
            {"type": "text", "text": "part two"},
        ]}])
        prompt = client._compose(req, tmp_path / "o.json")
        assert "part one part two" in prompt

    def test_model_override_per_request(self, fake_codex, tmp_path):
        client = make_client(tmp_path, default_model="model-a")
        assert client._model == "model-a"


class TestExtractJson:
    @pytest.mark.parametrize("text,expected", [
        ('{"a": 1}', {"a": 1}),
        ('prose then {"a": {"b": 2}} trailing', {"a": {"b": 2}}),
        ('```json\n{"a": 1}\n```', {"a": 1}),
        ("no json here", None),
        ("", None),
        ("{broken", None),
        ('[1, 2, 3]', None),          # a list is not the contract
    ])
    def test_extraction_matrix(self, text, expected):
        assert _extract_json(text) == expected
