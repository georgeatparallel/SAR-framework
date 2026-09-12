"""Reviewer agent.

Evaluates the quality of an analyzer's report and decides the next
action via tool calls: mark_as_complete, request_rework, or
request_more_info.

Migrated from: agentic_sar/agent_service.py reviewer_agent()
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sar.contracts.events import Event, EventType
from sar.contracts.llm import LLMRequest
from sar.llm.tools import get_reviewer_tools

if TYPE_CHECKING:
    from sar.contracts.pipeline import ReviewerVerdict
    from sar.llm.client import LLMClient
    from sar.events import EventBus

logger = logging.getLogger(__name__)

_EVALUATION_CRITERIA = (
    "1. Citation coverage — every factual claim must have an inline [Source N] citation.\n"
    "2. Factual grounding — no claims that go beyond what the sources say; "
    "no hallucinations or unsupported assertions.\n"
    "3. Logical structure — the report must follow the required six-section format "
    "(Executive Summary, Key Findings, Methodology, Evidence, Gaps, Conclusion).\n"
    "4. Completeness — the report must directly answer the research question "
    "and cover all major angles supported by the available sources.\n"
    "5. No hallucinations — reject any report that invents statistics, citations, "
    "or sources not present in the submitted source list."
)


def _emit_decision(event_bus: EventBus | None, verdict: str) -> None:
    """Emit a review_decision event if a bus is available.

    Args:
        event_bus: Optional EventBus instance.
        verdict: The reviewer verdict string.
    """
    if event_bus is None:
        return
    event = Event(
        event_type=EventType.AGENT_COMPLETE,
        data={
            "agent": "Reviewer",
            "event": "review_decision",
            "verdict": verdict,
        },
    )
    event_bus.emit(event)


async def run(
    query: str,
    report: str,
    llm: LLMClient,
    is_final_round: bool = False,
    event_bus: EventBus | None = None,
) -> tuple[str, str | list[str] | None]:
    """Evaluate a report and decide the next pipeline action.

    On non-final rounds, the reviewer can:
    - mark_as_complete(): Report is satisfactory.
    - request_rework(feedback): Sources OK, analysis needs fixing.
    - request_more_info(queries): Sources insufficient, search again.

    On the final round, request_more_info is removed (no budget for
    more searches).

    Args:
        query: The original research question.
        report: The Markdown report to evaluate.
        llm: LLM client for evaluation.
        is_final_round: If True, only complete or rework are available.
        event_bus: Optional event bus for progress updates.

    Returns:
        Tuple of (verdict_command, payload):
        - ("complete", None)
        - ("needs_rework", feedback_string)
        - ("needs_more_info", list_of_new_queries)

    Raises:
        LLMClientError: If the LLM call fails.
    """
    round_note = (
        "NOTE: This is the final review round. "
        "You may only call mark_as_complete or request_rework — "
        "requesting more information is not available.\n\n"
        if is_final_round
        else ""
    )

    system_prompt = (
        "You are a Critical Research Reviewer. Your role is to evaluate the "
        "quality of a research report and decide the appropriate next action.\n\n"
        "EVALUATION CRITERIA — check each in order:\n"
        f"{_EVALUATION_CRITERIA}\n\n"
        f"{round_note}"
        "DECISION RULES:\n"
        "- If the report meets all criteria: call mark_as_complete.\n"
        "- If the report has flaws fixable with the existing sources: "
        "call request_rework with specific, actionable feedback.\n"
        "- If the sources are fundamentally insufficient to answer the question "
        "(and this is not the final round): call request_more_info with targeted "
        "search queries that would supply the missing information.\n"
        "Be decisive — do not request rework for minor stylistic issues."
    )

    user_prompt = (
        f"Original research question: {query}\n\n"
        f"--- REPORT TO REVIEW ---\n{report}\n--- END REPORT ---\n\n"
        "Evaluate the report against all criteria and call the appropriate tool."
    )

    tools = get_reviewer_tools(is_final_round)

    request = LLMRequest(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        tools=tools,
    )

    response = await llm.call(request)

    tool_calls = getattr(response, "tool_calls", []) or []

    if tool_calls:
        tc = tool_calls[0]
        name = tc.name
        args = tc.arguments

        if name == "mark_as_complete":
            _emit_decision(event_bus, "complete")
            logger.info("Reviewer: verdict=complete.")
            return ("complete", None)

        if name == "request_rework":
            feedback = args.get("feedback", "")
            _emit_decision(event_bus, "needs_rework")
            logger.info("Reviewer: verdict=needs_rework.")
            return ("needs_rework", feedback)

        if name == "request_more_info":
            queries: list[str] = args.get("queries", [])
            _emit_decision(event_bus, "needs_more_info")
            logger.info(
                "Reviewer: verdict=needs_more_info, %d new queries.", len(queries)
            )
            return ("needs_more_info", queries)

        # Unknown tool name — default to complete
        logger.warning(
            "Reviewer: unknown tool call %r — defaulting to complete.", name
        )
        _emit_decision(event_bus, "complete")
        return ("complete", None)

    # No tool call at all — default to complete
    logger.warning("Reviewer: LLM produced no tool call — defaulting to complete.")
    _emit_decision(event_bus, "complete")
    return ("complete", None)
