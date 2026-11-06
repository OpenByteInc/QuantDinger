"""Exchange-calendar scheduling for portfolio deployments."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd


CALENDAR_BY_MARKET = {
    "USStock": "XNYS",
    "HKStock": "XHKG",
    "AStock": "XSHG",
    "CNStock": "XSHG",
}


class MarketScheduleError(RuntimeError):
    pass


def equity_data_market(market: str, product=None, api_family: str = "") -> str:
    """Only the stock API uses underlying exchange data; tokens use venue candles."""
    if market == "Crypto" and str(api_family or (product or {}).get("api_family") or "").lower() == "stock":
        underlying = str((product or {}).get("underlying_market") or "")
        if underlying not in {"USStock", "HKStock"}:
            raise MarketScheduleError("portfolio.marketCalendarUnsupported")
        return underlying
    return market


def equity_bar_session_date(value, market: str):
    stamp = pd.Timestamp(value)
    stamp = stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")
    return (stamp.tz_convert("Asia/Hong_Kong") if market == "HKStock" else stamp).date()


def equity_daily_bar_cutoff(market: str, now: Optional[datetime] = None) -> pd.Timestamp:
    """Include provider daily labels only through the last completed session."""
    session = latest_completed_session(market, now, data_delay_minutes=15)
    day = pd.Timestamp(session).tz_localize(None)
    if market == "HKStock":
        day = day.tz_localize("Asia/Hong_Kong").tz_convert("UTC").tz_localize(None)
    return day + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)


def _equity_week_sessions(market: str, label):
    day = pd.Timestamp(label).tz_localize(None).normalize()
    monday = day - pd.Timedelta(days=day.weekday())
    return _calendar(market).sessions_in_range(monday, monday + pd.Timedelta(days=6))


def equity_weekly_bar_cutoff(market: str, now: Optional[datetime] = None) -> pd.Timestamp:
    current = _utc(now)
    session = latest_completed_session(market, current, data_delay_minutes=15)
    sessions = _equity_week_sessions(market, session)
    if _python_utc(_calendar(market).session_close(sessions[-1])) + timedelta(minutes=15) > current:
        sessions = _equity_week_sessions(market, session - timedelta(days=7))
    label = pd.Timestamp(sessions[-1]).tz_localize(None).normalize()
    if market == "HKStock":
        label = label.tz_localize("Asia/Hong_Kong").tz_convert("UTC").tz_localize(None)
    return label + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)


def equity_intraday_bar_open(market: str, seconds: int, now: Optional[datetime] = None) -> pd.Timestamp:
    """Find the last completed regular-session bar, including shortened tails."""
    current = _utc(now)
    calendar = _calendar(market)
    sessions = calendar.sessions_in_range(
        pd.Timestamp((current - timedelta(days=20)).date()), pd.Timestamp(current.date())
    )
    for session in reversed(sessions):
        opens, closes = _python_utc(calendar.session_open(session)), _python_utc(calendar.session_close(session))
        breaks = calendar.session_break_start(session), calendar.session_break_end(session)
        segments = [(opens, closes)] if pd.isna(breaks[0]) else [
            (opens, _python_utc(breaks[0])), (_python_utc(breaks[1]), closes)
        ]
        for segment_start, end in reversed(segments):
            # Provider intraday grids remain anchored to the session open,
            # including buckets that overlap the end of the lunch break.
            start = opens
            if current >= end:
                index = max(0, int(((end - start).total_seconds() - 1) // seconds))
            elif current >= start + timedelta(seconds=seconds):
                index = int((current - start).total_seconds() // seconds) - 1
            else:
                continue
            bar_start = start + timedelta(seconds=index * seconds)
            if bar_start + timedelta(seconds=seconds) <= segment_start:
                continue
            bar_end = bar_start + timedelta(seconds=seconds)
            closed_at = max(min(bar_end, b) for a, b in segments if bar_start < b and bar_end > a)
            if closed_at > current:
                continue
            return pd.Timestamp(bar_start).tz_localize(None)
    raise MarketScheduleError("portfolio.noCompletedMarketSession")


def filter_closed_equity_klines(rows, timeframe: str, market: str, now: Optional[datetime] = None):
    """Use exchange sessions, not label plus 24 hours, to confirm equity bars."""
    from app.data_sources.base import timeframe_seconds
    current = _utc(now)
    seconds = timeframe_seconds(timeframe)
    calendar = _calendar(market)
    accepted = {}
    for row in rows or []:
        stamp = pd.Timestamp(int(row["time"]), unit="ms" if int(row["time"]) > 10_000_000_000 else "s", tz="UTC")
        day = pd.Timestamp(equity_bar_session_date(stamp, market))
        if seconds >= 604800:
            sessions = _equity_week_sessions(market, day)
            if not len(sessions):
                continue
            closes = _python_utc(calendar.session_close(sessions[-1])) + timedelta(minutes=15)
        elif seconds >= 86400:
            if not calendar.is_session(day):
                continue
            closes = _python_utc(calendar.session_close(day)) + timedelta(minutes=15)
        else:
            if not calendar.is_session(day):
                continue
            opens, end = calendar.session_open(day), calendar.session_close(day)
            break_start, break_end = calendar.session_break_start(day), calendar.session_break_end(day)
            segments = [(opens, end)] if pd.isna(break_start) else [(opens, break_start), (break_end, end)]
            bar_end = stamp + pd.Timedelta(seconds=seconds)
            intersections = [min(bar_end, b) for a, b in segments if stamp < b and bar_end > a]
            if not intersections:
                continue
            closes = _python_utc(max(intersections))
        if closes <= current:
            accepted[row["time"]] = row
    return [accepted[key] for key in sorted(accepted)]


def resample_equity_klines(rows, market: str, source_seconds: int, target_seconds: int):
    """Aggregate complete calendar buckets using only expected trading intervals."""
    calendar = _calendar(market)
    buckets = {}
    expected = {}
    for row in rows or []:
        stamp = pd.Timestamp(int(row["time"]), unit="s", tz="UTC")
        day = pd.Timestamp(equity_bar_session_date(stamp, market))
        if not calendar.is_session(day):
            continue
        if target_seconds == 604800 and source_seconds == 86400:
            sessions = _equity_week_sessions(market, day)
            start = day - pd.Timedelta(days=day.weekday())
            key = int(start.tz_localize(calendar.tz).timestamp())
            expected[key] = {session.date() for session in sessions}
            source_key = day.date()
        else:
            opens, closes = calendar.session_open(day), calendar.session_close(day)
            break_start, break_end = calendar.session_break_start(day), calendar.session_break_end(day)
            segments = [(opens, closes)] if pd.isna(break_start) else [(opens, break_start), (break_end, closes)]
            source_end = stamp + pd.Timedelta(seconds=source_seconds)
            if stamp < opens or not any(stamp < b and source_end > a for a, b in segments):
                continue
            offset = int((stamp - opens).total_seconds()) // target_seconds * target_seconds
            start = opens + pd.Timedelta(seconds=offset)
            end = min(start + pd.Timedelta(seconds=target_seconds), closes)
            key = int(start.timestamp())
            expected[key] = {
                value for value in range(key, int(end.timestamp()), source_seconds)
                if any(value < int(b.timestamp()) and value + source_seconds > int(a.timestamp()) for a, b in segments)
            }
            source_key = int(stamp.timestamp())
        buckets.setdefault(key, {})[source_key] = row
    merged = []
    for key, bucket in sorted(buckets.items()):
        if not expected[key].issubset(bucket):
            continue
        chunk = [bucket[item] for item in sorted(expected[key])]
        merged.append({
            "time": key, "open": chunk[0]["open"], "close": chunk[-1]["close"],
            "high": max(row["high"] for row in chunk), "low": min(row["low"] for row in chunk),
            "volume": round(sum(row.get("volume", 0) for row in chunk), 2),
        })
    return merged


def equity_daily_execution_session(market: str, regular_only: bool = True, now: Optional[datetime] = None) -> Optional[datetime]:
    """Use the next regular open for brokers, or completed reference data for venues."""
    current = _utc(now)
    if not regular_only:
        return latest_completed_session(market, current, data_delay_minutes=15)
    calendar = _calendar(market)
    today = pd.Timestamp(pd.Timestamp(current).tz_convert(calendar.tz).date())
    if not calendar.is_session(today):
        return None
    opens = _python_utc(calendar.session_open(today)) + timedelta(minutes=5)
    closes = _python_utc(calendar.session_close(today))
    if not opens <= current < closes:
        return None
    break_start = calendar.session_break_start(today)
    break_end = calendar.session_break_end(today)
    if pd.notna(break_start) and _python_utc(break_start) <= current < _python_utc(break_end):
        return None
    return _python_utc(calendar.previous_session(today))


def is_equity_market_open(market: str, now: Optional[datetime] = None) -> bool:
    """Return whether an equity market is inside its regular trading session."""
    normalized_market = str(market or "").strip()
    if normalized_market not in CALENDAR_BY_MARKET:
        return True
    current = _utc(now)
    calendar = _calendar(normalized_market)
    session = pd.Timestamp(pd.Timestamp(current).tz_convert(calendar.tz).date())
    if not calendar.is_session(session):
        return False
    session_open = _python_utc(calendar.session_open(session))
    session_close = _python_utc(calendar.session_close(session))
    if not session_open <= current < session_close:
        return False
    break_start = calendar.session_break_start(session)
    break_end = calendar.session_break_end(session)
    if pd.notna(break_start) and _python_utc(break_start) <= current < _python_utc(break_end):
        return False
    return True


def is_equity_bar_close(market: str, closed_at: datetime) -> bool:
    """Return whether a UTC boundary closes a bar inside an equity session."""
    normalized_market = str(market or "").strip()
    if normalized_market not in CALENDAR_BY_MARKET:
        return True
    boundary = _utc(closed_at)
    calendar = _calendar(normalized_market)
    session_date = pd.Timestamp(boundary).tz_convert(calendar.tz).date()
    session = pd.Timestamp(session_date)
    if not calendar.is_session(session):
        return False
    session_open = _python_utc(calendar.session_open(session))
    session_close = _python_utc(calendar.session_close(session))
    if not session_open < boundary <= session_close:
        return False
    break_start = calendar.session_break_start(session)
    break_end = calendar.session_break_end(session)
    if pd.notna(break_start) and _python_utc(break_start) < boundary <= _python_utc(break_end):
        return False
    return True


def latest_completed_session(
    market: str,
    now: Optional[datetime] = None,
    *,
    data_delay_minutes: int = 15,
) -> datetime:
    current = _utc(now)
    if _is_continuous_market(market):
        cutoff = current - timedelta(minutes=max(0, int(data_delay_minutes)))
        return datetime(cutoff.year, cutoff.month, cutoff.day, tzinfo=timezone.utc) - timedelta(days=1)
    calendar = _calendar(market)
    sessions = calendar.sessions_in_range(
        pd.Timestamp((current - timedelta(days=14)).date()),
        pd.Timestamp(current.date()),
    )
    delay = timedelta(minutes=max(0, int(data_delay_minutes)))
    completed = [session for session in sessions if _python_utc(calendar.session_close(session)) + delay <= current]
    if not completed:
        raise MarketScheduleError("portfolio.noCompletedMarketSession")
    return _python_utc(completed[-1]).replace(hour=0, minute=0, second=0, microsecond=0)


def next_rebalance_run(
    market: str,
    frequency: str,
    now: Optional[datetime] = None,
    *,
    data_delay_minutes: int = 15,
) -> datetime:
    current = _utc(now)
    clean_frequency = str(frequency or "weekly").strip().lower()
    if clean_frequency not in {"daily", "weekly", "monthly"}:
        raise MarketScheduleError("portfolio.invalidRebalanceFrequency")
    if _is_continuous_market(market):
        candidate = (current + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        candidate += timedelta(minutes=max(0, int(data_delay_minutes)))
        if clean_frequency == "weekly":
            while candidate.weekday() != 0:
                candidate += timedelta(days=1)
        elif clean_frequency == "monthly":
            while candidate.day != 1:
                candidate += timedelta(days=1)
        return candidate.replace(tzinfo=None)

    calendar = _calendar(market)
    sessions = calendar.sessions_in_range(
        pd.Timestamp(current.date()),
        pd.Timestamp((current + timedelta(days=360)).date()),
    )
    delay = timedelta(minutes=max(0, int(data_delay_minutes)))
    for index, session in enumerate(sessions):
        run_at = _python_utc(calendar.session_close(session)) + delay
        if run_at <= current or not _is_period_end(sessions, index, clean_frequency):
            continue
        return run_at.replace(tzinfo=None)
    raise MarketScheduleError("portfolio.nextMarketSessionUnavailable")


def _is_period_end(sessions, index: int, frequency: str) -> bool:
    if frequency == "daily":
        return True
    current = sessions[index]
    if index + 1 >= len(sessions):
        return False
    following = sessions[index + 1]
    if frequency == "weekly":
        return current.isocalendar().week != following.isocalendar().week or current.year != following.year
    return current.month != following.month or current.year != following.year


def _calendar(market: str):
    try:
        import exchange_calendars as exchange_calendars
    except ImportError as exc:
        raise MarketScheduleError("portfolio.marketCalendarUnavailable") from exc
    code = CALENDAR_BY_MARKET.get(str(market or "").strip())
    if not code:
        raise MarketScheduleError("portfolio.marketCalendarUnsupported")
    return exchange_calendars.get_calendar(code)


def _is_continuous_market(market: str) -> bool:
    return str(market or "").strip() in {"Crypto", "Cryptocurrency"}


def _utc(value: Optional[datetime]) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _python_utc(value) -> datetime:
    parsed = pd.Timestamp(value)
    if parsed.tzinfo is None:
        parsed = parsed.tz_localize("UTC")
    return parsed.tz_convert("UTC").to_pydatetime()
