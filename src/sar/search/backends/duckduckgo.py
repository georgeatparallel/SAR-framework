"""DuckDuckGo search backend.

Fallback web search that requires no API key. Uses the
``duckduckgo_search`` library (DDGS class) for structured results.
Lower quality than Brave but always available.

Migrated from: agentic_sar/academic_sources.py search_duckduckgo_html()
"""

from __future__ import annotations

import asyncio
import logging

from sar.contracts.search import SearchResult, SourceType

logger = logging.getLogger(__name__)


class DuckDuckGoBackend:
    """DuckDuckGo search backend using the duckduckgo_search library.

    Attributes:
        name: "duckduckgo"
        requires_key: False
    """

    name: str = "duckduckgo"
    requires_key: bool = False

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        """Search DuckDuckGo for web results.

        Uses the ``duckduckgo_search`` library in a thread executor
        to avoid blocking the event loop.

        Args:
            query: Search query string.
            limit: Max results to extract.

        Returns:
            Normalized SearchResult list.
        """
        # The upstream package was renamed: `duckduckgo_search` is deprecated
        # in favour of `ddgs`. Prefer the new name and keep the old one
        # working, so an environment pinned to either still searches.
        try:
            from ddgs import DDGS
        except ImportError:
            try:
                from duckduckgo_search import DDGS  # type: ignore[no-redef]
            except ImportError as exc:
                logger.warning(
                    "Neither ddgs nor duckduckgo_search is installed (%s). "
                    "Install with: pip install ddgs",
                    exc,
                )
                raise

        try:
            loop = asyncio.get_running_loop()
            raw_results = await loop.run_in_executor(
                None, lambda: DDGS().text(query, max_results=limit)
            )

            results: list[SearchResult] = []
            for item in raw_results or []:
                title = item.get("title", "")
                url = item.get("href", "")
                body = item.get("body", "")

                if not url or not title:
                    continue

                results.append(SearchResult(
                    title=title,
                    url=url,
                    snippet=body,
                    source_type=SourceType.WEB,
                    backend_name=self.name,
                ))

            logger.info(
                "DuckDuckGo: %d results for '%s'", len(results), query
            )
            return results

        except Exception as exc:
            logger.warning(
                "DuckDuckGo search failed for '%s': %s", query, exc
            )
            raise

    async def healthcheck(self) -> bool:
        """Check if DuckDuckGo search is functional.

        Returns:
            True if a test query succeeds.
        """
        try:
            results = await self.search("test", limit=1)
            return len(results) > 0
        except Exception:
            return False
