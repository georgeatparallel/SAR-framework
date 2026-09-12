"""Semantic Scholar search backend.

Uses the Semantic Scholar Graph API v1 (free, no key required).
Returns papers with title, authors, year, abstract, citation count,
and open-access PDF URL when available.

API docs: https://api.semanticscholar.org/api-docs/graph
Migrated from: agentic_sar/academic_sources.py search_semantic_scholar()
"""

from __future__ import annotations

import logging

import aiohttp

from sar.contracts.search import SearchResult, SourceType

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.semanticscholar.org/graph/v1"
_FIELDS = (
    "title,url,abstract,authors,year,citationCount,"
    "isOpenAccess,openAccessPdf,externalIds"
)
_TIMEOUT = aiohttp.ClientTimeout(total=20)


class SemanticScholarBackend:
    """Semantic Scholar Graph API backend.

    Attributes:
        name: "semantic_scholar"
        requires_key: False (API key is optional for higher rate limits).
        base_url: API base URL.
    """

    name: str = "semantic_scholar"
    requires_key: bool = False

    def __init__(self, api_key: str | None = None) -> None:
        """Initialize with an optional API key.

        Args:
            api_key: Semantic Scholar API key for higher rate limits.
                     Passed as ``x-api-key`` header when present.
        """
        self._api_key = api_key

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        """Search Semantic Scholar for papers matching the query.

        Args:
            query: Search query string.
            limit: Max results to return.

        Returns:
            Normalized SearchResult list.
        """
        url = f"{_BASE_URL}/paper/search"
        params = {"query": query, "limit": limit, "fields": _FIELDS}
        headers: dict[str, str] = {}
        if self._api_key:
            headers["x-api-key"] = self._api_key

        try:
            async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
                async with session.get(url, params=params, headers=headers) as resp:
                    resp.raise_for_status()
                    data = await resp.json()

            results: list[SearchResult] = []
            for paper in data.get("data", []):
                doi = (paper.get("externalIds") or {}).get("DOI")
                pdf_url: str | None = None
                oap = paper.get("openAccessPdf")
                if oap and isinstance(oap, dict):
                    pdf_url = oap.get("url")
                authors = [
                    a.get("name", "")
                    for a in (paper.get("authors") or [])
                ]
                paper_url = paper.get("url") or (
                    f"https://www.semanticscholar.org/paper/{paper.get('paperId', '')}"
                )
                results.append(SearchResult(
                    title=paper.get("title", ""),
                    url=paper_url,
                    snippet=(paper.get("abstract") or "")[:400],
                    source_type=SourceType.ACADEMIC,
                    doi=doi,
                    authors=authors,
                    year=paper.get("year"),
                    citation_count=paper.get("citationCount", 0),
                    backend_name=self.name,
                    raw_metadata={"pdf_url": pdf_url} if pdf_url else {},
                ))
            logger.info(
                "Semantic Scholar: %d results for '%s'", len(results), query
            )
            return results

        except Exception as exc:
            logger.warning(
                "Semantic Scholar search failed for '%s': %s", query, exc
            )
            raise

    async def healthcheck(self) -> bool:
        """Ping the Semantic Scholar API.

        Returns:
            True if API responds with 200.
        """
        url = f"{_BASE_URL}/paper/search"
        params = {"query": "test", "limit": 1, "fields": "title"}
        headers: dict[str, str] = {}
        if self._api_key:
            headers["x-api-key"] = self._api_key

        try:
            async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
                async with session.get(url, params=params, headers=headers) as resp:
                    return resp.status == 200
        except Exception:
            return False
