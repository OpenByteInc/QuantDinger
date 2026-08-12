"""Exchange-time conversion helpers for Futu market data."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.services.futu_trading.mappers import safe_float

_MARKET_TIMEZONES = {
    "HKStock": ZoneInfo("Asia/Hong_Kong"),
    "USStock": ZoneInfo("America/New_York"),
}


def market_timezone(market: str) -> ZoneInfo:
    return _MARKET_TIMEZONES.get(str(market or ""), ZoneInfo("UTC"))


def futu_time_key_to_timestamp(value: Any, market: str) -> int:
    """Interpret Futu's naive ``time_key`` in the exchange's local timezone."""
    if not isinstance(value, str):
        return int(safe_float(value))
    local_dt = datetime.strptime(value[:19], "%Y-%m-%d %H:%M:%S")
    return int(local_dt.replace(tzinfo=market_timezone(market)).timestamp())
