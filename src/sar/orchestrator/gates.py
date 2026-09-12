"""Gate functions for pipeline phase transitions.

Each gate checks a precondition before allowing a transition.
Gates return GateDecision objects with metrics for the audit trail.
A failed gate blocks the transition and logs the reason.

All gate functions are pure: they inspect state and return a decision
without mutating anything.

New in v2 -- replaces implicit if/elif control flow.
"""

from __future__ import annotations

import logging

from sar.contracts.pipeline import (
    GateDecision,
    PipelineState,
    ReviewerVerdict,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Linear-flow gates (EXPAND -> SEARCH -> JUDGE -> READ -> ANALYZE -> REVIEW)
# ---------------------------------------------------------------------------


def check_queries_generated(state: PipelineState) -> GateDecision:
    """Gate: EXPAND -> SEARCH. Requires at least one query.

    Args:
        state: Current pipeline state after EXPAND phase.

    Returns:
        GateDecision with passed=True if queries list is non-empty.
    """
    count = len(state.queries)
    passed = count > 0
    return GateDecision(
        gate_name="queries_generated",
        passed=passed,
        reason=f"Generated {count} queries" if passed else "No queries generated",
        metrics={"query_count": count},
    )


def check_results_obtained(
    state: PipelineState, *, min_results: int = 1
) -> GateDecision:
    """Gate: SEARCH -> JUDGE. Requires at least ``min_results`` search results.

    Args:
        state: Current pipeline state after SEARCH phase.
        min_results: Minimum number of results required to proceed.

    Returns:
        GateDecision with passed=True if results list meets the minimum.
    """
    count = len(state.results)
    passed = count >= min_results
    return GateDecision(
        gate_name="results_obtained",
        passed=passed,
        reason=(
            f"Got {count} results (min={min_results})"
            if passed
            else f"Only {count} results, need {min_results}"
        ),
        metrics={"result_count": count, "min_required": min_results},
    )


def check_quality_threshold(
    state: PipelineState, *, min_score: float = 4.0, min_kept: int = 1
) -> GateDecision:
    """Gate: JUDGE -> READ. Requires that enough results pass the quality threshold.

    After the source judge scores each result, this gate verifies that
    at least ``min_kept`` results have a ``relevance_score`` at or above
    ``min_score``.

    Args:
        state: Current pipeline state after source-judge filtering.
        min_score: Minimum relevance score to consider a result "kept".
        min_kept: Minimum number of results above the threshold.

    Returns:
        GateDecision with passed=True if enough results survive filtering.
    """
    # Results should already be filtered by the source judge.
    # Count results with relevance_score above threshold if the attribute exists;
    # otherwise count all results (pre-judge results pass by default).
    kept = 0
    for r in state.results:
        score = getattr(r, "relevance_score", None)
        if score is not None:
            if score >= min_score:
                kept += 1
        else:
            # No score attribute -- treat as passing (judge hasn't scored yet)
            kept += 1

    passed = kept >= min_kept
    return GateDecision(
        gate_name="quality_threshold",
        passed=passed,
        reason=(
            f"{kept} results above score {min_score}"
            if passed
            else f"No results above score {min_score}"
        ),
        metrics={
            "kept_count": kept,
            "total_count": len(state.results),
            "threshold": min_score,
            "min_kept": min_kept,
        },
    )


def check_summaries_ready(state: PipelineState) -> GateDecision:
    """Gate: READ -> ANALYZE. Requires at least one paper summary.

    Args:
        state: Current pipeline state after reader agents.

    Returns:
        GateDecision with passed=True if summaries list is non-empty.
    """
    count = len(state.summaries)
    passed = count > 0
    return GateDecision(
        gate_name="summaries_ready",
        passed=passed,
        reason=f"{count} summaries ready" if passed else "No summaries produced",
        metrics={"summary_count": count},
    )


def check_report_submitted(state: PipelineState) -> GateDecision:
    """Gate: ANALYZE -> REVIEW. Requires a non-empty report.

    Args:
        state: Current pipeline state after analyzer.

    Returns:
        GateDecision with passed=True if state.report is non-empty.
    """
    has_report = state.report is not None and len(state.report.strip()) > 0
    return GateDecision(
        gate_name="report_submitted",
        passed=has_report,
        reason="Report submitted" if has_report else "No report submitted",
        metrics={"report_length": len(state.report) if state.report else 0},
    )


# ---------------------------------------------------------------------------
# Reviewer-outcome gates (REVIEW -> COMPLETE | ANALYZE | EXPAND)
# ---------------------------------------------------------------------------


def check_reviewer_approved(state: PipelineState) -> GateDecision:
    """Gate: REVIEW -> COMPLETE. Requires the latest reviewer verdict to be APPROVED.

    Inspects the most recent IterationRecord for its reviewer_verdict.

    Args:
        state: Current pipeline state after reviewer.

    Returns:
        GateDecision with passed=True if latest verdict is APPROVED.
    """
    verdict = _latest_verdict(state)
    passed = verdict == ReviewerVerdict.APPROVED
    return GateDecision(
        gate_name="reviewer_approved",
        passed=passed,
        reason=(
            "Reviewer approved"
            if passed
            else f"Reviewer verdict: {verdict.value if verdict else 'none'}"
        ),
        metrics={"verdict": verdict.value if verdict else "none"},
    )


def check_needs_rework(state: PipelineState) -> GateDecision:
    """Gate: REVIEW -> ANALYZE. Requires reviewer verdict = NEEDS_REWORK.

    Args:
        state: Current pipeline state.

    Returns:
        GateDecision with passed=True if verdict is NEEDS_REWORK.
    """
    verdict = _latest_verdict(state)
    passed = verdict == ReviewerVerdict.NEEDS_REWORK
    return GateDecision(
        gate_name="needs_rework",
        passed=passed,
        reason=(
            "Reviewer requested rework"
            if passed
            else f"Reviewer verdict: {verdict.value if verdict else 'none'}"
        ),
        metrics={"verdict": verdict.value if verdict else "none"},
    )


def check_needs_more_info(
    state: PipelineState, *, max_iterations: int = 3
) -> GateDecision:
    """Gate: REVIEW -> EXPAND. Requires verdict = NEEDS_MORE_INFO and budget for more iterations.

    Args:
        state: Current pipeline state.
        max_iterations: Maximum allowed iterations.

    Returns:
        GateDecision with passed=True if verdict is NEEDS_MORE_INFO
        and iteration count has not been exhausted.
    """
    verdict = _latest_verdict(state)
    is_needs_more = verdict == ReviewerVerdict.NEEDS_MORE_INFO
    under_limit = state.iteration < max_iterations

    passed = is_needs_more and under_limit

    if not is_needs_more:
        reason = f"Reviewer verdict: {verdict.value if verdict else 'none'}"
    elif not under_limit:
        reason = f"Max iterations reached ({state.iteration}/{max_iterations})"
    else:
        reason = (
            f"Reviewer requested more info, iteration {state.iteration}/{max_iterations}"
        )

    return GateDecision(
        gate_name="needs_more_info",
        passed=passed,
        reason=reason,
        metrics={
            "verdict": verdict.value if verdict else "none",
            "iteration": state.iteration,
            "max_iterations": max_iterations,
        },
    )


# ---------------------------------------------------------------------------
# Cross-cutting budget and iteration guards
# ---------------------------------------------------------------------------


def check_budget_remaining(state: PipelineState) -> GateDecision:
    """Cross-cutting gate: checks token budget has not been exceeded.

    Can be composed with any transition gate.

    Args:
        state: Current pipeline state.

    Returns:
        GateDecision with passed=True if within budget.
    """
    over = state.budget.is_over_budget
    return GateDecision(
        gate_name="budget_remaining",
        passed=not over,
        reason=(
            f"Budget OK (${state.budget.cost_usd:.2f} / ${state.budget.ceiling_usd:.2f})"
            if not over
            else (
                f"OVER BUDGET: ${state.budget.cost_usd:.2f} "
                f">= ${state.budget.ceiling_usd:.2f} ceiling"
            )
        ),
        metrics={
            "cost_usd": state.budget.cost_usd,
            "ceiling_usd": state.budget.ceiling_usd,
            "total_tokens": state.budget.total_tokens,
        },
    )


def check_max_iterations(
    state: PipelineState, *, max_iterations: int = 3
) -> GateDecision:
    """Cross-cutting gate: checks that max iteration count has not been reached.

    Args:
        state: Current pipeline state.
        max_iterations: Maximum allowed iterations.

    Returns:
        GateDecision with passed=True if under the iteration limit.
    """
    passed = state.iteration < max_iterations
    return GateDecision(
        gate_name="max_iterations",
        passed=passed,
        reason=(
            f"Iteration {state.iteration}/{max_iterations}"
            if passed
            else f"Max iterations reached ({state.iteration}/{max_iterations})"
        ),
        metrics={"iteration": state.iteration, "max": max_iterations},
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _latest_verdict(state: PipelineState) -> ReviewerVerdict | None:
    """Extract the most recent reviewer verdict from the iteration log.

    Args:
        state: Current pipeline state.

    Returns:
        The latest ReviewerVerdict, or None if no iterations recorded yet.
    """
    if not state.iterations:
        return None
    return state.iterations[-1].reviewer_verdict
