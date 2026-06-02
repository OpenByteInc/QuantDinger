"""Neutral grid exchange hedge-mode requirements."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.services.grid.config import GridBotConfig
from app.services.grid.exchange_requirements import (
    detect_hedge_position_mode,
    fetch_exchange_dual_leg_snapshot,
    neutral_grid_requires_hedge_mode,
    validate_neutral_grid_exchange_support,
)
from app.services.grid.runner import GridRestingRunner


def _neutral_cfg(**overrides):
    bp = {
        "upperPrice": 100000,
        "lowerPrice": 90000,
        "gridCount": 5,
        "amountPerGrid": 50,
        "gridDirection": "neutral",
        "initialPositionPct": 15,
    }
    bp.update(overrides.get("bot_params") or {})
    return GridBotConfig.from_trading_config(
        {
            "leverage": 5,
            "market_type": "swap",
            "bot_params": bp,
        }
    )


def test_neutral_grid_requires_hedge_for_swap():
    cfg = _neutral_cfg()
    assert neutral_grid_requires_hedge_mode(cfg) is True
    cfg_long = GridBotConfig.from_trading_config(
        {"market_type": "swap", "bot_params": {"gridDirection": "long"}}
    )
    assert neutral_grid_requires_hedge_mode(cfg_long) is False


def test_bitget_one_way_blocks_neutral_startup():
    class FakeBitget:
        def get_account_pos_mode(self, **kwargs):
            return "one_way_mode"

    cfg = _neutral_cfg()
    ok, msg = validate_neutral_grid_exchange_support(
        cfg,
        FakeBitget(),
        symbol="BTC/USDT",
        exchange_config={"product_type": "USDT-FUTURES", "margin_coin": "USDT"},
    )
    assert ok is False
    assert "hedge" in msg.lower()


def test_bitget_hedge_allows_neutral_startup():
    class FakeBitget:
        def get_account_pos_mode(self, **kwargs):
            return "hedge_mode"

    cfg = _neutral_cfg()
    ok, msg = validate_neutral_grid_exchange_support(
        cfg,
        FakeBitget(),
        symbol="BTC/USDT",
        exchange_config={"product_type": "USDT-FUTURES", "margin_coin": "USDT"},
    )
    assert ok is True
    assert msg == ""


def test_runner_startup_rejects_bitget_one_way(monkeypatch):
    class FakeBitgetClient:
        def get_account_pos_mode(self, **kwargs):
            return "one_way_mode"

    def _create_client():
        return FakeBitgetClient()

    with patch("app.services.grid.engine.place_grid_limit_order") as place:
        place.return_value = MagicMock(exchange_order_id="ex1")
        with patch("app.services.grid.engine.GridRestingOrderRepository") as repo_cls:
            repo = repo_cls.return_value
            repo.has_open_for_cell.return_value = False
            repo.insert.return_value = 1
            runner = GridRestingRunner(
                99,
                "BTC/USDT",
                {
                    "leverage": 5,
                    "market_type": "swap",
                    "initial_capital": 100,
                    "bot_params": {
                        "upperPrice": 80500,
                        "lowerPrice": 70000,
                        "gridCount": 32,
                        "amountPerGrid": 3,
                        "gridDirection": "neutral",
                        "initialPositionPct": 15,
                    },
                },
                {"exchange_id": "bitget", "product_type": "USDT-FUTURES", "margin_coin": "USDT"},
                user_id=1,
                initial_capital=100,
                enqueue_market_fn=lambda *a, **k: True,
                create_client_fn=_create_client,
            )
            ok, msg = runner.startup(73494.0)
    assert ok is False
    assert "hedge" in msg.lower()


def test_fetch_exchange_dual_leg_snapshot(monkeypatch):
    class FakeBitget:
        def get_account_pos_mode(self, **kwargs):
            return "hedge_mode"

    from app.services.live_trading import position_query

    monkeypatch.setattr(
        position_query,
        "query_exchange_position_size",
        lambda **kwargs: 0.01 if kwargs.get("pos_side") == "long" else 0.0,
    )
    snap = fetch_exchange_dual_leg_snapshot(
        FakeBitget(),
        symbol="BTC/USDT",
        market_type="swap",
        exchange_config={"product_type": "USDT-FUTURES"},
    )
    assert snap["long_size"] == 0.01
    assert snap["short_size"] == 0.0
    assert snap["hedge_mode"] is True
