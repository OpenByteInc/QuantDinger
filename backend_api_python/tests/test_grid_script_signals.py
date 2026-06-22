"""Grid script signal mapping — close_long must not become open_short when DB leg is missing."""
from __future__ import annotations

import pandas as pd

from app.services.strategy_script_runtime import StrategyScriptContext
from app.services.trading_executor import TradingExecutor


def _make_executor() -> TradingExecutor:
    return TradingExecutor.__new__(TradingExecutor)


def test_explicit_close_long_emits_without_local_position():
  ex = _make_executor()
  ctx = StrategyScriptContext(pd.DataFrame({"close": [1.0]}), 10000.0)
  ctx.close_long(amount=36.0, price=73000.0, reason="grid_sell_take")
  sigs = ex._script_orders_to_execution_signals(
      ctx,
      trade_direction="both",
      bar_close=73000.0,
      closed_ts=pd.Timestamp("2026-05-29T00:57:00Z"),
      trading_config={"bot_type": "grid", "market_type": "swap", "leverage": 1},
  )
  assert len(sigs) == 1
  assert sigs[0]["type"] == "close_long"


def test_legacy_grid_sell_without_long_becomes_open_short():
  ex = _make_executor()
  ctx = StrategyScriptContext(pd.DataFrame({"close": [1.0]}), 10000.0)
  ctx.sell(amount=36.0, price=73000.0)
  sigs = ex._script_orders_to_execution_signals(
      ctx,
      trade_direction="both",
      bar_close=73000.0,
      closed_ts=pd.Timestamp("2026-05-29T00:57:00Z"),
      trading_config={"bot_type": "grid", "market_type": "swap", "leverage": 1},
  )
  assert len(sigs) == 1
  assert sigs[0]["type"] == "open_short"
