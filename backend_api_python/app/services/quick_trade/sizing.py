"""Quick Trade quote-to-base sizing helpers."""

from __future__ import annotations

import math

from app.utils.logger import get_logger


logger = get_logger(__name__)


class QuickTradeSizingError(ValueError):
    """The order cannot be sized safely; no submission is permitted."""

    def __init__(self):
        super().__init__("quickTrade.errorHints.sizingUnavailable")


def resolve_order_notional_usdt(amount_usdt: float, leverage: int, market_type: str) -> float:
    """Translate the page input into quote notional using one consistent contract."""
    amount = max(0.0, float(amount_usdt or 0.0))
    if str(market_type or "").strip().lower() == "swap":
        return amount * max(1, int(leverage or 1))
    return amount


def convert_usdt_to_base_qty(client, symbol: str, usdt_amount: float, market_type: str, reference_price: float = 0.0) -> float:
    """
    Convert USDT amount to base asset quantity for all exchanges.

    This is a unified function that works for all exchanges.
    For spot: converts USDT -> base qty (e.g., 100 USDT -> 0.033 ETH)
    For swap: converts USDT -> base qty (e.g., 100 USDT -> 0.033 ETH), which will then be converted to contracts

    Args:
        client: Exchange client instance
        symbol: Trading pair (e.g., "ETH/USDT")
        usdt_amount: USDT amount to convert
        market_type: "spot" or "swap"
        reference_price: A trusted recent price supplied by the caller (optional)

    Returns:
        Base asset quantity
    """
    try:
        usdt_amount = float(usdt_amount)
        reference_price = float(reference_price)
        if not math.isfinite(usdt_amount) or usdt_amount <= 0:
            raise QuickTradeSizingError()
        if not math.isfinite(reference_price) or reference_price < 0:
            raise QuickTradeSizingError()
        # Try to get current price from exchange
        current_price = 0.0

        if reference_price > 0:
            current_price = reference_price
            logger.info(f"Using supplied reference price {reference_price} for USDT conversion")
        else:
            # Try to get current market price from exchange
            if hasattr(client, "get_ticker"):
                try:
                    ticker = client.get_ticker(symbol=symbol)
                    if isinstance(ticker, dict):
                        for _pk in ("last", "lastPr", "lastPx", "lastPrice", "close", "price"):
                            try:
                                current_price = float(ticker.get(_pk) or 0)
                            except Exception:
                                current_price = 0.0
                            if math.isfinite(current_price) and current_price > 0:
                                break
                            current_price = 0.0
                except Exception:
                    current_price = 0.0

            # OKX
            from app.services.live_trading.okx import OkxClient
            if current_price <= 0 and isinstance(client, OkxClient):
                try:
                    from app.services.live_trading.symbols import to_okx_spot_inst_id, to_okx_swap_inst_id
                    inst_id = to_okx_spot_inst_id(symbol) if market_type == "spot" else to_okx_swap_inst_id(symbol)
                    logger.debug(f"OKX: Getting ticker for inst_id={inst_id}, symbol={symbol}, market_type={market_type}")
                    ticker = client.get_ticker(inst_id=inst_id)
                    if ticker:
                        current_price = float(ticker.get("last") or ticker.get("lastPx") or 0)
                        logger.debug(f"OKX: Got price {current_price} from ticker")
                    else:
                        logger.warning(f"OKX: get_ticker returned empty result for inst_id={inst_id}")
                except AttributeError as e:
                    logger.error(f"OKX: get_ticker method not found: {e}")
                    raise
                except Exception as e:
                    logger.error(f"OKX: Failed to get ticker: {e}")
                    raise

            # Binance - try to get price from public API
            from app.services.live_trading.binance import BinanceFuturesClient
            from app.services.live_trading.binance_spot import BinanceSpotClient
            if current_price <= 0 and isinstance(client, (BinanceFuturesClient, BinanceSpotClient)):
                try:
                    # Binance public ticker endpoint
                    base_url = getattr(client, "base_url", "")
                    if "binance" in base_url.lower():
                        if isinstance(client, BinanceFuturesClient):
                            ticker_path = "/fapi/v1/ticker/price"
                        else:
                            ticker_path = "/api/v3/ticker/price"
                        from app.services.live_trading.symbols import to_binance_futures_symbol
                        # Binance spot and futures use the same symbol format
                        sym = to_binance_futures_symbol(symbol)
                        status, data, _ = client._request("GET", ticker_path, params={"symbol": sym})
                        if status == 200 and isinstance(data, dict):
                            current_price = float(data.get("price") or 0)
                except Exception:
                    pass

            # Bybit v5 - same host as trading API; tickers/orderbook are public.
            from app.services.live_trading.bybit import BybitClient
            if current_price <= 0 and isinstance(client, BybitClient):
                try:
                    from app.services.live_trading.symbols import to_bybit_symbol

                    bsym = to_bybit_symbol(symbol).upper()
                    cat = "spot" if (market_type or "").strip().lower() == "spot" else "linear"
                    if getattr(client, "base_url", "") and bsym:
                        status, jd, _ = client._request(
                            "GET",
                            "/v5/market/tickers",
                            params={"category": cat, "symbol": bsym},
                        )
                        if status == 200:
                            lst = (((jd.get("result") or {}).get("list")) or []) if isinstance(jd, dict) else []
                            if lst and isinstance(lst[0], dict):
                                t0 = lst[0]
                                current_price = float(
                                    str(
                                        t0.get("lastPrice")
                                        or t0.get("markPrice")
                                        or t0.get("indexPrice")
                                        or 0
                                    ).replace(",", "")
                                    or 0
                                )
                        if current_price <= 0:
                            status, od, _ = client._request(
                                "GET",
                                "/v5/market/orderbook",
                                params={"category": cat, "symbol": bsym, "limit": 25},
                            )
                            if status == 200:
                                res = (od.get("result") or {}) if isinstance(od, dict) else {}
                                bids = res.get("b") or []
                                asks = res.get("a") or []
                                bp = float(str(bids[0][0]).replace(",", "")) if bids and bids[0] else 0.0
                                ap = float(str(asks[0][0]).replace(",", "")) if asks and asks[0] else 0.0
                                if bp > 0 and ap > 0:
                                    current_price = (bp + ap) / 2.0
                                else:
                                    current_price = bp or ap
                except Exception:
                    pass

        if math.isfinite(current_price) and current_price > 0:
            base_qty = usdt_amount / current_price
            if not math.isfinite(base_qty) or base_qty <= 0:
                raise QuickTradeSizingError()
            logger.info(f"Converted USDT amount {usdt_amount} to base qty {base_qty:.8f} using price {current_price} for {symbol}")
            return base_qty
        logger.warning("Quick Trade sizing rejected: no valid price for %s on %s", symbol, type(client).__name__)
        raise QuickTradeSizingError()
    except QuickTradeSizingError:
        raise
    except Exception as e:
        logger.warning("Quick Trade sizing rejected for %s: %s", symbol, e)
        raise QuickTradeSizingError() from e
