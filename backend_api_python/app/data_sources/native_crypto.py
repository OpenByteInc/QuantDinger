"""Native public REST clients for supported cryptocurrency exchanges."""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests


SUPPORTED_EXCHANGES = ("binance", "bitget", "bybit", "okx", "gate", "htx")

_COMMON_TIMEFRAMES = {
    "1m": "1m",
    "3m": "3m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1h",
    "2h": "2h",
    "4h": "4h",
    "6h": "6h",
    "8h": "8h",
    "12h": "12h",
    "1d": "1d",
    "1w": "1w",
}

_SUPPORTED_TIMEFRAMES = {
    "binance": set(_COMMON_TIMEFRAMES),
    "okx": {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d", "1w"},
    "bybit": {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d", "1w"},
    "bitget": {"1m", "5m", "15m", "30m", "1h", "4h", "6h", "12h", "1d", "1w"},
    "gate": {"1m", "5m", "15m", "30m", "1h", "4h", "8h", "1d", "1w"},
    "htx": {"1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"},
}

_TIMEFRAME_SECONDS = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "2h": 7200,
    "4h": 14400,
    "6h": 21600,
    "8h": 28800,
    "12h": 43200,
    "1d": 86400,
    "1w": 604800,
}


class NativeCryptoAPIError(RuntimeError):
    """Raised when an exchange public API returns an unusable response."""


def normalize_exchange_id(exchange_id: str) -> str:
    value = str(exchange_id or "").strip().lower()
    if value == "huobi":
        value = "htx"
    if value not in SUPPORTED_EXCHANGES:
        raise ValueError(f"Unsupported crypto exchange: {value or '<empty>'}")
    return value


def normalize_market_type(market_type: str) -> str:
    value = str(market_type or "spot").strip().lower()
    if value in {"future", "futures", "perp", "perpetual", "linear"}:
        value = "swap"
    if value not in {"spot", "swap"}:
        raise ValueError(f"Unsupported crypto market type: {value}")
    return value


def _symbol_parts(symbol: str) -> Tuple[str, str]:
    value = str(symbol or "").strip().upper().split(":", 1)[0]
    if "/" in value:
        base, quote = value.split("/", 1)
        return base, quote
    for quote in ("USDT", "USDC", "BUSD", "USD", "BTC", "ETH"):
        if value.endswith(quote) and len(value) > len(quote):
            return value[: -len(quote)], quote
    return value, "USDT"


def _canonical_symbol(base: str, quote: str, market_type: str) -> str:
    core = f"{str(base).upper()}/{str(quote).upper()}"
    return f"{core}:{str(quote).upper()}" if market_type == "swap" else core


def _float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _ticker(
    *,
    symbol: str,
    last: Any,
    open_price: Any = 0,
    high: Any = 0,
    low: Any = 0,
    change: Any = None,
    percentage: Any = None,
    quote_volume: Any = 0,
    timestamp: Any = None,
) -> Dict[str, Any]:
    last_value = _float(last)
    open_value = _float(open_price)
    change_value = _float(change) if change is not None else last_value - open_value
    percentage_value = (
        _float(percentage)
        if percentage is not None
        else ((change_value / open_value) * 100 if open_value else 0.0)
    )
    return {
        "symbol": symbol,
        "last": last_value,
        "close": last_value,
        "open": open_value,
        "high": _float(high),
        "low": _float(low),
        "change": change_value,
        "percentage": percentage_value,
        "changePercent": percentage_value,
        "quoteVolume": _float(quote_volume),
        "timestamp": int(_float(timestamp)) if timestamp is not None else None,
    }


class NativeCryptoPublicClient:
    """Small exchange-native client with the contract used by CryptoDataSource."""

    def __init__(
        self,
        exchange_id: str,
        market_type: str = "spot",
        *,
        timeout_ms: int = 10000,
        proxy: str = "",
        okx_host: str = "",
    ) -> None:
        self.id = normalize_exchange_id(exchange_id)
        self.market_type = normalize_market_type(market_type)
        self.timeout = max(float(timeout_ms or 10000) / 1000.0, 1.0)
        self.enableRateLimit = True
        self.timeframes = {key: key for key in _SUPPORTED_TIMEFRAMES[self.id]}
        self.markets: Dict[str, Dict[str, Any]] = {}
        self._markets_lock = threading.Lock()
        self._request_lock = threading.Lock()
        self._last_request_at = 0.0
        self._session_local = threading.local()
        self._proxies = {"http": proxy, "https": proxy} if proxy else None
        self._okx_host = str(okx_host or "www.okx.com").strip()

    def _throttle(self) -> None:
        with self._request_lock:
            remaining = 0.04 - (time.monotonic() - self._last_request_at)
            if remaining > 0:
                time.sleep(remaining)
            self._last_request_at = time.monotonic()

    def _http_session(self) -> requests.Session:
        session = getattr(self._session_local, "value", None)
        if session is None:
            session = requests.Session()
            self._session_local.value = session
        return session

    def _get(self, url: str, params: Optional[Dict[str, Any]] = None) -> Any:
        self._throttle()
        response = self._http_session().get(
            url,
            params=params or {},
            timeout=self.timeout,
            proxies=self._proxies,
            headers={"User-Agent": "QuantDinger/5 public-market"},
        )
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict):
            code = payload.get("code")
            if code not in (None, 0, "0", "00000", 200, "200"):
                raise NativeCryptoAPIError(
                    f"{self.id} public API error {code}: "
                    f"{payload.get('msg') or payload.get('message') or payload}"
                )
            if payload.get("status") not in (None, "ok"):
                raise NativeCryptoAPIError(f"{self.id} public API error: {payload}")
            if payload.get("retCode") not in (None, 0, "0"):
                raise NativeCryptoAPIError(
                    f"{self.id} public API error {payload.get('retCode')}: {payload.get('retMsg')}"
                )
            if payload.get("label") and payload.get("message"):
                raise NativeCryptoAPIError(
                    f"{self.id} public API error {payload.get('label')}: {payload.get('message')}"
                )
        return payload

    def _native_symbol(self, symbol: str) -> str:
        base, quote = _symbol_parts(symbol)
        if self.id in {"binance", "bybit", "bitget"}:
            return f"{base}{quote}"
        if self.id == "okx":
            return f"{base}-{quote}-SWAP" if self.market_type == "swap" else f"{base}-{quote}"
        if self.id == "gate":
            return f"{base}_{quote}"
        if self.id == "htx":
            return f"{base}-{quote}" if self.market_type == "swap" else f"{base}{quote}".lower()
        raise ValueError(f"Unsupported crypto exchange: {self.id}")

    def load_markets(self, reload: bool = False) -> Dict[str, Dict[str, Any]]:
        if self.markets and not reload:
            return self.markets
        with self._markets_lock:
            if self.markets and not reload:
                return self.markets
            rows = self._fetch_market_rows()
            markets: Dict[str, Dict[str, Any]] = {}
            for row in rows:
                base = str(row.get("base") or "").upper()
                quote = str(row.get("quote") or "").upper()
                if not base or not quote:
                    continue
                symbol = _canonical_symbol(base, quote, self.market_type)
                info = {
                    "id": str(row.get("id") or self._native_symbol(symbol)),
                    "symbol": symbol,
                    "base": base,
                    "quote": quote,
                    "settle": str(row.get("settle") or quote).upper(),
                    "active": bool(row.get("active", True)),
                    "spot": self.market_type == "spot",
                    "swap": self.market_type == "swap",
                    "contract": self.market_type == "swap",
                    "linear": self.market_type == "swap" and str(row.get("settle") or quote).upper() == quote,
                    "future": False,
                    "type": self.market_type,
                    "info": row.get("info") or row,
                }
                markets[symbol] = info
                if self.market_type == "swap":
                    markets.setdefault(f"{base}/{quote}", info)
            self.markets = markets
            return markets

    def _fetch_market_rows(self) -> List[Dict[str, Any]]:
        if self.id == "binance":
            host = "https://fapi.binance.com" if self.market_type == "swap" else "https://api.binance.com"
            path = "/fapi/v1/exchangeInfo" if self.market_type == "swap" else "/api/v3/exchangeInfo"
            payload = self._get(host + path)
            rows = []
            for item in payload.get("symbols", []):
                active = item.get("status") == "TRADING"
                if self.market_type == "swap" and item.get("contractType") != "PERPETUAL":
                    continue
                rows.append({
                    "id": item.get("symbol"),
                    "base": item.get("baseAsset"),
                    "quote": item.get("quoteAsset"),
                    "settle": item.get("marginAsset") or item.get("quoteAsset"),
                    "active": active,
                    "info": item,
                })
            return rows
        if self.id == "okx":
            payload = self._get(
                f"https://{self._okx_host}/api/v5/public/instruments",
                {"instType": "SWAP" if self.market_type == "swap" else "SPOT"},
            )
            rows = []
            for item in payload.get("data", []):
                inst_id = str(item.get("instId") or "")
                parts = inst_id.split("-")
                base = item.get("baseCcy") or item.get("ctValCcy") or (parts[0] if parts else "")
                quote = item.get("quoteCcy") or item.get("settleCcy") or (parts[1] if len(parts) > 1 else "")
                rows.append({
                    "id": inst_id,
                    "base": base,
                    "quote": quote,
                    "settle": item.get("settleCcy") or quote,
                    "active": item.get("state") == "live",
                    "info": item,
                })
            return rows
        if self.id == "bybit":
            category = "linear" if self.market_type == "swap" else "spot"
            cursor = ""
            rows = []
            for _ in range(10):
                params: Dict[str, Any] = {"category": category}
                if self.market_type == "swap":
                    params["limit"] = 1000
                if cursor:
                    params["cursor"] = cursor
                payload = self._get("https://api.bybit.com/v5/market/instruments-info", params)
                result = payload.get("result") or {}
                for item in result.get("list") or []:
                    rows.append({
                        "id": item.get("symbol"),
                        "base": item.get("baseCoin"),
                        "quote": item.get("quoteCoin"),
                        "settle": item.get("settleCoin") or item.get("quoteCoin"),
                        "active": item.get("status") == "Trading",
                        "info": item,
                    })
                next_cursor = str(result.get("nextPageCursor") or "")
                if self.market_type == "spot" or not next_cursor or next_cursor == cursor:
                    break
                cursor = next_cursor
            return rows
        if self.id == "bitget":
            if self.market_type == "swap":
                payload = self._get(
                    "https://api.bitget.com/api/v2/mix/market/contracts",
                    {"productType": "USDT-FUTURES"},
                )
            else:
                payload = self._get("https://api.bitget.com/api/v2/spot/public/symbols")
            rows = []
            for item in payload.get("data") or []:
                status = str(item.get("status") or item.get("symbolStatus") or "").lower()
                rows.append({
                    "id": item.get("symbol"),
                    "base": item.get("baseCoin"),
                    "quote": item.get("quoteCoin"),
                    "settle": item.get("quoteCoin"),
                    "active": status in {"online", "normal", "listed"},
                    "info": item,
                })
            return rows
        if self.id == "gate":
            path = "/api/v4/futures/usdt/contracts" if self.market_type == "swap" else "/api/v4/spot/currency_pairs"
            payload = self._get("https://api.gateio.ws" + path)
            rows = []
            for item in payload:
                native_id = str(item.get("name") or item.get("id") or "")
                parts = native_id.split("_")
                rows.append({
                    "id": native_id,
                    "base": item.get("base") or (parts[0] if parts else ""),
                    "quote": item.get("quote") or (parts[1] if len(parts) > 1 else "USDT"),
                    "settle": "USDT",
                    "active": not bool(item.get("in_delisting")) and item.get("trade_status", "tradable") == "tradable",
                    "info": item,
                })
            return rows
        if self.id == "htx":
            if self.market_type == "swap":
                payload = self._get("https://api.hbdm.com/linear-swap-api/v1/swap_contract_info")
                rows = []
                for item in payload.get("data") or []:
                    code = str(item.get("contract_code") or "")
                    parts = code.split("-")
                    rows.append({
                        "id": code,
                        "base": item.get("symbol") or (parts[0] if parts else ""),
                        "quote": parts[1] if len(parts) > 1 else "USDT",
                        "settle": "USDT",
                        "active": int(item.get("contract_status") or 0) == 1,
                        "info": item,
                    })
                return rows
            payload = self._get("https://api.huobi.pro/v1/common/symbols")
            return [
                {
                    "id": item.get("symbol"),
                    "base": item.get("base-currency"),
                    "quote": item.get("quote-currency"),
                    "active": item.get("state") == "online",
                    "info": item,
                }
                for item in payload.get("data") or []
            ]
        raise ValueError(f"Unsupported crypto exchange: {self.id}")

    def fetch_ticker(self, symbol: str) -> Dict[str, Any]:
        native = self._native_symbol(symbol)
        canonical = _canonical_symbol(*_symbol_parts(symbol), self.market_type)
        if self.id == "binance":
            host = "https://fapi.binance.com" if self.market_type == "swap" else "https://api.binance.com"
            path = "/fapi/v1/ticker/24hr" if self.market_type == "swap" else "/api/v3/ticker/24hr"
            row = self._get(host + path, {"symbol": native})
            percentage = _float(row.get("change24h")) * 100
            return _ticker(
                symbol=canonical,
                last=row.get("lastPrice"),
                open_price=row.get("openPrice"),
                high=row.get("highPrice"),
                low=row.get("lowPrice"),
                change=row.get("priceChange"),
                percentage=row.get("priceChangePercent"),
                quote_volume=row.get("quoteVolume"),
                timestamp=row.get("closeTime"),
            )
        if self.id == "okx":
            payload = self._get(f"https://{self._okx_host}/api/v5/market/ticker", {"instId": native})
            rows = payload.get("data") or []
            if not rows:
                raise NativeCryptoAPIError(f"symbol not found: {native}")
            row = rows[0]
            return _ticker(
                symbol=canonical,
                last=row.get("last"),
                open_price=row.get("open24h"),
                high=row.get("high24h"),
                low=row.get("low24h"),
                quote_volume=row.get("volCcy24h"),
                timestamp=row.get("ts"),
            )
        if self.id == "bybit":
            payload = self._get(
                "https://api.bybit.com/v5/market/tickers",
                {"category": "linear" if self.market_type == "swap" else "spot", "symbol": native},
            )
            rows = (payload.get("result") or {}).get("list") or []
            if not rows:
                raise NativeCryptoAPIError(f"symbol not found: {native}")
            row = rows[0]
            pct = _float(row.get("price24hPcnt")) * 100
            return _ticker(
                symbol=canonical,
                last=row.get("lastPrice"),
                open_price=row.get("prevPrice24h"),
                high=row.get("highPrice24h"),
                low=row.get("lowPrice24h"),
                percentage=pct,
                quote_volume=row.get("turnover24h"),
                timestamp=payload.get("time"),
            )
        if self.id == "bitget":
            if self.market_type == "swap":
                payload = self._get(
                    "https://api.bitget.com/api/v2/mix/market/ticker",
                    {"symbol": native, "productType": "USDT-FUTURES"},
                )
            else:
                payload = self._get(
                    "https://api.bitget.com/api/v2/spot/market/tickers",
                    {"symbol": native},
                )
            rows = payload.get("data") or []
            row = rows[0] if isinstance(rows, list) and rows else rows
            if not isinstance(row, dict):
                raise NativeCryptoAPIError(f"symbol not found: {native}")
            return _ticker(
                symbol=canonical,
                last=row.get("lastPr") or row.get("last"),
                open_price=row.get("open"),
                high=row.get("high24h"),
                low=row.get("low24h"),
                percentage=percentage,
                quote_volume=row.get("quoteVolume"),
                timestamp=row.get("ts") or payload.get("requestTime"),
            )
        if self.id == "gate":
            if self.market_type == "swap":
                rows = self._get(
                    "https://api.gateio.ws/api/v4/futures/usdt/tickers",
                    {"contract": native},
                )
            else:
                rows = self._get(
                    "https://api.gateio.ws/api/v4/spot/tickers",
                    {"currency_pair": native},
                )
            if not rows:
                raise NativeCryptoAPIError(f"symbol not found: {native}")
            row = rows[0]
            return _ticker(
                symbol=canonical,
                last=row.get("last"),
                open_price=row.get("open"),
                high=row.get("high_24h") or row.get("high_24h_usd"),
                low=row.get("low_24h") or row.get("low_24h_usd"),
                percentage=row.get("change_percentage"),
                quote_volume=row.get("quote_volume") or row.get("volume_24h_quote"),
            )
        if self.id == "htx":
            if self.market_type == "swap":
                payload = self._get(
                    "https://api.hbdm.com/linear-swap-ex/market/detail/merged",
                    {"contract_code": native},
                )
            else:
                payload = self._get(
                    "https://api.huobi.pro/market/detail/merged",
                    {"symbol": native},
                )
            row = payload.get("tick") or {}
            if not row:
                raise NativeCryptoAPIError(f"symbol not found: {native}")
            return _ticker(
                symbol=canonical,
                last=row.get("close"),
                open_price=row.get("open"),
                high=row.get("high"),
                low=row.get("low"),
                quote_volume=row.get("vol"),
                timestamp=payload.get("ts"),
            )
        raise ValueError(f"Unsupported crypto exchange: {self.id}")

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        since: Optional[int] = None,
        limit: int = 300,
    ) -> List[List[Any]]:
        if timeframe not in self.timeframes:
            raise NativeCryptoAPIError(f"Unsupported timeframe {timeframe} on {self.id}")
        limit = max(1, min(int(limit or 300), 1000))
        native = self._native_symbol(symbol)
        tf_ms = int(_TIMEFRAME_SECONDS[timeframe] * 1000)
        rows: Iterable[Any]
        if self.id == "binance":
            host = "https://fapi.binance.com" if self.market_type == "swap" else "https://api.binance.com"
            path = "/fapi/v1/klines" if self.market_type == "swap" else "/api/v3/klines"
            params: Dict[str, Any] = {"symbol": native, "interval": timeframe, "limit": limit}
            if since is not None:
                params["startTime"] = int(since)
            rows = self._get(host + path, params)
            parsed = [[int(row[0]), _float(row[1]), _float(row[2]), _float(row[3]), _float(row[4]), _float(row[5])] for row in rows]
        elif self.id == "okx":
            bar = {"1h": "1H", "2h": "2H", "4h": "4H", "6h": "6H", "12h": "12H", "1d": "1D", "1w": "1W"}.get(timeframe, timeframe)
            params = {"instId": native, "bar": bar, "limit": min(limit, 300)}
            path = "/api/v5/market/candles"
            if since is not None:
                path = "/api/v5/market/history-candles"
                params["after"] = int(since) + tf_ms * min(limit, 300)
            payload = self._get(f"https://{self._okx_host}{path}", params)
            rows = payload.get("data") or []
            parsed = [[int(row[0]), _float(row[1]), _float(row[2]), _float(row[3]), _float(row[4]), _float(row[5])] for row in rows]
        elif self.id == "bybit":
            interval = {"1m": "1", "3m": "3", "5m": "5", "15m": "15", "30m": "30", "1h": "60", "2h": "120", "4h": "240", "6h": "360", "12h": "720", "1d": "D", "1w": "W"}[timeframe]
            params = {
                "category": "linear" if self.market_type == "swap" else "spot",
                "symbol": native,
                "interval": interval,
                "limit": min(limit, 1000),
            }
            if since is not None:
                params["start"] = int(since)
                params["end"] = int(since) + tf_ms * min(limit, 1000)
            payload = self._get("https://api.bybit.com/v5/market/kline", params)
            rows = (payload.get("result") or {}).get("list") or []
            parsed = [[int(row[0]), _float(row[1]), _float(row[2]), _float(row[3]), _float(row[4]), _float(row[5])] for row in rows]
        elif self.id == "bitget":
            granularity = {
                "1m": "1min" if self.market_type == "spot" else "1m",
                "5m": "5min" if self.market_type == "spot" else "5m",
                "15m": "15min" if self.market_type == "spot" else "15m",
                "30m": "30min" if self.market_type == "spot" else "30m",
                "1h": "1h",
                "4h": "4h",
                "6h": "6h",
                "12h": "12h",
                "1d": "1day" if self.market_type == "spot" else "1D",
                "1w": "1week" if self.market_type == "spot" else "1W",
            }[timeframe]
            scope = "mix" if self.market_type == "swap" else "spot"
            params = {"symbol": native, "granularity": granularity, "limit": min(limit, 1000)}
            if self.market_type == "swap":
                params["productType"] = "USDT-FUTURES"
            if since is not None:
                params["startTime"] = int(since)
                params["endTime"] = int(since) + tf_ms * min(limit, 1000)
            payload = self._get(f"https://api.bitget.com/api/v2/{scope}/market/candles", params)
            rows = payload.get("data") or []
            parsed = [[int(row[0]), _float(row[1]), _float(row[2]), _float(row[3]), _float(row[4]), _float(row[5])] for row in rows]
        elif self.id == "gate":
            interval = "7d" if timeframe == "1w" else timeframe
            params = {"interval": interval, "limit": min(limit, 1000)}
            if since is not None:
                params["from"] = int(since // 1000)
                params["to"] = int((int(since) + tf_ms * min(limit, 1000)) // 1000)
                params.pop("limit", None)
            if self.market_type == "swap":
                params["contract"] = native
                rows = self._get("https://api.gateio.ws/api/v4/futures/usdt/candlesticks", params)
                parsed = [[int(row.get("t") or 0) * 1000, _float(row.get("o")), _float(row.get("h")), _float(row.get("l")), _float(row.get("c")), _float(row.get("v"))] for row in rows]
            else:
                params["currency_pair"] = native
                rows = self._get("https://api.gateio.ws/api/v4/spot/candlesticks", params)
                parsed = [[int(row[0]) * 1000, _float(row[5]), _float(row[3]), _float(row[4]), _float(row[2]), _float(row[6])] for row in rows]
        elif self.id == "htx":
            period = {"1m": "1min", "5m": "5min", "15m": "15min", "30m": "30min", "1h": "60min", "4h": "4hour", "1d": "1day", "1w": "1week"}[timeframe]
            params = {"period": period, "size": min(limit, 2000)}
            if self.market_type == "swap":
                params["contract_code"] = native
                url = "https://api.hbdm.com/linear-swap-ex/market/history/kline"
            else:
                params["symbol"] = native
                url = "https://api.huobi.pro/market/history/kline"
            payload = self._get(url, params)
            rows = payload.get("data") or []
            parsed = [[int(row.get("id") or 0) * 1000, _float(row.get("open")), _float(row.get("high")), _float(row.get("low")), _float(row.get("close")), _float(row.get("amount"))] for row in rows]
        else:
            raise ValueError(f"Unsupported crypto exchange: {self.id}")
        lower_bound = int(since) if since is not None else 0
        unique = {int(row[0]): row for row in parsed if row and int(row[0]) >= lower_bound}
        return sorted(unique.values(), key=lambda row: row[0])[:limit]

    def fetch_server_time(self) -> int:
        """Return exchange UTC time from a public market-data endpoint."""
        if self.id != "binance":
            raise NotImplementedError("Public server time is only implemented for Binance")
        if self.market_type == "swap":
            url = "https://fapi.binance.com/fapi/v1/time"
        else:
            url = "https://api.binance.com/api/v3/time"
        payload = self._get(url)
        server_time = int(payload.get("serverTime") or 0)
        if server_time <= 0:
            raise NativeCryptoAPIError("Binance public server time response was invalid")
        return server_time


def create_native_crypto_client(
    exchange_id: str,
    market_type: str = "spot",
    *,
    timeout_ms: int = 10000,
    proxy: str = "",
    okx_host: str = "",
) -> NativeCryptoPublicClient:
    return NativeCryptoPublicClient(
        exchange_id,
        market_type,
        timeout_ms=timeout_ms,
        proxy=proxy,
        okx_host=okx_host,
    )


__all__ = [
    "NativeCryptoAPIError",
    "NativeCryptoPublicClient",
    "SUPPORTED_EXCHANGES",
    "create_native_crypto_client",
    "normalize_exchange_id",
    "normalize_market_type",
]
