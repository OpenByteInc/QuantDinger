import pandas as pd

from app.services.strategy_v2 import StrategyV2BacktestRunner, StrategyV2LiveSession
from app.services.strategy_v2.data import MultiAssetDataPortal
from app.services.strategy_v2.models import ScheduleSpec
from app.services.strategy_v2.runtime import MultiAssetSimulationBroker, OrderIntent, Position


def _frame(prices):
    index = pd.date_range("2026-01-01", periods=len(prices), freq="D")
    return pd.DataFrame({
        "open": prices,
        "high": [price * 1.01 for price in prices],
        "low": [price * 0.99 for price in prices],
        "close": prices,
        "volume": [100000] * len(prices),
    }, index=index)


def test_data_portal_caches_timestamps_and_slices_point_in_time_history():
    portal = MultiAssetDataPortal({"USStock:AAPL": _frame(range(1000))})
    cached_timestamps = portal.timestamps

    portal.set_clock(cached_timestamps[500], include_current=False)
    previous = portal.visible_frame("AAPL", count=2)
    portal.set_clock(cached_timestamps[500], include_current=True)
    current = portal.visible_frame("AAPL", count=2)

    assert portal.timestamps is cached_timestamps
    assert list(previous.index) == list(cached_timestamps[498:500])
    assert list(current.index) == list(cached_timestamps[499:501])


def test_multi_asset_strategy_controls_symbols_and_rebalances_without_ui_market_fields():
    code = """
def initialize(context):
    context.set_universe(["USStock:AAPL", "USStock:MSFT"])
    context.subscribe(frequency="1d")
    run_daily(rebalance, time="09:35")

def rebalance(context, data):
    order_target_percent("AAPL", 0.5)
    order_target_percent("MSFT", 0.5)
"""
    runner = StrategyV2BacktestRunner(
        code=code,
        frames={
            "USStock:AAPL": _frame([100, 101, 102]),
            "USStock:MSFT": _frame([200, 202, 204]),
        },
        initial_capital=10000,
        commission=0,
        slippage=0,
    )

    result = runner.run()

    assert result["engine"]["version"] == "quantdinger-strategy-api-v2"
    assert result["manifest"]["strategyType"] == "portfolio"
    assert {trade["symbol"] for trade in result["rawTrades"]} == {"USStock:AAPL", "USStock:MSFT"}
    assert result["totalExecutions"] >= 2
    assert result["finalEquity"] > 10000


def test_history_is_point_in_time_and_close_signal_fills_next_open():
    code = """
def initialize(context):
    context.set_universe(["USStock:AAPL"])
    context.subscribe(frequency="1d")

def handle_data(context, data):
    bars = get_history(10, security_list="AAPL")
    if len(bars) == 1:
        order_target_percent("AAPL", 1.0)
"""
    runner = StrategyV2BacktestRunner(
        code=code,
        frames={"USStock:AAPL": _frame([100, 110, 121])},
        initial_capital=10000,
        commission=0,
        slippage=0,
    )

    result = runner.run()

    assert len(result["rawTrades"]) == 1
    assert result["rawTrades"][0]["time"].startswith("2026-01-02")
    assert result["rawTrades"][0]["price"] == 110


def test_full_target_percent_reserves_commission_instead_of_rejecting_order():
    code = """
def initialize(context):
    context.set_universe(["USStock:AAPL"])
    context.subscribe(frequency="1d")

def handle_data(context, data):
    order_target_percent("AAPL", 1.0)
"""
    result = StrategyV2BacktestRunner(
        code=code,
        frames={"USStock:AAPL": _frame([100, 101, 102])},
        initial_capital=10000,
        commission=0.0005,
        slippage=0.0005,
    ).run()

    assert result["totalTrades"] == 0
    assert result["totalExecutions"] == 1
    assert result["positions"]["USStock:AAPL"]["amount"] > 0


def test_swap_margin_budget_expands_target_percent_by_leverage():
    broker = MultiAssetSimulationBroker(
        initial_capital=10_000,
        leverage=5,
        commission=0,
        slippage=0,
    )
    order = OrderIntent(symbol="Crypto:BTC/USDT@okx:swap", kind="target_percent", value=0.25)

    target = broker._target_quantity(
        order,
        Position(order.symbol),
        price=100,
        equity=10_000,
    )

    assert target == 125.0


def test_explicit_backtest_quantity_is_not_scaled_by_leverage():
    broker = MultiAssetSimulationBroker(initial_capital=10_000, leverage=5)
    order = OrderIntent(symbol="Crypto:BTC/USDT@okx:swap", kind="target_quantity", value=2.5)

    target = broker._target_quantity(
        order,
        Position(order.symbol),
        price=100,
        equity=10_000,
    )

    assert target == 2.5


def test_runtime_rejects_leverage_not_declared_by_strategy():
    code = """
def initialize(context):
    context.set_universe(["Crypto:BTC/USDT@okx:swap"])
    context.subscribe(frequency="1d")

def handle_data(context, data):
    pass
"""
    try:
        StrategyV2BacktestRunner(
            code=code,
            frames={"Crypto:BTC/USDT@okx:swap": _frame([100, 101])},
            initial_capital=10000,
            leverage_enabled=True,
            leverage=2,
        )
    except ValueError as exc:
        assert str(exc) == "strategyV2.leverageNotAllowed"
    else:
        raise AssertionError("Expected leverage policy rejection")


def test_runtime_helpers_and_logger_are_supported():
    code = """
def initialize(context):
    g.sec_code = "600519.XSHG"
    context.set_universe([g.sec_code])
    context.subscribe(frequency="1d")
    log.info(context.current_dt)
    run_daily(daily_event, time="14:50")

def daily_event(context):
    if not is_trade():
        return
    bars = get_history(2, "1d", "close", g.sec_code, fq="pre", include=True)
    position = get_position(g.sec_code)
    log.info("position=%s" % position.amount)
    if len(bars) >= 1 and position.amount == 0:
        order_target_value(g.sec_code, context.portfolio.available_cash)
"""
    runner = StrategyV2BacktestRunner(
        code=code,
        frames={"CNStock:600519.SH": _frame([100, 101, 102])},
        initial_capital=10000,
        commission=0,
        slippage=0,
    )

    result = runner.run()

    assert result["totalTrades"] == 0
    assert result["totalExecutions"] == 1
    assert result["sampleCount"] == len(result["equityCurve"])
    assert any("position=0.0" in item for item in result["logs"])
    position = next(iter(result["positions"].values()))
    assert position["amount"] > 0


def test_backtest_separates_executions_from_closed_trades_and_realized_metrics():
    code = """
def initialize(context):
    context.set_universe(["USStock:AAPL"])
    context.subscribe(frequency="1d")
    g.calls = 0

def handle_data(context, data):
    g.calls += 1
    if g.calls == 1:
        order_target_percent("AAPL", 0.5, reason="entry")
    elif g.calls == 2:
        order_target_percent("AAPL", 0.0, reason="exit")
"""
    result = StrategyV2BacktestRunner(
        code=code,
        frames={"USStock:AAPL": _frame([100, 110, 120, 130])},
        initial_capital=10000,
        commission=0,
        slippage=0,
    ).run()

    assert result["totalExecutions"] == 2
    assert result["totalTrades"] == 1
    assert result["rawTrades"][0]["type"] == "open_long"
    assert result["rawTrades"][1]["type"] == "close_long"
    assert result["closedTrades"][0]["entry_price"] == 110
    assert result["closedTrades"][0]["exit_price"] == 120
    assert result["closedTrades"][0]["profit"] > 0
    assert result["winRate"] == 100.0
    assert result["profitFactor"] > 0
    assert result["avgTrade"] > 0


def test_live_session_processes_each_closed_bar_once_and_preserves_state():
    code = """
def initialize(context):
    context.set_universe(["USStock:AAPL"])
    context.subscribe(frequency="1d")
    g.calls = 0

def handle_data(context, data):
    g.calls += 1
    if g.calls == 1:
        order_target_percent("AAPL", 0.5)
"""
    session = StrategyV2LiveSession(
        code=code,
        frames={"USStock:AAPL": _frame([100, 101])},
        initial_capital=10000,
    )

    first_orders, _, first_timestamp = session.process({"USStock:AAPL": _frame([100, 101])})
    duplicate_orders, _, duplicate_timestamp = session.process({"USStock:AAPL": _frame([100, 101])})
    next_orders, _, next_timestamp = session.process({"USStock:AAPL": _frame([100, 101, 102])})

    assert len(first_orders) == 1
    assert first_orders[0].kind == "target_percent"
    assert duplicate_orders == []
    assert next_orders == []
    assert first_timestamp == duplicate_timestamp
    assert next_timestamp > first_timestamp


def test_get_fundamentals_resolves_public_api_field_aliases():
    frame = _frame([100, 101])
    frame["pe_ratio"] = [20.0, 21.0]
    frame["return_on_equity"] = [0.10, 0.12]
    code = """
def initialize(context):
    context.set_universe(["USStock:AAPL"])
    context.subscribe(frequency="1d")

def handle_data(context, data):
    values = get_fundamentals(["PE", "ROE"], "AAPL")
    if not values.empty:
        log.info("pe=%s,roe=%s" % (values.iloc[0]["PE"], values.iloc[0]["ROE"]))
"""
    result = StrategyV2BacktestRunner(
        code=code,
        frames={"USStock:AAPL": frame},
        initial_capital=10000,
    ).run()

    assert any("pe=21.0,roe=0.12" in item for item in result["logs"])


def test_scheduler_honors_weekday_monthday_and_intraday_time():
    weekly = ScheduleSpec("weekly", "rebalance", weekday=3, time="09:35")
    monthly = ScheduleSpec("monthly", "rebalance", monthday=15, time="09:35")
    daily = ScheduleSpec("daily", "rebalance", time="09:35")

    assert not StrategyV2BacktestRunner._schedule_due(
        weekly, pd.Timestamp("2026-01-06"), pd.Timestamp("2026-01-05"), "1d"
    )
    assert StrategyV2BacktestRunner._schedule_due(
        weekly, pd.Timestamp("2026-01-07"), pd.Timestamp("2026-01-06"), "1d"
    )
    assert StrategyV2BacktestRunner._schedule_due(
        monthly, pd.Timestamp("2026-01-16"), pd.Timestamp("2026-01-14"), "1d"
    )
    assert not StrategyV2BacktestRunner._schedule_due(
        daily, pd.Timestamp("2026-01-05 09:30"), pd.Timestamp("2026-01-05 09:25"), "5m"
    )
    assert StrategyV2BacktestRunner._schedule_due(
        daily, pd.Timestamp("2026-01-05 09:35"), pd.Timestamp("2026-01-05 09:30"), "5m"
    )


def test_rejected_and_deferred_orders_are_visible_in_audit_ledger():
    frame = _frame([100, 101, 102])
    frame["is_suspended"] = [False, True, False]
    frame["lot_size"] = [10, 10, 10]
    code = """
def initialize(context):
    context.set_universe(["USStock:AAPL"])
    context.subscribe(frequency="1d")

def handle_data(context, data):
    order_target_value("AAPL", 50)
"""
    result = StrategyV2BacktestRunner(
        code=code,
        frames={"USStock:AAPL": frame},
        initial_capital=10000,
        commission=0,
        slippage=0,
    ).run()

    statuses = {(item["status"], item["statusReason"]) for item in result["orderLedger"]}
    assert ("deferred", "suspended") in statuses
    assert ("rejected", "minimum_trade_unit") in statuses
    assert result["attribution"]["orderStatus"]["deferred"] >= 1
    assert result["holdingSnapshots"]


def test_deferred_target_order_never_reverses_its_original_direction():
    frame = _frame([100, 100, 1000, 1000])
    frame["volume"] = [1, 1, 1, 1]
    code = """
def initialize(context):
    g.symbol = "Crypto:BTC/USDT"
    g.sent = False
    context.set_universe([g.symbol])
    context.subscribe(frequency="1d")

def handle_data(context, data):
    if not g.sent:
        order_target_percent(g.symbol, 0.5, reason="entry")
        g.sent = True
"""
    result = StrategyV2BacktestRunner(
        code=code,
        frames={"Crypto:BTC/USDT": frame},
        initial_capital=100,
        commission=0,
        slippage=0,
    ).run()

    assert result["totalExecutions"] == 1
    assert result["totalTrades"] == 0
    assert result["rawTrades"][0]["side"] == "buy"
    assert any(
        item["statusReason"] == "target_already_met"
        for item in result["orderLedger"]
    )


def test_crypto_lot_rounding_is_filled_without_a_tail_retry():
    frame = _frame([58_700, 58_700, 58_700])
    code = """
def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.sent = False
    context.set_universe([g.symbol])
    context.subscribe(frequency="1m")

def handle_data(context, data):
    if not g.sent:
        order_target_percent(g.symbol, 0.95, reason="entry")
        g.sent = True
"""
    result = StrategyV2BacktestRunner(
        code=code,
        frames={"Crypto:BTC/USDT@swap": frame},
        initial_capital=10_000,
        commission=0.0005,
        slippage=0.0005,
    ).run()

    assert result["totalExecutions"] == 1
    assert result["rawTrades"][0]["status"] == "filled"
    assert result["attribution"]["orderStatus"] == {
        "filled": 1,
        "partial": 0,
        "deferred": 0,
        "rejected": 0,
    }
    assert not any(
        item["statusReason"] == "target_already_met"
        for item in result["orderLedger"]
    )


def test_crypto_target_reversals_do_not_retry_untradable_tail_quantities():
    frame = _frame([58_700] * 6)
    code = """
def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.step = 0
    context.set_universe([g.symbol])
    context.subscribe(frequency="1m")

def handle_data(context, data):
    target = 0.95 if g.step % 2 == 0 else -0.95
    order_target_percent(g.symbol, target, reason="regime_change")
    g.step += 1
"""
    result = StrategyV2BacktestRunner(
        code=code,
        frames={"Crypto:BTC/USDT@swap": frame},
        initial_capital=10_000,
        commission=0.0005,
        slippage=0.0005,
    ).run()

    assert result["totalExecutions"] == 5
    assert {item["status"] for item in result["rawTrades"]} == {"filled"}
    assert result["attribution"]["orderStatus"]["partial"] == 0
    assert result["attribution"]["orderStatus"]["rejected"] == 0
