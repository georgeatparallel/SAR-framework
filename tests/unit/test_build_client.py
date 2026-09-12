"""Backend selection: build_client() is the single switch between run modes."""

from __future__ import annotations

import pytest

from sar.llm import CodexClient, LLMClientError, OpenAIClient, build_client


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("SAR_LLM_BACKEND", raising=False)
    monkeypatch.delenv("SAR_CODEX_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


class TestSelection:
    def test_default_is_openai(self):
        with pytest.raises(LLMClientError, match="OPENAI_API_KEY"):
            build_client()

    def test_env_selects_codex(self, monkeypatch):
        monkeypatch.setenv("SAR_LLM_BACKEND", "codex")
        assert isinstance(build_client(), CodexClient)

    def test_explicit_arg_beats_env(self, monkeypatch):
        monkeypatch.setenv("SAR_LLM_BACKEND", "openai")
        assert isinstance(build_client("codex"), CodexClient)

    def test_unknown_backend_raises(self):
        with pytest.raises(LLMClientError, match="unknown LLM backend"):
            build_client("gemini")

    def test_case_insensitive(self):
        assert isinstance(build_client("CODEX"), CodexClient)


def _openai_installed() -> bool:
    import importlib.util

    return importlib.util.find_spec("openai") is not None


class TestOpenAI:
    def test_key_from_arg(self):
        # Without the [openai] extra the constructor must fail with an
        # actionable message, not a bare ModuleNotFoundError.
        if _openai_installed():
            assert isinstance(build_client("openai", api_key="sk-test"),
                              OpenAIClient)
        else:
            with pytest.raises(LLMClientError, match=r"agentic-sar\[openai\]"):
                build_client("openai", api_key="sk-test")

    def test_key_from_env(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        if _openai_installed():
            assert isinstance(build_client("openai"), OpenAIClient)
        else:
            with pytest.raises(LLMClientError, match=r"agentic-sar\[openai\]"):
                build_client("openai")

    def test_missing_key_raises(self):
        with pytest.raises(LLMClientError, match="OPENAI_API_KEY"):
            build_client("openai")


class TestCodexModel:
    def test_model_arg(self):
        c = build_client("codex", model="my-model")
        assert c._model == "my-model"

    def test_model_env(self, monkeypatch):
        monkeypatch.setenv("SAR_CODEX_MODEL", "env-model")
        assert build_client("codex")._model == "env-model"

    def test_model_default(self):
        assert build_client("codex")._model == "gpt-5.6-terra"
