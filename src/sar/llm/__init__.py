"""LLM gateway service — provider-agnostic LLM access.

No search dependency. This service owns:
- LLMClient protocol and OpenAI implementation (client.py)
- Codex CLI implementation for local agent execution (codex_client.py)
- build_client() selects the backend from config or SAR_LLM_BACKEND
- Tool schema definitions for function calling (tools.py)
- Retry logic with exponential backoff and circuit breaker (retry.py)
- Token/cost budget tracking per pipeline run (budget.py)
"""

from __future__ import annotations

from sar.llm.budget import BudgetTracker
from sar.llm.client import LLMClient, LLMClientError, OpenAIClient
from sar.llm.codex_client import CodexClient
from sar.llm.retry import CircuitBreaker, CircuitOpenError, call_with_backoff

def build_client(
    backend: str | None = None,
    *,
    api_key: str | None = None,
    model: str | None = None,
    **kwargs: object,
) -> LLMClient:
    """Construct the configured LLM backend.

    Two execution modes share one protocol. ``openai`` calls the hosted API
    directly. ``codex`` drives the local ``codex exec`` binary, which runs as
    an agent process on this machine. Agent and orchestrator code is identical
    either way.

    Args:
        backend: "openai" or "codex". Defaults to $SAR_LLM_BACKEND, else openai.
        api_key: OpenAI key. Required for the openai backend only.
        model: Model identifier. Each backend supplies its own default.
        **kwargs: Passed through to the backend constructor.

    Raises:
        LLMClientError: On an unknown backend or a missing OpenAI key.
    """
    import os

    choice = (backend or os.environ.get("SAR_LLM_BACKEND") or "openai").lower()
    if choice == "codex":
        return CodexClient(
            default_model=model or os.environ.get("SAR_CODEX_MODEL")
            or "gpt-5.6-terra",
            **kwargs,  # type: ignore[arg-type]
        )
    if choice == "openai":
        key = api_key or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise LLMClientError("OPENAI_API_KEY is not set")
        return OpenAIClient(api_key=key, default_model=model or "gpt-4o-mini")
    raise LLMClientError(f"unknown LLM backend: {choice!r}")


__all__ = [
    "BudgetTracker",
    "CodexClient",
    "CircuitBreaker",
    "CircuitOpenError",
    "LLMClient",
    "LLMClientError",
    "OpenAIClient",
    "build_client",
    "call_with_backoff",
]
