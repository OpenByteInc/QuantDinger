"""Exercise wall-clock schedules through the real sandbox, without external I/O."""

from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest

from app.services import trading_executor as runtime
from app.services import virtual_trading
from app.services.strategy_runtime import candle_readiness


@pytest.mark.parametrize("replay_first", [False, True])
def test_daily_portfolio_schedules_execute_and_seal_separate_batches(monkeypatch, replay_first):
    clock = [pd.Timestamp("2026-09-14 15:00:05Z")]
    monotonic = [100.0]

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
    monkeypatch.setattr(runtime, "time", SimpleNamespace(monotonic=lambda: monotonic[0]))
    source = '''
def initialize(context):
    context.set_universe(["Crypto:BTC/USDT@binance:spot", "Crypto:ETH/USDT@binance:spot"])
    context.subscribe(frequency="1d")
    run_daily(rebalance, time="15:01")
    run_daily(rebalance, time="15:02")

def rebalance(context, data):
    order_target_percent("Crypto:BTC/USDT@binance:spot", 0.4)
    order_target_percent("Crypto:ETH/USDT@binance:spot", 0.4)
'''
    symbols = [f"Crypto:{coin}/USDT@binance:spot" for coin in ("BTC", "ETH")]
    candidates = [
        {"key": key, "market": "Crypto", "symbol": f"{coin}/USDT", "market_type": "spot", "exchange_id": "binance"}
        for coin, key in zip(("BTC", "ETH"), symbols)
    ]
    frame = pd.DataFrame(
        {"open": 100., "high": 102., "low": 99., "close": 101., "volume": 10.},
        index=pd.date_range("2026-09-01", periods=13, freq="D", tz="UTC"),
    )
    fetch = Mock(return_value=({"1d": {key: frame for key in symbols}}, []))
    monkeypatch.setattr(runtime.StrategyV2BacktestService, "resolve_candidates", lambda *a, **kw: (candidates, 0))
    monkeypatch.setattr(runtime.StrategyV2BacktestService, "fetch_frequency_frames", fetch)
    monkeypatch.setattr(runtime, "build_runtime_event_bus", Mock(return_value=Mock()))
    executor = runtime.TradingExecutor(external_bar_triggers=False)
    strategy = {"id": 1, "user_id": 1, "execution_mode": "signal", "initial_capital": 1000}
    monkeypatch.setattr(executor, "_load_strategy", lambda _: strategy)
    monkeypatch.setattr(executor, "_load_source", lambda _: (1, source))
    monkeypatch.setattr(executor, "_load_schedule_timezone", lambda _: "UTC")
    monkeypatch.setattr(executor, "_positions_by_symbol", lambda *a, **kw: {})
    monkeypatch.setattr(executor, "_calculate_current_equity", lambda *a, **kw: 1000)
    monkeypatch.setattr(executor, "_live_prices", lambda *a: {key: 110. for key in symbols})
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
    store.load.return_value = {}
    monkeypatch.setattr(runtime, "RuntimeStateStore", lambda **kw: store)
    intents = Mock()
    intents.statuses_by_client_order_ids.return_value = {}
    monkeypatch.setattr(runtime, "OrderIntentService", lambda **kw: intents)
    monkeypatch.setattr(runtime, "persist_cancellations", Mock())
    monkeypatch.setattr(virtual_trading, "mark_virtual_positions", Mock())
    monkeypatch.setattr(virtual_trading, "match_virtual_limit_orders", Mock(return_value=[]))
    batches, sealed = [], []

    @contextmanager
    def batch(**kwargs):
        assert kwargs["enabled"]
        group = str(kwargs["timestamp"])
        batches.append(group)
        if replay_first and len(batches) == 1:
            yield None
            return
        yield group
        sealed.append(group)

    monkeypatch.setattr(runtime, "rebalance_batch", batch)
    try:
        for delay in executor._run_strategy_loop(1):
            assert delay <= 5
            clock[0] += pd.Timedelta(minutes=1)
            monotonic[0] += 60
    finally:
        executor.close()

    assert executor._last_exit_reason == {}, logs.call_args_list
    assert submit.call_count == (2 if replay_first else 4), logs.call_args_list
    assert batches == ["2026-09-14 15:01:00+00:00", "2026-09-14 15:02:00+00:00"]
    assert sealed == (batches[1:] if replay_first else batches)
    assert [call.kwargs["rebalance_group_id"] for call in submit.call_args_list] == [group for group in sealed for _ in range(2)]
    assert len({call.kwargs["intent"].client_order_id for call in submit.call_args_list}) == submit.call_count
    assert sum("rebalanceReplaySkipped" in str(call) for call in logs.call_args_list) == int(replay_first)
    assert all(call.kwargs.get("trigger_mode") == "wall_clock_schedule" for call in heartbeat.call_args_list[1:])
    assert fetch.call_count == 1
