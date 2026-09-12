"""Tests for SemanticScholarBackend using httpx.MockTransport.

The backend constructs its own ``httpx.AsyncClient`` inside search()
and healthcheck(), so these tests monkeypatch ``httpx.AsyncClient``
with a factory that builds a client on a MockTransport handler.

Coded behavior pinned here: search() logs and RE-RAISES on failure
(it does not return []); healthcheck() returns False on any error.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from sar.contracts.search import SearchResult, SourceType
from sar.search.backends.semantic_scholar import SemanticScholarBackend

# Keep a reference to the real class before any monkeypatching.
_REAL_ASYNC_CLIENT = httpx.AsyncClient


def install_transport(monkeypatch, handler) -> None:
    """Replace httpx.AsyncClient with a factory bound to a MockTransport."""
    transport = httpx.MockTransport(handler)

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return _REAL_ASYNC_CLIENT(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)


PAYLOAD = {
    "total": 2,
    "data": [
        {
            "paperId": "abc123",
            "title": "Networked Protest",
            "url": "https://www.semanticscholar.org/paper/abc123",
            "abstract": "A study of online mobilization. " * 30,
            "authors": [{"name": "Ada Lovelace"}, {"name": "Alan Turing"}],
            "year": 2021,
            "citationCount": 42,
            "isOpenAccess": True,
            "openAccessPdf": {"url": "https://example.org/networked.pdf"},
            "externalIds": {"DOI": "10.1000/xyz123"},
        },
        {
            # Minimal paper: no url, no abstract, no DOI, no authors.
            "paperId": "def456",
            "title": "Sparse Paper",
            "abstract": None,
            "authors": [],
            "year": None,
            "citationCount": 0,
            "externalIds": None,
            "openAccessPdf": None,
        },
    ],
}


class TestSearch:
    """search() maps the Graph API payload onto SearchResult."""

    async def test_returns_search_results(self, monkeypatch):
        captured: dict[str, httpx.Request] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["request"] = request
            return httpx.Response(200, json=PAYLOAD)

        install_transport(monkeypatch, handler)
        backend = SemanticScholarBackend()

        results = await backend.search("protest networks", limit=7)

        assert len(results) == 2
        assert all(isinstance(r, SearchResult) for r in results)

        # The request hit the paper/search endpoint with our params.
        request = captured["request"]
        assert request.url.path.endswith("/graph/v1/paper/search")
        assert request.url.params["query"] == "protest networks"
        assert request.url.params["limit"] == "7"
        assert "citationCount" in request.url.params["fields"]

    async def test_normalizes_fields(self, monkeypatch):
        install_transport(
            monkeypatch, lambda req: httpx.Response(200, json=PAYLOAD)
        )
        backend = SemanticScholarBackend()

        first, second = await backend.search("protest networks")

        assert first.title == "Networked Protest"
        assert first.url == "https://www.semanticscholar.org/paper/abc123"
        assert first.snippet.startswith("A study of online mobilization.")
        assert len(first.snippet) == 400  # abstract truncated
        assert first.source_type == SourceType.ACADEMIC
        assert first.doi == "10.1000/xyz123"
        assert first.authors == ["Ada Lovelace", "Alan Turing"]
        assert isinstance(first.year, int) and first.year == 2021
        assert first.citation_count == 42
        assert first.backend_name == "semantic_scholar"
        assert first.raw_metadata == {"pdf_url": "https://example.org/networked.pdf"}

        # Sparse paper: URL falls back to the paperId permalink.
        assert second.url == "https://www.semanticscholar.org/paper/def456"
        assert second.snippet == ""
        assert second.doi is None
        assert second.authors == []
        assert second.year is None
        assert second.citation_count == 0
        assert second.raw_metadata == {}

    async def test_empty_api_response(self, monkeypatch):
        install_transport(
            monkeypatch,
            lambda req: httpx.Response(200, json={"total": 0, "data": []}),
        )
        backend = SemanticScholarBackend()

        assert await backend.search("nothing to see") == []

    async def test_missing_data_key(self, monkeypatch):
        install_transport(
            monkeypatch, lambda req: httpx.Response(200, json={})
        )
        backend = SemanticScholarBackend()

        assert await backend.search("still nothing") == []

    async def test_http_error_reraises(self, monkeypatch):
        # As coded, search() logs the failure and re-raises; it does not
        # swallow the error into an empty list.
        install_transport(
            monkeypatch,
            lambda req: httpx.Response(500, json={"error": "boom"}),
        )
        backend = SemanticScholarBackend()

        with pytest.raises(httpx.HTTPStatusError):
            await backend.search("protest networks")

    async def test_connection_error_reraises(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route to host", request=request)

        install_transport(monkeypatch, handler)
        backend = SemanticScholarBackend()

        with pytest.raises(httpx.ConnectError):
            await backend.search("protest networks")

    async def test_api_key_sent_as_header(self, monkeypatch):
        captured: dict[str, httpx.Request] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["request"] = request
            return httpx.Response(200, json={"data": []})

        install_transport(monkeypatch, handler)
        backend = SemanticScholarBackend(api_key="sekrit")

        await backend.search("protest networks")

        assert captured["request"].headers["x-api-key"] == "sekrit"


class TestHealthcheck:
    """healthcheck() maps status codes and errors onto a bool."""

    async def test_true_on_200(self, monkeypatch):
        install_transport(
            monkeypatch,
            lambda req: httpx.Response(200, json={"data": []}),
        )

        assert await SemanticScholarBackend().healthcheck() is True

    async def test_false_on_500(self, monkeypatch):
        install_transport(
            monkeypatch,
            lambda req: httpx.Response(500, text=json.dumps({"error": "down"})),
        )

        assert await SemanticScholarBackend().healthcheck() is False

    async def test_false_on_connection_error(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("offline", request=request)

        install_transport(monkeypatch, handler)

        assert await SemanticScholarBackend().healthcheck() is False
