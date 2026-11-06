"""Shared multi-timeframe loading for live Strategy API V2 sessions."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Callable

import pandas as pd

from app.data_sources.errors import (
    MarketDataFailure,
    MarketDataUnavailableError,
    classify_market_data_failure,
)
from app.services.fundamental_data import get_fundamental_data_service
from app.services.strategy_v2.contract import StrategyV2ContractError
from app.services.strategy_v2.frequencies import frequency_seconds
from app.services.strategy_v2.models import StrategyManifest
from app.services.strategy_v2.readiness import warmup_shortfalls
from app.services.strategy_v2.service import StrategyV2BacktestService


_STOCK_INTRADAY_HISTORY_DAY_CAPS = {
    "1m": 7,
    "3m": 7,
    "5m": 59,
    "15m": 59,
    "30m": 59,
}


def live_history_days(
    frequency: str,
    warmup_bars: int,
    candidates: list[dict[str, object]] | tuple = (),
) -> int:
    """Return a frequency-aware live lookback with a startup buffer."""
    bars = max(10, max(1, int(warmup_bars or 0)) * 3)
    seconds = frequency_seconds(frequency) * bars
    days = max(1, int(math.ceil(seconds / 86_400)))
    normalized = str(frequency or "").strip().lower()
    stock_session = any(
        str(item.get("market") or "") in {"USStock", "HKStock", "CNStock", "AStock"}
        or str(item.get("underlying_market") or "") in {"USStock", "HKStock", "CNStock", "AStock"}
        or str(item.get("api_family") or "").strip().lower() == "stock"
        for item in candidates
    )
    if stock_session and normalized.endswith(("m", "h")):
        hours = float(normalized[:-1]) / (60 if normalized.endswith("m") else 1)
        session_days = math.ceil(max(1, warmup_bars) * 3 * hours / 4 * 7 / 5 * 1.5)
        days = max(days, 7, session_days)
        provider_cap = _STOCK_INTRADAY_HISTORY_DAY_CAPS.get(normalized)
        if provider_cap is not None:
            days = min(days, provider_cap)
    return days


def completed_bar_token(frequency: str, now: datetime | None = None, *, venue: str = "", market_type: str = "spot") -> int:
    """Return a stable token for the latest fully completed UTC candle."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    seconds = max(1, frequency_seconds(frequency))
    from app.data_sources.candle_calendar import candle_origin
    origin = candle_origin(frequency, venue, market_type)
    return int((current.timestamp() - origin) // seconds) - 1


def completed_bar_close(frequency: str, token: int, *, venue: str = "", market_type: str = "spot") -> datetime:
    from app.data_sources.candle_calendar import candle_origin
    return datetime.fromtimestamp(
        (token + 1) * frequency_seconds(frequency) + candle_origin(frequency, venue, market_type),
        timezone.utc,
    )


def seconds_until_next_completed_bar(
    frequency: str,
    now: datetime | None = None,
    *,
    grace_seconds: float = 0.25,
    venue: str = "",
    market_type: str = "spot",
) -> float:
    """Return the delay until the next UTC candle can be treated as closed."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    seconds = max(1, frequency_seconds(frequency))
    timestamp = current.timestamp()
    from app.data_sources.candle_calendar import candle_origin
    origin = candle_origin(frequency, venue, market_type)
    next_boundary = (math.floor((timestamp - origin) / seconds) + 1) * seconds + origin
    return max(0.0, next_boundary - timestamp + max(0.0, float(grace_seconds or 0.0)))


def daily_equity_execution_policy(frequency: str, candidates, *, execution_mode: str, schedules=()):
    from app.services.market_schedule import equity_data_market

    if execution_mode != "live" or frequency != "1d" or schedules or not candidates:
        return None
    markets = {equity_data_market(str(member.get("market") or ""), member) for member in candidates}
    if len(markets) != 1 or not markets.issubset({"USStock", "HKStock"}):
        return None
    return markets.pop(), any(member.get("market") != "Crypto" for member in candidates)


def equity_daily_frames_ready(frames, candidates, signal_session: datetime, market: str) -> bool:
    """Do not consume a signal while any constituent still has yesterday's data."""
    from app.services.market_schedule import equity_bar_session_date

    expected = signal_session.date()
    for member in candidates:
        frame = frames.get(str(member["key"]))
        if frame is None or frame.empty or equity_bar_session_date(frame.index[-1], market) != expected:
            return False
    return True


def load_live_frequency_frames(
    *,
    service: StrategyV2BacktestService,
    candidates: list[dict[str, object]],
    manifest: StrategyManifest,
    end_date: datetime,
    warn: Callable[[str], None] | None = None,
) -> dict[str, dict[str, pd.DataFrame]]:
    """Load a complete live frame bundle for all declared strategy timeframes."""
    start_dates = {
        frequency: end_date
        - timedelta(days=live_history_days(frequency, manifest.warmup_bars, candidates))
        for frequency in manifest.frequencies
    }
    bundles, skipped = service.fetch_frequency_frames(
        candidates,
        manifest.frequencies,
        start_dates,
        end_date,
    )
    driving_frequency = manifest.driving_frequency
    driving_frames = bundles.get(driving_frequency, {})
    if not driving_frames:
        structured = [
            item.get("market_data_error")
            for item in skipped
            if isinstance(item.get("market_data_error"), dict)
        ]
        priority = {
            "region_restricted": 0,
            "proxy_failure": 1,
            "symbol_not_found": 2,
            "unsupported_timeframe": 3,
            "rate_limited": 4,
            "exchange_unavailable": 5,
            "no_market_data": 6,
        }
        if structured:
            selected = min(
                structured,
                key=lambda value: priority.get(str(value.get("code") or ""), 99),
            )
            raise MarketDataUnavailableError(MarketDataFailure.from_mapping(selected))
        detail = "; ".join(str(item.get("reason") or "") for item in skipped[:3])
        raise MarketDataUnavailableError(
            classify_market_data_failure(
                detail or "No usable market data",
                symbol=str(candidates[0].get("symbol") or "") if candidates else "",
                timeframe=driving_frequency,
            )
        )

    if skipped and warn:
        details = ", ".join(
            f"{item.get('symbol') or '?'}@{item.get('frequency') or '?'}:"
            f"{item.get('reason') or 'unavailable'}"
            for item in skipped[:5]
        )
        suffix = f" ({details})" if details else ""
        warn(
            f"Skipped {len(skipped)} instrument/timeframe data source(s)"
            f" without usable market data{suffix}"
        )

    if manifest.fundamental_dependencies:
        driving_frames = get_fundamental_data_service().enrich_panel(
            driving_frames,
            candidates,
        )
        bundles[driving_frequency] = driving_frames
        service.validate_fundamental_dependencies(driving_frames, manifest)
    return bundles


def live_ready_universe_symbols(
    frequency_frames: dict[str, dict[str, pd.DataFrame]],
    manifest: StrategyManifest,
    end_date: datetime,
    candidates: list[dict[str, object]] | tuple = (),
) -> tuple[frozenset[str], list[dict[str, object]]]:
    """Return symbols safe to expose through a live dynamic universe."""
    loaded_symbols = (
        set.intersection(*(set(frames) for frames in frequency_frames.values()))
        if frequency_frames
        else set()
    )
    candidate_symbols = {
        str(item.get("key") or "")
        for item in candidates
        if str(item.get("key") or "")
    }

    if manifest.universe.kind == "static":
        missing = sorted(candidate_symbols - loaded_symbols)
        if missing:
            raise StrategyV2ContractError(
                "strategyV2.noMarketData:" + ",".join(missing[:6])
            )

    shortfalls = warmup_shortfalls(
        frequency_frames,
        manifest.warmup_bars,
        end_date,
        candidates,
    )
    if manifest.universe.kind == "static" and shortfalls:
        details = ";".join(
            f"{item['symbol']}@{item['frequency']}="
            f"{item['count']}/{item['required']}"
            for item in shortfalls[:6]
        )
        raise StrategyV2ContractError(
            "strategyV2.insufficientWarmupData:" + details
        )

    excluded = {str(item["symbol"]) for item in shortfalls}
    ready = frozenset(loaded_symbols - excluded)
    if not ready:
        if shortfalls:
            details = ";".join(
                f"{item['symbol']}@{item['frequency']}="
                f"{item['count']}/{item['required']}"
                for item in shortfalls[:6]
            )
            raise StrategyV2ContractError(
                "strategyV2.insufficientWarmupData:" + details
            )
        raise StrategyV2ContractError("strategyV2.noMarketData")
    return ready, shortfalls


__all__ = [
    "completed_bar_token",
    "live_ready_universe_symbols",
    "live_history_days",
    "load_live_frequency_frames",
    "seconds_until_next_completed_bar",
]
