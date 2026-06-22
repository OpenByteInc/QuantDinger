"""Grid config validation before live start."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.services.grid.config import GridBotConfig
from app.services.grid.levels import generate_cells, generate_levels

MIN_GRID_FEE_COVERAGE_MULTIPLIER = 1.25


def _fee_rate_pct(fee_rate: float) -> float:
    try:
        return max(0.0, float(fee_rate or 0.0)) * 100.0
    except Exception:
        return 0.0


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

    # Net-profit guard: a grid cell must cover both entry and exit commissions.
    # Leverage and quantity cancel out in this per-unit check, so compare the
    # price distance with estimated round-trip fees directly.
    worst = None
    for cell in cells:
        lo = float(cell.lower_price or 0.0)
        hi = float(cell.upper_price or 0.0)
        if lo <= 0 or hi <= lo:
            continue
        gross_per_unit = hi - lo
        round_trip_fee_per_unit = (lo + hi) * max(0.0, float(fee_rate or 0.0))
        required = round_trip_fee_per_unit * MIN_GRID_FEE_COVERAGE_MULTIPLIER
        margin = gross_per_unit - required
        if worst is None or margin < worst[0]:
            worst = (margin, lo, hi, gross_per_unit, required)
    if worst is not None and worst[0] <= 0:
        _, lo, hi, gross, required = worst
        gross_pct = (gross / lo * 100.0) if lo > 0 else 0.0
        required_pct = (required / lo * 100.0) if lo > 0 else 0.0
        return (
            False,
            "Grid spacing is too narrow after fees: "
            f"worst cell [{lo:.4f}, {hi:.4f}] captures ~{gross_pct:.3f}% "
            f"but needs ~{required_pct:.3f}% to cover round-trip fees "
            f"({_fee_rate_pct(fee_rate):.3f}% per side plus safety buffer). "
            "Widen the price range, reduce gridCount, or lower fee settings.",
            warnings,
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
