"""Unified limit-order placement / query for all live_trading clients."""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from app.services.live_trading.base import BaseRestClient, LiveOrderResult, LiveTradingError
from app.services.live_trading.binance import BinanceFuturesClient
from app.services.live_trading.binance_spot import BinanceSpotClient
from app.services.live_trading.bitget import BitgetMixClient
from app.services.live_trading.bitget_spot import BitgetSpotClient
from app.services.live_trading.bybit import BybitClient
from app.services.live_trading.coinbase_exchange import CoinbaseExchangeClient
from app.services.live_trading.deepcoin import DeepcoinClient
from app.services.live_trading.gate import GateSpotClient, GateUsdtFuturesClient, to_gate_currency_pair
from app.services.live_trading.htx import HtxClient
from app.services.live_trading.kraken import KrakenClient
from app.services.live_trading.kraken_futures import KrakenFuturesClient
from app.services.live_trading.kucoin import KucoinFuturesClient, KucoinSpotClient
from app.services.live_trading.okx import OkxClient, to_okx_swap_inst_id
from app.utils.logger import get_logger

logger = get_logger(__name__)


def make_grid_client_order_id(strategy_id: int, cell_index: int, purpose: str) -> str:
    """Short client oid (OKX max 32). purpose: e/l/x/s = entry long/exit/short entry."""
    p = (purpose or "x")[:1].lower()
    if "long_entry" in purpose:
        p = "e"
    elif "long_exit" in purpose:
        p = "x"
    elif "short_entry" in purpose:
        p = "s"
    elif "short_exit" in purpose:
        p = "c"
    ts = int(__import__("time").time()) % 1000000
    return f"g{int(strategy_id) % 10000:04d}c{int(cell_index):03d}{p}{ts % 99999:05d}"[:32]


def place_grid_limit_order(
    client: BaseRestClient,
    *,
    symbol: str,
    side: str,
    quantity: float,
    price: float,
    market_type: str,
    exchange_config: Dict[str, Any],
    pos_side: str = "",
    reduce_only: bool = False,
    client_order_id: Optional[str] = None,
    leverage: float = 1.0,
    margin_mode: str = "cross",
    post_only: bool = True,
) -> LiveOrderResult:
    sd = str(side or "").strip().lower()
    if sd not in ("buy", "sell"):
        raise LiveTradingError(f"Invalid side: {side}")
    qty = float(quantity or 0)
    px = float(price or 0)
    if qty <= 0 or px <= 0:
        raise LiveTradingError("Invalid grid limit qty/price")

    mt = str(market_type or "swap").strip().lower()
    if mt in ("futures", "future", "perp", "perpetual"):
        mt = "swap"
    ex_cfg = exchange_config if isinstance(exchange_config, dict) else {}
    coid = str(client_order_id or "")

    if isinstance(client, BinanceFuturesClient):
        return client.place_limit_order(
            symbol=str(symbol),
            side="BUY" if sd == "buy" else "SELL",
            quantity=qty,
            price=px,
            reduce_only=reduce_only,
            position_side=pos_side or ("long" if sd == "buy" else "short"),
            client_order_id=coid or None,
        )
    if isinstance(client, BinanceSpotClient):
        return client.place_limit_order(
            symbol=str(symbol),
            side="BUY" if sd == "buy" else "SELL",
            quantity=qty,
            price=px,
            client_order_id=coid or None,
        )
    if isinstance(client, OkxClient):
        if mt == "swap":
            try:
                inst_id = to_okx_swap_inst_id(str(symbol))
                client.set_leverage(
                    inst_id=inst_id,
                    lever=float(leverage or 1),
                    mgn_mode=str(margin_mode or "cross"),
                    pos_side=pos_side or ("long" if sd == "buy" and not reduce_only else "short"),
                )
            except Exception:
                pass
        return client.place_limit_order(
            market_type=mt,
            symbol=str(symbol),
            side=sd,
            size=qty,
            price=px,
            pos_side=pos_side or ("long" if sd == "buy" else "short"),
            td_mode=str(margin_mode or "cross"),
            reduce_only=reduce_only,
            client_order_id=coid or None,
        )
    if isinstance(client, BitgetMixClient):
        product_type = str(ex_cfg.get("product_type") or ex_cfg.get("productType") or "USDT-FUTURES")
        margin_coin = str(ex_cfg.get("margin_coin") or ex_cfg.get("marginCoin") or "USDT")
        mm = str(margin_mode or ex_cfg.get("margin_mode") or "cross")
        try:
            if mt == "swap":
                client.set_leverage(
                    symbol=str(symbol),
                    leverage=float(leverage or 1),
                    margin_coin=margin_coin,
                    product_type=product_type,
                    margin_mode=mm,
                    hold_side=pos_side or "long",
                )
        except Exception:
            pass
        return client.place_limit_order(
            symbol=str(symbol),
            side=sd,
            size=qty,
            price=px,
            margin_coin=margin_coin,
            product_type=product_type,
            margin_mode=mm,
            reduce_only=reduce_only,
            post_only=post_only,
            client_order_id=coid or None,
        )
    if isinstance(client, BitgetSpotClient):
        return client.place_limit_order(
            symbol=str(symbol), side=sd, size=qty, price=px, client_order_id=coid or None
        )
    if isinstance(client, BybitClient):
        return client.place_limit_order(
            symbol=str(symbol),
            side=sd,
            qty=qty,
            price=px,
            reduce_only=reduce_only,
            pos_side=pos_side or ("long" if sd == "buy" else "short"),
            client_order_id=coid or None,
        )
    if isinstance(client, CoinbaseExchangeClient):
        return client.place_limit_order(
            symbol=str(symbol), side=sd, size=qty, price=px, client_order_id=coid or None
        )
    if isinstance(client, KrakenClient):
        return client.place_limit_order(
            symbol=str(symbol), side=sd, size=qty, price=px, client_order_id=coid or None
        )
    if isinstance(client, KrakenFuturesClient):
        return client.place_limit_order(
            symbol=str(symbol),
            side=sd,
            size=qty,
            price=px,
            reduce_only=reduce_only,
            post_only=post_only,
            client_order_id=coid or None,
        )
    if isinstance(client, KucoinSpotClient):
        return client.place_limit_order(
            symbol=str(symbol), side=sd, size=qty, price=px, client_order_id=coid or None
        )
    if isinstance(client, KucoinFuturesClient):
        try:
            if mt == "swap":
                client.set_leverage(symbol=str(symbol), leverage=float(leverage or 1))
        except Exception:
            pass
        return client.place_limit_order(
            symbol=str(symbol),
            side=sd,
            size=qty,
            price=px,
            reduce_only=reduce_only,
            post_only=post_only,
            client_order_id=coid or None,
        )
    if isinstance(client, GateSpotClient):
        return client.place_limit_order(
            symbol=str(symbol), side=sd, size=qty, price=px, client_order_id=coid or None
        )
    if isinstance(client, GateUsdtFuturesClient):
        try:
            client.set_leverage(contract=to_gate_currency_pair(str(symbol)), leverage=float(leverage or 1))
        except Exception:
            pass
        return client.place_limit_order(
            symbol=str(symbol),
            side=sd,
            size=qty,
            price=px,
            reduce_only=reduce_only,
            client_order_id=coid or None,
        )
    if isinstance(client, DeepcoinClient):
        return client.place_limit_order(
            symbol=str(symbol),
            side=sd,
            qty=qty,
            price=px,
            reduce_only=reduce_only,
            pos_side=pos_side or ("long" if sd == "buy" else "short"),
            client_order_id=coid or None,
        )
    if isinstance(client, HtxClient):
        try:
            if mt == "swap":
                client.set_leverage(symbol=str(symbol), leverage=float(leverage or 1))
        except Exception:
            pass
        return client.place_limit_order(
            symbol=str(symbol),
            side=sd,
            size=qty,
            price=px,
            reduce_only=reduce_only,
            pos_side=pos_side or ("long" if sd == "buy" else "short"),
            client_order_id=coid or None,
        )
    raise LiveTradingError(f"Unsupported client for grid limit: {type(client)}")


def cancel_grid_order(
    client: BaseRestClient,
    *,
    symbol: str,
    market_type: str,
    exchange_order_id: str = "",
    client_order_id: str = "",
) -> None:
    mt = str(market_type or "swap").strip().lower()
    if isinstance(client, OkxClient):
        client.cancel_order(
            market_type=mt,
            symbol=str(symbol),
            ord_id=str(exchange_order_id or ""),
            cl_ord_id=str(client_order_id or ""),
        )
        return
    if hasattr(client, "cancel_order"):
        kwargs: Dict[str, Any] = {}
        if symbol and "symbol" in client.cancel_order.__code__.co_varnames:
            kwargs["symbol"] = symbol
        if exchange_order_id:
            kwargs["order_id"] = exchange_order_id
        if client_order_id:
            kwargs["client_order_id"] = client_order_id
        client.cancel_order(**kwargs)


def query_grid_order_fill(
    client: BaseRestClient,
    *,
    symbol: str,
    market_type: str,
    exchange_order_id: str = "",
    client_order_id: str = "",
) -> Tuple[float, float, str]:
    """
    Returns (filled_qty, avg_price, status).
    status: open | partial | filled | cancelled | unknown
    """
    mt = str(market_type or "swap").strip().lower()
    try:
        if isinstance(client, OkxClient):
            q = client.get_order(
                market_type=mt,
                symbol=str(symbol),
                ord_id=str(exchange_order_id or ""),
                cl_ord_id=str(client_order_id or ""),
            )
            data = (q.get("data") or [{}])[0] if isinstance(q, dict) else {}
            state = str(data.get("state") or "").lower()
            filled = float(data.get("accFillSz") or data.get("fillSz") or 0)
            avg = float(data.get("avgPx") or 0)
            st = "filled" if state == "filled" else ("cancelled" if state == "canceled" else "open")
            if filled > 0 and st == "open":
                st = "partial"
            return filled, avg, st
        if isinstance(client, BinanceFuturesClient):
            q = client.get_order(symbol=str(symbol), order_id=str(exchange_order_id or ""), client_order_id=str(client_order_id or ""))
            filled = float(q.get("executedQty") or 0)
            avg = float(q.get("avgPrice") or 0)
            st_raw = str(q.get("status") or "").upper()
            if st_raw == "FILLED":
                return filled, avg, "filled"
            if st_raw in ("CANCELED", "EXPIRED", "REJECTED"):
                return filled, avg, "cancelled"
            return filled, avg, "partial" if filled > 0 else "open"
        if hasattr(client, "get_order"):
            q = client.get_order(
                symbol=str(symbol),
                order_id=str(exchange_order_id or ""),
                client_order_id=str(client_order_id or ""),
            )
            if isinstance(q, dict):
                filled = float(q.get("filled") or q.get("filledSize") or q.get("executedQty") or 0)
                avg = float(q.get("avg_price") or q.get("avgPrice") or q.get("price") or 0)
                st = str(q.get("status") or q.get("state") or "open").lower()
                if "fill" in st and "partial" not in st:
                    return filled, avg, "filled"
                if "cancel" in st:
                    return filled, avg, "cancelled"
                return filled, avg, "partial" if filled > 0 else "open"
    except Exception as e:
        logger.debug("query_grid_order_fill: %s", e)
    return 0.0, 0.0, "unknown"
