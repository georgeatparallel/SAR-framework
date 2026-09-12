"""Retry logic: exponential backoff and circuit breaker.

Provides resilience for all external API calls (LLM, search backends).
Configurable via AppConfig or direct parameters.

Migrated from: agentic_sar/retry_utils.py
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import random
import time
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

RETRYABLE_STATUS_CODES: set[int] = {408, 429, 500, 502, 503, 504}


def _status_code(exc: Exception) -> int | None:
    """Extract HTTP status code from an exception if available.

    Args:
        exc: The exception to inspect.

    Returns:
        The HTTP status code, or None if not found.
    """
    response = getattr(exc, "response", None)
    code = getattr(exc, "status_code", None)
    if code is None and response is not None:
        code = getattr(response, "status_code", None)
    return code


def _retry_after_seconds(exc: Exception) -> float | None:
    """Extract Retry-After header value from an exception if available.

    Args:
        exc: The exception to inspect.

    Returns:
        Retry-After delay in seconds, or None if not found.
    """
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None) or {}
    retry_after = headers.get("retry-after") or headers.get("Retry-After")
    if retry_after is None:
        return None
    try:
        return max(float(retry_after), 0.0)
    except (TypeError, ValueError):
        return None


def is_retryable_error(exc: Exception) -> bool:
    """Determine whether an exception represents a retryable error.

    Checks HTTP status codes, exception class names, and error messages
    for patterns that indicate a transient failure.

    Args:
        exc: The exception to evaluate.

    Returns:
        True if the error is likely transient and worth retrying.
    """
    code = _status_code(exc)
    if code in RETRYABLE_STATUS_CODES:
        return True

    exc_name = exc.__class__.__name__.lower()
    if any(
        token in exc_name
        for token in ("ratelimit", "timeout", "connection", "internalserver", "servererror")
    ):
        return True

    message = str(exc).lower()
    return any(
        token in message
        for token in (
            "rate limit",
            "too many requests",
            "timed out",
            "timeout",
            "connection error",
            "server error",
            "temporarily unavailable",
        )
    )


async def call_with_backoff(
    func: Callable[..., Any],
    *args: Any,
    operation_name: str = "",
    max_attempts: int = 6,
    base_delay: float = 2.0,
    max_delay: float = 60.0,
    jitter: float = 0.5,
    retryable_exceptions: tuple[type[Exception], ...] = (Exception,),
    **kwargs: Any,
) -> Any:
    """Call a function with exponential backoff retry.

    Retries on specified exceptions with exponentially increasing delays
    and random jitter to avoid thundering herd. Supports both sync and
    async callables.

    Args:
        func: The async or sync function to call.
        *args: Positional arguments for func.
        operation_name: Label for logging (e.g., "openai_chat").
        max_attempts: Maximum number of attempts before giving up.
        base_delay: Initial delay in seconds.
        max_delay: Maximum delay cap in seconds.
        jitter: Random jitter range in seconds added to each delay.
        retryable_exceptions: Exception types that trigger a retry.
        **kwargs: Keyword arguments for func.

    Returns:
        The return value of func on success.

    Raises:
        The last exception if all attempts are exhausted.
    """
    op_name = operation_name or getattr(func, "__name__", "api_call")

    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            result = func(*args, **kwargs)
            if inspect.isawaitable(result):
                result = await result
            return result
        except retryable_exceptions as exc:
            last_exc = exc
            retryable = is_retryable_error(exc)

            if not retryable or attempt >= max_attempts:
                logger.error(
                    "[retry] %s failed after %d/%d attempts: %s",
                    op_name,
                    attempt,
                    max_attempts,
                    exc,
                )
                raise

            retry_after = _retry_after_seconds(exc)
            if retry_after is not None:
                delay = retry_after
            else:
                delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
            delay += random.uniform(0.0, jitter)

            logger.warning(
                "[retry] %s attempt %d/%d failed with retryable error (status=%s): %s. "
                "Retrying in %.1fs.",
                op_name,
                attempt,
                max_attempts,
                _status_code(exc),
                exc,
                delay,
            )
            await asyncio.sleep(delay)

    # Should not reach here, but satisfy type checker
    assert last_exc is not None
    raise last_exc


class CircuitBreaker:
    """Circuit breaker pattern for external service calls.

    Prevents cascading failures by short-circuiting calls to a
    failing service. Three states: CLOSED (normal), OPEN (failing),
    HALF_OPEN (testing recovery).

    Attributes:
        name: Identifier for this circuit (e.g., "brave_search").
        failure_threshold: Failures before opening.
        reset_timeout: Seconds before trying half-open.
        state: Current state (closed, open, half_open).
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 3,
        reset_timeout: float = 30.0,
    ) -> None:
        """Initialize the circuit breaker.

        Args:
            name: Circuit identifier for logging.
            failure_threshold: Number of consecutive failures to trigger open.
            reset_timeout: Seconds to wait before half-open test.
        """
        self.name = name
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self._state: str = "closed"
        self._failure_count: int = 0
        self._last_failure_time: float | None = None

    @property
    def state(self) -> str:
        """Return the current circuit state.

        Returns:
            One of "closed", "open", or "half_open".
        """
        return self._state

    def _check_open_timeout(self) -> None:
        """Transition from open to half_open if the reset timeout has elapsed."""
        if self._state != "open":
            return
        if self._last_failure_time is None:
            return
        elapsed = time.monotonic() - self._last_failure_time
        if elapsed >= self.reset_timeout:
            logger.info(
                "[circuit:%s] Reset timeout elapsed (%.1fs) — transitioning to half_open.",
                self.name,
                elapsed,
            )
            self._state = "half_open"

    async def call(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Execute func through the circuit breaker.

        Args:
            func: The async or sync function to call.
            *args: Positional arguments.
            **kwargs: Keyword arguments.

        Returns:
            Return value of func.

        Raises:
            CircuitOpenError: If the circuit is open and reset timeout hasn't elapsed.
        """
        self._check_open_timeout()

        if self._state == "open":
            raise CircuitOpenError(
                f"Circuit '{self.name}' is open. {self._failure_count} consecutive "
                f"failures; retry after {self.reset_timeout}s."
            )

        try:
            result = func(*args, **kwargs)
            if inspect.isawaitable(result):
                result = await result
            self._record_success()
            return result
        except Exception as exc:
            self._record_failure()
            raise

    def _record_success(self) -> None:
        """Record a successful call, resetting the circuit to closed."""
        if self._state == "half_open":
            logger.info(
                "[circuit:%s] Half-open probe succeeded — closing circuit.",
                self.name,
            )
        self._failure_count = 0
        self._last_failure_time = None
        self._state = "closed"

    def _record_failure(self) -> None:
        """Record a failed call, potentially opening the circuit."""
        self._failure_count += 1
        self._last_failure_time = time.monotonic()
        if self._failure_count >= self.failure_threshold:
            if self._state != "open":
                logger.warning(
                    "[circuit:%s] Circuit OPENED after %d consecutive failures.",
                    self.name,
                    self._failure_count,
                )
            self._state = "open"

    def reset(self) -> None:
        """Force-reset the circuit to closed. Used in tests."""
        self._state = "closed"
        self._failure_count = 0
        self._last_failure_time = None


class CircuitOpenError(Exception):
    """Raised when a circuit breaker is open and call is rejected."""

    pass
