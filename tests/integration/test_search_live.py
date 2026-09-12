"""Live integration tests for search backends.

These tests hit real external APIs and are marked slow.
Run with: pytest -m slow
"""

import pytest

pytestmark = pytest.mark.slow

# TODO(tests): test SemanticScholarBackend.search returns results for known query
# TODO(tests): test ArxivBackend.search returns results for known query
# TODO(tests): test CrossRefBackend.search returns results with DOIs
# TODO(tests): test PubMedBackend.search returns results for medical query
# TODO(tests): test BraveBackend.search returns web results (requires BRAVE_API_KEY)
# TODO(tests): test DuckDuckGoBackend.search returns results
# TODO(tests): test WikipediaBackend.search returns results
# TODO(tests): test SearchEngine.search aggregates from multiple backends
# TODO(tests): test SearchEngine.search deduplicates real results
