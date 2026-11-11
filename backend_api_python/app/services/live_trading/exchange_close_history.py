"""Normalize recent exchange fills used to reconcile externally closed legs."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional

from app.services.live_trading.binance import BinanceFuturesClient
from app.services.live_trading.binance_spot import BinanceSpotClient
from app.services.live_trading.bitget import BitgetMixClient
from app.services.live_trading.bitget_spot import BitgetSpotClient
from app.services.live_trading.bybit import BybitClient
from app.services.live_trading.gate import GateSpotClient, GateUsdtFuturesClient
from app.services.live_trading.htx import HtxClient
from app.services.live_trading.okx import OkxClient
from app.services.alpaca_trading import AlpacaClient
from app.services.ibkr_trading import IBKRClient
from app.services.live_trading.symbols import (
    to_binance_futures_symbol,
    to_bitget_um_symbol,
    to_gate_currency_pair,
    to_htx_contract_code,
    to_okx_spot_inst_id,
    to_okx_swap_inst_id,
)
from app.utils.logger import get_logger
from app.utils.trade_close_reason import (
    EXCHANGE_ADL,
    EXCHANGE_LIQUIDATION,
    EXCHANGE_NATIVE_CLOSE,
)

logger = get_logger(__name__)


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _timestamp(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    try:
        raw = float(value)
    except Exception:
        return None
    if raw <= 0:
        return None
    if raw > 10_000_000_000_000:
        raw /= 1_000_000.0
    elif raw > 10_000_000_000:
        raw /= 1000.0
    try:
        return datetime.fromtimestamp(raw, tz=timezone.utc)
    except Exception:
        return None


def _fee_detail(value: Any, default_ccy: str = "") -> tuple[float, str]:
    parsed = value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            parsed = None
    rows = parsed if isinstance(parsed, list) else [parsed] if isinstance(parsed, dict) else []
    fees: Dict[str, float] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        ccy = str(
            row.get("feeCoin")
            or row.get("feeCcy")
            or row.get("feeCurrency")
            or default_ccy
        ).upper()
        value_raw = row.get("totalFee")
        if value_raw in (None, ""):
            value_raw = row.get("fee")
        fee = abs(_number(value_raw))
        if ccy and fee > 0:
            fees[ccy] = fees.get(ccy, 0.0) + fee
    if not fees:
        return 0.0, str(default_ccy or "")
    if len(fees) == 1:
        ccy, amount = next(iter(fees.items()))
        return amount, ccy
    return sum(fees.values()), "MIXED"


def _fill(
    *,
    fill_id: Any,
    qty: Any,
    price: Any,
    fee: Any = 0.0,
    fee_ccy: Any = "",
    closed_at: Any,
    reason: str = EXCHANGE_NATIVE_CLOSE,
    exchange_order_id: Any = "",
    client_order_id: Any = "",
) -> Optional[Dict[str, Any]]:
    fid = str(fill_id or "").strip()
    amount = abs(_number(qty))
    px = _number(price)
    closed = _timestamp(closed_at)
    if not fid or amount <= 0 or px <= 0 or closed is None:
        return None
    return {
        "exchange_fill_id": fid,
        "amount": amount,
        "price": px,
        "fee": abs(_number(fee)),
        "fee_ccy": str(fee_ccy or ""),
        "closed_at": closed,
        "close_reason": str(reason or EXCHANGE_NATIVE_CLOSE),
        "exchange_order_id": str(exchange_order_id or "").strip(),
        "client_order_id": str(client_order_id or "").strip(),
    }


def _forced_reason(row: Dict[str, Any]) -> str:
    """Normalize venue-specific forced-close markers without guessing from PnL."""
    values = " ".join(
        str(row.get(key) or "").strip().lower()
        for key in (
            "autoCloseType",
            "execType",
            "tradeSide",
            "closeType",
            "reason",
            "type",
            "clientOrderId",
            "clientOid",
            "text",
        )
    )
    compact = values.replace("_", "").replace("-", "").replace(" ", "")
    if "adl" in compact:
        return EXCHANGE_ADL
    if any(token in compact for token in ("liquidat", "busttrade", "forcedclose", "forceclose", "autoclose")):
        return EXCHANGE_LIQUIDATION
    return EXCHANGE_NATIVE_CLOSE


def _binance_rows(client: Any, symbol: str, side: str) -> List[Dict[str, Any]]:
    if isinstance(client, BinanceFuturesClient):
        rows = client.get_user_trades(symbol=symbol, limit=100) or []
    else:
        rows = client.get_my_trades(symbol=symbol, limit=100) or []
    forced_orders: Dict[str, str] = {}
    if isinstance(client, BinanceFuturesClient):
        native_symbol = to_binance_futures_symbol(symbol)
        for auto_close_type, reason in (
            ("LIQUIDATION", EXCHANGE_LIQUIDATION),
            ("ADL", EXCHANGE_ADL),
        ):
            try:
                forced = client._signed_request(
                    "GET",
                    "/fapi/v1/forceOrders",
                    params={
                        "symbol": native_symbol,
                        "autoCloseType": auto_close_type,
                        "limit": 100,
                    },
                )
            except Exception:
                forced = []
            forced_rows = forced if isinstance(forced, list) else (forced or {}).get("data", [])
            for forced_row in forced_rows if isinstance(forced_rows, list) else []:
                if not isinstance(forced_row, dict):
                    continue
                order_id = str(forced_row.get("orderId") or "").strip()
                client_id = str(forced_row.get("clientOrderId") or "").strip()
                if order_id:
                    forced_orders[order_id] = reason
                if client_id:
                    forced_orders[client_id] = reason
    want_buyer = side == "short"
    output: List[Dict[str, Any]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or bool(row.get("buyer") if "buyer" in row else row.get("isBuyer")) != want_buyer:
            continue
        pos_side = str(row.get("positionSide") or "BOTH").strip().lower()
        if pos_side not in ("", "both", side):
            continue
        order_id = str(row.get("orderId") or row.get("order_id") or "").strip()
        client_id = str(row.get("clientOrderId") or row.get("client_order_id") or "").strip()
        item = _fill(
            fill_id=row.get("id") or row.get("tradeId"),
            qty=row.get("qty") or row.get("quantity"),
            price=row.get("price"),
            fee=row.get("commission"),
            fee_ccy=row.get("commissionAsset"),
            closed_at=row.get("time"),
            reason=forced_orders.get(order_id) or forced_orders.get(client_id) or _forced_reason(row),
            exchange_order_id=order_id,
            client_order_id=client_id,
        )
        if item:
            output.append(item)
    return output


def _bybit_rows(client: BybitClient, symbol: str, side: str) -> List[Dict[str, Any]]:
    raw = client.get_executions(symbol=symbol) or {}
    rows = (((raw.get("result") or {}).get("list")) if isinstance(raw, dict) else None) or []
    want_side = "sell" if side == "long" else "buy"
    want_idx = 1 if side == "long" else 2
    output: List[Dict[str, Any]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or str(row.get("side") or "").lower() != want_side:
            continue
        idx = int(_number(row.get("positionIdx")))
        if idx not in (0, want_idx):
            continue
        closed_size = row.get("closedSize")
        if closed_size not in (None, "") and _number(closed_size) <= 0:
            continue
        qty = closed_size if _number(closed_size) > 0 else row.get("execQty")
        item = _fill(
            fill_id=row.get("execId"),
            qty=qty,
            price=row.get("execPrice"),
            fee=row.get("execFee"),
            fee_ccy=row.get("feeCurrency") or "USDT",
            closed_at=row.get("execTime"),
            reason=_forced_reason(row),
            exchange_order_id=row.get("orderId") or row.get("order_id"),
            client_order_id=row.get("orderLinkId") or row.get("clientOrderId"),
        )
        if item:
            output.append(item)
    return output


def _bitget_rows(client: Any, symbol: str, side: str, market_type: str) -> List[Dict[str, Any]]:
    if isinstance(client, BitgetMixClient):
        quote = str(symbol or "").split("/", 1)[-1].split(":", 1)[0].upper()
        product_type = "USDC-FUTURES" if quote == "USDC" else "USDT-FUTURES"
        raw = client._signed_request(
            "GET",
            "/api/v3/trade/fills",
            params={"category": product_type, "limit": 100},
        )
        data = raw.get("data") if isinstance(raw, dict) else {}
        rows = data.get("list") if isinstance(data, dict) else []
    else:
        raw = client._signed_request(
            "GET",
            "/api/v3/trade/fills",
            params={"category": "SPOT", "limit": 100},
        )
        data = raw.get("data") if isinstance(raw, dict) else {}
        rows = data.get("list") if isinstance(data, dict) else []
    want_side = "sell" if side == "long" else "buy"
    output: List[Dict[str, Any]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or str(row.get("side") or "").lower() != want_side:
            continue
        trade_side = str(row.get("tradeSide") or "").lower()
        if market_type != "spot" and trade_side and "open" in trade_side:
            continue
        fee, fee_ccy = _fee_detail(row.get("feeDetail"), "USDT")
        item = _fill(
            fill_id=row.get("execId") or row.get("tradeId") or row.get("fillId"),
            qty=row.get("execQty") or row.get("baseVolume") or row.get("size") or row.get("qty"),
            price=row.get("execPrice") or row.get("price") or row.get("fillPrice"),
            fee=fee,
            fee_ccy=fee_ccy,
            closed_at=row.get("createdTime") or row.get("cTime") or row.get("uTime"),
            reason=_forced_reason(row),
            exchange_order_id=row.get("orderId") or row.get("order_id"),
            client_order_id=row.get("clientOid") or row.get("clientOrderId"),
        )
        if item:
            output.append(item)
    return output


def _gate_rows(client: Any, symbol: str, side: str) -> List[Dict[str, Any]]:
    pair = to_gate_currency_pair(symbol)
    want_side = "sell" if side == "long" else "buy"
    multiplier = 1.0
    if isinstance(client, GateUsdtFuturesClient):
        rows = client._signed_request(
            "GET",
            "/api/v4/futures/usdt/my_trades",
            params={"contract": pair, "limit": 100, "offset": 0},
        )
        rows = rows if isinstance(rows, list) else []
        try:
            meta = client.get_contract(contract=pair) or {}
            multiplier = _number(meta.get("quanto_multiplier") or meta.get("contract_size"), 1.0)
        except Exception:
            multiplier = 1.0
    else:
        rows = client._order_trade_rows("/api/v4/spot/my_trades", {"currency_pair": pair})
    output: List[Dict[str, Any]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        if isinstance(client, GateUsdtFuturesClient):
            signed_size = _number(row.get("size"))
            row_side = "buy" if signed_size > 0 else "sell"
            qty = abs(signed_size) * max(multiplier, 0.0)
        else:
            row_side = str(row.get("side") or "").lower()
            qty = row.get("amount")
        if row_side != want_side:
            continue
        item = _fill(
            fill_id=row.get("id") or row.get("trade_id"),
            qty=qty,
            price=row.get("price"),
            fee=row.get("fee"),
            fee_ccy=row.get("fee_currency") or "USDT",
            closed_at=row.get("create_time_ms") or row.get("time_ms") or row.get("create_time") or row.get("time"),
            reason=_forced_reason(row),
            exchange_order_id=row.get("order_id") or row.get("orderId"),
            client_order_id=row.get("text") or row.get("client_order_id"),
        )
        if item:
            output.append(item)
    return output


def _okx_rows(
    client: OkxClient,
    symbol: str,
    side: str,
    market_type: str,
    opened_after: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    inst_type = "SPOT" if market_type == "spot" else "SWAP"
    inst_id = to_okx_spot_inst_id(symbol) if market_type == "spot" else to_okx_swap_inst_id(symbol)
    raw = client._signed_request(
        "GET",
        "/api/v5/trade/fills-history",
        params={"instType": inst_type, "instId": inst_id, "limit": "100"},
    )
    rows = raw.get("data") if isinstance(raw, dict) else []
    want_side = "sell" if side == "long" else "buy"
    multiplier = 1.0
    if market_type != "spot":
        try:
            meta = client.get_instrument(inst_type="SWAP", inst_id=inst_id) or {}
            multiplier = _number(meta.get("ctVal"), 1.0)
        except Exception:
            multiplier = 1.0
    output: List[Dict[str, Any]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or str(row.get("side") or "").lower() != want_side:
            continue
        pos_side = str(row.get("posSide") or "net").lower()
        if market_type != "spot" and pos_side not in ("", "net", side):
            continue
        item = _fill(
            fill_id=row.get("tradeId") or row.get("fillId"),
            qty=_number(row.get("fillSz")) * max(multiplier, 0.0),
            price=row.get("fillPx"),
            fee=row.get("fee"),
            fee_ccy=row.get("feeCcy") or "USDT",
            closed_at=row.get("ts"),
            exchange_order_id=row.get("ordId") or row.get("orderId"),
            client_order_id=row.get("clOrdId") or row.get("algoClOrdId"),
        )
        if item:
            output.append(item)
    if market_type == "spot":
        return output
    try:
        history = client._signed_request(
            "GET",
            "/api/v5/account/positions-history",
            params={"instType": "SWAP", "instId": inst_id, "limit": "30"},
        )
        history_rows = history.get("data") if isinstance(history, dict) else []
    except Exception:
        history_rows = []
    reason = EXCHANGE_NATIVE_CLOSE
    newest = None
    for row in history_rows if isinstance(history_rows, list) else []:
        if not isinstance(row, dict):
            continue
        pos_side = str(row.get("posSide") or row.get("direction") or "").lower()
        if pos_side and pos_side != side:
            continue
        closed = _timestamp(row.get("uTime") or row.get("cTime"))
        if opened_after and closed and closed < opened_after:
            continue
        if newest is None or (closed and closed > newest):
            newest = closed
            close_type = str(row.get("type") or "")
            reason = EXCHANGE_LIQUIDATION if close_type in {"3", "4"} else EXCHANGE_ADL if close_type == "5" else EXCHANGE_NATIVE_CLOSE
    for item in output:
        item["close_reason"] = reason
    return output


def _htx_rows(client: HtxClient, symbol: str, side: str, market_type: str) -> List[Dict[str, Any]]:
    want_side = "sell" if side == "long" else "buy"
    multiplier = 1.0
    if market_type == "spot":
        native_symbol = symbol.replace("/", "").replace("-", "").lower()
        raw = client._spot_private_request(
            "GET", "/v1/order/matchresults", params={"symbol": native_symbol, "size": 100}
        )
        rows = raw.get("data") if isinstance(raw, dict) else []
    else:
        try:
            info = client.get_contract_info(symbol=symbol) or {}
            multiplier = _number(info.get("contract_size") or info.get("contractSize"), 1.0)
        except Exception:
            multiplier = 1.0
        endpoint = (
            "/linear-swap-api/v1/swap_matchresults_exact"
            if client.margin_mode == "isolated"
            else "/linear-swap-api/v1/swap_cross_matchresults_exact"
        )
        raw = client._swap_private_request_raw(
            "POST", endpoint, json_body={"contract_code": to_htx_contract_code(symbol), "trade_type": 0, "size": 100}
        )
        data = raw.get("data") if isinstance(raw, dict) else {}
        rows = data.get("trades") if isinstance(data, dict) else data
    output: List[Dict[str, Any]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        direction = str(row.get("direction") or row.get("type") or "").lower()
        row_side = "sell" if "sell" in direction else "buy" if "buy" in direction else direction
        offset = str(row.get("offset") or "").lower()
        if row_side != want_side or (market_type != "spot" and offset and offset != "close"):
            continue
        qty = _number(row.get("trade_volume") or row.get("filled-amount") or row.get("volume"))
        if market_type != "spot":
            qty *= max(multiplier, 0.0)
        price = row.get("trade_price") or row.get("price")
        if _number(price) <= 0 and _number(qty) > 0:
            price = _number(row.get("filled-cash-amount")) / _number(qty)
        item = _fill(
            fill_id=row.get("trade_id") or row.get("match-id") or row.get("id"),
            qty=qty,
            price=price,
            fee=row.get("trade_fee") or row.get("filled-fees") or row.get("fee"),
            fee_ccy=row.get("fee-currency") or row.get("fee_asset") or "USDT",
            closed_at=row.get("created_at") or row.get("created-at") or row.get("ts"),
            reason=_forced_reason(row),
            exchange_order_id=row.get("order_id") or row.get("order-id") or row.get("orderId"),
            client_order_id=row.get("client_order_id") or row.get("client-order-id"),
        )
        if item:
            output.append(item)
    return output


def _alpaca_rows(
    client: AlpacaClient,
    symbol: str,
    side: str,
    opened_after: Optional[datetime],
) -> List[Dict[str, Any]]:
    after = opened_after or (datetime.now(timezone.utc) - timedelta(days=30))
    if after.tzinfo is None:
        after = after.replace(tzinfo=timezone.utc)
    rows = client.get_account_activities(
        activity_types=["FILL"],
        after=after.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        page_size=100,
        max_pages=5,
    )
    wanted_symbol = str(symbol or "").replace("/", "").replace("-", "").upper()
    wanted_side = "sell" if side == "long" else "buy"
    output: List[Dict[str, Any]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        row_symbol = str(row.get("symbol") or "").replace("/", "").replace("-", "").upper()
        if row_symbol != wanted_symbol or str(row.get("side") or "").lower() != wanted_side:
            continue
        item = _fill(
            fill_id=row.get("id") or row.get("activity_id") or row.get("execution_id"),
            qty=row.get("qty") or row.get("quantity"),
            price=row.get("price") or row.get("avg_price"),
            fee=0.0,
            fee_ccy="USD",
            closed_at=row.get("transaction_time") or row.get("executed_at") or row.get("at"),
            exchange_order_id=row.get("order_id"),
            client_order_id=row.get("client_order_id"),
        )
        if item:
            output.append(item)
    return output


def _ibkr_rows(client: IBKRClient, symbol: str, side: str) -> List[Dict[str, Any]]:
    wanted_side = "SLD" if side == "long" else "BOT"
    output: List[Dict[str, Any]] = []
    for row in client.get_executions(symbol=symbol):
        if not isinstance(row, dict) or str(row.get("side") or "").upper() != wanted_side:
            continue
        item = _fill(
            fill_id=row.get("execution_id"),
            qty=row.get("quantity"),
            price=row.get("price"),
            fee=row.get("commission"),
            fee_ccy=row.get("commission_ccy") or "USD",
            closed_at=row.get("executed_at"),
            exchange_order_id=row.get("order_id") or row.get("local_order_id"),
            client_order_id=row.get("client_order_id"),
        )
        if item:
            output.append(item)
    return output


def _select_exact_close(
    rows: Iterable[Dict[str, Any]],
    *,
    target_qty: float,
    opened_after: Optional[datetime],
    is_recorded: Callable[[str], bool],
) -> Optional[Dict[str, Any]]:
    opened = opened_after if isinstance(opened_after, datetime) else None
    if opened and opened.tzinfo is None:
        opened = opened.replace(tzinfo=timezone.utc)
    candidates = []
    for row in rows:
        closed = row.get("closed_at")
        fill_id = str(row.get("exchange_fill_id") or "")
        order_id = str(row.get("exchange_order_id") or "")
        if (
            not isinstance(closed, datetime)
            or (opened and closed < opened)
            or is_recorded(fill_id)
            or (order_id and is_recorded(order_id))
        ):
            continue
        candidates.append(dict(row))
    candidates.sort(key=lambda item: item["closed_at"])
    tolerance = max(1e-12, abs(float(target_qty or 0.0)) * 1e-6)
    total = sum(_number(row.get("amount")) for row in candidates)
    # Every unrecorded close-side fill after the strategy's latest open/add must
    # be explained by this leg. A subset match could belong to manual inventory
    # or another strategy and is therefore not safe enough to backfill.
    if not candidates or abs(total - target_qty) > tolerance:
        return None
    selected = candidates
    weighted = sum(_number(row.get("amount")) * _number(row.get("price")) for row in selected)
    order_ids = list(dict.fromkeys(str(row.get("exchange_order_id") or "") for row in selected if row.get("exchange_order_id")))
    client_ids = list(dict.fromkeys(str(row.get("client_order_id") or "") for row in selected if row.get("client_order_id")))
    return {
        "amount": total,
        "price": weighted / total if total > 0 else 0.0,
        "fee": sum(_number(row.get("fee")) for row in selected),
        "fee_ccy": selected[-1].get("fee_ccy") or "USDT",
        "closed_at": selected[-1]["closed_at"],
        "close_reason": selected[-1].get("close_reason") or EXCHANGE_NATIVE_CLOSE,
        "source": "fills_history",
        "components": selected,
        "exchange_order_id": order_ids[0] if len(order_ids) == 1 else "",
        "client_order_id": client_ids[0] if len(client_ids) == 1 else "",
    }


def resolve_exchange_external_close(
    client: Any,
    *,
    symbol: str,
    side: str,
    target_qty: float,
    opened_after: Optional[datetime],
    market_type: str,
    is_recorded: Callable[[str], bool],
) -> Optional[Dict[str, Any]]:
    """Return an exact, idempotent set of close fills for one flat account leg."""
    mt = "spot" if str(market_type or "").lower() == "spot" else "swap"
    try:
        if isinstance(client, AlpacaClient):
            rows = _alpaca_rows(client, symbol, side, opened_after)
        elif isinstance(client, IBKRClient):
            rows = _ibkr_rows(client, symbol, side)
        elif isinstance(client, (BinanceFuturesClient, BinanceSpotClient)):
            rows = _binance_rows(client, symbol, side)
        elif isinstance(client, BybitClient):
            rows = _bybit_rows(client, symbol, side)
        elif isinstance(client, (BitgetMixClient, BitgetSpotClient)):
            rows = _bitget_rows(client, symbol, side, mt)
        elif isinstance(client, (GateUsdtFuturesClient, GateSpotClient)):
            rows = _gate_rows(client, symbol, side)
        elif isinstance(client, OkxClient):
            rows = _okx_rows(client, symbol, side, mt, opened_after)
        elif isinstance(client, HtxClient):
            rows = _htx_rows(client, symbol, side, mt)
        else:
            return None
    except Exception as exc:
        logger.warning(
            "External close history lookup failed exchange=%s symbol=%s side=%s: %s",
            type(client).__name__,
            symbol,
            side,
            exc,
        )
        return None
    return _select_exact_close(
        rows,
        target_qty=float(target_qty or 0.0),
        opened_after=opened_after,
        is_recorded=is_recorded,
    )
