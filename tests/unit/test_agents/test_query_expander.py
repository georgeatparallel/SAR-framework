"""Tests for the query_expander agent.

Uses the shared MockLLMClient. Notes on coded behavior these tests pin:

- The tool-call branch matches on ``tc.tool_name``, not the ``name``
  field that contracts.llm.ToolCall actually defines -- so a real
  ToolCall never hits the tool branch and the agent falls back to
  parsing the text content.
- LLM failures are swallowed (logged) and the agent returns just the
  original query; no LLMClientError propagates.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from sar.agents import query_expander
from sar.contracts.events import EventType
from sar.contracts.llm import LLMResponse, ToolCall
from sar.events import EventBus

QUERY = "does social media help protests"


def tool_call_response(queries: list[Any]) -> SimpleNamespace:
    """Response whose tool call exposes tool_name, as the agent expects."""
    return SimpleNamespace(
        content=None,
        tool_calls=[
            SimpleNamespace(
                tool_name="submit_queries",
                arguments={"queries": queries},
            )
        ],
    )


class RaisingLLM:
    """LLM client whose call() always raises."""

    async def call(self, request: Any) -> Any:
        raise RuntimeError("provider down")


class TestToolCallPath:
    """Queries parsed from a submit_queries tool call."""

    async def test_returns_original_plus_variants(self, mock_llm):
        variants = ["protest mobilization online", "digital activism evidence"]
        mock_llm.set_response(tool_call_response(variants))

        result = await query_expander.run(QUERY, llm=mock_llm)

        assert result == [QUERY, *variants]
        assert result[0] == QUERY

    async def test_returns_expected_count(self, mock_llm):
        variants = [f"variant {i}" for i in range(5)]
        mock_llm.set_response(tool_call_response(variants))

        result = await query_expander.run(QUERY, llm=mock_llm, num_queries=5)

        assert len(result) == 6  # original + 5

    async def test_deduplicates_case_insensitively(self, mock_llm):
        variants = ["Alpha", "alpha", QUERY.upper(), "beta"]
        mock_llm.set_response(tool_call_response(variants))

        result = await query_expander.run(QUERY, llm=mock_llm)

        assert result == [QUERY, "Alpha", "beta"]

    async def test_skips_empty_entries(self, mock_llm):
        mock_llm.set_response(tool_call_response(["good one", "", None]))

        result = await query_expander.run(QUERY, llm=mock_llm)

        assert result == [QUERY, "good one"]

    async def test_request_carries_submit_queries_tool(self, mock_llm):
        mock_llm.set_response(tool_call_response(["v1"]))

        await query_expander.run(QUERY, llm=mock_llm, num_queries=3)

        request = mock_llm.calls[0]
        assert request.tools[0]["function"]["name"] == "submit_queries"
        assert request.tool_choice == {
            "type": "function",
            "function": {"name": "submit_queries"},
        }
        assert QUERY in request.messages[1]["content"]


class TestFallbackPaths:
    """Behavior without a matching tool call, as coded."""

    async def test_real_toolcall_shape_parses_the_tool_call(self, mock_llm):
        # contracts.llm.ToolCall exposes .name; the agents once matched only
        # .tool_name, which silently disabled this branch for every real
        # backend. Fixed: a real ToolCall now takes priority over content.
        mock_llm.set_response(
            LLMResponse(
                content='["from content a", "from content b"]',
                tool_calls=[
                    ToolCall(
                        id="tc-1",
                        name="submit_queries",
                        arguments={"queries": ["from tool call"]},
                    )
                ],
            )
        )

        result = await query_expander.run(QUERY, llm=mock_llm)

        assert "from tool call" in result
        assert "from content a" not in result

    async def test_json_object_content(self, mock_llm):
        mock_llm.set_response(
            SimpleNamespace(
                content='{"queries": ["obj one", "obj two"]}',
                tool_calls=[],
            )
        )

        result = await query_expander.run(QUERY, llm=mock_llm)

        assert result == [QUERY, "obj one", "obj two"]

    async def test_non_json_bulleted_content(self, mock_llm):
        mock_llm.set_response(
            SimpleNamespace(
                content="- first variant\n2. second variant\n\n* third variant",
                tool_calls=[],
            )
        )

        result = await query_expander.run(QUERY, llm=mock_llm)

        assert result == [QUERY, "first variant", "second variant", "third variant"]

    async def test_no_tool_call_and_empty_content(self, mock_llm):
        mock_llm.set_response(SimpleNamespace(content="", tool_calls=[]))

        result = await query_expander.run(QUERY, llm=mock_llm)

        assert result == [QUERY]

    async def test_llm_failure_returns_original_query(self):
        # The agent catches the exception and falls back; it does NOT
        # raise LLMClientError.
        result = await query_expander.run(QUERY, llm=RaisingLLM())

        assert result == [QUERY]


class TestEventBus:
    """AGENT_UPDATE emission when a bus is provided."""

    async def test_emits_agent_update(self, mock_llm):
        variants = ["v1", "v2"]
        mock_llm.set_response(tool_call_response(variants))
        bus = EventBus()
        seen: list[Any] = []
        bus.subscribe(EventType.AGENT_UPDATE, seen.append)

        await query_expander.run(QUERY, llm=mock_llm, event_bus=bus)

        assert len(seen) == 1
        event = seen[0]
        assert event.event_type == EventType.AGENT_UPDATE
        assert event.data["agent"] == "Query Expander"
        assert "2 search variants" in event.data["status"]
        assert event.data["data"]["variants"] == variants

    async def test_no_bus_is_fine(self, mock_llm):
        mock_llm.set_response(tool_call_response(["v1"]))

        result = await query_expander.run(QUERY, llm=mock_llm, event_bus=None)

        assert result == [QUERY, "v1"]
