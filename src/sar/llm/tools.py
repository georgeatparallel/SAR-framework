"""Tool schema definitions for LLM function calling.

Central registry of all tools that agents can expose to the LLM.
Each tool is a JSON dict matching the OpenAI function calling schema.
The orchestrator selects which tools to expose per agent.

Migrated from: inline tool dicts in agent_service.py and campaign_service.py
"""

from __future__ import annotations

from typing import Any

# Single source of truth for column names lives in campaign.schema.
# Importing here keeps the tool schemas aligned with the codebook automatically.
from sar.campaign.schema import DATASET_COLUMNS


def submit_queries_tool(num_queries: int = 5) -> dict[str, Any]:
    """Tool schema for the query expander to submit search queries.

    Args:
        num_queries: Expected number of queries (used in description).

    Returns:
        OpenAI function schema dict.
    """
    return {
        "type": "function",
        "function": {
            "name": "submit_queries",
            "description": (
                f"Submit {num_queries} expanded search queries. "
                "Return a diverse set of queries covering different "
                "angles of the research question."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "queries": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "List of expanded search query strings. "
                            "Each should be concise (under 12 words)."
                        ),
                    },
                },
                "required": ["queries"],
            },
        },
    }


def submit_scores_tool() -> dict[str, Any]:
    """Tool schema for the source judge to submit relevance scores.

    Returns:
        OpenAI function schema dict.
    """
    return {
        "type": "function",
        "function": {
            "name": "submit_scores",
            "description": (
                "Submit relevance scores for each search result. "
                "Score each source 0-10 based on relevance, authority, and recency."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "scores": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "url": {
                                    "type": "string",
                                    "description": "The URL of the search result.",
                                },
                                "score": {
                                    "type": "number",
                                    "description": "Relevance score 0-10.",
                                },
                                "reason": {
                                    "type": "string",
                                    "description": "Brief reason for the score.",
                                },
                            },
                            "required": ["url", "score", "reason"],
                        },
                        "description": "One scoring entry per search result.",
                    },
                },
                "required": ["scores"],
            },
        },
    }


def submit_extraction_tool(
    schema_fields: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Tool schema for the reader agent to submit extracted data.

    Args:
        schema_fields: Mapping of field name to description. If None,
            uses the default paper summary fields.

    Returns:
        OpenAI function schema dict.
    """
    if schema_fields is None:
        schema_fields = {
            "summary": "Brief summary of the source content.",
            "key_findings": "JSON array of key findings relevant to the query.",
            "methodology": "Research methodology used (if applicable).",
            "relevance_to_query": "How this source relates to the research question.",
            "quality_score": "Quality assessment 0-10 as a number.",
            "limitations": "Any limitations or caveats noted.",
        }

    properties: dict[str, Any] = {}
    for field_name, field_desc in schema_fields.items():
        if "score" in field_name.lower():
            properties[field_name] = {
                "type": "number",
                "description": field_desc,
            }
        elif "findings" in field_name.lower() or "claims" in field_name.lower() or "quotes" in field_name.lower():
            properties[field_name] = {
                "type": "array",
                "items": {"type": "string"},
                "description": field_desc,
            }
        else:
            properties[field_name] = {
                "type": "string",
                "description": field_desc,
            }

    return {
        "type": "function",
        "function": {
            "name": "submit_extraction",
            "description": (
                "Submit the structured extraction from this source. "
                "Include all fields specified in the schema."
            ),
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": list(schema_fields.keys()),
            },
        },
    }


def submit_report_tool() -> dict[str, Any]:
    """Tool schema for the analyzer agent to submit its report.

    The LLM calls this function to deliver the final Markdown report
    and list of cited URLs.

    Returns:
        OpenAI function schema dict.
    """
    return {
        "type": "function",
        "function": {
            "name": "submit_report",
            "description": (
                "Submit the completed research report. "
                "Call this as your final action after composing the full "
                "Markdown report."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "report": {
                        "type": "string",
                        "description": (
                            "The full, final, well-structured research report "
                            "in Markdown format."
                        ),
                    },
                    "citations_used": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "List of every source URL cited in the report, "
                            "taken exactly from the 'url' field of the "
                            "corresponding search result."
                        ),
                    },
                },
                "required": ["report", "citations_used"],
            },
        },
    }


def mark_as_complete_tool() -> dict[str, Any]:
    """Tool schema for the reviewer to approve the report.

    Returns:
        OpenAI function schema dict.
    """
    return {
        "type": "function",
        "function": {
            "name": "mark_as_complete",
            "description": (
                "Call this when the analysis is satisfactory, accurate, "
                "and fully addresses the user's original query."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    }


def request_rework_tool() -> dict[str, Any]:
    """Tool schema for the reviewer to request analysis rework.

    The LLM provides feedback text explaining what needs fixing.

    Returns:
        OpenAI function schema dict.
    """
    return {
        "type": "function",
        "function": {
            "name": "request_rework",
            "description": (
                "Call this when the analysis is flawed, inaccurate, or "
                "incomplete, but the existing source information appears "
                "sufficient to correct it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "feedback": {
                        "type": "string",
                        "description": (
                            "Specific, actionable feedback for the Analyzer "
                            "Agent. Explain what is missing, incorrect, or "
                            "needs improvement."
                        ),
                    },
                },
                "required": ["feedback"],
            },
        },
    }


def request_more_info_tool() -> dict[str, Any]:
    """Tool schema for the reviewer to request additional searches.

    The LLM provides a list of new search queries.

    Returns:
        OpenAI function schema dict.
    """
    return {
        "type": "function",
        "function": {
            "name": "request_more_info",
            "description": (
                "Call this when the analysis is fundamentally weak because "
                "sources are outdated, irrelevant, or insufficient. Also "
                "call this if the analysis mentions data-fetching errors."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "queries": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "New, specific search query strings to find "
                            "better information."
                        ),
                    },
                },
                "required": ["queries"],
            },
        },
    }


def submit_campaign_dossier_tool() -> dict[str, Any]:
    """Tool schema for the campaign synthesizer to submit a dossier.

    The LLM delivers inclusion memo, coded row, evidence log,
    uncertainty note, and narrative report in one structured call.

    Returns:
        OpenAI function schema dict.
    """
    return {
        "type": "function",
        "function": {
            "name": "submit_campaign_dossier",
            "description": (
                "Submit the final campaign dossier, coded row, evidence "
                "log, and citation URLs."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "inclusion_memo": {
                        "type": "object",
                        "properties": {
                            "campaign_name": {"type": "string"},
                            "country": {"type": "string"},
                            "start_date": {"type": "string"},
                            "end_date": {"type": "string"},
                            "included": {"type": "string"},
                            "reason_for_inclusion_or_exclusion": {"type": "string"},
                            "notes_on_scope_ambiguity": {"type": "string"},
                        },
                        "required": [
                            "campaign_name",
                            "country",
                            "start_date",
                            "end_date",
                            "included",
                            "reason_for_inclusion_or_exclusion",
                            "notes_on_scope_ambiguity",
                        ],
                    },
                    "coded_row": {
                        "type": "object",
                        "properties": {
                            col: {"type": ["string", "number", "null"]}
                            for col in DATASET_COLUMNS
                        },
                        "required": DATASET_COLUMNS,
                    },
                    "evidence_log": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "field_name": {"type": "string"},
                                "coded_value": {
                                    "type": ["string", "number", "null"],
                                },
                                "source_type": {"type": "string"},
                                "source_citation": {"type": "string"},
                                "source_link_or_identifier": {"type": "string"},
                                "notes": {"type": "string"},
                            },
                            "required": [
                                "field_name",
                                "coded_value",
                                "source_type",
                                "source_citation",
                                "source_link_or_identifier",
                                "notes",
                            ],
                        },
                    },
                    "uncertainty_note": {"type": "string"},
                    "report_markdown": {"type": "string"},
                    "citations_used": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": [
                    "inclusion_memo",
                    "coded_row",
                    "evidence_log",
                    "uncertainty_note",
                    "report_markdown",
                    "citations_used",
                ],
            },
        },
    }


def get_reviewer_tools(is_final_round: bool = False) -> list[dict[str, Any]]:
    """Return the tool set available to the reviewer agent.

    On non-final rounds: mark_as_complete, request_rework, request_more_info.
    On final round: mark_as_complete, request_rework only (no more searches).

    Args:
        is_final_round: Whether this is the last allowed iteration.

    Returns:
        List of tool schema dicts.
    """
    tools = [mark_as_complete_tool(), request_rework_tool()]
    if not is_final_round:
        tools.append(request_more_info_tool())
    return tools
