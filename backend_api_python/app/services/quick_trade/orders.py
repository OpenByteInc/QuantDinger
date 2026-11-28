"""Quick trade order helper functions."""

from __future__ import annotations

from typing import Any, Dict

from app.utils.logger import get_logger

logger = get_logger(__name__)


def normalize_order_status(status: str, order: Any = None) -> str:
    """Use native terminal evidence before comparing pre-rounding quantities."""
    status = str(status or "").strip().lower()
    order = order if isinstance(order, dict) else {}
    finish = str(order.get("finish_as") or "").lower()
    if status in {"finished", "closed"}:
        if finish == "filled":
            return "filled"
        if finish:
            return "cancelled"
        try:
            if order.get("left") is not None and abs(float(order.get("size") or 0)) > 0:
                return "filled" if float(order["left"]) == 0 else "cancelled"
        except (TypeError, ValueError):
            pass
    return status


def enrich_fill(
    client: Any,
    *,
    order_id: str,
    symbol: str,
    market_type: str,
    max_wait_sec: float = 8.0,
    initial_order: Any = None,
    include_fees: bool = True,
) -> Dict[str, Any]:
    """Best-effort post-place fill enrichment for a Quick Trade order."""
    out = {
        "filled": 0.0,
        "avg_price": 0.0,
        "fee": 0.0,
        "fee_ccy": "",
        "fees_by_ccy": {},
        "fee_status": "pending",
        "status": "",
    }
    oid = str(order_id or "").strip()
    if not oid:
        return out
    sym = str(symbol or "")
    market = (market_type or "swap").strip().lower()
    try:
        from app.services.live_trading.gate import GateSpotClient, GateUsdtFuturesClient
        from app.services.live_trading.okx import OkxClient
        from app.services.live_trading.symbols import to_gate_currency_pair

        fill_result: Dict[str, Any] = {}
        if isinstance(client, OkxClient):
            fill_result = client.wait_for_fill(
                symbol=sym,
                ord_id=oid,
                market_type=market,
                max_wait_sec=max_wait_sec,
            )
        elif isinstance(client, GateSpotClient):
            fill_result = client.wait_for_fill(order_id=oid, symbol=sym, max_wait_sec=max_wait_sec)
        elif isinstance(client, GateUsdtFuturesClient):
            if isinstance(initial_order, dict) and str(initial_order.get("id") or "") == oid:
                native_status = str(initial_order.get("status") or "").lower()
                if native_status in {"finished", "open"} and "left" in initial_order and "size" in initial_order:
                    contracts = max(0.0, abs(float(initial_order["size"])) - abs(float(initial_order["left"])))
                    out["filled"] = abs(client.contracts_signed_to_base_qty(contract=to_gate_currency_pair(sym), contracts_signed=contracts)) if contracts else 0.0
                    out["avg_price"] = float(initial_order.get("fill_price") or 0)
                    out["status"] = normalize_order_status(native_status, initial_order)
                    if out["filled"] > 0 and include_fees:
                        fee, currency = client.get_futures_trades_for_order(order_id=oid, contract=to_gate_currency_pair(sym))
                        out["fee"], out["fee_ccy"] = abs(float(fee or 0)), currency
                        if currency:
                            out["fees_by_ccy"] = {str(currency).upper(): out["fee"]}
                            out["fee_status"] = "actual" if out["fee"] else "actual_zero"
                    return out
            fill_result = client.wait_for_fill(
                order_id=oid,
                contract=to_gate_currency_pair(sym),
                max_wait_sec=max_wait_sec,
            )
        elif type(client).__module__.startswith(("app.services.alpaca_trading", "app.services.ibkr_trading")):
            result = client.get_order_status(oid)
            raw = getattr(result, "raw", {}) or {}
            fill_result = {
                "filled": getattr(result, "filled", 0.0),
                "avg_price": getattr(result, "avg_price", 0.0),
                "status": getattr(result, "status", ""),
                "fee": raw.get("commission") or 0.0,
                "fee_ccy": raw.get("commission_ccy") or "",
                "fees_by_ccy": raw.get("fees_by_ccy") or {},
                "fee_status": raw.get("fee_status") or "pending",
                "order": raw,
            }
            if (
                type(client).__module__.startswith("app.services.alpaca_trading")
                and str(fill_result["status"] or "").lower() == "filled"
                and "/" not in sym
                and not fill_result["fee_ccy"]
            ):
                fill_result["fee_ccy"] = "USD"
                fill_result["fees_by_ccy"] = {"USD": 0.0}
                fill_result["fee_status"] = "actual_zero"
        elif hasattr(client, "wait_for_fill"):
            try:
                fill_result = client.wait_for_fill(order_id=oid, max_wait_sec=max_wait_sec)
            except TypeError:
                try:
                    fill_result = client.wait_for_fill(symbol=sym, order_id=oid, max_wait_sec=max_wait_sec)
                except Exception as exc:
                    logger.info(
                        "enrich_fill: client %s wait_for_fill failed: %s",
                        type(client).__name__,
                        exc,
                    )
                    return out
        else:
            return out

        if isinstance(fill_result, dict):
            try:
                out["filled"] = float(fill_result.get("filled") or 0.0)
            except Exception:
                out["filled"] = 0.0
            try:
                out["avg_price"] = float(fill_result.get("avg_price") or 0.0)
            except Exception:
                out["avg_price"] = 0.0
            try:
                out["fee"] = abs(float(fill_result.get("fee") or 0.0))
            except Exception:
                out["fee"] = 0.0
            out["fee_ccy"] = str(fill_result.get("fee_ccy") or "").strip()
            raw_fees = fill_result.get("fees_by_ccy")
            if isinstance(raw_fees, dict):
                for currency, amount in raw_fees.items():
                    try:
                        value = float(amount or 0.0)
                    except (TypeError, ValueError):
                        continue
                    key = str(currency or "").strip().upper() or "UNKNOWN"
                    out["fees_by_ccy"][key] = out["fees_by_ccy"].get(key, 0.0) + value
            if not out["fees_by_ccy"] and out["fee_ccy"] and "fee" in fill_result:
                out["fees_by_ccy"] = {out["fee_ccy"].upper(): out["fee"]}
            out["fee_status"] = str(fill_result.get("fee_status") or "").strip().lower()
            if not out["fee_status"]:
                if out["fees_by_ccy"]:
                    out["fee_status"] = (
                        "actual"
                        if any(abs(float(value or 0.0)) > 1e-18 for value in out["fees_by_ccy"].values())
                        else "actual_zero"
                    )
                else:
                    out["fee_status"] = "pending"
            out["status"] = str(
                fill_result.get("status")
                or fill_result.get("state")
                or fill_result.get("orderStatus")
                or ""
            ).strip().lower()
            out["status"] = normalize_order_status(out["status"], fill_result.get("order"))
    except Exception as exc:
        logger.info("enrich_fill skipped: %s", exc)
    return out


def quick_order_status(
    *,
    requested_qty: float,
    filled_qty: float,
    exchange_status: str = "",
) -> str:
    """Map exchange cumulative fill state without treating partial fills as final."""
    status = str(exchange_status or "").strip().lower().replace("-", "_")
    if status in {"filled", "closed", "complete", "completed", "full_fill"}:
        return "filled"
    if status in {"cancelled", "canceled", "expired"}:
        return "cancelled"
    if status in {"rejected", "failed"}:
        return "failed"
    requested = max(0.0, float(requested_qty or 0.0))
    filled = max(0.0, float(filled_qty or 0.0))
    if requested > 0 and filled >= requested * 0.999999:
        return "filled"
    if filled > 0:
        return "partially_filled"
    return "submitted"


def quick_trade_protection_status(
    *,
    requested: bool,
    market_type: str,
    filled_qty: float,
    avg_price: float,
    responses: list[Dict[str, Any]],
    error: str = "",
) -> tuple[str, str]:
    """Summarize whether native protection is active for a Quick Trade order."""
    normalized_error = str(error or "").strip()
    if normalized_error:
        return "failed", normalized_error
    if responses:
        return "attached", ""
    if not requested:
        return "not_requested", ""
    if str(market_type or "").strip().lower() != "swap":
        return "unsupported_market", ""
    if float(filled_qty or 0.0) <= 0 or float(avg_price or 0.0) <= 0:
        return "pending_fill", ""
    return "failed", "native_protection_empty_response"


def attach_quick_trade_protection(
    client: Any,
    *,
    symbol: str,
    side: str,
    filled_qty: float,
    avg_price: float,
    tp_price: float,
    sl_price: float,
    market_type: str,
    exchange_config: Dict[str, Any],
    leverage: float,
    margin_mode: str,
    client_order_id: str,
    trailing_stop_pct: float = 0.0,
    trailing_activation_pct: float = 0.0,
) -> list[Dict[str, Any]]:
    """Attach native protection to the filled part of a Quick Trade entry."""
    if str(market_type or "").strip().lower() != "swap":
        return []
    if float(filled_qty or 0.0) <= 0 or float(avg_price or 0.0) <= 0:
        return []
    trailing_stop_pct = max(0.0, float(trailing_stop_pct or 0.0))
    trailing_activation_pct = max(0.0, float(trailing_activation_pct or 0.0))
    if float(tp_price or 0.0) <= 0 and float(sl_price or 0.0) <= 0 and trailing_stop_pct <= 0:
        return []
    from app.services.live_trading.native_protection import (
        NativeProtectionRequest,
        place_native_protection_orders,
    )

    cfg = exchange_config if isinstance(exchange_config, dict) else {}
    request = NativeProtectionRequest(
        symbol=str(symbol),
        pos_side="long" if str(side or "").lower() == "buy" else "short",
        quantity=float(filled_qty),
        entry_price=float(avg_price),
        stop_loss_price=float(sl_price or 0.0),
        take_profit_price=float(tp_price or 0.0),
        trailing_stop_pct=trailing_stop_pct,
        trailing_activation_pct=trailing_activation_pct,
        margin_mode="isolated" if str(margin_mode).lower() in ("isolated", "iso") else "cross",
        leverage=float(leverage or 1.0),
        product_type=str(cfg.get("product_type") or cfg.get("productType") or "USDT-FUTURES"),
        margin_coin=str(cfg.get("margin_coin") or cfg.get("marginCoin") or "USDT"),
        client_order_id=str(client_order_id or ""),
    )
    return place_native_protection_orders(client, request)


def limit_order_kwargs(client, symbol, amount, price, side, market_type, client_order_id):
    """Build kwargs compatible with any exchange client's place_limit_order."""
    from app.services.live_trading.binance import BinanceFuturesClient
    from app.services.live_trading.binance_spot import BinanceSpotClient
    from app.services.live_trading.bybit import BybitClient
    from app.services.live_trading.limit_price_safety import (
        fetch_live_reference_price,
        normalize_marketable_limit_price,
    )
    from app.services.live_trading.okx import OkxClient

    price = normalize_marketable_limit_price(
        side=side,
        limit_price=price,
        reference_price=fetch_live_reference_price(client, symbol=symbol),
    )

    if isinstance(client, (BinanceFuturesClient, BinanceSpotClient)):
        return {"quantity": amount, "price": price, "client_order_id": client_order_id}
    if isinstance(client, OkxClient):
        kwargs = {
            "market_type": market_type,
            "size": amount,
            "price": price,
            "client_order_id": client_order_id,
        }
        if market_type and market_type.strip().lower() != "spot":
            kwargs["pos_side"] = "long" if side.lower() == "buy" else "short"
        return kwargs
    if isinstance(client, BybitClient):
        return {"qty": amount, "price": price, "client_order_id": client_order_id}
    return {"size": amount, "price": price, "client_order_id": client_order_id}
