"""Keep live signal progress behind confirmed, current market data."""

from datetime import datetime, timedelta, timezone

import pandas as pd

from app.data_sources.candle_calendar import completed_candle_open
from app.data_sources.base import timeframe_seconds
from app.services.market_schedule import (
    equity_data_market, equity_bar_session_date, equity_daily_bar_cutoff,
    equity_weekly_bar_cutoff, equity_intraday_bar_open,
)


def expected_bar(member, frequency, now=None, frame=None):
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    market = equity_data_market(str(member.get("market") or "Crypto"), member)
    seconds = timeframe_seconds(frequency)
    if market in {"USStock", "HKStock"}:
        if seconds == 86400:
            return equity_daily_bar_cutoff(market, current)
        if seconds == 604800:
            return equity_weekly_bar_cutoff(market, current)
        return equity_intraday_bar_open(market, seconds, current)
    venue = str(member.get("exchange_id") or "")
    if not venue and market.lower() in {"crypto", "cryptocurrency"} and frame is not None and not frame.empty:
        origin = int(pd.Timestamp(frame.index[-1]).timestamp()) % seconds
        stamp = int((current.timestamp() - origin) // seconds) * seconds + origin - seconds
    else:
        stamp = completed_candle_open(current.timestamp(), frequency, venue, str(member.get("market_type") or "spot"))
    return pd.Timestamp(datetime.fromtimestamp(stamp, timezone.utc)).tz_localize(None)


def frame_is_current(frame, member, frequency, now=None):
    if frame is None or frame.empty:
        return False
    expected = expected_bar(member, frequency, now, frame)
    return frame_covers_bar(frame, member, frequency, expected)


def frame_covers_bar(frame, member, frequency, expected):
    if frame is None or frame.empty:
        return False
    latest = pd.Timestamp(frame.index[-1])
    if latest.tzinfo is not None:
        latest = latest.tz_convert("UTC").tz_localize(None)
    market = equity_data_market(str(member.get("market") or "Crypto"), member)
    if market in {"USStock", "HKStock"} and timeframe_seconds(frequency) >= 86400:
        actual_day = equity_bar_session_date(latest, market)
        expected_day = equity_bar_session_date(expected, market)
        if timeframe_seconds(frequency) == 604800:
            return actual_day.isocalendar()[:2] == expected_day.isocalendar()[:2]
        return actual_day == expected_day
    return latest == expected


def signal_bar_targets(candidates, frequency, frames, now=None):
    current = now or datetime.now(timezone.utc)
    return tuple(
        (str(member["key"]), str(expected_bar(member, frequency, current, frames.get(str(member["key"])))))
        for member in candidates
    )


def runtime_frames_ready(bundles, candidates, now=None):
    if not bundles or not candidates:
        return False
    current = now or datetime.now(timezone.utc)
    # A clock boundary is not a provider finality acknowledgement. Delay
    # evaluation briefly, without advancing the processed-bar watermark.
    eligible = current - timedelta(seconds=2)
    for frequency, frames in bundles.items():
        for member in candidates:
            frame = frames.get(str(member["key"]))
            if not frame_is_current(frame, member, frequency, current):
                return False
            if expected_bar(member, frequency, eligible, frame) != expected_bar(member, frequency, current, frame):
                return False
    return True


def candles_settling(candidates, frequency, frames, now=None):
    current = now or datetime.now(timezone.utc)
    return signal_bar_targets(candidates, frequency, frames, current) != signal_bar_targets(
        candidates, frequency, frames, current - timedelta(seconds=2)
    )
