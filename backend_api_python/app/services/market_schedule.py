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
