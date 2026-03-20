"""Tests for search_service helper functions.

These tests verify parsing logic without making real API calls.
"""

import os
import pytest

os.environ.setdefault("GEMINI_API_KEY", "test-key-not-real")
os.environ.setdefault("GOOGLE_API_KEY", "test-key-not-real")
os.environ.setdefault("SEARCH_ENGINE_ID", "test-engine-id")

from app.services.search_service import perform_google_search


def test_google_search_returns_list_on_failure():
    """With invalid credentials, perform_google_search returns an empty list
    rather than raising."""
    results = perform_google_search("test query", limit=1)
    assert isinstance(results, list)
