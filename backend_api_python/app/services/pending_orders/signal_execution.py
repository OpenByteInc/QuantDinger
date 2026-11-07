"""Signal-mode virtual execution and notification dispatch."""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Mapping

from app.utils.pnl import calc_notional_value


def dispatch_virtual_signal_order(
    *,
    worker: Any,
    order_row: Mapping[str, Any],
    payload: Mapping[str, Any],
    order_id: int,
    strategy_id: Any,
    strategy_name: str,
    signal_type: Any,
    symbol: Any,
    price: float,
    amount: float,
    direction: str,
    notification_config: Any,
    append_log: Callable[..., Any],
    logger: logging.Logger,
) -> None:
    """Fill the isolated virtual ledger, then send best-effort notifications."""
    try:
        from app.services.virtual_trading import execute_virtual_signal_order

        virtual_fill = execute_virtual_signal_order(dict(order_row), dict(payload))
    except Exception as exc:
        worker._mark_failed(order_id=order_id, error=f"virtual_fill_failed:{exc}"[:500])
        logger.exception(
            "Virtual fill failed: strategy_id=%s pending_id=%s signal=%s symbol=%s",
            strategy_id,
            order_id,
            signal_type,
            symbol,
        )
        append_log(
            int(strategy_id or 0),
            "error",
            "strategyRuntime.virtualFillFailed",
        )
        return

    resolved_notification_config = notification_config
    if not resolved_notification_config and strategy_id:
        resolved_notification_config = worker._load_notification_config(int(strategy_id))

    stake_quote = calc_notional_value(price, amount) or amount
    results = worker._notifier.notify_signal(
        strategy_id=int(strategy_id or 0),
        strategy_name=str(strategy_name or ""),
        symbol=str(symbol or ""),
        signal_type=str(signal_type or ""),
        price=price,
        stake_amount=float(stake_quote),
        direction=str(direction or "long"),
        notification_config=(
            resolved_notification_config
            if isinstance(resolved_notification_config, dict)
            else {}
        ),
        extra={
            "pending_order_id": order_id,
            "mode": "signal",
            "virtual_order_id": int(virtual_fill.get("virtual_order_id") or 0),
            "virtual_fill_price": float(virtual_fill.get("fill_price") or 0.0),
            "deferred_reason": str(virtual_fill.get("deferred_reason") or ""),
        },
    )

    attempted = list(results.keys())
    ok_channels = [channel for channel, result in results.items() if (result or {}).get("ok")]
    fail_channels = [channel for channel, result in results.items() if not (result or {}).get("ok")]
    virtual_status = str(virtual_fill.get("status") or "filled").strip().lower()
    deferred_reason = str(virtual_fill.get("deferred_reason") or "").strip().lower()
    virtual_note = f"virtual_{virtual_status}={int(virtual_fill.get('virtual_order_id') or 0)}"

    if ok_channels:
        note = f"{virtual_note};notified_ok={','.join(ok_channels)}"
        if fail_channels:
            note += f";fail={','.join(fail_channels)}"
        level = "signal"
        if deferred_reason == "market_closed":
            message = "strategyRuntime.virtualOrderDeferredMarketClosed"
        elif virtual_status == "open":
            message = "strategyRuntime.virtualLimitOrderOpened"
        else:
            message = "strategyRuntime.virtualFillCompleted"
    else:
        first_error = next(
            (
                f"{channel}:{(results.get(channel) or {}).get('error')}"
                for channel in attempted
                if (results.get(channel) or {}).get("error")
            ),
            "",
        )
        note = f"{virtual_note};notify_failed={first_error or 'no_channel'}"
        level = "warning"
        if deferred_reason == "market_closed":
            message = "strategyRuntime.virtualOrderDeferredMarketClosedNotificationFailed"
        elif virtual_status == "open":
            message = "strategyRuntime.virtualLimitOrderOpenedNotificationFailed"
        else:
            message = "strategyRuntime.virtualFillNotificationFailed"

    worker._mark_sent(
        order_id=order_id,
        note=note[:200],
        filled=float(virtual_fill.get("fill_quantity") or 0.0),
        avg_price=float(virtual_fill.get("fill_price") or 0.0),
        executed_at=int(time.time()) if virtual_status == "filled" else None,
        final_filled=virtual_status == "filled",
        preserve_intent_terminal=True,
    )
    append_log(int(strategy_id or 0), level, message)
