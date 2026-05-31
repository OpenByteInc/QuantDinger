"""Update local DB after a grid resting order fills."""

from __future__ import annotations

from typing import Any, Dict

from app.services.grid.resting_orders_repo import GridRestingOrder
from app.services.live_trading.records import (
    apply_fill_to_local_position,
    normalize_strategy_symbol,
    record_trade,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)

_PURPOSE_TO_SIGNAL = {
    "long_entry": "open_long",
    "long_exit": "close_long",
    "short_entry": "open_short",
    "short_exit": "close_short",
}


def apply_grid_fill_to_local_state(
    strategy_id: int,
    symbol: str,
    order: GridRestingOrder,
    filled_qty: float,
    avg_price: float,
    trading_config: Dict[str, Any],
) -> None:
    sym = normalize_strategy_symbol(symbol)
    purpose = str(order.purpose or "")
    signal_type = _PURPOSE_TO_SIGNAL.get(purpose, "")
    if not signal_type:
        return
    px = float(avg_price or order.price or 0)
    qty = float(filled_qty or order.quantity or 0)
    if qty <= 0 or px <= 0:
        return
    tc = trading_config if isinstance(trading_config, dict) else {}
    fee_rate = float(tc.get("commission") or 0) / 100.0 or 0.001

    try:
        profit, _pos, matched_entry = apply_fill_to_local_position(
            strategy_id=int(strategy_id),
            symbol=sym,
            signal_type=signal_type,
            filled=qty,
            avg_price=px,
        )
        record_trade(
            strategy_id=int(strategy_id),
            symbol=sym,
            trade_type=signal_type,
            price=px,
            amount=qty,
            commission=px * qty * fee_rate,
            profit=profit,
            close_reason=purpose,
            matched_entry_price=matched_entry,
            grid_matched_profit=profit if profit is not None else None,
        )
    except Exception as e:
        logger.warning("apply_grid_fill sid=%s: %s", strategy_id, e)
