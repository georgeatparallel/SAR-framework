"""Tests for the CLI construction surface.

Every entry point -- `sar research`, `sar campaign`, `sar batch`, `sar doctor`
-- had zero coverage, so nothing checked that the objects a run depends on are
actually assembled. This is where the search engine gets its backends and its
content fetcher; a regression here degrades every run silently rather than
failing.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from sar.cli import (
    KEYLESS_BACKENDS,
    build_content_fetcher,
    build_search_engine,
    keyed_backends,
    resolve_backend,
)
from sar.search.backends import BACKEND_NAMES, get_backend


def _config(**overrides):
    """A config stub carrying only what the builders read."""
    base = {
        "CACHE_DB_PATH": "",
        "URL_CACHE_TTL_DAYS": 7,
        "FETCH_CONTENT": True,
        "FETCH_USE_PLAYWRIGHT": False,
        "BRAVE_API_KEY": "",
        "UNPAYWALL_EMAIL": "",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class TestBackendRegistry:
    def test_every_keyless_backend_resolves(self):
        """The default registration list must not name a backend that is gone."""
        for name in KEYLESS_BACKENDS:
            assert get_backend(name) is not None, name

    def test_unknown_backend_returns_none(self):
        assert get_backend("not_a_backend") is None

    def test_registry_covers_all_shipped_backends(self):
        assert set(BACKEND_NAMES) >= set(KEYLESS_BACKENDS)


class TestBuildSearchEngine:
    def test_default_registers_the_keyless_set_in_order(self):
        engine = build_search_engine(config=_config())
        assert [b.name for b in engine._backends] == list(KEYLESS_BACKENDS)

    def test_explicit_names_are_honoured(self):
        engine = build_search_engine(config=_config(), backend_names=["crossref", "arxiv"])
        assert [b.name for b in engine._backends] == ["crossref", "arxiv"]

    def test_unknown_names_are_dropped_not_fatal(self):
        engine = build_search_engine(config=_config(), backend_names=["crossref", "nope"])
        assert [b.name for b in engine._backends] == ["crossref"]

    def test_keyed_backends_join_only_when_configured(self):
        plain = build_search_engine(config=_config())
        keyed = build_search_engine(config=_config(BRAVE_API_KEY="x"))
        assert "brave" not in [b.name for b in plain._backends]
        assert "brave" in [b.name for b in keyed._backends]

    def test_explicit_names_suppress_keyed_extras(self):
        """An explicit list means exactly that list."""
        engine = build_search_engine(
            config=_config(BRAVE_API_KEY="x"), backend_names=["crossref"]
        )
        assert [b.name for b in engine._backends] == ["crossref"]

    def test_engine_gets_a_fetcher(self):
        """Without this the pipeline reads snippets instead of documents."""
        engine = build_search_engine(config=_config())
        assert engine._fetcher is not None

    def test_fetching_can_be_disabled(self):
        engine = build_search_engine(config=_config(FETCH_CONTENT=False))
        assert engine._fetcher is None


class TestBuildContentFetcher:
    def test_no_cache_when_no_db_path(self):
        assert build_content_fetcher(_config()).cache is None

    def test_cache_is_created_under_the_configured_path(self, tmp_path):
        db = tmp_path / "nested" / "cache.db"
        fetcher = build_content_fetcher(_config(CACHE_DB_PATH=str(db)))
        assert fetcher.cache is not None
        assert db.parent.is_dir()

    def test_playwright_flag_is_passed_through(self):
        assert build_content_fetcher(_config(FETCH_USE_PLAYWRIGHT=False)).use_playwright is False


class TestKeyedBackends:
    def test_empty_without_credentials(self):
        assert keyed_backends(_config()) == []

    def test_unpaywall_needs_an_email(self):
        names = [b.name for b in keyed_backends(_config(UNPAYWALL_EMAIL="a@b.c"))]
        assert "unpaywall" in names


class TestResolveBackend:
    def test_cli_choice_wins(self, monkeypatch):
        monkeypatch.setenv("SAR_LLM_BACKEND", "codex")
        assert resolve_backend("openai") == "openai"

    def test_environment_is_used_when_no_flag(self, monkeypatch):
        monkeypatch.setenv("SAR_LLM_BACKEND", "codex")
        assert resolve_backend(None) == "codex"

    def test_defaults_to_openai(self, monkeypatch):
        monkeypatch.delenv("SAR_LLM_BACKEND", raising=False)
        assert resolve_backend(None) == "openai"


class TestDoctor:
    def test_doctor_runs_and_returns_an_exit_code(self, monkeypatch, capsys):
        from sar.__main__ import _doctor

        monkeypatch.setenv("SAR_LLM_BACKEND", "openai")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        code = _doctor()
        out = capsys.readouterr().out
        assert code == 0
        assert "selected backend" in out
        assert "version" in out

    def test_doctor_fails_when_selected_backend_unusable(self, monkeypatch, capsys):
        from sar.__main__ import _doctor

        monkeypatch.setenv("SAR_LLM_BACKEND", "openai")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setattr("sar.cli.research._load_env", lambda: None)
        assert _doctor() == 1

    def test_doctor_creates_no_directories(self, monkeypatch, tmp_path):
        """A diagnostic must not leave state behind in the caller's cwd."""
        from sar.__main__ import _doctor

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("sar.cli.research._load_env", lambda: None)
        _doctor()
        assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("module", ["research", "campaign", "batch"])
def test_every_cli_builds_a_parser(module):
    """Each entry point must at least import and expose its parser."""
    import importlib

    mod = importlib.import_module(f"sar.cli.{module}")
    parser = mod.build_parser()
    assert parser.format_help()
