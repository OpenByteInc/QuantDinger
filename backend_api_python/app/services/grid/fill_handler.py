"""Update local DB after a grid resting order fills."""

from __future__ import annotations

from typing import Any, Dict

from app.services.grid.resting_orders_repo import GridRestingOrder
from app.services.live_trading.fill_evidence import require_execution
from app.services.live_trading.leg_context import resolve_leg_context
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
    *,
    commission: float = 0.0,
    commission_ccy: str = "",
    commission_quote: float | None = None,
    fee_status: str = "pending",
    fee_source: str = "",
    exchange_fill_id: str = "",
    execution_event_id: int = 0,
    fees_by_ccy: Dict[str, float] | None = None,
    strategy_run_id: int = 0,
) -> None:
    sym = normalize_strategy_symbol(symbol)
    purpose = str(order.purpose or "")
    signal_type = _PURPOSE_TO_SIGNAL.get(purpose, "")
    if not signal_type:
        return
    px = float(avg_price or 0)
    qty = float(filled_qty or 0)
    require_execution(qty, px)
    tc = trading_config if isinstance(trading_config, dict) else {}
    from app.services.pending_orders.fill_records import spot_position_fill_quantity

    leg = resolve_leg_context(
        strategy_id=int(strategy_id),
        symbol=sym,
        market_type=str(tc.get("market_type") or "swap"),
        fill_source="grid_poller",
    )
    profit, _pos, matched_entry = apply_fill_to_local_position(
        strategy_id=int(strategy_id),
        symbol=sym,
        signal_type=signal_type,
        filled=spot_position_fill_quantity(
            market_type=str(tc.get('market_type') or 'swap'), symbol=sym, signal_type=signal_type,
            gross_quantity=qty, fees_by_ccy=fees_by_ccy or {}),
        avg_price=px,
        leg=leg,
        strategy_run_id=int(strategy_run_id or order.extra.get("strategy_run_id") or 0),
    )
    record_trade(
        strategy_id=int(strategy_id),
        symbol=sym,
        trade_type=signal_type,
        price=px,
        amount=qty,
        commission=float(commission or 0.0),
        commission_ccy=str(commission_ccy or ""),
        commission_quote=commission_quote,
        profit=profit,
        close_reason=purpose,
        matched_entry_price=matched_entry,
        grid_matched_profit=None,
        leg=leg,
        exchange_fill_id=str(exchange_fill_id or ""),
        execution_event_id=int(execution_event_id or 0),
        grid_order_id=int(order.id or 0),
        fee_status=str(fee_status or "pending"),
        fee_source=str(fee_source or ""),
        fees_by_ccy=fees_by_ccy,
        exchange_order_id=order.exchange_order_id,
    )


def record_grid_market_fill(*args, **kwargs):
    from app.utils.db import get_db_transaction
    from app.services.live_trading.fill_accounting import lock_strategy_fills
    with get_db_transaction():
        lock_strategy_fills(int(args[0] if args else kwargs['strategy_id']))
        return _record_grid_market_fill(*args, **kwargs)


def _record_grid_market_fill(
    strategy_id: int,
    symbol: str,
    signal_type: str,
    filled_qty: float,
    avg_price: float,
    trading_config: Dict[str, Any],
    *,
    reason: str = "",
    commission: float = 0.0,
    commission_ccy: str = "",
    commission_quote: float | None = None,
    exchange_order_id: str = "",
    client_order_id: str = "",
    exchange_id: str = "",
    user_id: int = 0,
    strategy_run_id: int = 0,
    fee_status: str = "pending",
    fee_source: str = "rest",
    fees_by_ccy: Dict[str, float] | None = None,
) -> int:
    """Record a grid initial/risk market fill into L2/L3 ledgers."""
    sym = normalize_strategy_symbol(symbol)
    sig = str(signal_type or "").strip().lower()
    if not sig:
        return 0
    px = float(avg_price or 0)
    qty = float(filled_qty or 0)
    require_execution(qty, px)
    tc = trading_config if isinstance(trading_config, dict) else {}
    leg = resolve_leg_context(
        strategy_id=int(strategy_id),
        symbol=sym,
        market_type=str(tc.get("market_type") or "swap"),
        fill_source="grid_market",
    )
    from app.services.pending_orders.fill_records import spot_position_fill_quantity
    fees_by_ccy = fees_by_ccy or ({commission_ccy: commission} if commission_ccy and commission_ccy != 'MIXED' else {})
    profit, _pos, matched_entry = apply_fill_to_local_position(
        strategy_id=int(strategy_id),
        symbol=sym,
        signal_type=sig,
        filled=spot_position_fill_quantity(market_type=leg.normalized_market_type(), symbol=sym, signal_type=sig, gross_quantity=qty, fees_by_ccy=fees_by_ccy),
        avg_price=px,
        leg=leg,
        strategy_run_id=int(strategy_run_id or 0),
    )
    trade_id = record_trade(
        strategy_id=int(strategy_id),
        symbol=sym,
        trade_type=sig,
        price=px,
        amount=qty,
        commission=float(commission or 0.0),
        commission_ccy=str(commission_ccy or ""),
        commission_quote=commission_quote,
        profit=profit,
        close_reason=str(reason or sig),
        matched_entry_price=matched_entry,
        leg=leg,
        strategy_run_id=int(strategy_run_id or 0),
        fee_status=str(fee_status or "pending"),
        fee_source=str(fee_source or "rest"),
        fees_by_ccy=fees_by_ccy,
        exchange_order_id=exchange_order_id,
    )
    if trade_id and (exchange_order_id or client_order_id):
        try:
            from app.services.execution_streams.repository import ExecutionEventRepository

            ExecutionEventRepository().register_binding(
                credential_id=int(leg.credential_id or 0),
                exchange_id=str(
                    exchange_id or tc.get("exchange_id") or tc.get("exchange") or ""
                ),
                market_type=leg.normalized_market_type(),
                owner_type="grid_market",
                owner_id=int(trade_id),
                user_id=int(user_id or 1),
                strategy_id=int(strategy_id),
                strategy_run_id=int(strategy_run_id or 0),
                symbol=sym,
                signal_type=sig,
                client_order_id=str(client_order_id or ""),
                exchange_order_id=str(exchange_order_id or ""),
                observed_filled=qty,
            )
        except Exception:
            logger.debug(
                "grid market execution binding failed trade_id=%s",
                trade_id,
                exc_info=True,
            )
    return int(trade_id or 0)
