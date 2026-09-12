"""HTTP route handlers for the agentic SAR web API.

Defines the three endpoints exposed by the FastAPI application:
- POST /research  — run a generic literature research pipeline
- POST /campaign  — run a campaign-specific research pipeline
- GET  /health    — liveness check with backend listing

Each handler is a thin adapter: it validates input via Pydantic, creates
a per-request AuditLog, calls the appropriate runner, and serialises the
result as JSON.  No business logic lives here.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from sar.orchestrator.runner import run_campaign_pipeline, run_pipeline
from sar.persistence.audit import AuditLog

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / response Pydantic models
# ---------------------------------------------------------------------------


class ResearchRequest(BaseModel):
    """Body for POST /research.

    Attributes:
        query: The user's natural-language research question.
    """

    query: str = Field(..., min_length=1, description="Research question to investigate.")


class ResearchResponse(BaseModel):
    """Response from POST /research.

    Attributes:
        report: Final Markdown research report (empty string if pipeline failed).
        success: True if the pipeline reached COMPLETE state.
        iterations_used: Number of EXPAND→REVIEW cycles executed.
    """

    report: str
    success: bool
    iterations_used: int


class CampaignRequest(BaseModel):
    """Body for POST /campaign.

    Attributes:
        campaign_name: Human-readable name of the protest campaign.
        country: Country where the campaign occurred.
        year_start: Year the campaign began (e.g. "2014").
        region: Geographic region (e.g. "East Asia", "Middle East").
    """

    campaign_name: str = Field(..., min_length=1, description="Name of the protest campaign.")
    country: str = Field(..., min_length=1, description="Country of the campaign.")
    year_start: str = Field(..., min_length=4, description="Start year (e.g. '2014').")
    region: str = Field(..., min_length=1, description="Geographic region.")


class CampaignResponse(BaseModel):
    """Response from POST /campaign.

    Attributes:
        report_markdown: Full Markdown dossier for the campaign.
        coded_row: Codebook-compliant structured data extracted from sources.
        inclusion_memo: Memo justifying inclusion/exclusion criteria.
        uncertainty_note: Free-text note on data limitations.
        success: True if the underlying pipeline completed successfully.
        iterations_used: Number of pipeline iterations executed.
    """

    report_markdown: str
    coded_row: dict[str, Any]
    inclusion_memo: dict[str, Any]
    uncertainty_note: str
    success: bool
    iterations_used: int


class HealthResponse(BaseModel):
    """Response from GET /health.

    Attributes:
        status: Always "ok" when the server is responsive.
        backends: List of search backend names registered in the engine.
    """

    status: str
    backends: list[str]


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------


@router.post("/research", response_model=ResearchResponse)
async def research(body: ResearchRequest, request: Request) -> ResearchResponse:
    """Run a generic literature research pipeline.

    Accepts a natural-language query, runs the full EXPAND → SEARCH →
    JUDGE → READ → ANALYZE → REVIEW state machine, and returns the
    synthesised Markdown report.

    Each request gets its own UUID-keyed AuditLog file written to the
    directory configured in ``AppConfig.AUDIT_LOG_DIR``.

    Args:
        body: Request body containing the research query.
        request: FastAPI Request, used to access shared app state.

    Returns:
        ResearchResponse with the final report, success flag, and
        iteration count.

    Raises:
        HTTPException: 500 if an unhandled exception occurs during the
            pipeline run.
    """
    state: dict[str, Any] = request.app.state.sar
    config = state["config"]
    search_engine = state["search_engine"]
    llm = state["llm"]

    run_id = str(uuid.uuid4())
    audit_log = AuditLog(run_id=run_id, log_dir=config.AUDIT_LOG_DIR)

    try:
        result = await run_pipeline(
            query=body.query,
            config=config,
            search_engine=search_engine,
            llm=llm,
            audit_log=audit_log,
        )
    except Exception as exc:
        audit_log.record_error("http_research", str(exc), {"query": body.query})
        raise HTTPException(status_code=500, detail=f"Pipeline failed: {exc}") from exc

    return ResearchResponse(
        report=result.report or "",
        success=result.success,
        iterations_used=result.iterations_used,
    )


@router.post("/campaign", response_model=CampaignResponse)
async def campaign(body: CampaignRequest, request: Request) -> CampaignResponse:
    """Run a campaign-specific research pipeline.

    Accepts a campaign specification, runs the pipeline with
    campaign-aware query expansion and synthesis, and returns a
    codebook-compliant dossier.

    Each request gets its own UUID-keyed AuditLog file.

    Args:
        body: Request body with campaign name, country, year, and region.
        request: FastAPI Request, used to access shared app state.

    Returns:
        CampaignResponse with the Markdown dossier, coded row, memos,
        success flag, and iteration count.

    Raises:
        HTTPException: 500 if an unhandled exception occurs during the
            pipeline run.
    """
    state: dict[str, Any] = request.app.state.sar
    config = state["config"]
    search_engine = state["search_engine"]
    llm = state["llm"]

    run_id = str(uuid.uuid4())
    audit_log = AuditLog(run_id=run_id, log_dir=config.AUDIT_LOG_DIR)

    campaign_spec = {
        "campaign_name": body.campaign_name,
        "country": body.country,
        "year_start": body.year_start,
        "region": body.region,
    }

    try:
        dossier = await run_campaign_pipeline(
            campaign_spec=campaign_spec,
            config=config,
            search_engine=search_engine,
            llm=llm,
            audit_log=audit_log,
        )
    except Exception as exc:
        audit_log.record_error("http_campaign", str(exc), campaign_spec)
        raise HTTPException(status_code=500, detail=f"Campaign pipeline failed: {exc}") from exc

    pipeline_result = dossier.get("pipeline_result")
    success = getattr(pipeline_result, "success", False) if pipeline_result else False
    iterations_used = getattr(pipeline_result, "iterations_used", 0) if pipeline_result else 0

    return CampaignResponse(
        report_markdown=dossier.get("report_markdown", ""),
        coded_row=dossier.get("coded_row", {}),
        inclusion_memo=dossier.get("inclusion_memo", {}),
        uncertainty_note=dossier.get("uncertainty_note", ""),
        success=success,
        iterations_used=iterations_used,
    )


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    """Liveness and readiness check.

    Returns HTTP 200 with the service status and the list of search
    backend names that were registered at startup.  Useful for load
    balancers and monitoring systems.

    Args:
        request: FastAPI Request, used to access shared app state.

    Returns:
        HealthResponse with ``status="ok"`` and the backend name list.
    """
    state: dict[str, Any] = request.app.state.sar
    backends: list[str] = state.get("backend_names", [])

    return HealthResponse(status="ok", backends=backends)
