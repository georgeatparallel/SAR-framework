"""Tests for Pydantic contract models.

Verifies round-trip serialization, validation edge cases,
default values, and enum behavior.
"""

import pytest
from pydantic import ValidationError

from sar.contracts.campaign import (
    CampaignSpec,
    CodedRow,
    EvidenceEntry,
    InclusionMemo,
)
from sar.contracts.pipeline import (
    GateDecision,
    IterationRecord,
    PipelinePhase,
    PipelineResult,
    PipelineState,
    ReviewerVerdict,
    TokenBudget,
)
from sar.contracts.search import SearchRequest, SearchResponse, SearchResult, SourceType

# ---------------------------------------------------------------------------
# SearchResult
# ---------------------------------------------------------------------------


class TestSearchResult:
    """Round-trip and validation tests for SearchResult."""

    def test_round_trip_when_full_fields(self):
        """Full SearchResult survives model_dump → model_validate."""
        sr = SearchResult(
            title="Paper Title",
            url="https://example.com/paper",
            snippet="abstract text",
            source_type=SourceType.ACADEMIC,
            doi="10.1234/test",
            authors=["Author A", "Author B"],
            year=2024,
            citation_count=42,
            relevance_score=0.85,
            backend_name="semantic_scholar",
            raw_metadata={"paperId": "abc123"},
        )
        dumped = sr.model_dump()
        restored = SearchResult.model_validate(dumped)
        assert restored == sr

    def test_round_trip_when_minimal_fields(self):
        """Minimal SearchResult (only required fields) round-trips."""
        sr = SearchResult(title="T", url="https://x.com")
        restored = SearchResult.model_validate(sr.model_dump())
        assert restored == sr

    def test_defaults_when_no_optional_fields(self):
        """Default values sane when only required fields given."""
        sr = SearchResult(title="T", url="https://x.com")
        assert sr.source_type == SourceType.UNKNOWN
        assert sr.doi is None
        assert sr.authors == []
        assert sr.year is None
        assert sr.citation_count is None
        assert sr.relevance_score == 0.0
        assert sr.backend_name == ""

    def test_rejects_when_relevance_score_out_of_range(self):
        """relevance_score outside [0, 10] raises ValidationError."""
        with pytest.raises(ValidationError):
            SearchResult(title="T", url="https://x.com", relevance_score=10.5)
        with pytest.raises(ValidationError):
            SearchResult(title="T", url="https://x.com", relevance_score=-0.1)

    def test_accepts_the_scale_the_source_judge_actually_assigns(self):
        """The judge scores 0-10 against QUALITY_THRESHOLD=6.0.

        The bound used to be le=1.0, so every real judge score violated the
        contract; nothing caught it because Pydantic does not validate on
        attribute assignment, which is how the judge sets it.
        """
        for score in (0.0, 5.0, 6.0, 10.0):
            assert (
                SearchResult(
                    title="T", url="https://x.com", relevance_score=score
                ).relevance_score
                == score
            )

    def test_rejects_when_invalid_source_type(self):
        """Invalid SourceType string raises ValidationError."""
        with pytest.raises(ValidationError):
            SearchResult(title="T", url="https://x.com", source_type="bogus")


# ---------------------------------------------------------------------------
# SearchRequest / SearchResponse
# ---------------------------------------------------------------------------


class TestSearchRequest:
    """Round-trip tests for SearchRequest."""

    def test_round_trip_when_defaults(self):
        """Default SearchRequest round-trips."""
        req = SearchRequest(query="protest movements")
        restored = SearchRequest.model_validate(req.model_dump())
        assert restored == req
        assert req.backends is None
        assert req.limit == 10
        assert req.academic_only is False

    def test_round_trip_when_all_fields(self):
        """Fully specified SearchRequest round-trips."""
        req = SearchRequest(
            query="test",
            backends=["scholar", "arxiv"],
            limit=20,
            academic_only=True,
        )
        restored = SearchRequest.model_validate(req.model_dump())
        assert restored == req


class TestSearchResponse:
    """Round-trip tests for SearchResponse."""

    def test_round_trip_when_with_results(self):
        """SearchResponse with results round-trips."""
        sr = SearchResult(title="P", url="https://x.com")
        resp = SearchResponse(
            results=[sr],
            query="q",
            backends_queried=["mock"],
            backends_failed=[],
            total_raw=1,
            total_deduped=1,
            elapsed_seconds=0.5,
        )
        restored = SearchResponse.model_validate(resp.model_dump())
        assert restored == resp

    def test_round_trip_when_empty(self):
        """Empty SearchResponse round-trips."""
        resp = SearchResponse()
        restored = SearchResponse.model_validate(resp.model_dump())
        assert restored == resp


# ---------------------------------------------------------------------------
# GateDecision
# ---------------------------------------------------------------------------


class TestGateDecision:
    """Tests for GateDecision contract."""

    def test_round_trip_when_passed(self):
        """Passed GateDecision round-trips with fields preserved."""
        gd = GateDecision(
            gate_name="test_gate",
            passed=True,
            reason="All good",
            metrics={"count": 5},
        )
        restored = GateDecision.model_validate(gd.model_dump())
        assert restored.gate_name == gd.gate_name
        assert restored.passed is True
        assert restored.reason == gd.reason
        assert restored.metrics == gd.metrics

    def test_round_trip_when_failed(self):
        """Failed GateDecision round-trips with passed=False preserved."""
        gd = GateDecision(
            gate_name="test_gate",
            passed=False,
            reason="Below threshold",
            metrics={"score": 2.1},
        )
        restored = GateDecision.model_validate(gd.model_dump())
        assert restored.passed is False

    def test_timestamp_auto_populates(self):
        """GateDecision.timestamp is automatically set."""
        gd = GateDecision(gate_name="g", passed=True, reason="ok")
        assert gd.timestamp is not None


# ---------------------------------------------------------------------------
# TokenBudget
# ---------------------------------------------------------------------------


class TestTokenBudget:
    """Tests for TokenBudget contract."""

    def test_defaults_when_no_args(self):
        """Default TokenBudget values are all zero (except ceiling)."""
        tb = TokenBudget()
        assert tb.prompt_tokens == 0
        assert tb.completion_tokens == 0
        assert tb.total_tokens == 0
        assert tb.cost_usd == 0.0
        assert tb.ceiling_usd == 5.0

    def test_is_over_budget_when_under_ceiling(self):
        """is_over_budget is False when cost < ceiling."""
        tb = TokenBudget(cost_usd=3.0, ceiling_usd=5.0)
        assert tb.is_over_budget is False

    def test_is_over_budget_when_at_ceiling(self):
        """is_over_budget is True when cost == ceiling."""
        tb = TokenBudget(cost_usd=5.0, ceiling_usd=5.0)
        assert tb.is_over_budget is True

    def test_is_over_budget_when_over_ceiling(self):
        """is_over_budget is True when cost > ceiling."""
        tb = TokenBudget(cost_usd=10.0, ceiling_usd=5.0)
        assert tb.is_over_budget is True

    def test_round_trip(self):
        """TokenBudget round-trips through serialization."""
        tb = TokenBudget(prompt_tokens=100, completion_tokens=50, total_tokens=150, cost_usd=0.5)
        restored = TokenBudget.model_validate(tb.model_dump())
        assert restored.prompt_tokens == tb.prompt_tokens
        assert restored.cost_usd == tb.cost_usd


# ---------------------------------------------------------------------------
# PipelineState
# ---------------------------------------------------------------------------


class TestPipelineState:
    """Tests for PipelineState contract."""

    def test_default_phase_when_no_args(self):
        """Default PipelineState phase is EXPAND."""
        ps = PipelineState()
        assert ps.phase == PipelinePhase.EXPAND

    def test_default_iteration_when_no_args(self):
        """Default iteration is 0."""
        ps = PipelineState()
        assert ps.iteration == 0

    def test_round_trip_when_populated(self):
        """PipelineState with data round-trips."""
        sr = SearchResult(title="P", url="https://x.com")
        ps = PipelineState(
            phase=PipelinePhase.SEARCH,
            iteration=2,
            queries=["q1", "q2"],
            results=[sr],
            report="Some report",
        )
        restored = PipelineState.model_validate(ps.model_dump())
        assert restored.phase == PipelinePhase.SEARCH
        assert restored.iteration == 2
        assert len(restored.queries) == 2
        assert len(restored.results) == 1

    def test_default_collections_when_no_args(self):
        """Default collections are empty, not None."""
        ps = PipelineState()
        assert ps.queries == []
        assert ps.results == []
        assert ps.summaries == []
        assert ps.gate_log == []
        assert ps.iterations == []
        assert ps.report is None
        assert ps.feedback is None


# ---------------------------------------------------------------------------
# IterationRecord
# ---------------------------------------------------------------------------


class TestIterationRecord:
    """Tests for IterationRecord contract."""

    def test_round_trip_when_with_verdict(self):
        """IterationRecord with reviewer_verdict round-trips."""
        ir = IterationRecord(
            iteration=1,
            phase=PipelinePhase.REVIEW,
            reviewer_verdict=ReviewerVerdict.APPROVED,
        )
        restored = IterationRecord.model_validate(ir.model_dump())
        assert restored.reviewer_verdict == ReviewerVerdict.APPROVED

    def test_defaults_when_minimal(self):
        """Minimal IterationRecord has sane defaults."""
        ir = IterationRecord(iteration=1, phase=PipelinePhase.EXPAND)
        assert ir.reviewer_verdict is None
        assert ir.feedback is None
        assert ir.queries == []
        assert ir.gate_decisions == []


# ---------------------------------------------------------------------------
# CampaignSpec
# ---------------------------------------------------------------------------


class TestCampaignSpec:
    """Tests for CampaignSpec contract."""

    def test_round_trip_when_full(self):
        """CampaignSpec full round-trip."""
        cs = CampaignSpec(
            campaign_id="test-2020",
            campaign_name="Test Campaign",
            country="Testland",
            year=2020,
            description="A test campaign",
            priority_fields=["outcome", "duration_days"],
        )
        restored = CampaignSpec.model_validate(cs.model_dump())
        assert restored == cs

    def test_required_fields_when_missing(self):
        """Missing required fields raise ValidationError."""
        with pytest.raises(ValidationError):
            CampaignSpec(campaign_id="test")  # missing campaign_name, country, year

    def test_defaults_when_optional_omitted(self):
        """Optional fields have sensible defaults."""
        cs = CampaignSpec(
            campaign_id="t",
            campaign_name="T",
            country="C",
            year=2020,
        )
        assert cs.description == ""
        assert cs.baseline_row is None
        assert cs.priority_fields == []


# ---------------------------------------------------------------------------
# InclusionMemo
# ---------------------------------------------------------------------------


class TestInclusionMemo:
    """Tests for InclusionMemo contract."""

    def test_round_trip_when_included(self):
        """Included InclusionMemo round-trips."""
        im = InclusionMemo(
            campaign_id="test-2020",
            included=True,
            reason="Meets all criteria",
            missing_fields=["gini_coefficient"],
        )
        restored = InclusionMemo.model_validate(im.model_dump())
        assert restored == im
        assert restored.included is True

    def test_round_trip_when_excluded(self):
        """Excluded InclusionMemo round-trips."""
        im = InclusionMemo(
            campaign_id="test-2020",
            included=False,
            reason="Not a social movement",
        )
        restored = InclusionMemo.model_validate(im.model_dump())
        assert restored.included is False


# ---------------------------------------------------------------------------
# CodedRow and EvidenceEntry
# ---------------------------------------------------------------------------


class TestCodedRow:
    """Tests for CodedRow contract."""

    def test_round_trip_when_arbitrary_fields(self):
        """CodedRow accepts arbitrary field names in fields dict."""
        cr = CodedRow(
            campaign_id="test",
            fields={"country": "Egypt", "year_start": "2011", "custom_field": "x"},
        )
        restored = CodedRow.model_validate(cr.model_dump())
        assert restored.fields["custom_field"] == "x"

    def test_defaults_when_minimal(self):
        """Minimal CodedRow has empty defaults."""
        cr = CodedRow(campaign_id="t")
        assert cr.fields == {}
        assert cr.evidence == []
        assert cr.warnings == []


class TestEvidenceEntry:
    """Tests for EvidenceEntry contract."""

    def test_round_trip(self):
        """EvidenceEntry round-trips."""
        ee = EvidenceEntry(
            field_name="outcome",
            value="1.0",
            source_url="https://example.com",
            confidence=0.9,
        )
        restored = EvidenceEntry.model_validate(ee.model_dump())
        assert restored == ee

    def test_rejects_when_confidence_out_of_range(self):
        """Confidence outside [0, 1] raises ValidationError."""
        with pytest.raises(ValidationError):
            EvidenceEntry(field_name="f", value="v", confidence=1.5)


# ---------------------------------------------------------------------------
# PipelineResult
# ---------------------------------------------------------------------------


class TestPipelineResult:
    """Tests for PipelineResult envelope."""

    def test_round_trip_when_success(self):
        """Successful PipelineResult round-trips."""
        pr = PipelineResult(
            success=True,
            report="# Report",
            iterations_used=3,
        )
        restored = PipelineResult.model_validate(pr.model_dump())
        assert restored.success is True
        assert restored.report == "# Report"

    def test_round_trip_when_failure(self):
        """Failed PipelineResult round-trips."""
        pr = PipelineResult(success=False, report=None)
        restored = PipelineResult.model_validate(pr.model_dump())
        assert restored.success is False
