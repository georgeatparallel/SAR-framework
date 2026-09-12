"""Query expander agent.

Takes a user's natural-language research question and expands it into
3-5 complementary search queries with academic phrasing, temporal
scoping, and synonym variants.

Migrated from: agentic_sar/agent_service.py query_expander_agent()
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from sar.contracts.events import EventType
from sar.contracts.llm import LLMRequest, ToolCall
from sar.llm.tools import submit_queries_tool

if TYPE_CHECKING:
    from sar.llm.client import LLMClient
    from sar.events import EventBus

logger = logging.getLogger(__name__)


def _emit_update(event_bus: EventBus | None, data: dict) -> None:
    """Emit an AGENT_UPDATE event if a bus is available."""
    if event_bus is None:
        return
    from sar.contracts.events import Event

    event = Event(
        event_type=EventType.AGENT_UPDATE,
        data={"agent": "Query Expander", **data},
    )
    event_bus.emit(event)


def _parse_queries_from_text(text: str) -> list[str]:
    """Best-effort extraction of query list from plain-text LLM output.

    Handles JSON arrays, JSON objects wrapping an array, and
    newline-separated strings.

    Args:
        text: Raw text content from the LLM.

    Returns:
        List of query strings (may be empty on parse failure).
    """
    text = text.strip()
    # Try JSON parse first
    try:
        raw = json.loads(text)
        if isinstance(raw, list):
            return [str(v) for v in raw if v]
        if isinstance(raw, dict):
            for key in ("queries", "variants", "results"):
                if isinstance(raw.get(key), list):
                    return [str(v) for v in raw[key] if v]
            # Fallback: collect all string values
            return [str(v) for v in raw.values() if isinstance(v, str) and v]
    except (json.JSONDecodeError, TypeError, ValueError):
        pass

    # Fallback: split lines, strip bullets/numbering
    lines = []
    for line in text.splitlines():
        line = line.strip().lstrip("-*0123456789.) ").strip('"').strip()
        if line:
            lines.append(line)
    return lines


async def run(
    query: str,
    llm: LLMClient,
    event_bus: EventBus | None = None,
    context: str = "",
    num_queries: int = 5,
) -> list[str]:
    """Expand a single query into multiple search variants.

    Asks the LLM to generate complementary queries that cover
    different angles: synonyms, academic terminology, temporal scope,
    grey literature, and related concepts.

    Args:
        query: The user's original research question.
        llm: LLM client for generating expansions.
        event_bus: Optional event bus for progress updates.
        context: Additional context to guide expansion.
        num_queries: Number of queries to generate.

    Returns:
        List of search queries (always includes the original).

    Raises:
        LLMClientError: If the LLM call fails.
    """
    context_block = f"\nAdditional context: {context}" if context else ""

    system_prompt = (
        "You are a search query expansion specialist for academic research. "
        "Your task is to generate diverse search queries that will maximize "
        "recall across academic databases, grey literature, and web sources."
    )

    user_prompt = (
        f'Given the research question: "{query}"\n'
        f"{context_block}\n\n"
        f"Generate exactly {num_queries} complementary search queries. Rules:\n"
        "- Vary specificity from broad to narrow.\n"
        '- Add academic/formal phrasing (e.g. "empirical evidence", "systematic review").\n'
        "- Include synonyms and alternative terminology.\n"
        "- Include at least one query targeting grey literature or reports.\n"
        "- Include temporal scoping where useful (e.g. date ranges).\n"
        "- Keep each query concise (under 12 words).\n"
        "- Do NOT repeat the original query verbatim.\n\n"
        f"Call the submit_queries tool with exactly {num_queries} query strings."
    )

    # Build request with tool calling
    request = LLMRequest(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        tools=[submit_queries_tool(num_queries)],
        tool_choice={"type": "function", "function": {"name": "submit_queries"}},
    )

    try:
        response = await llm.call(request)

        variants: list[str] = []

        # Try tool calls first
        tool_calls = getattr(response, "tool_calls", []) or []
        for tc in tool_calls:
            tc_name = getattr(tc, "tool_name", "")
            tc_args = getattr(tc, "arguments", {}) or {}
            if tc_name == "submit_queries":
                raw_queries = tc_args.get("queries", [])
                variants = [str(q) for q in raw_queries if q]
                break

        # Fallback: parse from text content
        if not variants:
            content = getattr(response, "content", "") or ""
            if content:
                variants = _parse_queries_from_text(content)

    except Exception as exc:
        logger.warning(
            "Query Expander: LLM call failed -- %s. Falling back to original query.",
            exc,
        )
        variants = []

    # Always include the original query first, deduplicate
    seen: set[str] = set()
    all_queries: list[str] = []
    for q in [query] + variants:
        q_lower = q.strip().lower()
        if q_lower and q_lower not in seen:
            seen.add(q_lower)
            all_queries.append(q.strip())

    _emit_update(
        event_bus,
        {
            "status": f"Generated {len(all_queries) - 1} search variants",
            "data": {"variants": all_queries[1:]},
        },
    )
    logger.info(
        "Query Expander: %d variants for '%s'.",
        len(all_queries) - 1,
        query,
    )
    return all_queries
