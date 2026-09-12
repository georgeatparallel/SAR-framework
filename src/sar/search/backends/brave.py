"""Brave Search API backend.

Primary web search backend replacing Google CSE. Free tier provides
2,000 queries/month. Returns structured JSON --- no browser automation needed.

API docs: https://brave.com/search/api/
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from sar.contracts.search import SearchResult, SourceType

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.search.brave.com/res/v1/web/search"
_TIMEOUT = httpx.Timeout(20.0)


class BraveBackend:
    """Brave Search API backend.

    Attributes:
        name: "brave"
        requires_key: True (BRAVE_API_KEY environment variable).
        api_key: Brave Search API key.
        base_url: API endpoint.
    """

    name: str = "brave"
    requires_key: bool = True

    def __init__(self, api_key: str = "") -> None:
        """Initialize with API key.

        Args:
            api_key: Brave Search API key. If empty, backend is disabled.
        """
        self._api_key = api_key

    def _extract_year(self, text: str) -> int | None:
        """Extract a 4-digit year from text.

        Args:
            text: Text that may contain a year.

        Returns:
            Year as int, or None.
        """
        match = re.search(r"\b(19|20)\d{2}\b", text or "")
        return int(match.group(0)) if match else None

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        """Search the web via Brave Search API.

        Args:
            query: Search query string.
            limit: Max results (Brave max per request: 20).

        Returns:
            Normalized SearchResult list.

        Raises:
            RuntimeError: If api_key is not set.
        """
        if not self._api_key:
            raise RuntimeError("BraveBackend: api_key is required but not set")

        params: dict[str, Any] = {"q": query, "count": min(limit, 20)}
        headers = {
            "X-Subscription-Token": self._api_key,
            "Accept": "application/json",
        }

        try:
            async with httpx.AsyncClient(
                timeout=_TIMEOUT, follow_redirects=True
            ) as client:
                resp = await client.get(
                    _BASE_URL, params=params, headers=headers
                )
                resp.raise_for_status()
                data = resp.json()

            results: list[SearchResult] = []
            web_results = data.get("web", {}).get("results", [])
            for item in web_results:
                title = item.get("title", "")
                url = item.get("url", "")
                description = item.get("description", "")
                age = item.get("age", "")

                results.append(SearchResult(
                    title=title,
                    url=url,
                    snippet=description,
                    source_type=SourceType.WEB,
                    year=self._extract_year(age or title),
                    backend_name=self.name,
                ))

            logger.info("Brave: %d results for '%s'", len(results), query)
            return results

        except Exception as exc:
            logger.warning("Brave search failed for '%s': %s", query, exc)
            raise

    async def healthcheck(self) -> bool:
        """Check if Brave API key is valid and API is reachable.

        Returns:
            True if API responds with 200.
        """
        if not self._api_key:
            return False

        params: dict[str, Any] = {"q": "test", "count": 1}
        headers = {
            "X-Subscription-Token": self._api_key,
            "Accept": "application/json",
        }
        try:
            async with httpx.AsyncClient(
                timeout=_TIMEOUT, follow_redirects=True
            ) as client:
                resp = await client.get(
                    _BASE_URL, params=params, headers=headers
                )
                return resp.status_code == 200
        except Exception:
            return False
