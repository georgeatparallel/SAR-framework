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
