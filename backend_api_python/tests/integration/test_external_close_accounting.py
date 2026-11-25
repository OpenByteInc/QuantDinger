"""Isolated PostgreSQL regression checks for competing close-fill writers."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.live_trading import external_flat_close as external, records
from app.services.live_trading.exchange_close_history import _select_exact_close
from app.services.pending_orders import fill_records
from app.utils import db_postgres as pg


@pytest.fixture
def ledger(monkeypatch, request):
    dsn = os.environ.get("QD_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("QD_TEST_POSTGRES_DSN is required (isolated initialized test database)")
    import psycopg2
    from psycopg2 import sql
    from psycopg2.pool import ThreadedConnectionPool

    schema = "qd_external_close_test_" + uuid4().hex
    admin = psycopg2.connect(dsn)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        for table in ("pending_orders", "qd_strategy_positions", "qd_strategy_trades"):
            cur.execute(sql.SQL("CREATE TABLE {}.{} (LIKE public.{} INCLUDING DEFAULTS INCLUDING INDEXES)").format(
                sql.Identifier(schema), sql.Identifier(table), sql.Identifier(table)))
            cur.execute(sql.SQL("CREATE SEQUENCE {}.{}").format(sql.Identifier(schema), sql.Identifier(table + "_ids")))
            cur.execute(sql.SQL("ALTER TABLE {}.{} ALTER COLUMN id SET DEFAULT nextval(%s)").format(
                sql.Identifier(schema), sql.Identifier(table)), (schema + "." + table + "_ids",))
    pool = ThreadedConnectionPool(1, 5, dsn, options=f"-c search_path={schema}")

    def cleanup():
        pool.closeall()
        with admin.cursor() as cur:
            cur.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
        admin.close()

    request.addfinalizer(cleanup)
    monkeypatch.setattr(pg, "_get_connection_pool", lambda: pool)
    monkeypatch.setattr(pg, "_acquire_conn_with_wait", lambda p: p.getconn())

    def query(statement, params=()):
        with pg.get_pg_connection() as db:
            cur = db.cursor()
            cur.execute(statement, params)
            rows = cur.fetchall() if cur._cursor.description else []
            db.commit()
            cur.close()
            return rows

    monkeypatch.setattr(records, "_get_user_id_from_strategy", lambda *_: 1)
    monkeypatch.setattr(external, "_active_strategy_run_id", lambda *_: 0)
    monkeypatch.setattr(external, "append_strategy_log", lambda *a, **k: None)
    monkeypatch.setattr(fill_records, "invalidate_position_sync_snapshot_for_exchange", lambda **_: None)
    closed = datetime(2026, 11, 4, 14, 30, tzinfo=timezone.utc)
    components = [{"amount": 1.0, "price": 160.39, "exchange_fill_id": "activity::1",
                   "exchange_order_id": "broker-close", "closed_at": closed, "fee_ccy": "USD"}]

    def history(_client, **kw):
        return _select_exact_close(components, target_qty=kw["target_qty"], opened_after=kw["opened_after"],
                                   is_recorded=kw["is_recorded"], is_managed_order=kw["is_managed_order"])

    monkeypatch.setattr(external, "resolve_exchange_external_close", history)
    records.record_trade(strategy_id=1, symbol="BKNG", trade_type="open_long", amount=1, price=153.414,
                         credential_id=10, market_type="spot", created_at=datetime(2026, 11, 3))
    records.upsert_position(strategy_id=1, symbol="BKNG", side="long", size=1, entry_price=153.414,
                            current_price=153.414, credential_id=10, market_type="spot")

    def pending(status="sent", **overrides):
        row = dict(credential_id=10, market_type="spot", symbol="BKNG", signal_type="close_long",
                   execution_mode="live", exchange_order_id="broker-close", client_order_id="qd_1_1")
        row.update(overrides)
        query("""INSERT INTO pending_orders
            (id, strategy_id, idempotency_key, status, credential_id, market_type, symbol, signal_type,
             execution_mode, exchange_order_id, client_order_id)
            VALUES (1, 1, 'test-close', %s, %s, %s, %s, %s, %s, %s, %s)""", (status, *row.values()))

    def normal_fill(total=1.0, source="worker_alpaca_fill_sync"):
        return fill_records.persist_strategy_fill(
            strategy_id=1, symbol="BKNG", signal_type="close_long", filled=total,
            cumulative_filled=total, cumulative_average_price=160.39, avg_price=160.39,
            exchange_config={"exchange_id": "alpaca", "credential_id": 10}, market_type="USStock",
            order_id=1, exchange_order_id="broker-close", fill_source=source)

    def reconcile():
        return external.reconcile_external_flat_closes(strategy_id=1, symbol="BKNG", side="long")

    yield SimpleNamespace(query=query, pending=pending, fill=normal_fill, reconcile=reconcile,
                          components=components, history=history)


@pytest.mark.parametrize("status", ["sent", "syncing", "filled", "cancelled", "failed"])
def test_pending_close_is_never_posted_as_external(ledger, status):
    ledger.pending(status)
    assert ledger.reconcile() == 0
    assert len(ledger.query("SELECT id FROM qd_strategy_trades")) == 1
    ledger.fill()
    ledger.fill(source="private_websocket")
    assert ledger.reconcile() == 0
    closes = ledger.query("SELECT amount, pending_order_id, exchange_order_id FROM qd_strategy_trades WHERE type='close_long'")
    assert len(closes) == 1
    assert float(closes[0]["amount"]) == pytest.approx(1)
    assert closes[0]["pending_order_id"] == 1
    assert closes[0]["exchange_order_id"] == "broker-close"
    assert not ledger.query("SELECT id FROM qd_strategy_positions")


def test_partial_pending_close_then_external_remainder(ledger):
    ledger.pending("cancelled")
    ledger.fill(0.6)
    ledger.components[0]["amount"] = 0.6
    ledger.components.append(dict(ledger.components[0], amount=0.4, exchange_fill_id="activity::2",
                                  exchange_order_id="manual-close"))
    assert ledger.reconcile() > 0
    ledger.fill(0.6)
    assert ledger.reconcile() == 0
    closes = ledger.query("SELECT amount, pending_order_id, exchange_order_id FROM qd_strategy_trades WHERE type='close_long' ORDER BY id")
    assert [float(row["amount"]) for row in closes] == pytest.approx([0.6, 0.4])
    assert [row["exchange_order_id"] for row in closes] == ["broker-close", "manual-close"]
    assert not ledger.query("SELECT id FROM qd_strategy_positions")


def test_external_partial_fills_share_order_but_are_each_recorded_once(ledger):
    ledger.components[0]["amount"] = 0.4
    ledger.components.append(dict(ledger.components[0], amount=0.6, exchange_fill_id="activity::2"))
    assert ledger.reconcile() > 0
    assert ledger.reconcile() == 0
    closes = ledger.query("SELECT amount, exchange_order_id FROM qd_strategy_trades WHERE type='close_long' ORDER BY id")
    assert [float(row["amount"]) for row in closes] == pytest.approx([0.4, 0.6])
    assert {row["exchange_order_id"] for row in closes} == {"broker-close"}
    assert not ledger.query("SELECT id FROM qd_strategy_positions")


@pytest.mark.parametrize("other_scope", [
    {"credential_id": 20}, {"market_type": "swap"}, {"symbol": "NVDA"},
    {"signal_type": "close_short"}, {"signal_type": "open_long"}, {"execution_mode": "signal"},
])
def test_unrelated_pending_order_does_not_block_real_external_close(ledger, other_scope):
    ledger.pending("sent", **other_scope)
    assert ledger.reconcile() > 0
    close = ledger.query("SELECT * FROM qd_strategy_trades WHERE type='close_long'")[0]
    assert close["fill_source"] == "position_sync"
    assert close["exchange_order_id"] == "broker-close"
    assert not ledger.query("SELECT id FROM qd_strategy_positions")


@pytest.mark.parametrize("market", ["USStock", "HKStock", "spot", "swap", "crypto"])
def test_managed_close_guard_covers_market_types(ledger, market):
    ledger.pending("filled", market_type=market)
    scope = dict(credential_id=10, market_type=market, symbol="BKNG", side="long")
    assert external._has_managed_close(external._pending_close_orders(**scope), ledger.components)


@pytest.mark.parametrize("symbol", ["BTC/USDT", "BTCUSDT", "BTC-USDT"])
def test_close_identity_uses_canonical_symbol_and_spot_alias(ledger, symbol):
    ledger.pending("filled", symbol=symbol, market_type="crypto")
    orders = external._pending_close_orders(credential_id=10, market_type="spot", symbol="BTC/USDT", side="long")
    assert external._has_managed_close(orders, ledger.components)


def test_concurrent_external_reconciliation_posts_once(ledger, monkeypatch):
    ready = Barrier(2)

    def history(*args, **kwargs):
        snapshot = ledger.history(*args, **kwargs)
        ready.wait(timeout=10)
        return snapshot

    monkeypatch.setattr(external, "resolve_exchange_external_close", history)
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: ledger.reconcile(), range(2)))
    assert sum(result > 0 for result in results) == 1
    assert len(ledger.query("SELECT id FROM qd_strategy_trades WHERE type='close_long'")) == 1


def test_external_batch_rolls_back_position_and_trades_on_failure(ledger, monkeypatch):
    ledger.components[0]["amount"] = 0.4
    ledger.components.append(dict(ledger.components[0], amount=0.6, exchange_fill_id="activity::2"))
    real_record = external.record_trade

    def record(**kwargs):
        if kwargs["exchange_fill_id"] == "activity::2":
            raise RuntimeError("injected write failure")
        return real_record(**kwargs)

    monkeypatch.setattr(external, "record_trade", record)
    with pytest.raises(RuntimeError, match="injected write failure"):
        ledger.reconcile()
    assert not ledger.query("SELECT id FROM qd_strategy_trades WHERE type='close_long'")
    assert float(ledger.query("SELECT size FROM qd_strategy_positions")[0]["size"]) == pytest.approx(1)


def test_normal_fill_during_history_read_is_not_double_posted(ledger, monkeypatch):
    def history(*args, **kwargs):
        snapshot = ledger.history(*args, **kwargs)
        ledger.pending("filled")
        ledger.fill()
        return snapshot

    monkeypatch.setattr(external, "resolve_exchange_external_close", history)
    assert ledger.reconcile() == 0
    closes = ledger.query("SELECT amount, pending_order_id FROM qd_strategy_trades WHERE type='close_long'")
    assert len(closes) == 1
    assert closes[0]["pending_order_id"] == 1
