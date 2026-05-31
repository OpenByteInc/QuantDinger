"""Grid bot dedicated risk exits (P0-2).

The generic ``_server_side_stop_loss_signal`` is a no-op for grid/DCA bots —
their entry_price is a sliding average and "price vs entry %" doesn't
correspond to anything meaningful for the user. Grid bots get
``_grid_bot_risk_exits`` instead, which:

* Treats ``stop_loss_pct`` / ``take_profit_pct`` as *equity drawdown* vs
  initial capital.
* Adds an "out-of-grid" breakout protection driven by
  ``grid_oob_buffer_pct`` (default 5%).
* Always closes BOTH legs in one call when triggered, returning a list of
  close_long + close_short signals.
"""
from __future__ import annotations

from app.services.trading_executor import TradingExecutor


def _make_executor() -> TradingExecutor:
    return TradingExecutor.__new__(TradingExecutor)


def test_generic_stop_loss_is_noop_for_grid_bot(monkeypatch):
    ex = _make_executor()
    cfg = {
        "bot_type": "grid",
        "stop_loss_pct": 5,
        "enable_server_side_stop_loss": True,
    }
    monkeypatch.setattr(ex, "_get_current_positions", lambda *a, **k: [
        {"side": "long", "entry_price": 100.0, "size": 1.0, "symbol": "BTC/USDT"}
    ])

    # Price collapsed 20% but the generic SL must stay silent for grid bots —
    # ``_grid_bot_risk_exits`` handles it instead.
    sig = ex._server_side_stop_loss_signal(
        strategy_id=1, symbol="BTC/USDT", current_price=80.0,
        market_type="swap", leverage=1.0,
        trading_config=cfg, timeframe_seconds=60,
    )
    assert sig is None


def test_grid_out_of_bounds_up_closes_both_legs(monkeypatch):
    ex = _make_executor()
    cfg = {
        "bot_type": "grid",
        "bot_params": {"upperPrice": 100.0, "lowerPrice": 80.0},
        "grid_oob_buffer_pct": 5,  # 5% above upper => trigger at 105
    }
    monkeypatch.setattr(ex, "_get_current_positions", lambda *a, **k: [
        {"side": "long", "entry_price": 90.0, "size": 1.0, "symbol": "BTC/USDT"},
        {"side": "short", "entry_price": 95.0, "size": 0.5, "symbol": "BTC/USDT"},
    ])
    monkeypatch.setattr(ex, "_calculate_current_equity", lambda *a, **k: 10000.0)

    exits = ex._grid_bot_risk_exits(
        strategy_id=1, symbol="BTC/USDT", current_price=106.0,
        trading_config=cfg, timeframe_seconds=60, initial_capital=10000.0,
    )
    types = [e['type'] for e in exits]
    reasons = {e.get('reason') for e in exits}
    assert sorted(types) == ['close_long', 'close_short']
    assert reasons == {'grid_out_of_bounds_up'}


def test_grid_out_of_bounds_down_closes_both_legs(monkeypatch):
    ex = _make_executor()
    cfg = {
        "bot_type": "grid",
        "bot_params": {"upperPrice": 100.0, "lowerPrice": 80.0},
        "grid_oob_buffer_pct": 5,  # 5% below lower => trigger at 76
    }
    monkeypatch.setattr(ex, "_get_current_positions", lambda *a, **k: [
        {"side": "long", "entry_price": 90.0, "size": 1.0, "symbol": "BTC/USDT"},
    ])
    monkeypatch.setattr(ex, "_calculate_current_equity", lambda *a, **k: 10000.0)

    exits = ex._grid_bot_risk_exits(
        strategy_id=1, symbol="BTC/USDT", current_price=75.0,
        trading_config=cfg, timeframe_seconds=60, initial_capital=10000.0,
    )
    assert len(exits) == 1
    assert exits[0]['type'] == 'close_long'
    assert exits[0]['reason'] == 'grid_out_of_bounds_down'


def test_grid_equity_drawdown_triggers_stop(monkeypatch):
    ex = _make_executor()
    cfg = {
        "bot_type": "grid",
        "stop_loss_pct": 10,  # 10% equity drawdown
        "bot_params": {"upperPrice": 100.0, "lowerPrice": 80.0},
    }
    monkeypatch.setattr(ex, "_get_current_positions", lambda *a, **k: [
        {"side": "long", "entry_price": 90.0, "size": 1.0, "symbol": "BTC/USDT"},
    ])
    # Equity dropped from 10000 -> 8800 == -12% (worse than -10% threshold).
    monkeypatch.setattr(ex, "_calculate_current_equity", lambda *a, **k: 8800.0)

    exits = ex._grid_bot_risk_exits(
        strategy_id=1, symbol="BTC/USDT", current_price=85.0,
        trading_config=cfg, timeframe_seconds=60, initial_capital=10000.0,
    )
    assert exits and exits[0]['reason'] == 'grid_equity_stop_loss'


def test_grid_equity_take_profit(monkeypatch):
    ex = _make_executor()
    cfg = {
        "bot_type": "grid",
        "take_profit_pct": 5,
        "bot_params": {"upperPrice": 100.0, "lowerPrice": 80.0},
    }
    monkeypatch.setattr(ex, "_get_current_positions", lambda *a, **k: [
        {"side": "short", "entry_price": 95.0, "size": 1.0, "symbol": "BTC/USDT"},
    ])
    monkeypatch.setattr(ex, "_calculate_current_equity", lambda *a, **k: 10600.0)

    exits = ex._grid_bot_risk_exits(
        strategy_id=1, symbol="BTC/USDT", current_price=88.0,
        trading_config=cfg, timeframe_seconds=60, initial_capital=10000.0,
    )
    assert exits and exits[0]['reason'] == 'grid_equity_take_profit'
    assert exits[0]['type'] == 'close_short'


def test_grid_no_exit_when_within_bounds_and_no_drawdown(monkeypatch):
    ex = _make_executor()
    cfg = {
        "bot_type": "grid",
        "stop_loss_pct": 10,
        "take_profit_pct": 10,
        "bot_params": {"upperPrice": 100.0, "lowerPrice": 80.0},
    }
    monkeypatch.setattr(ex, "_get_current_positions", lambda *a, **k: [
        {"side": "long", "entry_price": 90.0, "size": 1.0, "symbol": "BTC/USDT"},
    ])
    monkeypatch.setattr(ex, "_calculate_current_equity", lambda *a, **k: 10050.0)

    exits = ex._grid_bot_risk_exits(
        strategy_id=1, symbol="BTC/USDT", current_price=91.0,
        trading_config=cfg, timeframe_seconds=60, initial_capital=10000.0,
    )
    assert exits == []


def test_grid_no_exit_when_no_positions(monkeypatch):
    ex = _make_executor()
    cfg = {"bot_type": "grid", "stop_loss_pct": 5}
    monkeypatch.setattr(ex, "_get_current_positions", lambda *a, **k: [])

    exits = ex._grid_bot_risk_exits(
        strategy_id=1, symbol="BTC/USDT", current_price=50.0,
        trading_config=cfg, timeframe_seconds=60, initial_capital=10000.0,
    )
    assert exits == []
