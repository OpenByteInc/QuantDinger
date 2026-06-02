"""Grid bot configuration (reads existing bot_params field names)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


def _float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return float(default)


def _int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return int(default)


def _pct(v: Any, default: float = 0.0) -> float:
    """Accept 30 or 0.3 for 30%."""
    x = _float(v, default)
    if x > 1.0:
        return x / 100.0
    return max(0.0, x)


def sanitize_grid_bot_params(bot_params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Neutral grids do not use initial market position; clear stale pct from UI saves."""
    bp = dict(bot_params) if isinstance(bot_params, dict) else {}
    direction = str(bp.get("gridDirection") or bp.get("grid_direction") or "long").strip().lower()
    if direction == "neutral":
        bp["initialPositionPct"] = 0
        bp.pop("initial_position_pct", None)
    return bp


@dataclass
class GridBotConfig:
    upper_price: float
    lower_price: float
    grid_count: int
    amount_per_grid: float
    grid_mode: str
    grid_direction: str
    initial_position_pct: float
    order_mode: str
    boundary_action: str
    leverage: float
    market_type: str
    margin_mode: str

    @classmethod
    def from_trading_config(cls, trading_config: Dict[str, Any]) -> "GridBotConfig":
        tc = trading_config if isinstance(trading_config, dict) else {}
        bp = sanitize_grid_bot_params(tc.get("bot_params") if isinstance(tc.get("bot_params"), dict) else {})
        upper = _float(bp.get("upperPrice") or bp.get("upper_price"), 0.0)
        lower = _float(bp.get("lowerPrice") or bp.get("lower_price"), 0.0)
        direction = str(bp.get("gridDirection") or bp.get("grid_direction") or "long").strip().lower()
        if direction not in ("long", "short", "neutral"):
            direction = "long"
        order_mode = str(
            bp.get("orderMode") or bp.get("order_mode") or tc.get("order_mode") or "maker"
        ).strip().lower()
        boundary = str(bp.get("boundaryAction") or bp.get("boundary_action") or "pause").strip().lower()
        if boundary not in ("pause", "stop_loss", "hold"):
            boundary = "pause"
        mt = str(tc.get("market_type") or "swap").strip().lower()
        if mt in ("futures", "future", "perp", "perpetual"):
            mt = "swap"
        return cls(
            upper_price=upper,
            lower_price=lower,
            grid_count=max(2, _int(bp.get("gridCount") or bp.get("grid_count"), 10)),
            amount_per_grid=_float(bp.get("amountPerGrid") or bp.get("amount_per_grid"), 0.0),
            grid_mode=str(bp.get("gridMode") or bp.get("grid_mode") or "arithmetic").strip().lower(),
            grid_direction=direction,
            initial_position_pct=_pct(bp.get("initialPositionPct") or bp.get("initial_position_pct"), 0.0),
            order_mode=order_mode,
            boundary_action=boundary,
            leverage=max(1.0, _float(tc.get("leverage"), 1.0)),
            market_type=mt,
            margin_mode=str(tc.get("margin_mode") or tc.get("marginMode") or "cross").strip().lower(),
        )

    def effective_bounds(self, runtime_params: Optional[Dict[str, Any]] = None) -> tuple[float, float]:
        rp = runtime_params if isinstance(runtime_params, dict) else {}
        upper = _float(rp.get("upperPrice"), self.upper_price)
        lower = _float(rp.get("lowerPrice"), self.lower_price)
        return upper, lower
