"""Event system contracts.

Defines event types and payload shapes for the EventBus.
Used by SSE streaming, CLI output, and audit logging.

Migrated from: inline event dicts in agentic_sar/agent_service.py _emit()
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class EventType(str, Enum):
    """All event types emitted during a pipeline run.

    Attributes:
        PIPELINE_START: Pipeline execution begins.
        PIPELINE_END: Pipeline execution completes (success or failure).
        PHASE_ENTER: Entering a new pipeline phase.
        PHASE_EXIT: Exiting the current pipeline phase.
        GATE_CHECK: A gate decision was evaluated.
        SEARCH_START: A search operation is beginning.
        SEARCH_COMPLETE: A search operation finished.
        LLM_CALL_START: An LLM API call is beginning.
        LLM_CALL_COMPLETE: An LLM API call finished.
        AGENT_START: An agent is beginning its work.
        AGENT_COMPLETE: An agent finished its work.
        ERROR: An error occurred.
        WARNING: A non-fatal warning.
        INFO: Informational message.
    """

    PIPELINE_START = "pipeline_start"
    PIPELINE_END = "pipeline_end"
    PHASE_ENTER = "phase_enter"
    PHASE_EXIT = "phase_exit"
    GATE_CHECK = "gate_check"
    SEARCH_START = "search_start"
    SEARCH_COMPLETE = "search_complete"
    LLM_CALL_START = "llm_call_start"
    LLM_CALL_COMPLETE = "llm_call_complete"
    AGENT_START = "agent_start"
    AGENT_UPDATE = "agent_update"
    AGENT_COMPLETE = "agent_complete"
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class Event(BaseModel):
    """A single event emitted by the pipeline.

    All events carry a type, optional data payload, timestamp, and
    the pipeline run they belong to.

    Attributes:
        event_type: Event classification from EventType enum.
        data: Arbitrary JSON-serializable payload.
        timestamp: When the event was created.
        run_id: Pipeline run this event belongs to (None for global events).
    """

    event_type: EventType
    data: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    run_id: str | None = None
