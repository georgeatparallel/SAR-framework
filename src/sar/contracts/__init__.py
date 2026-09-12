"""Typed contracts for all service boundaries.

Every data structure that crosses a service boundary is defined here
as a Pydantic model. No bare dicts cross boundaries.

Re-exports all public contracts for convenience:
    from sar.contracts import SearchResult, PipelineState, CampaignSpec
"""

from __future__ import annotations

from .campaign import (
    BatchManifest,
    CampaignDossier,
    CampaignSpec,
    CodedRow,
    EvidenceEntry,
    InclusionMemo,
)
from .events import Event, EventType
from .llm import LLMRequest, LLMResponse, ToolCall, ToolDefinition, ToolResult
from .pipeline import (
    GateDecision,
    IterationRecord,
    PaperSummary,
    PipelinePhase,
    PipelineResult,
    PipelineState,
    ReviewerVerdict,
    TokenBudget,
)
from .search import SearchRequest, SearchResponse, SearchResult, SourceType

__all__ = [
    # search
    "SearchResult",
    "SearchRequest",
    "SearchResponse",
    "SourceType",
    # llm
    "ToolDefinition",
    "ToolCall",
    "ToolResult",
    "LLMRequest",
    "LLMResponse",
    # pipeline
    "PipelinePhase",
    "PipelineState",
    "GateDecision",
    "PaperSummary",
    "TokenBudget",
    "IterationRecord",
    "PipelineResult",
    "ReviewerVerdict",
    # campaign
    "CampaignSpec",
    "CampaignDossier",
    "CodedRow",
    "EvidenceEntry",
    "InclusionMemo",
    "BatchManifest",
    # events
    "Event",
    "EventType",
]
