"""Venue payload parsers.

Each parser emits per-execution quantities whenever the venue provides them.
Fee amounts use one convention across the application: positive means an
expense, negative means a rebate/credit.
"""

from __future__ import annotations

from datetime import datetime, timezone
from app.services.live_trading.binance_fees import aggregate_commissions
from typing import Any, Dict, Iterable, List

from app.services.execution_streams.events import (
    ExecutionEvent,
    FeeComponent,
    as_float,
    as_millis_datetime,
    normalize_status,
    normalize_symbol,
)


def _fee(currency: Any, amount: Any, *, signed_deduction: bool = False) -> List[FeeComponent]:
    ccy = str(currency or "").strip().upper()
    value = as_float(amount)
    if signed_deduction:
        value = -value
    if not ccy or abs(value) <= 1e-18:
        return []
    return [FeeComponent(currency=ccy, amount=value)]


def _identifiers(*items: tuple[str, Any]) -> List[Dict[str, str]]:
    result: List[Dict[str, str]] = []
    seen = set()
    for identifier_type, value in items:
        normalized = str(value or "").strip()
        key = (str(identifier_type or "").strip(), normalized)
        if not key[0] or not key[1] or key in seen:
            continue
        seen.add(key)
        result.append({"type": key[0], "value": key[1]})
    return result


def parse_binance(
    payload: Dict[str, Any],
    *,
    market_type: str,
) -> List[ExecutionEvent]:
    data = payload.get("o") if payload.get("e") == "ORDER_TRADE_UPDATE" else payload
    if not isinstance(data, dict):
        return []
    event_type = str(payload.get("e") or data.get("e") or "")
    if event_type not in {"executionReport", "ORDER_TRADE_UPDATE"}:
        return []
    qty = as_float(data.get("l"))
    trade_id = str(data.get("t") or "")
    if qty <= 0 or trade_id in {"", "-1"}:
        return []
    status = normalize_status(data.get("X"))
    fees = aggregate_commissions([
        {"commission": data.get("n"), "commissionAsset": data.get("N")},
    ], trade_id, qty)
    fee_status = "pending" if not fees else ("actual" if any(fees.values()) else "actual_zero")
    return [
        ExecutionEvent(
            exchange_id="binance",
            market_type=market_type,
            symbol=normalize_symbol(data.get("s")),
            exchange_order_id=str(data.get("i") or ""),
            client_order_id=str(data.get("c") or ""),
            exchange_fill_id=trade_id,
            side=str(data.get("S") or "").lower(),
            position_side=str(data.get("ps") or "").lower(),
            order_status=status,
            price=as_float(data.get("L") or data.get("ap")),
            quantity=qty,
            cumulative_quantity=as_float(data.get("z")),
            cumulative_average_price=(
                as_float(data.get("Z")) / as_float(data.get("z"))
                if market_type == "spot" and as_float(data.get("z")) > 0
                else as_float(data.get("ap"))
            ),
            realized_pnl=as_float(data.get("rp")) if data.get("rp") is not None else None,
            maker=bool(data.get("m")) if data.get("m") is not None else None,
            fee_status=fee_status,
            order_identifiers=_identifiers(
                ("algo_id", data.get("algoId") or data.get("ai")),
                ("algo_client_id", data.get("clientAlgoId") or data.get("caid")),
            ),
            occurred_at=as_millis_datetime(data.get("T") or payload.get("E")),
            fees=_fee(data.get("N"), data.get("n")),
            raw=payload,
        )
    ]


def parse_okx(payload: Dict[str, Any]) -> List[ExecutionEvent]:
    arg = payload.get("arg") if isinstance(payload.get("arg"), dict) else {}
    if str(arg.get("channel") or "") not in {"orders", "fills"}:
        return []
    out: List[ExecutionEvent] = []
    for item in payload.get("data") or []:
        if not isinstance(item, dict):
            continue
        qty = as_float(item.get("fillSz"))
        fill_id = str(item.get("tradeId") or item.get("fillId") or "")
        if qty <= 0 and not fill_id:
            continue
        cumulative_fees = str(arg.get("channel")) == "orders" and item.get("fee") not in (None, "")
        fee_ccy = item.get("feeCcy") if cumulative_fees else item.get("fillFeeCcy")
        fee_value = item.get("fee") if cumulative_fees else item.get("fillFee")
        rebate_ccy = item.get("rebateCcy")
        fees = _fee(fee_ccy, fee_value, signed_deduction=True)
        fees += _fee(rebate_ccy, -as_float(item.get("rebate")))
        inst_type = str(item.get("instType") or arg.get("instType") or "").upper()
        out.append(
            ExecutionEvent(
                exchange_id="okx",
                market_type="spot" if inst_type == "SPOT" else "swap",
                symbol=normalize_symbol(item.get("instId")),
                exchange_order_id=str(item.get("ordId") or ""),
                client_order_id=str(item.get("clOrdId") or ""),
                exchange_fill_id=fill_id,
                side=str(item.get("side") or "").lower(),
                position_side=str(item.get("posSide") or "").lower(),
                order_status=normalize_status(item.get("state")),
                price=as_float(item.get("fillPx") or item.get("avgPx")),
                quantity=qty,
                cumulative_quantity=as_float(item.get("accFillSz")),
                cumulative_average_price=as_float(item.get("avgPx")),
                fees_cumulative=cumulative_fees,
                realized_pnl=as_float(item.get("fillPnl")) if item.get("fillPnl") not in (None, "") else None,
                maker=str(item.get("execType") or "").upper() == "M"
                if item.get("execType") not in (None, "")
                else None,
                fee_status=("actual" if fees else "actual_zero") if fee_value not in (None, "") else "pending",
                order_identifiers=_identifiers(
                    ("algo_id", item.get("algoId")),
                    ("algo_client_id", item.get("algoClOrdId")),
                ),
                occurred_at=as_millis_datetime(item.get("fillTime") or item.get("uTime")),
                fees=fees,
                raw=item,
            )
        )
    return out


def parse_bybit(payload: Dict[str, Any]) -> List[ExecutionEvent]:
    if not str(payload.get("topic") or "").startswith("execution"):
        return []
    out: List[ExecutionEvent] = []
    for item in payload.get("data") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("execType") or "Trade").lower() != "trade":
            continue
        qty = as_float(item.get("execQty"))
        fill_id = str(item.get("execId") or "")
        if qty <= 0 and not fill_id:
            continue
        category = str(item.get("category") or "").lower()
        if category not in {'', 'spot', 'linear'}:
            continue
        fee_value = as_float(item.get("execFee"))
        fees = _fee(item.get("feeCurrency"), fee_value)
        for extra in item.get("extraFees") or []:
            if isinstance(extra, dict):
                fees += _fee(
                    extra.get("feeCoin") or item.get("feeCurrency"),
                    extra.get("fee"),
                )
        leaves_qty = item.get("leavesQty")
        order_status = (
            "filled"
            if leaves_qty not in (None, "") and as_float(leaves_qty) <= 0
            else "partial"
        )
        out.append(
            ExecutionEvent(
                exchange_id="bybit",
                market_type="spot" if category == "spot" else "swap",
                symbol=normalize_symbol(item.get("symbol")),
                exchange_order_id=str(item.get("orderId") or ""),
                client_order_id=str(item.get("orderLinkId") or ""),
                exchange_fill_id=fill_id,
                side=str(item.get("side") or "").lower(),
                position_side=str(item.get("side") or "").lower(),
                order_status=order_status,
                price=as_float(item.get("execPrice")),
                quantity=qty,
                realized_pnl=as_float(item.get("execPnl")) if item.get("execPnl") not in (None, "") else None,
                maker=bool(item.get("isMaker")) if item.get("isMaker") is not None else None,
                fee_status=("actual" if fees else "actual_zero") if item.get("execFee") not in (None, "") and item.get("feeCurrency") else "pending",
                order_identifiers=_identifiers(
                    ("parent_client_order_id", item.get("parentOrderLinkId")),
                    ("parent_order_id", item.get("parentOrderId")),
                ),
                occurred_at=as_millis_datetime(item.get("execTime") or payload.get("creationTime")),
                fees=fees,
                raw=item,
            )
        )
    return out


def parse_bitget(payload: Dict[str, Any]) -> List[ExecutionEvent]:
    arg = payload.get("arg") if isinstance(payload.get("arg"), dict) else {}
    topic = str(arg.get("topic") or arg.get("channel") or "")
    if topic not in {"fill", "order", "orders"}:
        return []
    out: List[ExecutionEvent] = []
    for item in payload.get("data") or []:
        if not isinstance(item, dict):
            continue
        is_uta = bool(arg.get("topic") or item.get("category"))
        category = str(item.get("category") or "").upper()
        inst_type = str(item.get("instType") or arg.get("instType") or "").upper()
        is_spot = category == "SPOT" if category else inst_type == "SPOT"
        is_order = topic in {"order", "orders"}
        if is_uta:
            qty = as_float(
                item.get("execQty")
                or item.get("fillQty")
                or item.get("cumExecQty")
                or item.get("qty")
            )
        else:
            qty = as_float(
                item.get("fillQty")
                or item.get("baseVolume")
                or item.get("execQty")
                or item.get("size")
            )
        fill_id = str(item.get("tradeId") or item.get("execId") or "")
        if qty <= 0 and not fill_id:
            continue
        fees: List[FeeComponent] = []
        details = item.get("feeDetail")
        if isinstance(details, list):
            for detail in details:
                if isinstance(detail, dict):
                    currency = detail.get("feeCoin") or detail.get("coin")
                    value = detail.get("totalFee")
                    if value in (None, ""):
                        value = detail.get("fee")
                    amount = as_float(value)
                    if not is_uta:
                        amount = -amount
                    if currency and abs(amount) > 1e-18:
                        fees.append(FeeComponent(currency=str(currency).upper(), amount=amount))
        if not fees:
            currency = item.get("fillFeeCoin") or item.get("feeCoin")
            amount = as_float(item.get("execFee") or item.get("fillFee") or item.get("fee"))
            if not is_uta:
                amount = -amount
            if currency and abs(amount) > 1e-18:
                fees = [FeeComponent(currency=str(currency).upper(), amount=amount)]
        out.append(
            ExecutionEvent(
                exchange_id="bitget",
                market_type="spot" if is_spot else "swap",
                symbol=normalize_symbol(item.get("symbol") or arg.get("symbol") or arg.get("instId")),
                exchange_order_id=str(item.get("orderId") or item.get("ordId") or ""),
                client_order_id=str(item.get("clientOid") or item.get("clOrdId") or ""),
                exchange_fill_id=fill_id,
                side=str(item.get("side") or "").lower(),
                position_side=str(item.get("posSide") or item.get("holdSide") or "").lower(),
                order_status=normalize_status(item.get("orderStatus") or item.get("status") or "partial"),
                price=as_float(
                    item.get("execPrice")
                    or (item.get("fillPrice") if not is_uta else None)
                    or item.get("avgPrice")
                    or item.get("priceAvg")
                    or item.get("fillPrice")
                    or item.get("price")
                ),
                quantity=qty,
                cumulative_quantity=as_float(item.get("cumExecQty") or item.get("accBaseVolume")),
                cumulative_average_price=as_float(item.get("avgPrice") or item.get("priceAvg")) if is_order else 0.0,
                fees_cumulative=is_order and isinstance(details, list),
                realized_pnl=as_float(item.get("execPnl") or item.get("profit")) if item.get("execPnl") not in (None, "") or item.get("profit") not in (None, "") else None,
                maker=str(item.get("tradeScope") or item.get("execType") or "").lower() == "maker"
                if item.get("tradeScope") or item.get("execType")
                else None,
                fee_status=("actual" if fees else "actual_zero")
                if details is not None
                or item.get("execFee") is not None
                or item.get("fillFee") is not None
                or item.get("fee") is not None
                else "pending",
                order_identifiers=_identifiers(
                    (
                        "parent_order_id",
                        item.get("planOrderId")
                        or item.get("triggerOrderId")
                        or item.get("strategyOrderId"),
                    ),
                    ("parent_client_order_id", item.get("planClientOid") or item.get("triggerClientOid")),
                ),
                occurred_at=as_millis_datetime(
                    item.get("createdTime")
                    or item.get("execTime")
                    or item.get("fillTime")
                    or item.get("uTime")
                    or item.get("cTime")
                ),
                fees=fees,
                raw=item,
            )
        )
    return out


def parse_gate(payload: Dict[str, Any], *, market_type: str) -> List[ExecutionEvent]:
    channel = str(payload.get("channel") or "")
    if channel not in {"spot.usertrades", "futures.usertrades"}:
        return []
    result = payload.get("result")
    items: Iterable[Any] = result if isinstance(result, list) else [result]
    out: List[ExecutionEvent] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        qty = abs(as_float(item.get("amount") or item.get("size")))
        fill_id = str(item.get("id") or item.get("trade_id") or "")
        if qty <= 0 and not fill_id:
            continue
        symbol = normalize_symbol(item.get("currency_pair") or item.get("contract") or (
            f"{item['stock']}/{item['money']}" if item.get("stock") and item.get("money") else ""
        ))
        fee_ccy = str(item.get("fee_currency") or "").strip().upper()
        if not fee_ccy and market_type != "spot" and "/" in symbol:
            fee_ccy = symbol.rsplit("/", 1)[-1]
        has_fee_field = item.get("fee") not in (None, "")
        out.append(
            ExecutionEvent(
                exchange_id="gate",
                market_type=market_type,
                symbol=symbol,
                exchange_order_id=str(item.get("order_id") or item.get("order") or ""),
                client_order_id=str(item.get("text") or ""),
                exchange_fill_id=fill_id,
                side=str(item.get("side") or ("buy" if as_float(item.get("size")) > 0 else "sell")).lower(),
                order_status="partial",
                price=as_float(item.get("price")),
                quantity=qty,
                realized_pnl=as_float(item.get("pnl")) if item.get("pnl") not in (None, "") else None,
                maker=str(item.get("role") or "").lower() == "maker" if item.get("role") else None,
                fee_status=("actual" if abs(as_float(item.get("fee"))) > 1e-18 else "actual_zero") if has_fee_field and fee_ccy else "pending",
                order_identifiers=_identifiers(
                    ("parent_order_id", item.get("price_order_id") or item.get("auto_order_id")),
                    ("child_order_id", item.get("suborder_id") or item.get("trade_id")),
                    ("child_client_order_id", item.get("suborder_text")),
                ),
                occurred_at=as_millis_datetime(item.get("create_time_ms") or item.get("time_ms") or item.get("create_time")),
                fees=_fee(fee_ccy, item.get("fee")),
                raw=item,
            )
        )
    return out


def parse_htx(payload: Dict[str, Any], *, market_type: str) -> List[ExecutionEvent]:
    topic = str(payload.get("ch") or payload.get("topic") or "")
    if "trade.clearing" not in topic and "matchOrders" not in topic:
        return []
    data = payload.get("data")
    if market_type != "spot" and isinstance(payload.get("trade"), list):
        parent = {k: v for k, v in payload.items() if k != 'real_profit'}
        data = [dict(parent, **trade) for trade in payload['trade'] if isinstance(trade, dict)]
    items: Iterable[Any] = data if isinstance(data, list) else [data]
    out: List[ExecutionEvent] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        qty = abs(as_float(item.get("tradeVolume") or item.get("trade_volume") or item.get("volume")))
        fill_id = str(item.get("id") or item.get("tradeId") or item.get("trade_id") or item.get("match_id") or "")
        if qty <= 0 and not fill_id:
            continue
        fee_ccy = item.get("feeCurrency") or item.get("fee_asset") or item.get("feeCurrencyCode")
        fee_value = item.get("transactFee") if item.get("transactFee") is not None else item.get("trade_fee")
        fees = _fee(fee_ccy, fee_value, signed_deduction=market_type != 'spot')
        out.append(
            ExecutionEvent(
                exchange_id="htx",
                market_type=market_type,
                symbol=normalize_symbol(item.get("contract_code") or item.get("symbol")),
                exchange_order_id=str(item.get("orderId") or item.get("order_id_str") or item.get("order_id") or ""),
                client_order_id=str(item.get("clientOrderId") or item.get("client_order_id") or ""),
                exchange_fill_id=fill_id,
                side=str(item.get("orderSide") or item.get("direction") or "").lower(),
                position_side=str(item.get("direction") or "").lower(),
                order_status="partial",
                price=as_float(item.get("tradePrice") or item.get("trade_price") or item.get("price")),
                quantity=qty,
                realized_pnl=as_float(item.get("real_profit")) if item.get("real_profit") not in (None, "") else None,
                fee_status=("actual" if fees else "actual_zero") if fee_value not in (None, "") and fee_ccy else "pending",
                order_identifiers=_identifiers(
                    ("parent_order_id", item.get("source_order_id") or item.get("relation_tpsl_order_id")),
                    ("child_order_id", item.get("relation_order_id")),
                ),
                occurred_at=as_millis_datetime(item.get("tradeTime") or item.get("created_at") or item.get("ts")),
                fees=fees,
                raw=item,
            )
        )
    return out


def parse_alpaca(payload: Dict[str, Any]) -> List[ExecutionEvent]:
    if str(payload.get("stream") or "") != "trade_updates":
        return []
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    event_name = str(data.get("event") or "")
    if event_name not in {"fill", "partial_fill"}:
        return []
    order = data.get("order") if isinstance(data.get("order"), dict) else {}
    qty = as_float(data.get("qty"))
    cumulative = as_float(order.get("filled_qty"))
    event_id = str(data.get("execution_id") or data.get("id") or "")
    occurred = data.get("timestamp")
    try:
        occurred_at = datetime.fromisoformat(str(occurred).replace("Z", "+00:00"))
    except Exception:
        occurred_at = datetime.now(timezone.utc)
    return [
        ExecutionEvent(
            exchange_id="alpaca",
            market_type="spot" if str(order.get("asset_class") or "") == "crypto" else "usstock",
            symbol=str(order.get("symbol") or ""),
            exchange_order_id=str(order.get("id") or ""),
            client_order_id=str(order.get("client_order_id") or ""),
            exchange_fill_id=event_id,
            side=str(order.get("side") or "").lower(),
            order_status="filled" if event_name == "fill" else "partial",
            price=as_float(data.get("price") or order.get("filled_avg_price")),
            quantity=qty,
            cumulative_quantity=cumulative,
            cumulative_average_price=as_float(order.get("filled_avg_price")),
            is_cumulative=qty <= 0 and cumulative > 0,
            fee_status="pending",
            occurred_at=occurred_at,
            raw=data,
        )
    ]


def parse_ibkr_execution(execution: Any, contract: Any = None) -> ExecutionEvent:
    symbol = str(getattr(contract, "symbol", "") or getattr(execution, "symbol", ""))
    occurred = getattr(execution, "time", None)
    if not isinstance(occurred, datetime):
        occurred = datetime.now(timezone.utc)
    elif occurred.tzinfo is None:
        occurred = occurred.replace(tzinfo=timezone.utc)
    return ExecutionEvent(
        exchange_id="ibkr",
        market_type="usstock",
        symbol=symbol,
        exchange_order_id=str(getattr(execution, "orderId", "") or getattr(execution, "permId", "")),
        client_order_id=str(getattr(execution, "orderRef", "") or ""),
        exchange_fill_id=str(getattr(execution, "execId", "") or ""),
        side=str(getattr(execution, "side", "") or "").lower(),
        order_status="partial",
        price=as_float(getattr(execution, "price", 0)),
        quantity=abs(as_float(getattr(execution, "shares", 0))),
        cumulative_quantity=abs(as_float(getattr(execution, "cumQty", 0))),
        cumulative_average_price=as_float(getattr(execution, "avgPrice", 0)),
        fee_status="pending",
        occurred_at=occurred,
        raw={
            "execId": str(getattr(execution, "execId", "") or ""),
            "permId": str(getattr(execution, "permId", "") or ""),
            "orderId": str(getattr(execution, "orderId", "") or ""),
        },
    )
