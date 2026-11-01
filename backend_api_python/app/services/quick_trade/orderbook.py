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


_CONTRACT_QUANTITY_UNITS = {
    "binance": "base",
    "okx": "contracts",
    "bitget": "base",
    "bybit": "base",
    "gate": "contracts",
    "htx": "contracts",
}
_NATIVE_CONTRACT_EXCHANGES = {
    exchange_id for exchange_id, quantity_unit in _CONTRACT_QUANTITY_UNITS.items() if quantity_unit == "contracts"
}
_CONTRACT_METADATA_TTL_SECONDS = 6 * 60 * 60
_contract_metadata_cache: Dict[Tuple[str, str], Tuple[float, float]] = {}


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
    contract_multiplier: float = 1.0,
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
        "base_asset": normalize_symbol(symbol).base,
        "quantity_unit": "base" if market_type == "spot" else _CONTRACT_QUANTITY_UNITS.get(exchange_id, "unknown"),
        "contract_multiplier": contract_multiplier,
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


def _contract_request_spec(exchange_id: str, parts: SymbolParts):
    if exchange_id == "okx":
        return "https://www.okx.com/api/v5/public/instruments", {
            "instType": "SWAP",
            "instId": f"{parts.dashed}-SWAP",
        }
    if exchange_id == "gate":
        return f"https://fx-api.gateio.ws/api/v4/futures/usdt/contracts/{parts.underscored}", {}
    if exchange_id == "htx":
        return "https://api.hbdm.com/linear-swap-api/v1/swap_contract_info", {
            "contract_code": parts.dashed,
        }
    raise OrderBookError("Unsupported contract metadata exchange")


def _extract_contract_multiplier(exchange_id: str, payload: Any, parts: SymbolParts) -> float:
    data = payload if isinstance(payload, dict) else {}
    if exchange_id == "okx":
        rows = data.get("data") or []
        instrument = rows[0] if rows and isinstance(rows[0], dict) else {}
        contract_currency = str(instrument.get("ctValCcy") or "").upper()
        contract_type = str(instrument.get("ctType") or "").lower()
        if contract_type == "inverse" or (contract_currency and contract_currency != parts.base):
            return 0.0
        contract_value = _safe_number(instrument.get("ctVal"))
        contract_multiple = _safe_number(instrument.get("ctMult")) or 1.0
        return contract_value * contract_multiple
    if exchange_id == "gate":
        return _safe_number(data.get("quanto_multiplier") or data.get("contract_size"))
    if exchange_id == "htx":
        rows = data.get("data") or []
        contract = rows[0] if rows and isinstance(rows[0], dict) else {}
        return _safe_number(contract.get("contract_size"))
    return 0.0


def _contract_multiplier(
    session: requests.Session,
    *,
    exchange_id: str,
    parts: SymbolParts,
    timeout: float,
) -> float:
    if exchange_id not in _NATIVE_CONTRACT_EXCHANGES:
        return 1.0
    cache_key = (exchange_id, parts.compact)
    cached = _contract_metadata_cache.get(cache_key)
    now = time.monotonic()
    if cached and now - cached[0] < _CONTRACT_METADATA_TTL_SECONDS:
        return cached[1]
    try:
        url, params = _contract_request_spec(exchange_id, parts)
        response = session.get(
            url,
            params=params,
            timeout=timeout,
            verify=get_live_trading_requests_verify(),
            proxies=get_live_trading_proxies(),
        )
        response.raise_for_status()
        multiplier = _extract_contract_multiplier(exchange_id, response.json(), parts)
    except (requests.RequestException, TypeError, ValueError):
        return 0.0
    if multiplier > 0:
        _contract_metadata_cache[cache_key] = (now, multiplier)
    return multiplier


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
    parts = normalize_symbol(symbol)
    url, params = _request_spec(exchange, parts, normalized_market_type, bounded_limit)
    response = session.get(
        url,
        params=params,
        timeout=timeout,
        verify=get_live_trading_requests_verify(),
        proxies=get_live_trading_proxies(),
    )
    response.raise_for_status()
    payload = response.json()
    contract_multiplier = 1.0
    if normalized_market_type != "spot":
        contract_multiplier = _contract_multiplier(
            session,
            exchange_id=exchange,
            parts=parts,
            timeout=timeout,
        )
    return normalize_order_book(
        exchange,
        payload,
        symbol=symbol,
        market_type=normalized_market_type,
        limit=bounded_limit,
        contract_multiplier=contract_multiplier,
    )
