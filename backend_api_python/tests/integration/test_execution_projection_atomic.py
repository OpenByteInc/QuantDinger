"""Opt-in PostgreSQL tests using an isolated schema and real fill writers.

Set QD_TEST_POSTGRES_DSN to a local PostgreSQL with the application schema.
Only table definitions are copied; account data and brokers are never used.
"""

import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from app.services.execution_streams import processor as module
from app.services.live_trading import records
from app.services.live_trading.leg_context import LegContext
from app.services.pending_orders import fill_records
from app.utils import db_postgres as pg


@pytest.fixture
def projection(monkeypatch):
    dsn = os.environ.get("QD_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("QD_TEST_POSTGRES_DSN is required")
    import psycopg2
    from psycopg2 import sql
    from psycopg2.pool import ThreadedConnectionPool

    schema = "qd_projection_test_" + uuid4().hex
    admin = psycopg2.connect(dsn)
    admin.autocommit = True
    tables = (
        "pending_orders",
        "qd_live_order_bindings",
        "qd_live_order_binding_identifiers",
        "qd_strategy_positions",
        "qd_strategy_trades",
        "strategy_order_fills",
        "strategy_order_intents",
    )
    pool = None
    try:
        with admin.cursor() as cur:
            cur.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            for table in tables:
                cur.execute(
                    sql.SQL("CREATE TABLE {}.{} (LIKE public.{} INCLUDING DEFAULTS INCLUDING INDEXES)").format(
                        sql.Identifier(schema), sql.Identifier(table), sql.Identifier(table)
                    )
                )
                # Do not consume production serial sequences through copied defaults.
                cur.execute(
                    sql.SQL("CREATE SEQUENCE {}.{}").format(sql.Identifier(schema), sql.Identifier(table + "_test_id"))
                )
                cur.execute(
                    sql.SQL("ALTER TABLE {}.{} ALTER COLUMN id SET DEFAULT nextval(%s)").format(
                        sql.Identifier(schema), sql.Identifier(table)
                    ),
                    (schema + "." + table + "_test_id",),
                )
        pool = ThreadedConnectionPool(1, 4, dsn, options=f"-c search_path={schema}")
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

        query("""INSERT INTO pending_orders
            (id,user_id,strategy_id,symbol,signal_type,market_type,execution_mode,status,amount,price,idempotency_key)
            VALUES (1,1,1,'BTC/USDT','open_long','swap','live','sent',2,100,'projection-test')""")
        query("""INSERT INTO qd_live_order_bindings
            (id,credential_id,exchange_id,market_type,owner_type,owner_id,strategy_id,pending_order_id)
            VALUES (1,1,'binance','swap','pending_order',1,1,1)""")
        monkeypatch.setattr(module, "load_strategy_configs", lambda *a: {"user_id": 1})
        monkeypatch.setattr(
            module, "resolve_exchange_config", lambda *a, **kw: {"exchange_id": "binance", "credential_id": 1}
        )
        monkeypatch.setattr(module, "bind_instrument_product_contract", lambda cfg, *a, **kw: cfg)
        monkeypatch.setattr(module, "create_client", lambda *a, **kw: object())
        monkeypatch.setattr(module, "complete_snapshot", lambda event: event)
        monkeypatch.setattr(module, "append_strategy_log", lambda *a, **kw: None)
        monkeypatch.setattr(records, "_get_user_id_from_strategy", lambda *a: 1)
        monkeypatch.setattr(
            fill_records,
            "resolve_leg_context",
            lambda **kw: LegContext(credential_id=1, pending_order_id=1, fill_source="private_websocket"),
        )
        monkeypatch.setattr(fill_records, "invalidate_position_sync_snapshot_for_exchange", lambda **kw: None)
        processor = module.ExecutionEventProcessor()
        monkeypatch.setattr(processor.repository, "fee_components", lambda *a: [{"currency": "USDT", "amount": 0.1}])
        event = {
            "id": 11,
            "quantity": 1,
            "cumulative_quantity": 1,
            "is_cumulative": True,
            "price": 100,
            "order_status": "partial",
            "exchange_id": "binance",
            "exchange_order_id": "order-1",
            "exchange_fill_id": "fill-1",
            "fee_status": "actual",
        }
        binding = {"id": 1, "pending_order_id": 1, "strategy_id": 1, "strategy_run_id": 1}
        yield processor, event, binding, query
    finally:
        if pool is not None:
            pool.closeall()
        with admin.cursor() as cur:
            cur.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
        admin.close()


def assert_ledger(query, amount):
    assert float(query("SELECT filled FROM pending_orders WHERE id=1")[0]["filled"]) == amount
    assert float(query("SELECT observed_filled FROM qd_live_order_bindings WHERE id=1")[0]["observed_filled"]) == amount
    positions = query("SELECT size FROM qd_strategy_positions")
    assert sum(float(r["size"]) for r in positions) == amount
    trades = query("SELECT id, amount, commission_quote FROM qd_strategy_trades")
    runtime = query("SELECT id, quantity, commission_quote FROM strategy_order_fills")
    assert len(trades) == (1 if amount else 0)
    assert len(runtime) == (1 if amount else 0)
    if amount:
        assert float(trades[0]["amount"]) == amount
        assert float(runtime[0]["quantity"]) == amount
        assert float(trades[0]["commission_quote"]) == pytest.approx(0.1)
        assert float(runtime[0]["commission_quote"]) == pytest.approx(0.1)


@pytest.mark.parametrize("failure_stage", ["config", "after_position", "after_trade", "runtime_sql"])
def test_failed_projection_rolls_back_all_writes_and_replays(projection, monkeypatch, failure_stage):
    processor, event, binding, query = projection

    def fail(*a, **kw):
        raise RuntimeError("injected projection failure")

    with monkeypatch.context() as patch:
        if failure_stage == "config":
            patch.setattr(module, "load_strategy_configs", fail)
        elif failure_stage == "after_position":
            patch.setattr(fill_records, "record_trade", fail)
        elif failure_stage == "after_trade":
            patch.setattr(fill_records, "_record_runtime_fill", fail)
        else:
            original = pg.PostgresCursor.execute

            def execute(cur, statement, params=None):
                if "INSERT INTO strategy_order_fills" in statement:
                    return original(cur, "SELECT 1 / 0")
                return original(cur, statement, params)

            patch.setattr(pg.PostgresCursor, "execute", execute)
        with pytest.raises(Exception):
            processor._process_pending_order(event, binding)
    assert_ledger(query, 0)
    processor._process_pending_order(event, binding)
    assert_ledger(query, 1)
    # Also covers a crash after commit but before marking the event processed.
    processor._process_pending_order(event, binding)
    assert_ledger(query, 1)


@pytest.mark.parametrize("cumulative", [True, False])
def test_concurrent_duplicate_event_is_applied_once(projection, cumulative):
    processor, event, binding, query = projection
    if not cumulative:
        event = {**event, "is_cumulative": False, "cumulative_quantity": 0}
    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(lambda _: processor._process_pending_order(event, binding), range(2)))
    assert_ledger(query, 1)


def test_subsequent_partial_fill_keeps_quantity_and_fees_consistent(projection):
    processor, event, binding, query = projection
    processor._process_pending_order(event, binding)
    second = {
        **event,
        "id": 12,
        "cumulative_quantity": 2,
        "price": 110,
        "exchange_fill_id": "fill-2",
        "order_status": "filled",
    }
    processor._process_pending_order(second, binding)
    processor._process_pending_order(second, binding)
    order = query("SELECT filled, avg_price, status FROM pending_orders WHERE id=1")[0]
    assert float(order["filled"]) == 2
    assert float(order["avg_price"]) == 105
    assert order["status"] == "filled"
    position = query("SELECT size, entry_price FROM qd_strategy_positions")[0]
    assert float(position["size"]) == 2
    assert float(position["entry_price"]) == 105
    totals = query("SELECT SUM(amount) AS quantity, SUM(commission_quote) AS fee FROM qd_strategy_trades")[0]
    assert float(totals["quantity"]) == 2
    assert float(totals["fee"]) == pytest.approx(0.2)


@pytest.mark.parametrize("ws_first", [True, False])
def test_rest_writer_and_ws_share_the_posted_baseline(projection, ws_first):
    processor, event, binding, query = projection
    event = dict(event, cumulative_average_price=100)

    def rest():
        fill_records.persist_strategy_fill(
            strategy_id=1,
            symbol="BTC/USDT",
            signal_type="open_long",
            filled=1,
            cumulative_filled=1,
            avg_price=100,
            exchange_config={"credential_id": 1},
            market_type="swap",
            order_id=1,
            cumulative_fees={"USDT": 0.1},
            commission_quote=0.1,
            fee_status="actual",
            fee_source="rest",
        )

    def ws():
        processor._process_pending_order(event, binding)

    for action in (ws, rest) if ws_first else (rest, ws):
        action()
    assert float(query("SELECT SUM(amount) AS quantity FROM qd_strategy_trades")[0]["quantity"]) == 1
    assert float(query("SELECT SUM(commission_quote) AS fee FROM qd_strategy_trades")[0]["fee"]) == pytest.approx(0.1)
    assert float(query("SELECT size FROM qd_strategy_positions")[0]["size"]) == 1


def test_observed_fill_without_ledger_recovers_after_crash(projection):
    _, _, _, query = projection
    query("UPDATE pending_orders SET filled=1, avg_price=100, status='filled' WHERE id=1")
    kwargs = dict(
        strategy_id=1,
        symbol="BTC/USDT",
        signal_type="open_long",
        filled=0,
        cumulative_filled=1,
        avg_price=100,
        exchange_config={"credential_id": 1},
        market_type="swap",
        order_id=1,
        cumulative_fees={"USDT": 0.1},
        commission_quote=0.1,
        fee_status="actual",
        fee_source="rest",
    )
    fill_records.persist_strategy_fill(**kwargs)
    fill_records.persist_strategy_fill(**kwargs)
    assert float(query("SELECT size FROM qd_strategy_positions")[0]["size"]) == 1
    assert len(query("SELECT * FROM qd_strategy_trades")) == 1


def test_late_cumulative_fee_and_rebate_do_not_add_quantity(projection):
    _, _, _, query = projection
    kwargs = dict(
        strategy_id=1,
        symbol="BTC/USDT",
        signal_type="open_long",
        filled=1,
        cumulative_filled=1,
        avg_price=100,
        exchange_config={"credential_id": 1},
        market_type="swap",
        order_id=1,
        fee_source="rest",
    )
    fill_records.persist_strategy_fill(**kwargs, fee_status="pending")
    for fee in (0.3, 0.3, -0.1, -0.1):
        fill_records.persist_strategy_fill(
            **kwargs, cumulative_fees={"USDT": fee}, commission_quote=fee, fee_status="actual"
        )
    trades = query("SELECT * FROM qd_strategy_trades")
    assert len(trades) == 1
    assert float(trades[0]["commission_quote"]) == pytest.approx(-0.1)
    assert float(query("SELECT size FROM qd_strategy_positions")[0]["size"]) == 1


def test_compound_order_ws_does_not_compare_one_leg_to_total(projection):
    import json

    processor, event, binding, query = projection
    response = {
        "phases": {
            "executor": {
                "limit_summary": {
                    "exchange_order_id": "limit",
                    "filled_qty": 1,
                    "avg_price": 100,
                    "fees_by_ccy": {"USDT": 0.1},
                },
                "market_summary": {
                    "exchange_order_id": "market",
                    "filled_qty": 1,
                    "avg_price": 110,
                    "fees_by_ccy": {"USDT": 0.1},
                },
            }
        }
    }
    query(
        "UPDATE pending_orders SET filled=2, avg_price=105, exchange_response_json=%s WHERE id=1",
        (json.dumps(response),),
    )
    fill_records.persist_strategy_fill(
        strategy_id=1,
        symbol="BTC/USDT",
        signal_type="open_long",
        filled=2,
        cumulative_filled=2,
        avg_price=105,
        exchange_config={"credential_id": 1},
        market_type="swap",
        order_id=1,
        cumulative_fees={"USDT": 0.2},
        commission_quote=0.2,
        fee_status="actual",
    )
    event.update(
        exchange_order_id="market",
        cumulative_quantity=2,
        cumulative_average_price=110,
        price=110,
        quantity=1,
        fees_cumulative=True,
        _snapshot_fees={"USDT": 0.2},
        order_status="filled",
    )
    processor._process_pending_order(event, binding)
    processor._process_pending_order(event, binding)
    totals = query(
        "SELECT SUM(amount) AS quantity, SUM(value) AS value, SUM(commission_quote) AS fee FROM qd_strategy_trades"
    )[0]
    assert float(totals["quantity"]) == 3
    assert float(totals["value"]) == 320
    assert float(totals["fee"]) == pytest.approx(0.3)


def test_missing_execution_price_does_not_reuse_previous_fill_and_can_replay(projection):
    from app.services.live_trading.base import LiveTradingError
    processor, event, binding, query = projection
    processor._process_pending_order(event, binding)
    missing = dict(event, id=12, exchange_fill_id="fill-2", cumulative_quantity=2, price=0)
    with pytest.raises(LiveTradingError, match="fillSnapshotNotReady"):
        processor._process_pending_order(missing, binding)
    assert_ledger(query, 1)
    actual = dict(missing, price=110)
    processor._process_pending_order(actual, binding)
    processor._process_pending_order(actual, binding)
    rows = query("SELECT amount, price FROM qd_strategy_trades ORDER BY id")
    assert len(rows) == 2
    assert [float(r["price"]) for r in rows] == [100, 110]
    assert float(query("SELECT entry_price FROM qd_strategy_positions")[0]["entry_price"]) == 105
