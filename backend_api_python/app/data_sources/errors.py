from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping


class DataSourceError(Exception):
    """Base class for data source related errors."""


@dataclass(frozen=True)
class MarketDataFailure:
    """Structured public-market-data failure safe to expose to the UI."""

    code: str
    message: str
    technical_detail: str = ""
    exchange_id: str = ""
    market_type: str = ""
    symbol: str = ""
    timeframe: str = ""
    retryable: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "technical_detail": self.technical_detail,
            "exchange_id": self.exchange_id,
            "market_type": self.market_type,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "retryable": self.retryable,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "MarketDataFailure":
        return cls(
            code=str(value.get("code") or "no_market_data"),
            message=str(value.get("message") or "No usable market data is available."),
            technical_detail=str(value.get("technical_detail") or "")[:500],
            exchange_id=str(value.get("exchange_id") or ""),
            market_type=str(value.get("market_type") or ""),
            symbol=str(value.get("symbol") or ""),
            timeframe=str(value.get("timeframe") or ""),
            retryable=bool(value.get("retryable", True)),
        )


class MarketDataUnavailableError(DataSourceError):
    """Carries a categorized failure through strategy-data loading layers."""

    def __init__(self, failure: MarketDataFailure):
        self.failure = failure
        super().__init__(f"marketData.{failure.code}")


def classify_market_data_failure(
    error: Any,
    *,
    exchange_id: str = "",
    market_type: str = "",
    symbol: str = "",
    timeframe: str = "",
) -> MarketDataFailure:
    """Map provider-specific errors to a small stable frontend contract."""
    detail = str(error or "").strip()
    detail = re.sub(
        r"(?i)(https?://)([^/@\s:]+):([^/@\s]+)@",
        r"\1***:***@",
        detail,
    )
    text = detail.lower()
    if any(token in text for token in (
        "451",
        "restricted location",
        "legal reasons",
        "region restricted",
        "block access from your country",
        "blocked access from your country",
    )):
        code = "region_restricted"
        message = "The exchange market-data endpoint is unavailable in this region."
        retryable = False
    elif any(token in text for token in ("proxyerror", "proxy error", "proxyconnect", "proxy connection", "tunnel connection", "socks")):
        code = "proxy_failure"
        message = "The market-data proxy could not connect to the exchange."
        retryable = True
    elif any(token in text for token in ("does not have market symbol", "symbol not found", "invalid symbol", "market does not exist", "trading pair not found")):
        code = "symbol_not_found"
        message = "The trading pair does not exist for this exchange and market type."
        retryable = False
    elif any(token in text for token in ("429", "too many requests", "rate limit", "ratelimit")) or re.search(r"http[ :]+418\b", text):
        code = "rate_limited"
        message = "The exchange rate limit was reached. Market data will be retried."
        retryable = True
    elif any(token in text for token in (
        "incomplete k-line",
        "incomplete kline",
        "incomplete candle",
        "incomplete market data",
        "incomplete history",
    )):
        code = "incomplete_market_data"
        message = (
            "The exchange returned incomplete K-line coverage. "
            "The missing interval will be retried."
        )
        retryable = True
    elif any(token in text for token in ("timeout", "timed out", "network error", "connection reset", "connection refused", "exchange not available", "service unavailable", "502", "503", "504")):
        code = "exchange_unavailable"
        message = "The exchange market-data service is temporarily unreachable."
        retryable = True
    elif "timeframe" in text and any(token in text for token in ("unsupported", "not support", "cannot serve")):
        code = "unsupported_timeframe"
        message = "This exchange does not provide the requested K-line timeframe."
        retryable = False
    else:
        code = "no_market_data"
        message = "The exchange returned no usable market data."
        retryable = True
    return MarketDataFailure(
        code=code,
        message=message,
        technical_detail=detail[:500],
        exchange_id=str(exchange_id or "").strip().lower(),
        market_type=str(market_type or "").strip().lower(),
        symbol=str(symbol or ""),
        timeframe=str(timeframe or ""),
        retryable=retryable,
    )


class UnsupportedMarketError(DataSourceError):
    """Raised when a requested market type is not supported by DataSourceFactory."""

    def __init__(self, market: str):
        self.market = str(market or "")
        super().__init__(f"Unsupported market type: {self.market}")
