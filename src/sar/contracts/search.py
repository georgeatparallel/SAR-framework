"""Search domain contracts.

Defines the data shapes for search requests, individual results,
and aggregated responses. Used by search backends, search engine,
and agents that consume search output.

Migrated from: bare dict[str, Any] in agentic_sar/academic_sources.py
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class SourceType(str, Enum):
    """Categorizes the provenance of a search result.

    Attributes:
        ACADEMIC: Peer-reviewed journal or conference paper.
        WEB: General web page (news, blog, documentation).
        WIKI: Wikipedia or wiki-family page.
        PREPRINT: Non-peer-reviewed preprint (arXiv, SSRN, etc.).
        GREY_LIT: Grey literature (NGO reports, government docs, working papers).
        UNKNOWN: Source type could not be determined.
    """

    ACADEMIC = "academic"
    WEB = "web"
    WIKI = "wiki"
    PREPRINT = "preprint"
    GREY_LIT = "grey_lit"
    UNKNOWN = "unknown"


class SearchResult(BaseModel):
    """A single search result from any backend.

    This is the universal schema -- every backend normalizes its raw
    API response into this shape before returning.

    Attributes:
        title: Title of the document or page.
        url: Canonical URL (used for deduplication).
        snippet: Short text excerpt or abstract.
        source_type: Category of the source (academic, web, etc.).
        doi: Digital Object Identifier, if available.
        authors: List of author names.
        year: Publication year, if available.
        citation_count: Number of citations, if known.
        relevance_score: Backend-assigned relevance in [0, 1].
        backend_name: Name of the search backend that produced this result.
        raw_metadata: Unprocessed backend-specific metadata for debugging.
    """

    title: str
    url: str
    snippet: str = ""
    source_type: SourceType = SourceType.UNKNOWN
    doi: str | None = None
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    citation_count: int | None = None
    relevance_score: float = Field(default=0.0, ge=0.0, le=1.0)
    backend_name: str = ""
    raw_metadata: dict[str, object] = Field(default_factory=dict)


class SearchRequest(BaseModel):
    """Input to the SearchEngine.

    Specifies the query string, which backends to query, result limit,
    and whether to restrict to academic sources only.

    Attributes:
        query: The search query string.
        backends: Which backends to use (None means all available).
        limit: Maximum number of results to return per backend.
        academic_only: If True, only return academic-type results.
    """

    query: str
    backends: list[str] | None = None
    limit: int = 10
    academic_only: bool = False


class SearchResponse(BaseModel):
    """Output from the SearchEngine.

    Aggregates deduplicated results from one or more backends, along
    with metadata about which backends were queried and any failures.

    Attributes:
        results: Deduplicated, merged results from all backends.
        query: The original query string.
        backends_queried: Names of backends that were successfully queried.
        backends_failed: Names of backends that returned errors.
        total_raw: Total number of results before deduplication.
        total_deduped: Number of results after deduplication.
        elapsed_seconds: Wall-clock time for the full search operation.
    """

    results: list[SearchResult] = Field(default_factory=list)
    query: str = ""
    backends_queried: list[str] = Field(default_factory=list)
    backends_failed: list[str] = Field(default_factory=list)
    total_raw: int = 0
    total_deduped: int = 0
    elapsed_seconds: float = 0.0
