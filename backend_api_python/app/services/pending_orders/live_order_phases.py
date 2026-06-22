"""Exchange-specific live order phase helpers.

PendingOrderWorker owns the workflow. This module owns the exchange parameter
differences for limit placement, fill polling, cancellation, and market tails.
"""

from __future__ import annotations

from typing import Any, Dict

from app.services.live_trading.base import LiveTradingError
from app.services.live_trading.binance import BinanceFuturesClient
from app.services.live_trading.binance_spot import BinanceSpotClient
from app.services.live_trading.bitget import BitgetMixClient
from app.services.live_trading.bitget_spot import BitgetSpotClient
from app.services.live_trading.bybit import BybitClient
from app.services.live_trading.coinbase_exchange import CoinbaseExchangeClient
from app.services.live_trading.gate import GateSpotClient, GateUsdtFuturesClient
from app.services.live_trading.htx import HtxClient
from app.services.live_trading.kraken import KrakenClient
from app.services.live_trading.kraken_futures import KrakenFuturesClient
from app.services.live_trading.okx import OkxClient
from app.services.live_trading.symbols import to_gate_currency_pair, to_okx_swap_inst_id
from app.services.pending_orders.live_order_support import FillAccumulator


def apply_fill_snapshot(fills: FillAccumulator, snapshot: Dict[str, Any]) -> None:
    fills.apply_fill(float(snapshot.get("filled") or 0.0), float(snapshot.get("avg_price") or 0.0))
    fills.apply_fee(float(snapshot.get("fee") or 0.0), str(snapshot.get("fee_ccy") or ""))


def maker_limit_price(*, ref_price: float, side: str, maker_offset: float) -> float:
    limit_price = float(ref_price or 0.0)
    if limit_price <= 0:
        raise LiveTradingError("missing_ref_price_for_limit_order")
    normalized_side = str(side or "").strip().lower()
    if normalized_side == "buy":
        return limit_price * (1.0 - float(maker_offset or 0.0))
    if normalized_side != "sell":
        raise LiveTradingError(f"unsupported_order_side:{side}")
    return limit_price * (1.0 + float(maker_offset or 0.0))


def place_live_limit_order(
    *,
    client: Any,
    symbol: str,
    side: str,
    amount: float,
    price: float,
    reduce_only: bool,
    pos_side: str,
    client_order_id: str,
    market_type: str,
    payload: Dict[str, Any],
    exchange_config: Dict[str, Any],
    leverage: float,
    order_mode: str,
) -> Any:
    if isinstance(client, BinanceFuturesClient):
        return client.place_limit_order(
            symbol=str(symbol),
            side="BUY" if side == "buy" else "SELL",
            quantity=amount,
            price=price,
            reduce_only=reduce_only,
            position_side=pos_side,
            client_order_id=client_order_id,
        )
    if isinstance(client, BinanceSpotClient):
        return client.place_limit_order(
            symbol=str(symbol),
            side="BUY" if side == "buy" else "SELL",
            quantity=amount,
            price=price,
            client_order_id=client_order_id,
        )
    if isinstance(client, OkxClient):
        td_mode = str(payload.get("margin_mode") or payload.get("td_mode") or "cross")
        if market_type == "swap":
            try:
                client.set_leverage(inst_id=to_okx_swap_inst_id(str(symbol)), lever=leverage, mgn_mode=td_mode, pos_side=pos_side)
            except Exception:
                pass
        return client.place_limit_order(
            market_type=market_type,
            symbol=str(symbol),
            side=side,
            size=amount,
            price=price,
            pos_side=pos_side,
            td_mode=td_mode,
            reduce_only=reduce_only,
            client_order_id=client_order_id,
        )
    if isinstance(client, BitgetMixClient):
        product_type = str(exchange_config.get("product_type") or exchange_config.get("productType") or "USDT-FUTURES")
        margin_coin = str(exchange_config.get("margin_coin") or exchange_config.get("marginCoin") or "USDT")
        margin_mode = str(
            payload.get("margin_mode")
            or payload.get("marginMode")
            or exchange_config.get("margin_mode")
            or exchange_config.get("marginMode")
            or "cross"
        )
        try:
            if market_type == "swap":
                client.set_leverage(
                    symbol=str(symbol),
                    leverage=leverage,
                    margin_coin=margin_coin,
                    product_type=product_type,
                    margin_mode=margin_mode,
                    hold_side=pos_side,
                )
        except Exception:
            pass
        return client.place_limit_order(
            symbol=str(symbol),
            side=side,
            size=amount,
            price=price,
            margin_coin=margin_coin,
            product_type=product_type,
            margin_mode=margin_mode,
            reduce_only=reduce_only,
            post_only=(order_mode in ("maker", "maker_then_market", "limit_first", "limit")),
            client_order_id=client_order_id,
            hold_side=pos_side or ("long" if side == "buy" else "short"),
        )
    if isinstance(client, BitgetSpotClient):
        return client.place_limit_order(symbol=str(symbol), side=side, size=amount, price=price, client_order_id=client_order_id)
    if isinstance(client, BybitClient):
        return client.place_limit_order(
            symbol=str(symbol),
            side=side,
            qty=amount,
            price=price,
            reduce_only=reduce_only,
            pos_side=pos_side,
            client_order_id=client_order_id,
        )
    if isinstance(client, CoinbaseExchangeClient):
        return client.place_limit_order(symbol=str(symbol), side=side, size=amount, price=price, client_order_id=client_order_id)
    if isinstance(client, KrakenClient):
        return client.place_limit_order(symbol=str(symbol), side=side, size=amount, price=price, client_order_id=client_order_id)
    if isinstance(client, KrakenFuturesClient):
        return client.place_limit_order(
            symbol=str(symbol),
            side=side,
            size=amount,
            price=price,
            reduce_only=reduce_only,
            post_only=(order_mode in ("maker", "maker_then_market", "limit_first", "limit")),
            client_order_id=client_order_id,
        )
    if isinstance(client, GateSpotClient):
        return client.place_limit_order(symbol=str(symbol), side=side, size=amount, price=price, client_order_id=client_order_id)
    if isinstance(client, GateUsdtFuturesClient):
        try:
            client.set_leverage(contract=to_gate_currency_pair(str(symbol)), leverage=leverage)
        except Exception:
            pass
        return client.place_limit_order(
            symbol=str(symbol),
            side=side,
            size=amount,
            price=price,
            reduce_only=reduce_only,
            client_order_id=client_order_id,
        )
    if isinstance(client, HtxClient):
        if market_type == "swap":
            try:
                client.set_leverage(symbol=str(symbol), leverage=leverage)
            except Exception:
                pass
        return client.place_limit_order(
            symbol=str(symbol),
            side=side,
            size=amount,
            price=price,
            reduce_only=reduce_only,
            pos_side=pos_side,
            client_order_id=client_order_id,
        )
    raise LiveTradingError(f"Unsupported client type: {type(client)}")


def wait_live_order_fill(
    *,
    client: Any,
    symbol: str,
    order_id: str,
    client_order_id: str,
    market_type: str,
    exchange_config: Dict[str, Any],
    max_wait_sec: float,
    phase: str,
) -> Dict[str, Any]:
    wait_sec = float(max_wait_sec or 0.0)
    if phase == "market":
        wait_sec = 5.0 if isinstance(client, (BinanceFuturesClient, BinanceSpotClient)) else 12.0
    elif isinstance(client, (BitgetMixClient, BitgetSpotClient, GateSpotClient, GateUsdtFuturesClient)):
        wait_sec = max(wait_sec, 8.0)

    if isinstance(client, (BinanceFuturesClient, BinanceSpotClient)):
        return client.wait_for_fill(symbol=str(symbol), order_id=order_id, client_order_id=client_order_id, max_wait_sec=wait_sec)
    if isinstance(client, OkxClient):
        return client.wait_for_fill(
            symbol=str(symbol),
            ord_id=order_id,
            cl_ord_id=client_order_id,
            market_type=market_type,
            max_wait_sec=wait_sec,
        )
    if isinstance(client, BitgetMixClient):
        product_type = str(exchange_config.get("product_type") or exchange_config.get("productType") or "USDT-FUTURES")
        return client.wait_for_fill(symbol=str(symbol), product_type=product_type, order_id=order_id, client_oid=client_order_id, max_wait_sec=wait_sec)
    if isinstance(client, BitgetSpotClient):
        return client.wait_for_fill(symbol=str(symbol), order_id=order_id, client_order_id=client_order_id, max_wait_sec=wait_sec)
    if isinstance(client, BybitClient):
        return client.wait_for_fill(symbol=str(symbol), order_id=order_id, client_order_id=client_order_id, max_wait_sec=wait_sec)
    if isinstance(client, CoinbaseExchangeClient):
        return client.wait_for_fill(order_id=order_id, client_order_id=client_order_id, max_wait_sec=wait_sec)
    if isinstance(client, KrakenClient):
        return client.wait_for_fill(order_id=order_id, max_wait_sec=wait_sec)
    if isinstance(client, KrakenFuturesClient):
        return client.wait_for_fill(order_id=order_id, client_order_id=client_order_id, max_wait_sec=wait_sec)
    if isinstance(client, GateSpotClient):
        return client.wait_for_fill(order_id=order_id, max_wait_sec=wait_sec)
    if isinstance(client, GateUsdtFuturesClient):
        return client.wait_for_fill(order_id=order_id, contract=to_gate_currency_pair(str(symbol)), max_wait_sec=wait_sec)
    if isinstance(client, HtxClient):
        return client.wait_for_fill(symbol=str(symbol), order_id=order_id, client_order_id=client_order_id, max_wait_sec=wait_sec)
    raise LiveTradingError(f"Unsupported client type: {type(client)}")


def cancel_live_limit_order(
    *,
    client: Any,
    symbol: str,
    order_id: str,
    client_order_id: str,
    market_type: str,
    exchange_config: Dict[str, Any],
) -> Any:
    if isinstance(client, BinanceFuturesClient):
        return client.cancel_order(symbol=str(symbol), order_id=order_id, client_order_id=client_order_id)
    if isinstance(client, BinanceSpotClient):
        return client.cancel_order(symbol=str(symbol), order_id=order_id, client_order_id=client_order_id)
    if isinstance(client, OkxClient):
        return client.cancel_order(market_type=market_type, symbol=str(symbol), ord_id=order_id, cl_ord_id=client_order_id)
    if isinstance(client, BitgetMixClient):
        product_type = str(exchange_config.get("product_type") or exchange_config.get("productType") or "USDT-FUTURES")
        margin_coin = str(exchange_config.get("margin_coin") or exchange_config.get("marginCoin") or "USDT")
        return client.cancel_order(symbol=str(symbol), product_type=product_type, margin_coin=margin_coin, order_id=order_id, client_oid=client_order_id)
    if isinstance(client, BitgetSpotClient):
        return client.cancel_order(symbol=str(symbol), client_order_id=client_order_id)
    if isinstance(client, BybitClient):
        return client.cancel_order(symbol=str(symbol), order_id=order_id, client_order_id=client_order_id)
    if isinstance(client, CoinbaseExchangeClient):
        return client.cancel_order(order_id=order_id, client_order_id=client_order_id)
    if isinstance(client, KrakenClient):
        return client.cancel_order(order_id=order_id)
    if isinstance(client, KrakenFuturesClient):
        return client.cancel_order(order_id=order_id, client_order_id=client_order_id)
    if isinstance(client, GateSpotClient):
        return client.cancel_order(order_id=order_id)
    if isinstance(client, GateUsdtFuturesClient):
        return client.cancel_order(order_id=order_id)
    if isinstance(client, HtxClient):
        return client.cancel_order(symbol=str(symbol), order_id=order_id, client_order_id=client_order_id)
    return None


def apply_okx_tail_guard(
    *,
    client: Any,
    symbol: str,
    remaining: float,
    market_type: str,
    phases: Dict[str, Any],
) -> float:
    if remaining <= 0 or not isinstance(client, OkxClient) or market_type != "swap":
        return remaining
    try:
        inst_id = to_okx_swap_inst_id(str(symbol))
        inst = client.get_instrument(inst_type="SWAP", inst_id=inst_id) or {}
        lot_sz = float(inst.get("lotSz") or 0.0)
        min_sz = float(inst.get("minSz") or 0.0)
        ct_val = float(inst.get("ctVal") or 0.0)
        min_contract = min_sz if min_sz > 0 else (lot_sz if lot_sz > 0 else 0.0)
        min_base = (min_contract * ct_val) if (min_contract > 0 and ct_val > 0) else 0.0
        if min_base > 0 and remaining < (min_base * 0.999999):
            phases["tail_guard"] = {
                "exchange": "okx",
                "inst_id": inst_id,
                "remaining": remaining,
                "min_base": min_base,
            }
            return 0.0
    except Exception:
        pass
    return remaining


def place_live_market_order(
    *,
    client: Any,
    symbol: str,
    side: str,
    amount: float,
    reduce_only: bool,
    pos_side: str,
    client_order_id: str,
    market_type: str,
    payload: Dict[str, Any],
    exchange_config: Dict[str, Any],
    leverage: float,
    ref_price: float,
    spot_quote_amt: float,
    spot_market_buy_uses_quote: bool,
) -> Any:
    if isinstance(client, BinanceFuturesClient):
        return client.place_market_order(
            symbol=str(symbol),
            side="BUY" if side == "buy" else "SELL",
            quantity=amount,
            reduce_only=reduce_only,
            position_side=pos_side,
            client_order_id=client_order_id,
        )
    if isinstance(client, BinanceSpotClient):
        return client.place_market_order(
            symbol=str(symbol),
            side="BUY" if side == "buy" else "SELL",
            quantity=amount,
            client_order_id=client_order_id,
        )
    if isinstance(client, OkxClient):
        td_mode = str(payload.get("margin_mode") or payload.get("td_mode") or "cross")
        if market_type == "swap":
            try:
                client.set_leverage(inst_id=to_okx_swap_inst_id(str(symbol)), lever=leverage, mgn_mode=td_mode, pos_side=pos_side)
            except Exception:
                pass
        return client.place_market_order(
            symbol=str(symbol),
            side=side,
            size=amount,
            market_type=market_type,
            pos_side=pos_side,
            td_mode=td_mode,
            reduce_only=reduce_only,
            client_order_id=client_order_id,
        )
    if isinstance(client, BitgetMixClient):
        product_type = str(exchange_config.get("product_type") or exchange_config.get("productType") or "USDT-FUTURES")
        margin_coin = str(exchange_config.get("margin_coin") or exchange_config.get("marginCoin") or "USDT")
        margin_mode = str(
            payload.get("margin_mode")
            or payload.get("marginMode")
            or exchange_config.get("margin_mode")
            or exchange_config.get("marginMode")
            or "cross"
        )
        try:
            if market_type == "swap":
                client.set_leverage(
                    symbol=str(symbol),
                    leverage=leverage,
                    margin_coin=margin_coin,
                    product_type=product_type,
                    margin_mode=margin_mode,
                    hold_side=pos_side,
                )
        except Exception:
            pass
        return client.place_market_order(
            symbol=str(symbol),
            side=side,
            size=amount,
            margin_coin=margin_coin,
            product_type=product_type,
            margin_mode=margin_mode,
            reduce_only=reduce_only,
            client_order_id=client_order_id,
            hold_side=pos_side or ("long" if side == "buy" else "short"),
        )
    if isinstance(client, BitgetSpotClient):
        mkt_size = spot_quote_amt if (side == "buy" and spot_market_buy_uses_quote and spot_quote_amt > 0) else amount
        if side == "buy" and mkt_size <= 0 and ref_price > 0:
            mkt_size = amount * ref_price
        return client.place_market_order(symbol=str(symbol), side=side, size=mkt_size, client_order_id=client_order_id)
    if isinstance(client, BybitClient):
        return client.place_market_order(
            symbol=str(symbol),
            side=side,
            qty=amount,
            reduce_only=reduce_only,
            pos_side=pos_side,
            client_order_id=client_order_id,
        )
    if isinstance(client, CoinbaseExchangeClient):
        return client.place_market_order(symbol=str(symbol), side=side, size=amount, client_order_id=client_order_id)
    if isinstance(client, KrakenClient):
        return client.place_market_order(symbol=str(symbol), side=side, size=amount, client_order_id=client_order_id)
    if isinstance(client, KrakenFuturesClient):
        return client.place_market_order(symbol=str(symbol), side=side, size=amount, reduce_only=reduce_only, client_order_id=client_order_id)
    if isinstance(client, GateSpotClient):
        mkt_size = spot_quote_amt if (side == "buy" and spot_market_buy_uses_quote and spot_quote_amt > 0) else amount
        if side == "buy" and mkt_size <= 0 and ref_price > 0:
            mkt_size = amount * ref_price
        return client.place_market_order(symbol=str(symbol), side=side, size=mkt_size, client_order_id=client_order_id)
    if isinstance(client, GateUsdtFuturesClient):
        try:
            client.set_leverage(contract=to_gate_currency_pair(str(symbol)), leverage=leverage)
        except Exception:
            pass
        return client.place_market_order(
            symbol=str(symbol),
            side=side,
            size=amount,
            reduce_only=reduce_only,
            client_order_id=client_order_id,
        )
    if isinstance(client, HtxClient):
        if market_type == "swap":
            try:
                client.set_leverage(symbol=str(symbol), leverage=leverage)
            except Exception:
                pass
        return client.place_market_order(
            symbol=str(symbol),
            side=side,
            qty=amount,
            reduce_only=reduce_only,
            pos_side=pos_side,
            client_order_id=client_order_id,
        )
    raise LiveTradingError(f"Unsupported client type: {type(client)}")
