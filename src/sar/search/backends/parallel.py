"""Opt-in, keyless web search through Parallel's Streamable HTTP MCP server."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import date
from typing import TYPE_CHECKING

import httpx
from pydantic import BaseModel

from sar.contracts.search import SearchResult, SourceType

if TYPE_CHECKING:
    from mcp import ClientSession

_URL = "https://search.parallel.ai/mcp"
_USER_AGENT = "SAR-framework/2.0 (+https://github.com/ignaciourbina/SAR-framework)"
_TIMEOUT = 30.0


class _WebResult(BaseModel):
    url: str
    title: str | None = None
    excerpts: list[str]
    publish_date: str | None = None


class _SearchPayload(BaseModel):
    results: list[_WebResult]


class ParallelBackend:
    """Parallel web search, enabled only by explicit backend selection.

    Attributes:
        name: Backend identifier, ``parallel``.
        requires_key: False; the anonymous MCP endpoint needs no API key.
    """

    name: str = "parallel"
    requires_key: bool = False

    def __init__(self) -> None:
        """Keep one anonymous session identity for this backend's searches."""
        self._session_id = uuid.uuid4().hex

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[ClientSession]:
        try:
            from mcp import ClientSession
            from mcp.client.streamable_http import streamable_http_client
            from mcp.types import Implementation
        except ImportError as exc:
            raise RuntimeError(
                'Parallel requires the optional dependency: pip install "agentic-sar[parallel]"'
            ) from exc

        # Bound the whole exchange, including initialization and transport cleanup.
        # The SDK handles both JSON and SSE responses and propagates cancellation.
        async with (
            asyncio.timeout(_TIMEOUT),
            httpx.AsyncClient(
                headers={"User-Agent": _USER_AGENT}, timeout=_TIMEOUT,
            ) as client,
            streamable_http_client(_URL, http_client=client) as (read, write, _),
            ClientSession(
                read, write,
                client_info=Implementation(name="SAR-framework", version="2.0.0"),
            ) as session,
        ):
            await session.initialize()
            yield session

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        """Search for source URLs and excerpts using anonymous Fast mode.

        Args:
            query: Research question or keyword query.
            limit: Maximum results retained locally; nonpositive values skip the request.

        Returns:
            Web results with excerpts in the existing snippet contract.

        Raises:
            RuntimeError: If dependencies are missing or the MCP tool reports an error.
            ValueError: If the server returns an invalid search payload.
            TimeoutError: If the complete MCP exchange takes over 30 seconds.
        """
        if limit <= 0:
            return []
        async with self._session() as session:
            response = await session.call_tool("web_search", {
                "objective": query,
                "search_queries": [query],
                "session_id": self._session_id,
            })
        text = "\n".join(c.text for c in response.content if c.type == "text")
        if response.isError:
            raise RuntimeError(f"Parallel web_search failed: {text}")
        payload = _SearchPayload.model_validate(
            response.structuredContent
            if response.structuredContent is not None else json.loads(text)
        )

        results = []
        for item in payload.results[:limit]:
            year = None
            if item.publish_date:
                with suppress(ValueError):
                    year = date.fromisoformat(item.publish_date).year
            results.append(SearchResult(
                title=item.title or item.url,
                url=item.url,
                snippet="\n\n".join(item.excerpts),
                source_type=SourceType.WEB,
                year=year,
                backend_name=self.name,
            ))
        return results

    async def healthcheck(self) -> bool:
        """Check tool availability without spending a search request.

        Returns:
            True if the server advertises ``web_search``, otherwise False.
        """
        try:
            async with self._session() as session:
                tools = await session.list_tools()
                return any(tool.name == "web_search" for tool in tools.tools)
        except Exception:
            return False
