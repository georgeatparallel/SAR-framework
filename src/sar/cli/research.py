"""CLI entry point for generic literature research.

Parses command-line arguments, wires up services, and runs the
research pipeline. Streams progress to stdout via the EventBus.

Usage:
    python -m cli.research "bots social media collective action"
    python -m cli.research --verbose --save-report report.md "query"
    python -m cli.research --context "empirical only" "query"

Migrated from: agentic_sar/research.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
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
    """Build the argument parser for the research CLI."""
    p = argparse.ArgumentParser(
        description="Run the v2 SAR generic-research pipeline.",
    )
    p.add_argument("query", help="Research question.")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="Stream EventBus events to stdout.")
    p.add_argument("-c", "--context", default="",
                   help="Additional context for the query expander.")
    p.add_argument("--save-report", default="",
                   help="Path to write the markdown report.")
    p.add_argument("--max-iterations", type=int, default=None,
                   help="Override AppConfig.MAX_ITERATIONS.")
    p.add_argument("--backends", default="",
                   help="Comma-separated backend names (default: all keyless).")
    p.add_argument("--backend", default=None, choices=["openai", "codex"],
                   help="LLM backend (default: $SAR_LLM_BACKEND, else openai).")
    p.add_argument("--model", default=None,
                   help="Model identifier override for the chosen backend.")
    return p


def _build_search_engine(backend_names: list[str] | None, config=None):
    from sar.search.backends import (
        ArxivBackend,
        CrossRefBackend,
        DuckDuckGoBackend,
        PubMedBackend,
        SemanticScholarBackend,
        WikipediaBackend,
    )
    from sar.search.engine import SearchEngine

    available = {
        "duckduckgo": DuckDuckGoBackend,
        "wikipedia": WikipediaBackend,
        "crossref": CrossRefBackend,
        "semantic_scholar": SemanticScholarBackend,
        "arxiv": ArxivBackend,
        "pubmed": PubMedBackend,
    }
    # Keep optional providers out of the default fan-out, even when installed.
    if backend_names and "parallel" in backend_names:
        from sar.search.backends import ParallelBackend

        available["parallel"] = ParallelBackend
    if backend_names:
        backends = [available[n]() for n in backend_names if n in available]
    else:
        backends = [cls() for cls in available.values()]
    if config is not None and not backend_names:
        from sar.cli import keyed_backends

        backends.extend(keyed_backends(config))
    return SearchEngine(backends=backends)


def _make_cli_event_handler(verbose: bool):
    """Build a handler that prints v2 events to stdout when --verbose."""
    if not verbose:
        return lambda _event: None

    def handler(event):
        et = getattr(event, "event_type", None)
        et_name = getattr(et, "name", str(et))
        data = getattr(event, "data", {}) or {}
        # Compact printable representation.
        try:
            payload = json.dumps(data, default=str)[:240]
        except Exception:
            payload = str(data)[:240]
        print(f"[event] {et_name}  {payload}", file=sys.stderr)

    return handler


async def main(args: argparse.Namespace) -> int:
    """Run the research pipeline with the given arguments."""
    _load_env()

    from sar.cli import build_llm
    from sar.config import AppConfig
    from sar.events import EventBus
    from sar.orchestrator.runner import run_pipeline
    from sar.persistence.audit import AuditLog

    try:
        config = AppConfig()
    except Exception as exc:
        print(f"Failed to load AppConfig: {exc}", file=sys.stderr)
        return 1

    if args.max_iterations is not None:
        # AppConfig is pydantic; copy with override.
        import contextlib

        with contextlib.suppress(Exception):
            config = config.model_copy(
                update={"MAX_ITERATIONS": args.max_iterations}
            )

    backend_names = [b.strip() for b in args.backends.split(",") if b.strip()]
    search_engine = _build_search_engine(backend_names or None, config)
    llm = build_llm(config, args.backend, args.model)

    run_id = f"research-{uuid.uuid4().hex[:8]}"
    audit_log = AuditLog(run_id=run_id, log_dir=config.AUDIT_LOG_DIR)

    event_bus = EventBus()
    event_bus.subscribe(None, _make_cli_event_handler(args.verbose))

    query = args.query
    if args.context:
        query = f"{query}\n\nContext: {args.context}"

    result = await run_pipeline(
        query=query,
        config=config,
        search_engine=search_engine,
        llm=llm,
        audit_log=audit_log,
        event_bus=event_bus,
    )

    report = getattr(result, "report", "") or ""
    if args.save_report:
        out = Path(args.save_report)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report, encoding="utf-8")
        print(f"Report saved to {out}", file=sys.stderr)
    else:
        print(report)

    summary = {
        "run_id": run_id,
        "iterations": getattr(result, "iterations_used", None),
        "summaries": len(getattr(result, "summaries", []) or []),
        "audit_log": str(audit_log.log_path),
    }
    print(json.dumps(summary, indent=2, default=str), file=sys.stderr)
    return 0


def cli_event_handler(event: object) -> None:
    """Default verbose printer (also exposed for external subscribers)."""
    _make_cli_event_handler(True)(event)


if __name__ == "__main__":
    parser = build_parser()
    sys.exit(asyncio.run(main(parser.parse_args())))
