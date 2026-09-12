"""Search backend implementations.

Each backend implements the SearchBackend protocol from _protocol.py.
Backends are registered with the SearchEngine at startup.

Available backends:
- semantic_scholar: Semantic Scholar Graph API (free, no key)
- arxiv: arXiv Atom API (free, no key)
- crossref: CrossRef REST API (free, polite pool with email)
- pubmed: PubMed E-utilities (free, no key)
- unpaywall: Unpaywall API (enrichment only, needs email)
- brave: Brave Search API (free tier, needs API key)
- duckduckgo: DuckDuckGo search via duckduckgo_search library (free, no key, fallback)
- wikipedia: Wikipedia API (free, no key)
"""

from __future__ import annotations

# SearchBackend is dependency-free, so it is imported eagerly. The concrete
# backends are loaded lazily via module __getattr__ below: importing this
# package must not require optional HTTP/scrape dependencies (test collection
# of orchestrator modules previously failed on exactly that).
from ._protocol import SearchBackend

_BACKENDS = {
    "ArxivBackend": "arxiv",
    "BraveBackend": "brave",
    "CrossRefBackend": "crossref",
    "DuckDuckGoBackend": "duckduckgo",
    "PubMedBackend": "pubmed",
    "SemanticScholarBackend": "semantic_scholar",
    "UnpaywallBackend": "unpaywall",
    "WikipediaBackend": "wikipedia",
}

__all__ = [
    "ArxivBackend",
    "BraveBackend",
    "CrossRefBackend",
    "DuckDuckGoBackend",
    "PubMedBackend",
    "SearchBackend",
    "SemanticScholarBackend",
    "UnpaywallBackend",
    "WikipediaBackend",
]


#: Registry key (the name users write in config or on the CLI) -> class name.
BACKEND_NAMES = {module: cls for cls, module in _BACKENDS.items()}


def get_backend(name: str):
    """Resolve a backend by its registry name, or None if unknown.

    Returns None rather than raising so that an unknown or uninstallable
    backend removes itself from the fan-out instead of aborting startup. The
    caller is expected to log the omission.
    """
    cls_name = BACKEND_NAMES.get(name)
    if cls_name is None:
        return None
    try:
        return __getattr__(cls_name)
    except Exception:
        return None


def __getattr__(name: str):
    if name in _BACKENDS:
        import importlib

        module = importlib.import_module(f".{_BACKENDS[name]}", __name__)
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(set(globals()) | set(_BACKENDS))
