"""Exchange-native protective orders for filled derivative entries."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, List, Mapping, Tuple

from app.services.live_trading.base import LiveTradingError
from app.services.live_trading.symbols import (
    to_binance_futures_symbol,
    to_bitget_um_symbol,
    to_bybit_symbol,
    to_gate_currency_pair,
    to_htx_contract_code,
    to_okx_swap_inst_id,
)


@dataclass(frozen=True)
class NativeProtectionRequest:
    symbol: str
    pos_side: str
    quantity: float
    entry_price: float
    stop_loss_price: float = 0.0
    take_profit_price: float = 0.0
    trailing_stop_pct: float = 0.0
    trailing_activation_pct: float = 0.0
    margin_mode: str = "cross"
    leverage: float = 1.0
    product_type: str = "USDT-FUTURES"
    margin_coin: str = "USDT"
    client_order_id: str = ""


def protection_prices_from_payload(
    payload: Mapping[str, Any],
    *,
    entry_price: float,
    pos_side: str,
) -> Tuple[float, float, float, float]:
    """Resolve absolute SL/TP and trailing ratios from a queued-order payload."""
    protection = payload.get("protection")
    spec = protection if isinstance(protection, Mapping) else {}
    entry = max(0.0, float(entry_price or 0.0))
    side = str(pos_side or "").strip().lower()

    def number(*values: Any) -> float:
        for value in values:
            try:
                result = float(value or 0.0)
            except Exception:
                continue
            if result > 0:
                return result
        return 0.0

    stop = number(payload.get("stop_loss_price"), payload.get("stopLossPrice"))
    take = number(payload.get("take_profit_price"), payload.get("takeProfitPrice"))
    stop_pct = number(spec.get("stop_loss_pct"), payload.get("stop_loss_pct"))
    take_pct = number(spec.get("take_profit_pct"), payload.get("take_profit_pct"))
    trailing_pct = number(spec.get("trailing_stop_pct"), payload.get("trailing_stop_pct"))
    activation_pct = number(
        spec.get("trailing_activation_pct"), payload.get("trailing_activation_pct")
    )
    if entry > 0 and stop <= 0 and stop_pct > 0:
        stop = entry * (1.0 - stop_pct if side == "long" else 1.0 + stop_pct)
    if entry > 0 and take <= 0 and take_pct > 0:
        take = entry * (1.0 + take_pct if side == "long" else 1.0 - take_pct)
    return stop, take, trailing_pct, activation_pct


def place_native_protection_orders(
    client: Any,
    request: NativeProtectionRequest,
) -> List[Dict[str, Any]]:
    """Place reduce-only native protection and return exchange responses."""
    side = str(request.pos_side or "").strip().lower()
    qty = float(request.quantity or 0.0)
    if side not in ("long", "short") or qty <= 0:
        raise LiveTradingError("invalid_native_protection_request")
    if request.stop_loss_price <= 0 and request.take_profit_price <= 0 and request.trailing_stop_pct <= 0:
        return []

    from app.services.live_trading.binance import BinanceFuturesClient
    from app.services.live_trading.bitget import BitgetMixClient
    from app.services.live_trading.bybit import BybitClient
    from app.services.live_trading.gate import GateUsdtFuturesClient
    from app.services.live_trading.htx import HtxClient
    from app.services.live_trading.okx import OkxClient

    if isinstance(client, BinanceFuturesClient):
        return _place_binance(client, request)
    if isinstance(client, OkxClient):
        return _place_okx(client, request)
    if isinstance(client, BitgetMixClient):
        return _place_bitget(client, request)
    if isinstance(client, BybitClient):
        return _place_bybit(client, request)
    if isinstance(client, GateUsdtFuturesClient):
        return _place_gate(client, request)
    if isinstance(client, HtxClient):
        return _place_htx(client, request)
    raise LiveTradingError(f"native_protection_not_supported:{type(client).__name__}")


def _place_binance(client: Any, request: NativeProtectionRequest) -> List[Dict[str, Any]]:
    close_side = "SELL" if request.pos_side == "long" else "BUY"
    dual = client.get_dual_side_position()
    if dual is None:
        raise LiveTradingError("binance_position_mode_unknown")
    quantity, quantity_precision = client._normalize_quantity(
        symbol=request.symbol,
        quantity=request.quantity,
        for_market=True,
    )
    if quantity <= 0:
        raise LiveTradingError("binance_native_protection_quantity_below_minimum")
    base: Dict[str, Any] = {
        "algoType": "CONDITIONAL",
        "symbol": to_binance_futures_symbol(request.symbol),
        "side": close_side,
        "positionSide": request.pos_side.upper() if dual else "BOTH",
        "quantity": client._dec_str(quantity, strict_precision=quantity_precision),
        "workingType": "MARK_PRICE",
    }
    if not dual:
        base["reduceOnly"] = "true"
    responses: List[Dict[str, Any]] = []
    for kind, price, suffix in (
        ("STOP_MARKET", request.stop_loss_price, "sl"),
        ("TAKE_PROFIT_MARKET", request.take_profit_price, "tp"),
    ):
        if price <= 0:
            continue
        trigger_price = client._normalize_price(symbol=request.symbol, price=price)
        if trigger_price <= 0:
            raise LiveTradingError("binance_native_protection_price_below_minimum")
        body = dict(
            base,
            type=kind,
            triggerPrice=client._dec_str(trigger_price),
        )
        client_algo_id = client._format_client_order_id(
            f"{request.client_order_id}-{suffix}" if request.client_order_id else ""
        )
        if client_algo_id:
            body["clientAlgoId"] = client_algo_id
        responses.append(client._signed_request("POST", "/fapi/v1/algoOrder", params=body))
    if request.trailing_stop_pct > 0:
        callback = min(10.0, max(0.1, request.trailing_stop_pct * 100.0))
        body = dict(base, type="TRAILING_STOP_MARKET", callbackRate=str(callback))
        activation = _activation_price(request)
        if activation > 0:
            activation_price = client._normalize_price(
                symbol=request.symbol,
                price=activation,
            )
            if activation_price <= 0:
                raise LiveTradingError("binance_native_protection_price_below_minimum")
            body["activatePrice"] = client._dec_str(activation_price)
        client_algo_id = client._format_client_order_id(
            f"{request.client_order_id}-trail" if request.client_order_id else ""
        )
        if client_algo_id:
            body["clientAlgoId"] = client_algo_id
        responses.append(client._signed_request("POST", "/fapi/v1/algoOrder", params=body))
    return responses


def _place_okx(client: Any, request: NativeProtectionRequest) -> List[Dict[str, Any]]:
    inst_id = to_okx_swap_inst_id(request.symbol)
    size, precision = client._normalize_order_size(
        inst_id=inst_id, market_type="swap", size=request.quantity
    )
    pos_side = client._resolve_pos_side(
        requested_pos_side=request.pos_side, market_type="swap"
    )
    base: Dict[str, Any] = {
        "instId": inst_id,
        "tdMode": "isolated" if request.margin_mode == "isolated" else "cross",
        "side": "sell" if request.pos_side == "long" else "buy",
        "posSide": pos_side,
        "ordType": "conditional",
        "sz": client._dec_str(size, strict_precision=precision),
    }
    if pos_side == "net":
        base["reduceOnly"] = "true"
    if client.broker_code:
        base["tag"] = str(client.broker_code)
    responses: List[Dict[str, Any]] = []
    for prefix, price in (("sl", request.stop_loss_price), ("tp", request.take_profit_price)):
        if price <= 0:
            continue
        body = dict(base)
        body[f"{prefix}TriggerPx"] = str(price)
        body[f"{prefix}OrdPx"] = "-1"
        body[f"{prefix}TriggerPxType"] = "mark"
        responses.append(client._signed_request("POST", "/api/v5/trade/order-algo", json_body=body))
    if request.trailing_stop_pct > 0:
        body = dict(base, ordType="move_order_stop", callbackRatio=str(request.trailing_stop_pct))
        activation = _activation_price(request)
        if activation > 0:
            body["activePx"] = str(activation)
        responses.append(client._signed_request("POST", "/api/v5/trade/order-algo", json_body=body))
    return responses


def _place_bitget(client: Any, request: NativeProtectionRequest) -> List[Dict[str, Any]]:
    resolver = getattr(client, "_resolve_bitget_client", None)
    if callable(resolver):
        client = resolver()
    from app.services.live_trading.bitget_classic import BitgetClassicMixClient

    if isinstance(client, BitgetClassicMixClient):
        return _place_bitget_classic(client, request)

    size, precision = client._normalize_size(
        symbol=request.symbol,
        product_type=request.product_type,
        base_size=request.quantity,
    )
    close_side = "sell" if request.pos_side == "long" else "buy"
    base = {
        "category": request.product_type.upper(),
        "symbol": to_bitget_um_symbol(request.symbol),
        "type": "tpsl",
        "side": close_side,
        "posSide": request.pos_side,
        "marginMode": "isolated" if request.margin_mode == "isolated" else "crossed",
        "tpslMode": "partial",
        "reduceOnly": "yes",
        "qty": client._dec_str(size, strict_precision=precision),
    }
    responses: List[Dict[str, Any]] = []
    if request.stop_loss_price > 0 or request.take_profit_price > 0:
        body = dict(base)
        if request.stop_loss_price > 0:
            body.update(
                stopLoss=str(request.stop_loss_price),
                slTriggerBy="mark",
                slOrderType="market",
            )
        if request.take_profit_price > 0:
            body.update(
                takeProfit=str(request.take_profit_price),
                tpTriggerBy="mark",
                tpOrderType="market",
            )
        if request.client_order_id:
            body["clientOid"] = str(request.client_order_id)[:32]
        responses.append(
            client._signed_request("POST", "/api/v3/trade/place-strategy-order", json_body=body)
        )
    if request.trailing_stop_pct > 0:
        body = {
            "category": request.product_type.upper(),
            "symbol": to_bitget_um_symbol(request.symbol),
            "type": "trailing_stop",
            "side": close_side,
            "posSide": request.pos_side,
            "marginMode": "isolated" if request.margin_mode == "isolated" else "crossed",
            "reduceOnly": "yes",
            "qty": client._dec_str(size, strict_precision=precision),
            "trailingStopParams": {
                "activationPrice": str(_activation_price(request) or request.entry_price),
                "activationType": "mark",
                "trailType": "ratio",
                "trailVariance": str(request.trailing_stop_pct * 100.0),
                "preOrderType": "market",
            },
        }
        responses.append(
            client._signed_request("POST", "/api/v3/trade/place-strategy-order", json_body=body)
        )
    return responses


def _place_bitget_classic(client: Any, request: NativeProtectionRequest) -> List[Dict[str, Any]]:
    size, precision = client._normalize_size(
        symbol=request.symbol,
        product_type=request.product_type,
        base_size=request.quantity,
    )
    base = {
        "marginCoin": request.margin_coin.upper(),
        "productType": request.product_type.upper(),
        "symbol": to_bitget_um_symbol(request.symbol),
        "triggerType": "mark_price",
        "executePrice": "0",
        "holdSide": request.pos_side,
        "size": client._dec_str(size, strict_precision=precision),
    }
    responses: List[Dict[str, Any]] = []
    for plan_type, price, suffix in (
        ("loss_plan", request.stop_loss_price, "sl"),
        ("profit_plan", request.take_profit_price, "tp"),
    ):
        if price <= 0:
            continue
        body = dict(base, planType=plan_type, triggerPrice=str(price))
        if request.client_order_id:
            body["clientOid"] = f"{request.client_order_id}-{suffix}"[:64]
        responses.append(
            client._signed_request(
                "POST",
                "/api/v2/mix/order/place-tpsl-order",
                json_body=body,
            )
        )
    if request.trailing_stop_pct > 0:
        body = dict(
            base,
            planType="moving_plan",
            triggerPrice=str(_activation_price(request) or request.entry_price),
            rangeRate=str(request.trailing_stop_pct * 100.0),
        )
        body.pop("executePrice", None)
        responses.append(
            client._signed_request(
                "POST",
                "/api/v2/mix/order/place-tpsl-order",
                json_body=body,
            )
        )
    return responses


def _place_bybit(client: Any, request: NativeProtectionRequest) -> List[Dict[str, Any]]:
    close_side = "Sell" if request.pos_side == "long" else "Buy"
    size, precision = client._normalize_qty(symbol=request.symbol, qty=request.quantity)
    base: Dict[str, Any] = {
        "category": "linear",
        "symbol": to_bybit_symbol(request.symbol),
        "side": close_side,
        "orderType": "Market",
        "qty": client._dec_str(size, strict_precision=precision),
        "positionIdx": client._resolve_position_idx(request.pos_side, symbol=request.symbol),
        "triggerBy": "MarkPrice",
        "reduceOnly": True,
        "closeOnTrigger": True,
    }
    responses: List[Dict[str, Any]] = []
    for kind, price, suffix in (
        ("sl", request.stop_loss_price, "sl"),
        ("tp", request.take_profit_price, "tp"),
    ):
        if price <= 0:
            continue
        rises = (kind == "tp" and request.pos_side == "long") or (
            kind == "sl" and request.pos_side == "short"
        )
        body = dict(base, triggerPrice=str(price), triggerDirection=1 if rises else 2)
        if request.client_order_id:
            body["orderLinkId"] = f"{request.client_order_id}-{suffix}"[:36]
        responses.append(client._signed_request("POST", "/v5/order/create", json_body=body))
    if request.trailing_stop_pct > 0:
        body = {
            "category": "linear",
            "symbol": to_bybit_symbol(request.symbol),
            "positionIdx": base["positionIdx"],
            "tpslMode": "Full",
            "trailingStop": str(request.entry_price * request.trailing_stop_pct),
            "activePrice": str(_activation_price(request) or request.entry_price),
        }
        responses.append(client._signed_request("POST", "/v5/position/trading-stop", json_body=body))
    return responses


def _place_gate(client: Any, request: NativeProtectionRequest) -> List[Dict[str, Any]]:
    contract = to_gate_currency_pair(request.symbol)
    close_side = "sell" if request.pos_side == "long" else "buy"
    size, extra_headers = client._resolve_order_size(
        contract=contract, side=close_side, base_size=request.quantity
    )
    initial: Dict[str, Any] = {
        "contract": contract,
        "price": "0",
        "tif": "ioc",
        "reduce_only": True,
    }
    # Gate's price-order schema is stricter than the regular futures-order
    # schema: ``initial.size`` is int64. Fractional-contract orders use the
    # string-typed ``initial.amount`` field instead.
    decimal_size = bool(extra_headers and extra_headers.get("X-Gate-Size-Decimal") == "1")
    if decimal_size:
        initial["amount"] = str(size)
    else:
        try:
            initial["size"] = int(Decimal(str(size)))
        except (ArithmeticError, TypeError, ValueError) as exc:
            raise LiveTradingError(f"Gate native protection resolved an invalid size: {size}") from exc
    responses: List[Dict[str, Any]] = []
    for price in (request.stop_loss_price, request.take_profit_price):
        if price <= 0:
            continue
        rule = 1 if price > request.entry_price else 2
        body = {
            "initial": initial,
            "trigger": {
                "strategy_type": 0,
                "price_type": 1,
                "price": str(price),
                "rule": rule,
                "expiration": 2_592_000,
            },
            "order_type": f"plan-close-{request.pos_side}-position",
        }
        responses.append(
            client._signed_request(
                "POST",
                "/api/v4/futures/usdt/price_orders",
                json_body=body,
                extra_headers=extra_headers,
            )
        )
    if request.trailing_stop_pct > 0:
        responses.append({"managed_by": "strategy_runtime", "type": "trailing_stop"})
    return responses


def _place_htx(client: Any, request: NativeProtectionRequest) -> List[Dict[str, Any]]:
    volume = client._base_to_contracts(symbol=request.symbol, qty=request.quantity)
    body: Dict[str, Any] = {
        "contract_code": to_htx_contract_code(request.symbol),
        "direction": "sell" if request.pos_side == "long" else "buy",
        "volume": volume,
    }
    if request.take_profit_price > 0:
        body.update(
            tp_trigger_price=request.take_profit_price,
            tp_order_price_type="optimal_5",
        )
    if request.stop_loss_price > 0:
        body.update(
            sl_trigger_price=request.stop_loss_price,
            sl_order_price_type="optimal_5",
        )
    responses: List[Dict[str, Any]] = []
    if request.stop_loss_price > 0 or request.take_profit_price > 0:
        path = (
            "/linear-swap-api/v1/swap_tpsl_order"
            if request.margin_mode == "isolated"
            else "/linear-swap-api/v1/swap_cross_tpsl_order"
        )
        raw = client._swap_private_request_raw("POST", path, json_body=body)
        if str(raw.get("status") or "").lower() == "error":
            raise LiveTradingError(f"HTX native protection error: {raw}")
        responses.append(raw)
    if request.trailing_stop_pct > 0:
        responses.append({"managed_by": "strategy_runtime", "type": "trailing_stop"})
    return responses


def _activation_price(request: NativeProtectionRequest) -> float:
    pct = float(request.trailing_activation_pct or 0.0)
    entry = float(request.entry_price or 0.0)
    if pct <= 0 or entry <= 0:
        return 0.0
    return entry * (1.0 + pct if request.pos_side == "long" else 1.0 - pct)
