"""Shared test fixtures for all test modules.

Provides mock LLM client, mock search backends, temporary cache,
and canned data factories. All fixtures use pytest scope conventions.
"""

from __future__ import annotations

from typing import Any

import pytest

# --- Mock LLM Client ---

class MockLLMClient:
    """Mock LLM client that returns canned responses.

    Configure via set_response() before each test.

    Attributes:
        calls: List of (request,) tuples recorded.
        canned_response: The response to return from call().
    """

    def __init__(self) -> None:
        """Initialize with empty call log and no canned response."""
        self.calls: list[Any] = []
        self.canned_response: Any = None

    def set_response(self, response: Any) -> None:
        """Set the canned response for the next call().

        Args:
            response: The LLMResponse-like object to return.
        """
        self.canned_response = response

    async def call(self, request: Any) -> Any:
        """Record the call and return the canned response.

        Args:
            request: The LLMRequest-like object.

        Returns:
            The canned response set via set_response().
        """
        self.calls.append(request)
        return self.canned_response


@pytest.fixture
def mock_llm() -> MockLLMClient:
    """Provide a fresh MockLLMClient for each test.

    Returns:
        MockLLMClient instance.
    """
    return MockLLMClient()


# --- Mock Search Backend ---

class MockSearchBackend:
    """Mock search backend that returns canned results.

    Attributes:
        name: Backend name.
        requires_key: Always False.
        canned_results: Results to return from search().
    """

    def __init__(self, name: str = "mock", results: list[Any] | None = None) -> None:
        """Initialize with optional canned results.

        Args:
            name: Backend identifier.
            results: Canned search results.
        """
        self.name = name
        self.requires_key = False
        self.canned_results = results or []

    async def search(self, query: str, limit: int = 10) -> list[Any]:
        """Return canned results.

        Args:
            query: Ignored.
            limit: Ignored.

        Returns:
            Canned results list.
        """
        return self.canned_results

    async def healthcheck(self) -> bool:
        """Always healthy.

        Returns:
            True.
        """
        return True


@pytest.fixture
def mock_backend() -> MockSearchBackend:
    """Provide a fresh MockSearchBackend.

    Returns:
        MockSearchBackend instance.
    """
    return MockSearchBackend()


# --- Temporary Cache ---

@pytest.fixture
def tmp_cache(tmp_path: Any) -> Any:
    """Provide a temporary URLCache backed by a temp directory.

    Args:
        tmp_path: pytest built-in temporary path fixture.

    Returns:
        URLCache instance using a temp database.
    """
    # TODO(tests): from sar.search.cache import URLCache
    # TODO(tests): return URLCache(db_path=tmp_path / "test_cache.db", ttl_days=1)
    pass


# --- Temporary Audit Log ---

@pytest.fixture
def tmp_audit_log(tmp_path: Any) -> Any:
    """Provide a temporary AuditLog backed by a temp directory.

    Args:
        tmp_path: pytest built-in temporary path fixture.

    Returns:
        AuditLog instance using a temp directory.
    """
    # TODO(tests): from sar.persistence.audit import AuditLog
    # TODO(tests): return AuditLog(run_id="test-run-001", log_dir=tmp_path / "audit")
    pass
