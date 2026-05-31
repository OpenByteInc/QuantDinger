"""Tests for professional grid engine."""

from __future__ import annotations

from app.services.grid.config import GridBotConfig
from app.services.grid.levels import generate_cells, generate_levels
from app.services.grid.validator import validate_grid_config


def test_generate_levels_arithmetic():
    levels = generate_levels(90000, 100000, 10, "arithmetic")
    assert len(levels) == 10
    assert levels[0] == 90000
    assert abs(levels[-1] - 100000) < 1e-6


def test_generate_cells_count():
    levels = generate_levels(100, 200, 5, "arithmetic")
    cells = generate_cells(levels)
    assert len(cells) == 4


def test_validate_long_grid_ok():
    cfg = GridBotConfig(
        upper_price=100000,
        lower_price=90000,
        grid_count=10,
        amount_per_grid=100,
        grid_mode="arithmetic",
        grid_direction="long",
        initial_position_pct=0.3,
        order_mode="maker",
        boundary_action="pause",
        leverage=5,
        market_type="swap",
        margin_mode="cross",
    )
    ok, msg, _ = validate_grid_config(cfg, initial_capital=10000)
    assert ok is True
    assert msg == ""


def test_validate_rejects_bad_bounds():
    cfg = GridBotConfig(
        upper_price=100,
        lower_price=200,
        grid_count=10,
        amount_per_grid=50,
        grid_mode="arithmetic",
        grid_direction="long",
        initial_position_pct=0,
        order_mode="maker",
        boundary_action="pause",
        leverage=1,
        market_type="swap",
        margin_mode="cross",
    )
    ok, msg, _ = validate_grid_config(cfg)
    assert ok is False
    assert "upperPrice" in msg


def test_config_from_trading_config_initial_pct():
    tc = {
        "leverage": 5,
        "market_type": "swap",
        "bot_params": {
            "upperPrice": 100000,
            "lowerPrice": 90000,
            "gridCount": 10,
            "amountPerGrid": 100,
            "gridDirection": "long",
            "initialPositionPct": 30,
        },
    }
    cfg = GridBotConfig.from_trading_config(tc)
    assert cfg.initial_position_pct == 0.3
    assert cfg.grid_direction == "long"
