"""Metering decorator: budget and audit accounting without touching agents.

Agents call ``llm.call(request)`` and never see accounting. Wrapping the
client once, at pipeline start, records every response's usage into the run's
BudgetTracker (which mutates ``state.budget``, making the budget gate live)
and writes one ``llm_call`` audit event per call, including the codex
backend's response provenance when the response carries it.
"""

from __future__ import annotations

from typing import Any

from sar.contracts.llm import LLMRequest, LLMResponse
from sar.llm.budget import BudgetTracker
from sar.llm.client import LLMClient


class MeteredClient:
    """LLMClient decorator that records usage and audit events per call.

    Attributes:
        inner: The wrapped client (OpenAIClient, CodexClient, ...).
        tracker: The BudgetTracker receiving each call's usage.
        audit_log: Optional AuditLog; one llm_call event per call when set.
    """

    def __init__(
        self,
        inner: LLMClient,
        tracker: BudgetTracker,
        audit_log: Any | None = None,
    ) -> None:
        self.inner = inner
        self.tracker = tracker
        self.audit_log = audit_log

    async def call(self, request: LLMRequest) -> LLMResponse:
        """Delegate to the wrapped client, then record usage and audit."""
        response = await self.inner.call(request)
        model = response.model or (request.model or "")
        self.tracker.record(model, response.usage)
        if self.audit_log is not None:
            extra: dict[str, object] = {}
            raw = response.raw or {}
            if isinstance(raw, dict) and "source" in raw:
                # Codex backend: which recovery path produced the JSON
                # (handoff-file, last-message, or stdout scrape).
                extra["response_source"] = raw["source"]
            self.audit_log.record_llm_call(
                agent_name=type(self.inner).__name__,
                model=model,
                input_tokens=response.usage.get("prompt_tokens", 0),
                output_tokens=response.usage.get("completion_tokens", 0),
                tool_calls=[c.name for c in response.tool_calls],
                extra=extra or None,
            )
        return response
