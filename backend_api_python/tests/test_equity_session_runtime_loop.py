"""Exercise the real runtime sandbox across a closed-to-open session boundary."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest

from app.services import trading_executor as runtime
from app.services.strategy_v2 import StrategyV2LiveSession
from app.services.strategy_runtime import candle_readiness


@pytest.mark.parametrize("opening_price,exit_count,quote_age", [
    (100., 0, 0), (97., 1, 0), (106., 1, 0), (97., 1, 60),
    (97., 0, 600), (97., 0, None), (97., 0, -60),
])
def test_off_hours_prices_do_not_latch_protection_and_open_rechecks_fresh_price(
    monkeypatch, opening_price, exit_count, quote_age,
):
    symbol = "USStock:AAPL"
    clock = [pd.Timestamp("2026-11-25 09:00:00Z")]
    quote = [90.]
    monotonic = [100.]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].to_pydatetime().astimezone(tz or timezone.utc)

    class Timestamp(pd.Timestamp):
        @classmethod
        def now(cls, tz=None):
            return clock[0].tz_convert(tz or "UTC")

    pandas_proxy = Mock(wraps=pd)
    pandas_proxy.Timestamp = Timestamp
    pandas_proxy.DataFrame = pd.DataFrame
    monkeypatch.setattr(runtime, "pd", pandas_proxy)
    monkeypatch.setattr(runtime, "datetime", Clock)
    monkeypatch.setattr(candle_readiness, "datetime", Clock)
    monkeypatch.setattr(runtime, "time", SimpleNamespace(monotonic=lambda: monotonic[0], time=lambda: clock[0].timestamp()))
    monkeypatch.setenv("BAR_CLOSE_EVENT_SCHEDULER_ENABLED", "0")
    source = '''
def initialize(context):
    context.set_universe(["USStock:AAPL"])
    context.subscribe(frequency="1m")

def handle_data(context, data):
    if get_position("USStock:AAPL").amount == 0:
        order_target_percent("USStock:AAPL", 0.1, stop_loss_pct=0.02, take_profit_pct=0.05)
'''
    frame = pd.DataFrame(
        {"open": 100., "high": 101., "low": 99., "close": 100., "volume": 10.},
        index=pd.date_range("2026-11-24 19:00", periods=120, freq="min", tz="UTC"),
    )
    seed = StrategyV2LiveSession(code=source, frames={symbol: frame}, initial_capital=1000)
    seed.process({symbol: frame})
    seed.synchronize_positions({symbol: {"amount": 1, "avg_cost": 100, "last_price": 100}})
    snapshot = seed.session_snapshot()
    assert snapshot["protection"]["states"]

    candidates = [{"key": symbol, "market": "USStock", "symbol": "AAPL", "market_type": "spot"}]
    monkeypatch.setattr(runtime.StrategyV2BacktestService, "resolve_candidates", lambda *a, **kw: (candidates, 0))
    monkeypatch.setattr(runtime.StrategyV2BacktestService, "fetch_frequency_frames", lambda *a, **kw: ({"1m": {symbol: frame}}, []))
    # Candle sourcing is independently covered; this test isolates session and risk state.
    monkeypatch.setattr(candle_readiness, "runtime_frames_ready", lambda *a, **kw: True)
    monkeypatch.setattr(candle_readiness, "candles_settling", lambda *a, **kw: False)
    monkeypatch.setattr(runtime, "build_runtime_event_bus", Mock(return_value=Mock()))
    executor = runtime.TradingExecutor(external_bar_triggers=False)
    strategy = {"id": 1, "user_id": 1, "execution_mode": "live", "market_category": "USStock", "initial_capital": 1000}
    monkeypatch.setattr(executor, "_load_strategy", lambda _: strategy)
    monkeypatch.setattr(executor, "_load_source", lambda _: (1, source))
    monkeypatch.setattr(executor, "_load_schedule_timezone", lambda _: "UTC")
    monkeypatch.setattr(executor, "_positions_by_symbol", lambda *a, **kw: {symbol: {"amount": 1, "avg_cost": 100, "last_price": quote[0]}})
    monkeypatch.setattr(executor, "_calculate_current_equity", lambda *a, **kw: 1000)
    monkeypatch.setattr(runtime.DataSourceFactory, "get_tickers", lambda *a, **kw: {
        "AAPL": {"last": quote[0], "quote_time": (
            clock[0].timestamp() - quote_age if quote_age is not None else None
        )},
    })
    monkeypatch.setattr(executor, "_register_event_subscriptions", lambda *a: None)
    monkeypatch.setattr(executor, "_mark_stopped", Mock())
    heartbeat = Mock()
    monkeypatch.setattr(executor, "_heartbeat", heartbeat)
    cycles = iter([True, True, True, False])
    monkeypatch.setattr(executor, "_is_strategy_running", lambda *a: next(cycles))
    submit = Mock(return_value=True)
    monkeypatch.setattr(executor, "_execute_strategy_v2_intent", submit)
    logs = Mock()
    monkeypatch.setattr(runtime, "append_strategy_log", logs)
    monkeypatch.setattr(runtime, "ensure_strategy_run", lambda **kw: SimpleNamespace(strategy_run_id=11, runtime_epoch=1))
    monkeypatch.setattr(runtime, "finish_strategy_run", Mock())
    store = Mock()
    store.load.return_value = snapshot
    monkeypatch.setattr(runtime, "RuntimeStateStore", lambda **kw: store)
    intents = Mock()
    intents.statuses_by_client_order_ids.return_value = {}
    monkeypatch.setattr(runtime, "OrderIntentService", lambda **kw: intents)
    monkeypatch.setattr(runtime, "persist_cancellations", Mock())
    from app.services.live_trading import records
    monkeypatch.setattr(records, "mark_live_positions", Mock())

    sessions = []
    session_class = runtime.StrategyV2LiveSession

    def create_session(**kwargs):
        session = session_class(**kwargs)
        session.evaluate_protections = Mock(wraps=session.evaluate_protections)
        session.evaluate_equity_risk = Mock(wraps=session.evaluate_equity_risk)
        session.process = Mock(wraps=session.process)
        sessions.append(session)
        return session

    monkeypatch.setattr(runtime, "StrategyV2LiveSession", create_session)
    try:
        for index, _delay in enumerate(executor._run_strategy_loop(1)):
            if index < 2:
                sessions[0].evaluate_protections.assert_not_called()
                sessions[0].evaluate_equity_risk.assert_not_called()
                sessions[0].process.assert_not_called()
                submit.assert_not_called()
                assert heartbeat.call_args.kwargs["status"] == "healthy"
                assert heartbeat.call_args.kwargs["last_error"] == ""
                assert not any("Live price is stale" in str(call) for call in logs.call_args_list)
            monotonic[0] += 60
            clock[0] = pd.Timestamp("2026-11-25 14:32:00Z") if index == 1 else clock[0] + pd.Timedelta(minutes=1)
            if index == 1:
                quote[0] = opening_price
    finally:
        executor.close()

    assert executor._last_exit_reason == {}, logs.call_args_list
    assert heartbeat.call_count == 4
    sessions[0].evaluate_protections.assert_called_once()
    valid_quote = quote_age is not None and 0 <= quote_age <= 90
    assert heartbeat.call_args.kwargs["status"] == ("healthy" if valid_quote else "degraded")
    assert sessions[0].evaluate_equity_risk.call_count == int(valid_quote)
    assert sessions[0].evaluate_protections.call_args.args[0] == ({symbol: opening_price} if valid_quote else {})
    assert submit.call_count == exit_count, logs.call_args_list
    if exit_count:
        assert submit.call_args.kwargs["intent"].reason == ("stop_loss" if opening_price < 100 else "take_profit")
        assert submit.call_args.kwargs["current_price_override"] == opening_price
    assert sum("Equity strategy execution paused" in str(call) for call in logs.call_args_list) == 1


@pytest.mark.parametrize("status", ["submitted", "sent", "syncing", "accepted"])
def test_equity_exit_in_flight_survives_restart_and_rejection_requires_new_condition(status):
    symbol = "USStock:WDAY"
    source = '''
def initialize(context):
    context.set_universe(["USStock:WDAY"])
    context.subscribe(frequency="1d")

def handle_data(context, data):
    order_target_percent("USStock:WDAY", 0.1, stop_loss_pct=0.03)
'''
    frame = pd.DataFrame(
        {"open": [100.], "high": [101.], "low": [99.], "close": [100.], "volume": [1000.]},
        index=pd.DatetimeIndex(["2026-11-24T00:00:00Z"]),
    )
    positions = {symbol: {"amount": 1., "avg_cost": 100.}}
    first = StrategyV2LiveSession(code=source, frames={symbol: frame}, initial_capital=1000)
    first.process({symbol: frame})
    first.synchronize_positions(positions)
    intent = first.evaluate_protections({symbol: 96.}, timestamp="2026-11-24T20:59:30Z")[0]
    first.context.update_order_statuses({intent.client_order_id: {"status": status}})

    restored = StrategyV2LiveSession(code=source, frames={symbol: frame}, initial_capital=1000)
    restored.restore_session_snapshot(first.session_snapshot())
    restored.synchronize_positions(positions)
    assert restored.evaluate_protections({symbol: 95.}, timestamp="2026-11-25T14:31:00Z") == []

    restored.context.update_order_statuses({intent.client_order_id: {"status": "rejected"}})
    restored.synchronize_positions(positions)
    assert not restored.pending_protection_exit_symbols()
    assert restored.evaluate_protections({symbol: 100.}, timestamp="2026-11-25T14:31:01Z") == []
    retry = restored.evaluate_protections({symbol: 96.}, timestamp="2026-11-25T14:31:02Z")[0]
    assert retry.reason == "stop_loss"
    assert retry.client_order_id != intent.client_order_id
