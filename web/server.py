"""Optional web UI entry point for the agentic SAR pipeline.

Provides a FastAPI application that exposes the research and campaign
pipelines as HTTP endpoints.  This is a thin serving layer — all
business logic lives in ``src/sar/``.

Per the project BLUEPRINT this is an *optional* component; the primary
interface is the CLI.  Start with::

    uvicorn web.server:app --reload

The module-level ``app`` instance is created by ``create_app()`` which
wires ``AppConfig``, ``SearchEngine``, ``LLMClient``, and route handlers
via a FastAPI lifespan context manager.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from sar.config import AppConfig
from sar.llm.client import OpenAIClient
from sar.search.engine import SearchEngine

logger = logging.getLogger(__name__)

# Backend registry: mapping from config name → (class, constructor kwargs builder).
# Only backends listed in ``AppConfig.SEARCH_BACKENDS`` are instantiated.
_BACKEND_REGISTRY: dict[str, tuple[type, ...]] = {}


def _build_search_engine(config: AppConfig) -> SearchEngine:
    """Instantiate a SearchEngine with the backends listed in config.

    Backends whose required API key is empty are skipped with a warning.

    Args:
        config: Application configuration with SEARCH_BACKENDS list
            and API key fields.

    Returns:
        Configured SearchEngine ready for queries.
    """
    from sar.search.backends import (
        ArxivBackend,
        BraveBackend,
        CrossRefBackend,
        DuckDuckGoBackend,
        PubMedBackend,
        SemanticScholarBackend,
        UnpaywallBackend,
        WikipediaBackend,
    )

    # Map config names to (class, kwargs) pairs.
    available: dict[str, tuple[type, dict[str, str]]] = {
        "semantic_scholar": (SemanticScholarBackend, {"api_key": config.SEMANTIC_SCHOLAR_API_KEY or None}),
        "arxiv": (ArxivBackend, {}),
        "crossref": (CrossRefBackend, {}),
        "pubmed": (PubMedBackend, {}),
        "brave": (BraveBackend, {"api_key": config.BRAVE_API_KEY}),
        "duckduckgo": (DuckDuckGoBackend, {}),
        "unpaywall": (UnpaywallBackend, {}),
        "wikipedia": (WikipediaBackend, {}),
    }

    engine = SearchEngine()

    for name in config.SEARCH_BACKENDS:
        entry = available.get(name)
        if entry is None:
            logger.warning("Unknown backend in SEARCH_BACKENDS: %s — skipped", name)
            continue

        cls, kwargs = entry

        # Skip backends that need an API key when none is configured.
        if name == "brave" and not config.BRAVE_API_KEY:
            logger.warning("Brave backend requested but SAR_BRAVE_API_KEY is empty — skipped")
            continue

        try:
            backend = cls(**kwargs)
            engine.register_backend(backend)
            logger.info("Registered search backend: %s", name)
        except Exception:
            logger.exception("Failed to instantiate backend %s — skipped", name)

    return engine


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Lifespan context manager: initialise shared resources on startup.

    Creates and stores ``AppConfig``, ``SearchEngine``, ``OpenAIClient``,
    and ensures the audit-log directory exists.  Resources are placed in
    ``app.state.sar`` so route handlers can access them via
    ``request.app.state.sar``.

    Args:
        app: The FastAPI application instance.

    Yields:
        Control to the running application.
    """
    config = AppConfig()  # type: ignore[call-arg]  # populated from env / .env

    # Ensure audit log directory exists.
    Path(config.AUDIT_LOG_DIR).mkdir(parents=True, exist_ok=True)

    search_engine = _build_search_engine(config)
    llm = OpenAIClient(
        api_key=config.OPENAI_API_KEY,
        default_model=config.OPENAI_MODEL_FAST,
    )

    backend_names = [b.name for b in search_engine._backends]

    app.state.sar = {
        "config": config,
        "search_engine": search_engine,
        "llm": llm,
        "backend_names": backend_names,
    }

    logger.info(
        "SAR web server started — backends: %s",
        ", ".join(backend_names) or "(none)",
    )

    yield

    logger.info("SAR web server shutting down")


def create_app() -> FastAPI:
    """Application factory: build and return a configured FastAPI instance.

    Registers the lifespan context manager and includes the route handlers
    from ``web.routes``.

    Returns:
        A FastAPI application ready to be served by uvicorn.
    """
    from web.routes import router

    application = FastAPI(
        title="Agentic SAR",
        description="Multi-agent research orchestration HTTP API.",
        version="2.0.0-dev",
        lifespan=_lifespan,
    )
    application.include_router(router)
    return application


app: FastAPI = create_app()
