"""LLM client: protocol definition and OpenAI implementation.

Decouples the orchestrator and agents from any specific LLM provider.
Swapping providers means implementing a new LLMClient — no changes
to agent code or orchestration logic.

Migrated from: inline openai.chat.completions.create() calls in agent_service.py
"""

from __future__ import annotations

import json
import logging
from typing import Any, Protocol, runtime_checkable

from sar.contracts.llm import LLMRequest, LLMResponse, ToolCall
from sar.llm.retry import call_with_backoff

logger = logging.getLogger(__name__)


def _is_reasoning_model(name: str) -> bool:
    """Return True for OpenAI models that reject `max_tokens`/`temperature`.

    The newer reasoning / GPT-5 family takes `max_completion_tokens` and
    only `temperature=1`. This list is conservative; expand as needed.
    """
    n = (name or "").lower()
    return n.startswith(("gpt-5", "o1", "o3", "o4"))


@runtime_checkable
class LLMClient(Protocol):
    """Protocol for LLM provider clients.

    All agent code and orchestration depends on this protocol,
    never on a concrete implementation.
    """

    async def call(self, request: LLMRequest) -> LLMResponse:
        """Send a request to the LLM and return the response.

        Args:
            request: Structured LLM request with messages, tools, and config.

        Returns:
            Structured response with content, tool calls, and token usage.

        Raises:
            LLMClientError: If the call fails after retries.
        """
        ...


class OpenAIClient:
    """OpenAI API implementation of LLMClient.

    Wraps the openai Python SDK (async client). Handles message formatting,
    tool schema conversion, and response parsing.

    Attributes:
        _client: The underlying AsyncOpenAI client instance.
        _default_model: Model to use when request.model is None.
    """

    def __init__(
        self,
        api_key: str,
        default_model: str = "gpt-4o-mini",
        retry_attempts: int = 4,
    ) -> None:
        """Initialize the OpenAI client.

        Args:
            api_key: OpenAI API key.
            default_model: Fallback model identifier.
            retry_attempts: Attempts for transient provider errors
                (429, 5xx, timeouts) before giving up.
        """
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise LLMClientError(
                "the openai backend needs the 'openai' package: "
                "pip install agentic-sar[openai]"
            ) from exc

        self._client = AsyncOpenAI(api_key=api_key)
        self._default_model = default_model
        self._retry_attempts = retry_attempts

    async def call(self, request: LLMRequest) -> LLMResponse:
        """Send a request to the OpenAI API.

        Converts LLMRequest to OpenAI's chat completions format, makes the
        API call, and parses the response back to LLMResponse.

        Args:
            request: Structured LLM request.

        Returns:
            Parsed LLMResponse with content, tool calls, and usage.

        Raises:
            LLMClientError: If the call fails unexpectedly.
        """
        model_name = request.model or self._default_model
        kwargs: dict[str, Any] = {
            "model": model_name,
            "messages": request.messages,
        }
        # Newer OpenAI models (gpt-5*, o1*, o3*) reject `max_tokens` and
        # also reject custom `temperature`. They require
        # `max_completion_tokens` and only accept temperature=1. They also
        # consume tokens for hidden reasoning, so a 4 k budget often leaves
        # zero tokens for the visible tool call: scale it up.
        if _is_reasoning_model(model_name):
            kwargs["max_completion_tokens"] = max(request.max_tokens, 8192)
        else:
            kwargs["temperature"] = request.temperature
            kwargs["max_tokens"] = request.max_tokens

        if request.tools:
            tools_payload: list[dict[str, Any]] = []
            for t in request.tools:
                # Accept ToolDefinition contract OR a raw OpenAI dict.
                if isinstance(t, dict):
                    if t.get("type") == "function" and isinstance(t.get("function"), dict):
                        tools_payload.append(t)
                    elif {"name", "description", "parameters"} <= set(t.keys()):
                        tools_payload.append({
                            "type": "function",
                            "function": {
                                "name": t["name"],
                                "description": t["description"],
                                "parameters": t["parameters"],
                            },
                        })
                    else:
                        raise ValueError(f"Unrecognised tool dict shape: {t}")
                else:
                    tools_payload.append({
                        "type": "function",
                        "function": {
                            "name": t.name,
                            "description": t.description,
                            "parameters": t.parameters,
                        },
                    })
            kwargs["tools"] = tools_payload

        if request.tool_choice is not None:
            kwargs["tool_choice"] = request.tool_choice

        logger.debug(
            "OpenAIClient: calling model=%s, messages=%d, tools=%d",
            kwargs["model"],
            len(request.messages),
            len(request.tools) if request.tools else 0,
        )

        try:
            # Rate limits and 5xx from the provider are transient; without
            # backoff a single 429 aborted the phase and the runner degraded
            # the whole pipeline around it.
            response = await call_with_backoff(
                self._client.chat.completions.create,
                operation_name="openai_chat",
                max_attempts=self._retry_attempts,
                retryable_exceptions=(Exception,),
                **kwargs,
            )
        except Exception as exc:
            logger.error("OpenAIClient: API call failed: %s", exc)
            raise LLMClientError(f"OpenAI API call failed: {exc}") from exc

        choice = response.choices[0]
        tool_calls = self._parse_tool_calls(choice.message.tool_calls)

        usage = {
            "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
            "completion_tokens": response.usage.completion_tokens if response.usage else 0,
            "total_tokens": response.usage.total_tokens if response.usage else 0,
        }

        logger.debug(
            "OpenAIClient: response model=%s, usage=%s, tool_calls=%d",
            response.model,
            usage,
            len(tool_calls),
        )

        return LLMResponse(
            content=choice.message.content,
            tool_calls=tool_calls,
            model=response.model,
            usage=usage,
        )

    def _parse_tool_calls(self, raw_tool_calls: Any) -> list[ToolCall]:
        """Parse OpenAI tool call objects into ToolCall contracts.

        Args:
            raw_tool_calls: OpenAI response tool_calls list (may be None).

        Returns:
            List of parsed ToolCall objects.
        """
        if not raw_tool_calls:
            return []

        parsed: list[ToolCall] = []
        for tc in raw_tool_calls:
            try:
                arguments = json.loads(tc.function.arguments)
            except (json.JSONDecodeError, TypeError) as exc:
                logger.warning(
                    "OpenAIClient: failed to parse tool call arguments for %s: %s",
                    tc.function.name,
                    exc,
                )
                arguments = {}

            parsed.append(
                ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=arguments,
                )
            )
        return parsed


class LLMClientError(Exception):
    """Raised when an LLM call fails after all retries."""

    pass
