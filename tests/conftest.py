import os
import pytest

# Set dummy env vars before any app imports so module-level config doesn't crash
os.environ.setdefault("GEMINI_API_KEY", "test-key-not-real")
os.environ.setdefault("GOOGLE_API_KEY", "test-key-not-real")
os.environ.setdefault("SEARCH_ENGINE_ID", "test-engine-id")

from app import create_app


@pytest.fixture
def app():
    """Create a Flask app configured for testing."""
    app = create_app("testing")
    yield app


@pytest.fixture
def client(app):
    """Flask test client."""
    return app.test_client()
