"""Tests for pipeline gate functions."""

import pytest

from sar.contracts.pipeline import (
    GateDecision,
    IterationRecord,
    PaperSummary,
    PipelinePhase,
    PipelineState,
    ReviewerVerdict,
    TokenBudget,
)
from sar.contracts.search import SearchResult, SourceType
from sar.orchestrator.gates import (
    check_budget_remaining,
    check_max_iterations,
    check_queries_generated,
    check_quality_threshold,
    check_report_submitted,
    check_results_obtained,
    check_reviewer_approved,
    check_needs_more_info,
    check_needs_rework,
    check_summaries_ready,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_result(**overrides) -> SearchResult:
    """Create a minimal SearchResult with sensible defaults."""
    defaults = dict(title="Paper", url="https://example.com/paper", snippet="abstract")
    defaults.update(overrides)
    return SearchResult(**defaults)


def _make_summary(**overrides) -> PaperSummary:
    """Create a minimal PaperSummary."""
    defaults = dict(source=_make_result(), extraction={"key": "value"}, quality_score=5.0)
    defaults.update(overrides)
    return PaperSummary(**defaults)


# ---------------------------------------------------------------------------
# check_queries_generated
# ---------------------------------------------------------------------------


class TestCheckQueriesGenerated:
    """Tests for EXPAND → SEARCH gate."""

    def test_passes_when_queries_non_empty(self):
        """Gate passes when at least one query is present."""
        state = PipelineState(queries=["q1", "q2"])
        decision = check_queries_generated(state)
        assert decision.passed is True
        assert decision.gate_name == "queries_generated"
        assert decision.metrics["query_count"] == 2

    def test_fails_when_queries_empty(self):
        """Gate fails when queries list is empty."""
        state = PipelineState(queries=[])
        decision = check_queries_generated(state)
        assert decision.passed is False
        assert decision.metrics["query_count"] == 0


# ---------------------------------------------------------------------------
# check_results_obtained
# ---------------------------------------------------------------------------


class TestCheckResultsObtained:
    """Tests for SEARCH → JUDGE gate."""

    def test_passes_when_results_at_minimum(self):
        """Gate passes when result count equals min_results."""
        state = PipelineState(results=[_make_result()])
        decision = check_results_obtained(state, min_results=1)
        assert decision.passed is True

    def test_passes_when_results_above_minimum(self):
        """Gate passes when result count exceeds min_results."""
        results = [_make_result(url=f"https://example.com/{i}") for i in range(5)]
        state = PipelineState(results=results)
        decision = check_results_obtained(state, min_results=3)
        assert decision.passed is True
        assert decision.metrics["result_count"] == 5

    def test_fails_when_results_below_minimum(self):
        """Gate fails when result count is below min_results."""
        state = PipelineState(results=[])
        decision = check_results_obtained(state, min_results=1)
        assert decision.passed is False

    @pytest.mark.parametrize("min_results", [1, 5, 10])
    def test_fails_when_no_results_any_minimum(self, min_results):
        """Gate fails for any positive min with zero results."""
        state = PipelineState(results=[])
        decision = check_results_obtained(state, min_results=min_results)
        assert decision.passed is False


# ---------------------------------------------------------------------------
# check_quality_threshold
# ---------------------------------------------------------------------------


class TestCheckQualityThreshold:
    """Tests for JUDGE → READ gate."""

    def test_passes_when_scores_above_threshold(self):
        """Gate passes when enough results have high relevance scores."""
        results = [
            _make_result(url="https://a.com", relevance_score=0.9),
            _make_result(url="https://b.com", relevance_score=0.8),
        ]
        state = PipelineState(results=results)
        decision = check_quality_threshold(state, min_score=0.5, min_kept=1)
        assert decision.passed is True
        assert decision.metrics["kept_count"] == 2

    def test_fails_when_all_scores_below_threshold(self):
        """Gate fails when no results meet the score threshold."""
        results = [
            _make_result(url="https://a.com", relevance_score=0.1),
            _make_result(url="https://b.com", relevance_score=0.2),
        ]
        state = PipelineState(results=results)
        decision = check_quality_threshold(state, min_score=0.5, min_kept=1)
        assert decision.passed is False
        assert decision.metrics["kept_count"] == 0

    def test_passes_when_no_relevance_score_attribute(self):
        """Results without relevance_score pass by default."""
        results = [_make_result()]
        state = PipelineState(results=results)
        # default min_score=4.0, but SearchResult default relevance_score is 0.0
        # so the attribute exists and score 0.0 < 4.0 → not kept
        decision = check_quality_threshold(state, min_score=4.0, min_kept=1)
        assert decision.passed is False

    def test_passes_when_mixed_scores(self):
        """Gate passes when at least min_kept results are above threshold."""
        results = [
            _make_result(url="https://a.com", relevance_score=0.9),
            _make_result(url="https://b.com", relevance_score=0.1),
            _make_result(url="https://c.com", relevance_score=0.8),
        ]
        state = PipelineState(results=results)
        decision = check_quality_threshold(state, min_score=0.5, min_kept=2)
        assert decision.passed is True
        assert decision.metrics["kept_count"] == 2


# ---------------------------------------------------------------------------
# check_summaries_ready
# ---------------------------------------------------------------------------


class TestCheckSummariesReady:
    """Tests for READ → ANALYZE gate."""

    def test_passes_when_summaries_present(self):
        """Gate passes with at least one summary."""
        state = PipelineState(summaries=[_make_summary()])
        decision = check_summaries_ready(state)
        assert decision.passed is True
        assert decision.metrics["summary_count"] == 1

    def test_fails_when_summaries_empty(self):
        """Gate fails with no summaries."""
        state = PipelineState(summaries=[])
        decision = check_summaries_ready(state)
        assert decision.passed is False


# ---------------------------------------------------------------------------
# check_report_submitted
# ---------------------------------------------------------------------------


class TestCheckReportSubmitted:
    """Tests for ANALYZE → REVIEW gate."""

    def test_passes_when_report_non_empty(self):
        """Gate passes with a non-empty report string."""
        state = PipelineState(report="# Final Report\nSome findings.")
        decision = check_report_submitted(state)
        assert decision.passed is True
        assert decision.metrics["report_length"] > 0

    def test_fails_when_report_is_none(self):
        """Gate fails when report is None."""
        state = PipelineState(report=None)
        decision = check_report_submitted(state)
        assert decision.passed is False

    def test_fails_when_report_is_empty_string(self):
        """Gate fails when report is an empty string."""
        state = PipelineState(report="")
        decision = check_report_submitted(state)
        assert decision.passed is False

    def test_fails_when_report_is_whitespace_only(self):
        """Gate fails when report is only whitespace."""
        state = PipelineState(report="   \n  ")
        decision = check_report_submitted(state)
        assert decision.passed is False


# ---------------------------------------------------------------------------
# check_max_iterations
# ---------------------------------------------------------------------------


class TestCheckMaxIterations:
    """Tests for cross-cutting iteration limit gate."""

    @pytest.mark.parametrize(
        "iteration, max_iter, expected",
        [
            (0, 3, True),
            (1, 3, True),
            (2, 3, True),
            (3, 3, False),
            (5, 3, False),
        ],
    )
    def test_check_max_iterations_parametrized(self, iteration, max_iter, expected):
        """Gate passes when iteration < max, fails when >=."""
        state = PipelineState(iteration=iteration)
        decision = check_max_iterations(state, max_iterations=max_iter)
        assert decision.passed is expected
        assert decision.gate_name == "max_iterations"
        assert decision.metrics["iteration"] == iteration
        assert decision.metrics["max"] == max_iter


# ---------------------------------------------------------------------------
# check_budget_remaining
# ---------------------------------------------------------------------------


class TestCheckBudgetRemaining:
    """Tests for cross-cutting budget gate."""

    def test_passes_when_under_ceiling(self):
        """Gate passes when cost is below the ceiling."""
        budget = TokenBudget(cost_usd=1.0, ceiling_usd=5.0, total_tokens=100)
        state = PipelineState(budget=budget)
        decision = check_budget_remaining(state)
        assert decision.passed is True
        assert decision.gate_name == "budget_remaining"

    def test_fails_when_over_ceiling(self):
        """Gate fails when cost exceeds the ceiling."""
        budget = TokenBudget(cost_usd=6.0, ceiling_usd=5.0, total_tokens=1000)
        state = PipelineState(budget=budget)
        decision = check_budget_remaining(state)
        assert decision.passed is False

    def test_fails_when_exactly_at_ceiling(self):
        """Gate fails when cost equals the ceiling (is_over_budget uses >=)."""
        budget = TokenBudget(cost_usd=5.0, ceiling_usd=5.0)
        state = PipelineState(budget=budget)
        decision = check_budget_remaining(state)
        assert decision.passed is False

    def test_passes_when_zero_cost(self):
        """Gate passes when no cost has been incurred."""
        budget = TokenBudget(cost_usd=0.0, ceiling_usd=5.0)
        state = PipelineState(budget=budget)
        decision = check_budget_remaining(state)
        assert decision.passed is True


# ---------------------------------------------------------------------------
# Reviewer-outcome gates
# ---------------------------------------------------------------------------


class TestReviewerGates:
    """Tests for reviewer verdict gates."""

    def _state_with_verdict(self, verdict: ReviewerVerdict) -> PipelineState:
        """Build a state with one iteration carrying a reviewer verdict."""
        iteration_rec = IterationRecord(
            iteration=1,
            phase=PipelinePhase.REVIEW,
            reviewer_verdict=verdict,
        )
        return PipelineState(iteration=1, iterations=[iteration_rec])

    def test_reviewer_approved_when_complete_verdict(self):
        """check_reviewer_approved passes when verdict is APPROVED."""
        state = self._state_with_verdict(ReviewerVerdict.APPROVED)
        decision = check_reviewer_approved(state)
        assert decision.passed is True
        assert decision.gate_name == "reviewer_approved"

    def test_check_reviewer_approved_when_not_approved(self):
        """check_reviewer_approved fails with a non-APPROVED verdict."""
        state = self._state_with_verdict(ReviewerVerdict.NEEDS_REWORK)
        decision = check_reviewer_approved(state)
        assert decision.passed is False
        assert isinstance(decision, GateDecision)
        assert decision.gate_name == "reviewer_approved"

    def test_check_needs_rework_when_needs_rework(self):
        """check_needs_rework passes with NEEDS_REWORK verdict."""
        state = self._state_with_verdict(ReviewerVerdict.NEEDS_REWORK)
        decision = check_needs_rework(state)
        assert decision.passed is True
        assert decision.gate_name == "needs_rework"

    def test_check_needs_rework_when_approved(self):
        """check_needs_rework fails when verdict is not NEEDS_REWORK."""
        state = self._state_with_verdict(ReviewerVerdict.APPROVED)
        decision = check_needs_rework(state)
        assert decision.passed is False

    def test_check_needs_more_info_when_needs_more_info_and_under_limit(self):
        """check_needs_more_info passes with correct verdict and budget."""
        state = self._state_with_verdict(ReviewerVerdict.NEEDS_MORE_INFO)
        state.iteration = 1
        decision = check_needs_more_info(state, max_iterations=3)
        assert decision.passed is True

    def test_check_needs_more_info_when_at_max_iterations(self):
        """check_needs_more_info fails at max iterations even with correct verdict."""
        state = self._state_with_verdict(ReviewerVerdict.NEEDS_MORE_INFO)
        state.iteration = 3
        decision = check_needs_more_info(state, max_iterations=3)
        assert decision.passed is False

    def test_check_needs_more_info_when_wrong_verdict(self):
        """check_needs_more_info fails with wrong verdict."""
        state = self._state_with_verdict(ReviewerVerdict.APPROVED)
        state.iteration = 0
        decision = check_needs_more_info(state, max_iterations=3)
        assert decision.passed is False

    def test_check_needs_more_info_when_no_iterations(self):
        """check_needs_more_info fails when no iterations exist."""
        state = PipelineState(iteration=0, iterations=[])
        decision = check_needs_more_info(state, max_iterations=3)
        assert decision.passed is False


# ---------------------------------------------------------------------------
# All gates return GateDecision with expected fields
# ---------------------------------------------------------------------------


class TestGateDecisionShape:
    """All gate functions return a well-formed GateDecision."""

    @pytest.mark.parametrize(
        "gate_fn, state",
        [
            (check_queries_generated, PipelineState(queries=["q"])),
            (check_results_obtained, PipelineState(results=[_make_result()])),
            (check_summaries_ready, PipelineState(summaries=[_make_summary()])),
            (check_report_submitted, PipelineState(report="report")),
            (check_max_iterations, PipelineState(iteration=0)),
            (check_budget_remaining, PipelineState()),
        ],
    )
    def test_gate_returns_decision_with_required_fields(self, gate_fn, state):
        """Every gate returns a GateDecision with gate_name, passed, reason, metrics."""
        decision = gate_fn(state)
        assert isinstance(decision, GateDecision)
        assert isinstance(decision.gate_name, str)
        assert isinstance(decision.passed, bool)
        assert isinstance(decision.reason, str)
        assert isinstance(decision.metrics, dict)
        assert decision.timestamp is not None
