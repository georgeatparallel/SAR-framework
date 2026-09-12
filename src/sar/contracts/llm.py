"""LLM service contracts.

Defines the request/response shapes for LLM calls, tool definitions,
and tool call results. Decouples agents from any specific LLM provider.

Migrated from: inline openai.chat.completions.create() calls in agent_service.py
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ToolDefinition(BaseModel):
    """Schema for a callable tool exposed to the LLM.

    Attributes:
        name: Function name the LLM will call.
        description: What the tool does (shown to the LLM).
        parameters: JSON Schema dict for the function's arguments.
    """

    name: str
    description: str
    parameters: dict[str, Any]


class ToolCall(BaseModel):
    """A tool invocation parsed from the LLM's response.

    Attributes:
        id: Provider-specific call identifier (for multi-turn).
        name: Name of the function called.
        arguments: Parsed JSON arguments.
    """

    id: str
    name: str
    arguments: dict[str, Any]


class ToolResult(BaseModel):
    """Result of executing a tool call, to be sent back to the LLM.

    Attributes:
        tool_call_id: The id of the ToolCall this result responds to.
        content: Serialized result content (typically JSON string).
    """

    tool_call_id: str
    content: str


class LLMRequest(BaseModel):
    """Input to the LLM client.

    Encapsulates everything needed for a single LLM API call,
    including conversation history, tool schemas, and generation params.

    Attributes:
        messages: Conversation messages (system, user, assistant, tool roles).
        tools: Tool schemas to expose (None means no tools).
        tool_choice: Force a specific tool or 'auto' (None means provider default).
        model: Model identifier override (None means use config default).
        temperature: Sampling temperature.
        max_tokens: Maximum number of tokens to generate.
    """

    messages: list[dict[str, Any]] = Field(default_factory=list)
    # Agents may emit either ToolDefinition objects or raw OpenAI-format
    # dicts (``{"type": "function", "function": {...}}``). The client
    # normalises both forms before calling the provider.
    tools: list[ToolDefinition | dict[str, Any]] | None = None
    tool_choice: dict[str, Any] | None = None
    model: str | None = None
    temperature: float = 0.3
    max_tokens: int = 4096


class LLMResponse(BaseModel):
    """Output from the LLM client.

    Contains the model's text response and/or tool calls, along with
    usage information and the raw provider response for debugging.

    Attributes:
        content: Text content of the response (may be None if tool_calls present).
        tool_calls: List of tool invocations (empty if plain text response).
        model: Model that actually served the request.
        usage: Token usage breakdown with prompt_tokens, completion_tokens, total_tokens.
        raw: Provider-specific raw response object for debugging. Excluded from
            serialization by default.
    """

    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    model: str = ""
    usage: dict[str, int] = Field(
        default_factory=lambda: {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
    )
    raw: dict[str, Any] | None = Field(default=None, exclude=True)
