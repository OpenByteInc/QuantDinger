"""Quote-only FutuOpenD client used by market-data code paths."""

from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional

from app.services.futu_trading.client import _ensure_futu
from app.services.futu_trading.config import FutuConfig, validate_opend_host
from app.services.futu_trading.mappers import classify_futu_error, safe_float
from app.services.futu_trading.symbols import (
    format_display_symbol,
    infer_market_category,
    to_futu_code,
)
from app.services.futu_trading.timezones import futu_time_key_to_timestamp
from app.utils.logger import get_logger

logger = get_logger(__name__)


class FutuQuoteClient:
    """Small wrapper that opens only ``OpenQuoteContext`` (never a trade context)."""

    def __init__(self, config: Optional[FutuConfig] = None):
        self.config = config or FutuConfig()
        self._quote_ctx = None
        self._connected = False
        self._lock = threading.RLock()

    @property
    def connected(self) -> bool:
        return bool(self._connected and self._quote_ctx is not None)

    def connect(self) -> bool:
        with self._lock:
            if self.connected:
                return True
            try:
                validate_opend_host(self.config.host)
                ft = _ensure_futu()
                kwargs: Dict[str, Any] = {
                    "host": self.config.host,
                    "port": int(self.config.port),
                }
                if self.config.is_encrypt is not None:
                    kwargs["is_encrypt"] = self.config.is_encrypt
                self._quote_ctx = ft.OpenQuoteContext(**kwargs)
                ret, data = self._quote_ctx.get_global_state()
                if ret != ft.RET_OK:
                    raise RuntimeError(f"OpenD quote probe failed: {data}")
                self._connected = True
                return True
            except Exception as exc:
                logger.error("Futu quote connection failed: %s", exc)
                self.close()
                return False

    def close(self, force: bool = False) -> None:
        if getattr(self, "_futu_pooled", False) and not force:
            from app.services.futu_trading.session_pool import get_futu_session_pool

            get_futu_session_pool().release(self)
            return
        with self._lock:
            if self._quote_ctx is not None:
                try:
                    self._quote_ctx.close()
                except Exception as exc:
                    logger.debug("Futu quote context close error: %s", exc)
            self._quote_ctx = None
            self._connected = False

    disconnect = close

    def _ensure_connected(self) -> None:
        if not self.connected and not self.connect():
            raise ConnectionError("Cannot connect to FutuOpenD quote service")

    def get_quote(self, symbol: str, market_type: str = "HKStock") -> Dict[str, Any]:
        with self._lock:
            self._ensure_connected()
            ft = _ensure_futu()
            market = market_type or infer_market_category(symbol)
            code = to_futu_code(symbol, market)
            ret, data = self._quote_ctx.get_market_snapshot([code])
            if ret != ft.RET_OK or data is None or len(data) == 0:
                error_code, message = classify_futu_error(
                    data if ret != ft.RET_OK else f"empty snapshot for {code}"
                )
                raise RuntimeError(f"{error_code}:{message}")
            row = data.iloc[0] if hasattr(data, "iloc") else data[0]
            snap = dict(row)
            return {
                "success": True,
                "symbol": format_display_symbol(code),
                "futu_code": code,
                "bid": safe_float(snap.get("bid_price") or snap.get("bid")),
                "ask": safe_float(snap.get("ask_price") or snap.get("ask")),
                "last": safe_float(snap.get("last_price") or snap.get("price") or snap.get("last")),
                "high": safe_float(snap.get("high_price") or snap.get("high")),
                "low": safe_float(snap.get("low_price") or snap.get("low")),
                "volume": safe_float(snap.get("volume")),
                "close": safe_float(snap.get("prev_close_price") or snap.get("close")),
                "raw": snap,
            }

    def get_history_kline(
        self,
        symbol: str,
        *,
        market_type: str = "",
        ktype: str = "K_DAY",
        start: Optional[str] = None,
        end: Optional[str] = None,
        max_count: int = 500,
        autype: str = "QFQ",
    ) -> List[Dict[str, Any]]:
        with self._lock:
            self._ensure_connected()
            ft = _ensure_futu()
            market = market_type or infer_market_category(symbol)
            code = to_futu_code(symbol, market)
            ktype_enum = getattr(ft.KLType, ktype, ft.KLType.K_DAY)
            autype_enum = getattr(ft.AuType, autype, ft.AuType.QFQ)
            page_req_key = None
            rows: List[Dict[str, Any]] = []
            remaining = max(1, int(max_count or 500))
            while remaining > 0:
                ret, data, page_req_key = self._quote_ctx.request_history_kline(
                    code=code,
                    start=start,
                    end=end,
                    ktype=ktype_enum,
                    autype=autype_enum,
                    max_count=min(1000, remaining),
                    page_req_key=page_req_key,
                )
                if ret != ft.RET_OK:
                    error_code, message = classify_futu_error(data)
                    raise RuntimeError(f"{error_code}:{message}")
                if data is None or len(data) == 0:
                    break
                records = data.to_dict("records") if hasattr(data, "to_dict") else list(data)
                for record in records:
                    try:
                        timestamp = futu_time_key_to_timestamp(
                            record.get("time_key") or record.get("time"),
                            market,
                        )
                    except (TypeError, ValueError):
                        continue
                    rows.append({
                        "time": timestamp,
                        "open": safe_float(record.get("open")),
                        "high": safe_float(record.get("high")),
                        "low": safe_float(record.get("low")),
                        "close": safe_float(record.get("close")),
                        "volume": safe_float(record.get("volume")),
                    })
                remaining -= len(records)
                if not page_req_key:
                    break
            rows.sort(key=lambda item: item["time"])
            return rows

    def subscribe_quote(self, symbols: List[str], market_type: str = "") -> bool:
        with self._lock:
            self._ensure_connected()
            ft = _ensure_futu()
            codes = [
                to_futu_code(symbol, market_type or infer_market_category(symbol))
                for symbol in symbols
                if symbol
            ]
            if not codes:
                return True
            ret, error = self._quote_ctx.subscribe(codes, [ft.SubType.QUOTE])
            if ret != ft.RET_OK:
                error_code, message = classify_futu_error(error)
                raise RuntimeError(f"{error_code}:{message}")
            return True
