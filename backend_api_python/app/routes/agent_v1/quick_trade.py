"""Trading (class T) — paper-only by default, hard-gated for live execution.

Live execution from agents requires *all* of the following:
  1. Token has scope `T`.
  2. Token has `paper_only=false` (operator must flip explicitly).
  3. Global live execution is enabled with `LIVE_TRADING_ENABLED=true`.
  4. Agent live execution is enabled with `AGENT_LIVE_TRADING_ENABLED=true`.

Until live is unlocked, this endpoint records orders to `qd_agent_paper_orders`
using the latest market price as the simulated fill — so AI workflows can
exercise the round trip without ever touching exchange credentials.
"""
from __future__ import annotations

import os
import time
import uuid
from typing import Any

from app.services.kline import KlineService
from app.services.settings.capabilities import is_live_trading_enabled
from app.utils.agent_auth import (
    SCOPE_T, agent_required, current_token, current_user_id,
    instrument_allowed, market_allowed, paper_only, with_idempotency,
)
from app.utils.agent_jobs import record_completed_job
from app.utils.db import get_db_connection
from app.utils.logger import get_logger
from flask import request

from . import agent_v1_bp
from ._helpers import envelope, error, get_json_or_400

logger = get_logger(__name__)
_kline = KlineService()
_ORDER_FIELDS = {
    "market", "symbol", "side", "qty", "order_type", "limit_price",
    "credential_id", "market_type", "leverage", "margin_mode", "tp_price", "sl_price",
}


def _live_trading_kill_switch() -> bool:
    agent_enabled = os.getenv("AGENT_LIVE_TRADING_ENABLED", "false").lower() in ("1", "true", "yes")
    return is_live_trading_enabled() and agent_enabled


def _last_price(market: str, symbol: str) -> float | None:
    try:
        rows = _kline.get_kline(market=market, symbol=symbol, timeframe="1m", limit=1) or []
        if not rows:
            return None
        last = rows[-1]
        if isinstance(last, dict):
            for k in ("close", "c", "Close"):
                v = last.get(k)
                if v is not None:
                    return float(v)
        return None
    except Exception as exc:
        logger.warning(f"agent_v1 quick_trade last_price failed: {exc}")
        return None


def _paper_fill_outcome(body: dict, last_price: float | None) -> tuple[float | None, str, str]:
    if last_price is None:
        return None, "rejected", "no last price available; recorded without fill"
    order_type = str(body.get("order_type") or "market").strip().lower()
    if order_type == "market":
        return float(last_price), "filled", ""
    side = str(body.get("side") or "").strip().lower()
    limit_price = float(body.get("limit_price") or body.get("limitPrice") or 0)
    marketable = (
        side == "buy" and float(last_price) <= limit_price
    ) or (
        side == "sell" and float(last_price) >= limit_price
    )
    if marketable:
        return float(last_price), "filled", ""
    return None, "submitted", "paper limit order is waiting for its trigger price"


def _record_paper_order(*, body: dict, fill_price: float | None, status: str, note: str = "") -> dict:
    import uuid

    order_uid = uuid.uuid4().hex
    market = (body.get("market") or "").strip()
    symbol = (body.get("symbol") or "").strip()
    side = (body.get("side") or "").strip().lower()
    order_type = (body.get("order_type") or body.get("orderType") or "market").strip().lower()
    qty = float(body.get("qty") or body.get("quantity") or 0)
    limit_price = body.get("limit_price") or body.get("limitPrice")
    if limit_price is not None:
        limit_price = float(limit_price)

    fill_value = (fill_price * qty) if (fill_price is not None and qty) else None

    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            INSERT INTO qd_agent_paper_orders
              (order_uid, user_id, agent_token_id, market, symbol, side, order_type,
               qty, limit_price, fill_price, fill_value, status, note)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                order_uid, current_user_id(), int(current_token().get("id") or 0),
                market, symbol, side, order_type,
                qty, limit_price, fill_price, fill_value, status, note,
            ),
        )
        db.commit()
        cur.close()

    return {
        "order_uid": order_uid,
        "market": market,
        "symbol": symbol,
        "side": side,
        "order_type": order_type,
        "qty": qty,
        "limit_price": limit_price,
        "fill_price": fill_price,
        "fill_value": fill_value,
        "status": status,
        "paper": True,
        "note": note,
    }


def _reserve_live_notional(notional: float) -> tuple[bool, dict]:
    token_id = int(current_token().get("id") or 0)
    user_id = current_user_id()
    key = (request.headers.get("Idempotency-Key") or "").strip()
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT max_order_notional, max_daily_notional
            FROM qd_agent_tokens
            WHERE id = %s AND user_id = %s
            FOR UPDATE
            """,
            (token_id, user_id),
        )
        limits = cur.fetchone() or {}
        per_order = float(limits.get("max_order_notional") or 1000)
        daily = float(limits.get("max_daily_notional") or 5000)
        if notional > per_order:
            db.rollback()
            cur.close()
            return False, {
                "reason": "max_order_notional",
                "estimated_notional": notional,
                "limit": per_order,
            }
        cur.execute(
            """
            SELECT COALESCE(SUM(notional), 0) AS used
            FROM qd_agent_notional_reservations
            WHERE agent_token_id = %s
              AND created_at >= date_trunc('day', NOW())
              AND status IN ('reserved', 'executed')
            """,
            (token_id,),
        )
        used = float((cur.fetchone() or {}).get("used") or 0)
        if used + notional > daily:
            db.rollback()
            cur.close()
            return False, {
                "reason": "max_daily_notional",
                "estimated_notional": notional,
                "used_today": used,
                "limit": daily,
            }
        cur.execute(
            """
            INSERT INTO qd_agent_notional_reservations
              (user_id, agent_token_id, idempotency_key, notional, status)
            VALUES (%s, %s, %s, %s, 'reserved')
            ON CONFLICT (agent_token_id, idempotency_key) DO NOTHING
            """,
            (user_id, token_id, key, notional),
        )
        db.commit()
        cur.close()
    return True, {
        "estimated_notional": notional,
        "used_today_before": used,
        "max_order_notional": per_order,
        "max_daily_notional": daily,
    }


def _finish_live_notional(status: str) -> None:
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            UPDATE qd_agent_notional_reservations
            SET status = %s, updated_at = NOW()
            WHERE agent_token_id = %s AND idempotency_key = %s
            """,
            (
                status,
                int(current_token().get("id") or 0),
                (request.headers.get("Idempotency-Key") or "").strip(),
            ),
        )
        db.commit()
        cur.close()


def _place_live_order(*, body: dict, user_id: int) -> dict:
    credential_id = int(body.get("credential_id") or body.get("credentialId") or 0)
    market = (body.get("market") or "").strip()
    symbol = (body.get("symbol") or "").strip()
    side = (body.get("side") or "").strip().lower()
    order_type = (body.get("order_type") or body.get("orderType") or "market").strip().lower()
    qty = float(body.get("qty") or body.get("quantity") or body.get("amount") or 0)
    limit_price = body.get("limit_price") or body.get("limitPrice") or body.get("price")
    limit_price_f = float(limit_price or 0)
    market_type = (body.get("market_type") or body.get("marketType") or "").strip().lower()
    leverage = int(body.get("leverage") or 1)
    margin_mode = (body.get("margin_mode") or body.get("marginMode") or "").strip().lower()
    if market_type in ("futures", "future", "perp", "perpetual"):
        market_type = "swap"
    if market_type not in ("spot", "swap"):
        market_type = "swap" if leverage > 1 else "spot"
    if market_type == "swap" and not margin_mode:
        margin_mode = "cross"
    if order_type == "limit" and limit_price_f <= 0:
        raise ValueError("limit_price is required for limit orders")
    if not credential_id:
        raise ValueError("credential_id is required for live agent trading")

    from app.routes.quick_trade import _record_quick_trade, _reject_quick_trade_if_desktop_broker
    from app.services.quick_trade.credentials import build_exchange_config, create_exchange_client
    from app.services.quick_trade.orders import (
        attach_quick_trade_protection,
        enrich_fill,
        limit_order_kwargs,
        quick_order_status,
    )

    cfg_overrides: dict[str, Any] = {"market_type": market_type}
    if margin_mode in ("cross", "crossed"):
        cfg_overrides["margin_mode"] = "cross"
        cfg_overrides["td_mode"] = "cross"
    elif margin_mode in ("iso", "isolated"):
        cfg_overrides["margin_mode"] = "isolated"
        cfg_overrides["td_mode"] = "isolated"

    exchange_config = build_exchange_config(credential_id, user_id, cfg_overrides)
    exchange_id = (exchange_config.get("exchange_id") or "").strip().lower()
    if not exchange_id:
        raise ValueError("Invalid credential: missing exchange_id")
    reject = _reject_quick_trade_if_desktop_broker(exchange_id)
    if reject is not None:
        raise ValueError("Quick Trade currently supports crypto exchange API keys only.")

    client = create_exchange_client(exchange_config, market_type=market_type)

    if market_type == "swap":
        from app.services.live_trading.account_configuration import configure_derivatives_account

        configure_derivatives_account(
            client,
            exchange_id=exchange_id,
            symbol=symbol,
            leverage=leverage,
            margin_mode=margin_mode,
        )

    client_order_id = f"qa{str(int(time.time()))[-6:]}{uuid.uuid4().hex[:8]}"
    if order_type == "market":
        from app.services.live_trading.execution import place_order_from_signal

        if market_type == "spot":
            signal_type = "open_long" if side == "buy" else "close_long"
        else:
            signal_type = "open_long" if side == "buy" else "open_short"
        result = place_order_from_signal(
            client=client,
            signal_type=signal_type,
            symbol=symbol,
            amount=qty,
            market_type=market_type,
            exchange_config=exchange_config,
            client_order_id=client_order_id,
        )
    else:
        result = client.place_limit_order(
            symbol=symbol,
            side=side.upper() if "binance" in exchange_id else side,
            **limit_order_kwargs(client, symbol, qty, limit_price_f, side, market_type, client_order_id),
        )

    exchange_order_id = str(getattr(result, "exchange_order_id", "") or "")
    filled = float(getattr(result, "filled", 0) or 0)
    avg_fill = float(getattr(result, "avg_price", 0) or 0)
    raw = getattr(result, "raw", {}) or {}
    commission = 0.0
    commission_ccy = ""
    commission_quote = None
    exchange_status = ""
    enrich = {"fee_status": "pending", "fees_by_ccy": {}}
    if exchange_order_id:
        enrich = enrich_fill(
            client,
            order_id=exchange_order_id,
            symbol=symbol,
            market_type=market_type,
            max_wait_sec=0.25,
            initial_order=raw,
        )
        if enrich.get("filled", 0.0) > 0:
            filled = float(enrich["filled"])
        if enrich.get("avg_price", 0.0) > 0:
            avg_fill = float(enrich["avg_price"])
        commission = float(enrich.get("fee") or 0.0)
        commission_ccy = str(enrich.get("fee_ccy") or "")
        exchange_status = str(enrich.get("status") or "")
        from app.services.quick_trade.fee_reconciliation import fee_update_from_fill
        fee_update = fee_update_from_fill(client, symbol, enrich, fallback_price=avg_fill)
        if fee_update:
            commission = fee_update["commission"]
            commission_ccy = fee_update["commission_ccy"]
            commission_quote = fee_update["commission_quote"]
        else:
            from app.services.live_trading.fee_quote import fee_to_quote
            commission_quote = fee_to_quote(
                client,
                symbol=symbol,
                fee=commission,
                fee_ccy=commission_ccy,
                fill_price=avg_fill,
            )

    tp_price = float(body.get("tp_price") or body.get("tpPrice") or 0)
    sl_price = float(body.get("sl_price") or body.get("slPrice") or 0)
    protection_result = []
    protection_error = ""
    try:
        protection_result = attach_quick_trade_protection(
            client,
            symbol=symbol,
            side=side,
            filled_qty=filled,
            avg_price=avg_fill,
            tp_price=tp_price,
            sl_price=sl_price,
            market_type=market_type,
            exchange_config=exchange_config,
            leverage=leverage,
            margin_mode=margin_mode,
            client_order_id=f"{client_order_id}p",
        )
    except Exception as protection_exc:
        protection_error = str(protection_exc)

    status = quick_order_status(
        requested_qty=qty,
        filled_qty=filled,
        exchange_status=exchange_status,
    )
    raw_record = dict(raw) if isinstance(raw, dict) else {"raw": raw}
    raw_record["_quick_trade"] = {
        "client_order_id": client_order_id,
        "requested_base_qty": qty,
        "exchange_status": exchange_status,
        "native_protection": protection_result,
        "native_protection_error": protection_error,
        "protected_filled_qty": filled if protection_result else 0.0,
        "margin_mode": margin_mode,
        "fees_by_ccy": dict(enrich.get("fees_by_ccy") or {}),
        "fee_status": str(enrich.get("fee_status") or "pending"),
    }

    trade_id = _record_quick_trade(
        user_id=user_id,
        credential_id=credential_id,
        exchange_id=exchange_id,
        symbol=symbol,
        side=side,
        order_type=order_type,
        amount=qty,
        price=limit_price_f if order_type == "limit" else avg_fill,
        leverage=leverage,
        market_type=market_type,
        tp_price=tp_price,
        sl_price=sl_price,
        status=status,
        exchange_order_id=exchange_order_id,
        filled=filled,
        avg_price=avg_fill,
        error_msg="",
        source="agent_mcp",
        raw_result=raw_record,
        commission=commission,
        commission_ccy=commission_ccy,
        commission_quote=commission_quote,
    )

    return {
        "trade_id": trade_id,
        "exchange_order_id": exchange_order_id,
        "market": market,
        "symbol": symbol,
        "side": side,
        "order_type": order_type,
        "qty": qty,
        "limit_price": limit_price_f if order_type == "limit" else None,
        "filled": filled,
        "avg_price": avg_fill,
        "status": status,
        "protection_status": "failed" if protection_error else ("attached" if protection_result else "not_requested"),
        "protection_error": protection_error,
        "paper": False,
    }


@agent_v1_bp.route("/quick-trade/orders", methods=["POST"])
@agent_required(SCOPE_T)
def place_order():
    """Place an order. Paper-only unless explicitly unlocked (see module doc)."""
    body, err = get_json_or_400()
    if err:
        return err
    unsupported = sorted(set(body) - _ORDER_FIELDS)
    if unsupported:
        return error(400, f"Unsupported order fields: {', '.join(unsupported)}")

    market = (body.get("market") or "").strip()
    symbol = (body.get("symbol") or "").strip()
    side = (body.get("side") or "").strip().lower()
    qty = body.get("qty")
    order_type = str(body.get("order_type") or "market").strip().lower()

    if not market or not symbol:
        return error(400, "market and symbol are required")
    if side not in ("buy", "sell"):
        return error(400, "side must be 'buy' or 'sell'")
    try:
        qty_f = float(qty)
        if qty_f <= 0:
            raise ValueError
    except Exception:
        return error(400, "qty must be a positive number")
    if order_type not in {"market", "limit"}:
        return error(400, "order_type must be 'market' or 'limit'")
    if order_type == "limit":
        try:
            if float(body.get("limit_price") or 0) <= 0:
                raise ValueError
        except (TypeError, ValueError):
            return error(400, "limit_price is required for limit orders")

    body = dict(body)
    body["qty"] = qty_f
    body["order_type"] = order_type

    if not market_allowed(market):
        return error(403, f"Market not allowed: {market}", http=403)
    if not instrument_allowed(symbol):
        return error(403, f"Instrument not allowed: {symbol}", http=403)

    with with_idempotency("quick_trade_order") as existing:
        if existing:
            return envelope({
                "duplicate": True,
                "previous": existing.get("result"),
            }, message="idempotent replay")

    # Live trading is hard-gated. Even with paper_only=false on the token, both
    # deployment switches must allow live execution before exchange clients are used.
    if not paper_only() and not _live_trading_kill_switch():
        return error(
            501,
            "Live agent trading requires LIVE_TRADING_ENABLED and AGENT_LIVE_TRADING_ENABLED",
            http=501,
        )

    if not paper_only():
        reference_price = float(body.get("limit_price") or 0) if order_type == "limit" else float(
            _last_price(market, symbol) or 0
        )
        if reference_price <= 0:
            return error(400, "A current market price is required to enforce live notional limits")
        notional = qty_f * reference_price
        reserved, limit_state = _reserve_live_notional(notional)
        if not reserved:
            return error(
                403,
                "Live agent trading notional limit exceeded",
                details=limit_state,
                http=403,
            )
        try:
            result = _place_live_order(body=body, user_id=current_user_id())
        except ValueError as exc:
            _finish_live_notional("failed")
            return error(400, str(exc), http=400)
        except Exception as exc:
            _finish_live_notional("failed")
            logger.error(f"agent_v1 live quick_trade failed: {exc}", exc_info=True)
            return error(500, "live quick_trade failed", details=str(exc), http=500)
        _finish_live_notional("executed")
        result["notional_policy"] = limit_state
        record_completed_job(
            user_id=current_user_id(),
            agent_token_id=int(current_token().get("id") or 0),
            kind="quick_trade_order",
            request_payload=body,
            result=result,
            idempotency_key=request.headers.get("Idempotency-Key"),
        )
        return envelope(result, message="live-order")

    fill_price, status, note = _paper_fill_outcome(body, _last_price(market, symbol))
    result = _record_paper_order(body=body, fill_price=fill_price, status=status, note=note)
    record_completed_job(
        user_id=current_user_id(),
        agent_token_id=int(current_token().get("id") or 0),
        kind="quick_trade_order",
        request_payload=body,
        result=result,
        idempotency_key=request.headers.get("Idempotency-Key"),
    )
    return envelope(result, message="paper-fill")


@agent_v1_bp.route("/quick-trade/kill-switch", methods=["POST"])
@agent_required(SCOPE_T)
def kill_switch():
    """Best-effort cancel agent orders and revoke every tenant T token."""
    body = request.get_json(silent=True) or {}
    if body.get("confirm") is not True:
        return error(400, "confirm=true is required for the emergency kill switch")
    user_id = current_user_id()
    live_cancelled = 0
    live_failures: list[dict] = []
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT id, credential_id, symbol, market_type, exchange_order_id, raw_result
            FROM qd_quick_trades
            WHERE user_id = %s AND source = 'agent_mcp'
              AND status IN ('submitted', 'partial', 'partially_filled')
              AND COALESCE(exchange_order_id, '') <> ''
            ORDER BY id DESC
            """,
            (user_id,),
        )
        live_rows = cur.fetchall() or []
        cur.close()

    for row in live_rows:
        try:
            from app.services.pending_orders.live_order_phases import cancel_live_limit_order
            from app.services.quick_trade.credentials import build_exchange_config, create_exchange_client

            market_type = str(row.get("market_type") or "swap")
            config = build_exchange_config(int(row.get("credential_id") or 0), user_id, {
                "market_type": market_type,
            })
            client = create_exchange_client(config, market_type=market_type)
            raw = row.get("raw_result") or {}
            if not isinstance(raw, dict):
                raw = {}
            metadata = raw.get("_quick_trade") or {}
            outcome = cancel_live_limit_order(
                client=client,
                symbol=str(row.get("symbol") or ""),
                order_id=str(row.get("exchange_order_id") or ""),
                client_order_id=str(metadata.get("client_order_id") or ""),
                market_type=market_type,
                exchange_config=config,
            )
            if outcome is None:
                raise ValueError("exchange client does not support cancellation")
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    "UPDATE qd_quick_trades SET status = 'cancelled' WHERE id = %s AND user_id = %s",
                    (row["id"], user_id),
                )
                db.commit()
                cur.close()
            live_cancelled += 1
        except Exception as exc:
            live_failures.append({
                "trade_id": row.get("id"),
                "exchange_order_id": row.get("exchange_order_id"),
                "error": str(exc)[:300],
            })

    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            UPDATE qd_agent_paper_orders
            SET status = 'cancelled', note = COALESCE(note,'') || ' [kill_switch]'
            WHERE user_id = %s AND status NOT IN ('filled','cancelled','rejected')
            """,
            (user_id,),
        )
        paper_affected = cur.rowcount
        cur.execute(
            """
            UPDATE qd_agent_tokens
            SET status = 'revoked'
            WHERE user_id = %s AND status = 'active'
              AND (',' || UPPER(scopes) || ',') LIKE '%%,T,%%'
            """,
            (user_id,),
        )
        revoked = cur.rowcount
        db.commit()
        cur.close()
    return envelope({
        "cancelled_open_paper_orders": int(paper_affected or 0),
        "cancelled_live_agent_orders": live_cancelled,
        "live_cancel_failures": live_failures,
        "revoked_t_tokens": int(revoked or 0),
        "manual_review_required": bool(live_failures),
    }, message="emergency-stop")
