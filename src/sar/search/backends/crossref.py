"""CrossRef search backend.

Uses the CrossRef REST API (free, polite pool with email header).
Returns scholarly metadata with DOI, citation count, and full-text links.

API docs: https://api.crossref.org/swagger-ui/index.html
Migrated from: agentic_sar/academic_sources.py search_crossref()
"""

from __future__ import annotations

import logging
import re

import aiohttp

from sar.contracts.search import SearchResult, SourceType

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.crossref.org/works"
_TIMEOUT = aiohttp.ClientTimeout(total=20)


class CrossRefBackend:
    """CrossRef REST API backend.

    Attributes:
        name: "crossref"
        requires_key: False
        base_url: API base URL.
        polite_email: Email for CrossRef polite pool (faster rate limits).
    """

    name: str = "crossref"
    requires_key: bool = False

    def __init__(self, polite_email: str = "researcher@example.com") -> None:
        """Initialize with optional polite-pool email.

        Args:
            polite_email: Email address for CrossRef polite pool.
                          Provides faster rate limits.
        """
        self._polite_email = polite_email

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        """Search CrossRef for works matching the query.

        Args:
            query: Search query string.
            limit: Max results.

        Returns:
            Normalized SearchResult list.
        """
        params = {
            "query": query,
            "rows": limit,
            "select": (
                "DOI,title,author,published-print,URL,"
                "abstract,is-referenced-by-count"
            ),
        }
        headers = {
            "User-Agent": (
                f"AgenticSAR/2.0 (mailto:{self._polite_email})"
            ),
        }

        try:
            async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
                async with session.get(
                    _BASE_URL, params=params, headers=headers
                ) as resp:
                    resp.raise_for_status()
                    data = await resp.json()

            results: list[SearchResult] = []
            for item in data.get("message", {}).get("items", []):
                title_list = item.get("title", [])
                title = title_list[0] if title_list else ""

                doi = item.get("DOI")
                link = item.get("URL") or (
                    f"https://doi.org/{doi}" if doi else ""
                )

                pub_date = (
                    item.get("published-print", {})
                    .get("date-parts", [[None]])[0]
                )
                year = pub_date[0] if pub_date else None

                authors: list[str] = []
                for a in item.get("author") or []:
                    given = a.get("given", "")
                    family = a.get("family", "")
                    if family:
                        authors.append(f"{given} {family}".strip())

                abstract = (item.get("abstract") or "")[:400]
                # Strip JATS XML tags from abstract if present
                if "<jats:" in abstract:
                    abstract = re.sub(r"<[^>]+>", " ", abstract).strip()

                citation_count = item.get("is-referenced-by-count", 0)

                results.append(SearchResult(
                    title=title,
                    url=link,
                    snippet=abstract,
                    source_type=SourceType.ACADEMIC,
                    doi=doi,
                    authors=authors,
                    year=year,
                    citation_count=citation_count,
                    backend_name=self.name,
                ))

            logger.info("CrossRef: %d results for '%s'", len(results), query)
            return results

        except Exception as exc:
            logger.warning("CrossRef search failed for '%s': %s", query, exc)
            raise

    async def healthcheck(self) -> bool:
        """Ping the CrossRef API.

        Returns:
            True if API responds.
        """
        params = {"query": "test", "rows": 1}
        headers = {
            "User-Agent": (
                f"AgenticSAR/2.0 (mailto:{self._polite_email})"
            ),
        }
        try:
            async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
                async with session.get(
                    _BASE_URL, params=params, headers=headers
                ) as resp:
                    return resp.status == 200
        except Exception:
            return False
