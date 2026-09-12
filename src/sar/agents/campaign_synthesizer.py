"""Campaign dossier synthesizer agent.

Produces a codebook-compliant campaign dossier from reader summaries.
Uses the submit_campaign_dossier tool to deliver structured output
including inclusion memo, coded row, evidence log, and narrative report.

Migrated from: agentic_sar/campaign_service.py campaign_dossier_synthesizer_agent()
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from sar.campaign.schema import get_codebook_prompt_block
from sar.contracts.events import Event, EventType
from sar.contracts.llm import LLMRequest
from sar.llm.tools import submit_campaign_dossier_tool

if TYPE_CHECKING:
    from sar.contracts.campaign import CampaignSpec
    from sar.events import EventBus
    from sar.llm.client import LLMClient

logger = logging.getLogger(__name__)

_NORMALIZATION_RULES = (
    "- Trivalued fields (nonviolent, organised, external_support, outcome): "
    "use ONLY `0.0` (no/failure), `0.5` (partial/mixed), `1.0` (yes/success), or `NA`.\n"
    "- Binary fields (platform_blocked, internet_shutdown): "
    "use ONLY `0.0` (no), `1.0` (yes), or `NA`.\n"
    "- Numeric fields: provide bare numbers with no units, prose, or ranges. "
    "Use `NA` when the value cannot be determined.\n"
    "- Categorical fields: use only the exact allowed values listed in the codebook.\n"
    "- Do NOT put narrative or caveats in coded_row fields. "
    "Place all justification, uncertainty, and contested interpretations in "
    "evidence_log, uncertainty_note, and report_markdown."
)

_INCLUSION_CRITERIA = (
    "A campaign should be INCLUDED if it is:\n"
    "- A documented mass mobilization or protest campaign.\n"
    "- Sufficiently covered by available sources to code at least the key fields "
    "(outcome, duration_days, repression_level).\n"
    "Mark `included` as 'yes', 'no', or 'partial' in the inclusion_memo."
)


def _format_campaign_context(campaign_spec: CampaignSpec) -> str:
    """Format the campaign specification as a prompt context block.

    Args:
        campaign_spec: Input campaign specification.

    Returns:
        Formatted multi-line string describing the campaign.
    """
    lines: list[str] = [
        f"Campaign: {_spec_get(campaign_spec, 'campaign_name')}",
        f"Country: {_spec_get(campaign_spec, 'country')}",
        f"Year: {_spec_get(campaign_spec, 'year', 'year_start')}",
    ]
    region = _spec_get(campaign_spec, "region")
    if region:
        lines.append(f"Region: {region}")
    description = _spec_get(campaign_spec, "description", "notes")
    if description:
        lines.append(f"Notes / guidance: {description}")
    priority_fields = _spec_get(campaign_spec, "priority_fields", default=[])
    if priority_fields:
        lines.append(
            f"Priority fields for this campaign: {', '.join(priority_fields)}"
        )
    return "\n".join(lines)


def _spec_get(spec: Any, key: str, *aliases: str, default: Any = "") -> Any:
    """Read a campaign-spec field from either an object or a dict.

    Callers supply the spec in three shapes -- a pydantic model, a
    ``SimpleNamespace`` built from the batch CSV, or a plain dict -- and the
    batch path names some fields differently (``year_start`` for ``year``,
    ``notes`` for ``description``). Reading attributes directly raised
    ``AttributeError`` on every batch run, which the runner caught and turned
    into an empty "Synthesis failed" dossier.
    """
    for k in (key, *aliases):
        if isinstance(spec, dict):
            if spec.get(k) not in (None, ""):
                return spec[k]
        elif hasattr(spec, k):
            value = getattr(spec, k)
            if value not in (None, ""):
                return value
    return default


def _format_reader_summaries(reader_summaries: list[dict[str, Any]]) -> str:
    """Format reader summaries as numbered evidence blocks for the LLM prompt.

    Args:
        reader_summaries: List of extraction dicts from reader agents.

    Returns:
        Multi-line string with one numbered block per evidence item.
    """
    if not reader_summaries:
        return "(No source evidence available)"

    blocks: list[str] = []
    for i, summary in enumerate(reader_summaries, start=1):
        title = summary.get("title", "") or summary.get("source_title", "") or "Untitled"
        url = summary.get("url", "") or summary.get("source_url", "") or ""
        extraction = summary.get("extraction", summary)  # flat dict fallback

        # Campaign-specific fields, present only when the reader ran with a
        # campaign extraction schema.
        outcome = extraction.get("outcome", "")
        duration = extraction.get("duration_days", "")
        repression = extraction.get("repression_level", "")
        scope_signal = extraction.get("scope_signal", "")
        timeline_facts = extraction.get("timeline_facts", [])
        summary_text = extraction.get("summary", "") or summary.get("notes", "")

        # The generic pipeline feeds PAPER_SUMMARY_SCHEMA output
        # (key_claims/key_quotes). Without these the evidence block collapsed to
        # a bare title and URL, leaving the synthesizer nothing to code from.
        key_claims = extraction.get("key_claims") or []
        if isinstance(key_claims, str):
            key_claims = [key_claims]
        quotes = extraction.get("key_quotes") or []
        if isinstance(quotes, str):
            quotes = [quotes]
        methodology = extraction.get("methodology", "")

        field_lines: list[str] = []
        if outcome != "":
            field_lines.append(f"  outcome: {outcome}")
        if duration != "":
            field_lines.append(f"  duration_days: {duration}")
        if repression != "":
            field_lines.append(f"  repression_level: {repression}")
        if scope_signal:
            field_lines.append(f"  scope_signal: {scope_signal}")
        if timeline_facts:
            for tf in timeline_facts:
                label = tf.get("label", "")
                value = tf.get("value", "")
                field_lines.append(f"  {label}: {value}")

        block = f"[Evidence {i}] {title}"
        if url:
            block += f"\nURL: {url}"
        if summary_text:
            block += f"\nSummary: {summary_text}"
        if key_claims:
            claims = "; ".join(str(c) for c in key_claims if str(c).strip())
            if claims:
                block += f"\nKey claims: {claims}"
        if methodology:
            block += f"\nMethodology: {methodology}"
        if field_lines:
            block += "\nExtracted fields:\n" + "\n".join(field_lines)
        if quotes:
            quoted = "\n".join(f'  - "{str(q)}"' for q in quotes if str(q).strip())
            if quoted:
                block += f"\nVerbatim quotes:\n{quoted}"

        blocks.append(block)

    return "\n\n".join(blocks)


def _emit_synthesized(event_bus: EventBus | None, campaign_name: str) -> None:
    """Emit a campaign_synthesized event if a bus is available.

    Args:
        event_bus: Optional EventBus instance.
        campaign_name: Name of the campaign that was synthesized.
    """
    if event_bus is None:
        return
    event = Event(
        event_type=EventType.AGENT_COMPLETE,
        data={
            "agent": "CampaignSynthesizer",
            "event": "campaign_synthesized",
            "campaign_name": campaign_name,
        },
    )
    event_bus.emit(event)


async def run(
    campaign_spec: CampaignSpec,
    reader_summaries: list[dict[str, Any]],
    llm: LLMClient,
    event_bus: EventBus | None = None,
) -> dict[str, Any]:
    """Synthesize a campaign dossier from reader summaries.

    Calls the LLM with the submit_campaign_dossier tool, providing
    codebook definitions, allowed values, and normalization rules
    in the system prompt. The LLM must produce all required fields.

    Args:
        campaign_spec: Input specification (country, name, years, focus fields).
        reader_summaries: Extracted data from campaign reader agents.
        llm: LLM client (uses MODEL_PRO for synthesis quality).
        event_bus: Optional event bus for progress updates.

    Returns:
        Raw dossier dict with keys: inclusion_memo, coded_row,
        evidence_log, uncertainty_note, report_markdown, citations_used.
        Must be validated by campaign.validators before persistence.

    Raises:
        LLMClientError: If the LLM call fails.
        ValueError: If the LLM does not call submit_campaign_dossier tool.
    """
    codebook_block = get_codebook_prompt_block()
    campaign_context = _format_campaign_context(campaign_spec)
    evidence_block = _format_reader_summaries(reader_summaries)

    system_prompt = (
        "You are an Expert Political Science Research Coder. Your task is to "
        "synthesize evidence from multiple sources and produce a codebook-compliant "
        "campaign dossier for inclusion in a protest dataset.\n\n"
        f"--- CODEBOOK CONSTRAINTS ---\n{codebook_block}\n"
        "--- END CODEBOOK ---\n\n"
        f"--- NORMALIZATION RULES ---\n{_NORMALIZATION_RULES}\n"
        "--- END NORMALIZATION RULES ---\n\n"
        f"--- INCLUSION CRITERIA ---\n{_INCLUSION_CRITERIA}\n"
        "--- END INCLUSION CRITERIA ---\n\n"
        "Your output must cover ALL 39 codebook columns in coded_row. "
        "For each field you code, provide an evidence_log entry citing the source. "
        "Write a full narrative report_markdown summarizing the campaign and your "
        "coding decisions. Describe all major uncertainties in uncertainty_note.\n\n"
        "When you have completed the full dossier, call submit_campaign_dossier."
    )

    user_prompt = (
        f"--- CAMPAIGN SPECIFICATION ---\n{campaign_context}\n"
        "--- END SPECIFICATION ---\n\n"
        f"--- SOURCE EVIDENCE ---\n{evidence_block}\n"
        "--- END EVIDENCE ---\n\n"
        "Code this campaign now. Produce a complete, accurate dossier "
        "and call submit_campaign_dossier with all required fields."
    )

    request = LLMRequest(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        tools=[submit_campaign_dossier_tool()],
        tool_choice={
            "type": "function",
            "function": {"name": "submit_campaign_dossier"},
        },
    )

    response = await llm.call(request)

    tool_calls = getattr(response, "tool_calls", []) or []
    for tc in tool_calls:
        if tc.name == "submit_campaign_dossier":
            result: dict[str, Any] = tc.arguments
            _emit_synthesized(event_bus, campaign_spec.campaign_name)
            logger.info(
                "CampaignSynthesizer: dossier produced for '%s'.",
                campaign_spec.campaign_name,
            )
            return result

    raise ValueError(
        "CampaignSynthesizer: LLM did not call submit_campaign_dossier tool. "
        f"Got tool_calls={[tc.name for tc in tool_calls]!r}."
    )
