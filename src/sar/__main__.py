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
    from sar.cli import resolve_backend

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
