import math

import pandas as pd
import pytest

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


def test_result_distinguishes_total_return_from_peak_to_trough_drawdown():
    runner = StrategyV2BacktestRunner(
        code="""
def initialize(context):
    context.set_universe(["USStock:AAPL"])
    context.subscribe(frequency="1d")

def handle_data(context, data):
    pass
""",
        frames={"USStock:AAPL": _frame([100, 101, 102])},
        initial_capital=100,
        commission=0,
        slippage=0,
    )
    runner.broker.equity_curve = [
        {"time": "2026-01-01", "value": 100.0},
        {"time": "2026-01-02", "value": 135.6793190007},
        {"time": "2026-01-03", "value": 94.5187761357},
    ]
    runner.broker.portfolio.total_value = 94.5187761357

    result = runner._result()

    assert result["totalReturn"] == pytest.approx(-5.4812238643)
    assert result["maxDrawdown"] == pytest.approx(-30.3366372769)
    assert result["maxDrawdownPeakEquity"] == pytest.approx(135.6793190007)
    assert result["maxDrawdownTroughEquity"] == pytest.approx(94.5187761357)
    assert result["maxDrawdownPeakTime"] == "2026-01-02"
    assert result["maxDrawdownTroughTime"] == "2026-01-03"
    assert result["equityCurve"][-1]["drawdown"] == pytest.approx(-30.3366372769)


def test_drawdown_uses_initial_capital_before_the_first_equity_sample():
    runner = StrategyV2BacktestRunner(
        code="""
def initialize(context):
    context.set_universe(["USStock:AAPL"])
    context.subscribe(frequency="1d")

def handle_data(context, data):
    pass
""",
        frames={"USStock:AAPL": _frame([100, 101, 102])},
        initial_capital=100,
        commission=0,
        slippage=0,
    )
    runner.broker.equity_curve = [
        {"time": "2026-01-01", "value": 99.0},
        {"time": "2026-01-02", "value": 101.0},
        {"time": "2026-01-03", "value": 100.0},
    ]
    runner.broker.portfolio.total_value = 100.0

    result = runner._result()

    assert result["maxDrawdown"] == pytest.approx(-1.0)
    assert result["maxDrawdownPeakEquity"] == pytest.approx(100.0)
    assert result["maxDrawdownTroughEquity"] == pytest.approx(99.0)
    assert result["maxDrawdownPeakTime"] == "2026-01-01"
    assert result["maxDrawdownTroughTime"] == "2026-01-01"
    assert result["equityCurve"][0]["drawdown"] == pytest.approx(-1.0)


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


def test_buy_limit_remains_resting_and_uses_favorable_gap_open():
    code = """
def initialize(context):
    context.set_universe(["USStock:AAPL"])
    context.subscribe(frequency="1d")
    g.sent = False

def handle_data(context, data):
    if not g.sent:
        order_value("AAPL", 95.0, order_type="limit", limit_price=95.0)
        g.sent = True
"""
    index = pd.date_range("2026-01-01", periods=3, freq="D")
    frame = pd.DataFrame({
        "open": [100.0, 100.0, 94.0],
        "high": [101.0, 101.0, 96.0],
        "low": [99.0, 99.0, 93.0],
        "close": [100.0, 100.0, 95.0],
        "volume": [100000.0] * 3,
    }, index=index)

    result = StrategyV2BacktestRunner(
        code=code,
        frames={"USStock:AAPL": frame},
        initial_capital=1000,
        commission=0,
        slippage=0,
    ).run()

    assert result["totalExecutions"] == 1
    assert result["executions"][0]["price"] == pytest.approx(94.0)
    assert result["executions"][0]["fill_reference"] == "gap_open"
    assert result["audit"]["passed"] is True
    assert any(
        row["status"] == "deferred" and row["statusReason"] == "limit_not_reached"
        for row in result["orderLedger"]
    )


def test_resting_limit_compacts_poll_events_without_changing_fill_or_status():
    code = """
def initialize(context):
    context.set_universe(["USStock:AAPL"])
    context.subscribe(frequency="1d")
    g.sent = False

def handle_data(context, data):
    if not g.sent:
        order_value(
            "AAPL",
            95.0,
            order_type="limit",
            limit_price=95.0,
            client_order_id="resting-buy",
        )
        g.sent = True
"""
    periods = 20
    index = pd.date_range("2026-01-01", periods=periods, freq="D")
    frame = pd.DataFrame({
        "open": [100.0] * (periods - 1) + [94.0],
        "high": [101.0] * (periods - 1) + [96.0],
        "low": [99.0] * (periods - 1) + [93.0],
        "close": [100.0] * (periods - 1) + [95.0],
        "volume": [100000.0] * periods,
    }, index=index)
    runner = StrategyV2BacktestRunner(
        code=code,
        frames={"USStock:AAPL": frame},
        initial_capital=1000,
        commission=0,
        slippage=0,
    )

    result = runner.run()

    resting = [
        item
        for item in result["orderLedger"]
        if item.get("statusReason") == "limit_not_reached"
    ]
    assert len(resting) == 1
    assert resting[0]["occurrenceCount"] == periods - 2
    assert resting[0]["firstEventTime"] < resting[0]["lastEventTime"]
    assert result["orderLedgerStats"] == {
        "storedEvents": 2,
        "eventOccurrences": periods - 1,
        "compactedOccurrences": periods - 3,
    }
    assert result["totalExecutions"] == 1
    assert result["executions"][0]["price"] == pytest.approx(94.0)
    status = runner.context.get_order_status("resting-buy")
    assert status["status"] == "filled"
    # Limit-order sizing is fixed at the submitted limit price; a favorable
    # gap improves cash usage without changing the requested base quantity.
    assert status["filled_quantity"] == pytest.approx(1.0)
    assert runner._order_status_cursor == len(runner.broker.order_ledger)


def test_buy_limit_fills_at_limit_when_touched_inside_bar():
    code = """
def initialize(context):
    context.set_universe(["USStock:AAPL"])
    context.subscribe(frequency="1d")
    g.sent = False

def handle_data(context, data):
    if not g.sent:
        order_value("AAPL", 95.0, order_type="limit", limit_price=95.0)
        g.sent = True
"""
    index = pd.date_range("2026-01-01", periods=2, freq="D")
    frame = pd.DataFrame({
        "open": [100.0, 100.0],
        "high": [101.0, 101.0],
        "low": [99.0, 94.0],
        "close": [100.0, 96.0],
        "volume": [100000.0] * 2,
    }, index=index)

    result = StrategyV2BacktestRunner(
        code=code,
        frames={"USStock:AAPL": frame},
        initial_capital=1000,
        commission=0,
        slippage=0,
    ).run()

    assert result["executions"][0]["price"] == pytest.approx(95.0)
    assert result["executions"][0]["fill_reference"] == "limit"
    assert result["audit"]["passed"] is True


def test_partial_incremental_limit_retries_only_the_remaining_notional():
    code = """
def initialize(context):
    context.set_universe(["Crypto:BTC/USDT@spot"])
    context.subscribe(frequency="1d")
    g.sent = False

def handle_data(context, data):
    if not g.sent:
        order_value(
            "Crypto:BTC/USDT@spot",
            100.0,
            order_type="limit",
            limit_price=100.0,
        )
        g.sent = True
"""
    index = pd.date_range("2026-01-01", periods=15, freq="D")
    frame = pd.DataFrame({
        "open": [100.0] * 15,
        "high": [101.0] * 15,
        "low": [99.0] * 15,
        "close": [100.0] * 15,
        # The simulator allows at most 10% of bar volume per fill.
        "volume": [1.0] * 15,
    }, index=index)

    result = StrategyV2BacktestRunner(
        code=code,
        frames={"Crypto:BTC/USDT@spot": frame},
        initial_capital=1000,
        commission=0,
        slippage=0,
    ).run()

    position = result["positions"]["Crypto:BTC/USDT@spot"]
    assert position["amount"] == pytest.approx(1.0)
    assert sum(row["notional"] for row in result["executions"]) == pytest.approx(100.0)
    assert result["audit"]["passed"] is True


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


def test_leveraged_backtest_force_closes_and_stops_after_insolvency():
    code = """
def initialize(context):
    context.set_universe(["Crypto:BTC/USDT@swap"])
    context.subscribe(frequency="1d")
    context.allow_leverage(max_leverage=5)

def handle_data(context, data):
    if get_position("Crypto:BTC/USDT@swap").amount == 0:
        order_target_percent("Crypto:BTC/USDT@swap", 0.95, reason="open_long")
"""
    result = StrategyV2BacktestRunner(
        code=code,
        frames={"Crypto:BTC/USDT@swap": _frame([100, 100, 70, 60, 50, 40])},
        initial_capital=10_000,
        leverage_enabled=True,
        leverage=5,
        commission=0,
        slippage=0,
    ).run()

    assert result["liquidated"] is True
    assert result["finalEquity"] == pytest.approx(0.0)
    assert result["totalReturn"] == pytest.approx(-100.0)
    assert result["annualizedReturn"] == pytest.approx(-100.0)
    assert result["totalExecutions"] == 2
    assert result["totalTrades"] == 1
    assert result["closedTrades"][0]["close_reason"] == "margin_liquidation"
    assert result["orderLedger"][-1]["statusReason"] == "margin_liquidation"
    assert result["audit"]["passed"] is True
    assert all(float(point["value"]) >= 0 for point in result["equityCurve"])


def test_leverage_does_not_change_regime_signal_count_while_account_is_solvent():
    code = """
def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    context.set_universe([g.symbol])
    context.subscribe(frequency="1d")
    context.allow_leverage(max_leverage=20)

def handle_data(context, data):
    bars = get_history(6, "1d", "close", g.symbol)
    if len(bars) < 5:
        return
    close = bars["close"]
    fast = float(close.tail(2).mean())
    slow = float(close.tail(5).mean())
    amount = float(get_position(g.symbol).amount or 0.0)
    target = 0.95 if fast > slow else -0.95
    if (target > 0 and amount <= 0) or (target < 0 and amount >= 0):
        order_target_percent(g.symbol, target, reason="regime_change")
"""
    prices = [100 + 2 * math.sin(index / 4) for index in range(120)]

    unleveraged = StrategyV2BacktestRunner(
        code=code,
        frames={"Crypto:BTC/USDT@swap": _frame(prices)},
        initial_capital=10_000,
        commission=0,
        slippage=0,
    ).run()
    leveraged = StrategyV2BacktestRunner(
        code=code,
        frames={"Crypto:BTC/USDT@swap": _frame(prices)},
        initial_capital=10_000,
        leverage_enabled=True,
        leverage=5,
        commission=0,
        slippage=0,
    ).run()

    assert unleveraged["liquidated"] is False
    assert leveraged["liquidated"] is False
    assert leveraged["totalExecutions"] == unleveraged["totalExecutions"]
    assert leveraged["totalTrades"] == unleveraged["totalTrades"]


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
    assert result["rawTrades"][0]["time"].endswith("Z")
    assert result["rawTrades"][0]["signal_time"].endswith("Z")
    assert result["closedTrades"][0]["entry_time"].endswith("Z")
    assert result["closedTrades"][0]["exit_time"].endswith("Z")
    assert all(point["time"].endswith("Z") for point in result["equityCurve"])
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


def test_live_daily_schedule_uses_wall_clock_without_startup_catch_up():
    code = """
def initialize(context):
    context.set_universe(["USStock:AAPL"])
    context.subscribe(frequency="1d")
    run_daily(rebalance, time="09:35")

def rebalance(context, data):
    order_target_percent("AAPL", 0.5)
"""
    frames = {"USStock:AAPL": _frame([100, 101])}
    session = StrategyV2LiveSession(
        code=code,
        frames=frames,
        initial_capital=10000,
        schedule_timezone="Asia/Shanghai",
    )

    startup_orders, _, _ = session.process(
        frames,
        schedule_time="2026-07-18 22:57:42+08:00",
    )
    early_orders, _, _ = session.process(
        frames,
        schedule_time="2026-07-19 09:34:59+08:00",
    )
    due_orders, _, _ = session.process(
        frames,
        schedule_time="2026-07-19 09:35:00+08:00",
    )
    duplicate_orders, _, _ = session.process(
        frames,
        schedule_time="2026-07-19 09:36:00+08:00",
    )

    assert startup_orders == []
    assert early_orders == []
    assert len(due_orders) == 1
    assert due_orders[0].signal_time == pd.Timestamp("2026-07-19 09:35:00+08:00")
    assert duplicate_orders == []


def test_live_daily_schedule_fires_without_a_new_daily_bar():
    code = """
def initialize(context):
    context.set_universe(["USStock:AAPL"])
    context.subscribe(frequency="1d")
    run_daily(rebalance, time="09:35")

def rebalance(context, data):
    order("AAPL", 1)
"""
    frames = {"USStock:AAPL": _frame([100])}
    session = StrategyV2LiveSession(
        code=code,
        frames=frames,
        initial_capital=10000,
        schedule_timezone="Asia/Shanghai",
    )

    session.process(frames, schedule_time="2026-07-19 09:34:00+08:00")
    orders, _, timestamp = session.process(
        frames,
        schedule_time="2026-07-19 09:35:00+08:00",
    )

    assert len(orders) == 1
    assert timestamp == frames["USStock:AAPL"].index[-1]


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


def test_position_cap_never_reverses_an_incremental_order_direction():
    broker = MultiAssetSimulationBroker(
        initial_capital=1000,
        commission=0.0005,
        slippage=0.0005,
    )
    position = Position(
        "Crypto:BTC/USDT@spot",
        amount=10,
        avg_cost=100,
        last_price=100,
    )
    broker.portfolio.positions[position.symbol] = position
    broker.portfolio.available_cash = 0.0001
    broker.portfolio.total_value = 1000.0001

    feasible, reason = broker._feasible_delta(
        delta=1,
        current=position,
        fill_price=100.05,
        equity=1000.0001,
        lot_size=1e-8,
        position_key=position.symbol,
    )

    assert feasible == 0
    assert reason == "position_limit"


def test_closed_trade_breaks_out_open_and_close_commission():
    frame = _frame([100, 100, 110, 110, 110])
    code = """
def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.step = 0
    context.set_universe([g.symbol])
    context.subscribe(frequency="1m")

def handle_data(context, data):
    if g.step == 0:
        order_target_value(g.symbol, 1000, reason="entry")
    elif g.step == 1:
        order_target_value(g.symbol, 0, reason="exit")
    g.step += 1
"""
    result = StrategyV2BacktestRunner(
        code=code, frames={"Crypto:BTC/USDT@swap": frame}, initial_capital=10_000,
        commission=0.001, slippage=0,
    ).run()

    trade = result["closedTrades"][0]
    assert trade["entry_commission"] > 0
    assert trade["exit_commission"] > 0
    assert trade["commission"] == pytest.approx(
        trade["entry_commission"] + trade["exit_commission"]
    )
    assert trade["profit"] == pytest.approx(trade["gross_profit"] - trade["commission"])


def test_grid_exit_uses_matched_cell_entry_without_hiding_account_realized_pnl():
    symbol = "Crypto:XAUT/USDT@swap"
    index = pd.date_range("2026-01-01", periods=4, freq="min")
    frame = pd.DataFrame({
        "open": [80.0, 90.0, 110.0, 105.0],
        "high": [80.0, 90.0, 110.0, 105.0],
        "low": [80.0, 90.0, 110.0, 105.0],
        "close": [80.0, 90.0, 110.0, 105.0],
        "volume": [100_000.0] * 4,
    }, index=index)
    code = """
def initialize(context):
    g.symbol = "Crypto:XAUT/USDT@swap"
    g.low_sent = False
    g.high_sent = False
    g.exit_sent = False
    context.set_universe([g.symbol])
    context.subscribe(frequency="1m")
    context.set_metadata(direction_mode="neutral")

def handle_data(context, data):
    if not g.low_sent:
        order(
            g.symbol, -1, position_side="short", order_type="limit",
            limit_price=90, reason="short_entry",
            client_order_id="grid-0-short-entry-1",
        )
        g.low_sent = True
    elif get_order_status("grid-0-short-entry-1")["status"] == "filled" and not g.high_sent:
        order(
            g.symbol, -1, position_side="short", order_type="limit",
            limit_price=110, reason="short_entry",
            client_order_id="grid-1-short-entry-1",
        )
        g.high_sent = True
    elif get_order_status("grid-1-short-entry-1")["status"] == "filled" and not g.exit_sent:
        order(
            g.symbol, 1, position_side="short", order_type="limit",
            limit_price=105, reason="short_exit",
            client_order_id="grid-1-short-exit-1",
        )
        g.exit_sent = True
"""

    result = StrategyV2BacktestRunner(
        code=code,
        frames={symbol: frame},
        initial_capital=1000,
        commission=0.001,
        slippage=0,
    ).run()

    trade = result["closedTrades"][0]
    assert trade["profit_basis"] == "grid_cell"
    assert trade["entry_price"] == pytest.approx(110)
    assert trade["matched_entry_price"] == pytest.approx(110)
    assert trade["exit_price"] == pytest.approx(105)
    assert trade["gross_profit"] == pytest.approx(5)
    assert trade["entry_commission"] == pytest.approx(0.11)
    assert trade["exit_commission"] == pytest.approx(0.105)
    assert trade["profit"] == pytest.approx(4.785)
    assert trade["grid_matched_profit"] == pytest.approx(4.785)
    assert trade["account_avg_entry_price"] == pytest.approx(100)
    assert trade["account_realized_profit"] == pytest.approx(-5.205)
    assert result["winRate"] == pytest.approx(100)
    assert result["gridMatchedProfit"] == pytest.approx(4.785)
    assert result["accountRealizedProfit"] == pytest.approx(-5.205)
    assert result["tradeProfitBasis"] == "grid_cell_when_available"
    assert result["attribution"]["symbols"][0]["realizedProfit"] == pytest.approx(-5.205)
    assert [row["client_order_id"] for row in result["executions"]] == [
        "grid-0-short-entry-1",
        "grid-1-short-entry-1",
        "grid-1-short-exit-1",
    ]


def test_live_session_snapshot_round_trips_strategy_timestamps_and_order_statuses():
    frame = _frame([100, 101])
    code = """
PERSIST_RUNTIME_STATE = True

def initialize(context):
    g.symbol = "Crypto:BTC/USDT@spot"
    g.last_order_at = None
    g.sent = False
    context.set_universe([g.symbol])
    context.subscribe(frequency="1d")

def handle_data(context, data):
    if not g.sent:
        g.last_order_at = context.current_dt
        g.reference = order_value(
            g.symbol,
            10,
            client_order_id="state-test-order",
        )
        g.sent = True
"""
    first = StrategyV2LiveSession(
        code=code,
        frames={"Crypto:BTC/USDT@spot": frame},
        initial_capital=100,
    )
    intents, _, _ = first.process(
        {"Crypto:BTC/USDT@spot": frame},
        schedule_time=frame.index[-1],
    )
    reference = intents[0].client_order_id
    first.context.update_order_statuses({
        reference: {
            "client_order_id": reference,
            "status": "submitted",
        }
    })

    restored = StrategyV2LiveSession(
        code=code,
        frames={"Crypto:BTC/USDT@spot": frame},
        initial_capital=100,
    )
    restored.restore_session_snapshot(first.session_snapshot())

    assert isinstance(restored.program.state.last_order_at, pd.Timestamp)
    assert restored.program.state.last_order_at == frame.index[-1]
    assert restored.program.state.reference == reference
    assert restored.context.get_order_status(reference)["status"] == "submitted"
    duplicate, _, _ = restored.process(
        {"Crypto:BTC/USDT@spot": frame},
        schedule_time=frame.index[-1],
    )
    assert duplicate == []


def test_custom_strategy_keeps_legacy_order_return_and_does_not_persist_g_by_default():
    frame = _frame([100, 101])
    code = """
def initialize(context):
    g.symbol = "Crypto:BTC/USDT@spot"
    g.counter = 0
    context.set_universe([g.symbol])
    context.subscribe(frequency="1d")

def handle_data(context, data):
    g.counter += 1
    g.legacy_result = order_value(g.symbol, 5)
    g.explicit_reference = order_value(
        g.symbol,
        5,
        client_order_id="custom-explicit-order",
    )
"""
    session = StrategyV2LiveSession(
        code=code,
        frames={"Crypto:BTC/USDT@spot": frame},
        initial_capital=100,
    )
    intents, _, _ = session.process(
        {"Crypto:BTC/USDT@spot": frame},
        schedule_time=frame.index[-1],
    )

    assert len(intents) == 2
    assert session.program.state.legacy_result is None
    assert session.program.state.explicit_reference == "custom-explicit-order"
    snapshot = session.session_snapshot()
    assert set(snapshot) == {"version", "protection"}

    restored = StrategyV2LiveSession(
        code=code,
        frames={"Crypto:BTC/USDT@spot": frame},
        initial_capital=100,
    )
    restored.restore_session_snapshot({
        **snapshot,
        "strategyState": {"counter": 99},
    })
    assert restored.program.state.counter == 0


def test_strategy_can_cancel_a_resting_limit_before_a_later_bar_crosses_it():
    index = pd.date_range("2026-01-01", periods=4, freq="1min")
    frame = pd.DataFrame({
        "open": [100, 100, 90, 90],
        "high": [101, 101, 91, 91],
        "low": [99, 99, 89, 89],
        "close": [100, 100, 90, 90],
        "volume": [1000] * 4,
    }, index=index)
    code = """
def initialize(context):
    g.symbol = "Crypto:BTC/USDT@spot"
    g.reference = ""
    g.cancelled = False
    context.set_universe([g.symbol])
    context.subscribe(frequency="1m")

def handle_data(context, data):
    if not g.reference:
        g.reference = order_value(
            g.symbol,
            50,
            order_type="limit",
            limit_price=95,
            client_order_id="cancel-me",
        )
        return
    if not g.cancelled and get_order_status(g.reference).get("status") == "deferred":
        g.cancelled = cancel_order(g.reference)
"""

    result = StrategyV2BacktestRunner(
        code=code,
        frames={"Crypto:BTC/USDT@spot": frame},
        initial_capital=100,
        commission=0,
        slippage=0,
    ).run()

    assert result["totalExecutions"] == 0
