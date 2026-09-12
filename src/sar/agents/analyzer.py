"""Analyzer agent.

Synthesizes a structured Markdown report from paper summaries,
citing every claim with inline [Source N] references. Uses the
submit_report() tool to deliver the result.

Migrated from: agentic_sar/agent_service.py analyzer_agent()
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from sar.contracts.events import Event, EventType
from sar.contracts.llm import LLMRequest
from sar.llm.tools import submit_report_tool

if TYPE_CHECKING:
    from sar.contracts.pipeline import PaperSummary
    from sar.events import EventBus
    from sar.llm.client import LLMClient

logger = logging.getLogger(__name__)

_REPORT_SECTIONS = (
    "1. Executive Summary — 2-3 sentence overview of the answer to the query.\n"
    "2. Key Findings — bullet list of the most important results, each with "
    "at least one [Source N] citation.\n"
    "3. Methodology — describe the research approaches used in the sources.\n"
    "4. Evidence — detailed synthesis of evidence from all sources, grouped by theme.\n"
    "5. Gaps & Limitations — what the available sources do not cover or where evidence is weak.\n"
    "6. Conclusion — direct answer to the research question with confidence statement."
)


def _format_summaries(summaries: list[PaperSummary]) -> str:
    """Format paper summaries as numbered source blocks for the LLM prompt.

    Args:
        summaries: List of PaperSummary objects from reader agents.

    Returns:
        Multi-line string with one numbered block per source.
    """
    blocks: list[str] = []
    for i, ps in enumerate(summaries, start=1):
        source = ps.source
        title = getattr(source, "title", None) or "Untitled"
        year = getattr(source, "year", None) or ""
        url = getattr(source, "url", None) or ""
        extraction = ps.extraction or {}

        # PAPER_SUMMARY_SCHEMA (the schema the reader actually requests) emits
        # key_claims/key_quotes; DEFAULT_SCHEMA and a custom extraction_schema
        # may emit key_findings/summary instead, so accept both spellings.
        key_claims = extraction.get("key_claims") or extraction.get("key_findings") or []
        if isinstance(key_claims, list):
            claims_text = "; ".join(str(c) for c in key_claims) if key_claims else "N/A"
        else:
            claims_text = str(key_claims)

        methodology = extraction.get("methodology", "N/A") or "N/A"
        summary_text = extraction.get("summary", "") or ps.notes or ""

        # Verbatim quotes are the evidence the report's citations rest on. They
        # were previously extracted and then dropped here, leaving the analyzer
        # asked to cite [Source N] with nothing to cite from.
        quotes = extraction.get("key_quotes") or []
        if isinstance(quotes, str):
            quotes = [quotes]

        year_str = f" ({year})" if year else ""
        block = (
            f"[Source {i}] {title}{year_str}\n"
            f"URL: {url}\n"
            f"Key claims: {claims_text}\n"
            f"Methodology: {methodology}"
        )
        if summary_text:
            block += f"\nSummary: {summary_text}"
        if quotes:
            quoted = "\n".join(f'  - "{str(q)}"' for q in quotes if str(q).strip())
            if quoted:
                block += f"\nVerbatim quotes:\n{quoted}"
        blocks.append(block)

    return "\n\n".join(blocks) if blocks else "(No sources available)"


def _emit_complete(event_bus: EventBus | None, report_length: int) -> None:
    """Emit an analysis_complete event if a bus is available.

    Args:
        event_bus: Optional EventBus instance.
        report_length: Character length of the generated report.
    """
    if event_bus is None:
        return
    event = Event(
        event_type=EventType.AGENT_COMPLETE,
        data={
            "agent": "Analyzer",
            "event": "analysis_complete",
            "report_length": report_length,
        },
    )
    event_bus.emit(event)


async def run(
    query: str,
    summaries: list[PaperSummary],
    llm: LLMClient,
    feedback: str | None = None,
    event_bus: EventBus | None = None,
) -> dict[str, Any]:
    """Synthesize a Markdown report from paper summaries.

    Calls the LLM with the submit_report tool to produce a structured
    report. If feedback is provided (from a reviewer rework request),
    it is included in the prompt.

    Args:
        query: The research question being answered.
        summaries: Extracted paper summaries from reader agents.
        llm: LLM client (uses MODEL_PRO for synthesis quality).
        feedback: Optional reviewer feedback for rework iterations.
        event_bus: Optional event bus for progress updates.

    Returns:
        Dict with keys "report" (Markdown string) and
        "citations_used" (list of URLs).

    Raises:
        LLMClientError: If the LLM call fails.
        ValueError: If the LLM does not call submit_report tool.
    """
    sources_block = _format_summaries(summaries)

    system_prompt = (
        "You are an Expert Research Analyst. Your task is to synthesize a "
        "comprehensive, well-structured Markdown research report from the "
        "provided sources.\n\n"
        "REQUIRED REPORT STRUCTURE — include all six sections in this order:\n"
        f"{_REPORT_SECTIONS}\n\n"
        "CITATION RULES:\n"
        "- Every factual claim MUST be followed by an inline citation: [Source N].\n"
        "- Use the exact [Source N] numbering from the source list provided.\n"
        "- Do not fabricate information not found in the sources.\n"
        "- If sources disagree, note the disagreement explicitly.\n\n"
        "When you have composed the complete report, call submit_report with "
        "the full Markdown text and the list of all cited source URLs."
    )

    user_prompt = (
        f"Research question: {query}\n\n"
        f"--- SOURCES ---\n{sources_block}\n--- END SOURCES ---\n\n"
        "Write the report now, following the required structure and citation rules."
    )

    if feedback:
        user_prompt += (
            "\n\n--- REVISION NOTES ---\n"
            "The following reviewer feedback must be addressed in this revision:\n"
            f"{feedback}\n"
            "--- END REVISION NOTES ---"
        )

    request = LLMRequest(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        tools=[submit_report_tool()],
        tool_choice={"type": "function", "function": {"name": "submit_report"}},
    )

    response = await llm.call(request)

    tool_calls = getattr(response, "tool_calls", []) or []
    for tc in tool_calls:
        if tc.name == "submit_report":
            args = tc.arguments
            report = args.get("report", "")
            citations_used = args.get("citations_used", [])
            result: dict[str, Any] = {
                "report": report,
                "citations_used": citations_used,
            }
            _emit_complete(event_bus, len(report))
            logger.info(
                "Analyzer: report generated (%d chars, %d citations).",
                len(report),
                len(citations_used),
            )
            return result

    raise ValueError(
        "Analyzer: LLM did not call the submit_report tool. "
        f"Got tool_calls={[tc.name for tc in tool_calls]!r}."
    )
