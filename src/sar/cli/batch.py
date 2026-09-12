"""CLI entry point for batch campaign verification.

Runs the campaign pipeline across a master dataset, compares outputs
against baseline values, and generates diff reports.

Usage:
    python -m cli.batch \\
        --dataset external_data/campaigns/master_dataset.csv \\
        --output-root ./batch_output

    python -m cli.batch --slug hong-kong-2014-umbrella --overwrite
    python -m cli.batch --dry-run --limit 5
    python -m cli.batch --rebuild-index --output-root ./batch_output

Migrated from: agentic_sar/run_campaign_batch_verification.py
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
from pathlib import Path

from dotenv import load_dotenv


def _load_env() -> None:
    # Installed package: honour the invoking directory's .env first. The
    # source-tree fallback (four levels up from src/sar/cli/) supports
    # running from a checkout without an install.
    if not load_dotenv(Path.cwd() / ".env"):
        load_dotenv(Path(__file__).resolve().parents[3] / ".env")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Run the v2 SAR campaign pipeline across a master dataset.",
    )
    p.add_argument("--dataset", default="",
                   help="Path to master_dataset.csv (required unless --rebuild-index).")
    p.add_argument("--output-root", required=True,
                   help="Directory for per-campaign output + batch manifest.")
    p.add_argument("--slug", default="",
                   help="Only run this campaign slug.")
    p.add_argument("--country", default="",
                   help="Filter by country (case-insensitive).")
    p.add_argument("--limit", type=int, default=None,
                   help="Max campaigns to process after filters.")
    p.add_argument("--offset", type=int, default=0,
                   help="Skip this many campaigns before starting.")
    p.add_argument("--backend", default=None, choices=["openai", "codex"],
                   help="LLM backend (default: $SAR_LLM_BACKEND, else openai).")
    p.add_argument("--model", default=None,
                   help="Model identifier override for the chosen backend.")
    p.add_argument("--overwrite", action="store_true",
                   help="Re-run even if output exists.")
    p.add_argument("--dry-run", action="store_true",
                   help="Print campaign list (after filters) and exit.")
    p.add_argument("--rebuild-index", action="store_true",
                   help="Rebuild summary CSVs from existing per-campaign outputs.")
    return p


def _build_search_engine(config):
    from sar.cli import build_search_engine

    return build_search_engine(config=config)


def _dry_run_list(dataset_path: Path, slug: str, country: str,
                  limit: int | None, offset: int) -> int:
    from sar.campaign.batch import build_campaign_spec_from_row

    with dataset_path.open("r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    specs = []
    for row in rows:
        spec = build_campaign_spec_from_row(row)
        if slug and spec["slug"] != slug:
            continue
        if country and spec["country"].lower() != country.lower():
            continue
        specs.append(spec)
    specs = specs[offset:]
    if limit is not None:
        specs = specs[:limit]
    print(json.dumps(
        [{"slug": s["slug"], "country": s["country"], "year": s.get("year_start")}
         for s in specs],
        indent=2, default=str))
    print(f"\n{len(specs)} campaigns selected.", file=sys.stderr)
    return 0


async def main(args: argparse.Namespace) -> int:
    _load_env()

    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    if args.rebuild_index:
        from sar.campaign.batch import rebuild_batch_index
        rebuild_batch_index(output_root)
        print(f"Rebuilt summary index at {output_root}")
        return 0

    if not args.dataset:
        print("--dataset is required unless --rebuild-index is set.", file=sys.stderr)
        return 2

    dataset_path = Path(args.dataset)
    if not dataset_path.exists():
        print(f"Dataset not found: {dataset_path}", file=sys.stderr)
        return 2

    if args.dry_run:
        return _dry_run_list(
            dataset_path, args.slug, args.country, args.limit, args.offset,
        )

    from sar.campaign.batch import run_batch
    from sar.cli import build_llm
    from sar.config import AppConfig

    try:
        config = AppConfig()
    except Exception as exc:
        print(f"Failed to load AppConfig: {exc}", file=sys.stderr)
        return 1

    llm = build_llm(config, args.backend, args.model)
    search_engine = _build_search_engine(config)

    manifest = await run_batch(
        dataset_path=dataset_path,
        output_root=output_root,
        config=config,
        search_engine=search_engine,
        llm=llm,
        slug_filter=args.slug or None,
        country_filter=args.country or None,
        limit=args.limit,
        offset=args.offset,
        overwrite=args.overwrite,
    )
    print(json.dumps({
        "run_id": manifest.get("run_id"),
        "completed_at": manifest.get("completed_at"),
        "totals": manifest.get("totals"),
    }, indent=2, default=str))
    return 0


if __name__ == "__main__":
    parser = build_parser()
    sys.exit(asyncio.run(main(parser.parse_args())))
