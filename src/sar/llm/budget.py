"""Token and cost budget tracking.

Tracks cumulative token usage and estimated cost across all LLM calls
in a pipeline run. Can enforce budget ceilings to prevent runaway costs.

New in v2 — no v1 equivalent.
"""

from __future__ import annotations

import logging
import warnings

from sar.contracts.pipeline import TokenBudget

logger = logging.getLogger(__name__)


class BudgetTracker:
    """Tracks token usage and cost for a single pipeline run.

    Accumulates input/output tokens from each LLM call and
    estimates cost based on per-token pricing. Thread-safe for
    single-threaded async contexts (no concurrent mutations).

    Attributes:
        PRICING: Model -> {"prompt": price_per_1M, "completion": price_per_1M} mapping.
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

    def __init__(self, max_input_tokens: int = 0, ceiling_usd: float = 0.0) -> None:
        """Initialize the budget tracker.

        Args:
            max_input_tokens: Token ceiling (0 = unlimited).
            ceiling_usd: Cost ceiling in USD (0.0 = unlimited). Not currently
                enforced but logged as a warning when exceeded.
        """
        self._budget = TokenBudget(max_input_tokens=max_input_tokens)
        self._ceiling_usd = ceiling_usd
        self._call_count: int = 0

    def record(self, model: str, usage: dict[str, int]) -> None:
        """Record token usage from one LLM call.

        Accumulates prompt and completion tokens into the running total
        and estimates cost based on the model's pricing tier.

        Args:
            model: Model identifier used for this call (e.g., "gpt-4o-mini").
            usage: Dict with keys "prompt_tokens", "completion_tokens", and
                optionally "total_tokens".
        """
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)

        self._budget.input_tokens += prompt_tokens
        self._budget.output_tokens += completion_tokens
        self._call_count += 1

        # Estimate cost
        pricing = self._resolve_pricing(model)
        cost_increment = (
            prompt_tokens * pricing["prompt"] / 1_000_000
            + completion_tokens * pricing["completion"] / 1_000_000
        )
        self._budget.estimated_cost_usd += cost_increment

        logger.debug(
            "BudgetTracker: call #%d model=%s +%d/%d tokens, cost +$%.6f (cumulative: $%.4f)",
            self._call_count,
            model,
            prompt_tokens,
            completion_tokens,
            cost_increment,
            self._budget.estimated_cost_usd,
        )

        # Warn if approaching or exceeding ceilings
        if self._budget.is_over_budget:
            warnings.warn(
                f"Token budget exceeded: {self._budget.input_tokens} input tokens "
                f"vs ceiling of {self._budget.max_input_tokens}.",
                ResourceWarning,
                stacklevel=2,
            )

        if self._ceiling_usd > 0 and self._budget.estimated_cost_usd >= self._ceiling_usd:
            warnings.warn(
                f"Cost budget exceeded: ${self._budget.estimated_cost_usd:.4f} "
                f"vs ceiling of ${self._ceiling_usd:.2f}.",
                ResourceWarning,
                stacklevel=2,
            )

    def _resolve_pricing(self, model: str) -> dict[str, float]:
        """Look up pricing for a model, falling back to gpt-4o-mini rates.

        Args:
            model: Model identifier string.

        Returns:
            Dict with "prompt" and "completion" prices per 1M tokens.
        """
        if model in self.PRICING:
            return self.PRICING[model]

        # Try prefix matching for versioned model names
        for known_model in self.PRICING:
            if model.startswith(known_model):
                return self.PRICING[known_model]

        logger.debug(
            "BudgetTracker: unknown model '%s', using gpt-4o-mini pricing.", model
        )
        return self.PRICING["gpt-4o-mini"]

    def is_over_budget(self) -> bool:
        """Check if cumulative input tokens exceed the ceiling.

        Returns:
            True if over budget (and ceiling is set), False otherwise.
        """
        return self._budget.is_over_budget

    def snapshot(self) -> TokenBudget:
        """Return a copy of the current budget state.

        Returns:
            Independent TokenBudget snapshot.
        """
        return self._budget.copy()

    @property
    def call_count(self) -> int:
        """Return the number of LLM calls recorded.

        Returns:
            Total number of calls tracked.
        """
        return self._call_count

    @property
    def total_tokens(self) -> int:
        """Return the total number of tokens (input + output).

        Returns:
            Sum of input and output tokens.
        """
        return self._budget.input_tokens + self._budget.output_tokens

    @property
    def estimated_cost_usd(self) -> float:
        """Return the estimated cumulative cost in USD.

        Returns:
            Running cost estimate.
        """
        return self._budget.estimated_cost_usd
