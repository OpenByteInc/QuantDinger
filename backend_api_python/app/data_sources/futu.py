"""
Futu OpenD market-data adapter for HKStock / USStock.

Used when a strategy's execution account is Futu so signal bars match the
broker. Falls back is handled by DataSourceFactory / callers — this module
raises identifiable errors instead of silently returning empty lists when
permissions or OpenD connectivity fail.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.data_sources.base import BaseDataSource
from app.utils.logger import get_logger

logger = get_logger(__name__)

_TF_TO_KLTYPE = {
    "1m": "K_1M",
    "3m": "K_3M",
    "5m": "K_5M",
    "15m": "K_15M",
    "30m": "K_30M",
    "1H": "K_60M",
    "60m": "K_60M",
    "4H": "K_60M",  # OpenD has no native 4H; caller may resample
    "1D": "K_DAY",
    "1W": "K_WEEK",
}


class FutuDataSourceError(RuntimeError):
    """Raised for permission / quota / OpenD failures (not empty markets)."""

    def __init__(self, code: str, message: str = ""):
        self.code = code
        super().__init__(f"{code}:{message}" if message else code)


class FutuDataSource(BaseDataSource):
    """Historical K-line + ticker via FutuOpenD."""

    name = "Futu/OpenD"
    close_after_request = True

    def __init__(
        self,
        *,
        market: str = "HKStock",
        host: Optional[str] = None,
        port: Optional[int] = None,
        exchange_config: Optional[Dict[str, Any]] = None,
    ):
        self.market = "USStock" if str(market or "").strip() == "USStock" else "HKStock"
        self._exchange_config = dict(exchange_config or {})
        self._host = str(
            host
            or self._exchange_config.get("futu_host")
            or self._exchange_config.get("host")
            or os.getenv("FUTU_OPEND_HOST")
            or "127.0.0.1"
        ).strip()
        self._port = int(
            port
            or self._exchange_config.get("futu_port")
            or self._exchange_config.get("port")
            or os.getenv("FUTU_OPEND_PORT")
            or 11111
        )
        self._client = None

    @classmethod
    def for_exchange_config(cls, exchange_config: Dict[str, Any], market: str = "HKStock") -> "FutuDataSource":
        return cls(market=market, exchange_config=exchange_config or {})

    def _get_client(self):
        if self._client is not None and getattr(self._client, "connected", False):
            return self._client
        from app.services.futu_trading.config import config_from_exchange_config
        from app.services.futu_trading.session_pool import get_futu_session_pool

        config_data = dict(self._exchange_config)
        config_data["futu_host"] = self._host
        config_data["futu_port"] = self._port
        config_data.setdefault("market_category", self.market)
        config_data.setdefault("exchange_id", "futu")
        cfg = config_from_exchange_config(config_data)
        # Quote-only consumers never need to retain a trading password.
        cfg.unlock_password = ""
        config_data["unlock_password"] = ""
        try:
            client = get_futu_session_pool().acquire(config_data, mode="quote")
        except Exception as exc:
            raise FutuDataSourceError("FUTU_OPEND_UNREACHABLE", f"{self._host}:{self._port}") from exc
        self._client = client
        return client

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.disconnect()
            except Exception:
                pass
            self._client = None

    def get_ticker(self, symbol: str) -> Dict[str, Any]:
        try:
            client = self._get_client()
            quote = client.get_quote(symbol, self.market)
            return {
                "last": float(quote.get("last") or 0),
                "bid": float(quote.get("bid") or 0),
                "ask": float(quote.get("ask") or 0),
                "high": float(quote.get("high") or 0),
                "low": float(quote.get("low") or 0),
                "volume": float(quote.get("volume") or 0),
                "previousClose": float(quote.get("close") or 0),
                "symbol": quote.get("symbol") or symbol,
                "source": "futu",
            }
        except FutuDataSourceError:
            raise
        except Exception as exc:
            msg = str(exc)
            if "PERMISSION" in msg.upper() or "权限" in msg or "FUTU_QUOTE" in msg:
                raise FutuDataSourceError("FUTU_QUOTE_PERMISSION_DENIED", msg) from exc
            if "UNREACHABLE" in msg.upper() or "connect" in msg.lower():
                raise FutuDataSourceError("FUTU_OPEND_UNREACHABLE", msg) from exc
            raise FutuDataSourceError("FUTU_API_ERROR", msg) from exc

    def get_kline(
        self,
        symbol: str,
        timeframe: str,
        limit: int,
        before_time: Optional[int] = None,
        after_time: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        tf = str(timeframe or "1D").strip()
        ktype = _TF_TO_KLTYPE.get(tf) or _TF_TO_KLTYPE.get(tf.upper()) or "K_DAY"
        lim = max(int(limit or 300), 1)

        end_dt = None
        start_dt = None
        if before_time:
            end_dt = datetime.fromtimestamp(int(before_time), tz=timezone.utc)
        if after_time:
            start_dt = datetime.fromtimestamp(int(after_time), tz=timezone.utc)
        if end_dt is None:
            end_dt = datetime.now(timezone.utc)
        if start_dt is None:
            # Rough lookback; OpenD pages results.
            seconds = {
                "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
                "1H": 3600, "60m": 3600, "4H": 14400, "1D": 86400, "1W": 604800,
            }.get(tf, 86400)
            start_dt = end_dt - timedelta(seconds=seconds * lim * 1.5)

        try:
            from app.services.futu_trading.timezones import market_timezone

            exchange_tz = market_timezone(self.market)
            client = self._get_client()
            rows = client.get_history_kline(
                symbol,
                market_type=self.market,
                ktype=ktype,
                start=start_dt.astimezone(exchange_tz).strftime("%Y-%m-%d"),
                end=end_dt.astimezone(exchange_tz).strftime("%Y-%m-%d"),
                max_count=lim + 5,
                autype="QFQ",
            )
        except FutuDataSourceError:
            raise
        except Exception as exc:
            msg = str(exc)
            if "QUOTA" in msg.upper() or "额度" in msg:
                raise FutuDataSourceError("FUTU_QUOTE_QUOTA_EXCEEDED", msg) from exc
            if "PERMISSION" in msg.upper() or "权限" in msg or "FUTU_QUOTE" in msg:
                raise FutuDataSourceError("FUTU_QUOTE_PERMISSION_DENIED", msg) from exc
            if "UNREACHABLE" in msg.upper() or "connect" in msg.lower():
                raise FutuDataSourceError("FUTU_OPEND_UNREACHABLE", msg) from exc
            raise FutuDataSourceError("FUTU_API_ERROR", msg) from exc

        # Tag source for observability (non-breaking extra field ignored by most consumers)
        for row in rows:
            row["source"] = "futu"

        # 4H resampling from 60m when requested
        if tf == "4H" and rows:
            rows = self._resample_hours(rows, hours=4)

        return self.filter_and_limit(
            rows,
            limit=lim,
            before_time=before_time,
            after_time=after_time,
            truncate=(after_time is None),
        )

    def _resample_hours(
        self,
        rows: List[Dict[str, Any]],
        hours: int = 4,
    ) -> List[Dict[str, Any]]:
        if not rows:
            return rows
        from app.services.futu_trading.timezones import market_timezone

        bucket_seconds = max(1, int(hours)) * 3600
        exchange_tz = market_timezone(self.market)
        buckets: Dict[tuple[Any, int], Dict[str, Any]] = {}
        for row in sorted(rows, key=lambda item: int(item.get("time") or 0)):
            t = int(row.get("time") or 0)
            local_time = datetime.fromtimestamp(t, tz=timezone.utc).astimezone(exchange_tz)
            trading_date = local_time.date()
            session_open = local_time.replace(hour=9, minute=30, second=0, microsecond=0)
            bucket_index = int((local_time - session_open).total_seconds() // bucket_seconds)
            key = (trading_date, bucket_index)
            cur = buckets.get(key)
            if cur is None:
                buckets[key] = {
                    "time": t,
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row.get("volume") or 0),
                    "source": "futu",
                }
            else:
                cur["high"] = max(cur["high"], float(row["high"]))
                cur["low"] = min(cur["low"], float(row["low"]))
                cur["close"] = float(row["close"])
                cur["volume"] = float(cur.get("volume") or 0) + float(row.get("volume") or 0)
        return sorted(buckets.values(), key=lambda item: item["time"])
