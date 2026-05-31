"""Grid config validation before live start."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.services.grid.config import GridBotConfig
from app.services.grid.levels import generate_cells, generate_levels


def validate_grid_config(
    cfg: GridBotConfig,
    *,
    initial_capital: float = 0.0,
    fee_rate: float = 0.001,
) -> Tuple[bool, str, List[str]]:
    warnings: List[str] = []
    if cfg.upper_price <= cfg.lower_price:
        return False, "upperPrice must be greater than lowerPrice", warnings
    if cfg.amount_per_grid <= 0:
        return False, "amountPerGrid must be > 0", warnings
    levels = generate_levels(cfg.lower_price, cfg.upper_price, cfg.grid_count, cfg.grid_mode)
    cells = generate_cells(levels)
    if len(cells) < 1:
        return False, "gridCount too small to form grid cells", warnings
    if cfg.grid_direction not in ("long", "short", "neutral"):
        return False, f"unsupported gridDirection: {cfg.grid_direction}", warnings
    if cfg.initial_position_pct < 0 or cfg.initial_position_pct > 1:
        return False, "initialPositionPct must be between 0 and 100 (or 0–1)", warnings

    # Rough fee vs spacing check on middle cell
    mid = cells[len(cells) // 2]
    if mid.lower_price > 0:
        spacing_pct = (mid.upper_price - mid.lower_price) / mid.lower_price
        min_edge = fee_rate * 2 * 1.5
        if spacing_pct <= min_edge:
            warnings.append(
                f"Grid spacing ~{spacing_pct*100:.3f}% may not cover fees (~{min_edge*100:.3f}%)"
            )

    if initial_capital > 0 and cfg.initial_position_pct > 0:
        init_usdt = initial_capital * cfg.initial_position_pct
        if init_usdt < cfg.amount_per_grid * 0.5:
            warnings.append(
                f"Initial position (~{init_usdt:.2f} USDT) is small vs amountPerGrid ({cfg.amount_per_grid})"
            )

    return True, "", warnings


def validate_for_executor(trading_config: Dict[str, Any], initial_capital: float = 0.0) -> Tuple[bool, str]:
    cfg = GridBotConfig.from_trading_config(trading_config)
    ok, msg, _w = validate_grid_config(cfg, initial_capital=initial_capital)
    return ok, msg
