"""Native crypto candle boundaries, independent of strategy and transport code."""

from app.data_sources.base import timeframe_seconds


def candle_origin(timeframe: str, venue: str = "", market_type: str = "spot") -> int:
    seconds = timeframe_seconds(timeframe)
    venue = str(venue or "").lower()
    offset = -8 * 3600 if venue in {"okx", "bitget", "htx", "huobi"} else 0
    if seconds == 604800:
        if venue == "gate" and market_type == "swap":
            return 0
        offset += 4 * 86400
    return offset % seconds


def candle_open(timestamp: float, timeframe: str, venue: str = "", market_type: str = "spot") -> int:
    seconds = timeframe_seconds(timeframe)
    origin = candle_origin(timeframe, venue, market_type)
    return int((timestamp - origin) // seconds) * seconds + origin


def completed_candle_open(timestamp: float, timeframe: str, venue: str = "", market_type: str = "spot") -> int:
    return candle_open(timestamp, timeframe, venue, market_type) - timeframe_seconds(timeframe)
