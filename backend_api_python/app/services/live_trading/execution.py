"""
Translate a strategy signal into a direct-exchange order call.

Supports:
- Crypto exchanges: Binance, OKX, Bitget, Bybit, Coinbase, Kraken, Gate, HTX
- Traditional brokers: Interactive Brokers (IBKR) for US stocks
- Forex brokers: MetaTrader 5 (MT5)
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.services.live_trading.base import BaseRestClient, LiveOrderResult, LiveTradingError
from app.services.live_trading.contracts import normalize_order_market_type, order_intent_from_signal
from app.services.live_trading.binance import BinanceFuturesClient
from app.services.live_trading.binance_spot import BinanceSpotClient
from app.services.live_trading.okx import OkxClient
from app.services.live_trading.bitget import BitgetMixClient
from app.services.live_trading.bitget_spot import BitgetSpotClient
from app.services.live_trading.bybit import BybitClient
from app.services.live_trading.coinbase_exchange import CoinbaseExchangeClient
from app.services.live_trading.kraken import KrakenClient
from app.services.live_trading.kraken_futures import KrakenFuturesClient
from app.services.live_trading.gate import GateSpotClient, GateUsdtFuturesClient

# Lazy import HTX
HtxClient = None

# Lazy import IBKR
IBKRClient = None

# Lazy import MT5
MT5Client = None

# Lazy import Alpaca
AlpacaClient = None


def _normalize_symbol_for_order(symbol: str, market_type: str = "swap") -> str:
    """
    规范化符号格式，确保符号符合交易所要求。
    
    处理各种输入格式：
    - BTC/USDT -> BTC/USDT
    - BTCUSDT -> BTC/USDT
    - BTC/USDT:USDT -> BTC/USDT
    - PI, TRX -> PI/USDT, TRX/USDT (默认添加 /USDT)
    
    Args:
        symbol: 原始符号
        market_type: 市场类型 (spot/swap)
        
    Returns:
        规范化后的符号
    """
    if not symbol:
        return symbol
    
    sym = symbol.strip()
    
    if ':' in sym:
        sym = sym.split(':', 1)[0]
    
    sym = sym.upper()
    
    if '/' in sym:
        return sym
    
    common_quotes = ['USDT', 'USD', 'BTC', 'ETH', 'BUSD', 'USDC']
    for quote in common_quotes:
        if sym.endswith(quote) and len(sym) > len(quote):
            base = sym[:-len(quote)]
            if base:
                return f"{base}/{quote}"
    
    return f"{sym}/USDT"


def _quote_amount_from_base_qty(client: BaseRestClient, *, symbol: str, base_qty: float) -> float:
    if float(base_qty or 0.0) <= 0:
        return 0.0
    if not hasattr(client, "get_ticker"):
        return float(base_qty or 0.0)
    try:
        ticker = client.get_ticker(symbol=symbol)
    except Exception:
        return float(base_qty or 0.0)
    if not isinstance(ticker, dict):
        return float(base_qty or 0.0)
    try:
        price = float(ticker.get("last") or ticker.get("lastPr") or ticker.get("lastPrice") or ticker.get("price") or 0.0)
    except Exception:
        price = 0.0
    if price <= 0:
        return float(base_qty or 0.0)
    return float(base_qty or 0.0) * price


def place_order_from_signal(
    client: BaseRestClient,
    *,
    signal_type: str,
    symbol: str,
    amount: float,
    market_type: str = "swap",
    exchange_config: Optional[Dict[str, Any]] = None,
    client_order_id: Optional[str] = None,
    quote_amount: float = 0,
) -> LiveOrderResult:
    if amount is None:
        amount = 0.0
    qty = float(amount or 0.0)
    quote_amt = float(quote_amount or 0.0)
    if qty <= 0 and quote_amt <= 0:
        raise LiveTradingError("Invalid amount")

    intent = order_intent_from_signal(
        signal_type=signal_type,
        symbol=symbol,
        quantity=qty,
        market_type=market_type,
        exchange_config=exchange_config,
        client_order_id=client_order_id,
        quote_amount=quote_amt,
    )
    side, pos_side, reduce_only = intent.side, intent.pos_side, intent.reduce_only

    cfg = exchange_config if isinstance(exchange_config, dict) else {}
    mt = normalize_order_market_type(market_type or cfg.get("market_type") or "swap")

    if mt == "spot":
        from app.services.live_trading.spot_sizing import (
            clamp_spot_close_quantity,
            normalize_spot_base_quantity,
            normalize_spot_quote_amount,
        )

        if side == "sell" and reduce_only:
            # Full/partial close: cap to free base and lot step.
            qty, _meta = clamp_spot_close_quantity(client, symbol=symbol, requested_qty=qty)
            if qty <= 0:
                raise LiveTradingError(
                    "Insufficient spot base balance to close (fees or balance mismatch)"
                )
        elif side == "buy" and not reduce_only:
            if quote_amt <= 0 and qty > 0:
                quote_amt = _quote_amount_from_base_qty(client, symbol=symbol, base_qty=qty)
            quote_amt = normalize_spot_quote_amount(client, symbol=symbol, quote_amount=quote_amt)
            if quote_amt <= 0:
                raise LiveTradingError("Invalid spot buy quote amount (below min/precision)")
            # BinanceSpot now uses ``quoteOrderQty`` for market BUY, so the
            # base ``quantity`` no longer needs to satisfy LOT_SIZE — keep it
            # only as a fallback for clients that still want base sizing.
            if isinstance(client, BinanceSpotClient) and quote_amt <= 0:
                qty = normalize_spot_base_quantity(
                    client, symbol=symbol, quantity=qty, for_market=True
                )
                if qty <= 0:
                    raise LiveTradingError("Invalid spot order quantity (below lot step/minQty)")
        else:
            qty = normalize_spot_base_quantity(
                client, symbol=symbol, quantity=qty, for_market=True
            )
            if qty <= 0:
                raise LiveTradingError("Invalid spot order quantity (below lot step/minQty)")
    # Spot does not support short signals in this system.
    if mt == "spot" and ("short" in (signal_type or "").lower()):
        raise LiveTradingError("spot market does not support short signals")
    
    symbol = _normalize_symbol_for_order(symbol, market_type=mt)

    if isinstance(client, BinanceFuturesClient):
        return client.place_market_order(
            symbol=symbol,
            side="BUY" if side == "buy" else "SELL",
            quantity=qty,
            reduce_only=reduce_only,
            position_side=pos_side,
            client_order_id=client_order_id,
        )
    if isinstance(client, OkxClient):
        td_mode = (cfg.get("margin_mode") or cfg.get("td_mode") or "cross")
        return client.place_market_order(
            symbol=symbol,
            side=side,
            pos_side=pos_side,
            size=qty,
            market_type=mt,
            td_mode=str(td_mode),
            reduce_only=reduce_only,
            client_order_id=client_order_id,
        )
    if isinstance(client, BitgetMixClient):
        margin_coin = str(cfg.get("margin_coin") or cfg.get("marginCoin") or "USDT")
        product_type = str(cfg.get("product_type") or cfg.get("productType") or "USDT-FUTURES")
        margin_mode = str(cfg.get("margin_mode") or cfg.get("marginMode") or cfg.get("td_mode") or "cross")
        return client.place_market_order(
            symbol=symbol,
            side=side,
            size=qty,
            margin_coin=margin_coin,
            product_type=product_type,
            margin_mode=margin_mode,
            reduce_only=reduce_only,
            client_order_id=client_order_id,
            hold_side=pos_side or ("long" if side == "buy" else "short"),
        )
    if isinstance(client, BinanceSpotClient):
        # Prefer ``quoteOrderQty`` for market BUY (Binance recommended) so we
        # never trip LOT_SIZE / NOTIONAL filters; SELL still uses base qty.
        if side == "buy" and quote_amt > 0:
            return client.place_market_order(
                symbol=symbol,
                side="BUY",
                quote_order_qty=quote_amt,
                client_order_id=client_order_id,
            )
        return client.place_market_order(
            symbol=symbol,
            side="BUY" if side == "buy" else "SELL",
            quantity=qty,
            client_order_id=client_order_id,
        )
    if isinstance(client, BitgetSpotClient):
        spot_size = quote_amt if (side == "buy" and quote_amt > 0) else qty
        if side == "buy" and spot_size <= 0:
            spot_size = _quote_amount_from_base_qty(client, symbol=symbol, base_qty=qty)
        return client.place_market_order(
            symbol=symbol,
            side=side,
            size=spot_size,
            client_order_id=client_order_id,
        )
    if isinstance(client, BybitClient):
        return client.place_market_order(
            symbol=symbol,
            side=side,
            qty=qty,
            reduce_only=reduce_only,
            pos_side=pos_side,
            client_order_id=client_order_id,
        )
    if isinstance(client, CoinbaseExchangeClient):
        return client.place_market_order(symbol=symbol, side=side, size=qty, client_order_id=client_order_id)
    if isinstance(client, KrakenClient):
        return client.place_market_order(symbol=symbol, side=side, size=qty, client_order_id=client_order_id)
    if isinstance(client, GateSpotClient):
        gate_size = qty
        if side == "buy":
            gate_size = quote_amt if quote_amt > 0 else _quote_amount_from_base_qty(
                client, symbol=symbol, base_qty=qty
            )
        return client.place_market_order(symbol=symbol, side=side, size=gate_size, client_order_id=client_order_id)
    if isinstance(client, GateUsdtFuturesClient):
        return client.place_market_order(symbol=symbol, side=side, size=qty, reduce_only=reduce_only, client_order_id=client_order_id)
    if isinstance(client, KrakenFuturesClient):
        return client.place_market_order(symbol=symbol, side=side, size=qty, reduce_only=reduce_only, client_order_id=client_order_id)

    global HtxClient
    if HtxClient is None:
        try:
            from app.services.live_trading.htx import HtxClient as _HtxClient
            HtxClient = _HtxClient
        except ImportError:
            pass

    if HtxClient is not None and isinstance(client, HtxClient):
        return client.place_market_order(
            symbol=symbol,
            side=side,
            qty=qty,
            reduce_only=reduce_only,
            pos_side=pos_side,
            client_order_id=client_order_id,
        )

    # Check for IBKR client (lazy import to avoid circular dependency)
    global IBKRClient
    if IBKRClient is None:
        try:
            from app.services.ibkr_trading import IBKRClient as _IBKRClient
            IBKRClient = _IBKRClient
        except ImportError:
            pass

    if IBKRClient is not None and isinstance(client, IBKRClient):
        return _place_ibkr_order(
            client=client,
            signal_type=signal_type,
            symbol=symbol,
            amount=qty,
            exchange_config=exchange_config,
        )

    # Check for MT5 client (lazy import to avoid circular dependency)
    global MT5Client
    if MT5Client is None:
        try:
            from app.services.mt5_trading import MT5Client as _MT5Client
            MT5Client = _MT5Client
        except ImportError:
            pass

    if MT5Client is not None and isinstance(client, MT5Client):
        return _place_mt5_order(
            client=client,
            signal_type=signal_type,
            symbol=symbol,
            amount=qty,
            exchange_config=exchange_config,
        )

    # Check for Alpaca client (US stocks + crypto via REST)
    global AlpacaClient
    if AlpacaClient is None:
        try:
            from app.services.alpaca_trading import AlpacaClient as _AlpacaClient
            AlpacaClient = _AlpacaClient
        except ImportError:
            pass

    if AlpacaClient is not None and isinstance(client, AlpacaClient):
        return _place_alpaca_order(
            client=client,
            signal_type=signal_type,
            symbol=symbol,
            amount=qty,
            exchange_config=exchange_config,
        )

    raise LiveTradingError(f"Unsupported client type: {type(client)}")


def _place_ibkr_order(
    client,
    *,
    signal_type: str,
    symbol: str,
    amount: float,
    exchange_config: Optional[Dict[str, Any]] = None,
) -> LiveOrderResult:
    """
    Place order via IBKR for US stocks.

    Signal mapping for stocks (no short selling in this implementation):
    - open_long / add_long -> BUY
    - close_long / reduce_long -> SELL
    - open_short / close_short -> Not supported (raises error)
    """
    sig = (signal_type or "").strip().lower()

    # Stock trading: no short selling support in basic implementation
    if "short" in sig:
        raise LiveTradingError("IBKR stock trading does not support short signals in this implementation")

    # Determine action
    if sig in ("open_long", "add_long"):
        action = "buy"
    elif sig in ("close_long", "reduce_long"):
        action = "sell"
    else:
        raise LiveTradingError(f"Unsupported signal_type for IBKR: {signal_type}")

    # Get market type from config
    cfg = exchange_config if isinstance(exchange_config, dict) else {}
    market_type = str(cfg.get("market_type") or cfg.get("market_category") or "USStock").strip()

    # Place market order
    result = client.place_market_order(
        symbol=symbol,
        side=action,
        quantity=amount,
        market_type=market_type,
    )

    # Convert IBKRClient result to LiveOrderResult format
    return LiveOrderResult(
        success=result.success,
        exchange_order_id=str(result.order_id) if result.order_id else "",
        filled=result.filled,
        avg_price=result.avg_price,
        raw={
            "status": result.status,
            "message": result.message,
            "raw": result.raw,
        },
    )


def _place_mt5_order(
    client,
    *,
    signal_type: str,
    symbol: str,
    amount: float,
    exchange_config: Optional[Dict[str, Any]] = None,
) -> LiveOrderResult:
    """
    Place order via MT5 for forex trading.

    Signal mapping for forex:
    - open_long / add_long -> BUY
    - close_long / reduce_long -> SELL
    - open_short / add_short -> SELL
    - close_short / reduce_short -> BUY
    """
    sig = (signal_type or "").strip().lower()

    # Determine action based on signal
    if sig in ("open_long", "add_long"):
        action = "buy"
    elif sig in ("close_long", "reduce_long"):
        action = "sell"
    elif sig in ("open_short", "add_short"):
        action = "sell"
    elif sig in ("close_short", "reduce_short"):
        action = "buy"
    else:
        raise LiveTradingError(f"Unsupported signal_type for MT5: {signal_type}")

    # Normalize symbol before placing order (MT5 requires specific format)
    from app.services.mt5_trading.symbols import normalize_symbol
    normalized_symbol = normalize_symbol(symbol)
    
    # Place market order
    result = client.place_market_order(
        symbol=normalized_symbol,
        side=action,
        volume=amount,
        comment="QuantDinger",
    )

    # Convert MT5Client result to LiveOrderResult format
    return LiveOrderResult(
        success=result.success,
        exchange_order_id=str(result.order_id) if result.order_id else "",
        filled=result.filled,
        avg_price=result.price,
        raw={
            "status": result.status,
            "message": result.message,
            "deal_id": result.deal_id,
            "raw": result.raw,
        },
    )


def _place_alpaca_order(
    client,
    *,
    signal_type: str,
    symbol: str,
    amount: float,
    exchange_config: Optional[Dict[str, Any]] = None,
) -> LiveOrderResult:
    """
    Place order via Alpaca for US stocks (USStock) or crypto.

    Signal mapping mirrors IBKR for stocks (no short selling in this basic flow);
    for crypto the same long-only mapping is used since Alpaca crypto doesn't
    support shorting either.

    - open_long  / add_long   -> BUY
    - close_long / reduce_long -> SELL
    - any *_short signal      -> rejected
    """
    sig = (signal_type or "").strip().lower()

    if "short" in sig:
        raise LiveTradingError("Alpaca does not support short signals (long-only)")

    if sig in ("open_long", "add_long"):
        action = "buy"
    elif sig in ("close_long", "reduce_long"):
        action = "sell"
    else:
        raise LiveTradingError(f"Unsupported signal_type for Alpaca: {signal_type}")

    cfg = exchange_config if isinstance(exchange_config, dict) else {}
    raw_market = str(cfg.get("market_category") or cfg.get("market_type") or "USStock").strip().lower()
    market_type = "crypto" if raw_market in ("crypto", "cryptocurrency") else "USStock"

    result = client.place_market_order(
        symbol=symbol,
        side=action,
        quantity=amount,
        market_type=market_type,
    )

    return LiveOrderResult(
        success=result.success,
        exchange_order_id=str(result.order_id) if result.order_id else "",
        filled=result.filled,
        avg_price=result.avg_price,
        raw={
            "status": result.status,
            "message": result.message,
            "raw": result.raw,
        },
    )
