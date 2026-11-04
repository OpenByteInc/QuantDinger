"""Reconnectable public market-price stream with bounded REST fallback.

The stream deliberately carries prices only. Private authenticated execution
streams remain responsible for orders and fills, keeping market data failure
independent from account event processing.
"""

from __future__ import annotations

import gzip
import json
import ssl
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, Mapping

from app.services.live_trading.base import _get_requests_verify
from app.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class PriceFeedSnapshot:
    prices: Dict[str, float]
    source: str
    age_ms: int
    connected: bool


def _normalized_symbol(value: object) -> str:
    return "".join(char for char in str(value or "").upper() if char.isalnum())


def _json_payload(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, bytes):
        try:
            raw = gzip.decompress(raw)
        except Exception:
            pass
        raw = raw.decode("utf-8", errors="replace")
    if isinstance(raw, Mapping):
        return dict(raw)
    try:
        value = json.loads(str(raw or "{}"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


class PublicMarketPriceFeed:
    """Small per-runtime public ticker client for supported crypto venues."""

    SUPPORTED_EXCHANGES = {"binance", "okx", "bybit", "bitget", "gate", "htx"}

    def __init__(
        self,
        *,
        exchange_id: str,
        market_type: str,
        instruments: Iterable[Mapping[str, Any]],
        rest_fallback: Callable[[], Dict[str, float]],
    ) -> None:
        self.exchange_id = str(exchange_id or "").strip().lower()
        self.market_type = str(market_type or "spot").strip().lower()
        self.instruments = [dict(item) for item in instruments]
        self.rest_fallback = rest_fallback
        self._aliases: Dict[str, str] = {}
        for item in self.instruments:
            key = str(item.get("key") or "")
            symbol = str(item.get("symbol") or "")
            instrument_id = str(item.get("instrument_id") or "")
            base = symbol.replace("/", "-").upper()
            aliases = {
                _normalized_symbol(symbol),
                _normalized_symbol(base),
                _normalized_symbol(base + ("-SWAP" if self.market_type != "spot" else "")),
                _normalized_symbol(instrument_id),
            }
            for alias in aliases:
                if alias:
                    self._aliases[alias] = key
        self._prices: Dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._ws: Any = None
        self._connected = False

    @property
    def supported(self) -> bool:
        has_special_api = any(
            str(item.get("api_family") or self.market_type).strip().lower()
            not in {"spot", "swap"}
            for item in self.instruments
        )
        return self.exchange_id in self.SUPPORTED_EXCHANGES and bool(self.instruments) and not has_special_api

    def start(self) -> None:
        if not self.supported or (self._thread and self._thread.is_alive()):
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name=f"MarketPrice-{self.exchange_id}-{id(self)}",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=max(0.0, float(timeout or 0.0)))
        self._connected = False

    def snapshot(
        self,
        *,
        max_age_seconds: float = 10.0,
        rest_fallback: Callable[[], Dict[str, float]] | None = None,
    ) -> PriceFeedSnapshot:
        now = time.monotonic()
        max_age = max(0.5, float(max_age_seconds or 0.0))
        with self._lock:
            stream_prices = {
                key: price
                for key, (price, seen_at) in self._prices.items()
                if price > 0 and now - seen_at <= max_age
            }
            ages = [now - seen_at for key, (_, seen_at) in self._prices.items() if key in stream_prices]
        missing = {
            str(item.get("key") or "")
            for item in self.instruments
            if str(item.get("key") or "") not in stream_prices
        }
        fallback: Dict[str, float] = {}
        if missing:
            try:
                fallback_loader = rest_fallback or self.rest_fallback
                fallback = {
                    str(key): float(value or 0.0)
                    for key, value in (fallback_loader() or {}).items()
                    if str(key) in missing and float(value or 0.0) > 0
                }
            except Exception as exc:
                logger.debug("Market price REST fallback failed exchange=%s: %s", self.exchange_id, exc)
        prices = {**fallback, **stream_prices}
        if stream_prices and fallback:
            source = "public_websocket+rest_fallback"
        elif stream_prices:
            source = "public_websocket"
        elif fallback:
            source = "rest_fallback"
        else:
            source = "unavailable"
        return PriceFeedSnapshot(
            prices=prices,
            source=source,
            age_ms=int(max(ages, default=0.0) * 1000),
            connected=bool(self._connected),
        )

    def _run(self) -> None:
        try:
            import websocket
        except ImportError:
            return
        backoff = 1.0
        while not self._stop.is_set():
            try:
                def on_open(ws):
                    self._connected = True
                    for message in self._subscribe_messages():
                        ws.send(json.dumps(message, separators=(",", ":")))

                def on_message(ws, raw):
                    payload = _json_payload(raw)
                    if not payload:
                        return
                    if payload.get("ping") is not None:
                        ws.send(json.dumps({"pong": payload["ping"]}))
                        return
                    if payload.get("op") == "ping":
                        ws.send(json.dumps({"op": "pong", "ts": payload.get("ts")}))
                        return
                    for symbol, price in self._parse(payload):
                        self._update(symbol, price)

                def on_error(_ws, error):
                    self._connected = False
                    logger.debug("Public market stream error exchange=%s: %s", self.exchange_id, error)

                def on_close(_ws, _code, _reason):
                    self._connected = False

                self._ws = websocket.WebSocketApp(
                    self._url(),
                    on_open=on_open,
                    on_message=on_message,
                    on_error=on_error,
                    on_close=on_close,
                )
                sslopt: Dict[str, Any] = {}
                verify = _get_requests_verify()
                if verify is False:
                    sslopt = {"cert_reqs": ssl.CERT_NONE, "check_hostname": False}
                elif isinstance(verify, str):
                    sslopt = {"ca_certs": verify}
                self._ws.run_forever(
                    ping_interval=20,
                    ping_timeout=10,
                    sslopt=sslopt,
                    skip_utf8_validation=True,
                )
            except Exception as exc:
                self._connected = False
                logger.debug("Public market stream reconnect exchange=%s: %s", self.exchange_id, exc)
            if self._stop.wait(backoff):
                break
            backoff = min(30.0, backoff * 2.0)

    def _symbols(self) -> list[str]:
        return [
            str(item.get("instrument_id") or item.get("symbol") or "")
            for item in self.instruments
        ]

    def _url(self) -> str:
        swap = self.market_type != "spot"
        if self.exchange_id == "binance":
            streams = "/".join(
                f"{_normalized_symbol(symbol).lower()}@{'markPrice@1s' if swap else 'ticker'}"
                for symbol in self._symbols()
            )
            host = "wss://fstream.binance.com" if swap else "wss://stream.binance.com:9443"
            return f"{host}/stream?streams={streams}"
        if self.exchange_id == "okx":
            return "wss://ws.okx.com/ws/v5/public"
        if self.exchange_id == "bybit":
            return f"wss://stream.bybit.com/v5/public/{'linear' if swap else 'spot'}"
        if self.exchange_id == "bitget":
            return "wss://ws.bitget.com/v3/ws/public"
        if self.exchange_id == "gate":
            return "wss://fx-ws.gateio.ws/v4/ws/usdt" if swap else "wss://api.gateio.ws/ws/v4/"
        return "wss://api.hbdm.com/swap-ws" if swap else "wss://api.huobi.pro/ws"

    def _subscribe_messages(self) -> list[Dict[str, Any]]:
        swap = self.market_type != "spot"
        if self.exchange_id == "okx":
            return [{"op": "subscribe", "args": [
                {
                    "channel": "tickers",
                    "instId": (
                        symbol.replace("/", "-").upper()
                        if symbol.upper().endswith("-SWAP") or not swap
                        else symbol.replace("/", "-").upper() + "-SWAP"
                    ),
                }
                for symbol in self._symbols()
            ]}]
        if self.exchange_id == "bybit":
            return [{"op": "subscribe", "args": [f"tickers.{_normalized_symbol(symbol)}" for symbol in self._symbols()]}]
        if self.exchange_id == "bitget":
            return [{"op": "subscribe", "args": [
                {
                    "instType": "usdt-futures" if swap else "spot",
                    "topic": "ticker",
                    "symbol": _normalized_symbol(symbol),
                }
                for symbol in self._symbols()
            ]}]
        if self.exchange_id == "gate":
            channel = "futures.tickers" if swap else "spot.tickers"
            return [{"time": int(time.time()), "channel": channel, "event": "subscribe", "payload": [
                symbol.replace("/", "_").upper() for symbol in self._symbols()
            ]}]
        if self.exchange_id == "htx":
            return [
                {"sub": f"market.{symbol.replace('/', '').lower()}.detail", "id": str(index + 1)}
                for index, symbol in enumerate(self._symbols())
            ]
        return []

    def _parse(self, payload: Dict[str, Any]) -> list[tuple[str, float]]:
        if self.exchange_id == "binance":
            data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
            price = (
                (data.get("c") or data.get("p"))
                if self.market_type == "spot"
                else (data.get("p") or data.get("c"))
            )
            return [(str(data.get("s") or ""), float(price or 0.0))]
        if self.exchange_id == "okx":
            return [
                (str(item.get("instId") or ""), float(
                    (
                        (item.get("last") or item.get("markPx"))
                        if self.market_type == "spot"
                        else (item.get("markPx") or item.get("last"))
                    ) or 0.0
                ))
                for item in payload.get("data") or [] if isinstance(item, dict)
            ]
        if self.exchange_id == "bybit":
            data = payload.get("data") or {}
            rows = data if isinstance(data, list) else [data]
            return [
                (str(item.get("symbol") or ""), float(
                    (
                        (item.get("lastPrice") or item.get("markPrice"))
                        if self.market_type == "spot"
                        else (item.get("markPrice") or item.get("lastPrice"))
                    ) or 0.0
                ))
                for item in rows if isinstance(item, dict)
            ]
        if self.exchange_id == "bitget":
            return [
                (str(item.get("symbol") or ""), float(
                    (
                        (item.get("lastPr") or item.get("lastPrice") or item.get("markPrice"))
                        if self.market_type == "spot"
                        else (item.get("markPrice") or item.get("lastPr") or item.get("lastPrice"))
                    ) or 0.0
                ))
                for item in payload.get("data") or [] if isinstance(item, dict)
            ]
        if self.exchange_id == "gate":
            result = payload.get("result") or []
            rows = result if isinstance(result, list) else [result]
            return [
                (str(item.get("contract") or item.get("currency_pair") or ""), float(
                    (
                        (item.get("last") or item.get("mark_price"))
                        if self.market_type == "spot"
                        else (item.get("mark_price") or item.get("last"))
                    ) or 0.0
                ))
                for item in rows if isinstance(item, dict)
            ]
        tick = payload.get("tick") or {}
        channel = str(payload.get("ch") or "")
        symbol = channel.split(".")[1] if channel.count(".") >= 2 else ""
        return [(symbol, float(tick.get("close") or tick.get("price") or 0.0))] if isinstance(tick, dict) else []

    def _update(self, symbol: str, price: float) -> None:
        try:
            value = float(price or 0.0)
        except Exception:
            return
        key = self._aliases.get(_normalized_symbol(symbol))
        if not key or value <= 0:
            return
        with self._lock:
            self._prices[key] = (value, time.monotonic())
