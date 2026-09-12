"""Console entry point: ``sar`` / ``python -m sar``.

Subcommands wrap the CLI modules in :mod:`sar.cli`. ``doctor`` reports what
this environment can run, so a broken setup fails at diagnosis rather than
mid-pipeline.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import importlib.util
import os
import shutil
import sys


def _version() -> str:
    try:
        from importlib.metadata import version

        return version("agentic-sar")
    except Exception:
        return "2.0.0 (uninstalled source tree)"


def _doctor() -> int:
    """Report backend and dependency readiness. Exit 0 if the selected
    backend is runnable, 1 otherwise."""
    from sar.cli.research import _load_env

    # Load .env exactly as the subcommands do. Reading only os.environ made
    # doctor report "not set" and exit 1 in a directory where `sar research`
    # ran fine -- a false negative that sent people hunting the wrong problem.
    _load_env()

    from sar.cli import resolve_backend

    print(f"version          : {_version()}")
    backend = resolve_backend(None)
    print(f"selected backend : {backend}  (SAR_LLM_BACKEND or default)")

    ok = True
    codex = shutil.which("codex")
    print(f"codex binary     : {codex or 'not on PATH'}")
    key = bool(os.environ.get("OPENAI_API_KEY"))
    print(f"OPENAI_API_KEY   : {'set' if key else 'not set'}")
    if backend == "codex" and not codex:
        print("  -> codex backend selected but no codex binary", file=sys.stderr)
        ok = False
    if backend == "openai" and not key:
        print("  -> openai backend selected but no key", file=sys.stderr)
        ok = False

    for extra, mods in [
        ("openai", ["openai"]),
        ("scrape", ["bs4", "playwright"]),
        ("pdf", ["fitz"]),
        ("web", ["fastapi", "uvicorn"]),
    ]:
        present = all(importlib.util.find_spec(m) is not None for m in mods)
        print(f"extra [{extra:7s}] : {'installed' if present else 'not installed'}")

    # Search-side readiness. keyed_backends() drops Brave and Unpaywall when
    # their credentials are absent, so a run silently loses those sources; and
    # the default DuckDuckGo backend needs a package that is easy to miss.
    for label, mod in [("ddgs (duckduckgo)", "ddgs")]:
        present = importlib.util.find_spec(mod) is not None
        print(f"{label:17s}: {'installed' if present else 'MISSING -- web search disabled'}")
    for var in ("SAR_BRAVE_API_KEY", "BRAVE_API_KEY", "SAR_UNPAYWALL_EMAIL",
                "UNPAYWALL_EMAIL", "SAR_SEMANTIC_SCHOLAR_API_KEY"):
        if os.environ.get(var):
            print(f"{var:17s}: set")

    # Playwright ships a Python package and, separately, browser binaries.
    # find_spec only proves the former, so a fetcher can still fail to render.
    if importlib.util.find_spec("playwright") is not None:
        from pathlib import Path as _Path

        browsers = _Path.home() / ".cache" / "ms-playwright"
        have_browsers = browsers.is_dir() and any(browsers.iterdir())
        print(
            f"{'playwright':17s}: package installed, browsers "
            f"{'present' if have_browsers else 'NOT installed (run: playwright install chromium)'}"
        )

    # The fetcher and audit trail both need writable directories.
    try:
        from pathlib import Path as _P

        from sar.config import AppConfig

        cfg = AppConfig()
        for label, target in (
            ("cache db", _P(cfg.CACHE_DB_PATH).parent),
            ("audit logs", _P(getattr(cfg, "AUDIT_LOG_DIR", "data/audit_logs"))),
        ):
            # Check, never create: a diagnostic must not leave directories
            # behind in whatever cwd it was run from.
            probe = target
            while not probe.exists() and probe != probe.parent:
                probe = probe.parent
            writable = os.access(probe, os.W_OK)
            state = "writable" if writable else "NOT WRITABLE"
            if not target.exists():
                state += " (will be created)"
            print(f"{label:17s}: {target} {state}")
            if not writable:
                ok = False
    except Exception as exc:  # pragma: no cover - diagnostics must not crash
        print(f"{'paths':17s}: could not verify ({exc})")

    if backend == "codex":
        default_model = os.environ.get("SAR_CODEX_MODEL", "gpt-5.6-terra")
    else:
        default_model = os.environ.get("OPENAI_MODEL_FAST", "gpt-4o-mini")
    print(f"model default    : {default_model}")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="sar",
        description="Search, Analyze, Review: a research pipeline with an audit trail.",
    )
    parser.add_argument("--version", action="version", version=f"sar {_version()}")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("research", add_help=False,
                   help="Answer one research question (see: sar research --help).")
    sub.add_parser("batch", add_help=False,
                   help="Run campaign coding over a dataset slice.")
    sub.add_parser("campaign", add_help=False,
                   help="Code a single campaign into a dossier.")
    sub.add_parser("doctor", help="Check binaries, keys, and optional extras.")

    argv = list(sys.argv[1:] if argv is None else argv)
    # Split off the subcommand so its own parser owns the remaining flags,
    # including --help.
    args, rest = parser.parse_known_args(argv)
    if args.command is None:
        parser.print_help()
        return 2
    if args.command == "doctor":
        return _doctor()

    module = importlib.import_module(f"sar.cli.{args.command}")
    sub_args = module.build_parser().parse_args(rest)
    return asyncio.run(module.main(sub_args))


if __name__ == "__main__":
    sys.exit(main())
