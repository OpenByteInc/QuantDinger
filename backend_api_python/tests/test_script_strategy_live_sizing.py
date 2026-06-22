"""Script strategy live sizing — ctx.buy(price, qty) must match backtest semantics."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

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


def test_non_bot_script_buy_does_not_emit_add_long_when_already_long():
    ex = _make_executor()
    ctx = StrategyScriptContext(pd.DataFrame({"close": [0.94]}), 50.0)
    ctx.position.open_long(0.90, 10)
    ctx.buy(price=0.94, amount=42.5)

    sigs = ex._script_orders_to_execution_signals(
        ctx,
        trade_direction="long",
        bar_close=0.94,
        closed_ts=pd.Timestamp("2026-06-02T02:01:00Z"),
        trading_config={"market_type": "swap", "leverage": 10},
    )

    assert sigs == []


def test_bot_script_buy_can_emit_add_long_when_already_long():
    ex = _make_executor()
    ctx = StrategyScriptContext(pd.DataFrame({"close": [0.94]}), 50.0)
    ctx.position.open_long(0.90, 10)
    ctx.buy(price=0.94, amount=5)

    sigs = ex._script_orders_to_execution_signals(
        ctx,
        trade_direction="long",
        bar_close=0.94,
        closed_ts=pd.Timestamp("2026-06-02T02:01:00Z"),
        trading_config={"market_type": "swap", "leverage": 10, "bot_type": "dca"},
    )

    assert len(sigs) == 1
    assert sigs[0]["type"] == "add_long"
    assert sigs[0]["script_quote_amount"] == 5


def test_explicit_script_add_long_emits_add_long_when_already_long():
    ex = _make_executor()
    ctx = StrategyScriptContext(pd.DataFrame({"close": [0.94]}), 50.0)
    ctx.position.open_long(0.90, 10)
    ctx.add_long(amount=2.5, price=0.94)

    sigs = ex._script_orders_to_execution_signals(
        ctx,
        trade_direction="long",
        bar_close=0.94,
        closed_ts=pd.Timestamp("2026-06-02T02:02:00Z"),
        trading_config={"market_type": "swap", "leverage": 10},
    )

    assert len(sigs) == 1
    assert sigs[0]["type"] == "add_long"
    assert sigs[0]["script_base_qty"] == 2.5


def test_explicit_script_add_long_requires_existing_long():
    ex = _make_executor()
    ctx = StrategyScriptContext(pd.DataFrame({"close": [0.94]}), 50.0)
    ctx.add_long(amount=2.5, price=0.94)

    sigs = ex._script_orders_to_execution_signals(
        ctx,
        trade_direction="long",
        bar_close=0.94,
        closed_ts=pd.Timestamp("2026-06-02T02:02:00Z"),
        trading_config={"market_type": "swap", "leverage": 10},
    )

    assert sigs == []


def test_bot_script_buy_emits_quote_amount():
    ex = _make_executor()
    ctx = StrategyScriptContext(pd.DataFrame({"close": [668.2]}), 100.0)
    ctx.buy(price=668.2, amount=3.22)

    sigs = ex._script_orders_to_execution_signals(
        ctx,
        trade_direction="long",
        bar_close=668.2,
        closed_ts=pd.Timestamp("2026-06-02T14:28:00Z"),
        trading_config={"market_type": "swap", "leverage": 5, "bot_type": "martingale"},
    )

    assert len(sigs) == 1
    assert sigs[0]["position_size"] == 3.22
    assert sigs[0]["script_quote_amount"] == 3.22
    assert "script_base_qty" not in sigs[0]


def test_bot_script_small_quote_amount_stays_quote_not_base_qty():
    ex = _make_executor()
    ctx = StrategyScriptContext(pd.DataFrame({"close": [2.0]}), 100.0)
    ctx.buy(price=2.0, amount=0.5)

    sigs = ex._script_orders_to_execution_signals(
        ctx,
        trade_direction="long",
        bar_close=2.0,
        closed_ts=pd.Timestamp("2026-06-02T14:29:00Z"),
        trading_config={"market_type": "swap", "leverage": 10, "bot_type": "dca"},
    )

    assert len(sigs) == 1
    assert sigs[0]["position_size"] == 0.5
    assert sigs[0]["script_quote_amount"] == 0.5


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
@patch.object(TradingExecutor, "_get_available_capital", return_value=100.0)
@patch.object(TradingExecutor, "_get_daily_pnl", return_value=0.0)
def test_execute_signal_uses_bot_script_quote_amount_with_leverage(_daily, _cap, mock_order):
    ex = _make_executor()

    ok = ex._execute_signal(
        strategy_id=32,
        strategy_name="martingale",
        exchange=MagicMock(),
        symbol="BNB/USDT",
        current_price=668.2,
        signal_type="open_long",
        position_size=3.22,
        current_positions=[],
        trade_direction="long",
        leverage=5,
        initial_capital=100.0,
        market_type="swap",
        execution_mode="live",
        trading_config={"bot_type": "martingale"},
        script_quote_amount=3.22,
    )

    assert ok is True
    mock_order.assert_called_once()
    assert mock_order.call_args.kwargs["amount"] == pytest.approx(3.22 * 5 / 668.2)


@patch.object(TradingExecutor, "_execute_exchange_order", return_value={"success": True})
@patch.object(TradingExecutor, "_get_available_capital", return_value=100.0)
@patch.object(TradingExecutor, "_get_daily_pnl", return_value=0.0)
def test_execute_signal_uses_small_bot_quote_amount_with_leverage(_daily, _cap, mock_order):
    ex = _make_executor()

    ok = ex._execute_signal(
        strategy_id=33,
        strategy_name="dca",
        exchange=MagicMock(),
        symbol="DOGE/USDT",
        current_price=2.0,
        signal_type="open_long",
        position_size=0.5,
        current_positions=[],
        trade_direction="long",
        leverage=10,
        initial_capital=100.0,
        market_type="swap",
        execution_mode="live",
        trading_config={"bot_type": "dca"},
        script_quote_amount=0.5,
    )

    assert ok is True
    mock_order.assert_called_once()
    assert mock_order.call_args.kwargs["amount"] == pytest.approx(0.5 * 10 / 2.0)


@patch("app.services.trading_executor.append_strategy_log")
@patch.object(TradingExecutor, "_execute_exchange_order", return_value={"success": True})
@patch.object(TradingExecutor, "_get_available_capital", return_value=100.0)
@patch.object(TradingExecutor, "_get_daily_pnl", return_value=0.0)
def test_execute_signal_rejects_script_base_qty_above_capital(_daily, _cap, mock_order, mock_log):
    ex = _make_executor()

    ok = ex._execute_signal(
        strategy_id=1,
        strategy_name="test",
        exchange=MagicMock(),
        symbol="ETH/USDT",
        current_price=1978.84,
        signal_type="open_long",
        position_size=10.04,
        current_positions=[],
        trade_direction="long",
        leverage=1,
        initial_capital=100.0,
        market_type="spot",
        execution_mode="signal",
        trading_config={},
        script_base_qty=10.04,
    )

    assert ok is False
    mock_order.assert_not_called()
    assert "script order amount exceeds capital" in mock_log.call_args.args[2]


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


def test_fetch_latest_kline_keeps_xaut_on_configured_crypto_market():
    ex = _make_executor()
    ex.kline_service = MagicMock()
    ex.kline_service.get_kline.return_value = [{"time": 1}, {"time": 2}]

    out = ex._fetch_latest_kline(
        "XAUT",
        "15m",
        limit=2,
        market_category="Crypto",
        exchange_id="bitget",
        market_type="swap",
    )

    assert len(out) == 2
    kwargs = ex.kline_service.get_kline.call_args.kwargs
    assert kwargs["market"] == "Crypto"
    assert kwargs["exchange_id"] == "bitget"
    assert kwargs["market_type"] == "swap"


@patch("app.services.trading_executor.append_strategy_log")
@patch.object(TradingExecutor, "_execute_exchange_order", return_value={"success": True})
@patch.object(TradingExecutor, "_get_available_capital", return_value=100.0)
@patch.object(TradingExecutor, "_get_daily_pnl", return_value=0.0)
def test_signal_mode_close_records_matched_entry_price(_daily, _cap, _order, _log):
    ex = _make_executor()
    ex._record_trade = MagicMock()
    ex._close_position = MagicMock()
    ex._simulated_open_qty_from_trade_rows = MagicMock(return_value=0.5)

    ok = ex._execute_signal(
        strategy_id=6,
        strategy_name="paper",
        exchange=MagicMock(),
        symbol="ETH/USDT",
        current_price=1900.0,
        signal_type="close_short",
        position_size=0,
        current_positions=[
            {"symbol": "ETH/USDT", "side": "short", "size": 0.5, "entry_price": 2000.0}
        ],
        trade_direction="both",
        leverage=1,
        initial_capital=1000.0,
        market_type="swap",
        execution_mode="signal",
        trading_config={},
    )

    assert ok is True
    ex._record_trade.assert_called_once()
    assert ex._record_trade.call_args.kwargs["matched_entry_price"] == pytest.approx(2000.0)


@patch("app.services.trading_executor.append_strategy_log")
@patch.object(TradingExecutor, "_execute_exchange_order", return_value={"success": True})
@patch.object(TradingExecutor, "_get_available_capital", return_value=100.0)
@patch.object(TradingExecutor, "_get_daily_pnl", return_value=0.0)
def test_signal_mode_rejects_ghost_close_without_unmatched_open_trade(_daily, _cap, mock_order, _log):
    ex = _make_executor()
    ex._record_trade = MagicMock()
    ex._close_position = MagicMock()
    ex._simulated_open_qty_from_trade_rows = MagicMock(return_value=0.0)

    ok = ex._execute_signal(
        strategy_id=6,
        strategy_name="paper",
        exchange=MagicMock(),
        symbol="ETH/USDT",
        current_price=1800.0,
        signal_type="close_short",
        position_size=0,
        current_positions=[
            {"symbol": "ETH/USDT", "side": "short", "size": 0.5, "entry_price": 2000.0}
        ],
        trade_direction="both",
        leverage=1,
        initial_capital=1000.0,
        market_type="swap",
        execution_mode="signal",
        trading_config={},
    )

    assert ok is False
    mock_order.assert_not_called()
    ex._record_trade.assert_not_called()
    ex._close_position.assert_called_once_with(6, "ETH/USDT", "short")
