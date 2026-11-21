from datetime import datetime, timezone
import sqlite3

import pytest

from app.services.strategy_daily_pnl import (
    _load_current_equity,
    choose_opening_equity,
    resolve_business_day_window,
)


def test_business_day_window_uses_client_iana_timezone():
    start, end, name = resolve_business_day_window(
        now=datetime(2026, 7, 21, 18, 30, tzinfo=timezone.utc),
        timezone_name="Asia/Shanghai",
    )

    assert name == "Asia/Shanghai"
    assert start == datetime(2026, 7, 21, 16, 0)
    assert end == datetime(2026, 7, 22, 16, 0)


def test_opening_equity_prefers_nearest_midnight_snapshot():
    day_start = datetime(2026, 7, 21, 16, 0)
    opening, estimated, source = choose_opening_equity(
        day_start=day_start,
        before={"equity": 1010, "captured_at": datetime(2026, 7, 21, 15, 58)},
        after={"equity": 1011, "captured_at": datetime(2026, 7, 21, 16, 4)},
        reconstructed=999,
    )

    assert opening == 1010
    assert estimated is False
    assert source == "snapshot_before"


def test_opening_equity_marks_ledger_reconstruction_as_estimated():
    opening, estimated, source = choose_opening_equity(
        day_start=datetime(2026, 7, 21, 16, 0),
        before=None,
        after=None,
        reconstructed=987.5,
    )

    assert opening == 987.5
    assert estimated is True
    assert source == "ledger_reconstruction"


def test_current_equity_query_escapes_literal_percent_for_postgres(monkeypatch):
    class Cursor:
        def execute(self, query, params):
            query % tuple(repr(value) for value in params)

        def fetchall(self):
            return []

        def close(self):
            return None

    class Connection:
        def cursor(self):
            return Cursor()

    class ConnectionContext:
        def __enter__(self):
            return Connection()

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(
        "app.services.strategy_daily_pnl.get_db_connection",
        lambda: ConnectionContext(),
    )

    assert _load_current_equity([1], user_id=2) == {}


@pytest.fixture
def equity_ledger(monkeypatch):
    """Execute the API and executor aggregate queries against the same isolated ledger."""
    connection = sqlite3.connect(":memory:")
    connection.row_factory = lambda _cursor, row: dict(zip(
        (column[0] for column in _cursor.description), row,
    ))
    connection.executescript("""
        CREATE TABLE qd_strategies_trading (
            id INTEGER, user_id INTEGER, created_at TEXT, status TEXT,
            execution_mode TEXT, initial_capital REAL
        );
        CREATE TABLE qd_strategy_trades (
            strategy_id INTEGER, strategy_run_id INTEGER, type TEXT,
            profit REAL, commission_quote REAL, commission REAL,
            created_at TEXT DEFAULT '2026-01-01'
        );
        CREATE TABLE qd_strategy_funding_fees (strategy_id INTEGER, amount REAL, occurred_at TEXT);
        CREATE TABLE qd_strategy_broker_activities (strategy_id INTEGER, amount REAL, occurred_at TEXT);
        CREATE TABLE qd_strategy_positions (
            strategy_id INTEGER, size REAL, unrealized_pnl REAL,
            side TEXT, entry_price REAL, current_price REAL
        );
        CREATE TABLE qd_strategy_virtual_accounts (strategy_id INTEGER, realized_pnl REAL);
        CREATE TABLE qd_strategy_virtual_trades (strategy_id INTEGER, type TEXT, profit REAL);
        CREATE TABLE qd_strategy_virtual_positions (strategy_id INTEGER, size REAL, unrealized_pnl REAL);
        INSERT INTO qd_strategies_trading VALUES (7, 2, '2026-01-01', 'running', 'live', 500);
        INSERT INTO qd_strategy_trades (strategy_id, strategy_run_id, type, profit, commission_quote, commission) VALUES
            (7, 10, 'open_long', NULL, 200.75, 999),
            (7, 10, 'close_long', -53.54, NULL, 200.75),
            (7, 11, 'close_long', 0, 0, 0),
            (99, 11, 'close_long', 100000, 0, 0);
        INSERT INTO qd_strategy_funding_fees VALUES (7, -8.59, '2026-01-01'), (99, 10000, '2026-01-01');
        INSERT INTO qd_strategy_broker_activities VALUES (7, -0.01, '2026-01-01'), (99, 10000, '2026-01-01');
    """)

    class Cursor:
        def __init__(self):
            self.inner = connection.cursor()

        def execute(self, query, params):
            self.inner.execute(query.replace("%s", "?").replace("%%", "%"), params)

        def fetchone(self):
            return self.inner.fetchone()

        def fetchall(self):
            return self.inner.fetchall()

        def close(self):
            self.inner.close()

    class Database:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def cursor(self):
            return Cursor()

    for module in ("trading_executor", "strategy_daily_pnl"):
        monkeypatch.setattr(f"app.services.{module}.get_db_connection", Database)
    monkeypatch.setattr("app.routes.strategy_ledger_routes.get_db_connection", Database)
    yield connection
    connection.close()


@pytest.mark.parametrize("run_id", [0, 10, 11, 12])
def test_restarted_strategy_sizes_from_same_lifetime_equity_as_api(equity_ledger, run_id):
    from app.services.trading_executor import TradingExecutor
    from app.services.strategy_v2 import OrderIntent
    from app.services.pending_orders.order_budget import strategy_order_budget_snapshot

    executor = TradingExecutor.__new__(TradingExecutor)
    executor._load_strategy = lambda _sid: {"execution_mode": "live"}
    equity = executor._calculate_current_equity(7, 500, strategy_run_id=run_id)
    api_equity = _load_current_equity([7], user_id=2)[7]["equity"]
    assert equity == pytest.approx(36.36)
    assert equity == pytest.approx(api_equity)

    quantity = executor._target_amount(
        OrderIntent(symbol="Crypto:BTC/USDT@swap", kind="target_percent", value=.95),
        current=0, capital=equity, price=60000, leverage=2, market_type="swap",
    )
    assert quantity * 60000 == pytest.approx(69.084)
    args = dict(price=60000, initial_capital=equity, leverage=2, market_type="swap")
    assert strategy_order_budget_snapshot(action="open_long", quantity=quantity, **args)["allowed"]
    assert not strategy_order_budget_snapshot(action="open_long", quantity=800 / 60000, **args)["allowed"]
    assert strategy_order_budget_snapshot(action="close_long", quantity=800 / 60000, **args)["allowed"]


def test_lifetime_equity_retains_unrealized_and_clamps_only_execution_budget(equity_ledger):
    from app.services.trading_executor import TradingExecutor

    executor = TradingExecutor.__new__(TradingExecutor)
    executor._load_strategy = lambda _sid: {"execution_mode": "live"}
    equity_ledger.execute("INSERT INTO qd_strategy_positions VALUES (7, 1, -50, 'long', 100, 50)")
    equity = executor._calculate_current_equity(
        7, 500, strategy_run_id=12,
        current_positions=[{"symbol": "BTC/USDT", "side": "long", "size": 1, "entry_price": 100}],
        current_prices={"Crypto:BTC/USDT@swap": 50},
    )
    assert _load_current_equity([7])[7]["equity"] == pytest.approx(-13.64)
    assert equity == 0


def test_signal_equity_uses_virtual_ledger_not_live_history(equity_ledger):
    from app.services.trading_executor import TradingExecutor

    equity_ledger.execute("UPDATE qd_strategies_trading SET execution_mode = 'signal' WHERE id = 7")
    equity_ledger.execute("INSERT INTO qd_strategy_virtual_accounts VALUES (7, -25)")
    executor = TradingExecutor.__new__(TradingExecutor)
    executor._load_strategy = lambda _sid: {"execution_mode": "signal"}
    assert executor._calculate_current_equity(7, 500, strategy_run_id=12) == 475
    assert _load_current_equity([7])[7]["equity"] == 475


@pytest.mark.parametrize("size", [0, 0.000000000000000002])
def test_old_flat_position_cannot_contribute_stale_floating_profit(equity_ledger, size):
    equity_ledger.execute(
        "INSERT INTO qd_strategy_positions VALUES (7, ?, 27.114474, 'long', 58529.55302631579, 60313.3)",
        (size,),
    )
    metrics = _load_current_equity([7])[7]
    assert metrics["unrealized"] == pytest.approx(0, abs=1e-12)
    assert metrics["equity"] == pytest.approx(36.36)


@pytest.mark.parametrize("side,expected", [("long", 0.01), ("short", -0.01)])
def test_small_real_position_is_valued_not_discarded(equity_ledger, side, expected):
    equity_ledger.execute(
        "INSERT INTO qd_strategy_positions VALUES (7, 0.00001, 99, ?, 60000, 61000)", (side,),
    )
    metrics = _load_current_equity([7])[7]
    assert metrics["unrealized"] == pytest.approx(expected)
    assert metrics["equity"] == pytest.approx(36.36 + expected)


def test_equity_curve_also_ignores_old_floating_profit(equity_ledger, monkeypatch):
    from types import SimpleNamespace
    from app.routes import strategy_ledger_routes as routes

    monkeypatch.setattr(routes, "get_strategy_service", lambda: SimpleNamespace(
        get_strategy=lambda *_args, **_kwargs: {"initial_capital": 500, "execution_mode": "live"},
    ))
    equity_ledger.execute(
        "INSERT INTO qd_strategy_positions VALUES (7, 2e-18, 27.114474, 'long', 58529.55302631579, 60313.3)"
    )
    curve, error = routes._build_strategy_equity_curve(2, 7)
    assert error is None
    assert curve[-1]["equity"] == pytest.approx(36.36)


@pytest.mark.parametrize("mode", ["live", "signal"])
def test_unavailable_equity_never_falls_back_to_initial_capital(monkeypatch, mode):
    from app.services.trading_executor import TradingExecutor

    def unavailable():
        raise RuntimeError("ledger unavailable")

    monkeypatch.setattr("app.services.trading_executor.get_db_connection", unavailable)
    executor = TradingExecutor.__new__(TradingExecutor)
    executor._load_strategy = lambda _sid: {"execution_mode": mode}
    with pytest.raises(RuntimeError, match="ledger unavailable"):
        executor._calculate_current_equity(7, 500, strategy_run_id=12)
