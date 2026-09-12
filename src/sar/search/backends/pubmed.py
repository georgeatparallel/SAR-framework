"""PubMed search backend.

Uses PubMed E-utilities (two-step: esearch + esummary, free, no key).
Returns biomedical literature metadata.

API docs: https://www.ncbi.nlm.nih.gov/books/NBK25500/
Migrated from: agentic_sar/academic_sources.py search_pubmed()
"""

from __future__ import annotations

import logging

import httpx

from sar.contracts.search import SearchResult, SourceType

logger = logging.getLogger(__name__)

_EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
_TIMEOUT = httpx.Timeout(20.0)


class PubMedBackend:
    """PubMed E-utilities backend.

    Two-step search: esearch returns PMIDs, esummary returns metadata.

    Attributes:
        name: "pubmed"
        requires_key: False
    """

    name: str = "pubmed"
    requires_key: bool = False

    def __init__(self, email: str = "researcher@example.com") -> None:
        """Initialize with contact email for NCBI polite access.

        Args:
            email: Email address sent with API requests.
        """
        self._email = email

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        """Search PubMed for articles matching the query.

        Two-step process: ESearch to retrieve PMIDs, then ESummary
        to fetch metadata for each PMID.

        Args:
            query: Search query string.
            limit: Max results.

        Returns:
            Normalized SearchResult list.
        """
        try:
            async with httpx.AsyncClient(
                timeout=_TIMEOUT, follow_redirects=True
            ) as client:
                # Step 1: ESearch — get PMIDs
                esearch_params = {
                    "db": "pubmed",
                    "term": query,
                    "retmax": limit,
                    "retmode": "json",
                    "tool": "agentic_sar",
                    "email": self._email,
                }
                resp = await client.get(
                    f"{_EUTILS_BASE}/esearch.fcgi", params=esearch_params
                )
                resp.raise_for_status()
                search_data = resp.json()

                pmids = (
                    search_data.get("esearchresult", {}).get("idlist", [])
                )
                if not pmids:
                    logger.info("PubMed: 0 results for '%s'", query)
                    return []

                # Step 2: ESummary — get metadata
                esummary_params = {
                    "db": "pubmed",
                    "id": ",".join(pmids),
                    "retmode": "json",
                    "tool": "agentic_sar",
                    "email": self._email,
                }
                resp = await client.get(
                    f"{_EUTILS_BASE}/esummary.fcgi", params=esummary_params
                )
                resp.raise_for_status()
                summary_data = resp.json()

            results: list[SearchResult] = []
            result_map = summary_data.get("result", {})

            for pmid in pmids:
                doc = result_map.get(pmid, {})
                if not doc or doc == pmid:
                    continue

                title = doc.get("title", "")
                pub_date = doc.get("pubdate", "")
                year = (
                    int(pub_date[:4])
                    if pub_date and pub_date[:4].isdigit()
                    else None
                )
                authors = [
                    a.get("name", "") for a in doc.get("authors", [])
                ]

                doi: str | None = None
                for aid in doc.get("articleids", []):
                    if aid.get("idtype") == "doi":
                        doi = aid.get("value")

                link = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"

                results.append(SearchResult(
                    title=title,
                    url=link,
                    snippet=doc.get("source", ""),
                    source_type=SourceType.ACADEMIC,
                    doi=doi,
                    authors=authors,
                    year=year,
                    citation_count=0,
                    backend_name=self.name,
                ))

            logger.info("PubMed: %d results for '%s'", len(results), query)
            return results

        except Exception as exc:
            logger.warning("PubMed search failed for '%s': %s", query, exc)
            raise

    async def healthcheck(self) -> bool:
        """Ping the PubMed API.

        Returns:
            True if esearch responds.
        """
        params = {
            "db": "pubmed",
            "term": "test",
            "retmax": 1,
            "retmode": "json",
        }
        try:
            async with httpx.AsyncClient(
                timeout=_TIMEOUT, follow_redirects=True
            ) as client:
                resp = await client.get(
                    f"{_EUTILS_BASE}/esearch.fcgi", params=params
                )
                return resp.status_code == 200
        except Exception:
            return False
