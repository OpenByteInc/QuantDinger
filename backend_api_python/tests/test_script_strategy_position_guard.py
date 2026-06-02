"""P0/P1 guards: fill-ledger sync skip, inflight dedup, hydrate eligibility."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd

from app.services.live_trading.strategy_position_sync import strategy_uses_fill_ledger
from app.services.strategy_script_runtime import StrategyScriptContext
from app.services.trading_executor import TradingExecutor


def test_strategy_uses_fill_ledger_script_not_bot():
    assert strategy_uses_fill_ledger({"strategy_type": "ScriptStrategy", "trading_config": {}})


def test_strategy_uses_fill_ledger_grid():
    assert strategy_uses_fill_ledger(
        {"strategy_type": "ScriptStrategy", "bot_type": "grid", "trading_config": {"bot_type": "grid"}}
    )


def test_strategy_uses_fill_ledger_indicator_false():
    assert not strategy_uses_fill_ledger({"strategy_type": "IndicatorStrategy"})


def test_strategy_uses_fill_ledger_martingale_bot_false():
    assert not strategy_uses_fill_ledger(
        {"strategy_type": "ScriptStrategy", "trading_config": {"bot_type": "martingale"}}
    )


def test_effective_position_state_inflight_open_long():
    ex = TradingExecutor.__new__(TradingExecutor)
    with patch.object(ex, "_inflight_open_side", return_value="long"):
        state = ex._effective_position_state(1, "APT/USDT", [])
    assert state == "long"


def test_is_signal_allowed_blocks_duplicate_open_while_inflight():
    ex = TradingExecutor.__new__(TradingExecutor)
    with patch.object(ex, "_inflight_open_side", return_value="long"):
        state = ex._effective_position_state(1, "APT/USDT", [])
    assert not ex._is_signal_allowed(state, "open_long")


def test_live_script_hydrate_candidate():
    assert TradingExecutor._is_live_script_hydrate_candidate(
        {"execution_mode": "live", "bot_type": ""}
    )
    assert not TradingExecutor._is_live_script_hydrate_candidate(
        {"execution_mode": "live", "bot_type": "martingale", "strategy_mode": "bot"}
    )


def test_hydrate_calls_exchange_fallback_for_live_script():
    ex = TradingExecutor.__new__(TradingExecutor)
    ctx = StrategyScriptContext(pd.DataFrame({"close": [1.0]}), 50.0)
    with patch.object(ex, "_get_current_positions", return_value=[]), patch.object(
        ex, "_hydrate_grid_ctx_from_exchange_best_effort"
    ) as mock_hydrate, patch.object(
        ex, "_calculate_current_equity", return_value=50.0
    ):
        ex._hydrate_script_ctx_from_positions(
            ctx,
            strategy_id=1,
            symbol="APT/USDT",
            initial_capital=50.0,
            current_price=0.94,
            trading_config={"execution_mode": "live"},
        )
    mock_hydrate.assert_called_once()


@patch("app.services.pending_order_worker.PendingOrderWorker")
@patch("app.services.exchange_execution.load_strategy_configs")
def test_sync_skips_fill_ledger_strategy(mock_load, mock_worker_cls):
    from app.services.live_trading.strategy_position_sync import sync_strategy_positions_from_exchange

    mock_load.return_value = {"strategy_type": "ScriptStrategy", "trading_config": {}}
    sync_strategy_positions_from_exchange(99)
    mock_worker_cls.assert_not_called()
