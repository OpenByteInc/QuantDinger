"""
Quick Trade API - manual / discretionary order placement.

Allows users to place market or limit orders directly from AI analysis
or indicator analysis pages, without creating a strategy first.

Endpoints:
  POST /api/quick-trade/place-order      - Place a quick order
  POST /api/quick-trade/cancel-order     - Cancel an active limit order
  POST /api/quick-trade/close-position   - Close an existing position
  GET  /api/quick-trade/balance          - Get available balance
  GET  /api/quick-trade/position         - Get current position for symbol
  GET  /api/quick-trade/history          - Get quick trade history
  GET  /api/quick-trade/order-book       - Get a public crypto order-book snapshot
"""

from __future__ import annotations

import json
import math
import time
import traceback
import uuid
from typing import Any, Dict, List, Optional

from flask import g, jsonify, request
from app.openapi.blueprint import HumanBlueprint as Blueprint
from app.openapi.schemas.high_risk import (
    QuickTradeCancelRequestSchema,
    QuickTradeCloseRequestSchema,
    QuickTradeOrderRequestSchema,
    QuickTradeProtectionRequestSchema,
)

from app.utils.db import get_db_connection
from app.utils.logger import get_logger
from app.utils.auth import login_required
from app.utils.timeutil import to_utc_iso
from app.services.quick_trade.balances import (
    empty_balance_dict,
    fetch_balance_raw,
    parse_balance as _parse_balance,  # noqa: F401 - compatibility export
)
from app.services.quick_trade.credentials import build_exchange_config, create_exchange_client
from app.services.quick_trade.errors import (
    exchange_error_user_message,
    merge_balance_leg_errors,
    parse_trade_error_hint,
)
from app.services.quick_trade.orders import (
    attach_quick_trade_protection,
    enrich_fill,
    limit_order_kwargs,
    quick_trade_protection_status,
    quick_order_status,
)
from app.services.quick_trade.symbols import is_supported_crypto_exchange, symbols_match as quick_trade_symbols_match
from app.services.quick_trade.history import parse_quick_trade_metadata, history_order_status
from app.services.quick_trade.trade_history import resolve_trade_history
from app.services.quick_trade.fee_reconciliation import fee_update_from_fill, reconcile_quick_trade_fees
from app.services.quick_trade.order_reconciliation import reconcile_quick_trade_orders
from app.services.quick_trade.account_warmup import configure_quick_trade_derivatives, warm_quick_trade_account
from app.services.quick_trade.products import (
    build_product_aware_config as _build_product_aware_config,
)
from app.services.quick_trade.positions import (
    infer_position_side_from_row as _infer_position_side_from_row,
    normalize_okx_positions_raw as _normalize_okx_positions_raw,
    parse_positions as _parse_positions,
)
from app.services.quick_trade.strategy_conflicts import cached_recent_strategy_activity as _recent_strategy_conflict
from app.services.quick_trade.sizing import (
    convert_usdt_to_base_qty as _convert_usdt_to_base_qty,
    resolve_order_notional_usdt as _resolve_order_notional_usdt,
)
from app.services.quick_trade.external_closes import sync_exchange_close_fills as _sync_exchange_close_fills
from app.services.quick_trade.transport import close_quick_trade_transport, open_quick_trade_transport
from app.services.quick_trade.order_safety import (
    matching_position_size as _matching_position_size,
    normalized_submission_quantity,
    serialized_quick_trade_close, serialized_quick_trade_order,
    verify_full_market_close,
)
from app.services.quick_trade.orderbook import OrderBookError, fetch_order_book
from app.services.pending_orders.order_quantities import exchange_quantity_snapshot
from app.services.quick_trade.spot_positions import (
    enrich_spot_positions as _enrich_spot_positions_impl,
    quick_trade_spot_avg_entry_price as _quick_trade_spot_avg_entry_price,
)
from app.utils.request_guard import RequestGuardError, cache_key, guarded_cached

logger = get_logger(__name__)

quick_trade_blp = Blueprint('quick_trade', __name__)
quick_trade_blp.before_request(open_quick_trade_transport)
quick_trade_blp.teardown_request(close_quick_trade_transport)


@quick_trade_blp.route('/order-book', methods=['GET'])
@login_required
def get_order_book():
    """Return a normalized public order-book snapshot for a supported crypto venue."""
    exchange_id = str(request.args.get('exchange_id') or '').strip().lower()
    symbol = str(request.args.get('symbol') or '').strip()
    market_type = str(request.args.get('market_type') or 'swap').strip().lower()
    try:
        limit = int(request.args.get('limit') or 25)
    except (TypeError, ValueError):
        limit = 25
    if not exchange_id or not symbol:
        return jsonify({"code": 0, "msg": "quickTrade.selectSymbol"}), 400
    if not is_supported_crypto_exchange(exchange_id):
        return jsonify({"code": 0, "msg": "quickTrade.unsupportedMarket"}), 400
    try:
        snapshot = fetch_order_book(
            g.quick_trade_session,
            exchange_id=exchange_id,
            symbol=symbol,
            market_type=market_type,
            limit=limit,
        )
        return jsonify({"code": 1, "msg": "success", "data": snapshot})
    except (OrderBookError, ValueError):
        return jsonify({"code": 0, "msg": "quickTrade.orderFailed"}), 400
    except Exception as exc:
        logger.warning("quick-trade order book failed exchange=%s symbol=%s: %s", exchange_id, symbol, exc)
        return jsonify({"code": 0, "msg": "quickTrade.errorHints.networkError"}), 502

def _reject_quick_trade_if_desktop_broker(exchange_id: str):
    """Quick Trade is USDT-centric and only wired to crypto exchange clients."""
    if not is_supported_crypto_exchange(exchange_id):
        return jsonify(
            {
                "code": 0,
                "msg": "Quick Trade currently supports crypto exchange API keys only.",
            }
        ), 400
    return None


def _record_quick_trade(
    user_id: int,
    credential_id: int,
    exchange_id: str,
    symbol: str,
    side: str,
    order_type: str,
    amount: float,
    price: float,
    leverage: int,
    market_type: str,
    tp_price: float,
    sl_price: float,
    status: str,
    exchange_order_id: str,
    filled: float,
    avg_price: float,
    error_msg: str,
    source: str,
    raw_result: Dict[str, Any],
    commission: float = 0.0,
    commission_ccy: str = "",
    commission_quote: Optional[float] = None,
    client_order_id: str = "",
):
    """Insert a quick trade record into the database."""
    from app.services.live_trading.partner_attribution import redact_partner_attribution

    raw_result = redact_partner_attribution(raw_result or {})
    try:
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                INSERT INTO qd_quick_trades
                    (user_id, credential_id, exchange_id, symbol, side, order_type,
                     amount, price, leverage, market_type, tp_price, sl_price,
                     status, exchange_order_id, filled_amount, avg_fill_price,
                     commission, commission_ccy, commission_quote,
                     client_order_id, error_msg, source, raw_result, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                RETURNING id
                """,
                (
                    user_id, credential_id, exchange_id, symbol, side, order_type,
                    amount, price, leverage, market_type, tp_price, sl_price,
                    status, exchange_order_id, filled, avg_price,
                    float(commission or 0.0), str(commission_ccy or "").strip().upper(),
                    float(commission_quote) if commission_quote is not None else None,
                    str(client_order_id or "")[:100], error_msg, source, json.dumps(raw_result or {}),
                ),
            )
            row = cur.fetchone()
            db.commit()
            cur.close()
            trade_id = int((row or {}).get("id") or 0)
            if trade_id > 0 and exchange_order_id:
                from app.services.execution_streams.repository import ExecutionEventRepository

                ExecutionEventRepository().register_binding(
                    credential_id=int(credential_id or 0),
                    exchange_id=str(exchange_id or ""),
                    market_type=str(market_type or "swap"),
                    owner_type="quick_trade",
                    owner_id=trade_id,
                    user_id=int(user_id or 1),
                    symbol=str(symbol or ""),
                    signal_type=str(side or ""),
                    client_order_id=str(client_order_id or ""),
                    exchange_order_id=str(exchange_order_id or ""),
                    observed_filled=float(filled or 0.0),
                )
            return trade_id
    except Exception as e:
        logger.error(f"Failed to record quick trade: {e}")
        return None


# ---------- endpoints ----------
@quick_trade_blp.route('/place-order', methods=['POST'])
@login_required
@quick_trade_blp.arguments(QuickTradeOrderRequestSchema, location="json")
@serialized_quick_trade_order
def place_order(body):
    """
    Place a quick market or limit order.

    Body JSON:
      credential_id  (int)    - saved exchange credential ID
      symbol         (str)    - e.g. "BTC/USDT"
      side           (str)    - "buy" or "sell"
      order_type     (str)    - "market" or "limit"  (default: market)
      amount         (float)  - spot quote amount or swap margin amount in USDT
      quantity       (float)  - optional exact base quantity for spot sell orders
      price          (float)  - limit price (required for limit orders)
      leverage       (int)    - leverage multiplier (default: 1)
                                - leverage = 1: spot market
                                - leverage > 1: swap (perpetual futures) market
      market_type    (str)    - "swap" / "spot" (optional, auto-determined by leverage if not provided)
      tp_price       (float)  - exchange-native take-profit price for swaps
      sl_price       (float)  - exchange-native stop-loss price for swaps
      source         (str)    - "ai_radar" / "ai_analysis" / "indicator" / "manual"
    """
    exchange_id = ""
    request_started = time.monotonic()
    try:
        user_id = g.user_id
        credential_id = int(body.get("credential_id") or 0)
        symbol = str(body.get("symbol") or "").strip()
        side = str(body.get("side") or "").strip().lower()
        order_type = str(body.get("order_type") or "market").strip().lower()
        usdt_amount = float(body.get("amount") or 0)
        requested_base_quantity = float(body.get("quantity") or 0)
        price = float(body.get("price") or 0)
        reference_price = float(body.get("reference_price") or 0)
        reference_price_at_ms = int(body.get("reference_price_at_ms") or 0)
        leverage = int(body.get("leverage") or 1)
        market_type = str(body.get("market_type") or "").strip().lower()
        tp_price = float(body.get("tp_price") or 0)
        sl_price = float(body.get("sl_price") or 0)
        source = str(body.get("source") or "manual").strip()
        margin_mode = str(body.get("margin_mode") or body.get("marginMode") or "").strip().lower()
        if margin_mode in ("cross", "crossed"):
            margin_mode = "cross"
        elif margin_mode in ("iso", "isolated"):
            margin_mode = "isolated"
        else:
            margin_mode = ""

        # ---- validation ----
        if not credential_id:
            return jsonify({"code": 0, "msg": "Missing credential_id"}), 400
        if not symbol:
            return jsonify({"code": 0, "msg": "Missing symbol"}), 400
        if side not in ("buy", "sell"):
            return jsonify({"code": 0, "msg": "side must be 'buy' or 'sell'"}), 400
        if order_type == "limit" and price <= 0:
            return jsonify({"code": 0, "msg": "price required for limit orders"}), 400

        # Preserve an explicit 1x perpetual selection. Infer only when omitted.
        if market_type in ("futures", "future", "perp", "perpetual"):
            market_type = "swap"
        if market_type not in ("spot", "swap"):
            market_type = "swap" if leverage > 1 else "spot"
        if market_type == "swap" and not margin_mode:
            margin_mode = "cross"
        uses_base_quantity = market_type == "spot" and requested_base_quantity > 0
        if usdt_amount > 0 and requested_base_quantity > 0:
            return jsonify({"code": 0, "msg": "amount and quantity are mutually exclusive"}), 400
        if usdt_amount <= 0 and not uses_base_quantity:
            return jsonify({"code": 0, "msg": "amount must be > 0"}), 400
        if requested_base_quantity > 0 and not uses_base_quantity:
            return jsonify({"code": 0, "msg": "quantity is only supported for spot orders"}), 400
        order_notional_usdt = _resolve_order_notional_usdt(usdt_amount, leverage, market_type)

        # ---- build exchange client ----
        cfg_overrides: Dict[str, Any] = {"market_type": market_type}
        if margin_mode in ("cross", "isolated"):
            cfg_overrides["margin_mode"] = margin_mode
            cfg_overrides["td_mode"] = margin_mode
        exchange_config, resolved_product = _build_product_aware_config(
            credential_id,
            user_id,
            symbol=symbol,
            market_type=market_type,
            source=body,
            overrides=cfg_overrides,
        )
        exchange_id = (exchange_config.get("exchange_id") or "").strip().lower()
        if not exchange_id:
            return jsonify({"code": 0, "msg": "Invalid credential: missing exchange_id"}), 400

        qt_rej = _reject_quick_trade_if_desktop_broker(exchange_id)
        if qt_rej is not None:
            return qt_rej
        client = create_exchange_client(exchange_config, market_type=market_type)

        strategy_conflict = _recent_strategy_conflict(
            client,
            user_id=user_id,
            credential_id=credential_id,
            symbol=symbol,
            market_type=market_type,
        )
        if strategy_conflict:
            return jsonify({
                "code": 0,
                "msg": "quickTrade.workflow.strategyConflict",
                "error_hint": "quickTrade.workflow.strategyConflict",
                "data": {"strategy_conflict": strategy_conflict},
            }), 409

        if market_type == "swap":
            configure_quick_trade_derivatives(
                client,
                user_id=user_id,
                credential_id=credential_id,
                exchange_id=exchange_id,
                symbol=symbol,
                leverage=leverage,
                margin_mode=margin_mode,
            )

        # Spot orders may use quote notional or base quantity.
        # Swap input is margin and expands by leverage.
        conversion_price = price if order_type == "limit" and price > 0 else 0.0
        if order_type == "market" and reference_price > 0 and reference_price_at_ms > 0:
            reference_age_ms = abs(int(time.time() * 1000) - reference_price_at_ms)
            if reference_age_ms <= 30_000:
                conversion_price = reference_price
        base_qty = requested_base_quantity if uses_base_quantity else _convert_usdt_to_base_qty(
            client,
            symbol,
            order_notional_usdt,
            market_type,
            conversion_price,
        )

        quote_for_buy = 0.0
        if market_type == "spot":
            from app.services.live_trading.spot_sizing import (
                fetch_spot_last_price,
                normalize_spot_base_quantity,
                normalize_spot_quote_amount,
                scale_spot_open_notional,
            )
            from app.services.live_trading.bitget_spot import BitgetSpotClient

            if order_type == "market" and side == "buy" and not uses_base_quantity:
                quote_for_buy = normalize_spot_quote_amount(
                    client,
                    symbol=symbol,
                    quote_amount=scale_spot_open_notional(usdt_amount),
                )
                if quote_for_buy <= 0:
                    return jsonify(
                        {
                            "code": 0,
                            "msg": "Order notional is below the exchange minimum. Increase the USDT amount.",
                        }
                    ), 400
                if not isinstance(client, BitgetSpotClient):
                    base_qty = normalize_spot_base_quantity(
                        client, symbol=symbol, quantity=base_qty, for_market=True
                    )
            else:
                base_qty = normalize_spot_base_quantity(
                    client, symbol=symbol, quantity=base_qty, for_market=(order_type == "market")
                )
            if base_qty <= 0 and quote_for_buy <= 0:
                px = fetch_spot_last_price(client, symbol=symbol)
                hint = f" Unable to fetch a valid {symbol} price; check the API key or symbol." if px <= 0 else ""
                return jsonify(
                    {
                        "code": 0,
                        "msg": f"Order quantity is below the exchange minimum. Increase the amount or check symbol rules.{hint}",
                    }
                    ), 400

            if uses_base_quantity:
                reference_price = price if order_type == "limit" else fetch_spot_last_price(client, symbol=symbol)
                if reference_price <= 0:
                    return jsonify({"code": 0, "msg": f"Unable to fetch a valid {symbol} price."}), 400
                usdt_amount = base_qty * reference_price
                order_notional_usdt = usdt_amount

            if side == "buy":
                need_quote = float(quote_for_buy or 0) if quote_for_buy > 0 else float(usdt_amount or 0)
                if need_quote > 0:
                    try:
                        bal = fetch_balance_raw(
                            client,
                            exchange_id=exchange_id,
                            market_type="spot",
                            exchange_config=exchange_config,
                        )
                        avail = float(bal.get("available") or 0)
                        if need_quote > avail + 1e-6:
                            return jsonify(
                                {
                                    "code": 0,
                                    "msg": (
                                        f"Insufficient spot USDT balance: need about {need_quote:.4f} USDT, "
                                        f"available {avail:.4f} USDT."
                                    ),
                                    "error_hint": "quickTrade.errorHints.insufficientBalance",
                                }
                            ), 400
                    except Exception as be:
                        logger.warning("spot buy balance pre-check skipped: %s", be)
        
        # Validate conversion: equal values usually mean the price lookup fallback was used.
        if market_type != "spot" and base_qty == order_notional_usdt and order_notional_usdt >= 1:
            logger.error(
                "USDT conversion may have failed: base_qty (%s) equals order_notional_usdt (%s)",
                base_qty,
                order_notional_usdt,
            )
            logger.error(f"This suggests the price fetch failed. Order may fail due to insufficient margin.")

        executable_base_qty, quantity_snapshot = normalized_submission_quantity(
            client,
            exchange_id=exchange_id,
            symbol=symbol,
            market_type=market_type,
            requested=base_qty,
            exchange_config=exchange_config,
            source="quick_trade_submission",
            snapshotter=exchange_quantity_snapshot,
        )
        status_base_qty = executable_base_qty
        status_quantity_mode = (
            "exchange_status"
            if market_type == "spot"
            and order_type == "market"
            and side == "buy"
            and quote_for_buy > 0
            and not uses_base_quantity
            else "base_quantity"
        )
        if status_quantity_mode == "exchange_status":
            status_base_qty = 0.0

        timestamp_suffix = str(int(time.time()))[-6:]  # Last 6 digits of timestamp
        uuid_suffix = uuid.uuid4().hex[:8]  # 8 hex chars
        client_order_id = f"qt{timestamp_suffix}{uuid_suffix}"  # Total: 2 + 6 + 8 = 16 chars

        result = None
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
                amount=base_qty,  # Use converted base qty
                market_type=market_type,
                exchange_config=exchange_config,
                client_order_id=client_order_id,
                quote_amount=quote_for_buy,
                spot_buy_by_quantity=bool(side == "buy" and uses_base_quantity),
            )
            logger.info(
                "Quick Trade order acknowledged exchange=%s elapsed_ms=%.1f",
                exchange_id,
                (time.monotonic() - request_started) * 1000,
            )
        else:
            # Limit orders: use direct client call (execution.py doesn't handle limit orders)
            result = client.place_limit_order(
                symbol=symbol,
                side=side.upper() if "binance" in exchange_id else side,
                **limit_order_kwargs(client, symbol, base_qty, price, side, market_type, client_order_id),
            )

        # ---- extract result ----
        exchange_order_id = str(getattr(result, "exchange_order_id", "") or "")
        filled = float(getattr(result, "filled", 0) or 0)
        avg_fill = float(getattr(result, "avg_price", 0) or 0)
        raw = getattr(result, "raw", {}) or {}

        # ---- bounded fill enrichment ----
        # Use terminal ACK details immediately when available. Fee reconciliation
        # remains asynchronous so a secondary trade-history lookup never delays
        # the order acknowledgement shown to the user.
        commission = 0.0
        commission_ccy = ""
        commission_quote = None
        exchange_status = ""
        enrich = {"fee_status": "pending", "fees_by_ccy": {}}
        if exchange_order_id and (
            order_type == "limit" or tp_price > 0 or sl_price > 0
        ):
            enrich = enrich_fill(
                client,
                order_id=exchange_order_id,
                symbol=symbol,
                market_type=market_type,
                max_wait_sec=0.25,
                initial_order=raw,
                include_fees=False,
            )
            if enrich.get("filled", 0.0) > 0:
                filled = float(enrich["filled"])
            if enrich.get("avg_price", 0.0) > 0:
                avg_fill = float(enrich["avg_price"])
            commission = float(enrich.get("fee") or 0.0)
            commission_ccy = str(enrich.get("fee_ccy") or "")
            exchange_status = str(enrich.get("status") or "")
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

        protection_result: list[Dict[str, Any]] = []
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
            logger.error("Quick Trade native protection failed order=%s: %s", exchange_order_id, protection_exc)

        protection_status, protection_error = quick_trade_protection_status(
            requested=tp_price > 0 or sl_price > 0,
            market_type=market_type,
            filled_qty=filled,
            avg_price=avg_fill,
            responses=protection_result,
            error=protection_error,
        )

        status = quick_order_status(
            requested_qty=status_base_qty,
            filled_qty=filled,
            exchange_status=exchange_status,
        )
        raw_record = dict(raw) if isinstance(raw, dict) else {"raw": raw}
        raw_record["_quick_trade"] = {
            "requested_base_qty": base_qty,
            "executable_base_qty": executable_base_qty,
            "status_quantity_mode": status_quantity_mode,
            "quantity_snapshot": quantity_snapshot,
            "input_amount_usdt": usdt_amount,
            "notional_usdt": order_notional_usdt,
            "amount_semantics": "base_quantity" if uses_base_quantity else ("margin" if market_type == "swap" else "quote_notional"),
            "input_base_quantity": requested_base_quantity if uses_base_quantity else None,
            "exchange_status": exchange_status,
            "native_protection": protection_result,
            "native_protection_error": protection_error,
            "protection_status": protection_status,
            "protected_filled_qty": filled if protection_result else 0.0,
            "margin_mode": margin_mode,
            "client_order_id": client_order_id,
            "instrument_id": str((resolved_product or {}).get("instrument_id") or ""),
            "product_type": str((resolved_product or {}).get("product_type") or ""),
            "api_family": str((resolved_product or {}).get("api_family") or ""),
            "fees_by_ccy": dict(enrich.get("fees_by_ccy") or {}),
            "fee_status": str(enrich.get("fee_status") or "pending"),
        }

        # ---- record trade ----
        # Store the submitted or derived quote value while preserving base quantity in metadata.
        trade_id = _record_quick_trade(
            user_id=user_id,
            credential_id=credential_id,
            exchange_id=exchange_id,
            symbol=symbol,
            side=side,
            order_type=order_type,
            amount=usdt_amount,
            price=price if order_type == "limit" else avg_fill,
            leverage=leverage,
            market_type=market_type,
            tp_price=tp_price,
            sl_price=sl_price,
            status=status,
            exchange_order_id=exchange_order_id,
            filled=filled,
            avg_price=avg_fill,
            error_msg=protection_error,
            source=source,
            raw_result=raw_record,
            commission=commission,
            commission_ccy=commission_ccy,
            commission_quote=commission_quote,
            client_order_id=client_order_id,
        )

        return jsonify({
            "code": 1,
            "msg": "Order placed successfully",
            "data": {
                "trade_id": trade_id,
                "exchange_order_id": exchange_order_id,
                "filled": filled,
                "avg_price": avg_fill,
                "status": status,
                "margin_amount": usdt_amount if market_type == "swap" else None,
                "notional_amount": order_notional_usdt,
                "protection_status": protection_status,
                "protection_error": protection_error,
            },
        })

    except Exception as e:
        logger.error(f"quick trade failed: {e}")
        logger.error(traceback.format_exc())

        # Try to record the failure
        try:
            _record_quick_trade(
                user_id=g.user_id,
                credential_id=int(body.get("credential_id") or 0),
                exchange_id="",
                symbol=str(body.get("symbol") or ""),
                side=str(body.get("side") or ""),
                order_type=str(body.get("order_type") or "market"),
                amount=float(body.get("amount") or 0),  # Original USDT amount
                price=0,
                leverage=int(body.get("leverage") or 1),
                market_type=str(body.get("market_type") or "swap"),
                tp_price=0,
                sl_price=0,
                status="failed",
                exchange_order_id="",
                filled=0,
                avg_price=0,
                error_msg=str(e)[:500],
                source=str(body.get("source") or "manual"),
                raw_result={},
            )
        except Exception:
            pass

        err_str = str(e)
        if err_str.startswith("strategyV2."):
            return jsonify({"code": 0, "msg": err_str}), 400
        err_meta = exchange_error_user_message(exchange_id=exchange_id, err=err_str)
        resp: Dict[str, Any] = {"code": 0, "msg": err_meta.get("message") or err_str}
        if err_meta.get("hint_key"):
            resp["error_hint"] = err_meta["hint_key"]
        return jsonify(resp), 500


@quick_trade_blp.route('/balance', methods=['GET'])
@login_required
def get_balance():
    """
    Get available balance from exchange.

    Query: credential_id (int), market_type (str, default "swap") - active leg for ``available``/``total``.

    Response also includes ``swap`` and ``spot`` so the UI can show both account types.
    """
    try:
        user_id = g.user_id
        credential_id = request.args.get("credential_id", type=int)
        market_type = request.args.get("market_type", "swap").strip().lower()
        if market_type in ("futures", "future", "perp", "perpetual"):
            market_type = "swap"

        if not credential_id:
            return jsonify({"code": 0, "msg": "Missing credential_id"}), 400

        base_cfg = build_exchange_config(credential_id, user_id, {})
        exchange_id = (base_cfg.get("exchange_id") or "").strip().lower()
        qt_rej = _reject_quick_trade_if_desktop_broker(exchange_id)
        if qt_rej is not None:
            return qt_rej
        product_args = {
            "symbol": request.args.get("symbol", "").strip(),
            "instrument_id": request.args.get("instrument_id", "").strip(),
            "product_type": request.args.get("product_type", "").strip(),
            "api_family": request.args.get("api_family", "").strip(),
            "resolve_product": request.args.get("resolve_product", "").strip(),
        }
        requested_leverage = max(1, request.args.get("leverage", default=1, type=int) or 1)
        requested_margin_mode = request.args.get("margin_mode", "cross").strip().lower()
        if requested_margin_mode not in ("cross", "isolated"):
            requested_margin_mode = "cross"

        def _compute_balance() -> Dict[str, Any]:
            swap_bal = empty_balance_dict()
            spot_bal = empty_balance_dict()

            for mt in ("swap", "spot"):
                try:
                    if mt == market_type and product_args["symbol"]:
                        cfg, _ = _build_product_aware_config(
                            credential_id,
                            user_id,
                            symbol=product_args["symbol"],
                            market_type=mt,
                            source=product_args,
                            overrides={"market_type": mt},
                        )
                    else:
                        cfg = build_exchange_config(credential_id, user_id, {"market_type": mt})
                    client = create_exchange_client(cfg, market_type=mt)
                    parsed = fetch_balance_raw(
                        client,
                        exchange_id=exchange_id,
                        market_type=mt,
                        exchange_config=cfg,
                    )
                    if mt == market_type:
                        warm_quick_trade_account(
                            client,
                            user_id=user_id,
                            credential_id=credential_id,
                            exchange_id=exchange_id,
                            symbol=product_args["symbol"],
                            market_type=mt,
                            leverage=requested_leverage,
                            margin_mode=requested_margin_mode,
                        )
                    if mt == "spot":
                        spot_bal = parsed
                    else:
                        swap_bal = parsed
                    logger.info(
                        "Balance for %s/%s: available=%.4f total=%.4f",
                        exchange_id,
                        mt,
                        float(parsed.get("available") or 0),
                        float(parsed.get("total") or 0),
                    )
                except Exception as be:
                    logger.warning("Balance leg failed (%s/%s): %s", exchange_id, mt, be)
                    leg = empty_balance_dict()
                    leg["error"] = str(be)
                    if mt == "spot":
                        spot_bal = leg
                    else:
                        swap_bal = leg

            active = spot_bal if market_type == "spot" else swap_bal
            balance_data = {
                "available": float(active.get("available") or 0),
                "total": float(active.get("total") or 0),
                "currency": str(active.get("currency") or "USDT"),
                "market_type": market_type,
                "swap": swap_bal,
                "spot": spot_bal,
            }
            err_meta = merge_balance_leg_errors(swap_bal, spot_bal, exchange_id=exchange_id)
            if not err_meta and active.get("error"):
                err_meta = exchange_error_user_message(exchange_id=exchange_id, err=str(active.get("error")))
                if err_meta.get("message"):
                    balance_data["error"] = err_meta["message"]
                if err_meta.get("hint_key"):
                    balance_data["error_hint_key"] = err_meta["hint_key"]
                if err_meta.get("request_ip"):
                    balance_data["request_ip"] = err_meta["request_ip"]
            elif err_meta:
                balance_data.update(err_meta)
            return balance_data

        balance_data = _compute_balance() if request.args.get("refresh") == "1" else guarded_cached(
            cache_key(
                "quick_trade_balance",
                user_id,
                credential_id,
                market_type,
                product_args["symbol"],
                product_args["instrument_id"],
                product_args["product_type"],
                product_args["api_family"],
                requested_leverage,
                requested_margin_mode,
            ),
            _compute_balance,
            ttl_sec=8,
            stale_ttl_sec=90,
            timeout_sec=10,
            namespace="quick_trade_balance",
            max_concurrent=4,
        )
        return jsonify({"code": 1, "msg": "success", "data": balance_data})
    except RequestGuardError as e:
        return jsonify({"code": 0, "msg": str(e)}), e.status_code
    except Exception as e:
        logger.error(f"get_balance failed: {e}")
        return jsonify({"code": 0, "msg": str(e)}), 500


def _enrich_spot_positions(
    positions: list,
    *,
    client: Any,
    symbol: str,
    user_id: int,
    credential_id: int,
    market_type: str,
    exchange_id: str = "",
) -> list:
    return _enrich_spot_positions_impl(
        positions, client=client, symbol=symbol, user_id=user_id,
        credential_id=credential_id, market_type=market_type,
        exchange_id=exchange_id, average_cost_loader=_quick_trade_spot_avg_entry_price,
    )


def _fetch_spot_holdings_raw(client: Any, *, symbol: str) -> Dict[str, Any]:
    """
    Spot "position" = base-asset wallet balance for the trading pair.

    Returns the same ``{"data": [row, ...]}`` envelope as derivative position APIs.
    """
    from app.services.live_trading.spot_sizing import get_spot_base_holding
    from app.services.live_trading.symbols import _split_base_quote

    sym = str(symbol or "").strip()
    base, quote = _split_base_quote(sym)
    if not base:
        return {"data": []}
    display = sym if sym else f"{base}/{quote or 'USDT'}"
    holding = get_spot_base_holding(client, symbol=display)
    total = float(holding.get("total") or 0.0)
    avail = float(holding.get("available") or 0.0)
    if total <= 0 and avail <= 0:
        return {"data": []}
    qty = total if total > 0 else avail
    row: Dict[str, Any] = {
        "symbol": display,
        "bal": qty,
        "availBal": avail,
        "side": "long",
    }
    avg_cost = float(holding.get("avg_cost") or 0.0)
    if avg_cost > 0:
        row["avgCost"] = avg_cost
        row["openAvgPx"] = avg_cost
    return {"data": [row]}


def _fetch_exchange_positions_raw(
    client: Any,
    exchange_config: Dict[str, Any],
    *,
    symbol: str,
    market_type: str,
) -> Any:
    """
    Fetch raw position payload for quick-trade / close-position.

    Many clients do not accept ``symbol=`` on ``get_positions()`` (Gate),
    or need extra args (Bitget ``product_type``, OKX ``inst_type``). Centralize here.
    """
    from app.services.live_trading.binance import BinanceFuturesClient
    from app.services.live_trading.binance_spot import BinanceSpotClient
    from app.services.live_trading.bitget import BitgetMixClient
    from app.services.live_trading.bitget_spot import BitgetSpotClient
    from app.services.live_trading.bybit import BybitClient
    from app.services.live_trading.gate import GateSpotClient, GateUsdtFuturesClient
    from app.services.live_trading.htx import HtxClient
    from app.services.live_trading.okx import OkxClient
    from app.services.live_trading.symbols import (
        to_bybit_symbol,
        to_gate_currency_pair,
        to_okx_swap_inst_id,
    )

    mt = (market_type or "swap").strip().lower()

    if mt == "spot" and isinstance(
        client, (BinanceSpotClient, BitgetSpotClient, OkxClient)
    ):
        return _fetch_spot_holdings_raw(client, symbol=symbol)

    if isinstance(client, OkxClient):
        inst_id = to_okx_swap_inst_id(symbol)
        raw = client.get_positions(inst_id=inst_id, inst_type="SWAP")
        return _normalize_okx_positions_raw(raw)

    if isinstance(client, BinanceFuturesClient):
        return client.get_positions(symbol=symbol)

    if isinstance(client, BitgetMixClient):
        pt = str(exchange_config.get("product_type") or exchange_config.get("productType") or "USDT-FUTURES")
        return client.get_positions(product_type=pt, symbol=symbol)

    if isinstance(client, GateSpotClient) and mt == "spot":
        raw_accounts = client.get_accounts()
        items = raw_accounts if isinstance(raw_accounts, list) else []
        base_asset = ""
        if symbol:
            base_asset = str(symbol).split("/", 1)[0].split(":", 1)[0].strip().upper()
        rows = []
        for item in items:
            if not isinstance(item, dict):
                continue
            ccy = str(item.get("currency") or "").upper()
            if base_asset and ccy != base_asset:
                continue
            try:
                av = float(item.get("available") or item.get("available_balance") or 0)
            except Exception:
                av = 0.0
            try:
                lk = float(item.get("locked") or item.get("freeze") or 0)
            except Exception:
                lk = 0.0
            total = av + lk
            if total <= 0:
                continue
            rows.append(
                {
                    "symbol": f"{ccy}/USDT",
                    "bal": total,
                    "availBal": av,
                }
            )
        return {"data": rows}

    if isinstance(client, BybitClient):
        if mt == "spot" or getattr(client, "category", "") == "spot":
            return client.get_spot_holdings(symbol=symbol)
        # Bybit v5 requires symbol or settleCoin; query the contract directly.
        raw = client.get_positions(symbol=symbol)
        lst = (((raw or {}).get("result") or {}).get("list")) if isinstance(raw, dict) else None
        if not isinstance(lst, list):
            return raw
        sym_norm = to_bybit_symbol(symbol)
        filtered = [p for p in lst if isinstance(p, dict) and str(p.get("symbol") or "").strip() == sym_norm]
        if isinstance(raw, dict):
            out = dict(raw)
            res = dict((raw.get("result") or {}) if isinstance(raw.get("result"), dict) else {})
            res["list"] = filtered
            out["result"] = res
            return out
        return {"result": {"list": filtered}}

    if isinstance(client, GateUsdtFuturesClient):
        raw = client.get_positions()
        items = raw if isinstance(raw, list) else []
        c = to_gate_currency_pair(symbol)
        logger.info("Gate positions: total=%d, target=%s, contracts=%s",
                     len(items), c,
                     [(str(p.get("contract")), p.get("size")) for p in items if isinstance(p, dict) and p.get("size")][:10])
        filtered = [p for p in items if isinstance(p, dict) and str(p.get("contract") or "").strip() == c]
        out = []
        for p in filtered:
            q = dict(p)
            try:
                ct_sz = float(q.get("size") or 0)
            except Exception:
                ct_sz = 0.0
            if abs(ct_sz) > 1e-12:
                base_amt = client.contracts_signed_to_base_qty(contract=c, contracts_signed=ct_sz)
                if base_amt > 0:
                    q["positionAmt"] = base_amt
                    # Preserve direction for _parse_positions. Gate encodes short as
                    # negative contract size but positionAmt is always positive.
                    q["positionSide"] = _infer_position_side_from_row(q).upper()
            out.append(q)
        logger.info("Gate filtered positions for %s: %d items, sizes=%s", c, len(out),
                     [(p.get("size"), p.get("positionAmt")) for p in out])
        return out

    if isinstance(client, HtxClient):
        if mt == "spot":
            return client.get_positions(symbol=symbol)
        raw = client.get_positions(symbol=symbol)
        data = (raw.get("data") if isinstance(raw, dict) else None) or []
        if not isinstance(data, list):
            data = []
        out_items = []
        for p in data:
            if not isinstance(p, dict):
                continue
            q = dict(p)
            cc = str(q.get("contract_code") or "").strip()
            if cc:
                parts = cc.split("-", 1)
                if len(parts) == 2:
                    q["symbol"] = f"{parts[0]}/{parts[1]}"
            try:
                vol = float(q.get("volume") or q.get("available") or 0)
            except Exception:
                vol = 0.0
            if abs(vol) > 1e-12 and cc:
                from app.services.live_trading.fill_accounting import contract_multiplier
                cs = contract_multiplier(client, 'htx', cc.replace('-', '/'))
                q['positionAmt'] = abs(vol) * cs
            out_items.append(q)
        logger.info("HTX positions for %s: %d items, sizes=%s", symbol, len(out_items),
                     [(p.get("contract_code"), p.get("volume"), p.get("positionAmt")) for p in out_items])
        return {"data": out_items}

    if hasattr(client, "get_positions"):
        try:
            return client.get_positions(symbol=symbol)
        except TypeError:
            return client.get_positions()

    if hasattr(client, "get_position"):
        return client.get_position(symbol=symbol)

    if mt == "spot":
        return _fetch_spot_holdings_raw(client, symbol=symbol)

    return None


@quick_trade_blp.route('/position', methods=['GET'])
@login_required
def get_position():
    """
    Get current position for a symbol from exchange.

    Query: credential_id (int), symbol (str), market_type (str)
    """
    try:
        user_id = g.user_id
        credential_id = request.args.get("credential_id", type=int)
        symbol = request.args.get("symbol", "").strip()
        market_type = request.args.get("market_type", "swap").strip().lower()

        if not credential_id or not symbol:
            return jsonify({"code": 0, "msg": "Missing credential_id or symbol"}), 400

        exchange_config, _ = _build_product_aware_config(
            credential_id,
            user_id,
            symbol=symbol,
            market_type=market_type,
            source=request.args,
            overrides={"market_type": market_type},
        )
        exchange_id_pos = (exchange_config.get("exchange_id") or "").strip().lower()
        qt_rej = _reject_quick_trade_if_desktop_broker(exchange_id_pos)
        if qt_rej is not None:
            return qt_rej

        client = create_exchange_client(exchange_config, market_type=market_type)

        def _compute_position() -> List[Dict[str, Any]]:
            positions: List[Dict[str, Any]] = []
            try:
                raw = _fetch_exchange_positions_raw(
                    client, exchange_config, symbol=symbol, market_type=market_type
                )
                positions = _parse_positions(raw)
                if market_type == "spot" and positions:
                    positions = _enrich_spot_positions(
                        positions,
                        client=client,
                        symbol=symbol,
                        user_id=user_id,
                        credential_id=credential_id,
                        market_type=market_type,
                        exchange_id=exchange_id_pos,
                    )
            except Exception as pe:
                logger.warning(f"Position fetch failed: {pe}")
                logger.warning(traceback.format_exc())
                raise
            return positions

        positions = _compute_position() if request.args.get("refresh") == "1" else guarded_cached(
            cache_key(
                "quick_trade_position",
                user_id,
                credential_id,
                market_type,
                symbol,
                request.args.get("instrument_id", ""),
                request.args.get("product_type", ""),
                request.args.get("api_family", ""),
            ),
            _compute_position,
            ttl_sec=2,
            stale_ttl_sec=90,
            timeout_sec=10,
            namespace="quick_trade_position",
            max_concurrent=6,
        )

        strategy_conflict = _recent_strategy_conflict(
            client,
            user_id=user_id,
            credential_id=credential_id,
            symbol=symbol,
            market_type=market_type,
        )
        logger.info(f"Returning {len(positions)} positions for symbol={symbol}, market_type={market_type}")
        return jsonify({
            "code": 1,
            "msg": "success",
            "data": {"positions": positions, "strategy_conflict": strategy_conflict},
        })
    except RequestGuardError as e:
        return jsonify({"code": 0, "msg": str(e), "data": {"positions": []}}), e.status_code
    except Exception as e:
        logger.error(f"get_position failed: {e}")
        return jsonify({"code": 0, "msg": str(e)}), 500


def _quick_trade_net_base_qty(
    user_id: int,
    credential_id: int,
    symbol: str,
    market_type: str,
    position_side: str,
) -> float:
    """
    Best-effort net base-asset qty from qd_quick_trades.

    Long positions use filled buy minus sell; short positions use sell minus buy.

    Used when user chooses to close only the portion accumulated via Quick Trade, not manual exchange orders.
    Imperfect if the user also traded the same symbol elsewhere or records are incomplete.
    """
    mt = (market_type or "swap").strip().lower()
    ps = (position_side or "").strip().lower()
    sym = str(symbol or "").strip()
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT
              COALESCE(SUM(CASE WHEN side = 'buy' THEN filled_amount ELSE 0 END), 0) AS b,
              COALESCE(SUM(CASE WHEN side = 'sell' THEN filled_amount ELSE 0 END), 0) AS s
            FROM qd_quick_trades
            WHERE user_id = %s AND credential_id = %s AND symbol = %s AND market_type = %s
              AND status = 'filled' AND COALESCE(filled_amount, 0) > 0
            """,
            (int(user_id), int(credential_id), sym, mt),
        )
        row = cur.fetchone() or {}
        cur.close()
    buy_sum = float(row.get("b") or 0)
    sell_sum = float(row.get("s") or 0)
    if ps == "long":
        net = buy_sum - sell_sum
    elif ps == "short":
        net = sell_sum - buy_sum
    else:
        net = 0.0
    return max(0.0, float(net))


@quick_trade_blp.route('/close-position', methods=['POST'])
@login_required
@quick_trade_blp.arguments(QuickTradeCloseRequestSchema, location="json")
@serialized_quick_trade_close
def close_position(body):
    """
    Close an existing position.
    
    Body JSON:
      credential_id  (int)    - saved exchange credential ID
      symbol         (str)    - e.g. "BTC/USDT"
      market_type    (str)    - "swap" / "spot" (default: swap)
      size           (float)  - position size to close (optional, defaults to full position)
      close_scope    (str)    - "full" (default) or "system_tracked" (swap only: min(position, net from qd_quick_trades))
      position_side  (str)    - optional "long" / "short"; required when both directions exist for the same symbol
      source         (str)    - "ai_radar" / "ai_analysis" / "indicator" / "manual"
    """
    request_started = time.monotonic()
    try:
        user_id = g.user_id
        credential_id = int(body.get("credential_id") or 0)
        symbol = str(body.get("symbol") or "").strip()
        market_type = str(body.get("market_type") or "swap").strip().lower()
        close_size = float(body.get("size") or 0)  # 0 means close full position
        order_type = str(body.get("order_type") or "market")
        limit_price = float(body.get("price") or 0)
        if order_type == "limit" and (not math.isfinite(limit_price) or limit_price <= 0):
            return jsonify({"code": 0, "msg": "quickTrade.workflow.invalidExitPrice"}), 400
        source = str(body.get("source") or "manual").strip()
        close_scope_raw = str(body.get("close_scope") or body.get("closeScope") or "full").strip().lower()
        if close_scope_raw in ("system", "system_tracked", "quick_trade", "app"):
            close_scope = "system_tracked"
        else:
            close_scope = "full"
        
        # ---- validation ----
        if not credential_id:
            return jsonify({"code": 0, "msg": "Missing credential_id"}), 400
        if not symbol:
            return jsonify({"code": 0, "msg": "Missing symbol"}), 400
        
        if market_type in ("futures", "future", "perp", "perpetual"):
            market_type = "swap"
        
        # ---- build exchange client ----
        exchange_config, resolved_product = _build_product_aware_config(
            credential_id,
            user_id,
            symbol=symbol,
            market_type=market_type,
            source=body,
            overrides={"market_type": market_type},
        )
        exchange_id = (exchange_config.get("exchange_id") or "").strip().lower()
        if not exchange_id:
            return jsonify({"code": 0, "msg": "Invalid credential: missing exchange_id"}), 400

        qt_rej = _reject_quick_trade_if_desktop_broker(exchange_id)
        if qt_rej is not None:
            return qt_rej

        client = create_exchange_client(exchange_config, market_type=market_type)

        want_side = str(body.get("position_side") or body.get("close_side") or "").strip().lower()
        if want_side not in ("", "long", "short"):
            want_side = ""

        fast_market_close = (
            market_type == "swap"
            and order_type == "market"
            and close_scope == "full"
            and close_size > 0
            and want_side in ("long", "short")
        )

        # The UI already holds an exchange position snapshot. A reduce-only market
        # order can safely use it without blocking on another position REST call.
        if fast_market_close:
            positions = [
                {
                    "symbol": symbol,
                    "side": want_side,
                    "size": close_size,
                    "entry_price": 0.0,
                    "mark_price": 0.0,
                    "leverage": 1.0,
                }
            ]
        else:
            positions = []
            try:
                raw = _fetch_exchange_positions_raw(
                    client, exchange_config, symbol=symbol, market_type=market_type
                )
                positions = _parse_positions(raw)
            except Exception as pe:
                logger.warning(f"Position fetch failed: {pe}")

            if not positions:
                return jsonify({"code": 0, "msg": f"No position found for {symbol}"}), 404

        matches: list = []
        for pos in positions:
            pos_symbol = pos.get("symbol", "").strip()
            if not quick_trade_symbols_match(symbol, pos_symbol):
                continue
            ps = str(pos.get("side") or "").strip().lower()
            if want_side in ("long", "short"):
                if ps == want_side:
                    matches.append(pos)
            else:
                matches.append(pos)

        position = None
        if len(matches) == 1:
            position = matches[0]
        elif len(matches) > 1:
            if want_side in ("long", "short"):
                position = matches[0]
            else:
                return jsonify(
                    {
                        "code": 0,
                        "msg": "Both long and short positions exist for this symbol. Set position_side to long or short.",
                    }
                ), 400
        if not position:
            return jsonify({"code": 0, "msg": f"No position found for {symbol}"}), 404

        position_side = str(position.get("side") or "").strip().lower()
        position_size = float(position.get("size") or 0)
        
        if position_size <= 0:
            return jsonify({"code": 0, "msg": "Position size is zero or invalid"}), 400
        
        if close_scope == "system_tracked" and market_type != "swap":
            return jsonify({"code": 0, "msg": "system_tracked close_scope is only supported for swap/perp"}), 400

        tracked_net = 0.0
        if close_scope == "system_tracked":
            tracked_net = _quick_trade_net_base_qty(
                user_id, credential_id, symbol, market_type, position_side=position_side
            )
            if tracked_net <= 0:
                return jsonify(
                    {
                        "code": 0,
                        "msg": "No filled Quick Trade volume found for this symbol; use full close or check history.",
                    }
                ), 400

        # Determine close size
        if close_size > 0:
            actual_close_size = min(close_size, position_size)
        elif close_scope == "system_tracked":
            actual_close_size = min(tracked_net, position_size)
            logger.info(
                "close_position system_tracked: symbol=%s side=%s position=%s tracked_net=%s close=%s",
                symbol,
                position.get("side"),
                position_size,
                tracked_net,
                actual_close_size,
            )
        else:
            actual_close_size = position_size
        if actual_close_size > position_size:
            actual_close_size = position_size
        if actual_close_size <= 0:
            return jsonify({"code": 0, "msg": "Close size is zero"}), 400

        if market_type == "spot":
            from app.services.live_trading.spot_sizing import clamp_spot_close_quantity

            adjusted, spot_meta = clamp_spot_close_quantity(
                client, symbol=symbol, requested_qty=actual_close_size
            )
            if adjusted <= 0:
                return jsonify(
                    {
                        "code": 0,
                        "msg": "strategyRuntime.spotBalanceInsufficient",
                    }
                ), 400
            if spot_meta.get("adjusted"):
                logger.info(
                    "quick_trade spot close adjusted: symbol=%s requested=%s final=%s meta=%s",
                    symbol,
                    actual_close_size,
                    adjusted,
                    spot_meta,
                )
            actual_close_size = adjusted

        status_close_size, close_quantity_snapshot = normalized_submission_quantity(
            client,
            exchange_id=exchange_id,
            symbol=symbol,
            market_type=market_type,
            requested=actual_close_size,
            exchange_config=exchange_config,
            source="quick_trade_close_submission",
            snapshotter=exchange_quantity_snapshot,
        )
        logger.info(
            "Quick Trade close prepared exchange=%s fast=%s elapsed_ms=%.1f",
            exchange_id,
            fast_market_close,
            (time.monotonic() - request_started) * 1000,
        )
        
        # ---- determine signal type based on position side ----
        if market_type == "spot":
            # Spot only supports long positions
            if position_side != "long":
                return jsonify({"code": 0, "msg": "Spot market only supports closing long positions"}), 400
            signal_type = "close_long"
        else:
            # Swap: close_long or close_short
            if position_side == "long":
                signal_type = "close_long"
            elif position_side == "short":
                signal_type = "close_short"
            else:
                return jsonify({"code": 0, "msg": f"Unknown position side: {position_side}"}), 400
        
        # ---- place close order ----
        from app.services.live_trading.execution import place_order_from_signal
        
        # Generate client_order_id
        timestamp_suffix = str(int(time.time()))[-6:]
        uuid_suffix = uuid.uuid4().hex[:8]
        client_order_id = f"qtc{timestamp_suffix}{uuid_suffix}"  # 'c' for close
        
        order_args = dict(
            client=client,
            signal_type=signal_type,
            symbol=symbol,
            amount=actual_close_size,  # Use position size directly (already in base qty)
            market_type=market_type,
            exchange_config=exchange_config,
            client_order_id=client_order_id,
            quote_amount=0,
        )
        if order_type == "limit":
            from app.services.pending_orders.live_order_phases import place_live_limit_order
            result = place_live_limit_order(
                client=client, symbol=symbol, side="sell" if position_side == "long" else "buy",
                amount=actual_close_size, price=limit_price, reduce_only=True, pos_side=position_side,
                client_order_id=client_order_id, market_type=market_type, payload=exchange_config,
                exchange_config=exchange_config, leverage=float(position.get("leverage") or 1), order_mode="gtc",
            )
        else:
            result = place_order_from_signal(**order_args)
        logger.info(
            "Quick Trade close acknowledged exchange=%s fast=%s elapsed_ms=%.1f",
            exchange_id,
            fast_market_close,
            (time.monotonic() - request_started) * 1000,
        )
        
        # ---- extract result ----
        exchange_order_id = str(getattr(result, "exchange_order_id", "") or "")
        filled = float(getattr(result, "filled", 0) or 0)
        avg_fill = float(getattr(result, "avg_price", 0) or 0)
        raw = getattr(result, "raw", {}) or {}

        # Bound optional fill polling; the order reconciler owns later updates.
        commission = 0.0
        commission_ccy = ""
        commission_quote = None
        exchange_status = ""
        enrich = {"fee_status": "pending", "fees_by_ccy": {}}
        if exchange_order_id and not fast_market_close:
            enrich = enrich_fill(
                client,
                order_id=exchange_order_id,
                symbol=symbol,
                market_type=market_type,
                max_wait_sec=0.25,
                initial_order=raw,
                include_fees=False,
            )
            if enrich.get("filled", 0.0) > 0:
                filled = float(enrich["filled"])
            if enrich.get("avg_price", 0.0) > 0:
                avg_fill = float(enrich["avg_price"])
            commission = float(enrich.get("fee") or 0.0)
            commission_ccy = str(enrich.get("fee_ccy") or "")
            exchange_status = str(enrich.get("status") or "")
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

        status = quick_order_status(
            requested_qty=status_close_size,
            filled_qty=filled,
            exchange_status=exchange_status,
        )

        remaining_size: Optional[float] = None
        close_complete: Optional[bool] = None
        retry_close_size = 0.0
        if (
            order_type == "market"
            and close_scope == "full"
            and not fast_market_close
        ):
            try:
                verification = verify_full_market_close(
                    client=client,
                    exchange_config=exchange_config,
                    symbol=symbol,
                    market_type=market_type,
                    position_side=position_side,
                    requested=actual_close_size,
                    filled=filled,
                    status=status,
                    fetch_positions=_fetch_exchange_positions_raw,
                    parse_positions=_parse_positions,
                )
                remaining_size = verification["remaining_size"]
                close_complete = verification["close_complete"]
                retry_close_size = verification["retry_close_size"]
                status = verification["status"]
                logger.info(
                    "quick_trade close verification: exchange=%s market=%s symbol=%s side=%s requested=%s filled=%s remaining=%s retry=%s complete=%s",
                    exchange_id,
                    market_type,
                    symbol,
                    position_side,
                    actual_close_size,
                    filled,
                    remaining_size,
                    retry_close_size,
                    close_complete,
                )
            except Exception as verify_error:
                logger.warning(
                    "quick_trade close verification failed: exchange=%s market=%s symbol=%s side=%s error=%s",
                    exchange_id,
                    market_type,
                    symbol,
                    position_side,
                    verify_error,
                )
        raw_record = dict(raw) if isinstance(raw, dict) else {"raw": raw}
        raw_record["_quick_trade"] = {
            "requested_base_qty": actual_close_size,
            "executable_base_qty": status_close_size,
            "quantity_snapshot": close_quantity_snapshot,
            "exchange_status": exchange_status,
            "is_close": True,
            "close_side": position_side,
            "close_scope": close_scope,
            "entry_price": float(position.get("entry_price") or 0),
            "instrument_id": str((resolved_product or {}).get("instrument_id") or ""),
            "product_type": str((resolved_product or {}).get("product_type") or ""),
            "api_family": str((resolved_product or {}).get("api_family") or ""),
            "fees_by_ccy": dict(enrich.get("fees_by_ccy") or {}),
            "fee_status": str(enrich.get("fee_status") or "pending"),
            "remaining_size": remaining_size,
            "close_complete": close_complete,
            "retry_close_size": retry_close_size,
        }

        # ---- calculate USDT amount for recording ----
        # Convert base asset quantity to USDT amount for consistent recording
        # amount (USDT) = base_qty * price
        usdt_amount = actual_close_size * avg_fill if avg_fill > 0 else 0
        # If price is not available, try to use entry price or mark price as fallback
        if usdt_amount <= 0:
            entry_price = float(position.get("entry_price") or 0)
            mark_price = float(position.get("mark_price") or 0)
            fallback_price = mark_price if mark_price > 0 else entry_price
            if fallback_price > 0:
                usdt_amount = actual_close_size * fallback_price
        
        # ---- record trade ----
        trade_id = _record_quick_trade(
            user_id=user_id,
            credential_id=credential_id,
            exchange_id=exchange_id,
            symbol=symbol,
            side="sell" if position_side == "long" else "buy",  # Opposite of position side
            order_type=order_type,
            amount=usdt_amount,  # Record USDT amount, not base asset quantity
            price=limit_price if order_type == "limit" else avg_fill,
            leverage=float(position.get("leverage") or 1),
            market_type=market_type,
            tp_price=0,
            sl_price=0,
            status=status,
            exchange_order_id=exchange_order_id,
            filled=filled,
            avg_price=avg_fill,
            error_msg="",
            source=source,
            raw_result=raw_record,
            commission=commission,
            commission_ccy=commission_ccy,
            commission_quote=commission_quote,
        )
        
        return jsonify({
            "code": 1,
            "msg": "Position closed successfully",
            "data": {
                "trade_id": trade_id,
                "exchange_order_id": exchange_order_id,
                "filled": filled,
                "avg_price": avg_fill,
                "closed_size": actual_close_size,
                "position_side": position_side,
                "close_scope": close_scope,
                "tracked_net_base": tracked_net if close_scope == "system_tracked" else None,
                "status": status,
                "remaining_size": remaining_size,
                "close_complete": close_complete,
                "retry_close_size": retry_close_size,
            },
        })
        
    except Exception as e:
        logger.error(f"close_position failed: {e}")
        logger.error(traceback.format_exc())
        err_str = str(e)
        if err_str.startswith("strategyV2."):
            return jsonify({"code": 0, "msg": err_str}), 400
        hint = parse_trade_error_hint(err_str)
        resp: Dict[str, Any] = {"code": 0, "msg": err_str}
        if hint:
            resp["error_hint"] = hint
        return jsonify(resp), 500


@quick_trade_blp.route('/cancel-order', methods=['POST'])
@login_required
@quick_trade_blp.arguments(QuickTradeCancelRequestSchema, location="json")
def cancel_order(body):
    """Cancel an active Quick Trade limit order owned by the current user."""
    user_id = int(g.user_id)
    trade_id = int(body.get("trade_id") or 0)
    active_statuses = {
        "new", "open", "pending", "accepted", "submitted", "partial", "partially_filled",
    }
    try:
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                SELECT id, user_id, credential_id, exchange_id, symbol, order_type,
                       market_type, status, exchange_order_id, filled_amount,
                       avg_fill_price, commission, commission_ccy, commission_quote,
                       raw_result
                FROM qd_quick_trades
                WHERE id = %s AND user_id = %s
                """,
                (trade_id, user_id),
            )
            row = cur.fetchone()
            cur.close()

        if not row:
            return jsonify({"code": 0, "msg": "quick_trade.order_not_found"}), 404

        row = dict(row)
        status = str(row.get("status") or "").strip().lower().replace("-", "_")
        if str(row.get("order_type") or "").strip().lower() != "limit" or status not in active_statuses:
            return jsonify({"code": 0, "msg": "quick_trade.order_not_cancellable"}), 409

        order_id = str(row.get("exchange_order_id") or "").strip()
        credential_id = int(row.get("credential_id") or 0)
        if not order_id or credential_id <= 0:
            return jsonify({"code": 0, "msg": "quick_trade.order_not_cancellable"}), 409

        market_type = str(row.get("market_type") or "swap").strip().lower()
        stored_raw = row.get("raw_result") or {}
        if isinstance(stored_raw, str):
            try:
                stored_raw = json.loads(stored_raw) or {}
            except Exception:
                stored_raw = {}
        if not isinstance(stored_raw, dict):
            stored_raw = {"raw": stored_raw}
        metadata = stored_raw.get("_quick_trade")
        if not isinstance(metadata, dict):
            metadata = {}
        margin_mode = str(metadata.get("margin_mode") or "cross").strip().lower()
        exchange_config, _ = _build_product_aware_config(
            credential_id,
            user_id,
            symbol=str(row.get("symbol") or ""),
            market_type=market_type,
            source=metadata,
            overrides={"market_type": market_type, "margin_mode": margin_mode, "td_mode": margin_mode},
        )
        client = create_exchange_client(exchange_config, market_type=market_type)

        from app.services.pending_orders.live_order_phases import cancel_live_limit_order

        outcome = cancel_live_limit_order(
            client=client,
            symbol=str(row.get("symbol") or ""),
            order_id=order_id,
            client_order_id=str(metadata.get("client_order_id") or ""),
            market_type=market_type,
            exchange_config=exchange_config,
        )
        if outcome is None:
            return jsonify({"code": 0, "msg": "quick_trade.cancel_unsupported"}), 409

        fill = enrich_fill(
            client,
            order_id=order_id,
            symbol=str(row.get("symbol") or ""),
            market_type=market_type,
            max_wait_sec=0.5,
        )
        previous_filled = max(0.0, float(row.get("filled_amount") or 0.0))
        observed_filled = max(0.0, float(fill.get("filled") or 0.0))
        filled = max(previous_filled, observed_filled)
        avg_price = max(0.0, float(fill.get("avg_price") or 0.0))
        if avg_price <= 0:
            avg_price = max(0.0, float(row.get("avg_fill_price") or 0.0))
        requested_qty = max(0.0, float(metadata.get("requested_base_qty") or 0.0))
        reconciled_status = quick_order_status(
            requested_qty=requested_qty,
            filled_qty=filled,
            exchange_status=str(fill.get("status") or ""),
        )
        final_status = "filled" if reconciled_status == "filled" else "cancelled"
        commission = max(float(row.get("commission") or 0.0), float(fill.get("fee") or 0.0))
        commission_ccy = str(fill.get("fee_ccy") or row.get("commission_ccy") or "").strip().upper()
        from app.services.live_trading.fee_quote import fee_to_quote

        commission_quote = fee_to_quote(
            client,
            symbol=str(row.get("symbol") or ""),
            fee=commission,
            fee_ccy=commission_ccy,
            fill_price=avg_price,
        )
        if commission_quote is None and row.get("commission_quote") is not None:
            commission_quote = float(row.get("commission_quote") or 0.0)
        metadata["cancelled_at"] = int(time.time())
        metadata["cancel_outcome"] = outcome if isinstance(outcome, dict) else {"result": str(outcome)}
        metadata["exchange_status"] = str(fill.get("status") or "")
        stored_raw["_quick_trade"] = metadata

        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                UPDATE qd_quick_trades
                SET status = %s,
                    filled_amount = %s,
                    avg_fill_price = %s,
                    commission = %s,
                    commission_ccy = %s,
                    commission_quote = %s,
                    raw_result = %s
                WHERE id = %s AND user_id = %s
                  AND status IN ('new', 'open', 'pending', 'accepted', 'submitted', 'partial', 'partially_filled')
                """,
                (
                    final_status,
                    filled,
                    avg_price,
                    commission,
                    commission_ccy,
                    commission_quote,
                    json.dumps(stored_raw, ensure_ascii=False),
                    trade_id,
                    user_id,
                ),
            )
            db.commit()
            cur.close()

        return jsonify({
            "code": 1,
            "msg": "success",
            "data": {
                "trade_id": trade_id,
                "exchange_order_id": order_id,
                "status": final_status,
                "filled_amount": filled,
                "avg_fill_price": avg_price,
            },
        })
    except Exception as exc:
        logger.error("cancel_order failed trade_id=%s: %s", trade_id, exc)
        hint = parse_trade_error_hint(str(exc))
        response: Dict[str, Any] = {"code": 0, "msg": "quick_trade.cancel_failed"}
        if hint:
            response["error_hint"] = hint
        return jsonify(response), 500


@quick_trade_blp.route('/history', methods=['GET'])
@login_required
def get_history():
    """
    Get quick trade history for the current user.

    Query: limit, offset, credential_id, symbol, market_type
    """
    try:
        user_id = g.user_id
        limit = min(int(request.args.get("limit") or 50), 200)
        offset = int(request.args.get("offset") or 0)
        credential_id_raw = str(request.args.get("credential_id") or "").strip()
        credential_id = int(credential_id_raw) if credential_id_raw else 0
        symbol = str(request.args.get("symbol") or "").strip()
        market_type = str(request.args.get("market_type") or "").strip().lower()
        if market_type in ("future", "futures", "perp", "perpetual"):
            market_type = "swap"

        if credential_id > 0 and symbol and market_type in ("spot", "swap"):
            _sync_exchange_close_fills(
                user_id=user_id,
                credential_id=credential_id,
                symbol=symbol,
                market_type=market_type,
            )

        filters = ["user_id = %s"]
        params: List[Any] = [user_id]
        if credential_id > 0:
            filters.append("credential_id = %s")
            params.append(credential_id)
        if symbol:
            filters.append("UPPER(symbol) = UPPER(%s)")
            params.append(symbol)
        if market_type in ("spot", "swap"):
            filters.append("market_type = %s")
            params.append(market_type)
        params.extend([limit, offset])

        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                f"""
                SELECT q.id, q.credential_id, q.exchange_id, q.symbol, q.side, q.order_type,
                       q.amount, q.price, q.leverage, q.market_type, q.tp_price, q.sl_price,
                       q.status, q.exchange_order_id, q.filled_amount, q.avg_fill_price,
                       q.commission, q.commission_ccy, q.commission_quote,
                       q.error_msg, q.source, q.raw_result, q.created_at,
                       (
                           SELECT SUM(e.realized_pnl)
                           FROM qd_execution_events e
                           WHERE e.credential_id = q.credential_id
                             AND e.exchange_order_id = q.exchange_order_id
                             AND e.realized_pnl IS NOT NULL
                       ) AS realized_pnl
                FROM qd_quick_trades q
                WHERE {' AND '.join(filters)}
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
                """,
                tuple(params),
            )
            rows = cur.fetchall() or []
            cur.close()

        reconcile_quick_trade_orders(
            rows,
            user_id=user_id,
            credential_id=credential_id,
            symbol=symbol,
            market_type=market_type,
        )
        reconcile_quick_trade_fees(
            rows,
            user_id=user_id,
            credential_id=credential_id,
            symbol=symbol,
            market_type=market_type,
        )

        trades = []
        for r in rows:
            trades.append({
                "id": r.get("id"),
                "credential_id": r.get("credential_id"),
                "exchange_id": r.get("exchange_id") or "",
                "symbol": r.get("symbol") or "",
                "side": r.get("side") or "",
                "order_type": r.get("order_type") or "market",
                "amount": float(r.get("amount") or 0),
                "price": float(r.get("price") or 0),
                "leverage": int(r.get("leverage") or 1),
                "market_type": r.get("market_type") or "swap",
                "tp_price": float(r.get("tp_price") or 0),
                "sl_price": float(r.get("sl_price") or 0),
                "status": history_order_status(r),
                "exchange_order_id": r.get("exchange_order_id") or "",
                "filled_amount": float(r.get("filled_amount") or 0),
                "avg_fill_price": float(r.get("avg_fill_price") or 0),
                "commission": float(r.get("commission") or 0),
                "commission_ccy": r.get("commission_ccy") or "",
                "commission_quote": float(r.get("commission_quote") or 0),
                "realized_pnl": None if r.get("realized_pnl") is None else float(r.get("realized_pnl")),
                "error_msg": r.get("error_msg") or "",
                "source": r.get("source") or "",
                **parse_quick_trade_metadata(r.get("raw_result")),
                "created_at": to_utc_iso(r.get("created_at")) or str(r.get("created_at") or ""),
            })

        data = resolve_trade_history(trades, user_id, credential_id, symbol, market_type, limit, offset)
        return jsonify({"code": 1, "msg": "success", "data": data})
    except Exception as e:
        logger.error(f"get_history failed: {e}")
        return jsonify({"code": 0, "msg": str(e)}), 500


@quick_trade_blp.route('/position-history', methods=['GET'])
@login_required
def get_position_history():
    from app.services.quick_trade.position_activity import exchange_position_history, quick_trade_position_history
    credential_id = request.args.get("credential_id", type=int) or 0
    symbol = str(request.args.get("symbol") or "").strip()
    market_type = str(request.args.get("market_type") or "swap")
    if not credential_id or not symbol:
        return jsonify({"code": 0, "msg": "quickTrade.workflow.invalidContext"}), 400
    try:
        config = build_exchange_config(credential_id, g.user_id, {"market_type": market_type})
        if market_type != "swap":
            return jsonify({"code": 1, "data": {"supported": False, "positions": []}})
        _sync_exchange_close_fills(
            user_id=g.user_id,
            credential_id=credential_id,
            symbol=symbol,
            market_type=market_type,
        )
        client = create_exchange_client(config, market_type=market_type)
        data = exchange_position_history(client, symbol)
        if not data.get("supported"):
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    """
                    SELECT id, side, amount, leverage, status, exchange_order_id,
                           filled_amount, avg_fill_price, commission, commission_quote,
                           raw_result, created_at
                    FROM qd_quick_trades
                    WHERE user_id = %s AND credential_id = %s
                      AND UPPER(symbol) = UPPER(%s) AND market_type = 'swap'
                    ORDER BY created_at ASC, id ASC
                    """,
                    (g.user_id, credential_id, symbol),
                )
                rows = cur.fetchall() or []
                cur.close()
            data = quick_trade_position_history(rows, symbol)
        return jsonify({"code": 1, "data": data})
    except Exception as exc:
        logger.warning("Quick Trade position history failed: %s", exc)
        return jsonify({"code": 0, "msg": "quickTrade.workflow.historyFailed"}), 400


@quick_trade_blp.route('/position-protection', methods=['GET'])
@login_required
def get_position_protection():
    from app.services.quick_trade.position_activity import gate_protection_orders
    credential_id = request.args.get("credential_id", type=int) or 0
    symbol = str(request.args.get("symbol") or "").strip()
    if not credential_id or not symbol:
        return jsonify({"code": 0, "msg": "quickTrade.workflow.invalidContext"}), 400
    try:
        config = build_exchange_config(credential_id, g.user_id, {"market_type": "swap"})
        client = create_exchange_client(config, market_type="swap")
        return jsonify({"code": 1, "data": gate_protection_orders(client, symbol)})
    except Exception as exc:
        logger.warning("Quick Trade protection read failed: %s", exc)
        return jsonify({"code": 0, "msg": "quickTrade.workflow.protectionFailed"}), 400


@quick_trade_blp.route('/position-protection', methods=['POST'])
@login_required
@quick_trade_blp.arguments(QuickTradeProtectionRequestSchema, location="json")
def set_position_protection(body):
    from app.services.quick_trade.position_activity import gate_protection_orders, validate_exit_price
    from app.services.live_trading.gate import GateUsdtFuturesClient
    try:
        symbol, side = body["symbol"], body["position_side"]
        config = build_exchange_config(body["credential_id"], g.user_id, {"market_type": "swap"})
        client = create_exchange_client(config, market_type="swap")
        if not isinstance(client, GateUsdtFuturesClient):
            return jsonify({"code": 0, "msg": "quickTrade.workflow.protectionUnsupported"}), 400
        existing = gate_protection_orders(client, symbol)["orders"]
        if body["action"] == "cancel":
            match = next((o for o in existing if o["id"] == body["order_id"] and o["side"] == side), None)
            if not match:
                return jsonify({"code": 0, "msg": "quickTrade.workflow.orderGone"}), 409
            client._signed_request("DELETE", f'/api/v4/futures/usdt/price_orders/{match["id"]}')
        else:
            positions = _parse_positions(_fetch_exchange_positions_raw(client, config, symbol=symbol, market_type="swap"))
            matches = [p for p in positions if quick_trade_symbols_match(symbol, p["symbol"]) and p["side"] == side]
            if len(matches) != 1:
                return jsonify({"code": 0, "msg": "quickTrade.workflow.positionChanged"}), 409
            pos = matches[0]
            price = float(body["price"])
            reference = float(pos.get("mark_price") or 0)
            validate_exit_price(side, body["kind"], price, reference)
            if not any(o["side"] == side and o["kind"] == body["kind"] and o["price"] == price for o in existing):
                attach_quick_trade_protection(
                    client, symbol=symbol, side="buy" if side == "long" else "sell",
                    filled_qty=pos["size"], avg_price=reference,
                    tp_price=price if body["kind"] == "tp" else 0,
                    sl_price=price if body["kind"] == "sl" else 0,
                    market_type="swap", exchange_config=config, leverage=pos["leverage"],
                    margin_mode="cross", client_order_id=f"qtp{uuid.uuid4().hex[:16]}",
                )
        return jsonify({"code": 1, "data": {"accepted": True}})
    except ValueError as exc:
        return jsonify({"code": 0, "msg": str(exc)}), 400
    except Exception as exc:
        logger.warning("Quick Trade protection update failed: %s", exc)
        return jsonify({"code": 0, "msg": "quickTrade.workflow.protectionFailed"}), 400


# openapi-compat: legacy import name
quick_trade_bp = quick_trade_blp
