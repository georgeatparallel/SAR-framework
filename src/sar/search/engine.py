"""Search engine: backend registration, parallel fan-out, deduplication.

Coordinates multiple SearchBackend implementations, fans out queries
in parallel via asyncio.gather, deduplicates by URL and DOI, and
returns a unified SearchResponse.

Migrated from: agentic_sar/academic_sources.py federated_academic_search()
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from sar.contracts.search import SearchRequest, SearchResponse, SearchResult, SourceType
from sar.search.backends._protocol import SearchBackend

if TYPE_CHECKING:
    from sar.search.cache import URLCache
    from sar.search.fetch import ContentFetcher

logger = logging.getLogger(__name__)

#: Source types that count as "academic" for ``academic_only`` filtering.
_ACADEMIC_SOURCE_TYPES: frozenset[SourceType] = frozenset(
    {SourceType.ACADEMIC, SourceType.PREPRINT}
)


class SearchEngine:
    """Federated search coordinator.

    Registers backends at startup, fans out queries to all active backends
    in parallel, deduplicates results by URL and DOI, and optionally
    fetches full content for each result via a ``ContentFetcher``.

    Backends are queried with ``asyncio.gather``; individual failures are
    caught and logged without aborting the overall search (fail-loud per
    backend, degrade-intentionally at the engine level).

    Attributes:
        _backends: Registered search backend instances.
        _fetcher: Optional content fetcher for URL → text extraction.
        _cache: Optional URL cache for avoiding redundant fetches.
    """

    def __init__(
        self,
        backends: list[SearchBackend] | None = None,
        fetcher: ContentFetcher | None = None,
        cache: URLCache | None = None,
    ) -> None:
        """Initialize the search engine.

        Args:
            backends: Initial list of search backend instances.  Backends
                are stored in insertion order; the first registered backend
                has priority in deduplication tie-breaking.  If ``None``,
                the engine starts with no backends.
            fetcher: Content fetcher instance used to retrieve full text
                for each result URL.  If ``None``, content fetching is
                skipped and results carry only the snippet from the backend.
            cache: URL cache instance passed to the fetcher.  If ``None``
                and ``fetcher`` is also ``None``, no caching occurs.

        Raises:
            ValueError: If ``backends`` contains two entries with the same
                ``name``.
        """
        self._backends: list[SearchBackend] = []
        self._fetcher: ContentFetcher | None = fetcher
        self._cache: URLCache | None = cache

        for backend in backends or []:
            self.register_backend(backend)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register_backend(self, backend: SearchBackend) -> None:
        """Add a search backend to the engine.

        Args:
            backend: Backend instance implementing the ``SearchBackend``
                protocol.

        Raises:
            ValueError: If a backend with the same ``name`` is already
                registered.
        """
        for existing in self._backends:
            if existing.name == backend.name:
                raise ValueError(
                    f"A backend named '{backend.name}' is already registered. "
                    "Use a unique name or deregister the existing backend first."
                )
        self._backends.append(backend)
        logger.debug("Registered search backend: %s", backend.name)

    async def search(self, request: SearchRequest) -> SearchResponse:
        """Execute a federated search across all active backends.

        Selects backends (honouring ``request.backends`` filter), fans
        out the query in parallel, deduplicates results by URL and DOI,
        optionally applies ``academic_only`` filtering, and returns a
        ``SearchResponse`` with full provenance statistics.

        Args:
            request: Search parameters including query string, optional
                backend filter, per-backend result limit, and
                ``academic_only`` flag.

        Returns:
            Aggregated, deduplicated ``SearchResponse``.

        Raises:
            RuntimeError: If no backends are registered.
        """
        if not self._backends:
            raise RuntimeError(
                "SearchEngine has no registered backends. "
                "Call register_backend() before search()."
            )

        start = time.monotonic()

        # Select which backends to query.
        if request.backends is not None:
            requested_names: set[str] = set(request.backends)
            active_backends = [b for b in self._backends if b.name in requested_names]
            unknown = requested_names - {b.name for b in self._backends}
            if unknown:
                logger.warning(
                    "Requested backends not registered: %s", ", ".join(sorted(unknown))
                )
        else:
            active_backends = list(self._backends)

        if not active_backends:
            logger.warning("No active backends after filtering; returning empty response.")
            return SearchResponse(
                results=[],
                query=request.query,
                backends_queried=[],
                backends_failed=[],
                total_raw=0,
                total_deduped=0,
                elapsed_seconds=time.monotonic() - start,
            )

        # Fan out and collect raw results together with per-backend errors.
        all_results, failed_backends = await self._fan_out_with_tracking(
            request.query, active_backends, request.limit
        )

        total_raw = len(all_results)

        # Deduplicate.
        deduped = self._deduplicate(all_results)

        # Apply academic_only filter.
        if request.academic_only:
            deduped = [r for r in deduped if r.source_type in _ACADEMIC_SOURCE_TYPES]

        elapsed = time.monotonic() - start
        queried_names = [b.name for b in active_backends if b.name not in failed_backends]

        logger.info(
            "Search complete: query=%r backends=%d raw=%d deduped=%d elapsed=%.2fs",
            request.query,
            len(active_backends),
            total_raw,
            len(deduped),
            elapsed,
        )

        return SearchResponse(
            results=deduped,
            query=request.query,
            backends_queried=queried_names,
            backends_failed=list(failed_backends),
            total_raw=total_raw,
            total_deduped=len(deduped),
            elapsed_seconds=elapsed,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _fan_out_with_tracking(
        self,
        query: str,
        backends: list[SearchBackend],
        limit: int,
    ) -> tuple[list[SearchResult], set[str]]:
        """Fan out a query and track which backends failed.

        Args:
            query: Search query string.
            backends: Backends to query.
            limit: Max results per backend.

        Returns:
            A tuple of (all_results, failed_backend_names).
        """
        tasks = [self._safe_backend_call(b, query, limit) for b in backends]
        per_backend: list[list[SearchResult]] = await asyncio.gather(*tasks)

        all_results: list[SearchResult] = []
        failed: set[str] = set()

        for backend, results in zip(backends, per_backend):
            if results is None:
                # _safe_backend_call signals failure by returning None.
                failed.add(backend.name)
            else:
                all_results.extend(results)

        return all_results, failed

    async def _fan_out_single_query(
        self, query: str, backends: list[SearchBackend], limit: int
    ) -> list[SearchResult]:
        """Run one query against all backends in parallel.

        Individual backend failures are swallowed and logged; the
        overall gather always succeeds and returns whatever results
        the healthy backends produced.

        Args:
            query: Search query string.
            backends: Backends to query.
            limit: Max results per backend.

        Returns:
            Combined results from all responding backends (not yet
            deduplicated).
        """
        tasks = [self._safe_backend_call(b, query, limit) for b in backends]
        per_backend: list[list[SearchResult] | None] = await asyncio.gather(*tasks)

        combined: list[SearchResult] = []
        for results in per_backend:
            if results is not None:
                combined.extend(results)
        return combined

    async def _safe_backend_call(
        self, backend: SearchBackend, query: str, limit: int
    ) -> list[SearchResult] | None:
        """Call a backend and return its results, or None on failure.

        Catches all exceptions so that one failing backend never aborts
        the engine's fan-out.

        Args:
            backend: The backend to query.
            query: Search query string.
            limit: Max results to request.

        Returns:
            List of ``SearchResult`` objects, or ``None`` if the backend
            raised an exception.
        """
        try:
            results = await backend.search(query, limit=limit)
            logger.debug(
                "Backend %s returned %d results for query %r",
                backend.name,
                len(results),
                query,
            )
            return results
        except Exception as exc:
            logger.warning(
                "Backend %s failed for query %r: %s",
                backend.name,
                query,
                exc,
            )
            return None

    def _deduplicate(self, results: list[SearchResult]) -> list[SearchResult]:
        """Remove duplicate results by URL and DOI.

        Iteration order determines priority: the first occurrence of a
        URL or DOI is kept.  Backends registered earlier (or listed first
        in ``request.backends``) therefore have priority in tie-breaking,
        which should reflect the quality ordering of the backend list.

        When a result's DOI matches one already retained, the retained
        result is upgraded with any additional metadata the duplicate
        carries (longer snippet, higher citation count, more authors).

        Args:
            results: Raw combined results from all backends, in the order
                they were received (backend order is preserved).

        Returns:
            Deduplicated list in stable input order.
        """
        seen_urls: set[str] = set()
        seen_dois: set[str] = set()
        # Maps DOI → index in ``deduped`` for metadata enrichment.
        doi_index: dict[str, int] = {}
        deduped: list[SearchResult] = []

        for result in results:
            url = result.url.strip().rstrip("/")
            doi = result.doi.strip().lower() if result.doi else None

            # --- URL deduplication ---
            if url in seen_urls:
                # Exact URL duplicate; skip entirely.
                continue

            # --- DOI deduplication with enrichment ---
            if doi and doi in seen_dois:
                # Same paper from a different backend or URL variant.
                # Enrich the already-kept result with any better metadata.
                existing_idx = doi_index[doi]
                existing = deduped[existing_idx]
                deduped[existing_idx] = self._enrich(existing, result)
                # Register the new URL so it is not processed again.
                seen_urls.add(url)
                continue

            # New result: add it.
            seen_urls.add(url)
            if doi:
                seen_dois.add(doi)
                doi_index[doi] = len(deduped)

            deduped.append(result)

        return deduped

    @staticmethod
    def _enrich(primary: SearchResult, secondary: SearchResult) -> SearchResult:
        """Return a copy of ``primary`` upgraded with better fields from ``secondary``.

        Fields updated: snippet (longer wins), citation_count (higher wins),
        authors (non-empty wins), year (non-None wins).

        Args:
            primary: The already-retained result.
            secondary: The duplicate that may carry richer metadata.

        Returns:
            A new ``SearchResult`` instance with the best metadata from both.
        """
        updates: dict[str, object] = {}

        if len(secondary.snippet) > len(primary.snippet):
            updates["snippet"] = secondary.snippet

        if primary.citation_count is None and secondary.citation_count is not None:
            updates["citation_count"] = secondary.citation_count
        elif (
            primary.citation_count is not None
            and secondary.citation_count is not None
            and secondary.citation_count > primary.citation_count
        ):
            updates["citation_count"] = secondary.citation_count

        if not primary.authors and secondary.authors:
            updates["authors"] = secondary.authors

        if primary.year is None and secondary.year is not None:
            updates["year"] = secondary.year

        if not updates:
            return primary

        return primary.model_copy(update=updates)
