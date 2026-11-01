"""Public crypto order-book snapshots for the Quick Trade console."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Sequence, Tuple

import requests

from app.services.live_trading.base import get_live_trading_proxies, get_live_trading_requests_verify
from app.services.quick_trade.symbols import is_supported_crypto_exchange


class OrderBookError(RuntimeError):
    """Raised when a supported venue cannot return a usable snapshot."""


@dataclass(frozen=True)
class SymbolParts:
    base: str
    quote: str

    @property
    def compact(self) -> str:
        return f"{self.base}{self.quote}"

    @property
    def dashed(self) -> str:
        return f"{self.base}-{self.quote}"

    @property
    def underscored(self) -> str:
        return f"{self.base}_{self.quote}"


def normalize_symbol(symbol: str) -> SymbolParts:
    raw = str(symbol or "").strip().upper()
    if ":" in raw:
        raw = raw.rsplit(":", 1)[-1]
    for suffix in ("-SWAP", "_SWAP", "/SWAP", "SWAP", "-PERP", "_PERP", "PERP"):
        if raw.endswith(suffix):
            raw = raw[: -len(suffix)]
            break
    for separator in ("/", "-", "_"):
        if separator in raw:
            parts = [part for part in raw.split(separator) if part]
            if len(parts) >= 2:
                return SymbolParts(parts[0], parts[1])
    compact = "".join(ch for ch in raw if ch.isalnum())
    for quote in ("USDT", "USDC", "FDUSD", "BUSD", "USD", "EUR", "BTC", "ETH"):
        if compact.endswith(quote) and len(compact) > len(quote):
            return SymbolParts(compact[: -len(quote)], quote)
    raise OrderBookError("Unsupported crypto symbol format")


def _safe_number(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) and number > 0 else 0.0


def _levels(rows: Iterable[Sequence[Any]], *, descending: bool, limit: int) -> List[Dict[str, float]]:
    normalized: List[Dict[str, float]] = []
    for row in rows or []:
        if isinstance(row, dict):
            price = _safe_number(row.get("p") or row.get("price"))
            size = _safe_number(row.get("s") or row.get("size") or row.get("amount"))
        elif isinstance(row, (list, tuple)) and len(row) >= 2:
            price = _safe_number(row[0])
            size = _safe_number(row[1])
        else:
            continue
        if price <= 0 or size <= 0:
            continue
        normalized.append({"price": price, "size": size, "notional": price * size})
    normalized.sort(key=lambda item: item["price"], reverse=descending)
    return normalized[:limit]


def _extract_rows(exchange_id: str, payload: Any) -> Tuple[Iterable[Sequence[Any]], Iterable[Sequence[Any]], int]:
    now_ms = int(time.time() * 1000)
    if exchange_id == "binance":
        return payload.get("bids") or [], payload.get("asks") or [], int(payload.get("E") or now_ms)
    if exchange_id == "okx":
        rows = (payload.get("data") or [{}])[0]
        return rows.get("bids") or [], rows.get("asks") or [], int(rows.get("ts") or now_ms)
    if exchange_id == "bitget":
        data = payload.get("data") or {}
        if isinstance(data, list):
            data = data[0] if data else {}
        return (
            data.get("bids") or data.get("b") or [],
            data.get("asks") or data.get("a") or [],
            int(data.get("ts") or payload.get("requestTime") or now_ms),
        )
    if exchange_id == "bybit":
        data = payload.get("result") or {}
        return data.get("b") or [], data.get("a") or [], int(data.get("ts") or payload.get("time") or now_ms)
    if exchange_id == "gate":
        return payload.get("bids") or [], payload.get("asks") or [], int(payload.get("current") or payload.get("update") or now_ms)
    if exchange_id == "htx":
        tick = payload.get("tick") or {}
        return tick.get("bids") or [], tick.get("asks") or [], int(payload.get("ts") or now_ms)
    raise OrderBookError("Unsupported crypto exchange")


def normalize_order_book(
    exchange_id: str,
    payload: Any,
    *,
    symbol: str,
    market_type: str,
    limit: int,
) -> Dict[str, Any]:
    bids_raw, asks_raw, timestamp = _extract_rows(exchange_id, payload if isinstance(payload, dict) else {})
    if timestamp < 1_000_000_000_000:
        timestamp *= 1000
    bids = _levels(bids_raw, descending=True, limit=limit)
    asks = _levels(asks_raw, descending=False, limit=limit)
    if not bids or not asks:
        raise OrderBookError("Exchange returned an empty order book")
    best_bid = bids[0]["price"]
    best_ask = asks[0]["price"]
    mid = (best_bid + best_ask) / 2
    spread = max(0.0, best_ask - best_bid)
    bid_total = sum(row["notional"] for row in bids)
    ask_total = sum(row["notional"] for row in asks)
    total = bid_total + ask_total
    bid_share = (bid_total / total * 100) if total > 0 else 50.0
    return {
        "exchange_id": exchange_id,
        "symbol": symbol,
        "market_type": market_type,
        "timestamp": timestamp,
        "bids": bids,
        "asks": asks,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "mid": mid,
        "spread": spread,
        "spread_bps": (spread / mid * 10000) if mid > 0 else 0.0,
        "bid_total": bid_total,
        "ask_total": ask_total,
        "bid_share": bid_share,
        "ask_share": 100 - bid_share,
        "buy_wall": max(bids, key=lambda row: row["notional"]),
        "sell_wall": max(asks, key=lambda row: row["notional"]),
    }


def _request_spec(exchange_id: str, parts: SymbolParts, market_type: str, limit: int):
    spot = market_type == "spot"
    if exchange_id == "binance":
        base = "https://api.binance.com" if spot else "https://fapi.binance.com"
        path = "/api/v3/depth" if spot else "/fapi/v1/depth"
        return base + path, {"symbol": parts.compact, "limit": limit}
    if exchange_id == "okx":
        inst_id = parts.dashed if spot else f"{parts.dashed}-SWAP"
        return "https://www.okx.com/api/v5/market/books", {"instId": inst_id, "sz": limit}
    if exchange_id == "bitget":
        return "https://api.bitget.com/api/v3/market/orderbook", {
            "category": "SPOT" if spot else "USDT-FUTURES",
            "symbol": parts.compact,
            "limit": limit,
        }
    if exchange_id == "bybit":
        return "https://api.bybit.com/v5/market/orderbook", {
            "category": "spot" if spot else "linear",
            "symbol": parts.compact,
            "limit": limit,
        }
    if exchange_id == "gate":
        if spot:
            return "https://api.gateio.ws/api/v4/spot/order_book", {
                "currency_pair": parts.underscored,
                "limit": limit,
                "with_id": "true",
            }
        return "https://fx-api.gateio.ws/api/v4/futures/usdt/order_book", {
            "contract": parts.underscored,
            "limit": limit,
            "with_id": "true",
        }
    if exchange_id == "htx":
        if spot:
            return "https://api.htx.com/market/depth", {"symbol": parts.compact.lower(), "type": "step0", "depth": limit}
        return "https://api.hbdm.com/linear-swap-ex/market/depth", {
            "contract_code": parts.dashed,
            "type": "step0",
            "depth": limit,
        }
    raise OrderBookError("Unsupported crypto exchange")


def fetch_order_book(
    session: requests.Session,
    *,
    exchange_id: str,
    symbol: str,
    market_type: str = "swap",
    limit: int = 25,
    timeout: float = 4.0,
) -> Dict[str, Any]:
    exchange = str(exchange_id or "").strip().lower()
    if not is_supported_crypto_exchange(exchange):
        raise OrderBookError("Unsupported crypto exchange")
    normalized_market_type = "spot" if str(market_type or "").strip().lower() == "spot" else "swap"
    bounded_limit = max(5, min(int(limit or 25), 50))
    url, params = _request_spec(exchange, normalize_symbol(symbol), normalized_market_type, bounded_limit)
    response = session.get(
        url,
        params=params,
        timeout=timeout,
        verify=get_live_trading_requests_verify(),
        proxies=get_live_trading_proxies(),
    )
    response.raise_for_status()
    payload = response.json()
    return normalize_order_book(
        exchange,
        payload,
        symbol=symbol,
        market_type=normalized_market_type,
        limit=bounded_limit,
    )
