"""Size rejection recovery and cross-worker notification arbitration."""

import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from unittest.mock import MagicMock

import pytest

from app.services.pending_orders import live_order_support as support
from app.services.pending_orders.error_classification import exit_size_rejection_kind
from app.services.pending_orders.live_order_support import LiveOrderNotifier
from app.services.strategy_v2 import live_execution


@pytest.mark.parametrize("error,expected", [
    ("Filter failure: MIN_NOTIONAL", "notional"),
    ("Order's notional must be no smaller than 5", "notional"),
    ("Filter failure: LOT_SIZE", ""),
    ("Filter failure: MARKET_LOT_SIZE", ""),
    ("quantity must be a multiple of the board lot size", ""),
    ("Odd-lot orders are not supported", ""),
    ("qty too small", "quantity"),
    ("bad precision", ""),
    ("invalid amount; actual order quantity is 0; minimum notional is about 50", ""),
    ("insufficient balance", ""), ("ReduceOnly order is rejected", ""),
    ("strategyRuntime.noStrategyInventory", ""),
    ("strategyRuntime.spotBalanceUnavailable", ""),
    ("connection timed out", ""), ("invalid api key", ""),
    ("order size too large", ""),
    ("position not found; actual order quantity is 0; min_notional=10", ""),
    ("invalid amount: insufficient balance", ""),
])
def test_size_rejections_do_not_imply_dust(error, expected):
    assert exit_size_rejection_kind(error) == expected


def _notifier(**changes):
    values = {
        "order_id": 101, "strategy_id": 7,
        "order_row": {"user_id": 12, "strategy_run_id": 42, "credential_id": 5},
        "payload": {"symbol": "BTC/USDT", "signal_type": "close_long", "amount": 0.001,
                    "ref_price": 1000, "market_type": "spot", "notification_config": {"email": True}},
        "notifier": MagicMock(notify_signal=MagicMock(return_value={"email": {"ok": True}})),
        "load_notification_config": lambda _: {}, "load_strategy_name": lambda _: "Test strategy",
        "resolved_exchange_id": "binance", "resolved_credential_id": 5,
    }
    values.update(changes)
    return LiveOrderNotifier(**values)


def _mock_db(monkeypatch, result):
    db = MagicMock()
    db.cursor.return_value.fetchone.return_value = result
    monkeypatch.setattr(support, "get_db_connection", lambda: db)
    db.__enter__.return_value = db
    return db


def test_duplicate_failure_does_not_send_any_channel(monkeypatch):
    db = _mock_db(monkeypatch, None)
    notice = _notifier()
    notice.notify(status="failed", error="qty too small")
    notice.notifier.notify_signal.assert_not_called()
    query, params = db.cursor.return_value.execute.call_args.args
    assert "ON CONFLICT" in query and "DO NOTHING" in query and "INTERVAL" not in query
    assert params[:2] == (0, 7)
    db.commit.assert_called_once()


def test_pending_id_and_price_do_not_change_failure_key(monkeypatch):
    db = _mock_db(monkeypatch, {"state_key": "claimed"})
    first = _notifier()
    claim1 = first._claim_exit_failure_notice("qty too small", "")
    second = _notifier(order_id=999, payload={**first.payload, "ref_price": 1005})
    claim2 = second._claim_exit_failure_notice("qty too small", "binance")
    assert claim1[0] == claim2[0] and claim1[1] != claim2[1]
    assert len(claim1[0]) <= 128
    assert db.commit.call_count == 2


@pytest.mark.parametrize("changes", [
    {"payload": {"symbol": "ETH/USDT", "signal_type": "close_long", "amount": 0.001, "market_type": "spot"}},
    {"resolved_exchange_id": "okx"}, {"resolved_credential_id": 6},
    {"order_row": {"user_id": 13, "strategy_run_id": 42}},
    {"payload": {"symbol": "BTC/USDT", "signal_type": "close_short", "amount": 0.001, "market_type": "swap"}},
])
def test_notification_scope_separates_accounts_symbols_and_legs(changes):
    assert _notifier()._exit_notice_scope("") != _notifier(**changes)._exit_notice_scope("")


@pytest.mark.parametrize("error", ["invalid api key", "insufficient balance", "strategyRuntime.noStrategyInventory"])
def test_unrelated_critical_errors_are_not_throttled(monkeypatch, error):
    monkeypatch.setattr(support, "get_db_connection", lambda: pytest.fail("no size failure claim"))
    notice = _notifier()
    notice.notify(status="failed", error=error)
    notice.notifier.notify_signal.assert_called_once()


def test_success_is_sent_and_resets_previous_size_notices(monkeypatch):
    db = _mock_db(monkeypatch, None)
    notice = _notifier()
    notice.notify(status="sent", exchange_id="binance")
    notice.notifier.notify_signal.assert_called_once()
    query, params = db.cursor.return_value.execute.call_args.args
    assert "DELETE FROM strategy_runtime_state" in query
    assert "::bigint <= %s" in query and params[-1] == 101


@pytest.mark.parametrize("raises", [False, True])
def test_failed_delivery_releases_only_its_own_claim(monkeypatch, raises):
    db = _mock_db(monkeypatch, {"state_key": "claimed"})
    notice = _notifier()
    notice.notifier.notify_signal.return_value = {"email": {"ok": False}}
    if raises:
        notice.notifier.notify_signal.side_effect = RuntimeError("delivery failed")
    notice.notify(status="failed", error="qty too small")
    first, last = db.cursor.return_value.execute.call_args_list
    token = json.loads(first.args[1][3])["claim"]
    assert "state_json->>'claim' = %s" in last.args[0]
    assert last.args[1][-1] == token


def test_database_failure_does_not_hide_alert(monkeypatch):
    def unavailable():
        raise RuntimeError("test database unavailable")
    monkeypatch.setattr(support, "get_db_connection", unavailable)
    notice = _notifier()
    notice.notify(status="failed", error="qty too small")
    notice.notifier.notify_signal.assert_called_once()


@pytest.mark.integration
def test_postgres_notification_claim_concurrency_without_expiry(monkeypatch):
    """Optional isolated database test; never accepts an application database."""
    dsn = os.getenv("QD_EXIT_NOTICE_TEST_DSN", "")
    if not dsn:
        pytest.skip("set QD_EXIT_NOTICE_TEST_DSN to a disposable local test database")
    import psycopg2
    from psycopg2.extras import RealDictCursor
    from psycopg2.extensions import parse_dsn

    config = parse_dsn(dsn)
    assert config.get("host") in {"127.0.0.1", "localhost"}
    assert config.get("dbname") == "quantdinger_exit_notice_test"
    schema = "exit_notice_test_" + uuid.uuid4().hex

    @contextmanager
    def connection():
        conn = psycopg2.connect(dsn, options=f"-c search_path={schema}", cursor_factory=RealDictCursor)
        try:
            yield conn
        finally:
            conn.close()

    admin = psycopg2.connect(dsn)
    admin.autocommit = True
    try:
        with admin.cursor() as cur:
            cur.execute(f"CREATE SCHEMA {schema}")
            cur.execute(f"""CREATE TABLE {schema}.strategy_runtime_state (
                strategy_run_id INTEGER NOT NULL, strategy_id INTEGER NOT NULL,
                state_key VARCHAR(128) NOT NULL, state_json JSONB NOT NULL,
                version BIGINT NOT NULL DEFAULT 1, updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                UNIQUE(strategy_run_id, strategy_id, state_key))""")
        monkeypatch.setattr(support, "get_db_connection", connection)
        sender = MagicMock(notify_signal=MagicMock(return_value={"email": {"ok": True}}))

        def send(index):
            _notifier(order_id=index, notifier=sender).notify(status="failed", error="qty too small")

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(send, range(100, 124)))
        assert sender.notify_signal.call_count == 1
        send(125)
        assert sender.notify_signal.call_count == 1
        with connection() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE strategy_runtime_state SET updated_at = NOW() - INTERVAL '30 days'")
            conn.commit()
        send(126)
        assert sender.notify_signal.call_count == 1
        _notifier(order_id=127, notifier=sender).notify(status="sent", exchange_id="binance")
        send(128)
        assert sender.notify_signal.call_count == 3
        _notifier(strategy_id=8, notifier=sender).notify(status="failed", error="qty too small")
        _notifier(order_row={"user_id": 12, "strategy_run_id": 43}, notifier=sender).notify(
            status="failed", error="qty too small")
        assert sender.notify_signal.call_count == 4
        with connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""CREATE TABLE pending_orders (
                    id SERIAL PRIMARY KEY, user_id INTEGER, strategy_id INTEGER,
                    symbol TEXT, market_type TEXT, execution_mode TEXT, signal_type TEXT,
                    exchange_id TEXT, credential_id INTEGER, status TEXT, amount NUMERIC,
                    filled NUMERIC, last_error TEXT, payload_json TEXT)""")
                cur.execute("""INSERT INTO pending_orders VALUES
                    (1, 12, 7, 'META', 'spot', 'live', 'close_long', 'alpaca', 5,
                     'failed', 0.00000001, 0, 'qty too small', %s)""",
                            (json.dumps({"amount": 0.00000001}),))
            conn.commit()
        monkeypatch.setattr(live_execution, "get_db_connection", connection)
        request = live_execution.LiveOrderRequest(
            user_id=12, strategy_id=7, strategy_run_id=99, symbol="META", market_type="spot",
            execution_mode="live", action="close_long", exchange_id="alpaca", credential_id=5,
            quantity=0.00000001, reference_price=500, signal_timestamp=999999999,
        )
        for _ in range(20):
            # A fresh gateway models an evaluator restart; no in-memory latch.
            assert live_execution.StrategyV2OrderGateway()._exit_size_retry_deferred(request)
        for changes in ({"credential_id": 6}, {"exchange_id": "ibkr"}, {"user_id": 13},
                        {"strategy_id": 8}, {"symbol": "AAPL"}, {"quantity": 1}):
            assert not live_execution.StrategyV2OrderGateway()._exit_size_retry_deferred(replace(request, **changes))
    finally:
        with admin.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        admin.close()
