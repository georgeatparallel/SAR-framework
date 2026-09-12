"""CLI entry point for campaign-specific research.

Runs the campaign research pipeline for a single campaign with
structured dossier output.

Usage:
    python -m cli.campaign \\
        --campaign-name "Euromaidan" \\
        --country "Ukraine" \\
        --year-start 2013 --year-end 2014 \\
        --region "Post-Soviet" \\
        --save-dir /tmp/campaign_output

Migrated from: agentic_sar/campaign_research.py
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import re
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv


def _load_env() -> None:
    # Installed package: honour the invoking directory's .env first. The
    # source-tree fallback (four levels up from src/sar/cli/) supports
    # running from a checkout without an install.
    if not load_dotenv(Path.cwd() / ".env"):
        load_dotenv(Path(__file__).resolve().parents[3] / ".env")


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the campaign CLI.

    Returns:
        Configured ArgumentParser.
    """
    p = argparse.ArgumentParser(
        description="Run the v2 SAR campaign research pipeline for one campaign.",
    )
    p.add_argument("--campaign-name", required=True)
    p.add_argument("--country", required=True)
    p.add_argument("--year-start", required=True, type=int)
    p.add_argument("--year-end", type=int)
    p.add_argument("--region", default="")
    p.add_argument("--notes", default="")
    p.add_argument("--save-dir", default="")
    p.add_argument("--backend", default=None, choices=["openai", "codex"],
                   help="LLM backend (default: $SAR_LLM_BACKEND, else openai).")
    p.add_argument("--model", default=None,
                   help="Model identifier override for the chosen backend.")
    p.add_argument("--focus-fields", default="",
                   help="Comma-separated codebook fields to prioritise.")
    return p


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _build_search_engine(config):
    """Construct a SearchEngine with all keyless backends registered."""
    from sar.search.backends import (
        ArxivBackend,
        CrossRefBackend,
        DuckDuckGoBackend,
        PubMedBackend,
        SemanticScholarBackend,
        WikipediaBackend,
    )
    from sar.search.engine import SearchEngine

    backends = [
        DuckDuckGoBackend(),
        WikipediaBackend(),
        CrossRefBackend(),
        SemanticScholarBackend(),
        ArxivBackend(),
        PubMedBackend(),
    ]
    from sar.cli import keyed_backends

    backends.extend(keyed_backends(config))
    return SearchEngine(backends=backends)


def _save_dossier(out_dir: Path, result: dict) -> None:
    """Persist v2 campaign-pipeline output to disk.

    Writes campaign_dossier.json, campaign_report.md, coded_row.csv,
    evidence_log.csv, inclusion_memo.json.
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    spec = result.get("campaign_spec")
    spec_dict = spec.model_dump() if hasattr(spec, "model_dump") else (spec or {})

    payload = {
        "campaign_spec": spec_dict,
        "inclusion_memo": result.get("inclusion_memo", {}),
        "coded_row": result.get("coded_row", {}),
        "evidence_log": result.get("evidence_log", []),
        "uncertainty_note": result.get("uncertainty_note", ""),
        "report_markdown": result.get("report_markdown", ""),
        "citations_used": result.get("citations_used", []),
    }
    (out_dir / "campaign_dossier.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8"
    )

    (out_dir / "campaign_report.md").write_text(
        result.get("report_markdown", "") or "", encoding="utf-8"
    )

    coded_row = result.get("coded_row") or {}
    if coded_row:
        with (out_dir / "coded_row.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(coded_row.keys()))
            w.writeheader()
            w.writerow({k: ("" if v is None else v) for k, v in coded_row.items()})

    evidence = result.get("evidence_log") or []
    if evidence:
        keys = sorted({k for row in evidence for k in (row or {})})
        with (out_dir / "evidence_log.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            for row in evidence:
                w.writerow({k: ("" if (row or {}).get(k) is None else row[k]) for k in keys})

    note = result.get("uncertainty_note") or ""
    if note:
        (out_dir / "uncertainty_note.md").write_text(note, encoding="utf-8")


async def main(args: argparse.Namespace) -> int:
    """Run the campaign research pipeline with the given arguments."""
    _load_env()

    from sar.cli import build_llm
    from sar.config import AppConfig
    from sar.contracts.campaign import CampaignSpec
    from sar.events import EventBus
    from sar.orchestrator.runner import run_campaign_pipeline
    from sar.persistence.audit import AuditLog

    try:
        config = AppConfig()
    except Exception as exc:
        print(f"Failed to load AppConfig: {exc}", file=sys.stderr)
        print("Hint: the v2 config reads env vars prefixed SAR_ (e.g. "
              "SAR_OPENAI_API_KEY). Set them in tools/agentic_sar_v2/.env.",
              file=sys.stderr)
        return 1

    campaign_id = _slug(f"{args.country}_{args.year_start}_{args.campaign_name}")
    priority = [f.strip() for f in (args.focus_fields or "").split(",") if f.strip()]
    spec = CampaignSpec(
        campaign_id=campaign_id,
        campaign_name=args.campaign_name,
        country=args.country,
        year=args.year_start,
        description=args.notes,
        priority_fields=priority,
    )

    run_id = f"campaign-{campaign_id}-{uuid.uuid4().hex[:8]}"
    audit_log = AuditLog(run_id=run_id, log_dir=config.AUDIT_LOG_DIR)

    llm = build_llm(config, args.backend, args.model)
    search_engine = _build_search_engine(config)
    event_bus = EventBus()

    result = await run_campaign_pipeline(
        campaign_spec=spec,
        config=config,
        search_engine=search_engine,
        llm=llm,
        audit_log=audit_log,
        event_bus=event_bus,
    )

    summary = {
        "run_id": run_id,
        "campaign_id": campaign_id,
        "campaign_name": args.campaign_name,
        "country": args.country,
        "year_start": args.year_start,
        "included": (result.get("inclusion_memo") or {}).get("included"),
        "evidence_log_rows": len(result.get("evidence_log") or []),
        "citations_used": len(result.get("citations_used") or []),
        "audit_log": str(audit_log.log_path),
    }

    if args.save_dir:
        save_dir = Path(args.save_dir)
        _save_dossier(save_dir, result)
        summary["saved_to"] = str(save_dir)

    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    parser = build_parser()
    sys.exit(asyncio.run(main(parser.parse_args())))
