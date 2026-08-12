"""Bounded Futu quote cache for strategy risk ticks.

Subscribes (best-effort) to OpenD QUOTE for the strategy's symbols and
refreshes a local last-price cache. Falls back to snapshot polling when
push is unavailable.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, Iterable, List, Mapping, Optional

from app.utils.logger import get_logger

logger = get_logger(__name__)


class FutuQuoteFeed:
    """Per-runtime quote cache backed by FutuClient.get_quote / subscribe."""

    def __init__(
        self,
        *,
        exchange_config: Dict[str, Any],
        instruments: Iterable[Mapping[str, Any]],
        poll_interval_sec: float = 2.0,
        max_symbols: int = 50,
    ) -> None:
        self.exchange_config = dict(exchange_config or {})
        self.instruments = [dict(item) for item in instruments][: max(1, int(max_symbols))]
        self.poll_interval_sec = max(0.5, float(poll_interval_sec))
        self._prices: Dict[str, float] = {}
        self._updated_at = 0.0
        self._connected = False
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._client = None
        self._last_error = ""

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def last_error(self) -> str:
        return self._last_error

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="FutuQuoteFeed", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        if self._client is not None:
            try:
                self._client.disconnect()
            except Exception:
                pass
            self._client = None
        self._connected = False

    def snapshot(self, max_age_seconds: float = 10.0) -> Dict[str, Any]:
        age_ms = int(max(0.0, (time.time() - self._updated_at) * 1000))
        stale = (time.time() - self._updated_at) > float(max_age_seconds)
        return {
            "prices": dict(self._prices),
            "source": "futu_quote" if self._prices and not stale else "futu_stale",
            "age_ms": age_ms,
            "connected": self._connected,
            "error": self._last_error,
        }

    def _run(self) -> None:
        try:
            from app.services.futu_trading.config import config_from_exchange_config
            from app.services.futu_trading.quote_client import FutuQuoteClient

            config = config_from_exchange_config(self.exchange_config)
            config.unlock_password = ""
            self._client = FutuQuoteClient(config)
            if not self._client.connect():
                raise RuntimeError("FutuOpenD quote connection failed")
            codes: List[str] = []
            for item in self.instruments:
                symbol = str(item.get("symbol") or "")
                market = str(item.get("market") or "HKStock")
                if symbol:
                    codes.append(symbol)
                    try:
                        self._client.subscribe_quote([symbol], market)
                    except Exception:
                        pass
            self._connected = True
            while not self._stop.is_set():
                self._poll_once()
                self._stop.wait(self.poll_interval_sec)
        except Exception as exc:
            self._last_error = str(exc)
            self._connected = False
            logger.warning("FutuQuoteFeed stopped: %s", exc)

    def _poll_once(self) -> None:
        if self._client is None:
            return
        updated = False
        for item in self.instruments:
            key = str(item.get("key") or "")
            symbol = str(item.get("symbol") or "")
            market = str(item.get("market") or "HKStock")
            if not key or not symbol:
                continue
            try:
                quote = self._client.get_quote(symbol, market)
                if isinstance(quote, dict) and quote.get("success"):
                    price = float(quote.get("last") or quote.get("close") or 0.0)
                    if price > 0:
                        self._prices[key] = price
                        updated = True
            except Exception as exc:
                self._last_error = str(exc)
        if updated:
            self._updated_at = time.time()
            self._connected = True
