"""Read-only, paginated exchange order snapshots shared by account and grid views."""

from typing import Any

from app.services.live_trading.base import LiveTradingError
from app.services.live_trading.records import normalize_strategy_symbol


def _rows(value: Any) -> list[dict]:
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise LiveTradingError("invalid_open_orders_response")
    return value


def _compact_orders(rows: list[dict], exchange: str, market_type: str) -> list[dict]:
    result = []
    for row in rows:
        native = str(row.get("symbol") or "").upper()
        symbol = native
        for quote in ("USDT", "USDC", "BTC", "ETH", "EUR"):
            if symbol.endswith(quote) and len(symbol) > len(quote):
                symbol = f"{symbol[:-len(quote)]}/{quote}"
                break
        result.append({
            "symbol": normalize_strategy_symbol(symbol) or symbol,
            "inst_id": native,
            "market_type": market_type,
            "exchange_order_id": str(row.get("orderId") or ""),
            "side": str(row.get("side") or "").lower(),
            "order_type": str(row.get("orderType") or ""),
            "price": float(row.get("avgPrice") or row.get("priceAvg") or row.get("price") or 0),
            "amount": float(row.get("qty") or row.get("size") or 0),
            "filled": float(row.get("cumExecQty") or row.get("baseVolume") or 0),
            "status": str(row.get("orderStatus") or row.get("status") or ""),
        })
    return result


def fetch_exchange_open_orders(client, *, exchange_id: str, market_type: str, symbol: str = "",
                               request_allowed=None) -> list[dict]:
    """Return a complete snapshot or raise; truncated/error responses are never empty success."""
    from app.services.live_trading.account_snapshot import (
        _parse_binance_orders, _parse_okx_orders, _parse_gate_spot_orders,
        _parse_gate_futures_orders,
    )
    from app.services.live_trading.symbols import to_binance_futures_symbol, to_okx_spot_inst_id, to_okx_swap_inst_id
    from app.services.live_trading.gate import to_gate_currency_pair

    ex = {"gateio": "gate", "okex": "okx", "binanceusdm": "binance",
          "binancefutures": "binance"}.get(exchange_id, exchange_id)
    mt = market_type
    if mt not in {"spot", "swap"}:
        raise LiveTradingError("unsupported_open_orders_market")
    compact = to_binance_futures_symbol(symbol)
    params = {}
    if ex == "bybit":
        path = "/v5/order/realtime"
        params = {"category": "spot" if mt == "spot" else "linear", "openOnly": 0, "limit": 50}
        if symbol:
            params["symbol"] = compact
        elif mt == "swap":
            params["settleCoin"] = "USDT"
    elif ex == "bitget":
        path = "/api/v3/trade/unfilled-orders"
        params = {"limit": 100, "category": "SPOT" if mt == "spot" else "USDT-FUTURES"}
        if symbol:
            params["symbol"] = compact
    elif ex == "okx":
        path = "/api/v5/trade/orders-pending"
        params = {"instType": "SPOT" if mt == "spot" else "SWAP", "limit": 100}
        if symbol:
            params["instId"] = to_okx_spot_inst_id(symbol) if mt == "spot" else to_okx_swap_inst_id(symbol)
    elif ex == "binance":
        path = "/api/v3/openOrders" if mt == "spot" else "/fapi/v1/openOrders"
        if symbol:
            params["symbol"] = compact
    elif ex == "gate":
        path = "/api/v4/spot/open_orders" if mt == "spot" else "/api/v4/futures/usdt/orders"
        params = {"page": 1, "limit": 100, "account": "spot"} if mt == "spot" else {"status": "open", "limit": 100, "offset": 0}
        if symbol:
            if mt == "spot":
                path = "/api/v4/spot/orders"
                params.update(currency_pair=to_gate_currency_pair(symbol), status="open")
            else:
                params["contract"] = to_gate_currency_pair(symbol)
    else:
        raise LiveTradingError("unsupported_open_orders_exchange")

    orders = {}
    cursors = set()
    for page in range(50):
        if request_allowed is not None and not request_allowed():
            raise LiveTradingError("open_orders_request_budget_exhausted")
        raw = client._signed_request("GET", path, params=dict(params))
        cursor = ""
        if ex == "bybit":
            payload = raw.get("result") if isinstance(raw, dict) else None
            if not isinstance(payload, dict):
                raise LiveTradingError("invalid_open_orders_response")
            rows = _rows(payload.get("list"))
            cursor = str(payload.get("nextPageCursor") or "")
            more = bool(cursor)
            normalized = _compact_orders(rows, ex, mt)
        elif ex == "bitget":
            payload = raw.get("data") if isinstance(raw, dict) else None
            if not isinstance(payload, dict) or "list" not in payload:
                raise LiveTradingError("invalid_open_orders_response")
            rows = _rows(payload.get("list"))
            cursor = str(payload.get("cursor") or "")
            more = bool(cursor)
            normalized = _compact_orders(rows, ex, mt)
        elif ex == "okx":
            rows = _rows(raw.get("data") if isinstance(raw, dict) else None)
            more = len(rows) >= 100
            cursor = str(rows[-1].get("ordId") or "") if rows else ""
            normalized = _parse_okx_orders(rows, market_type=mt)
        elif ex == "binance":
            rows = _rows(raw.get("raw") if isinstance(raw, dict) else raw)
            more = False
            normalized = _parse_binance_orders(rows, market_type=mt)
        else:
            rows = _rows(raw)
            if mt == "spot" and not symbol:
                more = any(len(_rows(group.get("orders"))) >= 100 for group in rows)
                normalized = _parse_gate_spot_orders(rows)
            elif mt == "spot":
                more = len(rows) >= 100
                normalized = _parse_gate_spot_orders([{"currency_pair": params["currency_pair"], "orders": rows}])
            else:
                more = len(rows) >= 100
                normalized = _parse_gate_futures_orders(rows, client=client)

        previous_count = len(orders)
        for order in normalized:
            oid = order["exchange_order_id"]
            if not oid or not order["symbol"]:
                raise LiveTradingError("invalid_open_order_identity")
            orders[(order["symbol"], oid)] = order
        if not more:
            return list(orders.values())
        if len(orders) == previous_count:
            raise LiveTradingError("open_orders_pagination_stalled")
        if ex == "gate":
            if mt == "spot":
                params["page"] = page + 2
            else:
                params["offset"] = (page + 1) * 100
        else:
            if not cursor or cursor in cursors:
                raise LiveTradingError("open_orders_pagination_stalled")
            cursors.add(cursor)
            params[{"bybit": "cursor", "bitget": "cursor", "okx": "after"}[ex]] = cursor
    raise LiveTradingError("open_orders_pagination_limit")
