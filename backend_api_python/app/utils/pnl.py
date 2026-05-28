"""Shared PnL helpers — keep futures margin semantics consistent across routes."""
from __future__ import annotations


def is_derivatives_market(market_type: str) -> bool:
    mt = str(market_type or "").strip().lower()
    return mt in ("swap", "futures", "future", "perp", "perpetual")


def calc_unrealized_pnl(side: str, entry_price: float, current_price: float, size: float) -> float:
    """Absolute PnL in quote currency (USDT) from base-asset size."""
    try:
        ep = float(entry_price or 0.0)
        cp = float(current_price or 0.0)
        sz = float(size or 0.0)
        if ep <= 0 or cp <= 0 or sz <= 0:
            return 0.0
        s = (side or "").strip().lower()
        if s == "short":
            return (ep - cp) * sz
        return (cp - ep) * sz
    except Exception:
        return 0.0


def calc_notional_value(entry_price: float, size: float) -> float:
    try:
        ep = float(entry_price or 0.0)
        sz = float(size or 0.0)
        if ep <= 0 or sz <= 0:
            return 0.0
        return ep * sz
    except Exception:
        return 0.0


def calc_margin_notional(notional: float, leverage: float, market_type: str) -> float:
    """Margin used for a linear USDT-margined position."""
    try:
        n = float(notional or 0.0)
        if n <= 0:
            return 0.0
        if not is_derivatives_market(market_type):
            return n
        lev = float(leverage or 1.0)
        if lev <= 0:
            lev = 1.0
        return n / lev
    except Exception:
        return 0.0


def calc_pnl_percent(
    entry_price: float,
    size: float,
    pnl: float,
    *,
    leverage: float = 1.0,
    market_type: str = "spot",
) -> float:
    """
    Return-on-margin % for derivatives, price-change % for spot.

    Futures semantics match backtest / server-side exits:
    user-facing stop/take-profit percentages are margin PnL, so display should too.
    """
    try:
        denom = calc_notional_value(entry_price, size)
        if denom <= 0:
            return 0.0
        lev = float(leverage or 1.0)
        if lev <= 0:
            lev = 1.0
        mult = lev if is_derivatives_market(market_type) else 1.0
        return float(pnl) / denom * 100.0 * mult
    except Exception:
        return 0.0
