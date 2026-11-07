from types import SimpleNamespace
from unittest.mock import Mock
from datetime import datetime, timezone
import time

import pandas as pd
import pytest

from app.services import trading_executor as runtime
from app.services.live_trading.gate import GateStockClient


SYMBOL = "Crypto:NVDA/USD@gate:spot"
CODE = '''
def initialize(context):
    g.symbol = "Crypto:NVDA/USD@gate:spot"
    context.set_universe([g.symbol])
    context.subscribe(frequency="1m", fields=["close"])
    context.set_warmup(50)
    context.set_benchmark(g.symbol)
    context.set_metadata(direction_mode="long_only", strategy_family="ema_crossover")

def handle_data(context, data):
    bars = data.history(g.symbol, count=500, fields=["close"])
    if len(bars) >= 32:
        fast = bars.close.ewm(span=10, adjust=False).mean()
        slow = bars.close.ewm(span=30, adjust=False).mean()
        if fast.iloc[-1] > slow.iloc[-1] and fast.iloc[-2] <= slow.iloc[-2]:
            order_target_percent(g.symbol, 0.95)
'''


@pytest.mark.parametrize("account_options", [{}, {"environment": "testnet"}, {"enable_demo_trading": True}])
@pytest.mark.parametrize("data_mode", ["current", "stale", "recover"])
def test_gate_stock_complete_live_loop_uses_exchange_quotes(monkeypatch, account_options, data_mode):
    from app.services.strategy_runtime import candle_readiness

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 9, 14, 15, 0, 5, tzinfo=timezone.utc)

    monkeypatch.setattr(candle_readiness, "datetime", Clock)
    monkeypatch.setattr(runtime, "datetime", Clock)
    sessions = []
    session_type = runtime.StrategyV2LiveSession

    def create_session(**kwargs):
        session = session_type(**kwargs)
        session.process = Mock(wraps=session.process)
        sessions.append(session)
        return session

    monkeypatch.setattr(runtime, "StrategyV2LiveSession", create_session)
    product = {
        "symbol": "NVDA/USD", "exchange_id": "gate", "market_type": "spot",
        "instrument_id": "NVDA", "product_type": "direct_equity", "api_family": "stock",
        "underlying_market": "USStock", "underlying_symbol": "NVDA",
    }
    strategy = {
        "id": 1, "user_id": 1, "execution_mode": "live", "initial_capital": 1000,
        "trading_config": {"instrument_products": [product], "risk_tick_seconds": 0.25},
        "exchange_config": {"exchange_id": "gate", "api_key": "test", "secret_key": "test", **(account_options or {})},
    }
    executor = runtime.TradingExecutor()
    monkeypatch.setattr(executor, "_load_strategy", lambda _: strategy)
    monkeypatch.setattr(executor, "_load_source", lambda _: (1, CODE))
    monkeypatch.setattr(executor, "_load_schedule_timezone", lambda _: "UTC")
    monkeypatch.setattr(executor, "_positions_by_symbol", lambda *a, **kw: {})
    monkeypatch.setattr(executor, "_calculate_current_equity", lambda *a, **kw: 1000)
    submit = Mock(return_value=True)
    monkeypatch.setattr(executor, "_execute_strategy_v2_intent", submit)
    cycles = iter([True, True, True, False])
    monkeypatch.setattr(executor, "_is_strategy_running", lambda *a: next(cycles))
    heartbeats = []
    monkeypatch.setattr(executor, "_heartbeat", lambda *a, **kw: heartbeats.append((dict(a[3]), kw)))
    monkeypatch.setattr(executor, "_mark_stopped", lambda *a: None)
    logs = []
    monkeypatch.setattr(runtime, "append_strategy_log", lambda *a: logs.append(a))
    monkeypatch.setattr(runtime, "ensure_strategy_run", lambda **kw: SimpleNamespace(strategy_run_id=1))
    monkeypatch.setattr(runtime, "finish_strategy_run", lambda *a, **kw: None)
    store = Mock()
    store.load.return_value = {}
    monkeypatch.setattr(runtime, "RuntimeStateStore", lambda **kw: store)
    monkeypatch.setattr(runtime, "persist_cancellations", lambda *a: None)
    monotonic = [time.monotonic()]
    monkeypatch.setattr(runtime, "time", SimpleNamespace(monotonic=lambda: monotonic[0], sleep=lambda _: None, time=time.time))
    frame = pd.DataFrame(
        {"open": 200., "high": 201., "low": 199., "close": 200., "volume": 1.},
        index=pd.date_range("2026-09-14 14:00", periods=60, freq="min", tz="UTC"),
    )
    stale_frame = frame.copy()
    stale_frame.index -= pd.Timedelta(minutes=1)
    fetches = []

    def fetch_frames(*args, **kwargs):
        fetches.append(1)
        ready = data_mode == "current" or (data_mode == "recover" and len(fetches) >= 3)
        return {"1m": {SYMBOL: frame if ready else stale_frame}}, []

    monkeypatch.setattr(runtime.StrategyV2BacktestService, "fetch_frequency_frames", fetch_frames)
    request = Mock(return_value=(200, {"data": {"bids": [{"p": "210.1"}], "asks": [{"p": "210.3"}]}}, ""))
    monkeypatch.setattr(GateStockClient, "_request", request)

    for _delay in executor._run_strategy_loop(1):
        monotonic[0] += 10

    if account_options:
        assert executor._last_exit_reason == {1: "strategyV2.gateStockTestnetUnsupported"}
        assert not heartbeats
        request.assert_not_called()
        submit.assert_not_called()
        assert logs == [(1, "error", "strategyV2.gateStockTestnetUnsupported")]
        return
    assert executor._last_exit_reason == {}, logs
    assert len(heartbeats) == 4, logs
    assert all(prices.get(SYMBOL) == 210.2 for prices, _ in heartbeats[1:]), logs
    if data_mode == "current":
        assert all(meta["status"] == "healthy" for _, meta in heartbeats[1:]), logs
        assert sessions[0].process.call_count == 1
    elif data_mode == "stale":
        assert all(meta["status"] == "degraded" for _, meta in heartbeats[1:]), logs
        assert all(meta["last_error"] == "marketData.incomplete_market_data" for _, meta in heartbeats[1:])
        assert sessions[0].last_processed is None
        sessions[0].process.assert_not_called()
        submit.assert_not_called()
        assert len(fetches) >= 3
    else:
        assert heartbeats[1][1]["status"] == "degraded"
        assert heartbeats[-1][1]["status"] == "healthy"
        assert sessions[0].process.call_count == 1
        assert sessions[0].last_processed == frame.index[-1]
    assert request.call_count == 1


def test_gate_stock_testnet_rejected_before_deployment_is_saved(monkeypatch):
    from app.services import exchange_execution
    from app.services.strategy_v2 import deployment

    monkeypatch.setattr(deployment, "get_script_source_service", lambda: SimpleNamespace(
        get_source=lambda *a, **kw: {"name": "Gate NVDA", "code": CODE},
        get_latest_version=lambda *a, **kw: {"id": 11, "code": CODE},
    ))
    monkeypatch.setattr(deployment.StrategyV2DeploymentService, "_credential_exchange", lambda *a: "gate")
    monkeypatch.setattr(deployment, "resolve_runtime_instrument", lambda **kw: {
        "asset_id": 101,
        "instrument_id": "NVDA",
        "api_family": "stock",
        "product_type": "direct_equity",
    })
    resolve = Mock(return_value={"exchange_id": "gate", "environment": "testnet"})
    monkeypatch.setattr(exchange_execution, "resolve_exchange_config", resolve)
    database = Mock(side_effect=AssertionError("No deployment should be persisted"))
    monkeypatch.setattr(deployment, "get_db_connection", database)

    with pytest.raises(deployment.StrategyV2ContractError, match="gateStockTestnetUnsupported"):
        deployment.StrategyV2DeploymentService().save(user_id=7, payload={
            "sourceId": 1, "initialCapital": 1000, "credentialId": 23, "executionMode": "live",
        })

    resolve.assert_called_once_with({"credential_id": 23, "exchange_id": "gate"}, user_id=7)
    database.assert_not_called()


def test_gate_stock_testnet_rejected_before_starting_thread(monkeypatch):
    from app.services import exchange_execution

    executor = runtime.TradingExecutor()
    strategy = {
        "id": 1, "user_id": 7, "execution_mode": "live", "market_type": "spot",
        "trading_config": {"instrument_products": [{"exchange_id": "gate", "api_family": "stock"}]},
        "exchange_config": {"credential_id": 23},
    }
    monkeypatch.setattr(executor, "_load_strategy", lambda _: strategy)
    monkeypatch.setattr(exchange_execution, "resolve_exchange_config", lambda *a, **kw: {
        "exchange_id": "gate", "use_testnet": True,
    })

    assert executor.start_strategy(1) is False
    assert executor._last_start_failure == "strategyV2.gateStockTestnetUnsupported"
    assert not executor.running_strategies
