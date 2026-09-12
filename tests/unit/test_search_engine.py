"""Tests for SearchEngine: registration, fan-out, deduplication."""

import pytest
from unittest.mock import AsyncMock

from sar.contracts.search import SearchRequest, SearchResponse, SearchResult, SourceType
from sar.search.engine import SearchEngine


# ---------------------------------------------------------------------------
# Mock backend helper
# ---------------------------------------------------------------------------


class FakeBackend:
    """Minimal async search backend for testing.

    Attributes:
        name: Backend identifier.
        requires_key: Always False.
    """

    def __init__(self, name: str, results: list[SearchResult] | None = None) -> None:
        self.name = name
        self.requires_key = False
        self._results = results or []

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        """Return canned results."""
        return self._results

    async def healthcheck(self) -> bool:
        """Always healthy."""
        return True


class FailingBackend:
    """Backend that always raises on search."""

    def __init__(self, name: str = "failing") -> None:
        self.name = name
        self.requires_key = False

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        """Always raises."""
        raise RuntimeError("Backend unavailable")

    async def healthcheck(self) -> bool:
        """Always unhealthy."""
        return False


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


class TestRegisterBackend:
    """Tests for backend registration."""

    def test_register_backend_when_new_name(self):
        """Registering a backend with a new name succeeds."""
        engine = SearchEngine()
        backend = FakeBackend("scholar")
        engine.register_backend(backend)
        assert len(engine._backends) == 1

    def test_register_backend_when_duplicate_name(self):
        """Registering a backend with a duplicate name raises ValueError."""
        engine = SearchEngine()
        engine.register_backend(FakeBackend("scholar"))
        with pytest.raises(ValueError, match="already registered"):
            engine.register_backend(FakeBackend("scholar"))

    def test_register_backend_when_multiple_unique(self):
        """Registering multiple uniquely-named backends succeeds."""
        engine = SearchEngine()
        engine.register_backend(FakeBackend("a"))
        engine.register_backend(FakeBackend("b"))
        engine.register_backend(FakeBackend("c"))
        assert len(engine._backends) == 3

    def test_init_with_backends_when_unique(self):
        """Passing backends to __init__ registers them all."""
        engine = SearchEngine(backends=[FakeBackend("a"), FakeBackend("b")])
        assert len(engine._backends) == 2

    def test_init_with_backends_when_duplicate(self):
        """Passing duplicate-named backends to __init__ raises ValueError."""
        with pytest.raises(ValueError):
            SearchEngine(backends=[FakeBackend("a"), FakeBackend("a")])


# ---------------------------------------------------------------------------
# Search: deduplication
# ---------------------------------------------------------------------------


class TestSearchDeduplication:
    """Tests for fan-out and deduplication."""

    @pytest.fixture
    def overlapping_results(self):
        """Two backends returning overlapping URLs."""
        shared_url = "https://example.com/paper1"
        unique_url_a = "https://example.com/paper2"
        unique_url_b = "https://example.com/paper3"

        results_a = [
            SearchResult(title="P1", url=shared_url, backend_name="a"),
            SearchResult(title="P2", url=unique_url_a, backend_name="a"),
        ]
        results_b = [
            SearchResult(title="P1 dup", url=shared_url, backend_name="b"),
            SearchResult(title="P3", url=unique_url_b, backend_name="b"),
        ]
        return results_a, results_b

    async def test_search_returns_deduped_results_when_overlapping_urls(
        self, overlapping_results
    ):
        """Two backends with overlapping URLs → deduped count is correct."""
        results_a, results_b = overlapping_results
        engine = SearchEngine(
            backends=[
                FakeBackend("a", results_a),
                FakeBackend("b", results_b),
            ]
        )
        request = SearchRequest(query="test")
        response = await engine.search(request)

        assert response.total_raw == 4
        assert response.total_deduped == 3
        assert len(response.results) == 3

    async def test_search_deduplicates_by_doi_when_same_doi(self):
        """Two results with the same DOI → only one kept."""
        doi = "10.1234/test"
        results_a = [
            SearchResult(
                title="P1",
                url="https://a.com/p1",
                doi=doi,
                snippet="short",
                backend_name="a",
            ),
        ]
        results_b = [
            SearchResult(
                title="P1 enriched",
                url="https://b.com/p1",
                doi=doi,
                snippet="a much longer abstract with more detail",
                authors=["Alice", "Bob"],
                backend_name="b",
            ),
        ]
        engine = SearchEngine(
            backends=[
                FakeBackend("a", results_a),
                FakeBackend("b", results_b),
            ]
        )
        request = SearchRequest(query="test")
        response = await engine.search(request)

        assert response.total_raw == 2
        assert response.total_deduped == 1
        # The kept result should be enriched with the longer snippet
        kept = response.results[0]
        assert len(kept.snippet) > len("short")

    async def test_search_deduplication_enriches_metadata_when_better_available(self):
        """DOI dedup enriches the primary with better metadata from secondary."""
        doi = "10.5555/enrich"
        r1 = SearchResult(
            title="P",
            url="https://a.com/p",
            doi=doi,
            authors=[],
            citation_count=None,
            backend_name="a",
        )
        r2 = SearchResult(
            title="P",
            url="https://b.com/p",
            doi=doi,
            authors=["Alice"],
            citation_count=99,
            year=2023,
            backend_name="b",
        )
        engine = SearchEngine(backends=[FakeBackend("a", [r1]), FakeBackend("b", [r2])])
        response = await engine.search(SearchRequest(query="test"))
        kept = response.results[0]
        assert kept.authors == ["Alice"]
        assert kept.citation_count == 99
        assert kept.year == 2023


# ---------------------------------------------------------------------------
# Search: error handling
# ---------------------------------------------------------------------------


class TestSearchErrorHandling:
    """Tests for graceful backend failure."""

    async def test_search_when_backend_fails(self):
        """One failing backend doesn't abort — results from healthy backend returned."""
        good_results = [
            SearchResult(title="Good", url="https://good.com", backend_name="good"),
        ]
        engine = SearchEngine(
            backends=[
                FakeBackend("good", good_results),
                FailingBackend("bad"),
            ]
        )
        request = SearchRequest(query="test")
        response = await engine.search(request)

        assert len(response.results) == 1
        assert response.results[0].title == "Good"
        assert "bad" in response.backends_failed
        assert "good" in response.backends_queried

    async def test_search_when_all_backends_fail(self):
        """All backends failing returns empty results with all in backends_failed."""
        engine = SearchEngine(
            backends=[FailingBackend("a"), FailingBackend("b")]
        )
        response = await engine.search(SearchRequest(query="test"))
        assert response.results == []
        assert set(response.backends_failed) == {"a", "b"}

    async def test_search_when_no_backends_registered(self):
        """Searching with no backends raises RuntimeError."""
        engine = SearchEngine()
        with pytest.raises(RuntimeError, match="no registered backends"):
            await engine.search(SearchRequest(query="test"))


# ---------------------------------------------------------------------------
# Search: academic_only filter
# ---------------------------------------------------------------------------


class TestSearchAcademicFilter:
    """Tests for academic_only filtering."""

    async def test_search_academic_only_when_mixed_sources(self):
        """Non-academic results excluded when academic_only=True."""
        results = [
            SearchResult(
                title="Academic",
                url="https://a.com/academic",
                source_type=SourceType.ACADEMIC,
            ),
            SearchResult(
                title="Web",
                url="https://a.com/web",
                source_type=SourceType.WEB,
            ),
            SearchResult(
                title="Preprint",
                url="https://a.com/preprint",
                source_type=SourceType.PREPRINT,
            ),
            SearchResult(
                title="Wiki",
                url="https://a.com/wiki",
                source_type=SourceType.WIKI,
            ),
        ]
        engine = SearchEngine(backends=[FakeBackend("test", results)])
        request = SearchRequest(query="test", academic_only=True)
        response = await engine.search(request)

        # Only ACADEMIC and PREPRINT should survive
        assert len(response.results) == 2
        types = {r.source_type for r in response.results}
        assert types == {SourceType.ACADEMIC, SourceType.PREPRINT}

    async def test_search_academic_only_false_when_all_kept(self):
        """All sources kept when academic_only=False (default)."""
        results = [
            SearchResult(title="A", url="https://a.com", source_type=SourceType.WEB),
            SearchResult(title="B", url="https://b.com", source_type=SourceType.WIKI),
        ]
        engine = SearchEngine(backends=[FakeBackend("test", results)])
        response = await engine.search(SearchRequest(query="test"))
        assert len(response.results) == 2


# ---------------------------------------------------------------------------
# Search: backend filter
# ---------------------------------------------------------------------------


class TestSearchBackendFilter:
    """Tests for backend selection via request.backends."""

    async def test_search_filters_backends_when_specified(self):
        """Only backends listed in request.backends are queried."""
        results_a = [SearchResult(title="A", url="https://a.com", backend_name="a")]
        results_b = [SearchResult(title="B", url="https://b.com", backend_name="b")]
        engine = SearchEngine(
            backends=[FakeBackend("a", results_a), FakeBackend("b", results_b)]
        )
        response = await engine.search(SearchRequest(query="test", backends=["a"]))
        assert len(response.results) == 1
        assert response.results[0].backend_name == "a"
        assert "a" in response.backends_queried
        assert "b" not in response.backends_queried

    async def test_search_when_empty_results(self):
        """Search with no results from any backend returns valid empty response."""
        engine = SearchEngine(backends=[FakeBackend("empty", [])])
        response = await engine.search(SearchRequest(query="nothing"))
        assert response.results == []
        assert response.total_raw == 0
        assert response.total_deduped == 0
        assert response.query == "nothing"
