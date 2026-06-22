"""Risk-control guard helpers shared by live execution and backtests."""

from __future__ import annotations

from typing import Literal


DEFAULT_TAKER_FEE_RATE = 0.001


def coerce_fee_rate(value: object, *, default: float = DEFAULT_TAKER_FEE_RATE) -> float:
    """Return a sane decimal fee rate, e.g. 0.001 for 0.1%."""
    try:
        fee = float(value if value is not None else default)
    except (TypeError, ValueError):
        fee = float(default)
    if fee < 0:
        return 0.0
    if fee > 0.05:
        # This helper expects decimal rates. If a caller accidentally passes
        # a percent number such as 0.1 (0.1%), interpret it defensively.
        fee = fee / 100.0
    return min(fee, 0.05)


def trailing_exit_locks_net_profit(
    side: Literal["long", "short"],
    *,
    entry_price: float,
    exit_price: float,
    fee_rate: float,
    extra_buffer: float = 0.0,
) -> bool:
    """
    True when a trailing exit price is beyond round-trip fee breakeven.

    Trailing exits are presented as profit-protecting exits in the product. A
    tiny activation/callback pair can otherwise close after a favorable move
    while still losing money after open + close fees.
    """
    try:
        entry = float(entry_price or 0.0)
        exit_px = float(exit_price or 0.0)
    except (TypeError, ValueError):
        return False
    if entry <= 0 or exit_px <= 0:
        return False

    fee = coerce_fee_rate(fee_rate, default=0.0)
    try:
        extra = max(0.0, float(extra_buffer or 0.0))
    except (TypeError, ValueError):
        extra = 0.0
    min_move = max(0.0, 2.0 * fee + extra)

    if side == "long":
        return exit_px >= entry * (1.0 + min_move)
    if side == "short":
        return exit_px <= entry * (1.0 - min_move)
    return False
