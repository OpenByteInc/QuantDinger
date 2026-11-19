from __future__ import annotations

from decimal import Decimal
import sys

import pandas as pd
import pytest

from app.services.strategy_v2.contract import compile_strategy_v2, inspect_strategy_v2
from app.services.strategy_v2.sandbox import (
    IsolatedStrategyV2LiveSession,
    run_strategy_backtest_isolated,
)


SOURCE = '''
"""Isolated process regression strategy."""

PERSIST_RUNTIME_STATE = True
GRID_TEMPLATE_VERSION = 7
CELL_LOWER = [90.0, 95.0]
CELL_UPPER = [95.0, 100.0]

def initialize(context):
    g.symbol = "Crypto:BTC/USDT@spot"
    g.emitted = False
    context.set_universe([g.symbol])
    context.subscribe(frequency="1d")

def handle_data(context, data):
    if not g.emitted:
        order_target_percent(g.symbol, 0.5, reason="isolated_entry")
        g.emitted = True
'''


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": [100.0, 101.0, 102.0],
            "high": [102.0, 103.0, 104.0],
            "low": [99.0, 100.0, 101.0],
            "close": [101.0, 102.0, 103.0],
            "volume": [10.0, 11.0, 12.0],
        },
        index=pd.date_range("2026-01-01", periods=3, freq="D", tz="UTC"),
    )


def test_manifest_inspection_does_not_load_user_handlers_in_parent():
    program = inspect_strategy_v2(SOURCE)

    assert program.handler("handle_data") is None
    assert program.has_handler("handle_data") is True
    assert program.namespace["GRID_TEMPLATE_VERSION"] == 7
    assert program.namespace["CELL_LOWER"] == [90.0, 95.0]


def test_legacy_compile_call_is_metadata_only_outside_runtime_worker(monkeypatch):
    monkeypatch.delenv("QD_STRATEGY_RUNTIME_WORKER", raising=False)

    program = compile_strategy_v2(SOURCE)

    assert program.handler("initialize") is None
    assert program.handler("handle_data") is None
    assert program.has_handler("handle_data") is True


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="kernel sandbox requires Linux")
def test_live_callbacks_execute_through_isolated_process():
    frame = _frame()
    symbol = "Crypto:BTC/USDT@spot"
    session = IsolatedStrategyV2LiveSession(
        code=SOURCE,
        frames={symbol: frame.iloc[:2]},
        initial_capital=10_000.0,
        universe_members=[symbol],
    )
    try:
        session.synchronize_positions({
            symbol: {
                "amount": Decimal("1.25000000"),
                "side": "long",
                "avg_cost": Decimal("100.12500000"),
                "last_price": Decimal("101.50000000"),
            },
        })
        orders, logs, timestamp = session.process(
            {symbol: frame.iloc[:2]},
            universe_members=[symbol],
        )
        assert logs == []
        assert timestamp == frame.index[1]
        assert len(orders) == 1
        assert orders[0].kind == "target_percent"
        assert orders[0].reason == "isolated_entry"

        snapshot = session.session_snapshot()
        assert snapshot["strategyState"]["emitted"] is True

        orders, _logs, _timestamp = session.process(
            {symbol: frame},
            universe_members=[symbol],
        )
        assert orders == []
    finally:
        session.close()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="kernel sandbox requires Linux")
def test_backtest_callbacks_execute_through_isolated_process():
    frame = _frame()
    symbol = "Crypto:BTC/USDT@spot"

    result = run_strategy_backtest_isolated(
        code=SOURCE,
        frames={symbol: frame},
        frequency_frames=None,
        initial_capital=10_000.0,
        params=None,
        leverage_enabled=False,
        leverage=1.0,
        commission=0.0,
        slippage=0.0,
        default_universe_members=[symbol],
        timeout=60.0,
    )

    assert result["totalExecutions"] >= 1
    assert result["manifest"]["codeHash"] == inspect_strategy_v2(SOURCE).manifest.code_hash
