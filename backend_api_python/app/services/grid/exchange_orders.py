"""Unified limit-order placement / query for all live_trading clients."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.services.grid.fill_units import parse_grid_order_fill
from app.services.live_trading.base import BaseRestClient, LiveOrderResult, LiveTradingError
from app.services.live_trading.binance import BinanceFuturesClient
from app.services.live_trading.binance_spot import BinanceSpotClient
from app.services.live_trading.bitget import BitgetMixClient
from app.services.live_trading.bitget_spot import BitgetSpotClient
from app.services.live_trading.bybit import BybitClient
from app.services.live_trading.coinbase_exchange import CoinbaseExchangeClient
from app.services.live_trading.gate import GateSpotClient, GateUsdtFuturesClient, to_gate_currency_pair
from app.services.live_trading.htx import HtxClient
from app.services.live_trading.kraken import KrakenClient
from app.services.live_trading.kraken_futures import KrakenFuturesClient
from app.services.live_trading.okx import OkxClient
from app.services.live_trading.symbols import to_okx_spot_inst_id, to_okx_swap_inst_id
from app.utils.logger import get_logger

logger = get_logger(__name__)


def make_grid_initial_client_order_id(strategy_id: int, leg: str = "") -> str:
    """Stable client oid for grid initial market leg (one per strategy/leg, avoids duplicate opens)."""
    suffix = str(leg or "").strip().lower()[:1]
    if suffix not in ("l", "s"):
        suffix = ""
    return f"ginit{int(strategy_id) % 100000:05d}{suffix}"[:32]


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
            hold_side=pos_side or ("long" if sd == "buy" else "short"),
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


def wait_grid_market_fill(
    client: BaseRestClient,
    *,
    symbol: str,
    market_type: str,
    exchange_config: Dict[str, Any],
    exchange_order_id: str = "",
    client_order_id: str = "",
    max_wait_sec: float = 15.0,
) -> Tuple[float, float]:
    """Poll until market order fill is known. Returns (filled_qty, avg_price)."""
    mt = str(market_type or "swap").strip().lower()
    ex_cfg = exchange_config if isinstance(exchange_config, dict) else {}
    ex_oid = str(exchange_order_id or "")
    coid = str(client_order_id or "")

    try:
        if isinstance(client, BitgetMixClient) and hasattr(client, "wait_for_fill"):
            product_type = str(ex_cfg.get("product_type") or ex_cfg.get("productType") or "USDT-FUTURES")
            q = client.wait_for_fill(
                symbol=str(symbol),
                product_type=product_type,
                order_id=ex_oid,
                client_oid=coid,
                max_wait_sec=max_wait_sec,
            )
            return float(q.get("filled") or 0), float(q.get("avg_price") or 0)
        if isinstance(client, BitgetSpotClient) and hasattr(client, "wait_for_fill"):
            q = client.wait_for_fill(
                symbol=str(symbol),
                order_id=ex_oid,
                client_order_id=coid,
                max_wait_sec=max_wait_sec,
            )
            return float(q.get("filled") or 0), float(q.get("avg_price") or 0)
        if isinstance(client, GateUsdtFuturesClient) and hasattr(client, "wait_for_fill"):
            contract = to_gate_currency_pair(str(symbol))
            q = client.wait_for_fill(
                order_id=ex_oid,
                contract=contract,
                max_wait_sec=max_wait_sec,
            )
            return float(q.get("filled") or 0), float(q.get("avg_price") or 0)
        if hasattr(client, "wait_for_fill"):
            kwargs: Dict[str, Any] = {
                "max_wait_sec": max_wait_sec,
            }
            if ex_oid:
                kwargs["order_id"] = ex_oid
            if coid:
                kwargs["client_order_id"] = coid
            if "symbol" in client.wait_for_fill.__code__.co_varnames:
                kwargs["symbol"] = str(symbol)
            if isinstance(client, OkxClient):
                kwargs["ord_id"] = ex_oid
                kwargs["cl_ord_id"] = coid
                kwargs["market_type"] = mt
            q = client.wait_for_fill(**kwargs)
            if isinstance(q, dict):
                filled = float(q.get("filled") or 0)
                avg = float(q.get("avg_price") or 0)
                if filled <= 0:
                    try:
                        raw = _fetch_grid_client_order(
                            client,
                            symbol=str(symbol),
                            market_type=str(market_type or "swap"),
                            exchange_order_id=ex_oid,
                            client_order_id=coid,
                            exchange_config=ex_cfg,
                        )
                        if isinstance(raw, dict) and raw:
                            filled, avg, _ = parse_grid_order_fill(
                                client,
                                symbol=str(symbol),
                                market_type=str(market_type or "swap"),
                                exchange_config=ex_cfg,
                                data=raw,
                            )
                    except Exception as e:
                        logger.debug("wait_grid_market_fill fallback parse: %s", e)
                return filled, avg
    except Exception as e:
        logger.warning("wait_grid_market_fill: %s", e)
    return 0.0, 0.0


def execute_grid_market_order(
    client: BaseRestClient,
    *,
    symbol: str,
    signal_type: str,
    quantity: float,
    market_type: str,
    exchange_config: Dict[str, Any],
    leverage: float = 1.0,
    max_wait_sec: float = 15.0,
    client_order_id: Optional[str] = None,
) -> Tuple[bool, float, float]:
    """
    Place a synchronous market order for grid initial/risk paths.

    Returns (filled_ok, filled_qty, avg_price). ``filled_ok`` is True only when
    exchange reports a non-zero fill (not merely order accepted).
    """
    from app.services.live_trading.execution import place_order_from_signal

    sig = str(signal_type or "").strip().lower()
    qty = float(quantity or 0)
    if qty <= 0 and not sig.startswith("close_"):
        return False, 0.0, 0.0

    coid = str(client_order_id or "").strip()
    if not coid:
        ts = int(__import__("time").time()) % 100000000
        coid = f"ginit{ts}"[:32]
    mt = str(market_type or "swap").strip().lower()
    try:
        if isinstance(client, BitgetMixClient) and mt == "swap":
            ex_cfg = exchange_config if isinstance(exchange_config, dict) else {}
            product_type = str(ex_cfg.get("product_type") or ex_cfg.get("productType") or "USDT-FUTURES")
            margin_coin = str(ex_cfg.get("margin_coin") or ex_cfg.get("marginCoin") or "USDT")
            margin_mode = str(ex_cfg.get("margin_mode") or ex_cfg.get("marginMode") or "cross")
            pos_side = "long" if sig in ("open_long", "close_long", "add_long", "reduce_long") else "short"
            try:
                client.set_leverage(
                    symbol=str(symbol),
                    leverage=float(leverage or 1),
                    margin_coin=margin_coin,
                    product_type=product_type,
                    margin_mode=margin_mode,
                    hold_side=pos_side,
                )
            except Exception:
                pass
        res = place_order_from_signal(
            client,
            signal_type=sig,
            symbol=str(symbol),
            amount=qty,
            market_type=str(market_type or "swap"),
            exchange_config=exchange_config if isinstance(exchange_config, dict) else {},
            client_order_id=coid,
        )
    except Exception as e:
        logger.warning("execute_grid_market_order place failed: %s", e)
        return False, 0.0, 0.0

    ex_oid = str(getattr(res, "exchange_order_id", None) or "")
    filled, avg = wait_grid_market_fill(
        client,
        symbol=str(symbol),
        market_type=str(market_type or "swap"),
        exchange_config=exchange_config if isinstance(exchange_config, dict) else {},
        exchange_order_id=ex_oid,
        client_order_id=coid,
        max_wait_sec=max_wait_sec,
    )
    return filled > 0, filled, avg


def cancel_grid_order(
    client: BaseRestClient,
    *,
    symbol: str,
    market_type: str,
    exchange_order_id: str = "",
    client_order_id: str = "",
    exchange_config: Optional[Dict[str, Any]] = None,
) -> None:
    mt = str(market_type or "swap").strip().lower()
    ex_cfg = exchange_config if isinstance(exchange_config, dict) else {}
    if isinstance(client, OkxClient):
        client.cancel_order(
            market_type=mt,
            symbol=str(symbol),
            ord_id=str(exchange_order_id or ""),
            cl_ord_id=str(client_order_id or ""),
        )
        return
    if isinstance(client, BitgetMixClient):
        product_type = str(ex_cfg.get("product_type") or ex_cfg.get("productType") or "USDT-FUTURES")
        margin_coin = str(ex_cfg.get("margin_coin") or ex_cfg.get("marginCoin") or "USDT")
        client.cancel_order(
            symbol=str(symbol),
            product_type=product_type,
            margin_coin=margin_coin,
            order_id=str(exchange_order_id or ""),
            client_oid=str(client_order_id or ""),
        )
        return
    if isinstance(client, BitgetSpotClient):
        client.cancel_order(
            symbol=str(symbol),
            order_id=str(exchange_order_id or ""),
            client_order_id=str(client_order_id or ""),
        )
        return
    if hasattr(client, "cancel_order"):
        kwargs: Dict[str, Any] = {}
        if symbol and "symbol" in client.cancel_order.__code__.co_varnames:
            kwargs["symbol"] = symbol
        if exchange_order_id:
            kwargs["order_id"] = exchange_order_id
        if client_order_id:
            if "client_oid" in client.cancel_order.__code__.co_varnames:
                kwargs["client_oid"] = client_order_id
            else:
                kwargs["client_order_id"] = client_order_id
        client.cancel_order(**kwargs)


def _unwrap_client_order_payload(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    data = raw.get("data")
    if isinstance(data, dict):
        return data
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0]
    return raw


def _float(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _extract_order_id_from_payload(data: Dict[str, Any], fallback: str = "") -> str:
    if not isinstance(data, dict):
        return str(fallback or "")
    for src in (data, data.get("raw") if isinstance(data.get("raw"), dict) else {}):
        if not isinstance(src, dict):
            continue
        row = src.get("data") if isinstance(src.get("data"), dict) else src
        if isinstance(row, dict):
            oid = str(row.get("orderId") or row.get("order_id") or "").strip()
            if oid:
                return oid
    return str(fallback or "")


def _bitget_contract_size(client: BitgetMixClient, symbol: str, exchange_config: Dict[str, Any]) -> float:
    product_type = str(exchange_config.get("product_type") or exchange_config.get("productType") or "USDT-FUTURES")
    try:
        contract = client.get_contract(symbol=str(symbol), product_type=product_type) or {}
        ct = _float(contract.get("contractSize") or contract.get("contractSz") or contract.get("ctVal"))
        return ct if ct > 0 else 1.0
    except Exception:
        return 1.0


def _unwrap_bitget_fills(raw: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return []
    data = raw.get("data")
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        rows = data.get("fillList") or data.get("fills") or data.get("list") or []
        if isinstance(rows, list):
            return [x for x in rows if isinstance(x, dict)]
    return []


def _fetch_bitget_grid_fills(
    client: BaseRestClient,
    *,
    symbol: str,
    market_type: str,
    exchange_order_id: str,
    exchange_config: Dict[str, Any],
) -> Dict[str, Any]:
    oid = str(exchange_order_id or "").strip()
    if not oid:
        return {}
    if isinstance(client, BitgetMixClient):
        product_type = str(exchange_config.get("product_type") or exchange_config.get("productType") or "USDT-FUTURES")
        return client.get_order_fills(symbol=str(symbol), product_type=product_type, order_id=oid)
    if isinstance(client, BitgetSpotClient):
        return client.get_fills(symbol=str(symbol), order_id=oid)
    return {}


def _aggregate_bitget_grid_fills(
    client: BaseRestClient,
    *,
    symbol: str,
    market_type: str,
    exchange_config: Dict[str, Any],
    raw: Dict[str, Any],
) -> Tuple[float, float, str]:
    fills = _unwrap_bitget_fills(raw)
    if not fills:
        return 0.0, 0.0, "unknown"
    mt = str(market_type or "swap").strip().lower()
    if mt in ("futures", "future", "perp", "perpetual"):
        mt = "swap"
    ct = _bitget_contract_size(client, symbol, exchange_config) if isinstance(client, BitgetMixClient) else 1.0

    total_base = 0.0
    total_quote = 0.0
    for f in fills:
        if isinstance(client, BitgetSpotClient):
            qty = _float(f.get("size") or f.get("baseVolume") or f.get("dealSize"))
            px = _float(f.get("priceAvg") or f.get("price") or f.get("fillPrice"))
            amount = _float(f.get("amount") or f.get("quoteVolume"))
        else:
            qty = _float(f.get("baseVolume"))
            if qty <= 0:
                contracts = _float(f.get("size") or f.get("fillSize") or f.get("filledQty"))
                qty = contracts * ct if contracts > 0 and mt == "swap" else contracts
            px = _float(f.get("fillPrice") or f.get("priceAvg") or f.get("price"))
            amount = _float(f.get("quoteVolume") or f.get("amount"))
        if qty <= 0:
            continue
        total_base += qty
        if px > 0:
            total_quote += qty * px
        elif amount > 0:
            total_quote += amount
    if total_base <= 0:
        return 0.0, 0.0, "unknown"
    avg = total_quote / total_base if total_quote > 0 else 0.0
    return total_base, avg, "filled"


def _parse_grid_order_fill(data: Dict[str, Any]) -> Tuple[float, float, str]:
    """Legacy generic parser — prefer parse_grid_order_fill() with client context."""
    if not data:
        return 0.0, 0.0, "unknown"
    from app.services.grid.fill_units import order_status_from_data

    filled = float(
        data.get("baseVolume")
        or data.get("executedQty")
        or data.get("cumExecQty")
        or data.get("filled")
        or data.get("accFillSz")
        or data.get("dealSize")
        or data.get("filled_size")
        or data.get("trade_volume")
        or 0
    )
    avg = float(
        data.get("avgPx")
        or data.get("avgPrice")
        or data.get("avg_price")
        or data.get("fill_price")
        or data.get("trade_avg_price")
        or data.get("price")
        or 0
    )
    if avg <= 0 and data.get("filled_total") and data.get("filled_amount"):
        try:
            filled_amt = float(data.get("filled_amount") or filled or 0)
            filled_total = float(data.get("filled_total") or 0)
            if filled_amt > 0 and filled_total > 0:
                avg = filled_total / filled_amt
                if filled <= 0:
                    filled = filled_amt
        except Exception:
            pass
    return filled, avg, order_status_from_data(data)


def _fetch_grid_client_order(
    client: BaseRestClient,
    *,
    symbol: str,
    market_type: str,
    exchange_order_id: str,
    client_order_id: str,
    exchange_config: Dict[str, Any],
) -> Dict[str, Any]:
    mt = str(market_type or "swap").strip().lower()
    oid = str(exchange_order_id or "")
    coid = str(client_order_id or "")
    if isinstance(client, OkxClient):
        inst_id = to_okx_spot_inst_id(symbol) if mt == "spot" else to_okx_swap_inst_id(symbol)
        return client.get_order(inst_id=inst_id, ord_id=oid, cl_ord_id=coid)
    if isinstance(client, (BinanceFuturesClient, BinanceSpotClient)):
        return client.get_order(symbol=str(symbol), order_id=oid, client_order_id=coid)
    if isinstance(client, BitgetMixClient):
        product_type = str(
            exchange_config.get("product_type") or exchange_config.get("productType") or "USDT-FUTURES"
        )
        return client.get_order(
            symbol=str(symbol),
            order_id=oid,
            client_order_id=coid,
            product_type=product_type,
        )
    if isinstance(client, BitgetSpotClient):
        return _unwrap_client_order_payload(client.get_order(symbol=str(symbol), order_id=oid, client_order_id=coid))
    if isinstance(client, BybitClient):
        return client.get_order(symbol=str(symbol), order_id=oid, client_order_id=coid)
    if isinstance(client, (GateSpotClient, GateUsdtFuturesClient)):
        if not oid:
            return {}
        return _unwrap_client_order_payload(client.get_order(order_id=oid))
    if isinstance(client, HtxClient):
        return client.get_order(symbol=str(symbol), order_id=oid, client_order_id=coid)
    if hasattr(client, "get_order"):
        try:
            raw = client.get_order(symbol=str(symbol), order_id=oid, client_order_id=coid)
        except TypeError:
            if oid:
                raw = client.get_order(order_id=oid)
            elif coid:
                raw = client.get_order(client_order_id=coid)
            else:
                return {}
        return _unwrap_client_order_payload(raw)
    return {}


def query_grid_order_fill(
    client: BaseRestClient,
    *,
    symbol: str,
    market_type: str,
    exchange_order_id: str = "",
    client_order_id: str = "",
    exchange_config: Optional[Dict[str, Any]] = None,
) -> Tuple[float, float, str]:
    """
    Returns (filled_qty, avg_price, status).
    status: open | partial | filled | cancelled | unknown
    """
    ex_cfg = exchange_config if isinstance(exchange_config, dict) else {}
    try:
        data = _fetch_grid_client_order(
            client,
            symbol=str(symbol),
            market_type=str(market_type or "swap"),
            exchange_order_id=str(exchange_order_id or ""),
            client_order_id=str(client_order_id or ""),
            exchange_config=ex_cfg,
        )
        if isinstance(data, dict) and data:
            filled, avg, status = parse_grid_order_fill(
                client,
                symbol=str(symbol),
                market_type=str(market_type or "swap"),
                exchange_config=ex_cfg,
                data=data,
            )
            if isinstance(client, (BitgetMixClient, BitgetSpotClient)) and filled <= 0 and status in ("open", "unknown"):
                fill_oid = _extract_order_id_from_payload(data, str(exchange_order_id or ""))
                try:
                    raw_fills = _fetch_bitget_grid_fills(
                        client,
                        symbol=str(symbol),
                        market_type=str(market_type or "swap"),
                        exchange_order_id=fill_oid,
                        exchange_config=ex_cfg,
                    )
                    ff, fa, fs = _aggregate_bitget_grid_fills(
                        client,
                        symbol=str(symbol),
                        market_type=str(market_type or "swap"),
                        exchange_config=ex_cfg,
                        raw=raw_fills,
                    )
                    if ff > 0:
                        return ff, fa, fs
                except Exception as e:
                    logger.debug("query_grid_order_fill bitget fills fallback: %s", e)
            return filled, avg, status
    except Exception as e:
        logger.debug("query_grid_order_fill: %s", e)
    return 0.0, 0.0, "unknown"
