"""Contract tests every HTTP search backend must satisfy.

Only `semantic_scholar` had a unit test; the other seven were exercised solely
through live network calls that CI never makes. These run offline against a
mock transport and pin the behaviour the engine depends on:

- the SearchBackend protocol surface (name, requires_key, healthcheck)
- a well-formed but empty response yields an empty list, not an exception
- transport and HTTP errors surface to the engine, which records the backend
  as failed rather than pretending it returned nothing
- every returned object validates as a SearchResult

Rate-limit handling is asserted at the engine level (see test_search_engine),
because retry and throttling live there rather than in each backend.
"""

from __future__ import annotations

import httpx
import pytest

from sar.contracts.search import SearchResult
from sar.search.backends import get_backend

#: (registry name, constructor kwargs) for every backend reachable offline.
HTTP_BACKENDS = [
    ("crossref", {}),
    ("arxiv", {}),
    ("pubmed", {}),
    ("wikipedia", {}),
    ("semantic_scholar", {}),
    ("brave", {"api_key": "test-key"}),
    ("unpaywall", {"email": "a@b.c"}),
]

#: Payloads that are structurally valid for each backend but contain no hits,
#: so the parser runs end to end and returns an empty list.
EMPTY_PAYLOADS = {
    "crossref": ('{"message": {"items": []}}', "application/json"),
    "arxiv": (
        '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"></feed>',
        "application/atom+xml",
    ),
    "pubmed": ('{"esearchresult": {"idlist": []}}', "application/json"),
    "wikipedia": ('{"query": {"search": []}}', "application/json"),
    "semantic_scholar": ('{"data": []}', "application/json"),
    "brave": ('{"web": {"results": []}}', "application/json"),
    "unpaywall": ('{"results": []}', "application/json"),
}


def _make(name: str, monkeypatch, handler):
    """Build a backend whose httpx calls are routed to `handler`."""
    real_init = httpx.AsyncClient.__init__

    def patched(self, *args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched)
    cls = get_backend(name)
    assert cls is not None, f"backend {name} did not resolve"
    return cls(**dict(HTTP_BACKENDS)[name])


# Unpaywall's search() is a documented no-op (it is a DOI-enrichment service
# registered as a peer backend), so the request-path assertions skip it.


@pytest.mark.parametrize("name,kwargs", HTTP_BACKENDS)
class TestProtocolSurface:
    def test_has_a_name_and_key_flag(self, name, kwargs):
        backend = get_backend(name)(**kwargs)
        assert isinstance(backend.name, str) and backend.name
        assert isinstance(backend.requires_key, bool)
        assert backend.name == name

    @pytest.mark.asyncio
    async def test_empty_response_yields_empty_list(self, name, kwargs, monkeypatch):
        body, content_type = EMPTY_PAYLOADS[name]
        backend = _make(
            name,
            monkeypatch,
            lambda request: httpx.Response(
                200, text=body, headers={"content-type": content_type}
            ),
        )
        assert await backend.search("anything", limit=5) == []

    @pytest.mark.asyncio
    async def test_http_error_propagates_to_the_engine(self, name, kwargs, monkeypatch):
        """The engine records a failed backend; swallowing here would hide it."""
        if name == "unpaywall":
            pytest.skip("enrichment-only: search() never issues a request")
        backend = _make(name, monkeypatch, lambda request: httpx.Response(500))
        with pytest.raises((httpx.HTTPStatusError, ValueError, RuntimeError)):
            await backend.search("anything", limit=5)

    @pytest.mark.asyncio
    async def test_transport_error_propagates(self, name, kwargs, monkeypatch):
        if name == "unpaywall":
            pytest.skip("enrichment-only: search() never issues a request")

        def boom(request):
            raise httpx.ConnectError("no route to host")

        backend = _make(name, monkeypatch, boom)
        with pytest.raises((httpx.TransportError, ValueError, RuntimeError)):
            await backend.search("anything", limit=5)

    @pytest.mark.asyncio
    async def test_healthcheck_returns_a_bool_when_unreachable(
        self, name, kwargs, monkeypatch
    ):
        def boom(request):
            raise httpx.ConnectError("down")

        backend = _make(name, monkeypatch, boom)
        if name == "unpaywall":
            # resolve_doi() swallows failures and returns None, so healthcheck
            # reports True even when the service is unreachable. Pinned as
            # known behaviour rather than silently expected to be correct.
            assert await backend.healthcheck() is True
        else:
            assert await backend.healthcheck() is False


class TestResultShape:
    """A populated response must produce valid SearchResult objects."""

    @pytest.mark.asyncio
    async def test_crossref_parses_a_work(self, monkeypatch):
        payload = """
        {"message": {"items": [
          {"title": ["A Study of Things"],
           "URL": "https://doi.org/10.1/abc",
           "DOI": "10.1/abc",
           "abstract": "<jats:p>We study things.</jats:p>",
           "published-print": {"date-parts": [[2021]]},
           "is-referenced-by-count": 12,
           "author": [{"given": "A", "family": "Smith"}]}
        ]}}
        """
        backend = _make(
            "crossref",
            monkeypatch,
            lambda request: httpx.Response(
                200, text=payload, headers={"content-type": "application/json"}
            ),
        )
        results = await backend.search("things", limit=5)
        assert len(results) == 1
        result = results[0]
        assert isinstance(result, SearchResult)
        assert result.title == "A Study of Things"
        assert result.doi == "10.1/abc"
        assert result.year == 2021
        # Backends never score relevance; the judge does.
        assert result.relevance_score == 0.0
        # And they never populate content -- that is the fetcher's job.
        assert result.content is None

    @pytest.mark.asyncio
    async def test_semantic_scholar_parses_a_paper(self, monkeypatch):
        payload = """
        {"data": [
          {"title": "Networked Protest",
           "url": "https://example.org/p",
           "abstract": "An abstract.",
           "year": 2019,
           "citationCount": 4,
           "authors": [{"name": "B Jones"}]}
        ]}
        """
        backend = _make(
            "semantic_scholar",
            monkeypatch,
            lambda request: httpx.Response(
                200, text=payload, headers={"content-type": "application/json"}
            ),
        )
        results = await backend.search("protest", limit=5)
        assert len(results) == 1
        assert results[0].title == "Networked Protest"
        assert results[0].year == 2019


class TestDuckDuckGo:
    """The one backend that is not HTTP-based: it wraps the ddgs package."""

    def test_registry_resolves_it(self):
        assert get_backend("duckduckgo") is not None

    @pytest.mark.asyncio
    async def test_missing_package_raises_so_the_engine_marks_it_failed(
        self, monkeypatch
    ):
        """Undeclared for a long time, this import failure silently removed
        web and press coverage from every run."""
        import builtins

        real_import = builtins.__import__

        def no_ddgs(name, *args, **kwargs):
            if name in {"ddgs", "duckduckgo_search"}:
                raise ImportError(f"no module named {name}")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", no_ddgs)
        backend = get_backend("duckduckgo")()
        with pytest.raises(ImportError):
            await backend.search("anything", limit=3)


class TestUnpaywallIsEnrichmentOnly:
    """Registered as a peer backend but contributes no search results."""

    @pytest.mark.asyncio
    async def test_search_always_returns_empty(self):
        backend = get_backend("unpaywall")(email="a@b.c")
        assert await backend.search("anything", limit=10) == []
