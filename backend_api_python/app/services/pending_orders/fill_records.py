"""Fill persistence helpers used by pending order execution."""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from app.utils.db import get_db_connection
from app.services.live_trading.leg_context import resolve_leg_context
from app.services.live_trading.records import (
    apply_fill_to_local_position,
    record_trade,
)
from app.services.pending_orders.position_sync_cache import invalidate_position_sync_snapshot_for_exchange
from app.utils.logger import get_logger
from app.utils.trade_close_reason import is_exit_trade_type

logger = get_logger(__name__)


def spot_position_fill_quantity(
    *,
    market_type: str,
    symbol: str,
    signal_type: str,
    gross_quantity: float,
    fees_by_ccy: Dict[str, float],
) -> float:
    """Return the quantity that actually changes strategy-owned spot inventory."""
    gross = max(0.0, float(gross_quantity or 0.0))
    if str(market_type or "").strip().lower() != "spot":
        return gross
    from app.services.live_trading.fee_quote import symbol_currencies

    base, _quote = symbol_currencies(symbol)
    base_fee = max(0.0, float((fees_by_ccy or {}).get(base, 0.0) or 0.0))
    signal = str(signal_type or "").strip().lower()
    if signal in {"open_long", "add_long"}:
        return max(0.0, gross - base_fee)
    if signal in {"close_long", "reduce_long"}:
        return gross + base_fee
    return gross


def proportional_spot_position_fill_quantity(
    market_type: str,
    symbol: str,
    signal_type: str,
    recorded_quantity: float,
    cumulative_quantity: float,
    cumulative_fees_by_ccy: Dict[str, float],
) -> float:
    """Apply only the recorded fill's proportional share of cumulative fees."""
    recorded = max(0.0, float(recorded_quantity or 0.0))
    cumulative = max(0.0, float(cumulative_quantity or 0.0))
    share = min(1.0, recorded / cumulative) if cumulative > 0 else 1.0
    fees = {
        currency: float(amount or 0.0) * share
        for currency, amount in (cumulative_fees_by_ccy or {}).items()
    }
    return spot_position_fill_quantity(
        market_type=market_type,
        symbol=symbol,
        signal_type=signal_type,
        gross_quantity=recorded,
        fees_by_ccy=fees,
    )


def persisted_order_fill_baseline(
    *,
    exchange_order_id: str,
    order_intent_id: int = 0,
    exchange_id: str = "",
) -> Tuple[float, float]:
    """Sum the fills already persisted in the ledger for one exchange order.

    Returns ``(filled_qty, avg_price)`` with a notional-weighted avg (0.0
    when nothing is recorded). Serves as the authoritative duplicate-fill
    guard: the live fill sync must never re-book quantity the ledger
    already holds for the same exchange order, even when the pending-order
    row lost its fill baseline (restart, stale snapshot marker).
    """
    exchange_order_id = str(exchange_order_id or "").strip()
    intent_id = int(order_intent_id or 0)
    venue = str(exchange_id or "").strip().lower()
    if not exchange_order_id and intent_id <= 0:
        return 0.0, 0.0
    try:
        with get_db_connection() as db:
            cur = db.cursor()
            if exchange_order_id:
                cur.execute(
                    """
                    SELECT COALESCE(SUM(quantity), 0) AS filled,
                           COALESCE(SUM(notional), 0) AS notional
                    FROM strategy_order_fills
                    WHERE exchange_order_id = %s
                      AND (%s = '' OR exchange_id = %s)
                    """,
                    (exchange_order_id, venue, venue),
                )
            else:
                cur.execute(
                    """
                    SELECT COALESCE(SUM(quantity), 0) AS filled,
                           COALESCE(SUM(notional), 0) AS notional
                    FROM strategy_order_fills
                    WHERE order_intent_id = %s
                    """,
                    (intent_id,),
                )
            baseline_row = cur.fetchone() or {}
            cur.close()
    except Exception:
        logger.debug("persisted fill baseline unavailable", exc_info=True)
        return 0.0, 0.0
    filled = max(0.0, float(baseline_row.get("filled") or 0.0))
    notional = max(0.0, float(baseline_row.get("notional") or 0.0))
    if filled <= 0.0:
        return 0.0, 0.0
    return filled, (notional / filled if notional > 0.0 else 0.0)


def persist_strategy_fill(
    *,
    strategy_id: int,
    symbol: str,
    signal_type: str,
    filled: float,
    avg_price: float,
    exchange_config: Dict[str, Any],
    market_type: str,
    order_id: int = 0,
    fill_source: str = "worker",
    commission: float = 0.0,
    commission_ccy: str = "",
    commission_quote: Optional[float] = None,
    profit: Optional[float] = None,
    close_reason: str = "",
    matched_entry_price: Optional[float] = None,
    grid_matched_profit: Optional[float] = None,
    inst_id: str = "",
    strategy_run_id: int = 0,
    order_intent_id: int = 0,
    exchange_id: str = "",
    exchange_order_id: str = "",
    raw_fill: Optional[Dict[str, Any]] = None,
    position_filled: Optional[float] = None,
    exchange_fill_id: str = "",
    execution_event_id: int = 0,
    fee_status: str = "pending",
    fee_source: str = "",
) -> Tuple[Optional[float], Optional[float]]:
    """Apply a fill to local positions and append a trade row."""
    filled_qty = float(filled or 0.0)
    avg_px = float(avg_price or 0.0)
    if abs(filled_qty) <= 1e-12:
        logger.info(
            "Skip zero-sized strategy fill: strategy_id=%s symbol=%s signal=%s order_id=%s source=%s",
            strategy_id,
            symbol,
            signal_type,
            order_id,
            fill_source,
        )
        return profit, matched_entry_price

    leg = resolve_leg_context(
        strategy_id=int(strategy_id),
        symbol=str(symbol or ""),
        exchange_config=exchange_config,
        market_type=str(market_type or "swap"),
        inst_id=str(inst_id or ""),
        fill_source=str(fill_source or "worker"),
        pending_order_id=int(order_id or 0),
    )
    position_qty = float(position_filled) if position_filled is not None else filled_qty
    profit_out, _pos, matched_entry = apply_fill_to_local_position(
        strategy_id=int(strategy_id),
        symbol=str(symbol or ""),
        signal_type=str(signal_type or ""),
        filled=position_qty,
        avg_price=avg_px,
        leg=leg,
    )
    if profit is None:
        profit = profit_out
    if matched_entry_price is None:
        matched_entry_price = matched_entry

    record_trade(
        strategy_id=int(strategy_id),
        symbol=str(symbol or ""),
        trade_type=str(signal_type or ""),
        price=avg_px,
        amount=filled_qty,
        commission=float(commission or 0.0),
        commission_ccy=str(commission_ccy or ""),
        commission_quote=commission_quote,
        profit=profit,
        close_reason=str(close_reason or ""),
        matched_entry_price=matched_entry_price,
        grid_matched_profit=grid_matched_profit if grid_matched_profit is not None else profit,
        leg=leg,
        strategy_run_id=int(strategy_run_id or 0),
        order_intent_id=int(order_intent_id or 0),
        execution_event_id=int(execution_event_id or 0),
        exchange_fill_id=str(exchange_fill_id or ""),
        fee_status=str(fee_status or "pending"),
        fee_source=str(fee_source or ""),
    )

    _record_runtime_fill(
        strategy_id=int(strategy_id),
        strategy_run_id=int(strategy_run_id or 0),
        order_intent_id=int(order_intent_id or 0),
        signal_type=str(signal_type or ""),
        price=avg_px,
        quantity=filled_qty,
        fee=float(commission or 0.0),
        fee_ccy=str(commission_ccy or ""),
        exchange_id=str(exchange_id or (exchange_config or {}).get("exchange_id") or ""),
        exchange_order_id=str(exchange_order_id or ""),
        raw_fill=raw_fill or {},
        credential_id=int(leg.credential_id or 0),
        exchange_fill_id=str(exchange_fill_id or ""),
        fee_status=str(fee_status or "pending"),
        commission_quote=commission_quote,
    )

    try:
        from app.services.live_trading.records import _get_user_id_from_strategy

        invalidate_position_sync_snapshot_for_exchange(
            user_id=_get_user_id_from_strategy(int(strategy_id)),
            exchange_id=str(exchange_config.get("exchange_id") or "").strip().lower(),
            market_type=str(market_type or "swap"),
            exchange_config=exchange_config if isinstance(exchange_config, dict) else {},
        )
    except Exception:
        pass
    return profit, matched_entry_price


def _record_runtime_fill(
    *,
    strategy_id: int,
    strategy_run_id: int,
    order_intent_id: int,
    signal_type: str,
    price: float,
    quantity: float,
    fee: float,
    fee_ccy: str,
    exchange_id: str,
    exchange_order_id: str,
    raw_fill: Dict[str, Any],
    credential_id: int = 0,
    exchange_fill_id: str = "",
    fee_status: str = "pending",
    commission_quote: Optional[float] = None,
) -> None:
    if strategy_run_id <= 0 and order_intent_id <= 0:
        return
    import json

    sig = str(signal_type or "").lower()
    pos_side = "short" if "short" in sig else "long" if "long" in sig else ""
    side = "buy" if sig in ("open_long", "add_long", "close_short", "reduce_short") else "sell"
    try:
        from app.services.live_trading.partner_attribution import redact_partner_attribution

        safe_raw = json.loads(json.dumps(redact_partner_attribution(raw_fill or {}), default=str))
    except Exception:
        safe_raw = {}
    try:
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                INSERT INTO strategy_order_fills
                (order_intent_id, strategy_run_id, strategy_id,
                 exchange_id, exchange_order_id, exchange_fill_id,
                 side, position_side, price, quantity, notional, fee, fee_ccy,
                 credential_id, commission_quote, fee_status, filled_at, raw_json)
                VALUES
                (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s)
                ON CONFLICT (exchange_id, credential_id, exchange_fill_id)
                WHERE exchange_fill_id <> '' DO NOTHING
                """,
                (
                    int(order_intent_id or 0),
                    int(strategy_run_id or 0),
                    int(strategy_id or 0),
                    str(exchange_id or ""),
                    str(exchange_order_id or ""),
                    str(exchange_fill_id or safe_raw.get("fill_id") or safe_raw.get("trade_id") or ""),
                    side,
                    pos_side,
                    float(price or 0.0),
                    float(quantity or 0.0),
                    float(price or 0.0) * float(quantity or 0.0),
                    float(fee or 0.0),
                    str(fee_ccy or ""),
                    int(credential_id or 0),
                    float(commission_quote) if commission_quote is not None else None,
                    str(fee_status or "pending"),
                    json.dumps(safe_raw, ensure_ascii=False),
                ),
            )
            if int(order_intent_id or 0) > 0:
                cur.execute(
                    """
                    UPDATE strategy_order_intents
                    SET status = CASE WHEN %s > 0 THEN 'partially_filled' ELSE status END,
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (float(quantity or 0.0), int(order_intent_id or 0)),
                )
            db.commit()
            cur.close()
    except Exception as exc:
        logger.debug("runtime fill record skipped: %s", exc)


def trade_close_reason_from_payload(payload: Dict[str, Any], signal_type: str) -> str:
    """Return the close reason only for exit-like trade types."""
    if is_exit_trade_type(str(signal_type or "")):
        return str((payload or {}).get("reason") or "").strip()
    return ""
