"""Tests for BudgetTracker and the MeteredClient decorator."""

import logging

import pytest

from sar.contracts.llm import LLMRequest, LLMResponse, ToolCall
from sar.contracts.pipeline import TokenBudget
from sar.llm.budget import BudgetTracker
from sar.llm.metering import MeteredClient

# ---------------------------------------------------------------------------
# Helpers / stubs
# ---------------------------------------------------------------------------


def _usage(prompt: int, completion: int) -> dict[str, int]:
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }


class StubLLMClient:
    """MockLLMClient-like inner client returning one canned LLMResponse."""

    def __init__(self, response: LLMResponse) -> None:
        self.response = response
        self.calls: list[LLMRequest] = []

    async def call(self, request: LLMRequest) -> LLMResponse:
        self.calls.append(request)
        return self.response


class RecordingAuditLog:
    """Records record_llm_call / record kwargs for assertion."""

    def __init__(self) -> None:
        self.llm_calls: list[dict] = []
        self.events: list[tuple] = []

    def record_llm_call(self, **kwargs) -> None:
        self.llm_calls.append(kwargs)

    def record(self, event_type, data=None) -> None:
        self.events.append((event_type, data))


# ---------------------------------------------------------------------------
# BudgetTracker: wrapping and accumulation
# ---------------------------------------------------------------------------


class TestBudgetTrackerWrapping:
    """The tracker mutates the caller's TokenBudget in place."""

    def test_mutates_caller_budget_in_place(self):
        """Gates holding the same TokenBudget object see recorded totals."""
        budget = TokenBudget()
        tracker = BudgetTracker(budget=budget)

        tracker.record("gpt-4o-mini", _usage(100, 50))

        assert tracker.budget is budget
        assert budget.prompt_tokens == 100
        assert budget.completion_tokens == 50
        assert budget.total_tokens == 150
        assert budget.cost_usd > 0.0

    def test_creates_fresh_budget_when_omitted(self):
        """No budget argument gives the tracker its own TokenBudget."""
        tracker = BudgetTracker()
        assert isinstance(tracker.budget, TokenBudget)
        assert tracker.budget.total_tokens == 0

    def test_accumulates_tokens_and_cost_per_pricing(self):
        """Metered calls add tokens and PRICING-derived cost."""
        budget = TokenBudget()
        tracker = BudgetTracker(budget=budget)

        # 1M prompt + 1M completion on gpt-4o-mini: 0.15 + 0.60 USD.
        tracker.record("gpt-4o-mini", _usage(1_000_000, 1_000_000))
        assert budget.prompt_tokens == 1_000_000
        assert budget.completion_tokens == 1_000_000
        assert budget.total_tokens == 2_000_000
        assert budget.cost_usd == pytest.approx(0.75)

        # A second call on gpt-4o adds 2.50 (prompt) + 10.00 (completion).
        tracker.record("gpt-4o", _usage(1_000_000, 1_000_000))
        assert budget.total_tokens == 4_000_000
        assert budget.cost_usd == pytest.approx(0.75 + 12.50)

        assert tracker.call_count == 2
        assert tracker.unmetered_calls == 0
        assert tracker.total_tokens == 4_000_000
        assert tracker.estimated_cost_usd == pytest.approx(13.25)


class TestUnmeteredCalls:
    """All-zero or missing usage counts the call but adds nothing."""

    @pytest.mark.parametrize(
        "usage",
        [
            None,
            {},
            _usage(0, 0),
            {"prompt_tokens": 0, "completion_tokens": 0},
            {"prompt_tokens": None, "completion_tokens": None},
        ],
        ids=["none", "empty", "zero-usage", "zero-no-total", "null-values"],
    )
    def test_unmetered_call_adds_nothing(self, usage):
        """Tokens and cost stay untouched; unmetered_calls increments."""
        budget = TokenBudget()
        tracker = BudgetTracker(budget=budget)

        tracker.record("gpt-4o-mini", usage)

        assert tracker.call_count == 1
        assert tracker.unmetered_calls == 1
        assert budget.prompt_tokens == 0
        assert budget.completion_tokens == 0
        assert budget.total_tokens == 0
        assert budget.cost_usd == 0.0

    def test_mixed_metered_and_unmetered(self):
        """Unmetered calls interleaved with metered ones do not distort totals."""
        tracker = BudgetTracker()
        tracker.record("codex", None)
        tracker.record("gpt-4o-mini", _usage(10, 20))
        tracker.record("codex", _usage(0, 0))

        assert tracker.call_count == 3
        assert tracker.unmetered_calls == 2
        assert tracker.total_tokens == 30


class TestUnknownModelPricing:
    """Unknown models are counted but billed at zero, warned once."""

    def test_tokens_counted_cost_zero(self, caplog):
        budget = TokenBudget()
        tracker = BudgetTracker(budget=budget)
        with caplog.at_level(logging.WARNING, logger="sar.llm.budget"):
            tracker.record("mystery-model", _usage(500, 500))

        assert budget.total_tokens == 1000
        assert budget.cost_usd == 0.0

    def test_warning_logged_once_per_model(self, caplog):
        tracker = BudgetTracker()
        with caplog.at_level(logging.WARNING, logger="sar.llm.budget"):
            tracker.record("mystery-model", _usage(10, 10))
            tracker.record("mystery-model", _usage(10, 10))
            tracker.record("other-mystery", _usage(10, 10))

        warnings = [
            r for r in caplog.records
            if r.levelno == logging.WARNING and "no pricing" in r.getMessage()
        ]
        assert len(warnings) == 2
        assert any("mystery-model" in w.getMessage() for w in warnings)
        assert any("other-mystery" in w.getMessage() for w in warnings)

    def test_prefix_match_uses_known_pricing(self, caplog):
        """A dated variant of a known model bills at the base model's rate."""
        budget = TokenBudget()
        tracker = BudgetTracker(budget=budget)
        with caplog.at_level(logging.WARNING, logger="sar.llm.budget"):
            tracker.record("gpt-4o-mini-2025-01-01", _usage(1_000_000, 0))

        assert budget.cost_usd == pytest.approx(0.15)
        assert not [
            r for r in caplog.records if "no pricing" in r.getMessage()
        ]


class TestCeilingAndSnapshot:
    """Ceiling override, over-budget flip, and snapshot independence."""

    def test_ceiling_usd_overrides_budget_ceiling(self):
        budget = TokenBudget(ceiling_usd=5.0)
        BudgetTracker(budget=budget, ceiling_usd=0.5)
        assert budget.ceiling_usd == 0.5

    def test_ceiling_untouched_when_not_given(self):
        budget = TokenBudget(ceiling_usd=5.0)
        BudgetTracker(budget=budget)
        assert budget.ceiling_usd == 5.0

    def test_is_over_budget_flips_at_ceiling(self):
        budget = TokenBudget()
        tracker = BudgetTracker(budget=budget, ceiling_usd=2.5)
        assert tracker.is_over_budget() is False

        # 1M prompt tokens on gpt-4o cost exactly 2.50 USD == ceiling.
        tracker.record("gpt-4o", _usage(1_000_000, 0))
        assert tracker.is_over_budget() is True
        assert budget.is_over_budget is True

    def test_snapshot_is_independent_copy(self):
        budget = TokenBudget()
        tracker = BudgetTracker(budget=budget)
        tracker.record("gpt-4o-mini", _usage(100, 100))

        snap = tracker.snapshot()
        assert snap is not budget
        assert snap.total_tokens == 200

        tracker.record("gpt-4o-mini", _usage(100, 100))
        assert budget.total_tokens == 400
        assert snap.total_tokens == 200  # unchanged


# ---------------------------------------------------------------------------
# MeteredClient
# ---------------------------------------------------------------------------


class TestMeteredClient:
    """The decorator records usage into the tracker and audits each call."""

    async def test_records_usage_and_audit_with_response_source(self):
        response = LLMResponse(
            content="done",
            tool_calls=[ToolCall(id="t1", name="submit_row", arguments={})],
            model="gpt-4o-mini",
            usage=_usage(100, 50),
            raw={"source": "handoff-file"},
        )
        inner = StubLLMClient(response)
        budget = TokenBudget()
        tracker = BudgetTracker(budget=budget)
        audit = RecordingAuditLog()
        client = MeteredClient(inner, tracker, audit_log=audit)

        request = LLMRequest(messages=[{"role": "user", "content": "hi"}])
        result = await client.call(request)

        # Response passes through untouched; inner saw the request.
        assert result is response
        assert inner.calls == [request]

        # tracker.record ran: the shared budget was mutated.
        assert budget.prompt_tokens == 100
        assert budget.completion_tokens == 50
        assert tracker.call_count == 1

        # One audit event with the codex provenance in extra.
        assert len(audit.llm_calls) == 1
        call = audit.llm_calls[0]
        assert call["agent_name"] == "StubLLMClient"
        assert call["model"] == "gpt-4o-mini"
        assert call["input_tokens"] == 100
        assert call["output_tokens"] == 50
        assert call["tool_calls"] == ["submit_row"]
        assert call["extra"] == {"response_source": "handoff-file"}

    async def test_no_source_in_raw_sends_extra_none(self):
        response = LLMResponse(model="gpt-4o-mini", usage=_usage(10, 5))
        audit = RecordingAuditLog()
        client = MeteredClient(
            StubLLMClient(response), BudgetTracker(), audit_log=audit
        )

        await client.call(LLMRequest())

        assert audit.llm_calls[0]["extra"] is None

    async def test_model_falls_back_to_request_model(self):
        """Empty response.model defers to the request's model."""
        response = LLMResponse(model="", usage=_usage(10, 5))
        audit = RecordingAuditLog()
        tracker = BudgetTracker()
        client = MeteredClient(StubLLMClient(response), tracker, audit_log=audit)

        await client.call(LLMRequest(model="gpt-4o"))

        assert audit.llm_calls[0]["model"] == "gpt-4o"
        assert tracker.estimated_cost_usd == pytest.approx(
            10 * 2.50 / 1_000_000 + 5 * 10.00 / 1_000_000
        )

    async def test_no_audit_log_still_meters(self):
        """audit_log=None records budget only, without erroring."""
        response = LLMResponse(model="gpt-4o-mini", usage=_usage(7, 3))
        tracker = BudgetTracker()
        client = MeteredClient(StubLLMClient(response), tracker)

        await client.call(LLMRequest())

        assert tracker.total_tokens == 10
