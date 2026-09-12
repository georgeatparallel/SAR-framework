"""Generic structured reader agent.

Reads a single document and extracts structured information according
to a configurable extraction schema. Parametrized -- the same reader
handles both generic paper reading and campaign-specific extraction.

Migrated from: agentic_sar/agent_service.py paper_reader_agent() +
               agentic_sar/campaign_service.py campaign_reader_agent()
               (unified into one parametrized reader)
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, TYPE_CHECKING

from sar.contracts.events import EventType
from sar.contracts.llm import LLMRequest
from sar.llm.tools import submit_extraction_tool

if TYPE_CHECKING:
    from sar.contracts.search import SearchResult
    from sar.contracts.pipeline import PaperSummary
    from sar.llm.client import LLMClient
    from sar.events import EventBus

logger = logging.getLogger(__name__)

# Maximum characters of content to send to the LLM per document.
MAX_CONTENT_CHARS: int = 6000

# Default extraction schema when none is provided.
DEFAULT_SCHEMA: dict[str, str] = {
    "summary": "Brief summary of the source content",
    "key_findings": "List of key findings relevant to the query",
    "methodology": "Research methodology used (if applicable)",
    "relevance_to_query": "How this source relates to the research question",
    "quality_score": "Quality assessment 0-10",
    "limitations": "Any limitations or caveats noted",
}

# Paper-summary schema matching PaperSummary contract fields.
PAPER_SUMMARY_SCHEMA: dict[str, str] = {
    "key_claims": "3-5 specific claims relevant to the query",
    "methodology": "One sentence: type of study, model, or method used",
    "relevance_score": "0-10 integer, how directly relevant to the query",
    "key_quotes": "1-2 direct verbatim quotes most relevant to the query",
}


def _emit_update(event_bus: EventBus | None, data: dict) -> None:
    """Emit an AGENT_UPDATE event if a bus is available."""
    if event_bus is None:
        return
    from sar.contracts.events import Event

    event = Event(
        event_type=EventType.AGENT_UPDATE,
        data={"agent": "Reader", **data},
    )
    event_bus.emit(event)


def _get_attr(obj: Any, name: str, default: Any = "") -> Any:
    """Safely get an attribute from a possibly-stub object."""
    return getattr(obj, name, default) if obj is not None else default


def _parse_extraction_from_text(text: str) -> dict[str, Any]:
    """Best-effort JSON extraction from plain text.

    Args:
        text: Raw text that may contain embedded JSON.

    Returns:
        Parsed dict, or empty dict on failure.
    """
    text = text.strip()
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError):
        pass

    # Try to find JSON block in markdown code fence
    for marker in ("```json", "```"):
        if marker in text:
            start = text.index(marker) + len(marker)
            end = text.find("```", start)
            if end > start:
                try:
                    return json.loads(text[start:end].strip())
                except (json.JSONDecodeError, TypeError, ValueError):
                    pass

    return {}


async def run(
    result: SearchResult,
    query: str,
    llm: LLMClient,
    extraction_schema: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
    event_bus: EventBus | None = None,
) -> dict[str, Any]:
    """Read a single document and extract structured information.

    If extraction_schema is None, uses the default paper summary schema
    (key_claims, methodology, relevance_score, key_quotes).
    If provided, extracts fields according to the custom schema.

    Args:
        result: Search result containing the document content.
        query: Research query for relevance context.
        llm: LLM client for extraction.
        extraction_schema: Optional JSON schema defining output fields.
            If None, defaults to PaperSummary fields.
        context: Optional extra context dict injected into the prompt
            (e.g., campaign_spec for campaign readers).
        event_bus: Optional event bus for progress updates.

    Returns:
        Extracted data as a dict matching the schema.
        For default schema, matches PaperSummary fields.

    Raises:
        LLMClientError: If the LLM call fails.
    """
    title = _get_attr(result, "title", "Untitled")
    content = (_get_attr(result, "content", "") or "")[:MAX_CONTENT_CHARS]
    snippet = _get_attr(result, "snippet", "") or ""
    year = _get_attr(result, "year", "n.d.")
    authors = _get_attr(result, "authors", []) or []
    citation_count = _get_attr(result, "citation_count", 0) or 0
    url = _get_attr(result, "url", "") or _get_attr(result, "link", "") or ""

    # If no content at all, return minimal extraction
    if not content and not snippet:
        schema = extraction_schema or PAPER_SUMMARY_SCHEMA
        empty_result: dict[str, Any] = {
            "title": title,
            "authors": authors,
            "year": year,
            "url": url,
            "citation_count": citation_count,
        }
        for field in schema:
            if "score" in field.lower():
                empty_result[field] = 0
            elif "claims" in field.lower() or "findings" in field.lower() or "quotes" in field.lower():
                empty_result[field] = []
            else:
                empty_result[field] = "N/A"
        return empty_result

    # Choose schema
    schema = extraction_schema or PAPER_SUMMARY_SCHEMA
    tool = submit_extraction_tool(schema)

    # Build context block
    context_block = ""
    if context:
        context_block = f"\nAdditional context:\n{json.dumps(context, indent=2)}\n"

    # Build author string
    author_str = ", ".join(authors[:3]) if authors else "Unknown"

    system_prompt = (
        "You are an expert research extraction agent. Your task is to "
        "carefully read the provided source and extract structured "
        "information according to the specified schema. Only include "
        "facts directly supported by the source. Do not invent values."
    )

    user_prompt = (
        f'Research query: "{query}"\n\n'
        f"Source: {title} ({year})\n"
        f"Authors: {author_str}\n"
        f"Citations: {citation_count}\n"
        f"{context_block}\n"
        f"Content:\n{content or snippet}\n\n"
        "Extract the requested fields by calling the submit_extraction tool."
    )

    request = LLMRequest()
    request.messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    request.tools = [tool]
    request.tool_choice = {"type": "function", "function": {"name": "submit_extraction"}}
    request.model = None  # Use default fast model

    try:
        response = await llm.call(request)

        extracted: dict[str, Any] = {}

        # Try tool calls first
        tool_calls = getattr(response, "tool_calls", []) or []
        for tc in tool_calls:
            tc_name = getattr(tc, "tool_name", "")
            tc_args = getattr(tc, "arguments", {}) or {}
            if tc_name == "submit_extraction":
                extracted = dict(tc_args)
                break

        # Fallback: parse from text
        if not extracted:
            text_content = getattr(response, "content", "") or ""
            if text_content:
                extracted = _parse_extraction_from_text(text_content)

        # Merge with metadata
        result_dict: dict[str, Any] = {
            "title": title,
            "authors": authors,
            "year": year,
            "url": url,
            "citation_count": citation_count,
        }
        result_dict.update(extracted)

        # Ensure relevance_score / quality_score is an int
        for score_key in ("relevance_score", "quality_score"):
            if score_key in result_dict:
                try:
                    result_dict[score_key] = int(result_dict[score_key])
                except (TypeError, ValueError):
                    result_dict[score_key] = 5

        return result_dict

    except Exception as exc:
        logger.warning("Reader: failed for '%s' -- %s", title, exc)
        fallback: dict[str, Any] = {
            "title": title,
            "authors": authors,
            "year": year,
            "url": url,
            "citation_count": citation_count,
        }
        if "key_claims" in schema:
            fallback["key_claims"] = [snippet[:200]] if snippet else []
            fallback["methodology"] = "N/A"
            fallback["relevance_score"] = 3
            fallback["key_quotes"] = []
        return fallback


async def run_batch(
    results: list[SearchResult],
    query: str,
    llm: LLMClient,
    extraction_schema: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
    concurrency: int = 10,
    event_bus: EventBus | None = None,
) -> list[dict[str, Any]]:
    """Read multiple documents concurrently.

    Spawns concurrent reader tasks up to the concurrency limit.
    Failed reads are logged and skipped (not fatal).

    Args:
        results: Search results to read.
        query: Research query.
        llm: LLM client.
        extraction_schema: Optional custom extraction schema.
        context: Optional extra context.
        concurrency: Max concurrent reader tasks.
        event_bus: Optional event bus.

    Returns:
        List of extracted data dicts, sorted by relevance_score descending.
        Failed reads are excluded (logged, not raised).
    """
    if not results:
        return []

    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def _read_one(result: SearchResult) -> dict[str, Any]:
        async with semaphore:
            return await run(
                result=result,
                query=query,
                llm=llm,
                extraction_schema=extraction_schema,
                context=context,
                event_bus=None,  # Don't emit per-item; emit aggregate below
            )

    _emit_update(
        event_bus,
        {
            "status": (
                f"Spawning {len(results)} concurrent reader agents "
                f"(concurrency={concurrency})"
            ),
        },
    )
    logger.info("Reader batch: launching %d concurrent agents.", len(results))

    raw_results = await asyncio.gather(
        *[_read_one(r) for r in results],
        return_exceptions=True,
    )

    clean: list[dict[str, Any]] = []
    for item in raw_results:
        if isinstance(item, Exception):
            logger.warning("Reader batch: agent raised exception -- %s", item)
        else:
            clean.append(item)

    # Sort by relevance_score (or quality_score) descending
    def _sort_key(d: dict[str, Any]) -> float:
        for key in ("relevance_score", "quality_score"):
            if key in d:
                try:
                    return float(d[key])
                except (TypeError, ValueError):
                    pass
        return 0.0

    clean.sort(key=_sort_key, reverse=True)

    _emit_update(
        event_bus,
        {
            "status": (
                f"Done -- {len(clean)} documents read, "
                f"top scores: {', '.join(str(_sort_key(s)) for s in clean[:5])}"
            ),
        },
    )
    logger.info("Reader batch: %d summaries ready.", len(clean))
    return clean
