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

from .arxiv import ArxivBackend
from .brave import BraveBackend
from .crossref import CrossRefBackend
from .duckduckgo import DuckDuckGoBackend
from .pubmed import PubMedBackend
from .semantic_scholar import SemanticScholarBackend
from .unpaywall import UnpaywallBackend
from .wikipedia import WikipediaBackend

__all__ = [
    "ArxivBackend",
    "BraveBackend",
    "CrossRefBackend",
    "DuckDuckGoBackend",
    "PubMedBackend",
    "SemanticScholarBackend",
    "UnpaywallBackend",
    "WikipediaBackend",
]
