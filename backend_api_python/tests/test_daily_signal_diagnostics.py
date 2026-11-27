from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest

from app.services import market_schedule, trading_executor as runtime
from app.services.strategy_runtime import candle_readiness


@pytest.mark.parametrize("market,symbol,hour", [("USStock", "AAPL", 15), ("HKStock", "00700.HK", 2)])
def test_daily_loop_waits_for_quotes_then_records_zero_intents_once(monkeypatch, market, symbol, hour):
    key = f"{market}:{symbol}"
    now = pd.Timestamp(f"2026-11-25T{hour:02d}:00:00Z")
    tick = [100.]
    fresh = [False]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.to_pydatetime().astimezone(tz or timezone.utc)

    class Timestamp(pd.Timestamp):
        @classmethod
        def now(cls, tz=None):
            return now.tz_convert(tz or "UTC")

    proxy = Mock(wraps=pd)
    proxy.Timestamp = Timestamp
    proxy.DataFrame = pd.DataFrame
    monkeypatch.setattr(runtime, "pd", proxy)
    for module in (runtime, candle_readiness, market_schedule):
        monkeypatch.setattr(module, "datetime", Clock)
    monkeypatch.setattr(runtime, "time", SimpleNamespace(monotonic=lambda: tick[0], time=lambda: now.timestamp()))
    monkeypatch.setenv("BAR_CLOSE_EVENT_SCHEDULER_ENABLED", "0")
    code = f'''
def initialize(context):
    context.set_universe(["{key}"])
    context.subscribe(frequency="1d")
    context.set_warmup(2)

def handle_data(context, data):
    if data.history("{key}", count=1, fields=["close"]).close.iloc[-1] < 50:
        order_target_percent("{key}", 0.1)
'''
    frame = pd.DataFrame({"open": 100., "high": 101., "low": 99., "close": 100., "volume": 1.},
                         index=pd.date_range("2026-11-20", "2026-11-24", freq="B"))
    members = [{"key": key, "market": market, "symbol": symbol, "market_type": "spot"}]
    monkeypatch.setattr(runtime.StrategyV2BacktestService, "resolve_candidates", lambda *a, **kw: (members, 0))
    monkeypatch.setattr(runtime.StrategyV2BacktestService, "fetch_frequency_frames", lambda *a, **kw: ({"1d": {key: frame}}, []))
    monkeypatch.setattr(runtime.DataSourceFactory, "get_tickers", lambda *a, **kw: {
        symbol: {"last": 100., "quote_source": "test", "quote_time": now.timestamp() - (0 if fresh[0] else 600)},
    })
    monkeypatch.setattr(runtime, "build_runtime_event_bus", Mock(return_value=Mock()))
    executor = runtime.TradingExecutor(external_bar_triggers=False)
    strategy = {"id": 1, "user_id": 1, "execution_mode": "live", "initial_capital": 1000, "market_category": market}
    monkeypatch.setattr(executor, "_load_strategy", lambda _: strategy)
    monkeypatch.setattr(executor, "_load_source", lambda _: (1, code))
    monkeypatch.setattr(executor, "_load_schedule_timezone", lambda _: "UTC")
    monkeypatch.setattr(executor, "_positions_by_symbol", lambda *a, **kw: {})
    monkeypatch.setattr(executor, "_calculate_current_equity", lambda *a, **kw: 1000)
    monkeypatch.setattr(executor, "_register_event_subscriptions", lambda *a: None)
    monkeypatch.setattr(executor, "_mark_stopped", Mock())
    heartbeat, submit, logs = Mock(), Mock(), Mock()
    monkeypatch.setattr(executor, "_heartbeat", heartbeat)
    monkeypatch.setattr(executor, "_execute_strategy_v2_intent", submit)
    monkeypatch.setattr(runtime, "append_strategy_log", logs)
    monkeypatch.setattr(runtime, "ensure_strategy_run", lambda **kw: SimpleNamespace(strategy_run_id=1, runtime_epoch=1))
    monkeypatch.setattr(runtime, "finish_strategy_run", Mock())
    store = Mock()
    store.load.return_value = {}
    monkeypatch.setattr(runtime, "RuntimeStateStore", lambda **kw: store)
    intents = Mock()
    intents.statuses_by_client_order_ids.return_value = {}
    monkeypatch.setattr(runtime, "OrderIntentService", lambda **kw: intents)
    monkeypatch.setattr(runtime, "persist_cancellations", Mock())
    cycles = iter([True, True, True, False])
    monkeypatch.setattr(executor, "_is_strategy_running", lambda *a: next(cycles))
    try:
        for index, _ in enumerate(executor._run_strategy_loop(1)):
            if index == 0:
                assert "Daily signal waiting for fresh quotes" in str(logs.call_args_list)
                assert "Daily signal evaluated" not in str(logs.call_args_list)
                assert heartbeat.call_args.kwargs["status"] == "degraded"
            fresh[0] = True
            tick[0] += 60
    finally:
        executor.close()
    assert executor._last_exit_reason == {}, logs.call_args_list
    submit.assert_not_called()
    evaluated = [call for call in logs.call_args_list if "Daily signal evaluated" in str(call)]
    assert len(evaluated) == 1, logs.call_args_list
    assert "intents=0" in str(evaluated[0])
    assert "Daily signal quote requirements recovered" in str(logs.call_args_list)
    assert heartbeat.call_args.kwargs["status"] == "healthy"
