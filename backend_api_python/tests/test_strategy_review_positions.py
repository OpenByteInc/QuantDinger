"""Exercise review position loading against the shipped PostgreSQL table DDL."""

from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
import os
from pathlib import Path
import re

import pytest

from app.services import strategy_review, virtual_trading
from app.services.strategy_review import StrategyReviewService
from app.utils.db_postgres import PostgresCursor


@pytest.fixture
def position_database(monkeypatch):
    dsn = os.getenv("QD_TEST_POSTGRES_DSN") or os.getenv("DATABASE_URL")
    if not dsn:
        pytest.skip("A test PostgreSQL DSN is required")
    psycopg2 = pytest.importorskip("psycopg2")
    from psycopg2.extras import RealDictCursor

    connection = psycopg2.connect(dsn)
    try:
        # Temporary tables shadow application tables and disappear on close.
        # Read the canonical DDL so a permissive SQL mock cannot hide a bad column.
        schema = (Path(__file__).resolve().parents[1] / "migrations/init.sql").read_text()
        with connection.cursor() as cursor:
            for table in ("qd_users", "qd_strategies_trading", "qd_strategy_positions"):
                statement = re.search(
                    rf"CREATE TABLE IF NOT EXISTS {table} \(.*?\n\);", schema, re.DOTALL
                )
                assert statement, f"Missing canonical table definition: {table}"
                cursor.execute(statement.group().replace("CREATE TABLE IF NOT EXISTS", "CREATE TEMP TABLE"))
            cursor.execute("INSERT INTO qd_users (id, username, password_hash) VALUES (1, 'review-test', 'unused')")
            cursor.execute(
                "INSERT INTO qd_strategies_trading (id, strategy_name) VALUES (10, 'review'), (11, 'other')"
            )
            cursor.execute(
                """
                INSERT INTO qd_strategy_positions
                    (strategy_id, symbol, side, size, entry_price, current_price, unrealized_pnl, updated_at)
                VALUES (10, 'BTC/USDT', 'long', 0.5, 100, 120, 10, '2026-01-01 12:00:00'),
                       (11, 'ETH/USDT', 'long', 1, 100, 125, 25, '2026-01-01 12:00:00')
                """
            )

        class TestConnection:
            def cursor(self):
                return PostgresCursor(connection.cursor(cursor_factory=RealDictCursor))

        @contextmanager
        def connect():
            yield TestConnection()

        monkeypatch.setattr(strategy_review, "get_db_connection", connect)
        yield
    finally:
        connection.rollback()
        connection.close()


def test_live_positions_reach_report_and_ai_input(position_database, monkeypatch):
    service = StrategyReviewService()
    monkeypatch.setattr(service.strategy_service, "get_strategy", lambda *args, **kwargs: {
        "id": 10, "execution_mode": "live", "trading_config": {"initial_capital": 1000}
    })
    monkeypatch.setattr(service, "_load_trades", lambda **kwargs: [])
    monkeypatch.setattr(service, "_load_recent_logs", lambda **kwargs: [])
    monkeypatch.setattr(service, "_review_source_context", lambda *args: {})
    monkeypatch.setattr(service, "_save_report", lambda **kwargs: None)
    ai_inputs = []

    def review(**kwargs):
        ai_inputs.append(kwargs["base_report"])
        return {"status": "success"}

    monkeypatch.setattr(service, "_build_ai_review", review)
    report = service.build_report(strategy_id=10, user_id=1)

    assert report["metrics"]["open_position_count"] == 1
    assert report["metrics"]["unrealized_pnl"] == 10.0
    position = report["evidence"]["current_positions"][0]
    assert position["symbol"] == "BTC/USDT"
    assert position["size"] == 0.5
    assert position["updated_at"] == int(datetime(2026, 1, 1, 12, tzinfo=timezone.utc).timestamp())
    assert ai_inputs[0]["evidence"]["current_positions"] == [position]
    assert service._load_positions(strategy_id=999) == []


def test_virtual_positions_keep_their_own_timestamp_fields(monkeypatch):
    monkeypatch.setattr(virtual_trading, "list_virtual_positions", lambda strategy_id: [{
        "strategy_id": strategy_id, "size": Decimal("0.5"), "unrealized_pnl": Decimal("10"),
        "created_at": datetime(2026, 1, 1), "updated_at": datetime(2026, 1, 2),
    }])
    service = StrategyReviewService.__new__(StrategyReviewService)
    positions = service._load_positions(strategy_id=10, virtual=True)
    assert positions[0]["size"] == 0.5
    assert positions[0]["created_at"] == 1767225600
    assert positions[0]["updated_at"] == 1767312000
