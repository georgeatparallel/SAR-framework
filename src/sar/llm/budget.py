"""Token and cost budget tracking.

Tracks cumulative token usage and estimated cost across all LLM calls in a
pipeline run. The tracker mutates a live :class:`TokenBudget` (normally
``state.budget``), so the orchestrator's ``check_budget_remaining`` gate sees
every recorded call.

Unmetered backends: the Codex CLI reports no token counts, so its responses
carry all-zero usage. Those calls are counted (``unmetered_calls``) but add
nothing to tokens or cost, and they can never trip the cost ceiling.
"""

from __future__ import annotations

import logging

from sar.contracts.pipeline import TokenBudget

logger = logging.getLogger(__name__)


class BudgetTracker:
    """Accumulates LLM usage into a TokenBudget.

    Attributes:
        PRICING: Model -> {"prompt": price_per_1M, "completion": price_per_1M}.
    """

    # Pricing per 1M tokens (approximate, as of 2024-2025)
    PRICING: dict[str, dict[str, float]] = {
        "gpt-4o-mini": {"prompt": 0.15, "completion": 0.60},
        "gpt-4o-mini-2024-07-18": {"prompt": 0.15, "completion": 0.60},
        "gpt-4o": {"prompt": 2.50, "completion": 10.00},
        "gpt-4o-2024-08-06": {"prompt": 2.50, "completion": 10.00},
        "gpt-4-turbo": {"prompt": 10.00, "completion": 30.00},
        "gpt-3.5-turbo": {"prompt": 0.50, "completion": 1.50},
    }

    def __init__(
        self,
        budget: TokenBudget | None = None,
        ceiling_usd: float | None = None,
    ) -> None:
        """Wrap a live TokenBudget (or create a fresh one).

        Args:
            budget: The budget to accumulate into. Pass ``state.budget`` so
                gates observe the totals. A new TokenBudget when omitted.
            ceiling_usd: Overrides the budget's cost ceiling when given.
        """
        self._budget = budget if budget is not None else TokenBudget()
        if ceiling_usd is not None:
            self._budget.ceiling_usd = ceiling_usd
        self._call_count: int = 0
        self._unmetered_calls: int = 0
        self._unknown_models: set[str] = set()

    def record(self, model: str, usage: dict[str, int] | None) -> None:
        """Record one LLM call's usage.

        All-zero (or missing) usage is treated as an unmetered call: counted,
        but contributing no tokens and no cost. This is the normal case for
        the Codex CLI backend, which does not report token counts.

        Args:
            model: Model identifier used for this call.
            usage: Dict with "prompt_tokens" / "completion_tokens" keys.
        """
        self._call_count += 1
        usage = usage or {}
        prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
        completion_tokens = int(usage.get("completion_tokens", 0) or 0)

        if prompt_tokens == 0 and completion_tokens == 0:
            self._unmetered_calls += 1
            logger.debug(
                "BudgetTracker: call #%d model=%s unmetered (no usage reported)",
                self._call_count, model,
            )
            return

        self._budget.prompt_tokens += prompt_tokens
        self._budget.completion_tokens += completion_tokens
        self._budget.total_tokens = (
            self._budget.prompt_tokens + self._budget.completion_tokens
        )

        pricing = self._resolve_pricing(model)
        cost_increment = (
            prompt_tokens * pricing["prompt"] / 1_000_000
            + completion_tokens * pricing["completion"] / 1_000_000
        )
        self._budget.cost_usd += cost_increment

        logger.debug(
            "BudgetTracker: call #%d model=%s +%d/%d tokens, cost +$%.6f "
            "(cumulative: $%.4f)",
            self._call_count, model, prompt_tokens, completion_tokens,
            cost_increment, self._budget.cost_usd,
        )

    def _resolve_pricing(self, model: str) -> dict[str, float]:
        """Pricing for a model; unknown models cost zero, warned once.

        Billing an unknown model at some other model's rate would silently
        misstate cost, so unknown means unpriced.
        """
        if model in self.PRICING:
            return self.PRICING[model]
        for known_model in self.PRICING:
            if model.startswith(known_model):
                return self.PRICING[known_model]
        if model not in self._unknown_models:
            self._unknown_models.add(model)
            logger.warning(
                "BudgetTracker: no pricing for model '%s'; its tokens are "
                "counted but not billed.", model,
            )
        return {"prompt": 0.0, "completion": 0.0}

    def is_over_budget(self) -> bool:
        """True when accumulated cost has reached the budget's ceiling."""
        return self._budget.is_over_budget

    def snapshot(self) -> TokenBudget:
        """An independent copy of the current budget state."""
        return self._budget.model_copy()

    @property
    def budget(self) -> TokenBudget:
        """The live TokenBudget this tracker accumulates into."""
        return self._budget

    @property
    def call_count(self) -> int:
        """Number of LLM calls recorded, metered or not."""
        return self._call_count

    @property
    def unmetered_calls(self) -> int:
        """Calls recorded with no reported usage (e.g. the codex backend)."""
        return self._unmetered_calls

    @property
    def total_tokens(self) -> int:
        """Total tokens across all metered calls."""
        return self._budget.total_tokens

    @property
    def estimated_cost_usd(self) -> float:
        """Running cost estimate in USD."""
        return self._budget.cost_usd
