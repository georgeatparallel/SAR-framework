"""Centralized application configuration.

Single source of truth for all settings. Loads from environment variables
with validation via Pydantic BaseSettings. Instantiate once at startup
and inject into services.

Migrated from: agentic_sar/config.py (v1 os.getenv with scattered defaults)
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppConfig(BaseSettings):
    """Application configuration validated at startup.

    All settings are loaded from environment variables prefixed with ``SAR_``
    and/or from a ``.env`` file. No setting should be accessed via
    ``os.getenv()`` anywhere else in the codebase -- inject this config
    object instead.

    Attributes:
        OPENAI_API_KEY: OpenAI API key (required).
        BRAVE_API_KEY: Brave Search API key (optional, enables Brave backend).
        SEMANTIC_SCHOLAR_API_KEY: Semantic Scholar API key (optional, raises rate limits).
        UNPAYWALL_EMAIL: Email for Unpaywall polite pool (optional).
        OPENAI_MODEL_FAST: Model identifier for fast/cheap LLM calls.
        OPENAI_MODEL_PRO: Model identifier for high-quality LLM calls.
        MAX_ITERATIONS: Maximum pipeline loop iterations before forced stop.
        MIN_RESULTS_TO_PROCEED: Minimum search results required to proceed past SEARCH phase.
        QUALITY_THRESHOLD: Minimum average quality score for paper summaries (0-10).
        BUDGET_CEILING_USD: Maximum allowed LLM spend per pipeline run in USD.
        FETCH_CONTENT: Whether search results are enriched with full document
            text. With this off the reader sees only backend snippets.
        FETCH_USE_PLAYWRIGHT: Whether to render JS-heavy pages with a headless
            browser when direct HTTP yields too little text.
        URL_CACHE_TTL_DAYS: Time-to-live for URL content cache entries in days.
        CACHE_DB_PATH: Path to the SQLite cache database.
        RETRY_MAX_ATTEMPTS: Maximum number of retry attempts for transient failures.
        RETRY_BASE_DELAY: Base delay in seconds for exponential backoff.
        RETRY_MAX_DELAY: Maximum delay in seconds between retries.
        AUDIT_LOG_DIR: Directory for audit log files.
        SEARCH_BACKENDS: Ordered list of search backend names to use.
        SEARCH_RESULTS_PER_BACKEND: Maximum results to request from each backend.
    """

    model_config = SettingsConfigDict(env_file=".env", env_prefix="SAR_")

    # --- API keys ---
    # Optional so that codex-backend runs need no key; build_client() raises
    # a clear error if the openai backend is selected without one.
    OPENAI_API_KEY: str = ""
    BRAVE_API_KEY: str = ""
    SEMANTIC_SCHOLAR_API_KEY: str = ""
    UNPAYWALL_EMAIL: str = ""

    # --- Model selection ---
    OPENAI_MODEL_FAST: str = "gpt-4o-mini"
    OPENAI_MODEL_PRO: str = "gpt-4o"

    # --- Pipeline tuning ---
    MAX_ITERATIONS: int = 3
    # Rework bound per expansion round: the ANALYZE <-> REVIEW cycle does not
    # advance the iteration counter, so it needs its own guard.
    MAX_REWORKS_PER_ITERATION: int = 2
    # Cost ceiling for metered backends (USD). None keeps TokenBudget's default.
    BUDGET_CEILING_USD: float | None = None
    MIN_RESULTS_TO_PROCEED: int = 3
    QUALITY_THRESHOLD: float = 6.0

    # --- Cache ---
    FETCH_CONTENT: bool = True
    FETCH_USE_PLAYWRIGHT: bool = True
    URL_CACHE_TTL_DAYS: int = 7
    CACHE_DB_PATH: str = "data/cache.db"

    # --- Retry policy ---
    RETRY_MAX_ATTEMPTS: int = 3
    RETRY_BASE_DELAY: float = 1.0
    RETRY_MAX_DELAY: float = 30.0

    # --- Audit ---
    AUDIT_LOG_DIR: str = "data/audit_logs"

    # --- Search ---
    SEARCH_BACKENDS: list[str] = Field(
        default=["semantic_scholar", "crossref", "brave"]
    )
    SEARCH_RESULTS_PER_BACKEND: int = 10
