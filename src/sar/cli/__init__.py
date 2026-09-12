"""Command-line entry points: research, batch, campaign.

Each module defers its ``sar.*`` imports until after ``_load_env()`` so that
``.env`` is loaded before any config object reads the environment.
"""


def resolve_backend(cli_choice: str | None) -> str:
    """The LLM backend for this invocation: CLI flag beats environment."""
    import os

    return (cli_choice or os.environ.get("SAR_LLM_BACKEND") or "openai").lower()


def build_llm(config, backend: str | None, model: str | None):
    """Construct the LLM client for a CLI run.

    The model default depends on the backend: the OpenAI backend uses the
    config's fast model, the codex backend uses its own default slug.
    """
    from sar.llm import build_client

    choice = resolve_backend(backend)
    if model is None and choice == "openai":
        model = getattr(config, "OPENAI_MODEL_FAST", None)
    return build_client(
        choice,
        api_key=getattr(config, "OPENAI_API_KEY", "") or None,
        model=model,
    )


def keyed_backends(config) -> list:
    """Backends that need credentials, included only when configured."""
    extras = []
    if getattr(config, "BRAVE_API_KEY", ""):
        from sar.search.backends import BraveBackend

        extras.append(BraveBackend(api_key=config.BRAVE_API_KEY))
    if getattr(config, "UNPAYWALL_EMAIL", ""):
        from sar.search.backends import UnpaywallBackend

        extras.append(UnpaywallBackend(email=config.UNPAYWALL_EMAIL))
    return extras


#: Keyless backends, in registration order. Order is meaningful: the search
#: engine keeps the first result seen for a URL, so earlier entries win
#: deduplication.
KEYLESS_BACKENDS = (
    "duckduckgo",
    "wikipedia",
    "crossref",
    "semantic_scholar",
    "arxiv",
    "pubmed",
)


def build_content_fetcher(config):
    """Build the fetcher that turns search hits into full document text.

    Returns None when fetching is disabled, in which case readers see only
    backend snippets. Wired here rather than in each CLI so every entry point
    gets the same behaviour.
    """
    if not getattr(config, "FETCH_CONTENT", True):
        return None

    from sar.search.fetch import ContentFetcher

    cache = None
    db_path = getattr(config, "CACHE_DB_PATH", "")
    if db_path:
        try:
            from pathlib import Path

            from sar.search.cache import URLCache

            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            cache = URLCache(
                db_path=db_path,
                ttl_days=getattr(config, "URL_CACHE_TTL_DAYS", 7),
            )
        except Exception:  # pragma: no cover - caching is best-effort
            cache = None

    return ContentFetcher(
        cache=cache,
        use_playwright=getattr(config, "FETCH_USE_PLAYWRIGHT", True),
    )


def build_search_engine(config=None, backend_names: list[str] | None = None):
    """Construct the search engine used by every entry point.

    This was previously duplicated in each of the three CLI modules and again
    in the web server, which is how the CLI and the web layer drifted into
    querying different source sets for the same question.
    """
    from sar.search.backends import get_backend
    from sar.search.engine import SearchEngine

    names = list(backend_names) if backend_names else list(KEYLESS_BACKENDS)
    backends = []
    for name in names:
        cls = get_backend(name)
        if cls is not None:
            backends.append(cls())

    if config is not None and not backend_names:
        backends.extend(keyed_backends(config))

    return SearchEngine(
        backends=backends,
        fetcher=build_content_fetcher(config) if config is not None else None,
    )
