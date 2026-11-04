"""Read venue-reported P&L without substituting strategy cost calculations."""
from decimal import Decimal, InvalidOperation
import json


def number(value):
    try:
        result = Decimal(str(value))
        return result if result.is_finite() and abs(result) < Decimal("1e100") else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def json_object(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return {}
    return value if isinstance(value, dict) else {}


def same_quantity(actual, expected):
    actual, expected = number(actual), number(expected)
    return (actual is not None and expected is not None and actual > 0 and expected > 0
            and abs(actual - expected) <= max(Decimal("1e-12"), expected * Decimal("1e-9")))


def fill_report(rows, *, expected, source, currency, basis="excluding_fees", multiplier=1):
    """Only complete, deduplicated fill evidence may represent an order total."""
    unique = {}
    for row in rows:
        key = str(row.get("id") or "")
        pnl, qty = number(row.get("pnl")), number(row.get("quantity"))
        if not key or pnl is None or qty is None or qty <= 0:
            return None
        evidence = (pnl, qty)
        if key in unique and unique[key] != evidence:
            return None
        unique[key] = evidence
    quantity = sum((v[1] for v in unique.values()), Decimal(0)) * Decimal(str(multiplier))
    target = number(expected)
    if not unique or target is None or target <= 0 or not currency:
        return None
    if not same_quantity(quantity, target):
        return None
    return dict(status="reported", amount=float(sum((v[0] for v in unique.values()), Decimal(0))),
                currency=currency, fee_basis=basis, source=source, scope="order",
                quantity=float(quantity), fill_ids=sorted(unique))


def websocket_report(events, *, expected, exchange, currency, multiplier=1):
    if exchange == "ibkr":
        executions = {str(e.get("exchange_fill_id")): e for e in events if number(e.get("quantity")) and number(e["quantity"]) > 0}
        rows, currencies = [], set()
        for event in events:
            raw = json_object(event.get("raw_json"))
            if not str(event.get("exchange_fill_id") or "").endswith(":commission"):
                continue
            base_id = str(event["exchange_fill_id"]).removesuffix(":commission")
            base = executions.get(base_id)
            if not base or not raw.get("currency"):
                return None
            currencies.add(str(raw["currency"]).upper())
            rows.append(dict(id=base_id, quantity=base["quantity"], pnl=raw.get("realizedPNL")))
        if len(currencies) != 1:
            return None
        return fill_report(rows, expected=expected, currency=next(iter(currencies)),
                           source="websocket:commissionReport.realizedPNL", basis="venue_defined")
    fields = {"binance": "rp", "okx": "fillPnl", "bybit": "execPnl",
              "bitget": "profit", "htx": "real_profit"}
    field = fields.get(exchange)
    if not field:
        return None
    rows = []
    for event in events:
        raw = json_object(event.get("raw_json"))
        item = raw.get("o", raw) if exchange == "binance" else raw
        if not isinstance(item, dict):
            return None
        # A cumulative order value must never be counted as a per-fill value.
        if exchange == "bitget":
            return None
        if exchange == "htx" and isinstance(item.get("trade"), list):
            original = next((r for r in item["trade"] if isinstance(r, dict)
                             and str(r.get("id") or r.get("trade_id") or "") == str(event.get("exchange_fill_id"))), {})
            pnl = original.get(field)
        else:
            pnl = item.get(field)
        rows.append(dict(id=event.get("exchange_fill_id"), quantity=event.get("quantity"), pnl=pnl))
    basis = "venue_defined" if exchange == "bybit" else "excluding_fees"
    return fill_report(rows, expected=expected, source="websocket:" + field, currency=currency,
                       basis=basis, multiplier=multiplier)


def fetch_order_report(client, *, exchange, symbol, order_id, expected, timestamp, config):
    from app.services.live_trading.fill_accounting import contract_multiplier
    from app.services.live_trading.symbols import to_okx_swap_inst_id
    if exchange == "tiger":
        result = client.get_order_status(order_id)
        raw = json_object(getattr(result, "raw", {}))
        pnl = number(raw.get("realized_pnl"))
        quantity = number(raw.get("filled_qty"))
        currency = str(raw.get("currency") or "").strip().upper()
        if pnl is None:
            return {"status": "unsupported", "reason": "venue_order_pnl_not_provided"}
        return fill_report(
            [dict(id=order_id, quantity=quantity, pnl=pnl)],
            expected=expected,
            source="rest:order.realized_pnl",
            currency=currency,
            basis="venue_defined",
        )
    quote = symbol.rsplit("/", 1)[-1].split(":", 1)[0].upper() if "/" in symbol else ""
    # Current derivatives clients support linear contracts. Do not label inverse amounts as quote P&L.
    if quote not in {"USDT", "USDC"}:
        return None
    if exchange in {"gate", "htx"} and quote != "USDT":
        return None
    multiplier = contract_multiplier(client, exchange, symbol) if exchange in {"gate", "okx", "htx"} else 1
    common = dict(expected=expected, currency=quote, multiplier=multiplier)
    if exchange == "binance":
        raw = client.get_user_trades(symbol=symbol, order_id=order_id, limit=1000, end_time_ms=int(timestamp * 1000) + 1000)
        rows = [r for r in raw if str(r.get("orderId")) == order_id]
        return fill_report([dict(id=r.get("id"), quantity=r.get("qty"), pnl=r.get("realizedPnl")) for r in rows],
                           source="rest:userTrades.realizedPnl", **common)
    if exchange == "okx":
        raw = client.get_order_fills(inst_id=to_okx_swap_inst_id(symbol), ord_id=order_id, inst_type="SWAP")
        rows = [r for r in raw.get("data", []) if str(r.get("ordId")) == order_id]
        return fill_report([dict(id=r.get("tradeId"), quantity=r.get("fillSz"), pnl=r.get("fillPnl")) for r in rows],
                           source="rest:fills.fillPnl", **common)
    if exchange == "bitget":
        raw = client.get_order_fills(symbol=symbol, product_type=config.get("product_type") or (quote + "-FUTURES"), order_id=order_id)
        rows = [r for r in (raw.get("data") or {}).get("fillList", []) if str(r.get("orderId")) == order_id]
        return fill_report([dict(id=r.get("tradeId"), quantity=r.get("baseVolume"), pnl=r.get("profit")) for r in rows],
                           source="rest:fills.profit", basis="venue_defined", **common)
    if exchange == "bybit":
        params = dict(category="linear", symbol=symbol.replace("/", ""), limit=100,
                      startTime=max(0, int(timestamp * 1000) - 86400000), endTime=int(timestamp * 1000) + 86400000)
        for _ in range(5):
            raw = client._signed_request("GET", "/v5/position/closed-pnl", params=params)
            result = raw.get("result") or {}
            rows = [r for r in result.get("list", []) if str(r.get("orderId")) == order_id]
            if rows:
                return fill_report([dict(id=order_id, quantity=r.get("closedSize"), pnl=r.get("closedPnl")) for r in rows],
                                   source="rest:closed-pnl.closedPnl", basis="venue_defined", **common)
            cursor = result.get("nextPageCursor")
            if not cursor or cursor == params.get("cursor"):
                break
            params["cursor"] = cursor
        return None
    if exchange == "gate":
        contract = symbol.replace("/", "_")
        fills = client._order_trade_rows("/api/v4/futures/usdt/my_trades", {"contract": contract, "order": order_id})
        fills = [r for r in fills if str(r.get("order_id")) == order_id and r.get("contract") == contract]
        if not fills:
            return None
        times = [float(r.get("create_time") or 0) for r in fills]
        if min(times) <= 0:
            return None
        params = {"type": "pnl", "from": int(min(times)) - 2, "to": int(max(times)) + 2, "limit": 1000}
        bills = client._signed_request("GET", "/api/v4/futures/usdt/account_book", params=params)
        if not isinstance(bills, list) or len(bills) >= 1000:
            return None
        by_fill, seen = {}, set()
        for bill in bills:
            if bill.get("type") != "pnl" or bill.get("contract") != contract:
                continue
            bid, fid, value = str(bill.get("id") or ""), str(bill.get("trade_id") or ""), number(bill.get("change"))
            if not bid or not fid or value is None:
                continue
            if bid not in seen:
                by_fill[fid] = by_fill.get(fid, Decimal(0)) + value
                seen.add(bid)
        rows = [dict(id=r.get("id") or r.get("trade_id"), quantity=abs(Decimal(str(r["size"]))),
                     pnl=by_fill.get(str(r.get("id") or r.get("trade_id")))) for r in fills]
        return fill_report(rows, source="rest:account_book.pnl", **common)
    if exchange == "htx":
        raw = client.get_order_match_results(symbol=symbol, order_id=order_id)
        data = raw.get("data") or {}
        if (not isinstance(data, dict) or not data.get("trades")
                or any(number(r.get("real_profit")) is None for r in data.get("trades", []))):
            endpoint = ("/linear-swap-api/v1/swap_order_detail" if client.margin_mode == "isolated"
                        else "/linear-swap-api/v1/swap_cross_order_detail")
            raw = client._swap_private_request_raw("POST", endpoint,
                json_body={"contract_code": symbol.replace("/", "-"), "order_id": order_id})
            data = raw.get("data") or {}
        if not isinstance(data, dict) or str(data.get("order_id_str") or data.get("order_id") or "") != order_id:
            return None
        rows = data.get("trades") or []
        return fill_report([dict(id=r.get("id"), quantity=r.get("trade_volume"), pnl=r.get("real_profit")) for r in rows],
                           source="rest:order_detail.real_profit", **common)
    return None
