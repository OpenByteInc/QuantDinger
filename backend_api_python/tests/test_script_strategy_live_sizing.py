"""Script strategy live sizing — ctx.buy(price, qty) must match backtest semantics."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd

from app.services.strategy_script_runtime import StrategyScriptContext
from app.services.trading_executor import TradingExecutor


def _make_executor() -> TradingExecutor:
    return TradingExecutor.__new__(TradingExecutor)


def test_script_buy_emits_script_base_qty():
    ex = _make_executor()
    ctx = StrategyScriptContext(pd.DataFrame({"close": [0.94]}), 50.0)
    ctx.buy(price=0.94, amount=42.5)

    sigs = ex._script_orders_to_execution_signals(
        ctx,
        trade_direction="long",
        bar_close=0.94,
        closed_ts=pd.Timestamp("2026-06-02T02:00:00Z"),
        trading_config={"market_type": "swap", "leverage": 10},
    )

    assert len(sigs) == 1
    assert sigs[0]["type"] == "open_long"
    assert sigs[0]["script_base_qty"] == 42.5


def test_script_buy_without_amount_omits_script_base_qty():
    ex = _make_executor()
    ctx = StrategyScriptContext(pd.DataFrame({"close": [0.94]}), 50.0)
    ctx.buy(price=0.94)

    sigs = ex._script_orders_to_execution_signals(
        ctx,
        trade_direction="long",
        bar_close=0.94,
        closed_ts=pd.Timestamp("2026-06-02T02:00:00Z"),
        trading_config={"market_type": "swap", "leverage": 10},
    )

    assert len(sigs) == 1
    assert "script_base_qty" not in sigs[0]


@patch.object(TradingExecutor, "_execute_exchange_order", return_value={"success": True})
@patch.object(TradingExecutor, "_get_available_capital", return_value=50.0)
@patch.object(TradingExecutor, "_get_daily_pnl", return_value=0.0)
def test_execute_signal_uses_script_base_qty_for_open(_daily, _cap, mock_order):
    ex = _make_executor()

    ok = ex._execute_signal(
        strategy_id=1,
        strategy_name="test",
        exchange=MagicMock(),
        symbol="APT/USDT",
        current_price=0.94,
        signal_type="open_long",
        position_size=0.053,
        current_positions=[],
        trade_direction="long",
        leverage=10,
        initial_capital=50.0,
        market_type="swap",
        execution_mode="live",
        trading_config={"entry_pct": 0.01},
        script_base_qty=42.5,
    )

    assert ok is True
    mock_order.assert_called_once()
    assert mock_order.call_args.kwargs["amount"] == 42.5


@patch.object(TradingExecutor, "_execute_exchange_order", return_value={"success": True})
@patch.object(TradingExecutor, "_get_available_capital", return_value=50.0)
@patch.object(TradingExecutor, "_get_daily_pnl", return_value=0.0)
def test_execute_signal_falls_back_to_entry_pct_without_script_qty(_daily, _cap, mock_order):
    ex = _make_executor()

    ok = ex._execute_signal(
        strategy_id=1,
        strategy_name="test",
        exchange=MagicMock(),
        symbol="APT/USDT",
        current_price=0.94,
        signal_type="open_long",
        position_size=0.05,
        current_positions=[],
        trade_direction="long",
        leverage=10,
        initial_capital=50.0,
        market_type="swap",
        execution_mode="live",
        trading_config={"entry_pct": 80},
    )

    assert ok is True
    mock_order.assert_called_once()
    # 50 * 80% * 10x / 0.94 ≈ 425.53
    amount = mock_order.call_args.kwargs["amount"]
    assert amount > 400.0
