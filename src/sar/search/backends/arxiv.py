"""arXiv search backend.

Uses the arXiv API (Atom XML format, free, no key required).
Returns preprints with title, authors, year, abstract, and PDF URL.

API docs: https://info.arxiv.org/help/api/index.html
Migrated from: agentic_sar/academic_sources.py search_arxiv()
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET

import httpx

from sar.contracts.search import SearchResult, SourceType

logger = logging.getLogger(__name__)

_BASE_URL = "http://export.arxiv.org/api/query"
_ATOM_NS = "http://www.w3.org/2005/Atom"
_ARXIV_NS = "http://arxiv.org/schemas/atom"
_TIMEOUT = httpx.Timeout(20.0)


class ArxivBackend:
    """arXiv Atom API backend.

    Attributes:
        name: "arxiv"
        requires_key: False
        base_url: API base URL.
    """

    name: str = "arxiv"
    requires_key: bool = False

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        """Search arXiv for papers matching the query.

        Args:
            query: Search query string.
            limit: Max results.

        Returns:
            Normalized SearchResult list.
        """
        params = {
            "search_query": f"all:{query}",
            "max_results": limit,
            "sortBy": "relevance",
        }

        try:
            async with httpx.AsyncClient(
                timeout=_TIMEOUT, follow_redirects=True
            ) as client:
                resp = await client.get(_BASE_URL, params=params)
                resp.raise_for_status()
                text = resp.text

            root = ET.fromstring(text)
            entries = root.findall(f"{{{_ATOM_NS}}}entry")
            results: list[SearchResult] = []

            for entry in entries:
                def _text(tag: str, entry=entry) -> str:
                    el = entry.find(f"{{{_ATOM_NS}}}{tag}")
                    return el.text.strip() if el is not None and el.text else ""

                arxiv_id = _text("id")
                title = _text("title").replace("\n", " ")
                abstract = _text("summary").replace("\n", " ")[:400]

                pub = _text("published")
                year = int(pub[:4]) if pub and len(pub) >= 4 else None

                authors = [
                    (a.find(f"{{{_ATOM_NS}}}name").text or "")
                    for a in entry.findall(f"{{{_ATOM_NS}}}author")
                    if a.find(f"{{{_ATOM_NS}}}name") is not None
                ]

                # PDF link
                pdf_url: str | None = None
                for link_el in entry.findall(f"{{{_ATOM_NS}}}link"):
                    if link_el.get("title") == "pdf":
                        pdf_url = link_el.get("href")

                # DOI (uses arxiv namespace)
                doi_el = entry.find(f"{{{_ARXIV_NS}}}doi")
                doi = (
                    doi_el.text.strip()
                    if doi_el is not None and doi_el.text
                    else None
                )

                results.append(SearchResult(
                    title=title,
                    url=arxiv_id,
                    snippet=abstract,
                    source_type=SourceType.PREPRINT,
                    doi=doi,
                    authors=authors,
                    year=year,
                    citation_count=0,
                    backend_name=self.name,
                    raw_metadata={"pdf_url": pdf_url} if pdf_url else {},
                ))

            logger.info("arXiv: %d results for '%s'", len(results), query)
            return results

        except Exception as exc:
            logger.warning("arXiv search failed for '%s': %s", query, exc)
            raise

    async def healthcheck(self) -> bool:
        """Ping the arXiv API.

        Returns:
            True if API responds.
        """
        params = {"search_query": "all:test", "max_results": 1}
        try:
            async with httpx.AsyncClient(
                timeout=_TIMEOUT, follow_redirects=True
            ) as client:
                resp = await client.get(_BASE_URL, params=params)
                return resp.status_code == 200
        except Exception:
            return False
