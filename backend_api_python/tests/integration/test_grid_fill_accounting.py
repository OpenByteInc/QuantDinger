"""Real PostgreSQL accounting tests; no exchange or user data is accessed."""

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.services.execution_streams import processor as projection
from app.services.execution_streams.normalizers import parse_okx
from app.services.grid import fill_handler, poller
from app.services.grid.engine import GridEngine
from app.services.grid.resting_orders_repo import GridRestingOrder
from app.services.live_trading import records
from app.services.live_trading.leg_context import LegContext
from app.services.live_trading.okx import OkxClient
from app.utils import db_postgres as pg
from app.utils.trade_net_pnl import enrich_trades_net_pnl


@pytest.fixture
def ledger(monkeypatch):
    dsn = os.environ.get("QD_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("QD_TEST_POSTGRES_DSN is required")
    import psycopg2
    from psycopg2 import sql
    from psycopg2.pool import ThreadedConnectionPool

    schema = "qd_grid_test_" + uuid4().hex
    admin = psycopg2.connect(dsn)
    admin.autocommit = True
    tables = (
        "qd_grid_resting_orders",
        "qd_strategy_positions",
        "qd_strategy_trades",
        "qd_live_order_bindings",
        "qd_live_order_binding_identifiers",
        "qd_grid_cells",
        "pending_orders",
        "strategy_order_fills",
        "strategy_order_intents",
        "qd_quick_trades",
        "qd_exchange_credentials",
        "qd_execution_events",
    )
    with admin.cursor() as cur:
        cur.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        for table in tables:
            cur.execute(
                sql.SQL("CREATE TABLE {}.{} (LIKE public.{} INCLUDING DEFAULTS INCLUDING INDEXES)").format(
                    sql.Identifier(schema), sql.Identifier(table), sql.Identifier(table)
                )
            )
            cur.execute(sql.SQL("CREATE SEQUENCE {}.{}").format(sql.Identifier(schema), sql.Identifier(table + "_ids")))
            cur.execute(
                sql.SQL("ALTER TABLE {}.{} ALTER COLUMN id SET DEFAULT nextval(%s)").format(
                    sql.Identifier(schema), sql.Identifier(table)
                ),
                (schema + "." + table + "_ids",),
            )
        for table in ("qd_execution_owner_projections", "qd_execution_fee_projections", "qd_exchange_order_pnl"):
            cur.execute(
                sql.SQL("CREATE TABLE {}.{} (LIKE public.{} INCLUDING DEFAULTS INCLUDING INDEXES)").format(
                    sql.Identifier(schema), sql.Identifier(table), sql.Identifier(table)
                )
            )
    pool = ThreadedConnectionPool(1, 5, dsn, options=f"-c search_path={schema}")
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

    query("""INSERT INTO qd_grid_resting_orders
        (id,strategy_id,symbol,purpose,side,pos_side,price,quantity,exchange_order_id)
        VALUES (1,1,'ETH/USDT','long_entry','buy','long',2376.11,0.052,'open-1'),
               (2,1,'ETH/USDT','long_exit','sell','long',2388.13,0.052,'close-2')""")
    client = MagicMock(spec=OkxClient)
    client.get_instrument.return_value = {"ctVal": "0.1", "ctValCcy": "ETH"}
    monkeypatch.setattr(projection, "create_client", lambda *a, **k: client)
    monkeypatch.setattr(
        fill_handler, "resolve_leg_context", lambda **k: LegContext(credential_id=1, fill_source="test")
    )
    monkeypatch.setattr(records, "_get_user_id_from_strategy", lambda *a: 1)
    engine = object.__new__(GridEngine)
    engine.strategy_id, engine.symbol = 1, "ETH/USDT"
    engine.trading_config = {"market_type": "swap"}
    engine._levels_and_cells = lambda: ([], [])
    runner = SimpleNamespace(engine=engine, exchange_config={"exchange_id": "okx"})
    monkeypatch.setattr("app.services.grid.runner.get_runner", lambda *a: runner)
    repository = MagicMock()
    components = {}
    repository.fee_components.side_effect = lambda event_id: components.get(event_id, [])
    processor = projection.ExecutionEventProcessor(repository)

    def event(order=1, event_id=11, total="0.52", last="0.52", average="2376.11", price=None, fee="0.02471154"):
        raw = {
            "instId": "ETH-USDT-SWAP",
            "ordId": "open-1" if order == 1 else "close-2",
            "tradeId": str(event_id),
            "fillSz": last,
            "accFillSz": total,
            "avgPx": average,
            "fillPx": price or average,
            "state": "filled" if total == "0.52" else "partially_filled",
            "fee": str(-float(fee)),
            "feeCcy": "USDT",
        }
        parsed = parse_okx({"arg": {"channel": "orders", "instType": "SWAP"}, "data": [raw]})[0]
        components[event_id] = [dict(currency=f.currency, amount=f.amount, quote_amount=f.amount) for f in parsed.fees]
        return dict(asdict(parsed), id=event_id), dict(owner_id=order, strategy_id=1)

    try:
        yield SimpleNamespace(query=query, processor=processor, event=event, runner=runner, client=client)
    finally:
        pool.closeall()
        with admin.cursor() as cur:
            cur.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
        admin.close()


def test_issue_248_quantity_and_net_profit(ledger):
    ledger.processor._process_grid(*ledger.event())
    assert float(ledger.query("SELECT size FROM qd_strategy_positions")[0]["size"]) == pytest.approx(0.052)
    ledger.processor._process_grid(*ledger.event(order=2, event_id=12, average="2388.13", fee="0.06209138"))
    trades = [dict(r) for r in ledger.query("SELECT * FROM qd_strategy_trades ORDER BY id")]
    enrich_trades_net_pnl(trades)
    assert float(trades[-1]["net_pnl"]) == pytest.approx(0.53823708)


def test_partial_fill_cumulative_prices_and_fees(ledger):
    ledger.processor._process_grid(*ledger.event(total=".26", last=".26", average="100", fee=".1"))
    ledger.processor._process_grid(
        *ledger.event(event_id=12, total=".52", last=".26", average="105", price="110", fee=".3")
    )
    position = ledger.query("SELECT size, entry_price FROM qd_strategy_positions")[0]
    assert float(position["size"]) == pytest.approx(0.052)
    assert float(position["entry_price"]) == pytest.approx(105)
    trades = ledger.query("SELECT price, commission_quote FROM qd_strategy_trades ORDER BY id")
    assert float(trades[-1]["price"]) == pytest.approx(110)
    assert sum(float(t["commission_quote"]) for t in trades) == pytest.approx(0.3)


def test_missing_first_event_uses_cumulative_average(ledger):
    ledger.processor._process_grid(*ledger.event(average="105", price="110", last=".26"))
    assert float(ledger.query("SELECT entry_price FROM qd_strategy_positions")[0]["entry_price"]) == 105


def test_grid_failure_rolls_back_position_trade_and_cursor(ledger, monkeypatch):
    original = fill_handler.record_trade

    def fail(**kwargs):
        original(**kwargs)
        raise RuntimeError("injected after trade")

    with monkeypatch.context() as patch:
        patch.setattr(fill_handler, "record_trade", fail)
        with pytest.raises(RuntimeError):
            ledger.processor._process_grid(*ledger.event())
    assert ledger.query("SELECT * FROM qd_strategy_positions") == []
    assert ledger.query("SELECT * FROM qd_strategy_trades") == []
    assert (
        float(ledger.query("SELECT processed_fill_qty FROM qd_grid_resting_orders WHERE id=1")[0]["processed_fill_qty"])
        == 0
    )
    ledger.processor._process_grid(*ledger.event())
    assert float(ledger.query("SELECT size FROM qd_strategy_positions")[0]["size"]) == pytest.approx(0.052)


def test_duplicate_grid_event_concurrently_posts_once(ledger):
    event, binding = ledger.event()
    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(lambda _: ledger.processor._process_grid(event, binding), range(2)))
    assert len(ledger.query("SELECT * FROM qd_strategy_trades")) == 1
    assert float(ledger.query("SELECT size FROM qd_strategy_positions")[0]["size"]) == pytest.approx(0.052)


@pytest.mark.parametrize("ws_first", [True, False])
def test_rest_and_ws_share_quantity_and_fees(ledger, monkeypatch, ws_first):
    order = GridRestingOrder.from_row(ledger.query("SELECT * FROM qd_grid_resting_orders WHERE id=1")[0])
    worker = object.__new__(poller.GridFillPoller)
    monkeypatch.setattr(poller, "query_grid_order_fill", lambda *a, **k: (0.052, 2376.11, "filled"))

    def snapshot(*a, **kw):
        kw["details"].update(fee=0.02471154, fee_ccy="USDT", fee_status="actual")
        return 0.052, 2376.11

    monkeypatch.setattr(poller, "wait_grid_market_fill", snapshot)
    ws = lambda: ledger.processor._process_grid(*ledger.event())
    rest = lambda: worker._poll_order(ledger.runner, ledger.client, order, "swap")
    for action in (ws, rest) if ws_first else (rest, ws):
        action()
    trades = ledger.query("SELECT amount, commission_quote FROM qd_strategy_trades")
    assert sum(float(t["amount"]) for t in trades) == pytest.approx(0.052)
    assert sum(float(t["commission_quote"]) for t in trades) == pytest.approx(0.02471154)


def test_metadata_failure_never_posts_contracts_as_eth(ledger):
    ledger.client.get_instrument.side_effect = TimeoutError("metadata unavailable")
    with pytest.raises(TimeoutError):
        ledger.processor._process_grid(*ledger.event())
    assert ledger.query("SELECT * FROM qd_strategy_positions") == []
    assert ledger.query("SELECT * FROM qd_strategy_trades") == []


def test_rest_ws_concurrent_post_once(ledger, monkeypatch):
    order = GridRestingOrder.from_row(ledger.query("SELECT * FROM qd_grid_resting_orders WHERE id=1")[0])
    worker = object.__new__(poller.GridFillPoller)
    monkeypatch.setattr(poller, "query_grid_order_fill", lambda *a, **k: (0.052, 2376.11, "filled"))

    def snapshot(*a, **kw):
        kw["details"].update(fee=0.02471154, fee_ccy="USDT", fee_status="actual")
        return 0.052, 2376.11

    monkeypatch.setattr(poller, "wait_grid_market_fill", snapshot)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(ledger.processor._process_grid, *ledger.event()),
            executor.submit(worker._poll_order, ledger.runner, ledger.client, order, "swap"),
        ]
        for future in futures:
            future.result()
    assert len(ledger.query("SELECT * FROM qd_strategy_trades")) == 1
    assert float(ledger.query("SELECT size FROM qd_strategy_positions")[0]["size"]) == pytest.approx(0.052)


def test_spot_late_base_fee_updates_inventory_once(ledger, monkeypatch):
    ledger.runner.engine.trading_config = {"market_type": "spot"}
    monkeypatch.setattr(
        fill_handler, "resolve_leg_context", lambda **k: LegContext(market_type="spot", credential_id=1)
    )
    event, binding = ledger.event(average="100", total="1", last="1", fee="0")
    event.update(market_type="spot", _snapshot_fees={"ETH": 0.01}, fees_cumulative=True, fee_status="actual")
    ledger.processor._process_grid(event, binding)
    assert float(ledger.query("SELECT size FROM qd_strategy_positions")[0]["size"]) == pytest.approx(0.99)
    second = dict(event, id=12, quantity=0, _snapshot_fees={"ETH": 0.02})
    ledger.processor._process_grid(second, binding)
    ledger.processor._process_grid(second, binding)
    assert float(ledger.query("SELECT size FROM qd_strategy_positions")[0]["size"]) == pytest.approx(0.98)
    assert float(
        ledger.query("SELECT commission_quote FROM qd_strategy_trades")[0]["commission_quote"]
    ) == pytest.approx(2)


def test_grid_market_partial_rest_then_ws_completes_quantity(ledger, monkeypatch):
    monkeypatch.setattr(projection, "load_strategy_configs", lambda *a: {"exchange_config": {"exchange_id": "okx"}})
    monkeypatch.setattr(projection, "resolve_exchange_config", lambda cfg, **kw: cfg)
    trade_id = fill_handler.record_grid_market_fill(
        1,
        "ETH/USDT",
        "open_long",
        0.026,
        100,
        {"market_type": "swap"},
        exchange_order_id="open-1",
        commission=0.1,
        commission_ccy="USDT",
        commission_quote=0.1,
        fee_status="actual",
        fees_by_ccy={"USDT": 0.1},
    )
    event, _ = ledger.event(average="105", price="110", last=".26", fee=".3")
    binding = {"owner_id": trade_id, "strategy_id": 1}
    ledger.processor._process_grid_market(event, binding)
    ledger.processor._process_grid_market(event, binding)
    trade = ledger.query("SELECT * FROM qd_strategy_trades")[0]
    assert (float(trade["amount"]), float(trade["price"]), float(trade["commission_quote"])) == pytest.approx(
        (0.052, 105, 0.3)
    )
    position = ledger.query("SELECT size, entry_price FROM qd_strategy_positions")[0]
    assert (float(position["size"]), float(position["entry_price"])) == pytest.approx((0.052, 105))


def test_quick_trade_fractional_stock_precision_and_duplicate(ledger, monkeypatch):
    monkeypatch.setattr(projection, "resolve_exchange_config", lambda cfg, **kw: cfg)
    ledger.query(
        "INSERT INTO qd_quick_trades (id, user_id, exchange_id, market_type, symbol) VALUES (1,1,'alpaca','usstock','AAPL')"
    )
    event = dict(
        id=11,
        exchange_id="alpaca",
        market_type="usstock",
        symbol="AAPL",
        exchange_order_id="a",
        quantity=1e-9,
        cumulative_quantity=1e-9,
        cumulative_average_price=200,
        price=200,
        fees_cumulative=True,
        _snapshot_fees={"USD": 1e-10},
        fee_status="actual",
        order_status="filled",
    )
    for _ in range(2):
        ledger.processor._process_quick_trade(event, {"owner_id": 1})
    trade = ledger.query("SELECT * FROM qd_quick_trades")[0]
    assert float(trade["filled_amount"]) == pytest.approx(1e-9, abs=1e-20)
    assert float(trade["commission_quote"]) == pytest.approx(1e-10, abs=1e-20)


@pytest.mark.parametrize("paused", [False, True])
def test_cell_failure_rolls_back_fill_and_never_places_exit(ledger, monkeypatch, paused):
    engine = ledger.runner.engine
    cell = SimpleNamespace(index=0, upper_price=2388.13)
    engine._levels_and_cells = lambda: ([], [cell])
    engine._cell_record = lambda index: None
    engine._paused_entries = paused
    engine._runtime_params = {}
    engine.cfg = SimpleNamespace(boundary_action="pause")
    engine._cells = MagicMock()
    engine._cells.update_state.return_value = False
    engine._ensure_cell_exit_coverage = MagicMock()
    monkeypatch.setattr("app.services.grid.engine.append_strategy_log", lambda *a: None)
    with pytest.raises(RuntimeError, match="fillSnapshotNotReady"):
        ledger.processor._process_grid(*ledger.event())
    assert ledger.query("SELECT * FROM qd_strategy_trades") == []
    assert ledger.query("SELECT * FROM qd_strategy_positions") == []
    engine._ensure_cell_exit_coverage.assert_not_called()


def test_trade_api_separates_grid_instruction_and_execution_and_hides_raw_payload(ledger, monkeypatch):
    from flask import Flask, g
    from app.routes import strategy_ledger_routes as routes
    from app.services.live_trading import funding_reconciliation as funding
    from app.services.live_trading import alpaca_activity_reconciliation as alpaca
    ledger.processor._process_grid(*ledger.event(average="2370", price="2370"))
    monkeypatch.setattr(routes, "get_strategy_service", lambda: SimpleNamespace(
        get_strategy=lambda *a, **k: {"trading_config": {"market_type":"swap", "bot_type":"grid"}}))
    monkeypatch.setattr(funding, "sync_strategy_funding", lambda *a, **k: None)
    monkeypatch.setattr(funding, "load_strategy_funding_summary", lambda *a, **k: {})
    monkeypatch.setattr(alpaca, "sync_strategy_alpaca_activities", lambda *a, **k: None)
    monkeypatch.setattr(alpaca, "load_strategy_broker_activity_summary", lambda *a, **k: {})
    monkeypatch.setattr(alpaca, "is_alpaca_strategy", lambda *a, **k: False)
    app = Flask(__name__)
    with app.test_request_context("/strategies/trades?id=1"):
        g.user_id = 1
        from inspect import unwrap
        response = unwrap(routes.get_trades)()
    data = response.get_json()
    assert data["code"] == 1
    row = data["data"]["trades"][0]
    assert row["price"] == 2370
    assert row["reference_price"] == 2376.11
    assert row["reference_kind"] == "limit"
    assert row["price_deviation_pct"] == pytest.approx((2370 / 2376.11 - 1) * 100)
    assert row["exchange_order_id"] == "open-1"
    assert "request_payload" not in row
    assert "grid_request_price" not in row


def test_grid_api_matches_exchange_orders_instead_of_account_average(ledger, monkeypatch):
    from flask import Flask, g
    from inspect import unwrap
    from app.routes import strategy_ledger_routes as routes
    from app.services.live_trading import funding_reconciliation as funding
    from app.services.live_trading import alpaca_activity_reconciliation as alpaca

    ledger.query("""INSERT INTO qd_grid_resting_orders
        (id,strategy_id,symbol,cell_index,purpose,side,pos_side,price,quantity,exchange_order_id)
        VALUES (3,1,'ETH/USDT',1,'long_entry','buy','long',110,1,'open-3')""")
    ledger.query("UPDATE qd_grid_resting_orders SET extra = '{\"entry_grid_order_ids\":[1]}'::jsonb WHERE id=2")
    event, binding = ledger.event(total="10", last="10", average="110", fee=".11")
    event.update(exchange_order_id="open-3")
    binding.update(owner_id=3)
    ledger.processor._process_grid(event, binding)
    ledger.processor._process_grid(*ledger.event(event_id=12,total="10",last="10",average="100",fee=".1"))
    ledger.processor._process_grid(*ledger.event(order=2,event_id=13,total="10",last="10",average="101",fee=".101"))
    assert float(ledger.query("SELECT profit FROM qd_strategy_trades WHERE grid_order_id=2")[0]["profit"]) == -4
    monkeypatch.setattr(routes,"get_strategy_service",lambda:SimpleNamespace(
        get_strategy=lambda *a,**k:{"trading_config":{"market_type":"swap","bot_type":"grid"}}))
    for module, names in [(funding,["sync_strategy_funding","load_strategy_funding_summary"]),
                          (alpaca,["sync_strategy_alpaca_activities","load_strategy_broker_activity_summary"])]:
        for name in names:
            monkeypatch.setattr(module,name,lambda *a,**k:{})
    monkeypatch.setattr(alpaca,"is_alpaca_strategy",lambda *a,**k:False)
    app=Flask(__name__)
    def read():
        with app.test_request_context("/strategies/trades?id=1"):
            g.user_id=1
            return unwrap(routes.get_trades)().get_json()["data"]
    from app.services.execution_streams import pnl_reconciliation
    from app.services.execution_streams.reported_pnl import fill_report
    ledger.query("INSERT INTO qd_exchange_credentials (id,user_id,exchange_id,encrypted_config) VALUES (1,1,'okx','test-only')")
    reported = fill_report([dict(id="venue-close", quantity=1, pnl=-4)], expected=1, source="rest:fills.fillPnl", currency="USDT")
    monkeypatch.setattr(pnl_reconciliation, "_client", lambda *a: (ledger.client, {}))
    monkeypatch.setattr(pnl_reconciliation, "fetch_order_report", lambda *a, **k: reported)
    data=read()
    closed=data["trades"][0]
    assert closed["exchange_pnl"]["amount"] == -4
    assert closed["exchange_pnl"]["fee_basis"] == "excluding_fees"
    assert closed["exchange_pnl"]["order_id"] == "close-2"
    assert len(ledger.query("SELECT * FROM qd_exchange_order_pnl")) == 1
    assert closed["pnl_status"] == "matched"
    assert closed["profit"] == pytest.approx(.799)
    assert closed["profit_gross"] == 1
    assert closed["matched_orders"][0]["entry_order_ids"] == ["open-1"]
    assert closed["matched_orders"][0]["exit_order_id"] == "close-2"
    assert data["cost_summary"]["gross_realized_pnl"] == 1
    ledger.query("UPDATE qd_strategy_trades SET fee_status='pending' WHERE grid_order_id=1")
    pending=read()
    assert pending["trades"][0]["profit"] is None
    assert pending["trades"][0]["pnl_status"] == "fees_pending"
    assert pending["cost_summary"]["net_realized_pnl"] is None
    assert pending["cost_summary"]["opening_commission"] is None
    ledger.query("UPDATE qd_strategy_trades SET fee_status='actual' WHERE grid_order_id=1")
    assert read()["trades"][0]["profit"] == pytest.approx(.799)


def test_grid_stream_does_not_use_order_limit_when_fill_price_missing(ledger):
    from app.services.live_trading.base import LiveTradingError
    event,binding=ledger.event()
    event.update(price=0, cumulative_average_price=0,raw_json={})
    with pytest.raises(LiveTradingError, match="fillSnapshotNotReady"):
        ledger.processor._project_grid(event,binding)
    assert ledger.query("SELECT * FROM qd_strategy_trades") == []


def test_reported_pnl_database_claims_limit_accounts_and_reuse_cache(ledger, monkeypatch):
    from app.services.execution_streams import pnl_reconciliation as pnl
    from app.services.execution_streams.reported_pnl import fill_report
    key = (1, "binance", "swap", "BTC/USDT", "order1")
    assert pnl._claim(key, 1)
    assert not pnl._claim(key, 1)
    for i in range(2, 5):
        assert pnl._claim((*key[:-1], "order" + str(i)), 1)
    assert not pnl._claim((*key[:-1], "order5"), 1)
    assert pnl._claim((2, *key[1:]), 1)
    report = fill_report([dict(id="f", quantity=1, pnl=-3)], expected=1, source="rest:test", currency="USDT")
    pnl._save(key, report)
    rows = [dict(id=1, credential_id=1, exchange_id="binance", market_type="swap", symbol="BTC/USDT",
                 exchange_order_id="order1", amount=1, type="close_long", profit=100)]
    monkeypatch.setattr(pnl, "_client", lambda *a: pytest.fail("Cached result must not query venue"))
    pnl.enrich_reported_order_pnl(rows, user_id=1, trading_config={})
    assert rows[0]["exchange_pnl"]["amount"] == -3
    assert rows[0]["profit"] == 100
    rows[0]["amount"] = 2
    pnl.enrich_reported_order_pnl(rows, user_id=1, trading_config={})
    assert rows[0]["exchange_pnl"]["status"] == "pending"


def test_reported_pnl_reads_durable_events_without_another_venue_query(ledger, monkeypatch):
    from app.services.execution_streams import pnl_reconciliation as pnl
    ledger.query("""INSERT INTO qd_execution_events
        (event_key,credential_id,exchange_id,market_type,symbol,exchange_order_id,exchange_fill_id,quantity,realized_pnl,raw_json,occurred_at)
        VALUES ('test-pnl',1,'binance','swap','BTC/USDT','o1','f1',1,-5,'{"o":{"rp":"-5"}}',NOW())""")
    rows = [dict(id=1, credential_id=1, exchange_id="binance", market_type="swap", symbol="BTC/USDT",
                 exchange_order_id="o1", amount=1, type="close_long", profit=100)]
    monkeypatch.setattr(pnl, "_client", lambda *a: pytest.fail("WS evidence is complete"))
    pnl.enrich_reported_order_pnl(rows, user_id=1, trading_config={})
    assert rows[0]["exchange_pnl"]["amount"] == -5
    assert rows[0]["exchange_pnl"]["source"] == "websocket:rp"
