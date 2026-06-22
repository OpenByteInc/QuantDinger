"""
Spot sizing helpers: align close quantity with exchange free base balance (fees/reserve).

Full-position buys often leave recorded size slightly above sellable free balance.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional, Tuple

from app.services.live_trading.base import BaseRestClient
from app.services.live_trading.symbols import _split_base_quote

logger = logging.getLogger(__name__)

_DEFAULT_CLOSE_SAFETY = 0.998
_DEFAULT_OPEN_BUFFER = 0.995


def _close_safety_ratio() -> float:
    try:
        v = float(os.getenv("SPOT_CLOSE_SAFETY_RATIO", str(_DEFAULT_CLOSE_SAFETY)))
    except Exception:
        v = _DEFAULT_CLOSE_SAFETY
    if v <= 0 or v > 1.0:
        v = _DEFAULT_CLOSE_SAFETY
    return v


def _open_quote_buffer() -> float:
    try:
        v = float(os.getenv("SPOT_OPEN_QUOTE_BUFFER", str(_DEFAULT_OPEN_BUFFER)))
    except Exception:
        v = _DEFAULT_OPEN_BUFFER
    if v <= 0 or v > 1.0:
        v = _DEFAULT_OPEN_BUFFER
    return v


def scale_spot_open_notional(usdt_notional: float) -> float:
    """Reserve a small USDT buffer so spot buys do not consume 100% of quote (fees)."""
    amt = float(usdt_notional or 0.0)
    if amt <= 0:
        return 0.0
    return amt * _open_quote_buffer()


def _pick_free_from_row(row: Dict[str, Any], *keys: str) -> float:
    for k in keys:
        if k in row and row.get(k) is not None:
            try:
                v = float(row.get(k) or 0.0)
                if v >= 0:
                    return v
            except Exception:
                continue
    return 0.0


def _pick_cost_from_row(row: Dict[str, Any], *keys: str) -> float:
    for k in keys:
        if k in row and row.get(k) is not None:
            try:
                v = float(row.get(k) or 0.0)
                if v > 0:
                    return v
            except Exception:
                continue
    return 0.0


def _spot_holding(total: float, available: float, avg_cost: float = 0.0) -> Dict[str, float]:
    t = max(0.0, float(total or 0.0))
    a = max(0.0, float(available or 0.0))
    if t <= 0 and a <= 0:
        return {"total": 0.0, "available": 0.0, "avg_cost": 0.0}
    if t <= 0:
        t = a
    if a <= 0:
        a = t
    return {
        "total": t,
        "available": a,
        "avg_cost": max(0.0, float(avg_cost or 0.0)),
    }


def get_spot_base_holding(client: BaseRestClient, *, symbol: str) -> Dict[str, float]:
    """
    Best-effort spot base-asset holding (total + available/free).

    Used by Quick Trade spot position display and close sizing.
    """
    base, _ = _split_base_quote(str(symbol or ""))
    if not base:
        return {"total": 0.0, "available": 0.0, "avg_cost": 0.0}
    base_u = base.upper()

    try:
        from app.services.live_trading.binance_spot import BinanceSpotClient

        if isinstance(client, BinanceSpotClient):
            raw = client.get_account() or {}
            for b in raw.get("balances") or []:
                if not isinstance(b, dict):
                    continue
                if str(b.get("asset") or "").upper() == base_u:
                    free = _pick_free_from_row(b, "free")
                    locked = _pick_free_from_row(b, "locked")
                    return _spot_holding(free + locked, free)
    except Exception as e:
        logger.warning("spot base holding (binance): %s", e)

    try:
        from app.services.live_trading.okx import OkxClient

        if isinstance(client, OkxClient):
            raw = client.get_balance() or {}
            data = (raw.get("data") or []) if isinstance(raw, dict) else []
            first = data[0] if isinstance(data, list) and data else {}
            if isinstance(first, dict):
                for det in first.get("details") or []:
                    if not isinstance(det, dict):
                        continue
                    if str(det.get("ccy") or "").upper() == base_u:
                        avail = _pick_free_from_row(det, "availBal", "cashBal")
                        total = _pick_free_from_row(det, "eq", "cashBal", "availBal")
                        frozen = _pick_free_from_row(det, "frozenBal")
                        if total <= 0:
                            total = avail + frozen
                        avg_cost = _pick_cost_from_row(
                            det, "openAvgPx", "accAvgPx", "avgPx", "avgCost"
                        )
                        return _spot_holding(total, avail, avg_cost)
    except Exception as e:
        logger.warning("spot base holding (okx): %s", e)

    try:
        from app.services.live_trading.gate import GateSpotClient

        if isinstance(client, GateSpotClient):
            raw = client.get_accounts()
            rows = raw if isinstance(raw, list) else (raw.get("data") if isinstance(raw, dict) else [])
            if not isinstance(rows, list):
                rows = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                if str(row.get("currency") or "").upper() == base_u:
                    avail = _pick_free_from_row(row, "available", "available_balance")
                    locked = _pick_free_from_row(row, "locked", "freeze")
                    return _spot_holding(avail + locked, avail)
    except Exception as e:
        logger.warning("spot base holding (gate): %s", e)

    try:
        from app.services.live_trading.bitget_spot import BitgetSpotClient

        if isinstance(client, BitgetSpotClient):
            raw = client.get_assets() or {}
            data = (raw.get("data") or []) if isinstance(raw, dict) else []
            for row in data if isinstance(data, list) else []:
                if not isinstance(row, dict):
                    continue
                if str(row.get("coin") or row.get("currency") or "").upper() == base_u:
                    avail = _pick_free_from_row(row, "available", "avail", "free")
                    frozen = _pick_free_from_row(row, "frozen", "lock")
                    total = _pick_free_from_row(row, "total", "balance")
                    if total <= 0:
                        total = avail + frozen
                    avg_cost = _pick_cost_from_row(
                        row, "averageOpenPrice", "avgOpenPrice", "avgCost", "openAvgPx"
                    )
                    return _spot_holding(total, avail, avg_cost)
    except Exception as e:
        logger.warning("spot base holding (bitget): %s", e)

    try:
        from app.services.live_trading.bybit import BybitClient

        if isinstance(client, BybitClient) and (getattr(client, "category", "") or "").strip().lower() == "spot":
            raw = client.get_wallet_balance(account_type="SPOT") or {}
            lst = ((raw.get("result") or {}).get("list") or []) if isinstance(raw, dict) else []
            for acct in lst:
                if not isinstance(acct, dict):
                    continue
                for coin in acct.get("coin") or []:
                    if not isinstance(coin, dict):
                        continue
                    if str(coin.get("coin") or "").upper() == base_u:
                        avail = _pick_free_from_row(
                            coin, "availableToWithdraw", "free", "walletBalance"
                        )
                        total = _pick_free_from_row(coin, "walletBalance", "equity", "availableToWithdraw")
                        avg_cost = _pick_cost_from_row(
                            coin, "avgPrice", "sessionAvgPrice", "accAvgPx", "avgCost"
                        )
                        return _spot_holding(total, avail, avg_cost)
    except Exception as e:
        logger.warning("spot base holding (bybit): %s", e)

    try:
        from app.services.live_trading.htx import HtxClient

        if isinstance(client, HtxClient) and getattr(client, "market_type", "") == "spot":
            balance = client.get_balance()
            items = (((balance.get("data") or {}).get("list")) if isinstance(balance, dict) else None) or []
            for item in items:
                if not isinstance(item, dict):
                    continue
                if str(item.get("currency") or "").upper() == base_u:
                    total = _pick_free_from_row(item, "balance")
                    avail = _pick_free_from_row(item, "available", "balance")
                    return _spot_holding(total, avail)
    except Exception as e:
        logger.warning("spot base holding (htx): %s", e)

    try:
        from app.services.live_trading.kraken import KrakenClient

        if isinstance(client, KrakenClient):
            raw = client.get_balance() or {}
            result = raw.get("result") if isinstance(raw, dict) else None
            if isinstance(result, dict):
                for key, val in result.items():
                    if base_u in str(key).upper():
                        try:
                            qty = float(val or 0.0)
                        except Exception:
                            qty = 0.0
                        if qty > 0:
                            return _spot_holding(qty, qty)
    except Exception as e:
        logger.warning("spot base holding (kraken): %s", e)

    return {"total": 0.0, "available": 0.0, "avg_cost": 0.0}


def get_spot_free_base_balance(client: BaseRestClient, *, symbol: str) -> float:
    """
    Best-effort free/available base asset on the connected spot account.
    Returns 0.0 if unknown or on error.
    """
    holding = get_spot_base_holding(client, symbol=symbol)
    return max(0.0, float(holding.get("available") or 0.0))


def fetch_spot_last_price(client: BaseRestClient, *, symbol: str) -> float:
    """Best-effort last price for USDT -> base conversion (supports Bitget ``lastPr``)."""
    if not hasattr(client, "get_ticker"):
        return 0.0
    try:
        ticker = client.get_ticker(symbol=symbol)
    except Exception:
        return 0.0
    if not isinstance(ticker, dict):
        return 0.0
    for key in ("last", "lastPr", "lastPx", "lastPrice", "close", "price"):
        try:
            px = float(ticker.get(key) or 0.0)
        except Exception:
            px = 0.0
        if px > 0:
            return px
    return 0.0


def prepare_spot_live_order_sizes(
    client: BaseRestClient,
    *,
    symbol: str,
    side: str,
    reduce_only: bool,
    base_qty: float,
    ref_price: float = 0.0,
) -> Tuple[float, float, bool]:
    """
    Normalize spot sizes for PendingOrderWorker (strategy live path).

    Returns:
        (base_qty, quote_amount, market_buy_uses_quote)
        ``market_buy_uses_quote`` is True when market BUY should send USDT notional
        (Bitget / Gate spot).
    """
    from app.services.live_trading.binance_spot import BinanceSpotClient
    from app.services.live_trading.bitget_spot import BitgetSpotClient
    from app.services.live_trading.gate import GateSpotClient

    qty = float(base_qty or 0.0)
    sd = (side or "").strip().lower()
    quote_amt = 0.0
    market_buy_uses_quote = False

    if sd == "sell" and reduce_only:
        qty = normalize_spot_base_quantity(
            client, symbol=symbol, quantity=qty, for_market=True
        )
        return max(0.0, qty), 0.0, False

    if sd == "buy" and not reduce_only:
        rp = float(ref_price or 0.0)
        if rp <= 0:
            rp = fetch_spot_last_price(client, symbol=symbol)
        if rp > 0 and qty > 0:
            quote_amt = qty * rp
        quote_amt = normalize_spot_quote_amount(
            client, symbol=symbol, quote_amount=quote_amt
        )
        if isinstance(client, (BitgetSpotClient, GateSpotClient)):
            market_buy_uses_quote = quote_amt > 0
        if isinstance(client, BinanceSpotClient) or not market_buy_uses_quote:
            qty = normalize_spot_base_quantity(
                client, symbol=symbol, quantity=qty, for_market=True
            )
        return max(0.0, qty), max(0.0, quote_amt), market_buy_uses_quote

    qty = normalize_spot_base_quantity(
        client, symbol=symbol, quantity=qty, for_market=True
    )
    return max(0.0, qty), 0.0, False


def normalize_spot_quote_amount(
    client: BaseRestClient,
    *,
    symbol: str,
    quote_amount: float,
) -> float:
    """Floor USDT notional for spot market buy (Bitget/Gate quote-sized orders)."""
    amt = float(quote_amount or 0.0)
    if amt <= 0:
        return 0.0
    norm = getattr(client, "_normalize_quote_size", None)
    if callable(norm):
        try:
            dec, _prec = norm(symbol=str(symbol), quote_size=amt)
            return float(dec or 0.0)
        except Exception as e:
            logger.warning("spot quote normalize failed (%s): %s", symbol, e)
    return amt


def normalize_spot_base_quantity(
    client: BaseRestClient,
    *,
    symbol: str,
    quantity: float,
    for_market: bool = True,
) -> float:
    """Floor quantity to exchange step when client supports _normalize_quantity."""
    qty = float(quantity or 0.0)
    if qty <= 0:
        return 0.0
    norm_qty = getattr(client, "_normalize_quantity", None)
    if callable(norm_qty):
        try:
            dec, _prec = norm_qty(symbol=str(symbol), quantity=qty, for_market=for_market)
            return float(dec or 0.0)
        except Exception as e:
            logger.warning("spot quantity normalize failed (%s): %s", symbol, e)
    norm_base = getattr(client, "_normalize_base_size", None)
    if callable(norm_base):
        try:
            dec, _prec = norm_base(symbol=str(symbol), base_size=qty)
            return float(dec or 0.0)
        except Exception as e:
            logger.warning("spot base normalize failed (%s): %s", symbol, e)
    return qty


def clamp_spot_close_quantity(
    client: BaseRestClient,
    *,
    symbol: str,
    requested_qty: float,
    safety_ratio: Optional[float] = None,
) -> Tuple[float, Dict[str, Any]]:
    """
    Cap sell size to exchange free base (with safety ratio) and normalize down to lot step.
    """
    req = float(requested_qty or 0.0)
    meta: Dict[str, Any] = {"requested": req}
    if req <= 0:
        return 0.0, meta

    ratio = float(safety_ratio) if safety_ratio is not None else _close_safety_ratio()
    free = get_spot_free_base_balance(client, symbol=symbol)
    meta["exchange_free"] = free
    meta["safety_ratio"] = ratio

    cap = req
    if free > 0:
        cap = min(req, free * ratio)
        meta["capped_before_normalize"] = cap

    final = normalize_spot_base_quantity(client, symbol=symbol, quantity=cap, for_market=True)
    meta["final"] = final
    if final < req * 0.999:
        meta["adjusted"] = True
        logger.info(
            "Spot close qty adjusted: symbol=%s requested=%s free=%s final=%s ratio=%s",
            symbol,
            req,
            free,
            final,
            ratio,
        )
    return max(0.0, final), meta

