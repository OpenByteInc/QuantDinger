"""SL/TP trigger thresholds must be the underlying's % price move directly.

After unification (no more ``risk_pct_basis`` toggle), leverage only
affects PnL magnitude / liquidation — it must NOT scale the trigger
threshold for stop-loss, take-profit or trailing exits, regardless of
any stale ``risk_pct_basis`` value on legacy strategies.
"""
from __future__ import annotations

import pytest

from app.services.trading_executor import TradingExecutor


def _make_executor() -> TradingExecutor:
    # Bypass __init__ side effects (DB column checks, threading) — we only
    # need pure instance methods here.
    return TradingExecutor.__new__(TradingExecutor)


def test_no_risk_pct_basis_helper_present():
    """The basis toggle was removed. Re-introducing it would silently
    flip live-trading risk thresholds — keep this test as a guard."""
    assert not hasattr(TradingExecutor, "_risk_pct_basis")


def test_stop_loss_triggers_on_underlying_price_move_only(monkeypatch):
    ex = _make_executor()

    cfg = {"stop_loss_pct": 9, "enable_server_side_stop_loss": True}
    monkeypatch.setattr(ex, "_get_current_positions", lambda *a, **k: [
        {"side": "long", "entry_price": 100.0, "size": 1.0, "symbol": "BTC/USDT"}
    ])

    # 92 / 100 = -8% > -9% — must NOT trigger even at 10x leverage.
    sig = ex._server_side_stop_loss_signal(
        strategy_id=1, symbol="BTC/USDT", current_price=92.0,
        market_type="swap", leverage=10.0,
        trading_config=cfg, timeframe_seconds=60,
    )
    assert sig is None

    # 91 / 100 = -9% — must trigger.
    sig = ex._server_side_stop_loss_signal(
        strategy_id=1, symbol="BTC/USDT", current_price=91.0,
        market_type="swap", leverage=10.0,
        trading_config=cfg, timeframe_seconds=60,
    )
    assert sig is not None
    assert sig["type"] == "close_long"
    assert sig["reason"] == "server_stop_loss"


def test_legacy_margin_field_is_ignored(monkeypatch):
    """Stale ``risk_pct_basis='margin'`` on old strategies must NOT bring
    back the divide-by-leverage branch."""
    ex = _make_executor()

    cfg = {
        "stop_loss_pct": 9,
        "enable_server_side_stop_loss": True,
        "risk_pct_basis": "margin",  # legacy field — must be ignored
    }
    monkeypatch.setattr(ex, "_get_current_positions", lambda *a, **k: [
        {"side": "long", "entry_price": 100.0, "size": 1.0, "symbol": "BTC/USDT"}
    ])

    # If the legacy basis were still active, 99.5 (-0.5% price) would already
    # cross the 0.9% margin-derived threshold and trigger.
    sig = ex._server_side_stop_loss_signal(
        strategy_id=1, symbol="BTC/USDT", current_price=99.5,
        market_type="swap", leverage=10.0,
        trading_config=cfg, timeframe_seconds=60,
    )
    assert sig is None


def test_take_profit_threshold_uses_price_move(monkeypatch):
    ex = _make_executor()

    cfg = {"take_profit_pct": 16, "enable_server_side_take_profit": True}
    monkeypatch.setattr(ex, "_get_current_positions", lambda *a, **k: [
        {"side": "long", "entry_price": 100.0, "size": 1.0,
         "highest_price": 0, "lowest_price": 0, "symbol": "BTC/USDT"}
    ])
    monkeypatch.setattr(ex, "_update_position", lambda *a, **k: None)

    # 16% price move required regardless of leverage.
    sig = ex._server_side_take_profit_or_trailing_signal(
        strategy_id=1, symbol="BTC/USDT", current_price=110.0,
        market_type="swap", leverage=10.0,
        trading_config=cfg, timeframe_seconds=60,
    )
    assert sig is None

    sig = ex._server_side_take_profit_or_trailing_signal(
        strategy_id=1, symbol="BTC/USDT", current_price=116.5,
        market_type="swap", leverage=10.0,
        trading_config=cfg, timeframe_seconds=60,
    )
    assert sig is not None
    assert sig["type"] == "close_long"
    assert sig["reason"] == "server_take_profit"


def test_strip_legacy_risk_pct_basis_on_save():
    """Strategy service must drop the toggle on every write so it cannot
    leak back into trading_config."""
    from app.services.strategy import _strip_legacy_risk_pct_basis

    cleaned = _strip_legacy_risk_pct_basis({
        "stop_loss_pct": 9,
        "risk_pct_basis": "margin",
        "riskPctBasis": "price",
    })
    assert "risk_pct_basis" not in cleaned
    assert "riskPctBasis" not in cleaned
    assert cleaned["stop_loss_pct"] == 9


def test_to_ratio_treats_input_as_percent_not_ratio():
    """trading_config.*_pct fields are stored in percent (9 = 9%,
    0.01 = 0.01%). The old auto-detect branch (``if x > 1: /= 100``)
    silently promoted 0.01 to 1% / 0.5 to 50% and broke sub-1% SL/TP.
    Keep this test as a regression guard."""
    ex = _make_executor()
    assert ex._to_ratio(9) == 0.09
    assert ex._to_ratio(0.5) == 0.005
    assert ex._to_ratio(0.01) == 0.0001
    assert ex._to_ratio(0) == 0.0
    assert ex._to_ratio(None) == 0.0
    assert ex._to_ratio(-5) == 0.0
    assert ex._to_ratio(150) == 1.0  # clamped


def test_sub_one_percent_stop_loss_triggers_at_underlying_price(monkeypatch):
    """0.01% SL must fire on 0.01% adverse price move, NOT 1%."""
    ex = _make_executor()
    cfg = {"stop_loss_pct": 0.01, "enable_server_side_stop_loss": True}
    monkeypatch.setattr(ex, "_get_current_positions", lambda *a, **k: [
        {"side": "long", "entry_price": 100.0, "size": 1.0, "symbol": "BTC/USDT"}
    ])

    # 99.995 / 100 = -0.005% — still above the -0.01% threshold, must NOT trigger.
    sig = ex._server_side_stop_loss_signal(
        strategy_id=1, symbol="BTC/USDT", current_price=99.995,
        market_type="swap", leverage=10.0,
        trading_config=cfg, timeframe_seconds=60,
    )
    assert sig is None, "0.005% adverse move must not trigger a 0.01% SL"

    # 99.99 / 100 = -0.01% — must trigger.
    sig = ex._server_side_stop_loss_signal(
        strategy_id=1, symbol="BTC/USDT", current_price=99.99,
        market_type="swap", leverage=10.0,
        trading_config=cfg, timeframe_seconds=60,
    )
    assert sig is not None, "0.01% adverse move must trigger a 0.01% SL"
    assert sig["type"] == "close_long"
    assert sig["reason"] == "server_stop_loss"

    # 99.5 / 100 = -0.5% — sanity: also triggers (we are well past 0.01%).
    sig = ex._server_side_stop_loss_signal(
        strategy_id=1, symbol="BTC/USDT", current_price=99.5,
        market_type="swap", leverage=10.0,
        trading_config=cfg, timeframe_seconds=60,
    )
    assert sig is not None


def test_half_percent_take_profit_triggers_correctly(monkeypatch):
    """0.5% TP must fire on 0.5% price move, NOT 50%."""
    ex = _make_executor()
    cfg = {"take_profit_pct": 0.5, "enable_server_side_take_profit": True}
    monkeypatch.setattr(ex, "_get_current_positions", lambda *a, **k: [
        {"side": "long", "entry_price": 100.0, "size": 1.0,
         "highest_price": 0, "lowest_price": 0, "symbol": "BTC/USDT"}
    ])
    monkeypatch.setattr(ex, "_update_position", lambda *a, **k: None)

    # 100.4 → 0.4% gain, must not trigger 0.5% TP.
    sig = ex._server_side_take_profit_or_trailing_signal(
        strategy_id=1, symbol="BTC/USDT", current_price=100.4,
        market_type="swap", leverage=10.0,
        trading_config=cfg, timeframe_seconds=60,
    )
    assert sig is None

    # 100.5 → 0.5% gain, must trigger.
    sig = ex._server_side_take_profit_or_trailing_signal(
        strategy_id=1, symbol="BTC/USDT", current_price=100.5,
        market_type="swap", leverage=10.0,
        trading_config=cfg, timeframe_seconds=60,
    )
    assert sig is not None
    assert sig["reason"] == "server_take_profit"


def test_live_trailing_waits_until_round_trip_fees_are_covered(monkeypatch):
    """Tiny trailing settings must not create a net-losing profit-protection exit."""
    ex = _make_executor()
    ex._exchange_fee_cache = {}
    cfg = {
        "trailing_enabled": True,
        "trailing_stop_pct": 0.03,        # flat config percent: 0.03% -> 0.0003
        "trailing_activation_pct": 0.09,  # 0.09% -> 0.0009
        "commission": 0.05,              # 0.05% taker fee
        "enable_server_side_take_profit": True,
    }
    monkeypatch.setattr(ex, "_update_position", lambda *a, **k: None)

    monkeypatch.setattr(ex, "_get_current_positions", lambda *a, **k: [
        {"side": "long", "entry_price": 100.0, "size": 1.0,
         "highest_price": 100.09, "lowest_price": 100.0, "symbol": "BTC/USDT"}
    ])
    sig = ex._server_side_take_profit_or_trailing_signal(
        strategy_id=1, symbol="BTC/USDT", current_price=100.059,
        market_type="swap", leverage=10.0,
        trading_config=cfg, timeframe_seconds=60,
    )
    assert sig is None

    monkeypatch.setattr(ex, "_get_current_positions", lambda *a, **k: [
        {"side": "long", "entry_price": 100.0, "size": 1.0,
         "highest_price": 100.20, "lowest_price": 100.0, "symbol": "BTC/USDT"}
    ])
    sig = ex._server_side_take_profit_or_trailing_signal(
        strategy_id=1, symbol="BTC/USDT", current_price=100.16,
        market_type="swap", leverage=10.0,
        trading_config=cfg, timeframe_seconds=60,
    )
    assert sig is not None
    assert sig["type"] == "close_long"
    assert sig["reason"] == "server_trailing_stop"
    assert sig["trigger_price"] == pytest.approx(100.16)
    assert sig["position_size"] == pytest.approx(1.0)
    assert sig["matched_entry_price"] == pytest.approx(100.0)


def test_backtest_trailing_profit_guard_uses_round_trip_fees():
    from app.utils.risk_guard import trailing_exit_locks_net_profit

    assert not trailing_exit_locks_net_profit(
        "long", entry_price=100.0, exit_price=100.05, fee_rate=0.0005
    )
    assert trailing_exit_locks_net_profit(
        "long", entry_price=100.0, exit_price=100.11, fee_rate=0.0005
    )
    assert not trailing_exit_locks_net_profit(
        "short", entry_price=100.0, exit_price=99.95, fee_rate=0.0005
    )
    assert trailing_exit_locks_net_profit(
        "short", entry_price=100.0, exit_price=99.89, fee_rate=0.0005
    )


def test_indicator_exit_owner_disables_server_side_price_exits(monkeypatch):
    ex = _make_executor()
    ex._exchange_fee_cache = {}
    cfg = {
        "enable_server_side_stop_loss": True,
        "enable_server_side_take_profit": True,
        "_strategy_cfg_from_code": {
            "exitOwner": "indicator",
            "risk": {
                "stopLossPct": 0.01,
                "takeProfitPct": 0.01,
                "trailing": {
                    "enabled": True,
                    "pct": 0.001,
                    "activationPct": 0.001,
                },
            },
            "position": {"entryPct": 1.0},
        },
    }
    monkeypatch.setattr(ex, "_get_current_positions", lambda *a, **k: [
        {"side": "long", "entry_price": 100.0, "size": 1.0,
         "highest_price": 110.0, "lowest_price": 90.0, "symbol": "BTC/USDT"}
    ])
    monkeypatch.setattr(ex, "_update_position", lambda *a, **k: None)

    assert ex._server_side_stop_loss_signal(
        strategy_id=1, symbol="BTC/USDT", current_price=50.0,
        market_type="swap", leverage=10.0,
        trading_config=cfg, timeframe_seconds=60,
    ) is None

    assert ex._server_side_take_profit_or_trailing_signal(
        strategy_id=1, symbol="BTC/USDT", current_price=120.0,
        market_type="swap", leverage=10.0,
        trading_config=cfg, timeframe_seconds=60,
    ) is None
