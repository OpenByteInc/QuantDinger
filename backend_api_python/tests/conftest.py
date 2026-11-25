"""Shared pytest fixtures."""
import asyncio
import os
import sys

# Ensure the backend package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Minimal env so config classes don't blow up
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-unit-tests-32-bytes-minimum")
os.environ.setdefault("ADMIN_USER", "testadmin")
os.environ.setdefault("ADMIN_PASSWORD", "testpass123")
os.environ.setdefault("TQDM_DISABLE", "1")
os.environ.setdefault("CACHE_ENABLED", "false")
os.environ.setdefault("SKIP_STARTUP_HOOKS", "1")
# Direct runtime unit tests execute the same handler-loading path as the
# credential-free strategy worker. Production processes never set this flag.
os.environ.setdefault("QD_STRATEGY_RUNTIME_WORKER", "1")

# Some optional trading dependencies still call asyncio.get_event_loop() at
# import time. Python 3.13 warns when no current loop exists, so provide one for
# the test session before importing the app.
try:
    asyncio.get_running_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

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


@pytest.fixture
def equity_market_clock(monkeypatch):
    """Keep broker submission tests independent of the machine's wall clock."""
    from datetime import datetime, timezone
    from app.services import market_schedule

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 25, 15, tzinfo=timezone.utc).astimezone(tz or timezone.utc)

    monkeypatch.setattr(market_schedule, "datetime", Clock)
