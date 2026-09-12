"""SearchBackend protocol definition.

All search backends implement this protocol. The SearchEngine
depends only on this protocol, never on concrete implementations.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from sar.contracts.search import SearchResult


@runtime_checkable
class SearchBackend(Protocol):
    """Protocol that all search backends must implement.

    Attributes:
        name: Unique backend identifier (e.g., "semantic_scholar").
        requires_key: Whether this backend needs an API key to function.
    """

    name: str
    requires_key: bool

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        """Execute a search query against this backend.

        Args:
            query: Natural-language search query.
            limit: Maximum number of results to return.

        Returns:
            List of SearchResult objects normalized to the universal schema.

        Raises:
            SearchBackendError: If the backend encounters an unrecoverable error.
        """
        ...

    async def healthcheck(self) -> bool:
        """Check if the backend is reachable and functional.

        Returns:
            True if the backend is healthy, False otherwise.
        """
        ...
