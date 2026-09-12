"""Campaign domain contracts.

Defines the data shapes for campaign research: specifications,
coded rows, evidence logs, dossier output, and batch manifests.

Migrated from: hardcoded DATASET_COLUMNS and CODEBOOK_ALLOWED_VALUES
in agentic_sar/campaign_service.py
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class EvidenceEntry(BaseModel):
    """A single piece of evidence supporting a coded field value.

    Attributes:
        field_name: Codebook field this evidence supports.
        value: The value assigned to the field.
        source_url: URL of the source, if available.
        source_title: Title of the source document, if available.
        confidence: Confidence level in [0, 1].
        notes: Additional context or caveats.
    """

    field_name: str
    value: Any
    source_url: str | None = None
    source_title: str | None = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    notes: str = ""


class CodedRow(BaseModel):
    """A single row of codebook-compliant campaign data.

    Keys are codebook column names, values are normalized.
    All values pass through campaign.validators before storage.

    Attributes:
        campaign_id: Unique identifier for the campaign.
        fields: Column name to normalized value mapping.
        evidence: Per-field evidence entries supporting coded values.
        warnings: Validation warnings or data-quality notes.
    """

    campaign_id: str
    fields: dict[str, Any] = Field(default_factory=dict)
    evidence: list[EvidenceEntry] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class CampaignSpec(BaseModel):
    """Input specification for a campaign research run.

    Provides the minimum information needed to launch a research
    pipeline for a specific campaign.

    Attributes:
        campaign_id: Unique identifier for the campaign (slug format).
        campaign_name: Human-readable campaign name.
        country: Country where the campaign occurred.
        year: Primary year of the campaign.
        description: Free-text description or research guidance.
        baseline_row: Pre-existing coded data to compare against (if any).
        priority_fields: Which codebook fields to prioritize in research.
    """

    campaign_id: str
    campaign_name: str
    country: str
    year: int
    description: str = ""
    baseline_row: dict[str, Any] | None = None
    priority_fields: list[str] = Field(default_factory=list)


class InclusionMemo(BaseModel):
    """Decision record for whether a campaign is included in the dataset.

    Attributes:
        campaign_id: Unique identifier for the campaign.
        included: Whether the campaign meets inclusion criteria.
        reason: Rationale for inclusion or exclusion.
        missing_fields: Codebook fields that could not be coded.
    """

    campaign_id: str
    included: bool
    reason: str
    missing_fields: list[str] = Field(default_factory=list)


class CampaignDossier(BaseModel):
    """Complete output from a campaign research pipeline run.

    Bundles the specification, coded data, evidence, inclusion decision,
    and narrative report into a single auditable artifact.

    Attributes:
        spec: The input campaign specification.
        coded_row: Codebook-normalized data row.
        inclusion_memo: Inclusion/exclusion decision (None if not yet assessed).
        report_md: Full narrative Markdown report.
        uncertainty_notes: Narrative on gaps and disputed facts.
        sources_consulted: Number of distinct sources consulted.
        created_at: Timestamp when the dossier was finalized.
    """

    spec: CampaignSpec
    coded_row: CodedRow
    inclusion_memo: InclusionMemo | None = None
    report_md: str = ""
    uncertainty_notes: str = ""
    sources_consulted: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)


class BatchManifest(BaseModel):
    """Metadata for a batch verification run.

    Tracks which campaigns were processed, succeeded, failed, or skipped
    in a single batch execution.

    Attributes:
        run_id: Unique batch run identifier.
        started_at: When the batch started.
        campaigns: List of campaign_ids scheduled for processing.
        completed: Campaign_ids that finished successfully.
        failed: Campaign_ids that errored during processing.
        skipped: Campaign_ids that were skipped (e.g., already processed).
    """

    run_id: str
    started_at: datetime
    campaigns: list[str]
    completed: list[str] = Field(default_factory=list)
    failed: list[str] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)
