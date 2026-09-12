"""Tests for the citation layer now that it is wired to the pipeline.

`citations.py` was fully implemented and imported by nothing: SAR produced
reports with inline `[Source N]` markers and no bibliography, and had no way to
export one. These cover the conversion from search results, the two output
formats, and the runner helpers that put a reference list on the report.
"""

from __future__ import annotations

from sar.contracts.pipeline import PaperSummary
from sar.contracts.search import SearchResult, SourceType
from sar.orchestrator.runner import _append_references, _collect_citations
from sar.persistence.citations import (
    Citation,
    CitationStore,
    citations_to_bibtex,
    from_search_result,
    to_apa,
    to_bibtex,
)


def _result(**overrides) -> SearchResult:
    base = dict(
        title="Networked Protest",
        url="https://example.org/paper",
        authors=["Tufekci, Zeynep"],
        year=2017,
        doi="10.1/abc",
        citation_count=42,
        source_type=SourceType.ACADEMIC,
        backend_name="crossref",
    )
    base.update(overrides)
    return SearchResult(**base)


def _summary(result: SearchResult) -> PaperSummary:
    return PaperSummary(source=result, extraction={}, quality_score=1.0, notes="")


class TestConversion:
    def test_search_result_maps_onto_a_citation(self):
        citation = from_search_result(_result())
        assert citation.title == "Networked Protest"
        assert citation.doi == "10.1/abc"
        assert citation.year == 2017
        assert citation.citation_count == 42
        assert citation.source == "crossref"

    def test_academic_source_type_marks_peer_reviewed(self):
        assert from_search_result(_result()).is_peer_reviewed is True
        assert (
            from_search_result(_result(source_type=SourceType.WEB)).is_peer_reviewed
            is False
        )

    def test_missing_fields_are_tolerated(self):
        citation = from_search_result(
            SearchResult(title="Untitled", url="https://example.org/x")
        )
        assert citation.authors == []
        assert citation.year is None


class TestFormatting:
    def test_apa_carries_author_year_and_title(self):
        rendered = to_apa(from_search_result(_result()))
        assert "2017" in rendered
        assert "Networked Protest" in rendered

    def test_bibtex_is_a_well_formed_entry(self):
        rendered = to_bibtex(from_search_result(_result()))
        assert rendered.strip().startswith("@")
        assert rendered.count("{") >= 2
        assert rendered.rstrip().endswith("}")

    def test_bibtex_of_many_keeps_every_entry(self):
        citations = [
            from_search_result(_result(url="https://example.org/a", doi="10.1/a")),
            from_search_result(_result(url="https://example.org/b", doi="10.1/b")),
        ]
        assert citations_to_bibtex(citations).count("@") == 2


class TestStore:
    def test_deduplicates_by_doi(self):
        store = CitationStore()
        store.add_from_search_result(_result(url="https://a.example/x"))
        store.add_from_search_result(_result(url="https://b.example/y"))
        assert store.count == 1, "same DOI seen twice is one source"

    def test_distinct_sources_are_kept(self):
        store = CitationStore()
        store.add_from_search_result(_result(doi="10.1/a"))
        store.add_from_search_result(_result(doi="10.1/b"))
        assert store.count == 2

    def test_apa_list_is_alphabetical(self):
        store = CitationStore()
        store.add(Citation(title="B", url="https://b", authors=["Zeta, A"], year=2020))
        store.add(Citation(title="A", url="https://a", authors=["Alpha, B"], year=2019))
        rendered = store.format_all_apa()
        assert rendered.index("Alpha") < rendered.index("Zeta")


class TestRunnerHelpers:
    def test_collects_one_citation_per_source(self):
        store = _collect_citations(
            [_summary(_result(doi="10.1/a")), _summary(_result(doi="10.1/b"))]
        )
        assert store is not None
        assert store.count == 2

    def test_returns_none_when_there_is_nothing_to_cite(self):
        assert _collect_citations([]) is None

    def test_skips_sources_without_a_url(self):
        assert _collect_citations([_summary(SearchResult(title="T", url=""))]) is None

    def test_references_are_appended_to_the_report(self):
        store = _collect_citations([_summary(_result())])
        report = _append_references("# Findings\n\nSomething [Source 1].", store)
        assert report.startswith("# Findings")
        assert "## References" in report
        assert "Networked Protest" in report

    def test_report_is_untouched_when_there_are_no_citations(self):
        assert _append_references("# Findings", None) == "# Findings"

    def test_missing_report_stays_missing(self):
        """A failed run has no report; a bibliography must not invent one."""
        store = _collect_citations([_summary(_result())])
        assert _append_references(None, store) is None


class TestApaEdgeCases:
    """The APA formatter's branches: container, identifier, and author rules."""

    def test_journal_is_italicised_when_present(self):
        rendered = to_apa(Citation(title="T", journal="Am. Pol. Sci. Rev.", year=2020))
        assert "*Am. Pol. Sci. Rev.*" in rendered

    def test_publisher_is_used_when_there_is_no_journal(self):
        rendered = to_apa(Citation(title="T", publisher="Yale University Press"))
        assert "Yale University Press" in rendered
        assert "*" not in rendered

    def test_journal_wins_over_publisher(self):
        rendered = to_apa(Citation(title="T", journal="J", publisher="P"))
        assert "*J*" in rendered
        assert "P." not in rendered

    def test_doi_is_normalised_to_a_single_prefix(self):
        """A DOI that already carries a resolver prefix must not be doubled."""
        for raw in ("10.1/x", "https://doi.org/10.1/x", "http://dx.doi.org/10.1/x"):
            rendered = to_apa(Citation(title="T", doi=raw))
            assert rendered.count("https://doi.org/") == 1
            assert rendered.endswith("https://doi.org/10.1/x")

    def test_url_is_used_only_when_there_is_no_doi(self):
        assert "https://example.org/p" in to_apa(
            Citation(title="T", url="https://example.org/p")
        )
        assert "https://example.org/p" not in to_apa(
            Citation(title="T", url="https://example.org/p", doi="10.1/x")
        )

    def test_trailing_period_in_a_title_is_not_doubled(self):
        assert ".." not in to_apa(Citation(title="A sentence title."))


class TestApaAuthorFormatting:
    """APA name handling, which has a rule per author count."""

    def test_first_last_becomes_last_initial(self):
        assert "Tufekci, Z." in to_apa(Citation(title="T", authors=["Zeynep Tufekci"]))

    def test_already_inverted_names_are_left_alone(self):
        assert "Tufekci, Zeynep" in to_apa(Citation(title="T", authors=["Tufekci, Zeynep"]))

    def test_middle_names_become_initials(self):
        assert "Doe, J. Q." in to_apa(Citation(title="T", authors=["John Quincy Doe"]))

    def test_single_token_name_is_kept_verbatim(self):
        assert "Aristotle" in to_apa(Citation(title="T", authors=["Aristotle"]))

    def test_blank_author_entries_are_dropped(self):
        rendered = to_apa(Citation(title="T", authors=["   ", "Zeynep Tufekci"]))
        assert rendered.startswith("Tufekci, Z.")

    def test_all_blank_authors_yield_no_author_segment(self):
        """No usable name means the entry opens at the date, not at a blank."""
        rendered = to_apa(Citation(title="T", authors=["", "  "]))
        assert rendered.strip().startswith("(n.d.)")

    def test_two_authors_are_joined_with_an_ampersand(self):
        rendered = to_apa(Citation(title="T", authors=["A Alpha", "B Beta"]))
        assert "Alpha, A., & Beta, B." in rendered

    def test_three_authors_use_comma_then_ampersand(self):
        rendered = to_apa(
            Citation(title="T", authors=["A Alpha", "B Beta", "C Gamma"])
        )
        assert "Alpha, A., Beta, B., & Gamma, C." in rendered

    def test_more_than_twenty_authors_are_elided(self):
        """APA 7: first 19, an ellipsis, then the final author."""
        authors = [f"F{i} Last{i}" for i in range(25)]
        rendered = to_apa(Citation(title="T", authors=authors))
        assert ". . ." in rendered
        assert rendered.count("Last") == 20  # 19 elided + the last one
        assert "Last24" in rendered


class TestBibtexEdgeCases:
    def test_journal_and_publisher_are_emitted(self):
        rendered = to_bibtex(Citation(title="T", journal="J", publisher="P", url="u"))
        assert "journal = {J}" in rendered
        assert "publisher = {P}" in rendered

    def test_doi_prefix_is_stripped(self):
        rendered = to_bibtex(Citation(title="T", doi="https://doi.org/10.1/x"))
        assert "doi = {10.1/x}" in rendered

    def test_authors_are_joined_with_and(self):
        rendered = to_bibtex(Citation(title="T", authors=["A Alpha", "B Beta"]))
        assert "author = {A Alpha and B Beta}" in rendered


class TestBibtexEntryType:
    """@article when a DOI or journal identifies it; @misc otherwise."""

    def test_doi_makes_it_an_article(self):
        assert to_bibtex(Citation(title="T", doi="10.1/x")).startswith("@article{")

    def test_journal_makes_it_an_article(self):
        assert to_bibtex(Citation(title="T", journal="J")).startswith("@article{")

    def test_neither_makes_it_misc(self):
        assert to_bibtex(Citation(title="T", url="https://e.org")).startswith("@misc{")


class TestBibtexKeys:
    """Keys are LastYYYY, and every fallback must still produce a usable one."""

    def test_key_uses_surname_and_year(self):
        assert "{Tufekci2017," in to_bibtex(
            Citation(title="T", authors=["Tufekci, Zeynep"], year=2017)
        )

    def test_key_from_an_uninverted_name(self):
        assert "{Tufekci2017," in to_bibtex(
            Citation(title="T", authors=["Zeynep Tufekci"], year=2017)
        )

    def test_key_strips_non_alphabetic_characters(self):
        assert "{OConnell2020," in to_bibtex(
            Citation(title="T", authors=["O'Connell, Sean"], year=2020)
        )

    def test_missing_author_falls_back_to_unknown(self):
        assert "{Unknown2020," in to_bibtex(Citation(title="T", year=2020))

    def test_missing_year_falls_back_to_nd(self):
        assert "{Tufekcind," in to_bibtex(
            Citation(title="T", authors=["Tufekci, Zeynep"])
        )

    def test_blank_author_name_falls_back_to_unknown(self):
        assert "{Unknown2020," in to_bibtex(
            Citation(title="T", authors=["   "], year=2020)
        )


class TestStoreBulkOperations:
    def test_add_many_returns_one_key_per_citation(self):
        store = CitationStore()
        keys = store.add_many(
            [Citation(title="A", doi="10.1/a"), Citation(title="B", doi="10.1/b")]
        )
        assert keys == ["10.1/a", "10.1/b"]
        assert store.count == 2

    def test_format_all_bibtex_covers_every_entry(self):
        store = CitationStore()
        store.add_many(
            [
                Citation(title="A", authors=["A Alpha"], year=2001, doi="10.1/a"),
                Citation(title="B", authors=["B Beta"], year=2002, doi="10.1/b"),
            ]
        )
        rendered = store.format_all_bibtex()
        assert rendered.count("@article{") == 2
        assert "Alpha2001" in rendered
        assert "Beta2002" in rendered

    def test_to_dicts_round_trips_through_the_dataclass(self):
        store = CitationStore()
        store.add(Citation(title="A", doi="10.1/a", authors=["A Alpha"], year=2001))
        dicts = store.to_dicts()
        assert len(dicts) == 1
        assert dicts[0]["title"] == "A"
        assert dicts[0]["doi"] == "10.1/a"

    def test_title_is_the_dedup_key_of_last_resort(self):
        """With neither DOI nor URL, the title keeps entries distinct."""
        store = CitationStore()
        store.add(Citation(title="A"))
        store.add(Citation(title="B"))
        store.add(Citation(title="A"))
        assert store.count == 2
