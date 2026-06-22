"""Shared pytest fixtures."""
import os
import sys

# Ensure the backend package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Minimal env so config classes don't blow up
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ADMIN_USER", "testadmin")
os.environ.setdefault("ADMIN_PASSWORD", "testpass123")
os.environ.setdefault("TQDM_DISABLE", "1")
os.environ.setdefault("CACHE_ENABLED", "false")
os.environ.setdefault("SKIP_STARTUP_HOOKS", "1")

import pytest
from app import create_app


@pytest.fixture(scope="session")
def app():
    """Create application for testing."""
    application = create_app("testing")
    application.config["TESTING"] = True
    return application


@pytest.fixture
def client(app):
    """A test client for the app."""
    return app.test_client()
