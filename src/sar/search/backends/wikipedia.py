"""Wikipedia search backend.

Uses the Wikipedia API for encyclopedic context and background
information. Free, no key required.

Migrated from: agentic_sar/academic_sources.py search_wikipedia()
"""

from __future__ import annotations

import logging
import re
from urllib.parse import quote_plus

import httpx

from sar.contracts.search import SearchResult, SourceType

logger = logging.getLogger(__name__)

_BASE_URL = "https://en.wikipedia.org/w/api.php"
_TIMEOUT = httpx.Timeout(20.0)


def _strip_html_tags(text: str) -> str:
    """Remove HTML tags from a string.

    Args:
        text: String potentially containing HTML tags.

    Returns:
        Plain text with HTML tags removed.
    """
    return re.sub(r"<[^>]+>", " ", text).strip()


class WikipediaBackend:
    """Wikipedia API search backend.

    Attributes:
        name: "wikipedia"
        requires_key: False
    """

    name: str = "wikipedia"
    requires_key: bool = False

    async def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        """Search Wikipedia for articles matching the query.

        Args:
            query: Search query string.
            limit: Max results.

        Returns:
            Normalized SearchResult list.
        """
        params = {
            "action": "query",
            "format": "json",
            "list": "search",
            "srsearch": query,
            "srlimit": min(limit, 50),
            "utf8": 1,
        }

        try:
            async with httpx.AsyncClient(
                timeout=_TIMEOUT, follow_redirects=True
            ) as client:
                resp = await client.get(_BASE_URL, params=params)
                resp.raise_for_status()
                data = resp.json()

            results: list[SearchResult] = []
            for item in data.get("query", {}).get("search", []):
                title = item.get("title", "").strip()
                if not title:
                    continue

                snippet_html = item.get("snippet", "")
                snippet = _strip_html_tags(snippet_html)

                url = (
                    "https://en.wikipedia.org/wiki/"
                    + quote_plus(title.replace(" ", "_"))
                )

                results.append(SearchResult(
                    title=title,
                    url=url,
                    snippet=snippet,
                    source_type=SourceType.WIKI,
                    backend_name=self.name,
                ))

            logger.info(
                "Wikipedia: %d results for '%s'", len(results), query
            )
            return results

        except Exception as exc:
            logger.warning(
                "Wikipedia search failed for '%s': %s", query, exc
            )
            raise

    async def healthcheck(self) -> bool:
        """Check if Wikipedia API is reachable.

        Returns:
            True if API responds.
        """
        params = {
            "action": "query",
            "format": "json",
            "list": "search",
            "srsearch": "test",
            "srlimit": 1,
        }
        try:
            async with httpx.AsyncClient(
                timeout=_TIMEOUT, follow_redirects=True
            ) as client:
                resp = await client.get(_BASE_URL, params=params)
                return resp.status_code == 200
        except Exception:
            return False
