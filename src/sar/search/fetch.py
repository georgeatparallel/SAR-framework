"""Content fetcher: URL → extracted text.

Handles HTTP fetching, PDF extraction, and Playwright-based rendering
for JavaScript-heavy pages. Triple-fallback strategy: direct HTTP →
Playwright → snippet fallback.

Migrated from: agentic_sar/search_service.py fetch_and_parse_html_async(), fetch_and_parse_pdf()

The heavy parsers are optional extras (``[scrape]`` for BeautifulSoup and
Playwright, ``[pdf]`` for PyMuPDF), so every one of them is imported lazily
inside the method that needs it. A missing extra degrades that one strategy
rather than breaking the import of this module.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from sar.search.cache import URLCache

logger = logging.getLogger(__name__)

#: Sent on direct HTTP fetches. Several publishers reject the default httpx
#: agent outright (the same 403 the Wikipedia backend had to fix).
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

#: Tags whose text is chrome, never content.
_BOILERPLATE_TAGS = ("script", "style", "nav", "footer", "header", "aside", "form")

#: Containers that usually hold the main article body, best first.
_CONTENT_SELECTORS = ("article", "main", "[role=main]", "#content", ".content")

#: Below this, an extraction is treated as failed so the next strategy runs.
MIN_USEFUL_CHARS = 200


class ContentFetcher:
    """Extracts full text content from URLs.

    Attempts direct HTTP first, falls back to Playwright for JS-rendered
    pages, and uses snippet as last resort. Caches results via URLCache.

    Attributes:
        cache: URL cache for TTL-based content storage.
        playwright_timeout_ms: Timeout for Playwright page loads.
    """

    def __init__(
        self,
        cache: URLCache | None = None,
        playwright_timeout_ms: int = 20_000,
        http_timeout_s: float = 20.0,
        use_playwright: bool = True,
        max_chars: int = 100_000,
    ) -> None:
        """Initialize the content fetcher.

        Args:
            cache: URL cache instance. If None, no caching.
            playwright_timeout_ms: Playwright page load timeout in milliseconds.
            http_timeout_s: Timeout for the direct HTTP attempt, in seconds.
            use_playwright: Whether to try Playwright when HTTP yields too
                little text. Disabled in tests and in environments with no
                browser binaries installed.
            max_chars: Upper bound on returned text, so one enormous document
                cannot dominate a prompt or the cache.
        """
        self.cache = cache
        self.playwright_timeout_ms = playwright_timeout_ms
        self.http_timeout_s = http_timeout_s
        self.use_playwright = use_playwright
        self.max_chars = max_chars

    async def fetch(self, url: str, snippet_fallback: str = "") -> str:
        """Fetch and extract text content from a URL.

        Tries in order: cache hit → direct HTTP → Playwright → snippet.

        Args:
            url: The URL to fetch.
            snippet_fallback: Text to return if all fetch methods fail.

        Returns:
            Extracted text content (may be snippet if all else fails).
        """
        if not url:
            return snippet_fallback

        if self.cache is not None:
            try:
                cached = self.cache.get(url)
            except Exception as exc:  # pragma: no cover - cache is best-effort
                logger.warning("ContentFetcher: cache read failed for %s -- %s", url, exc)
                cached = None
            if cached:
                return cached

        text: str | None = None
        try:
            text = await self._fetch_http(url)
        except Exception as exc:
            logger.warning("ContentFetcher: HTTP fetch failed for %s -- %s", url, exc)

        if (not text or len(text) < MIN_USEFUL_CHARS) and self.use_playwright:
            try:
                rendered = await self._fetch_playwright(url)
            except Exception as exc:
                logger.warning(
                    "ContentFetcher: Playwright fetch failed for %s -- %s", url, exc
                )
                rendered = None
            if rendered and len(rendered) > len(text or ""):
                text = rendered

        if not text or len(text) < MIN_USEFUL_CHARS:
            logger.info("ContentFetcher: falling back to snippet for %s", url)
            return snippet_fallback

        text = text[: self.max_chars]
        if self.cache is not None:
            try:
                self.cache.set(url, text)
            except Exception as exc:  # pragma: no cover - cache is best-effort
                logger.warning("ContentFetcher: cache write failed for %s -- %s", url, exc)
        return text

    async def fetch_batch(
        self, urls: list[tuple[str, str]], concurrency: int = 10
    ) -> dict[str, str]:
        """Fetch content for multiple URLs concurrently.

        Args:
            urls: List of (url, snippet_fallback) pairs.
            concurrency: Maximum concurrent fetches.

        Returns:
            Mapping of URL → extracted content.
        """
        if not urls:
            return {}

        semaphore = asyncio.Semaphore(max(1, concurrency))

        async def _one(url: str, fallback: str) -> tuple[str, str]:
            async with semaphore:
                return url, await self.fetch(url, fallback)

        results = await asyncio.gather(
            *(_one(url, fallback) for url, fallback in urls),
            return_exceptions=True,
        )

        out: dict[str, str] = {}
        for (url, fallback), result in zip(urls, results, strict=True):
            if isinstance(result, BaseException):
                logger.warning("ContentFetcher: batch fetch failed for %s -- %s", url, result)
                out[url] = fallback
            else:
                out[result[0]] = result[1]
        return out

    async def _fetch_http(self, url: str) -> str | None:
        """Fetch via direct HTTP with browser-like headers.

        Args:
            url: Target URL.

        Returns:
            Extracted text, or None if fetch/parse failed.
        """
        async with httpx.AsyncClient(
            timeout=self.http_timeout_s,
            follow_redirects=True,
            headers=BROWSER_HEADERS,
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").lower()
            if "pdf" in content_type or url.lower().endswith(".pdf"):
                return self._extract_pdf(response.content)
            return self._extract_html(response.text)

    async def _fetch_playwright(self, url: str) -> str | None:
        """Fetch via headless Playwright browser.

        Used for JavaScript-rendered pages that return empty via HTTP.

        Args:
            url: Target URL.

        Returns:
            Extracted text, or None if fetch failed.
        """
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            logger.debug("ContentFetcher: playwright not installed; skipping render")
            return None

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                page = await browser.new_page(
                    user_agent=BROWSER_HEADERS["User-Agent"]
                )
                await page.goto(
                    url, timeout=self.playwright_timeout_ms, wait_until="domcontentloaded"
                )
                html = await page.content()
            finally:
                await browser.close()
        return self._extract_html(html)

    def _extract_html(self, html: str) -> str:
        """Extract main content from HTML, removing boilerplate.

        Uses semantic selectors (article, main, [role=main]) and
        negative hints (nav, footer, ads) to isolate content.

        Args:
            html: Raw HTML string.

        Returns:
            Cleaned text content.
        """
        if not html:
            return ""
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            logger.debug("ContentFetcher: beautifulsoup4 not installed; returning raw text")
            return ""

        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(list(_BOILERPLATE_TAGS)):
            tag.decompose()

        # Prefer a semantic container; fall back to whichever candidate carries
        # the most paragraph text, then to the whole document.
        best_text = ""
        for selector in _CONTENT_SELECTORS:
            for node in soup.select(selector):
                text = node.get_text(separator="\n", strip=True)
                if len(text) > len(best_text):
                    best_text = text
            if len(best_text) >= MIN_USEFUL_CHARS:
                break

        if len(best_text) < MIN_USEFUL_CHARS:
            body_text = soup.get_text(separator="\n", strip=True)
            if len(body_text) > len(best_text):
                best_text = body_text

        return "\n".join(line for line in best_text.splitlines() if line.strip())

    def _extract_pdf(self, pdf_bytes: bytes) -> str:
        """Extract text from PDF bytes.

        Args:
            pdf_bytes: Raw PDF file content.

        Returns:
            Extracted text from all pages.
        """
        if not pdf_bytes:
            return ""
        try:
            import fitz  # PyMuPDF
        except ImportError:
            logger.debug("ContentFetcher: PyMuPDF not installed; cannot read PDF")
            return ""

        try:
            with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
                pages = [page.get_text() for page in doc]
        except Exception as exc:
            logger.warning("ContentFetcher: PDF parse failed -- %s", exc)
            return ""
        return "\n".join(p for p in pages if p and p.strip())
