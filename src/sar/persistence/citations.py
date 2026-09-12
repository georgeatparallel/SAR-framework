"""Citation management: storage and multi-format output.

Handles citation metadata storage and formatting to APA 7th edition,
BibTeX, and JSON. Provides both standalone formatting functions and
a CitationStore for collecting citations across a research session.

Migrated from: agentic_sar/citation_service.py
"""

from __future__ import annotations

import dataclasses
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from sar.contracts.search import SearchResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class Citation:
    """A single bibliographic citation.

    Attributes:
        url: Source URL.
        title: Document title.
        authors: List of author names.
        year: Publication year.
        doi: Digital Object Identifier.
        journal: Journal or publication name.
        publisher: Publisher name.
        citation_count: Number of citations.
        is_peer_reviewed: Whether peer-reviewed.
        pdf_url: Open-access PDF URL.
        source: Which search backend found this.
    """

    url: str = ""
    title: str = ""
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    doi: str = ""
    journal: str = ""
    publisher: str = ""
    citation_count: int = 0
    is_peer_reviewed: bool = False
    pdf_url: str = ""
    source: str = ""


# ---------------------------------------------------------------------------
# Construction helpers
# ---------------------------------------------------------------------------


def from_search_result(result: SearchResult) -> Citation:
    """Construct a Citation from a SearchResult contract object.

    Maps the SearchResult fields to Citation fields, handling missing
    values gracefully.

    Args:
        result: Search result with title, url, authors, etc.

    Returns:
        Populated Citation object.
    """
    return Citation(
        url=result.url,
        title=result.title,
        authors=list(result.authors) if result.authors else [],
        year=result.year,
        doi=result.doi or "",
        journal="",
        publisher="",
        citation_count=result.citation_count or 0,
        is_peer_reviewed=(result.source_type.value == "academic"),
        pdf_url="",
        source=result.backend_name,
    )


# ---------------------------------------------------------------------------
# APA 7th edition formatting
# ---------------------------------------------------------------------------


def to_apa(citation: Citation) -> str:
    """Format a citation as APA 7th edition string.

    Pattern:
        Author, F., & Author, F. (Year). Title. *Journal*. https://doi.org/...

    Missing fields are omitted gracefully.

    Args:
        citation: The citation to format.

    Returns:
        APA-formatted citation string.
    """
    parts: list[str] = []

    # Authors
    if citation.authors:
        formatted_authors = _format_authors_apa(citation.authors)
        parts.append(formatted_authors)

    # Year
    year_str = f"({citation.year})" if citation.year else "(n.d.)"
    parts.append(year_str)

    # Title
    if citation.title:
        parts.append(f"{citation.title.rstrip('.')}.")

    # Journal / publisher
    if citation.journal:
        parts.append(f"*{citation.journal}*.")
    elif citation.publisher:
        parts.append(f"{citation.publisher}.")

    # DOI or URL
    if citation.doi:
        doi_clean = citation.doi
        # Strip common prefixes to normalize
        for prefix in ("https://doi.org/", "http://dx.doi.org/"):
            if doi_clean.startswith(prefix):
                doi_clean = doi_clean[len(prefix):]
        parts.append(f"https://doi.org/{doi_clean}")
    elif citation.url:
        parts.append(citation.url)

    return " ".join(parts)


def _format_authors_apa(authors: list[str]) -> str:
    """Format a list of author name strings into APA author list notation.

    Attempts to detect ``First Last`` order and convert to ``Last, F.``
    Handles up to 20 authors (APA 7 rule: list all up to 20, then
    first 19 + ellipsis + last).

    Args:
        authors: Raw author name strings.

    Returns:
        APA-formatted author string.
    """
    max_authors = 20
    formatted: list[str] = []

    for name in authors[:max_authors]:
        name = name.strip()
        if not name:
            continue
        # Already in "Last, First" format?
        if "," in name:
            formatted.append(name)
            continue
        # Attempt "First [Middle] Last" -> "Last, F. [M.]"
        tokens = name.split()
        if len(tokens) >= 2:
            last = tokens[-1]
            initials = ". ".join(t[0].upper() for t in tokens[:-1] if t) + "."
            formatted.append(f"{last}, {initials}")
        else:
            formatted.append(name)

    if not formatted:
        return ""

    total = len(authors)
    if total > max_authors:
        # APA 7: first 19, then ellipsis, then last author
        subset = formatted[:19]
        last_author = authors[total - 1].strip()
        return ", ".join(subset) + ", . . . " + last_author
    elif len(formatted) == 1:
        return formatted[0]
    elif len(formatted) == 2:
        return f"{formatted[0]}, & {formatted[1]}"
    else:
        return ", ".join(formatted[:-1]) + f", & {formatted[-1]}"


# ---------------------------------------------------------------------------
# BibTeX formatting
# ---------------------------------------------------------------------------


def to_bibtex(citation: Citation) -> str:
    """Format a citation as BibTeX entry.

    Uses @article when a DOI or journal is present, @misc otherwise.
    The cite key is constructed as ``LastYYYY``.

    Args:
        citation: The citation to format.

    Returns:
        BibTeX entry string.
    """
    entry_type = "article" if (citation.doi or citation.journal) else "misc"
    key = _make_bibtex_key(citation.authors, citation.year)

    fields: list[tuple[str, str]] = []

    if citation.title:
        fields.append(("title", f"{{{citation.title}}}"))

    if citation.authors:
        bibtex_authors = " and ".join(citation.authors)
        fields.append(("author", f"{{{bibtex_authors}}}"))

    if citation.year:
        fields.append(("year", f"{{{citation.year}}}"))

    if citation.journal:
        fields.append(("journal", f"{{{citation.journal}}}"))

    if citation.publisher:
        fields.append(("publisher", f"{{{citation.publisher}}}"))

    if citation.doi:
        doi_clean = citation.doi
        for prefix in ("https://doi.org/", "http://dx.doi.org/"):
            if doi_clean.startswith(prefix):
                doi_clean = doi_clean[len(prefix):]
        fields.append(("doi", f"{{{doi_clean}}}"))

    if citation.url:
        fields.append(("url", f"{{{citation.url}}}"))

    if citation.citation_count:
        fields.append(("note", f"{{Cited by {citation.citation_count}}}"))

    field_lines = ",\n  ".join(f"{k} = {v}" for k, v in fields)
    return f"@{entry_type}{{{key},\n  {field_lines}\n}}"


def _make_bibtex_key(authors: list[str], year: int | None) -> str:
    """Generate a BibTeX cite key from the first author's last name + year.

    Args:
        authors: List of author name strings.
        year: Publication year.

    Returns:
        BibTeX cite key (e.g., "Smith2023").
    """
    if authors:
        first = authors[0].strip()
        if "," in first:
            last = first.split(",")[0].strip()
        else:
            tokens = first.split()
            last = tokens[-1] if tokens else "Unknown"
        # Strip non-ASCII / non-alpha for a clean key
        last = re.sub(r"[^A-Za-z]", "", last)
    else:
        last = "Unknown"

    year_str = str(year) if year else "nd"
    return f"{last}{year_str}"


# ---------------------------------------------------------------------------
# Dict serialization
# ---------------------------------------------------------------------------


def to_dict(citation: Citation) -> dict[str, Any]:
    """Convert a citation to a JSON-serializable dict.

    Args:
        citation: The citation to convert.

    Returns:
        Dict representation with all fields.
    """
    return dataclasses.asdict(citation)


# ---------------------------------------------------------------------------
# Bulk formatters
# ---------------------------------------------------------------------------


def citations_to_bibtex(citations: list[Citation]) -> str:
    """Format a list of citations as a complete BibTeX file.

    Args:
        citations: Citations to format.

    Returns:
        Concatenated BibTeX entries separated by blank lines.
    """
    return "\n\n".join(to_bibtex(c) for c in citations)


def citations_to_apa_list(citations: list[Citation]) -> list[str]:
    """Format a list of citations as APA strings.

    Args:
        citations: Citations to format.

    Returns:
        List of APA-formatted strings.
    """
    return [to_apa(c) for c in citations]


# ---------------------------------------------------------------------------
# CitationStore — session-level collection
# ---------------------------------------------------------------------------


class CitationStore:
    """In-memory collection of citations for a research session.

    Maintains unique citations (deduped by URL/DOI) and provides
    bulk formatting in APA or BibTeX format.
    """

    def __init__(self) -> None:
        """Initialize an empty citation store."""
        self._citations: dict[str, Citation] = {}  # dedup key -> Citation

    def add(self, citation: Citation) -> str:
        """Add a citation. Returns the deduplication key.

        Deduplicates by DOI (preferred) or URL.

        Args:
            citation: Citation to add.

        Returns:
            Deduplication key used for storage.
        """
        key = citation.doi or citation.url or citation.title
        self._citations[key] = citation
        return key

    def add_from_search_result(self, result: SearchResult) -> str:
        """Add a citation constructed from a SearchResult.

        Args:
            result: Search result to convert and store.

        Returns:
            Deduplication key used for storage.
        """
        citation = from_search_result(result)
        return self.add(citation)

    def add_many(self, citations: list[Citation]) -> list[str]:
        """Add multiple citations. Returns list of keys.

        Args:
            citations: Citations to add.

        Returns:
            List of deduplication keys.
        """
        return [self.add(c) for c in citations]

    def format_all_apa(self) -> str:
        """Format all citations as an APA reference list.

        Returns:
            Alphabetically sorted APA entries, separated by blank lines.
        """
        entries = citations_to_apa_list(list(self._citations.values()))
        entries.sort()  # APA: alphabetical by first author
        return "\n\n".join(entries)

    def format_all_bibtex(self) -> str:
        """Format all citations as BibTeX entries.

        Returns:
            Concatenated BibTeX entries separated by blank lines.
        """
        return citations_to_bibtex(list(self._citations.values()))

    def to_dicts(self) -> list[dict[str, Any]]:
        """Export all citations as a list of dicts.

        Returns:
            List of JSON-serializable citation dicts.
        """
        return [to_dict(c) for c in self._citations.values()]

    @property
    def count(self) -> int:
        """Number of unique citations in the store."""
        return len(self._citations)
