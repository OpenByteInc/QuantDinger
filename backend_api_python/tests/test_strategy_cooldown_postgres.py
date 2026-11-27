"""Opt-in isolated PostgreSQL checks for durable exchange recovery fences."""

import os
from contextlib import contextmanager

import pytest

from app.services import strategy_command_repository as module


@pytest.fixture
def database(monkeypatch):
    dsn = os.getenv("TEST_STRATEGY_COOLDOWN_DSN")
    if not dsn:
        pytest.skip("Set TEST_STRATEGY_COOLDOWN_DSN to an isolated test PostgreSQL")
    import psycopg2
    from psycopg2.extras import RealDictCursor

    conn = psycopg2.connect(dsn, cursor_factory=RealDictCursor)
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TEMP TABLE qd_strategies_trading (id integer PRIMARY KEY, status text, desired_runtime_epoch integer);
            CREATE TEMP TABLE strategy_runs (id integer, strategy_id integer, runtime_epoch integer, runtime_status text);
            CREATE TEMP TABLE qd_strategy_runtime_leases (strategy_id integer, fencing_token integer, lease_expires_at timestamptz);
            CREATE TEMP TABLE strategy_runtime_state (
                strategy_run_id integer, strategy_id integer, state_key text, state_json jsonb,
                version bigint DEFAULT 1, updated_at timestamptz DEFAULT NOW(),
                UNIQUE(strategy_run_id, strategy_id, state_key));
            CREATE TEMP TABLE qd_strategy_commands (
                id integer, claimed_by text, status text, result_json jsonb, attempts integer,
                available_at timestamptz, lease_expires_at timestamptz, updated_at timestamptz);
            INSERT INTO qd_strategies_trading VALUES (55, 'running', 7);
            INSERT INTO qd_strategy_runtime_leases VALUES (55, 9, NOW() + INTERVAL '1 hour');
            INSERT INTO qd_strategy_commands (id, claimed_by, status, attempts) VALUES (1, 'worker', 'processing', 3);
        """)
    conn.commit()

    @contextmanager
    def connection():
        yield conn

    monkeypatch.setattr(module, "get_db_connection", connection)
    try:
        yield conn
    finally:
        conn.close()


def test_cooldown_is_shared_longest_wins_and_old_owners_cannot_overwrite(database):
    repo = module.StrategyCommandRepository()
    repo.defer_exchange_start(55, epoch=7, token=9, seconds=1084)
    original = repo.runtime_request(55)
    assert 1080 <= original["retry_after_seconds"] <= 1084
    assert original["status"] == "running" and not original["ready"]
    repo.defer_exchange_start(55, epoch=7, token=9, seconds=60)
    assert repo.runtime_request(55)["startup_retry_at"] == original["startup_retry_at"]
    repo.defer_exchange_start(55, epoch=7, token=8, seconds=9000)
    assert repo.runtime_request(55)["startup_retry_at"] == original["startup_retry_at"]
    assert module.StrategyCommandRepository().runtime_request(55)["retry_after_seconds"] > 1000


def test_manual_stop_and_new_generation_fence_late_cooldown_writers(database):
    repo = module.StrategyCommandRepository()
    repo.defer_exchange_start(55, epoch=7, token=9, seconds=1084)
    original = repo.runtime_request(55)["startup_retry_at"]
    with database.cursor() as cur:
        cur.execute("UPDATE qd_strategies_trading SET status='stopped'")
    database.commit()
    repo.defer_exchange_start(55, epoch=7, token=9, seconds=9000)
    assert repo.runtime_request(55)["status"] == "stopped"
    assert repo.runtime_request(55)["startup_retry_at"] == original
    with database.cursor() as cur:
        cur.execute("UPDATE qd_strategies_trading SET status='running', desired_runtime_epoch=8")
    database.commit()
    repo.defer_exchange_start(55, epoch=7, token=9, seconds=9000)
    assert repo.runtime_request(55)["retry_after_seconds"] == 0
    repo.defer_exchange_start(55, epoch=8, token=9, seconds=10)
    assert 0 < repo.runtime_request(55)["retry_after_seconds"] <= 10


def test_deferred_start_keeps_attempt_budget_and_waits_until_retry(database):
    repo = module.StrategyCommandRepository()
    repo.defer_start(1, owner_id="worker", result={"status": "starting", "retry_after_seconds": 1084})
    with database.cursor() as cur:
        cur.execute("SELECT *, EXTRACT(EPOCH FROM available_at - NOW()) AS remaining FROM qd_strategy_commands")
        row = cur.fetchone()
    assert row["status"] == "pending" and row["attempts"] == 2
    assert row["lease_expires_at"] is None
    assert 1080 < row["remaining"] <= 1084
