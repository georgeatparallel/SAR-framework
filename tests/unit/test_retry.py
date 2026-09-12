"""Tests for retry backoff and the circuit breaker."""

import pytest

from sar.llm.retry import (
    CircuitBreaker,
    CircuitOpenError,
    call_with_backoff,
    is_retryable_error,
)

# Tiny delays so retries add negligible wall-clock time.
_FAST = dict(base_delay=0.01, max_delay=0.02, jitter=0.0)


class _StatusError(Exception):
    """Exception carrying an HTTP status code."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


# ---------------------------------------------------------------------------
# is_retryable_error
# ---------------------------------------------------------------------------


class TestIsRetryableError:
    """Token matrix for transient-error classification."""

    @pytest.mark.parametrize(
        "exc, expected",
        [
            # Message tokens
            (ValueError("Rate limit exceeded"), True),
            (RuntimeError("the request timed out"), True),
            (RuntimeError("Too Many Requests"), True),
            (RuntimeError("temporarily unavailable"), True),
            (RuntimeError("internal server error"), True),
            (ValueError("bad input"), False),
            (KeyError("missing"), False),
            # Exception class-name tokens
            (TimeoutError(""), True),
            (ConnectionError(""), True),
            # HTTP status codes
            (_StatusError("throttled", 429), True),
            (_StatusError("gateway", 502), True),
            (_StatusError("bad request", 400), False),
        ],
        ids=[
            "rate-limit-msg", "timed-out-msg", "too-many-requests-msg",
            "temporarily-unavailable-msg", "server-error-msg",
            "plain-valueerror", "plain-keyerror",
            "timeout-class", "connection-class",
            "status-429", "status-502", "status-400",
        ],
    )
    def test_matrix(self, exc, expected):
        assert is_retryable_error(exc) is expected


# ---------------------------------------------------------------------------
# call_with_backoff
# ---------------------------------------------------------------------------


class TestCallWithBackoff:
    """Backoff loop over sync and async callables."""

    async def test_sync_func_returns_on_first_success(self):
        calls = []

        def func(x, *, y):
            calls.append((x, y))
            return x + y

        result = await call_with_backoff(func, 1, y=2, **_FAST)
        assert result == 3
        assert len(calls) == 1

    async def test_async_func_returns_on_first_success(self):
        async def func():
            return "ok"

        assert await call_with_backoff(func, **_FAST) == "ok"

    async def test_retries_retryable_error_then_succeeds_sync(self):
        attempts = 0

        def flaky():
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise RuntimeError("connection timed out")
            return "recovered"

        result = await call_with_backoff(flaky, max_attempts=5, **_FAST)
        assert result == "recovered"
        assert attempts == 3

    async def test_retries_retryable_error_then_succeeds_async(self):
        attempts = 0

        async def flaky():
            nonlocal attempts
            attempts += 1
            if attempts < 2:
                raise RuntimeError("upstream timed out")
            return 42

        result = await call_with_backoff(flaky, max_attempts=3, **_FAST)
        assert result == 42
        assert attempts == 2

    async def test_non_retryable_error_raises_immediately(self):
        attempts = 0

        def broken():
            nonlocal attempts
            attempts += 1
            raise ValueError("malformed payload")

        with pytest.raises(ValueError, match="malformed payload"):
            await call_with_backoff(broken, max_attempts=5, **_FAST)
        assert attempts == 1

    async def test_max_attempts_exhaustion_raises_last_error(self):
        attempts = 0

        def always_failing():
            nonlocal attempts
            attempts += 1
            raise RuntimeError("timed out again")

        with pytest.raises(RuntimeError, match="timed out again"):
            await call_with_backoff(always_failing, max_attempts=2, **_FAST)
        assert attempts == 2

    async def test_non_matching_exception_type_propagates(self):
        """Exceptions outside retryable_exceptions are never caught."""

        def broken():
            raise KeyError("timed out")  # retryable message, wrong type

        with pytest.raises(KeyError):
            await call_with_backoff(
                broken, retryable_exceptions=(ValueError,), **_FAST
            )


# ---------------------------------------------------------------------------
# CircuitBreaker
# ---------------------------------------------------------------------------


class TestCircuitBreaker:
    """State transitions: closed -> open -> half_open -> closed."""

    async def test_starts_closed_and_passes_calls_through(self):
        breaker = CircuitBreaker("svc", failure_threshold=2)
        assert breaker.state == "closed"

        result = await breaker.call(lambda: "value")
        assert result == "value"
        assert breaker.state == "closed"

    async def test_opens_after_threshold_failures(self):
        breaker = CircuitBreaker("svc", failure_threshold=3)

        def boom():
            raise RuntimeError("down")

        for _ in range(2):
            with pytest.raises(RuntimeError):
                await breaker.call(boom)
        assert breaker.state == "closed"  # below threshold

        with pytest.raises(RuntimeError):
            await breaker.call(boom)
        assert breaker.state == "open"

    async def test_open_circuit_rejects_without_calling(self):
        breaker = CircuitBreaker("svc", failure_threshold=1, reset_timeout=30.0)
        calls = 0

        def boom():
            nonlocal calls
            calls += 1
            raise RuntimeError("down")

        with pytest.raises(RuntimeError):
            await breaker.call(boom)
        assert breaker.state == "open"

        with pytest.raises(CircuitOpenError, match="svc"):
            await breaker.call(boom)
        assert calls == 1  # the function was not invoked while open

    async def test_half_open_probe_success_closes_circuit(self):
        """With a zero reset timeout, the next call probes and closes."""
        breaker = CircuitBreaker("svc", failure_threshold=1, reset_timeout=0.0)

        def boom():
            raise RuntimeError("down")

        with pytest.raises(RuntimeError):
            await breaker.call(boom)
        assert breaker.state == "open"

        result = await breaker.call(lambda: "recovered")
        assert result == "recovered"
        assert breaker.state == "closed"

    async def test_half_open_probe_failure_reopens(self):
        breaker = CircuitBreaker("svc", failure_threshold=1, reset_timeout=0.0)

        def boom():
            raise RuntimeError("down")

        with pytest.raises(RuntimeError):
            await breaker.call(boom)
        with pytest.raises(RuntimeError):
            await breaker.call(boom)  # half-open probe fails
        assert breaker.state == "open"

    async def test_success_resets_failure_count(self):
        """A success between failures keeps the circuit closed."""
        breaker = CircuitBreaker("svc", failure_threshold=2)

        def boom():
            raise RuntimeError("down")

        with pytest.raises(RuntimeError):
            await breaker.call(boom)
        await breaker.call(lambda: "ok")
        with pytest.raises(RuntimeError):
            await breaker.call(boom)
        assert breaker.state == "closed"  # never hit 2 consecutive

    def test_reset_forces_closed(self):
        breaker = CircuitBreaker("svc", failure_threshold=1)
        breaker._record_failure()
        assert breaker.state == "open"
        breaker.reset()
        assert breaker.state == "closed"
