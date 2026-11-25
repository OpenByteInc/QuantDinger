"""Shared order-type and protection helpers for broker execution paths."""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, Tuple

from app.services.live_trading.base import LiveTradingError
from app.services.market_schedule import require_equity_order_session
from app.services.pending_orders.order_retry import submit_with_safe_retry


def submit_broker_order(
    client: Any,
    *,
    order_type: str,
    limit_price: float,
    bracket: bool = False,
    protection: Dict[str, Any] | None = None,
    on_retry: Callable[[int, float, str], None] | None = None,
    session_market: str,
    queued_at: Any = None,
    **order: Any,
) -> Any:
    """Keep broker order routing and stable-id rejection retries in one place."""
    protection = dict(protection or {})

    def submit():
        require_equity_order_session(session_market, queued_at=queued_at)
        if bracket:
            return client.place_bracket_order(
                **order, **protection,
                limit_price=limit_price if order_type == "limit" else 0.0,
            )
        if order_type == "limit":
            return client.place_limit_order(**order, **protection, price=limit_price)
        return client.place_market_order(**order, **protection)

    return submit_with_safe_retry(
        submit,
        result_error=lambda value: "" if value.success else value.message,
        on_retry=on_retry,
    )


def broker_order_type(payload: Dict[str, Any], ref_price: float) -> Tuple[str, float]:
    order_type = str(payload.get("order_type") or "market").strip().lower()
    if order_type == "maker_then_market":
        raise LiveTradingError("maker_then_market is not supported by broker execution")
    if order_type not in ("market", "limit"):
        raise LiveTradingError(f"unsupported_broker_order_type:{order_type}")
    limit_price = float(payload.get("limit_price") or 0.0)
    if order_type == "limit" and limit_price <= 0:
        limit_price = float(ref_price or 0.0)
    if order_type == "limit" and limit_price <= 0:
        raise LiveTradingError("broker_limit_price_required")
    return order_type, limit_price


def broker_protection_prices(
    payload: Dict[str, Any],
    *,
    signal_type: str,
    entry_price: float,
) -> Tuple[float, float]:
    sig = str(signal_type or "").strip().lower()
    if sig not in ("open_long", "add_long", "open_short", "add_short") or entry_price <= 0:
        return 0.0, 0.0
    from app.services.live_trading.native_protection import protection_prices_from_payload

    stop, take, _trailing, _activation = protection_prices_from_payload(
        payload,
        entry_price=float(entry_price),
        pos_side="short" if "short" in sig else "long",
    )
    return float(stop or 0.0), float(take or 0.0)


def native_stock_protection_allowed(
    payload: Dict[str, Any],
    exchange_config: Dict[str, Any],
    quantity: float,
    stop_price: float,
    take_price: float,
) -> bool:
    """Keep broker brackets opt-in and limited to whole-share orders."""
    enabled = payload.get("native_stock_protection")
    if enabled is None:
        enabled = exchange_config.get("native_stock_protection")
    enabled = (
        enabled
        if isinstance(enabled, bool)
        else str(enabled or "").strip().lower() in {"1", "true", "yes", "on"}
    )
    value = abs(float(quantity or 0.0))
    return bool(
        enabled
        and (stop_price > 0 or take_price > 0)
        and value > 0
        and abs(value - round(value)) <= 1e-9
    )


def redact_exchange_json(value: str) -> str:
    from app.services.live_trading.partner_attribution import redact_partner_attribution

    text_value = str(value or "")
    try:
        parsed = json.loads(text_value or "{}")
    except Exception:
        return str(redact_partner_attribution(text_value))
    return json.dumps(redact_partner_attribution(parsed), ensure_ascii=False)
