"""Hard safety limits for strategy-generated opening orders."""

from __future__ import annotations

import math
from typing import Any, Iterable, Mapping


ENTRY_ACTIONS = {"open_long", "add_long", "open_short", "add_short"}


def strategy_order_budget_snapshot(
    *,
    action: str,
    quantity: float,
    price: float,
    initial_capital: float,
    leverage: float,
    market_type: str,
    current_positions: Iterable[Mapping[str, Any]] = (),
    buffer_ratio: float = 0.02,
) -> dict[str, Any]:
    """Return a fail-closed projected notional check for an entry order.

    Values entering this boundary are base-asset quantities.  Comparing their
    quote notional to the strategy's configured capital catches legacy robot
    sources that accidentally pass quote currency to ``order()``.
    """
    action_norm = str(action or "").strip().lower()
    valid_numbers = True

    def finite_number(value: Any) -> float:
        nonlocal valid_numbers
        try:
            number = float(value)
            if isinstance(value, bool) or not math.isfinite(number):
                raise ValueError("invalid number")
            return number
        except (TypeError, ValueError, OverflowError):
            valid_numbers = False
            return 0.0

    qty = max(0.0, finite_number(quantity))
    mark = max(0.0, finite_number(price))
    capital = max(0.0, finite_number(initial_capital))
    market = str(market_type or "spot").strip().lower()
    parsed_leverage = finite_number(leverage)
    effective_leverage = max(1.0, parsed_leverage) if market != "spot" else 1.0
    capacity = capital * effective_leverage
    buffer = min(0.10, max(0.0, finite_number(buffer_ratio)))
    limit = capacity * (1.0 + buffer)

    side = "long" if action_norm.endswith("_long") else "short"
    current_qty = 0.0
    for row in current_positions or ():
        if str(row.get("side") or "long").strip().lower() != side:
            continue
        current_qty += abs(finite_number(row.get("size") or row.get("quantity") or 0.0))

    order_notional = qty * mark
    projected_notional = (current_qty + qty) * mark
    valid_numbers = valid_numbers and all(math.isfinite(number) for number in (
        capacity, limit, current_qty, order_notional, projected_notional,
    ))
    verifiable = valid_numbers and capacity > 0 and mark > 0 and qty > 0
    allowed = action_norm not in ENTRY_ACTIONS or (
        verifiable and order_notional <= limit + 1e-8 and projected_notional <= limit + 1e-8
    )
    return {
        "allowed": bool(allowed),
        "action": action_norm,
        "side": side,
        "quantity": qty,
        "price": mark,
        "order_notional": order_notional,
        "current_quantity": current_qty,
        "projected_notional": projected_notional,
        "capacity": capacity,
        "limit": limit,
        "buffer_ratio": buffer,
        "reason": "" if allowed else ("budget_unverifiable" if not verifiable else "strategy_budget_exceeded"),
    }


__all__ = ["ENTRY_ACTIONS", "strategy_order_budget_snapshot"]
