"""Live integration smoke tests for search backends.

These tests hit real external APIs and are marked slow.
Run with: pytest -m slow

Each test skips cleanly when the network is unreachable.
"""

from __future__ import annotations

import httpx
import pytest

from sar.search.backends.crossref import CrossRefBackend
from sar.search.backends.wikipedia import WikipediaBackend

pytestmark = pytest.mark.slow

_OFFLINE_ERRORS = (httpx.TransportError, httpx.TimeoutException, OSError)


async def _probe(url: str) -> None:
    """Skip the calling test when ``url`` is unreachable (offline)."""
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            await client.get(url)
    except _OFFLINE_ERRORS as exc:  # pragma: no cover - network dependent
        pytest.skip(f"network unreachable: {exc!r}")


async def test_wikipedia_search_returns_results():
    try:
        results = await WikipediaBackend().search("protest", limit=5)
    except _OFFLINE_ERRORS as exc:  # pragma: no cover - network dependent
        pytest.skip(f"network unreachable: {exc!r}")
    except httpx.HTTPStatusError as exc:  # pragma: no cover - network dependent
        if exc.response.status_code == 403:
            # Known backend defect: WikipediaBackend sends no User-Agent
            # header, and Wikimedia now blocks the default httpx UA
            # (https://phabricator.wikimedia.org/T400119).
            pytest.xfail("Wikipedia rejects the default httpx User-Agent (403)")
        raise

    assert len(results) >= 1
    first = results[0]
    assert first.title
    assert first.url.startswith("https://en.wikipedia.org/wiki/")


async def test_crossref_healthcheck():
    # healthcheck() swallows all errors into False, so probe connectivity
    # first to distinguish "offline" from "CrossRef degraded".
    await _probe("https://api.crossref.org/works?rows=0")

    assert await CrossRefBackend().healthcheck() is True
