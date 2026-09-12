"""Batch verification runner: mass campaign dossier generation and comparison.

Runs the campaign pipeline for multiple campaigns from a master dataset,
compares outputs against baseline values, and generates diff reports.

Migrated from: agentic_sar/run_campaign_batch_verification.py
and agentic_sar/campaign_service.py save_campaign_outputs()
"""

from __future__ import annotations

import csv
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sar.campaign.schema import DATASET_COLUMNS, KEY_FIELDS
from sar.campaign.validators import _is_na, _text, normalize_coded_row
from sar.persistence.audit import AuditLog

if TYPE_CHECKING:
    from sar.config import AppConfig
    from sar.llm.client import LLMClient
    from sar.search.engine import SearchEngine

logger = logging.getLogger(__name__)


def build_campaign_spec_from_row(row: dict[str, str]) -> dict[str, Any]:
    """Convert a CSV row to a CampaignSpec-compatible dict.

    Extracts identity fields and builds a notes string that highlights
    KEY_FIELDS values from the baseline for comparison during research.

    Args:
        row: Dictionary from csv.DictReader.

    Returns:
        CampaignSpec-compatible dict with campaign_name, country,
        year_start, region, notes, and baseline_row.
    """
    country = _text(row.get("country", ""))
    campaign_name = _text(row.get("campaign_name", ""))
    year_start = _text(row.get("year_start", ""))
    region = _text(row.get("region", ""))

    # Build notes from key fields for researcher context
    notes_parts: list[str] = []
    for field in KEY_FIELDS:
        val = _text(row.get(field, ""))
        if val and not _is_na(val):
            notes_parts.append(f"{field}={val}")
    notes = "; ".join(notes_parts) if notes_parts else ""

    # Build campaign slug for directory naming
    slug_parts = [
        country.lower().replace(" ", "-"),
        year_start,
        campaign_name.lower().replace(" ", "-"),
    ]
    slug = "-".join(p for p in slug_parts if p)

    return {
        "campaign_name": campaign_name,
        "country": country,
        "year_start": year_start,
        "region": region,
        "notes": notes,
        "slug": slug,
        "baseline_row": dict(row),
    }


def compare_with_baseline(
    researched: dict[str, str],
    baseline: dict[str, str],
) -> list[dict[str, str]]:
    """Compare researched values against baseline row.

    For each field, determines status:
    - match: same value
    - changed: different non-NA values
    - filled: baseline was NA, research found a value
    - missing: research returned NA, baseline had a value

    Args:
        researched: Coded row from pipeline output (normalized).
        baseline: Original row from master dataset.

    Returns:
        List of diff dicts with field_name, baseline_value,
        researched_value, and status.
    """
    diffs: list[dict[str, str]] = []

    for field in DATASET_COLUMNS:
        b_val = _text(baseline.get(field, "NA"))
        r_val = _text(researched.get(field, "NA"))

        b_na = _is_na(b_val)
        r_na = _is_na(r_val)

        if b_val.lower() == r_val.lower():
            status = "match"
        elif b_na and not r_na:
            status = "filled"
        elif not b_na and r_na:
            status = "missing"
        else:
            status = "changed"

        diffs.append({
            "field_name": field,
            "baseline_value": b_val if not b_na else "NA",
            "researched_value": r_val if not r_na else "NA",
            "status": status,
        })

    return diffs


def write_batch_outputs(
    output_root: Path,
    campaign_slug: str,
    dossier: dict[str, Any],
    baseline_row: dict[str, str],
    diffs: list[dict[str, str]],
) -> None:
    """Write all output files for a single campaign in the batch.

    Creates the campaign subdirectory and writes:
    - campaign_dossier.json (full structured dossier)
    - campaign_report.md (human-readable narrative report)
    - coded_row.csv (single-row codebook-compliant CSV)
    - baseline_row.csv (original dataset row for comparison)
    - evidence_log.csv (per-field evidence entries)
    - uncertainty_note.md (data gaps and caveats)
    - verification_diff.csv (field-by-field comparison)

    Args:
        output_root: Batch output root directory.
        campaign_slug: Slugified campaign name (used as subdirectory).
        dossier: Campaign dossier output dict.
        baseline_row: Original dataset row.
        diffs: Comparison results from compare_with_baseline().
    """
    campaign_dir = output_root / campaign_slug
    campaign_dir.mkdir(parents=True, exist_ok=True)

    # Full JSON dossier
    dossier_path = campaign_dir / "campaign_dossier.json"
    dossier_path.write_text(
        json.dumps(dossier, indent=2, default=str),
        encoding="utf-8",
    )

    # Markdown report
    report_md = dossier.get("report_markdown", "")
    (campaign_dir / "campaign_report.md").write_text(
        report_md, encoding="utf-8"
    )

    # Uncertainty note
    uncertainty = dossier.get("uncertainty_note", "")
    if uncertainty:
        (campaign_dir / "uncertainty_note.md").write_text(
            uncertainty, encoding="utf-8"
        )

    # Coded row CSV
    coded_row = dossier.get("coded_row", {})
    with (campaign_dir / "coded_row.csv").open(
        "w", newline="", encoding="utf-8"
    ) as f:
        writer = csv.DictWriter(f, fieldnames=DATASET_COLUMNS)
        writer.writeheader()
        writer.writerow(
            {col: coded_row.get(col, "NA") for col in DATASET_COLUMNS}
        )

    # Baseline row CSV
    with (campaign_dir / "baseline_row.csv").open(
        "w", newline="", encoding="utf-8"
    ) as f:
        writer = csv.DictWriter(f, fieldnames=DATASET_COLUMNS)
        writer.writeheader()
        writer.writerow(
            {col: baseline_row.get(col, "NA") for col in DATASET_COLUMNS}
        )

    # Evidence log CSV
    evidence_log = dossier.get("evidence_log", [])
    evidence_columns = [
        "field_name",
        "coded_value",
        "source_type",
        "source_citation",
        "source_link_or_identifier",
        "notes",
    ]
    with (campaign_dir / "evidence_log.csv").open(
        "w", newline="", encoding="utf-8"
    ) as f:
        writer = csv.DictWriter(f, fieldnames=evidence_columns)
        writer.writeheader()
        for entry in evidence_log:
            writer.writerow(
                {col: entry.get(col, "") for col in evidence_columns}
            )

    # Verification diff CSV
    diff_columns = [
        "field_name",
        "baseline_value",
        "researched_value",
        "status",
    ]
    with (campaign_dir / "verification_diff.csv").open(
        "w", newline="", encoding="utf-8"
    ) as f:
        writer = csv.DictWriter(f, fieldnames=diff_columns)
        writer.writeheader()
        writer.writerows(diffs)


async def run_batch(
    dataset_path: str | Path,
    output_root: str | Path,
    config: AppConfig,
    search_engine: SearchEngine,
    llm: LLMClient,
    slug_filter: str | None = None,
    country_filter: str | None = None,
    limit: int | None = None,
    offset: int = 0,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Run campaign verification for a batch of campaigns.

    Loads the master dataset, applies filters, and runs the campaign
    pipeline for each qualifying row. Compares outputs against baseline
    values and writes per-campaign artifacts plus a batch manifest.

    Args:
        dataset_path: Path to master_dataset.csv.
        output_root: Directory for output artifacts.
        config: Application configuration.
        search_engine: Configured search engine.
        llm: LLM client.
        slug_filter: If set, only run this campaign slug.
        country_filter: If set, only run campaigns for this country.
        limit: Maximum number of campaigns to process.
        offset: Skip this many campaigns before starting.
        overwrite: If True, re-run even if output exists.

    Returns:
        Batch manifest dict with run_id, counts, and per-campaign status.
    """
    dataset_path = Path(dataset_path)
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    # Load dataset
    with open(dataset_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    # Build specs and apply filters
    specs: list[dict[str, Any]] = []
    for row in rows:
        spec = build_campaign_spec_from_row(row)
        if slug_filter and spec["slug"] != slug_filter:
            continue
        if country_filter and spec["country"].lower() != country_filter.lower():
            continue
        specs.append(spec)

    # Apply offset and limit
    specs = specs[offset:]
    if limit is not None:
        specs = specs[:limit]

    # Build manifest
    run_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    manifest: dict[str, Any] = {
        "run_id": run_id,
        "started_at": datetime.now(UTC).isoformat(),
        "dataset_path": str(dataset_path),
        "total_campaigns": len(specs),
        "completed": [],
        "skipped": [],
        "failed": [],
    }

    for spec in specs:
        slug = spec["slug"]
        campaign_dir = output_root / slug

        # Skip existing unless overwrite requested
        if not overwrite and campaign_dir.exists():
            manifest["skipped"].append(slug)
            logger.info("Skipping existing: %s", slug)
            continue

        try:
            # Import here to avoid circular imports at module load time
            from sar.orchestrator.runner import run_campaign_pipeline

            campaign_audit = AuditLog(
                run_id=slug,
                log_dir=output_root / slug,
            )
            dossier = await run_campaign_pipeline(
                campaign_spec=spec,
                config=config,
                search_engine=search_engine,
                llm=llm,
                audit_log=campaign_audit,
            )

            # Normalize the coded row
            coded_row = normalize_coded_row(
                dossier.get("coded_row", {}), spec
            )
            dossier["coded_row"] = coded_row

            # Compare with baseline
            diffs = compare_with_baseline(coded_row, spec["baseline_row"])

            # Write outputs
            write_batch_outputs(
                output_root, slug, dossier, spec["baseline_row"], diffs
            )

            manifest["completed"].append(slug)
            logger.info("Completed: %s", slug)

        except Exception as e:
            manifest["failed"].append(slug)
            logger.error("Failed: %s: %s", slug, e)

    # Finalize manifest
    manifest["finished_at"] = datetime.now(UTC).isoformat()
    manifest["success_count"] = len(manifest["completed"])
    manifest["failure_count"] = len(manifest["failed"])
    manifest["skip_count"] = len(manifest["skipped"])

    manifest_path = output_root / "batch_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )

    logger.info(
        "Batch %s complete: %d completed, %d failed, %d skipped",
        run_id,
        len(manifest["completed"]),
        len(manifest["failed"]),
        len(manifest["skipped"]),
    )

    return manifest


def rebuild_batch_index(output_root: str | Path) -> None:
    """Rebuild summary CSVs from existing per-campaign outputs.

    Scans all campaign subdirectories and regenerates:
    - verification_summary.csv (one row per campaign with key stats)
    - verification_field_diffs.csv (all field diffs concatenated)
    - batch_manifest.json (counts and campaign list)

    Args:
        output_root: Batch output root directory.
    """
    output_root = Path(output_root)

    summary_rows: list[dict[str, str]] = []
    all_diffs: list[dict[str, str]] = []
    campaign_slugs: list[str] = []

    for dossier_path in sorted(
        output_root.glob("*/campaign_dossier.json")
    ):
        slug = dossier_path.parent.name
        campaign_slugs.append(slug)

        try:
            with open(dossier_path, encoding="utf-8") as f:
                data = json.loads(f.read())

            # Build summary row
            coded_row = data.get("coded_row", {})
            summary_row: dict[str, str] = {"campaign_slug": slug}
            summary_row.update(
                {col: coded_row.get(col, "NA") for col in DATASET_COLUMNS}
            )
            summary_rows.append(summary_row)

            # Load diffs if available
            diff_path = dossier_path.parent / "verification_diff.csv"
            if diff_path.exists():
                with open(diff_path, encoding="utf-8") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        row["campaign_slug"] = slug
                        all_diffs.append(row)

        except Exception as e:
            logger.warning("Could not load %s: %s", dossier_path, e)

    # Write verification_summary.csv
    if summary_rows:
        summary_path = output_root / "verification_summary.csv"
        fieldnames = ["campaign_slug"] + DATASET_COLUMNS
        with open(
            summary_path, "w", encoding="utf-8", newline=""
        ) as f:
            writer = csv.DictWriter(
                f, fieldnames=fieldnames, extrasaction="ignore"
            )
            writer.writeheader()
            writer.writerows(summary_rows)
        logger.info(
            "Rebuilt summary: %d campaigns -> %s",
            len(summary_rows),
            summary_path,
        )

    # Write verification_field_diffs.csv
    if all_diffs:
        diffs_path = output_root / "verification_field_diffs.csv"
        diff_fieldnames = [
            "campaign_slug",
            "field_name",
            "baseline_value",
            "researched_value",
            "status",
        ]
        with open(
            diffs_path, "w", encoding="utf-8", newline=""
        ) as f:
            writer = csv.DictWriter(
                f, fieldnames=diff_fieldnames, extrasaction="ignore"
            )
            writer.writeheader()
            writer.writerows(all_diffs)
        logger.info(
            "Rebuilt diffs: %d entries -> %s", len(all_diffs), diffs_path
        )

    # Write batch_manifest.json
    manifest = {
        "rebuilt_at": datetime.now(UTC).isoformat(),
        "total_campaigns": len(campaign_slugs),
        "campaigns": campaign_slugs,
    }
    manifest_path = output_root / "batch_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
