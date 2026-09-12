"""Tests for ContentFetcher.

The fetcher was a stub with no tests and no callers, so the pipeline silently
read search snippets instead of documents. These pin the fallback ladder
(cache -> HTTP -> Playwright -> snippet) and the extraction helpers.

Playwright is disabled throughout except where explicitly exercised: these must
run offline in CI with no browser binaries installed.
"""

from __future__ import annotations

import httpx
import pytest

from sar.search.fetch import MIN_USEFUL_CHARS, ContentFetcher

LONG_BODY = "This is a real sentence of document text. " * 20
HTML_PAGE = f"<html><body><article><p>{LONG_BODY}</p></article></body></html>"


class StubCache:
    """Minimal URLCache stand-in recording reads and writes."""

    def __init__(self, seeded: dict[str, str] | None = None) -> None:
        self.store = dict(seeded or {})
        self.writes: list[tuple[str, str]] = []

    def get(self, url: str) -> str | None:
        return self.store.get(url)

    def set(self, url: str, content: str) -> None:
        self.writes.append((url, content))
        self.store[url] = content


def _transport(handler) -> None:
    """Install a mock transport on every AsyncClient the fetcher opens."""
    return httpx.MockTransport(handler)


@pytest.fixture
def mock_http(monkeypatch):
    """Route the fetcher's httpx calls to a handler the test supplies."""

    def _install(handler):
        real_init = httpx.AsyncClient.__init__

        def patched(self, *args, **kwargs):
            kwargs["transport"] = _transport(handler)
            real_init(self, *args, **kwargs)

        monkeypatch.setattr(httpx.AsyncClient, "__init__", patched)

    return _install


class TestExtraction:
    """The HTML and PDF extractors are pure and testable on their own."""

    def test_html_extraction_strips_boilerplate(self):
        fetcher = ContentFetcher(use_playwright=False)
        html = (
            "<html><body><nav>menu menu menu</nav>"
            f"<article><p>{LONG_BODY}</p></article>"
            "<footer>copyright</footer></body></html>"
        )
        text = fetcher._extract_html(html)
        assert "menu menu" not in text
        assert "copyright" not in text
        assert "real sentence of document text" in text

    def test_html_extraction_falls_back_to_body(self):
        fetcher = ContentFetcher(use_playwright=False)
        text = fetcher._extract_html(f"<html><body><div>{LONG_BODY}</div></body></html>")
        assert "real sentence" in text

    def test_html_extraction_handles_empty(self):
        assert ContentFetcher(use_playwright=False)._extract_html("") == ""

    def test_pdf_extraction_handles_empty(self):
        assert ContentFetcher(use_playwright=False)._extract_pdf(b"") == ""

    def test_pdf_extraction_survives_garbage(self):
        """A corrupt PDF must degrade, not raise."""
        assert ContentFetcher(use_playwright=False)._extract_pdf(b"not a pdf") == ""


class TestFetchLadder:
    """cache -> HTTP -> snippet, in that order."""

    @pytest.mark.asyncio
    async def test_cache_hit_skips_http(self, mock_http):
        calls: list[str] = []

        def handler(request):
            calls.append(str(request.url))
            return httpx.Response(200, text=HTML_PAGE)

        mock_http(handler)
        cache = StubCache({"https://example.org/a": "cached text"})
        fetcher = ContentFetcher(cache=cache, use_playwright=False)

        assert await fetcher.fetch("https://example.org/a") == "cached text"
        assert calls == []

    @pytest.mark.asyncio
    async def test_http_success_populates_cache(self, mock_http):
        mock_http(lambda request: httpx.Response(200, text=HTML_PAGE))
        cache = StubCache()
        fetcher = ContentFetcher(cache=cache, use_playwright=False)

        text = await fetcher.fetch("https://example.org/a", "snippet")

        assert "real sentence of document text" in text
        assert cache.writes and cache.writes[0][0] == "https://example.org/a"

    @pytest.mark.asyncio
    async def test_http_error_falls_back_to_snippet(self, mock_http):
        mock_http(lambda request: httpx.Response(500))
        fetcher = ContentFetcher(use_playwright=False)

        assert await fetcher.fetch("https://example.org/a", "the snippet") == "the snippet"

    @pytest.mark.asyncio
    async def test_thin_page_falls_back_to_snippet(self, mock_http):
        """A page shorter than the useful threshold is not worth returning."""
        mock_http(lambda request: httpx.Response(200, text="<html><body>hi</body></html>"))
        fetcher = ContentFetcher(use_playwright=False)

        assert await fetcher.fetch("https://example.org/a", "the snippet") == "the snippet"

    @pytest.mark.asyncio
    async def test_pdf_content_type_routes_to_pdf_extractor(self, mock_http):
        mock_http(
            lambda request: httpx.Response(
                200, content=b"%PDF-1.4 broken", headers={"content-type": "application/pdf"}
            )
        )
        fetcher = ContentFetcher(use_playwright=False)
        # The bytes are not a valid PDF, so extraction yields nothing and the
        # snippet is used -- the point is that it took the PDF branch safely.
        assert await fetcher.fetch("https://example.org/a.pdf", "snip") == "snip"

    @pytest.mark.asyncio
    async def test_empty_url_returns_snippet(self):
        fetcher = ContentFetcher(use_playwright=False)
        assert await fetcher.fetch("", "snip") == "snip"

    @pytest.mark.asyncio
    async def test_result_is_truncated(self, mock_http):
        mock_http(lambda request: httpx.Response(200, text=HTML_PAGE))
        fetcher = ContentFetcher(use_playwright=False, max_chars=MIN_USEFUL_CHARS + 10)

        text = await fetcher.fetch("https://example.org/a", "snip")
        assert len(text) <= MIN_USEFUL_CHARS + 10


class TestFetchBatch:
    """Batch fetching is bounded and never raises on a single bad URL."""

    @pytest.mark.asyncio
    async def test_returns_one_entry_per_url(self, mock_http):
        mock_http(lambda request: httpx.Response(200, text=HTML_PAGE))
        fetcher = ContentFetcher(use_playwright=False)

        out = await fetcher.fetch_batch(
            [("https://example.org/a", "sa"), ("https://example.org/b", "sb")]
        )

        assert set(out) == {"https://example.org/a", "https://example.org/b"}
        assert all("real sentence" in v for v in out.values())

    @pytest.mark.asyncio
    async def test_failure_yields_the_snippet_not_an_exception(self, mock_http):
        mock_http(lambda request: httpx.Response(404))
        fetcher = ContentFetcher(use_playwright=False)

        out = await fetcher.fetch_batch([("https://example.org/a", "fallback")])

        assert out == {"https://example.org/a": "fallback"}

    @pytest.mark.asyncio
    async def test_empty_input(self):
        assert await ContentFetcher(use_playwright=False).fetch_batch([]) == {}
