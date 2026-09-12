"""Source quality judge agent.

Scores search results by relevance, authority, and recency. Filters
out low-quality sources while preserving a minimum result count.

Migrated from: agentic_sar/agent_service.py source_quality_judge_agent()
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from sar.contracts.events import EventType
from sar.contracts.llm import LLMRequest
from sar.llm.tools import submit_scores_tool

if TYPE_CHECKING:
    from sar.contracts.search import SearchResult
    from sar.events import EventBus
    from sar.llm.client import LLMClient

logger = logging.getLogger(__name__)

MIN_KEEP: int = 5
MAX_JUDGE: int = 40
DEFAULT_MIN_SCORE: float = 4.0


def _emit_update(event_bus: EventBus | None, data: dict) -> None:
    """Emit an AGENT_UPDATE event if a bus is available."""
    if event_bus is None:
        return
    from sar.contracts.events import Event

    event = Event(
        event_type=EventType.AGENT_UPDATE,
        data={"agent": "Source Quality Judge", **data},
    )
    event_bus.emit(event)


def _result_url(r: SearchResult) -> str:
    """Safely extract url from a SearchResult."""
    return getattr(r, "url", "") or getattr(r, "link", "") or ""


def _result_to_summary(r: SearchResult) -> dict:
    """Extract a compact summary dict for LLM scoring."""
    return {
        "url": _result_url(r),
        "title": getattr(r, "title", "") or "",
        "snippet": (getattr(r, "snippet", "") or "")[:300],
        "source": str(getattr(r, "source", "web")),
        "year": getattr(r, "year", None),
        "citation_count": getattr(r, "citation_count", 0) or 0,
        "is_peer_reviewed": bool(getattr(r, "is_peer_reviewed", False)),
    }


def _parse_scores_from_text(text: str) -> list[dict]:
    """Best-effort parse of score data from plain text LLM output.

    Args:
        text: Raw text that may contain JSON.

    Returns:
        List of score dicts with url, score, reason keys.
    """
    text = text.strip()
    try:
        raw = json.loads(text)
        if isinstance(raw, list):
            return raw
        if isinstance(raw, dict):
            for key in raw:
                if isinstance(raw[key], list):
                    return raw[key]
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    return []


async def run(
    results: list[SearchResult],
    query: str,
    llm: LLMClient,
    event_bus: EventBus | None = None,
    min_score: float = DEFAULT_MIN_SCORE,
) -> list[SearchResult]:
    """Score and filter search results by quality.

    Sends the top MAX_JUDGE results to the LLM for scoring.
    Returns results sorted by relevance score, keeping at least
    MIN_KEEP results even if scores are low.

    Args:
        results: Raw search results to evaluate.
        query: The research query (for relevance assessment).
        llm: LLM client for quality scoring.
        event_bus: Optional event bus for progress updates.
        min_score: Minimum relevance score to keep (0-10).

    Returns:
        Filtered and sorted search results with updated relevance_score.

    Raises:
        LLMClientError: If the LLM call fails.
    """
    if not results:
        return results

    total = len(results)

    # Pre-rank by citation count; truncate to MAX_JUDGE
    candidates = sorted(
        results,
        key=lambda r: getattr(r, "citation_count", 0) or 0,
        reverse=True,
    )[:MAX_JUDGE]

    source_summaries = [_result_to_summary(r) for r in candidates]

    system_prompt = (
        "You are an academic source quality assessor. For each source, "
        "evaluate its relevance to the research query, authority "
        "(peer-reviewed, citation count, source reputation), and recency. "
        "Peer-reviewed sources with citations score higher."
    )

    user_prompt = (
        f'Research query: "{query}"\n\n'
        "Score each source below 0-10 for overall relevance and quality. "
        "Call the submit_scores tool with one entry per source.\n\n"
        f"Sources:\n{json.dumps(source_summaries, indent=2)}"
    )

    request = LLMRequest()
    request.messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    request.tools = [submit_scores_tool()]
    request.tool_choice = {"type": "function", "function": {"name": "submit_scores"}}
    request.model = None  # Use default fast model

    try:
        response = await llm.call(request)

        score_entries: list[dict] = []

        # Try tool calls first
        tool_calls = getattr(response, "tool_calls", []) or []
        for tc in tool_calls:
            tc_name = getattr(tc, "tool_name", "")
            tc_args = getattr(tc, "arguments", {}) or {}
            if tc_name == "submit_scores":
                score_entries = tc_args.get("scores", [])
                break

        # Fallback: parse from text
        if not score_entries:
            content = getattr(response, "content", "") or ""
            if content:
                score_entries = _parse_scores_from_text(content)

        # Build URL -> score mapping
        score_map: dict[str, float] = {}
        for entry in score_entries:
            url = entry.get("url", "")
            score = float(entry.get("score", 5))
            if url:
                score_map[url] = score

        if score_map and len(score_map) != len(candidates):
            logger.warning(
                "Source Quality Judge: length mismatch (got %d scores, "
                "expected %d) -- using partial judgements.",
                len(score_map),
                len(candidates),
            )

        # Update relevance_score on each result and filter
        for r in candidates:
            url = _result_url(r)
            r.relevance_score = score_map.get(url, 5.0)

        # Filter by min_score
        kept = [r for r in candidates if getattr(r, "relevance_score", 5.0) >= min_score]

        # Ensure minimum count
        if len(kept) < MIN_KEEP:
            kept = sorted(
                candidates,
                key=lambda r: getattr(r, "relevance_score", 0),
                reverse=True,
            )[:MIN_KEEP]
        else:
            kept.sort(
                key=lambda r: getattr(r, "relevance_score", 0),
                reverse=True,
            )

        _emit_update(
            event_bus,
            {"status": f"Kept {len(kept)}/{total} sources after quality filter"},
        )
        logger.info("Source Quality Judge: kept %d/%d.", len(kept), total)
        return kept

    except Exception as exc:
        logger.warning(
            "Source Quality Judge: error -- %s. Keeping all %d results.",
            exc,
            total,
        )
        _emit_update(
            event_bus,
            {
                "status": (
                    f"Quality filter skipped (error) -- keeping all {total} sources"
                ),
            },
        )
        return results
