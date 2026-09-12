"""Pipeline orchestration contracts.

Defines the state machine's data structures: phases, state,
iteration records, gate decisions, and the final result envelope.

Migrated from: implicit local variables in agentic_sar/agent_service.py run_agentic_pipeline()
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from sar.contracts.search import SearchResult


class PipelinePhase(str, Enum):
    """Named phases of the orchestration state machine.

    Transitions: EXPAND -> SEARCH -> JUDGE -> READ -> ANALYZE -> REVIEW -> {COMPLETE | EXPAND}
    FAILED is a terminal state reached when an unrecoverable error occurs.
    """

    EXPAND = "expand"
    SEARCH = "search"
    JUDGE = "judge"
    READ = "read"
    ANALYZE = "analyze"
    REVIEW = "review"
    COMPLETE = "complete"
    FAILED = "failed"


class ReviewerVerdict(str, Enum):
    """Possible outcomes from the reviewer agent.

    Attributes:
        APPROVED: Report meets quality bar.
        NEEDS_MORE_INFO: Sources are insufficient; new queries needed.
        NEEDS_REWORK: Sources are sufficient but analysis is flawed.
        FAILED: Unrecoverable quality failure.
    """

    APPROVED = "approved"
    NEEDS_MORE_INFO = "needs_more_info"
    NEEDS_REWORK = "needs_rework"
    FAILED = "failed"


class TokenBudget(BaseModel):
    """Token and cost tracking for a pipeline run.

    Accumulates token usage and estimated cost across all LLM calls
    in a single pipeline execution.

    Attributes:
        prompt_tokens: Total prompt/input tokens consumed.
        completion_tokens: Total completion/output tokens consumed.
        total_tokens: Sum of prompt and completion tokens.
        cost_usd: Running estimated cost in USD.
        ceiling_usd: Maximum allowed cost before the pipeline is halted.
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    ceiling_usd: float = 5.0

    @property
    def is_over_budget(self) -> bool:
        """Check whether accumulated cost exceeds the ceiling.

        Returns:
            True if cost_usd >= ceiling_usd.
        """
        return self.cost_usd >= self.ceiling_usd


class GateDecision(BaseModel):
    """Record of a gate check at a phase transition.

    Every transition between phases passes through a gate function.
    The decision is logged to the audit trail for full transparency.

    Attributes:
        gate_name: Identifier of the gate (e.g., 'quality_threshold').
        passed: Whether the gate allowed the transition.
        reason: Human-readable explanation.
        metrics: Quantitative data behind the decision.
        timestamp: When the gate was evaluated.
    """

    gate_name: str
    passed: bool
    reason: str
    metrics: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC)
    )


class PaperSummary(BaseModel):
    """Structured extraction from a single document by the reader agent.

    Links the original SearchResult to the agent's extraction and
    quality assessment.

    Attributes:
        source: The SearchResult this summary was extracted from.
        extraction: Structured data extracted from the document (key claims,
            methodology, findings, etc.).
        quality_score: Agent-assigned quality score on a 0-10 scale.
        notes: Free-text notes, caveats, or observations about the document.
    """

    source: SearchResult
    extraction: dict[str, Any] = Field(default_factory=dict)
    quality_score: float = Field(default=0.0, ge=0.0, le=10.0)
    notes: str = ""


class IterationRecord(BaseModel):
    """Snapshot of one iteration through the pipeline loop.

    Captures the queries used, results obtained, summaries produced,
    and gate decisions made during a single iteration.

    Attributes:
        iteration: 1-indexed iteration number.
        phase: The phase that was active during this iteration.
        queries: Search queries executed in this iteration.
        results_count: Number of search results obtained.
        summaries_count: Number of papers read and summarized.
        gate_decisions: All gate checks during this iteration.
        feedback: Reviewer feedback text (if NEEDS_REWORK or NEEDS_MORE_INFO).
        reviewer_verdict: The reviewer's verdict for this iteration (if any).
    """

    iteration: int
    phase: PipelinePhase
    queries: list[str] = Field(default_factory=list)
    results_count: int = 0
    summaries_count: int = 0
    gate_decisions: list[GateDecision] = Field(default_factory=list)
    feedback: str | None = None
    reviewer_verdict: ReviewerVerdict | None = None


class PipelineState(BaseModel):
    """Full mutable state of a pipeline run.

    Passed through every phase transition. The orchestrator reads and
    writes this; agents receive read-only slices of it.

    Attributes:
        phase: Current phase of the state machine.
        iteration: Current iteration (0-indexed).
        queries: Active search queries.
        results: Accumulated search results across all iterations.
        summaries: Paper summaries from reader agents.
        report: Current report text (None until ANALYZE phase).
        feedback: Reviewer feedback (None until REVIEW phase).
        gate_log: All gate decisions across all iterations.
        budget: Token and cost tracking.
        iterations: Per-iteration history records.
    """

    phase: PipelinePhase = PipelinePhase.EXPAND
    iteration: int = 0
    queries: list[str] = Field(default_factory=list)
    results: list[SearchResult] = Field(default_factory=list)
    summaries: list[PaperSummary] = Field(default_factory=list)
    report: str | None = None
    feedback: str | None = None
    gate_log: list[GateDecision] = Field(default_factory=list)
    budget: TokenBudget = Field(default_factory=TokenBudget)
    iterations: list[IterationRecord] = Field(default_factory=list)


class PipelineResult(BaseModel):
    """Final output envelope from a completed pipeline run.

    Returned by the orchestrator when the pipeline reaches COMPLETE
    or FAILED state. Contains the report, summaries, and full audit trail.

    Attributes:
        success: Whether the pipeline completed successfully.
        report: Final Markdown report (None if failed before ANALYZE).
        summaries: All paper summaries produced.
        iterations_used: Total number of iterations executed.
        budget: Final token and cost summary.
        gate_log: Complete gate decision trail for auditability.
        citations: Bibliographic records for every source that survived to
            synthesis, deduplicated by DOI or URL. Structured rather than
            formatted so callers can render APA, BibTeX, or their own style.
    """

    success: bool
    report: str | None = None
    summaries: list[PaperSummary] = Field(default_factory=list)
    citations: list[dict[str, Any]] = Field(default_factory=list)
    iterations_used: int = 0
    budget: TokenBudget = Field(default_factory=TokenBudget)
    gate_log: list[GateDecision] = Field(default_factory=list)
