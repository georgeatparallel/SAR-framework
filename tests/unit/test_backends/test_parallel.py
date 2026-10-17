"""Exercise the opt-in CLI path over the real MCP SDK and a recorded HTTP boundary."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from mcp.client.streamable_http import streamable_http_client  # noqa: F401

from sar.cli.research import _build_search_engine
from sar.contracts.search import SearchRequest, SourceType
from sar.search.backends import ParallelBackend
from sar.search.backends import parallel as module

_CLIENT = httpx.AsyncClient
_TOOLS = {"tools": [{"name": "web_search", "inputSchema": {"type": "object"}}]}
_PAYLOAD = {"results": [
    {"url": "https://example.org/paper", "title": "Mobilization evidence",
     "excerpts": ["First supported passage.", "Second supported passage."],
     "publish_date": "2026-01-12"},
    {"url": "https://example.org/paper", "excerpts": ["Duplicate."]},
    {"url": "https://example.org/report", "title": None,
     "excerpts": [], "publish_date": "not a date"},
]}


def install_server(monkeypatch, *, payload=None, error=False, sse=False, call_handler=None):
    """Keep SDK serialization/transport intact and replace only HTTP I/O."""
    requests = []

    async def handler(request):
        requests.append(request)
        assert str(request.url) == "https://search.parallel.ai/mcp"
        assert request.headers["user-agent"] == module._USER_AGENT
        assert "authorization" not in request.headers
        assert "x-api-key" not in request.headers
        if request.method != "POST":
            return httpx.Response(405)
        message = json.loads(request.content)
        method = message["method"]
        if method == "notifications/initialized":
            return httpx.Response(202)
        if method == "initialize":
            result = {"protocolVersion": message["params"]["protocolVersion"],
                      "capabilities": {"tools": {}},
                      "serverInfo": {"name": "test-search", "version": "1"}}
        elif method == "tools/list":
            result = _TOOLS
        elif method == "tools/call":
            if call_handler:
                return await call_handler(request)
            data = _PAYLOAD if payload is None else payload
            result = {"content": [{"type": "text", "text": json.dumps(data)}],
                      "isError": error}
            if not error:
                result["structuredContent"] = data
        else:
            raise AssertionError(method)
        response = {"jsonrpc": "2.0", "id": message["id"], "result": result}
        if sse:
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  text=f"event: message\ndata: {json.dumps(response)}\n\n")
        return httpx.Response(200, json=response)

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return _CLIENT(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    return requests


@pytest.mark.parametrize("sse", [False, True])
async def test_cli_to_search_engine_normalizes_and_deduplicates(monkeypatch, sse):
    requests = install_server(monkeypatch, sse=sse)
    engine = _build_search_engine(["parallel"])
    response = await engine.search(SearchRequest(query="protest mobilization evidence", limit=3))
    assert response.backends_queried == ["parallel"]
    assert response.backends_failed == []
    assert response.total_raw == 3
    assert response.total_deduped == 2
    first, second = response.results
    assert first.title == "Mobilization evidence"
    assert first.url == "https://example.org/paper"
    assert first.snippet == "First supported passage.\n\nSecond supported passage."
    assert first.year == 2026
    assert first.source_type == SourceType.WEB
    assert first.backend_name == "parallel"
    assert second.title == second.url
    assert second.year is None

    await engine.search(SearchRequest(query="related research question", limit=1))
    messages = [json.loads(r.content) for r in requests if r.method == "POST"]
    calls = [m["params"] for m in messages if m["method"] == "tools/call"]
    assert calls[0]["name"] == "web_search"
    args = calls[0]["arguments"]
    assert args["objective"] == "protest mobilization evidence"
    assert args["search_queries"] == [args["objective"]]
    assert len(args["session_id"]) == 32
    assert calls[1]["arguments"]["session_id"] == args["session_id"]


async def test_local_limit_and_academic_filter(monkeypatch):
    install_server(monkeypatch)
    engine = _build_search_engine(["parallel"])
    response = await engine.search(SearchRequest(query="evidence", limit=1))
    assert len(response.results) == 1
    response = await engine.search(SearchRequest(query="evidence", academic_only=True))
    assert response.results == []
    assert response.backends_failed == []


async def test_zero_limit_does_not_connect(monkeypatch):
    requests = install_server(monkeypatch)
    assert await ParallelBackend().search("evidence", limit=0) == []
    assert requests == []


@pytest.mark.parametrize("payload", [{"results": []}, {"results": [], "warnings": []}])
async def test_empty_success(monkeypatch, payload):
    install_server(monkeypatch, payload=payload)
    response = await _build_search_engine(["parallel"]).search(SearchRequest(query="evidence"))
    assert response.results == []
    assert response.backends_queried == ["parallel"]
    assert response.backends_failed == []


@pytest.mark.parametrize("payload,error", [({}, False), ({"results": "bad"}, False),
                                           ({"error": "quota exceeded"}, True)])
async def test_invalid_or_tool_error_is_recorded_as_failure(monkeypatch, payload, error):
    install_server(monkeypatch, payload=payload, error=error)
    response = await _build_search_engine(["parallel"]).search(SearchRequest(query="evidence"))
    assert response.results == []
    assert response.backends_queried == []
    assert response.backends_failed == ["parallel"]


async def test_http_error_is_recorded_as_failure(monkeypatch):
    async def failed(request):
        return httpx.Response(503)

    install_server(monkeypatch, call_handler=failed)
    response = await _build_search_engine(["parallel"]).search(SearchRequest(query="evidence"))
    assert response.backends_failed == ["parallel"]


async def test_timeout_is_recorded_and_transport_closes(monkeypatch):
    stopped = asyncio.Event()

    async def pending(request):
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    install_server(monkeypatch, call_handler=pending)
    monkeypatch.setattr(module, "_TIMEOUT", 0.05)
    response = await _build_search_engine(["parallel"]).search(SearchRequest(query="evidence"))
    assert response.backends_failed == ["parallel"]
    assert stopped.is_set()


async def test_cancellation_propagates_and_transport_closes(monkeypatch):
    started, stopped = asyncio.Event(), asyncio.Event()

    async def pending(request):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    install_server(monkeypatch, call_handler=pending)
    task = asyncio.create_task(
        _build_search_engine(["parallel"]).search(SearchRequest(query="evidence"))
    )
    await asyncio.wait_for(started.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stopped.is_set()


async def test_healthcheck_discovers_without_search(monkeypatch):
    requests = install_server(monkeypatch)
    assert await ParallelBackend().healthcheck() is True
    assert not any(b'"tools/call"' in r.content for r in requests)


def test_default_and_explicit_backend_choices_are_unchanged():
    names = [b.name for b in _build_search_engine(None)._backends]
    assert names == ["duckduckgo", "wikipedia", "crossref", "semantic_scholar", "arxiv", "pubmed"]
    assert [b.name for b in _build_search_engine(["crossref"])._backends] == ["crossref"]
    assert [b.name for b in _build_search_engine(["crossref", "parallel"])._backends] == [
        "crossref", "parallel",
    ]
