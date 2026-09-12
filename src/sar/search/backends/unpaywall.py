"""Unpaywall enrichment backend.

NOT a search backend --- enrichment only. Given a DOI, returns the
best open-access PDF URL. Used post-search to enrich results.

API docs: https://unpaywall.org/products/api
Migrated from: agentic_sar/academic_sources.py enrich_with_unpaywall()
"""

from __future__ import annotations

import logging
from urllib.parse import quote_plus

import aiohttp

from sar.contracts.search import SearchResult

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.unpaywall.org/v2"
_TIMEOUT = aiohttp.ClientTimeout(total=20)


class UnpaywallBackend:
    """Unpaywall DOI -> open-access PDF enrichment.

    This backend does NOT implement search(). It provides resolve_doi()
    for post-search PDF discovery.

    Attributes:
        name: "unpaywall"
        requires_key: False (but needs email for polite access).
        email: Email for API access.
    """

    name: str = "unpaywall"
    requires_key: bool = False

    def __init__(self, email: str = "researcher@example.com") -> None:
        """Initialize with email for API access.

        Args:
            email: Email required by Unpaywall API.
        """
        self._email = email

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        """Not implemented --- Unpaywall is enrichment-only.

        Args:
            query: Unused.
            limit: Unused.

        Returns:
            Empty list.
        """
        return []

    async def resolve_doi(self, doi: str) -> str | None:
        """Look up open-access PDF URL for a DOI.

        Queries the Unpaywall API to find the best open-access location
        for a given DOI. Returns the PDF URL if available.

        Unpaywall only indexes journal articles; book chapters, encyclopedia
        entries, and SSRN preprints with non-standard DOI prefixes return
        422 Unprocessable Entity. Those are skipped silently.

        Args:
            doi: Digital Object Identifier string.

        Returns:
            PDF URL if found, None otherwise.
        """
        if not doi:
            return None

        url = f"{_BASE_URL}/{quote_plus(doi)}"
        params = {"email": self._email}

        try:
            async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
                async with session.get(url, params=params) as resp:
                    # 404 = DOI not found; 422 = non-journal DOI
                    if resp.status in (404, 422):
                        logger.debug(
                            "Unpaywall: skipping DOI %s (HTTP %d)",
                            doi, resp.status,
                        )
                        return None
                    resp.raise_for_status()
                    data = await resp.json()

            best = data.get("best_oa_location")
            if best:
                return best.get("url_for_pdf") or best.get("url")
            return None

        except Exception as exc:
            logger.debug(
                "Unpaywall lookup failed for DOI %s: %s", doi, exc
            )
            return None

    async def healthcheck(self) -> bool:
        """Check if Unpaywall API is reachable.

        Returns:
            True if API responds for a known DOI.
        """
        try:
            result = await self.resolve_doi("10.1038/nature12373")
            # The DOI should exist; result may or may not have a PDF
            return True
        except Exception:
            return False
