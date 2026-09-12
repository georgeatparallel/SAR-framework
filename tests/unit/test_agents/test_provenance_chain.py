"""Tests for the reader -> synthesis provenance chain.

These pin the contract that the pipeline's headline guarantee rests on: the
claims and verbatim quotes a reader extracts must actually reach the agent that
writes the report, and a reviewer that cannot produce a verdict must not be
read as an approval.

Each test here corresponds to a defect the suite previously could not see,
because the existing e2e tests asserted that ``PaperSummary.extraction`` was
populated and never asserted what was handed onward from it:

- The runner stored ``key_claims``/``key_quotes`` (PAPER_SUMMARY_SCHEMA, the
  schema the reader actually requests) while the analyzer read
  ``key_findings``/``summary`` (DEFAULT_SCHEMA, dead). Every source block
  rendered ``Key claims: N/A`` with no quotes at all.
- The campaign synthesizer read six campaign-only fields that the generic
  pipeline never produces, collapsing evidence blocks to a title and a URL.
- ``_format_campaign_context`` read ``.year``/``.description`` by attribute,
  raising ``AttributeError`` on the ``SimpleNamespace`` the batch path builds
  (which carries ``year_start``/``notes``), which the runner turned into an
  empty "Synthesis failed" dossier.
- The reviewer approved by default when the model returned no tool call.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from sar.agents import analyzer, campaign_synthesizer, reviewer
from sar.contracts.pipeline import PaperSummary
from sar.contracts.search import SearchResult

CLAIM = "Turnout rose by 14 points in treated municipalities"
QUOTE = "we observe a 14 point increase in turnout"
QUERY = "does social media mobilise protest"


def _summary(extraction: dict[str, Any]) -> PaperSummary:
    """A PaperSummary shaped exactly as the runner builds it."""
    return PaperSummary(
        source=SearchResult(
            title="Mobilisation and Media",
            url="https://example.org/paper",
            snippet="a short search snippet",
            year=2021,
        ),
        extraction=extraction,
        quality_score=0.8,
        notes="",
    )


def _report_response() -> SimpleNamespace:
    """A well-formed submit_report tool call."""
    return SimpleNamespace(
        content=None,
        tool_calls=[
            SimpleNamespace(
                name="submit_report",
                arguments={"report": "# Report", "citations_used": ["Source 1"]},
            )
        ],
    )


class TestAnalyzerReceivesEvidence:
    """What the runner stores must reach the analyzer's prompt."""

    @pytest.mark.asyncio
    async def test_prompt_carries_key_claims_from_paper_summary_schema(
        self, mock_llm: Any
    ) -> None:
        mock_llm.set_response(_report_response())
        await analyzer.run(
            query=QUERY,
            summaries=[_summary({"key_claims": [CLAIM], "methodology": "RCT"})],
            llm=mock_llm,
        )
        prompt = str(mock_llm.calls[0].messages)
        assert CLAIM in prompt
        assert "Key claims: N/A" not in prompt

    @pytest.mark.asyncio
    async def test_prompt_carries_verbatim_quotes(self, mock_llm: Any) -> None:
        mock_llm.set_response(_report_response())
        await analyzer.run(
            query=QUERY,
            summaries=[
                _summary(
                    {
                        "key_claims": [CLAIM],
                        "methodology": "RCT",
                        "key_quotes": [QUOTE],
                    }
                )
            ],
            llm=mock_llm,
        )
        prompt = str(mock_llm.calls[0].messages)
        assert QUOTE in prompt, "quotes are the evidence citations rest on"

    @pytest.mark.asyncio
    async def test_legacy_key_findings_spelling_still_renders(
        self, mock_llm: Any
    ) -> None:
        """A custom extraction_schema may still emit the older key name."""
        mock_llm.set_response(_report_response())
        await analyzer.run(
            query=QUERY,
            summaries=[_summary({"key_findings": [CLAIM]})],
            llm=mock_llm,
        )
        assert CLAIM in str(mock_llm.calls[0].messages)

    @pytest.mark.asyncio
    async def test_missing_claims_still_render_na(self, mock_llm: Any) -> None:
        mock_llm.set_response(_report_response())
        await analyzer.run(
            query=QUERY, summaries=[_summary({"methodology": "RCT"})], llm=mock_llm
        )
        assert "Key claims: N/A" in str(mock_llm.calls[0].messages)


class TestCampaignSynthesizerReceivesEvidence:
    """The same contract, for the dossier path."""

    def test_evidence_block_carries_claims_and_quotes(self) -> None:
        block = campaign_synthesizer._format_reader_summaries(
            [
                {
                    "title": "Protest Wave",
                    "url": "https://example.org/a",
                    "key_claims": [CLAIM],
                    "key_quotes": [QUOTE],
                    "methodology": "case study",
                }
            ]
        )
        assert CLAIM in block
        assert QUOTE in block
        assert "case study" in block

    def test_campaign_specific_fields_still_render(self) -> None:
        """A campaign extraction schema keeps working alongside the generic one."""
        block = campaign_synthesizer._format_reader_summaries(
            [{"title": "X", "outcome": "success", "duration_days": 12}]
        )
        assert "outcome: success" in block
        assert "duration_days: 12" in block


class TestCampaignContextAcceptsEverySpecShape:
    """The batch path builds a SimpleNamespace with different field names."""

    def test_batch_shaped_namespace_does_not_raise(self) -> None:
        spec = SimpleNamespace(
            campaign_name="Dey Protests",
            country="Iran",
            year_start="2017",
            region="MENA",
            notes="winter protests",
            slug="iran-2017-dey-protests",
        )
        out = campaign_synthesizer._format_campaign_context(spec)
        assert "Dey Protests" in out
        assert "2017" in out
        assert "winter protests" in out

    def test_plain_dict_spec_works(self) -> None:
        out = campaign_synthesizer._format_campaign_context(
            {"campaign_name": "A", "country": "B", "year": "1999"}
        )
        assert "1999" in out

    def test_canonical_field_names_work(self) -> None:
        spec = SimpleNamespace(
            campaign_name="A",
            country="B",
            year="2020",
            description="desc",
            priority_fields=["outcome", "duration_days"],
        )
        out = campaign_synthesizer._format_campaign_context(spec)
        assert "desc" in out
        assert "outcome, duration_days" in out


class TestReviewerFailsClosed:
    """A review that did not happen must not read as a passed review."""

    @pytest.mark.asyncio
    async def test_no_tool_call_raises(self, mock_llm: Any) -> None:
        mock_llm.set_response(SimpleNamespace(content="Looks fine to me.", tool_calls=[]))
        with pytest.raises(reviewer.ReviewerError):
            await reviewer.run(query=QUERY, report="# Report", llm=mock_llm)

    @pytest.mark.asyncio
    async def test_unknown_tool_raises(self, mock_llm: Any) -> None:
        mock_llm.set_response(
            SimpleNamespace(
                content=None,
                tool_calls=[SimpleNamespace(name="something_else", arguments={})],
            )
        )
        with pytest.raises(reviewer.ReviewerError):
            await reviewer.run(query=QUERY, report="# Report", llm=mock_llm)

    @pytest.mark.asyncio
    async def test_request_forces_a_tool_call(self, mock_llm: Any) -> None:
        mock_llm.set_response(
            SimpleNamespace(
                content=None,
                tool_calls=[SimpleNamespace(name="mark_as_complete", arguments={})],
            )
        )
        await reviewer.run(query=QUERY, report="# Report", llm=mock_llm)
        assert mock_llm.calls[0].tool_choice == "required"

    @pytest.mark.asyncio
    async def test_valid_verdict_still_returns(self, mock_llm: Any) -> None:
        mock_llm.set_response(
            SimpleNamespace(
                content=None,
                tool_calls=[
                    SimpleNamespace(
                        name="request_rework", arguments={"feedback": "add citations"}
                    )
                ],
            )
        )
        verdict, payload = await reviewer.run(
            query=QUERY, report="# Report", llm=mock_llm
        )
        assert verdict == "needs_rework"
        assert payload == "add citations"
