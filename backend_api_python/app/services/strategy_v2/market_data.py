"""Market-data loading for Strategy API V2 backtests."""

from __future__ import annotations

import math
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd

from app.data_sources import DataSourceFactory
from app.data_sources.errors import (
    MarketDataUnavailableError,
    classify_market_data_failure,
)
from app.services.backtest_cache import KlineCache
from app.services.market_schedule import equity_data_market, equity_bar_session_date
from app.utils.logger import get_logger

logger = get_logger(__name__)
_cache = KlineCache()


@dataclass
class _SharedFrameEntry:
    frame: pd.DataFrame
    coverage_start: pd.Timestamp
    coverage_end: pd.Timestamp
    size_bytes: int
    last_access: float


_shared_frames: dict[str, _SharedFrameEntry] = {}
_shared_frame_locks: dict[str, threading.RLock] = {}
_shared_frames_lock = threading.RLock()
_shared_frames_bytes = 0
_SHARED_FRAME_CACHE_MAX_SIZE = 256
_SHARED_FRAME_CACHE_MAX_BYTES = max(
    0,
    int(os.getenv("STRATEGY_SHARED_FRAME_CACHE_MAX_BYTES", str(256 * 1024 * 1024))),
)
_LIVE_CACHE_GRACE_BARS = 2
_INCOMPLETE_WARMUP_RETRIES = 1

TIMEFRAME_SECONDS = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
    "1w": 604800,
    "1mo": 2592000,
}

PROVIDER_TIMEFRAMES = {
    "1h": "1H",
    "4h": "4H",
    "1d": "1D",
    "1w": "1W",
    "1mo": "1M",
}


def _normalize_utc_datetime(value: datetime) -> datetime:
    """Return an aware UTC datetime, interpreting naive inputs as UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _last_completed_bar_open(
    timeframe_seconds: int,
    *,
    now: Optional[datetime] = None,
) -> pd.Timestamp:
    """Return the UTC open time of the most recently completed candle.

    Using a bar-aligned cutoff avoids treating the seconds inside the current
    minute as an uncovered cache tail and refetching the same 1m candle on
    every runtime heartbeat.
    """
    seconds = max(1, int(timeframe_seconds or 1))
    current = _normalize_utc_datetime(now or datetime.now(timezone.utc))
    completed_open = ((int(current.timestamp()) // seconds) - 1) * seconds
    return pd.Timestamp(
        datetime.fromtimestamp(completed_open, tz=timezone.utc)
    ).tz_localize(None)


def _market_bar_cutoff(market: str, timeframe: str, *, now=None) -> pd.Timestamp:
    if market in {"USStock", "HKStock"} and timeframe == "1d":
        from app.services.market_schedule import equity_daily_bar_cutoff

        return equity_daily_bar_cutoff(market, now)
    return _last_completed_bar_open(TIMEFRAME_SECONDS.get(timeframe, 86400), now=now)


def _covers_crypto_window(
    frame: pd.DataFrame,
    requested_start: pd.Timestamp,
    requested_end: pd.Timestamp,
    timeframe_seconds: int,
) -> bool:
    """Crypto trades continuously, so a historical frame must cover both endpoints."""
    if frame is None or frame.empty:
        return False
    tolerance = pd.Timedelta(seconds=max(1, timeframe_seconds) * 3)
    expected_rows = max(
        1,
        int((requested_end - requested_start).total_seconds() / max(1, timeframe_seconds)) + 1,
    )
    coverage_ratio = len(frame) / expected_rows
    return bool(
        frame.index.min() <= requested_start + tolerance
        and frame.index.max() >= requested_end - tolerance
        and coverage_ratio >= 0.98
        and frame.index.is_unique
        and (frame.index.to_series().diff().dropna() == pd.Timedelta(seconds=timeframe_seconds)).all()
    )


def _covers_equity_window(
    frame: pd.DataFrame,
    requested_start: pd.Timestamp,
    requested_end: pd.Timestamp,
    timeframe_seconds: int,
) -> bool:
    if frame is None or frame.empty:
        return False
    tolerance = max(
        pd.Timedelta(days=10),
        pd.Timedelta(seconds=max(1, timeframe_seconds) * 2),
    )
    return bool(
        frame.index.min() <= requested_start + tolerance
        and frame.index.max() >= requested_end - tolerance
    )


def _load_strategy_frame_uncached(
    market: str,
    symbol: str,
    timeframe: str,
    start_date: datetime,
    end_date: datetime,
    *,
    market_type: Optional[str] = None,
    exchange_id: Optional[str] = None,
    instrument_id: Optional[str] = None,
    api_family: Optional[str] = None,
) -> pd.DataFrame:
    product = _resolve_catalog_product(
        market,
        symbol,
        exchange_id=exchange_id,
        market_type=market_type,
        instrument_id=instrument_id,
    )
    resolved_api_family = str(
        api_family or (product or {}).get("api_family") or ""
    ).strip().lower()
    start_utc = _normalize_utc_datetime(start_date)
    end_utc = _normalize_utc_datetime(end_date)
    normalized_timeframe = str(timeframe or "1d").strip().lower()
    timeframe_seconds = TIMEFRAME_SECONDS.get(normalized_timeframe, 86400)
    provider_timeframe = PROVIDER_TIMEFRAMES.get(normalized_timeframe, normalized_timeframe)
    data_market = equity_data_market(market, product, resolved_api_family)
    closed_bar_cutoff = _market_bar_cutoff(data_market, normalized_timeframe)
    cutoff_utc = closed_bar_cutoff.to_pydatetime().replace(tzinfo=timezone.utc)
    provider_end_utc = min(end_utc, cutoff_utc)
    total_seconds = max(1.0, (provider_end_utc - start_utc).total_seconds())
    limit = int(math.ceil(total_seconds / timeframe_seconds * 1.15) + 200)
    after_time = int((start_utc - timedelta(seconds=timeframe_seconds)).timestamp())
    before_time = int((provider_end_utc + timedelta(seconds=timeframe_seconds)).timestamp())
    cache_key = ":".join((
        "session-calendar-v2",
        str(market),
        str(symbol),
        str(timeframe),
        str(market_type or ""),
        str(exchange_id or ""),
        str(instrument_id or ""),
        resolved_api_family,
        start_utc.isoformat(),
        end_utc.isoformat(),
    ))
    requested_start = pd.Timestamp(start_utc).tz_localize(None)
    requested_end = pd.Timestamp(end_utc).tz_localize(None)
    coverage_end = min(requested_end, closed_bar_cutoff)
    effective_market = str(market or "")
    effective_symbol = str(symbol or "")
    if effective_market.strip().lower() == "crypto" and resolved_api_family == "stock":
        underlying = str((product or {}).get("underlying_symbol") or "").strip().upper()
        underlying_market = str((product or {}).get("underlying_market") or "").strip()
        if not underlying or underlying_market not in {"USStock", "HKStock"}:
            raise MarketDataUnavailableError(
                classify_market_data_failure(
                    "Gate stock product has no supported underlying market-data identity",
                    exchange_id="gate",
                    market_type="spot",
                    symbol=symbol,
                    timeframe=timeframe,
                )
            )
        effective_market = underlying_market
        effective_symbol = underlying
    continuous_calendar = bool(
        effective_market.strip().lower() == "crypto"
        and _product_uses_continuous_crypto_calendar(
            product,
            resolved_api_family=resolved_api_family,
        )
    )
    equity_market = data_market in {"USStock", "HKStock", "AStock", "CNStock"}
    cached = _cache.get(cache_key)
    if cached is not None and not cached.empty:
        cache_is_usable = True
        if continuous_calendar:
            cache_is_usable = _covers_crypto_window(
                cached,
                requested_start,
                coverage_end,
                timeframe_seconds,
            )
        elif equity_market:
            cache_is_usable = _covers_equity_window(
                cached,
                requested_start,
                coverage_end,
                timeframe_seconds,
            )
        if cache_is_usable:
            return cached.copy()
        logger.warning(
            "Ignored incomplete cached history for %s %s: requested=%s~%s, actual=%s~%s",
            symbol,
            timeframe,
            requested_start,
            coverage_end,
            cached.index.min(),
            cached.index.max(),
        )
    try:
        if (
            str(market or "").strip().lower() == "crypto"
            and str(exchange_id or "").strip().lower() == "bitget"
            and str(market_type or "spot").strip().lower() == "spot"
            and resolved_api_family == "reality"
        ):
            from app.data_providers.bitget_reality_market import get_bitget_reality_klines

            rows = get_bitget_reality_klines(
                str(instrument_id or symbol),
                timeframe,
                limit,
                before_time=before_time,
                after_time=after_time,
            )
        else:
            rows = None
        kwargs = {
            "market": effective_market,
            "symbol": effective_symbol,
            "timeframe": provider_timeframe,
            "limit": limit,
            "before_time": before_time,
            "after_time": after_time,
            "exchange_id": exchange_id if effective_market == market else None,
            "market_type": market_type if effective_market == market else None,
            "closed_only": True,
        }
        if rows is not None:
            pass
        elif effective_market == market and str(exchange_id or "").strip():
            rows, failure = DataSourceFactory.get_kline_with_diagnostics(**kwargs)
            if not rows and failure is not None:
                raise MarketDataUnavailableError(failure)
        else:
            rows = DataSourceFactory.get_kline(**kwargs)
    except MarketDataUnavailableError:
        raise
    except Exception as exc:
        logger.warning(
            "Strategy market-data fetch failed for %s:%s %s via %s/%s: %s",
            market,
            symbol,
            timeframe,
            exchange_id or "default",
            market_type or "default",
            exc,
        )
        raise MarketDataUnavailableError(
            classify_market_data_failure(
                exc,
                exchange_id=exchange_id or "",
                market_type=market_type or "",
                symbol=symbol,
                timeframe=timeframe,
            )
        ) from exc
    if not rows:
        raise MarketDataUnavailableError(
            classify_market_data_failure(
                "marketData.noRows",
                exchange_id=exchange_id or "",
                market_type=market_type or "",
                symbol=symbol,
                timeframe=timeframe,
            )
        )
    frame = pd.DataFrame(rows)
    time_column = next((name for name in ("time", "timestamp", "datetime", "date") if name in frame.columns), "")
    if not time_column:
        raise MarketDataUnavailableError(
            classify_market_data_failure(
                "marketData.timestampMissing",
                exchange_id=exchange_id or "",
                market_type=market_type or "",
                symbol=symbol,
                timeframe=timeframe,
            )
        )
    raw_time = frame.pop(time_column)
    numeric = pd.to_numeric(raw_time, errors="coerce")
    if numeric.notna().any():
        unit = "ms" if float(numeric.dropna().abs().median()) > 10_000_000_000 else "s"
        converted = pd.to_datetime(numeric, unit=unit, errors="coerce", utc=True)
        frame.index = pd.DatetimeIndex(converted).tz_convert(None)
    else:
        converted = pd.to_datetime(raw_time, errors="coerce", utc=True)
        frame.index = pd.DatetimeIndex(converted).tz_convert(None)
    frame = frame[~frame.index.isna()].sort_index()
    frame.columns = [str(column).strip().lower() for column in frame.columns]
    if any(column not in frame.columns for column in ("open", "high", "low", "close")):
        raise MarketDataUnavailableError(
            classify_market_data_failure(
                "marketData.ohlcMissing",
                exchange_id=exchange_id or "",
                market_type=market_type or "",
                symbol=symbol,
                timeframe=timeframe,
            )
        )
    for column in ("open", "high", "low", "close", "volume"):
        if column not in frame.columns:
            frame[column] = 0.0
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame[(frame.index >= requested_start) & (frame.index <= requested_end)].dropna(
        subset=["open", "high", "low", "close"]
    )
    if requested_end >= closed_bar_cutoff:
        frame = frame[frame.index <= closed_bar_cutoff]
    if (
        continuous_calendar
        and not _covers_crypto_window(frame, requested_start, coverage_end, timeframe_seconds)
    ):
        if not frame.empty:
            logger.error(
                "Rejected incomplete crypto history for %s %s: requested=%s~%s, actual=%s~%s",
                symbol,
                timeframe,
                requested_start,
                coverage_end,
                frame.index.min(),
                frame.index.max(),
            )
        raise MarketDataUnavailableError(
            classify_market_data_failure(
                "Incomplete K-line coverage after strategy window normalization",
                exchange_id=exchange_id or "",
                market_type=market_type or "",
                symbol=symbol,
                timeframe=timeframe,
            )
        )
    if equity_market and not _covers_equity_window(
        frame,
        requested_start,
        coverage_end,
        timeframe_seconds,
    ):
        raise MarketDataUnavailableError(
            classify_market_data_failure(
                "Incomplete equity K-line coverage after strategy window normalization",
                exchange_id=exchange_id or "",
                market_type=market_type or "",
                symbol=symbol,
                timeframe=timeframe,
            )
        )
    if not frame.empty:
        _cache.put(cache_key, frame, timeframe)
    return frame.copy()


def _shared_frame_key(
    market: str,
    symbol: str,
    timeframe: str,
    market_type: Optional[str],
    exchange_id: Optional[str],
    instrument_id: Optional[str] = None,
    api_family: Optional[str] = None,
) -> str:
    """Return the process-wide identity for one canonical candle stream."""
    return ":".join((
        str(exchange_id or "default").strip().lower(),
        str(market or "").strip().lower(),
        str(market_type or "default").strip().lower(),
        str(symbol or "").strip().upper(),
        str(instrument_id or "").strip(),
        str(api_family or "").strip().lower(),
        str(timeframe or "1d").strip().lower(),
    ))


def _lock_for_shared_frame(key: str) -> threading.RLock:
    with _shared_frames_lock:
        lock = _shared_frame_locks.get(key)
        if lock is None:
            lock = threading.RLock()
            _shared_frame_locks[key] = lock
        return lock


def _merge_frames(current: pd.DataFrame, incoming: pd.DataFrame) -> pd.DataFrame:
    if current is None or current.empty:
        return incoming.copy()
    if incoming is None or incoming.empty:
        return current.copy()
    merged = pd.concat([current, incoming]).sort_index()
    return merged[~merged.index.duplicated(keep="last")]


def _frame_bytes(frame: pd.DataFrame) -> int:
    return int(frame.memory_usage(index=True, deep=True).sum())


def _remove_shared_frame(key: str) -> None:
    global _shared_frames_bytes
    entry = _shared_frames.pop(key, None)
    if entry is not None:
        _shared_frames_bytes = max(0, _shared_frames_bytes - entry.size_bytes)


def _evict_shared_frame_if_needed() -> None:
    with _shared_frames_lock:
        while _shared_frames and (
            len(_shared_frames) > _SHARED_FRAME_CACHE_MAX_SIZE
            or _shared_frames_bytes > _SHARED_FRAME_CACHE_MAX_BYTES
        ):
            oldest_key = min(
                _shared_frames,
                key=lambda item: _shared_frames[item].last_access,
            )
            _remove_shared_frame(oldest_key)


def _remember_shared_frame(
    key: str,
    frame: pd.DataFrame,
    coverage_start: pd.Timestamp,
    coverage_end: pd.Timestamp,
) -> None:
    global _shared_frames_bytes
    size_bytes = _frame_bytes(frame)
    with _shared_frames_lock:
        _remove_shared_frame(key)
        if _SHARED_FRAME_CACHE_MAX_BYTES <= 0 or size_bytes > _SHARED_FRAME_CACHE_MAX_BYTES:
            return
        _shared_frames[key] = _SharedFrameEntry(
            frame=frame,
            coverage_start=coverage_start,
            coverage_end=coverage_end,
            size_bytes=size_bytes,
            last_access=time.monotonic(),
        )
        _shared_frames_bytes += size_bytes
        _evict_shared_frame_if_needed()


def _is_live_request(
    requested_end: pd.Timestamp,
    closed_cutoff: pd.Timestamp,
    timeframe_seconds: int,
) -> bool:
    tolerance = pd.Timedelta(seconds=max(1, timeframe_seconds) * 2)
    return bool(requested_end >= closed_cutoff - tolerance)


def _cached_crypto_frame_is_usable(
    frame: pd.DataFrame,
    requested_start: pd.Timestamp,
    coverage_end: pd.Timestamp,
    timeframe_seconds: int,
    *,
    live_request: bool,
) -> bool:
    if not _covers_crypto_window(
        frame,
        requested_start,
        coverage_end,
        timeframe_seconds,
    ):
        return False
    if not live_request:
        return True
    max_lag = pd.Timedelta(
        seconds=max(1, timeframe_seconds) * _LIVE_CACHE_GRACE_BARS
    )
    return bool(frame.index.max() >= coverage_end - max_lag)


def _uses_continuous_crypto_calendar(
    market: str,
    symbol: str,
    *,
    exchange_id: Optional[str],
    market_type: Optional[str],
    instrument_id: Optional[str],
    api_family: Optional[str],
) -> bool:
    if str(market or "").strip().lower() != "crypto":
        return False
    product = _resolve_catalog_product(
        market,
        symbol,
        exchange_id=exchange_id,
        market_type=market_type,
        instrument_id=instrument_id,
    )
    resolved_api_family = str(
        api_family or (product or {}).get("api_family") or ""
    ).strip().lower()
    if not exchange_id:
        return True
    return _product_uses_continuous_crypto_calendar(
        product,
        resolved_api_family=resolved_api_family,
    )


def _product_uses_continuous_crypto_calendar(
    product: Optional[dict],
    *,
    resolved_api_family: str,
) -> bool:
    if resolved_api_family in {"reality", "stock"}:
        return False
    product_type = str((product or {}).get("product_type") or "crypto").strip().lower()
    return product_type != "direct_equity"


def _resolve_catalog_product(
    market: str,
    symbol: str,
    *,
    exchange_id: Optional[str],
    market_type: Optional[str],
    instrument_id: Optional[str],
) -> Optional[dict]:
    if (
        str(market or "").strip().lower() != "crypto"
        or not str(exchange_id or "").strip()
    ):
        return None
    try:
        from app.services.market.product_catalog import get_catalog_product

        return get_catalog_product(
            market="Crypto",
            symbol=symbol,
            exchange_id=str(exchange_id),
            market_type=str(market_type or "spot"),
            instrument_id=str(instrument_id or ""),
        )
    except Exception as exc:
        logger.debug("Product catalog lookup unavailable during market-data routing: %s", exc)
        return None


def clear_shared_strategy_frame_cache() -> None:
    """Clear process-local candle state. Intended for tests and controlled reloads."""
    global _shared_frames_bytes
    with _shared_frames_lock:
        _shared_frames.clear()
        _shared_frame_locks.clear()
        _shared_frames_bytes = 0


def load_strategy_frame(
    market: str,
    symbol: str,
    timeframe: str,
    start_date: datetime,
    end_date: datetime,
    *,
    market_type: Optional[str] = None,
    exchange_id: Optional[str] = None,
    instrument_id: Optional[str] = None,
    api_family: Optional[str] = None,
) -> pd.DataFrame:
    """Load candles through a process-wide, incremental singleflight cache.

    The first caller warms the complete requested window. Later callers sharing
    exchange/market/symbol/timeframe reuse that frame and only request uncovered
    edges. The per-stream lock makes concurrent warmups and increments collapse
    into one upstream request.
    """
    start_utc = _normalize_utc_datetime(start_date)
    end_utc = _normalize_utc_datetime(end_date)
    normalized_timeframe = str(timeframe or "1d").strip().lower()
    timeframe_seconds = TIMEFRAME_SECONDS.get(normalized_timeframe, 86400)
    requested_start = pd.Timestamp(start_utc).tz_localize(None)
    requested_end = pd.Timestamp(end_utc).tz_localize(None)
    product = _resolve_catalog_product(
        market, symbol, exchange_id=exchange_id, market_type=market_type, instrument_id=instrument_id,
    )
    data_market = equity_data_market(market, product, str(api_family or ""))
    closed_cutoff = _market_bar_cutoff(data_market, normalized_timeframe)
    coverage_end = min(requested_end, closed_cutoff)
    live_request = _is_live_request(
        requested_end,
        closed_cutoff,
        timeframe_seconds,
    )
    continuous_crypto_market = bool(
        str(market or "").strip().lower() == "crypto"
        and (
            not exchange_id
            or _product_uses_continuous_crypto_calendar(
                product,
                resolved_api_family=str(api_family or (product or {}).get("api_family") or "").strip().lower(),
            )
        )
    )
    equity_market = data_market in {"USStock", "HKStock", "AStock", "CNStock"}
    key = _shared_frame_key(
        market,
        symbol,
        timeframe,
        market_type,
        exchange_id,
        instrument_id,
        api_family,
    )

    with _lock_for_shared_frame(key):
        entry = _shared_frames.get(key)
        if (
            entry is not None
            and requested_start >= entry.coverage_start
            and coverage_end <= entry.coverage_end
        ):
            cached_result = entry.frame[
                (entry.frame.index >= requested_start)
                & (entry.frame.index <= coverage_end)
            ].copy()
            cached_result_is_usable = True
            if continuous_crypto_market:
                cached_result_is_usable = _cached_crypto_frame_is_usable(
                    cached_result,
                    requested_start,
                    coverage_end,
                    timeframe_seconds,
                    live_request=live_request,
                )
            elif equity_market:
                cached_result_is_usable = _covers_equity_window(
                    cached_result,
                    requested_start,
                    coverage_end,
                    timeframe_seconds,
                )
            if cached_result_is_usable:
                entry.last_access = time.monotonic()
                return cached_result
            # Do not let an earlier rejected frame bypass validation on a cache hit.
            entry = None
            with _shared_frames_lock:
                _remove_shared_frame(key)

        fetch_windows: list[tuple[pd.Timestamp, pd.Timestamp]] = []
        if entry is None:
            fetch_windows.append((requested_start, requested_end))
        else:
            overlap = pd.Timedelta(seconds=timeframe_seconds * 2)
            if requested_start < entry.coverage_start:
                fetch_windows.append((requested_start, entry.coverage_start + overlap))
            if coverage_end > entry.coverage_end:
                fetch_windows.append((
                    max(requested_start, entry.coverage_end - overlap),
                    requested_end,
                ))

        merged = entry.frame.copy() if entry is not None else pd.DataFrame()
        last_failure: Optional[MarketDataUnavailableError] = None
        for window_start, window_end in fetch_windows:
            incoming = pd.DataFrame()
            attempts = (
                1 + _INCOMPLETE_WARMUP_RETRIES
                if normalized_timeframe == "1m" and entry is None
                else 1
            )
            for attempt in range(attempts):
                try:
                    incoming = _load_strategy_frame_uncached(
                        market,
                        symbol,
                        timeframe,
                        window_start.to_pydatetime().replace(tzinfo=timezone.utc),
                        window_end.to_pydatetime().replace(tzinfo=timezone.utc),
                        market_type=market_type,
                        exchange_id=exchange_id,
                        instrument_id=instrument_id,
                        api_family=api_family,
                    )
                    last_failure = None
                    break
                except MarketDataUnavailableError as exc:
                    last_failure = exc
                    should_retry = bool(
                        exc.failure.code == "incomplete_market_data"
                        and attempt + 1 < attempts
                    )
                    if should_retry:
                        logger.warning(
                            "Retrying incomplete %s %s warmup (%s/%s)",
                            symbol,
                            timeframe,
                            attempt + 2,
                            attempts,
                        )
                        continue
                    break
            if incoming is not None and not incoming.empty:
                merged = _merge_frames(merged, incoming)

        if merged.empty:
            if last_failure is not None:
                raise last_failure
            return pd.DataFrame()

        # Keep the cache bounded around the active warmup window. If a future
        # caller requests older history, the missing prefix is fetched again.
        overlap = pd.Timedelta(seconds=timeframe_seconds * 2)
        merged = merged[merged.index >= requested_start - overlap]
        actual_start = merged.index.min()
        actual_end = merged.index.max()
        incomplete_equity_daily = (
            data_market in {"USStock", "HKStock"} and normalized_timeframe == "1d"
            and live_request
            and equity_bar_session_date(actual_end, data_market) < equity_bar_session_date(coverage_end, data_market)
        )
        complete_equity_window = bool(
            equity_market
            and _covers_equity_window(
                merged,
                requested_start,
                coverage_end,
                timeframe_seconds,
            )
        )
        cached_coverage_start = (
                requested_start
                if complete_equity_window
                else actual_start
                if continuous_crypto_market or equity_market
                else min(actual_start, requested_start)
            )
        cached_coverage_end = (
                coverage_end
                if complete_equity_window and not incomplete_equity_daily
                else actual_end
                if continuous_crypto_market or equity_market
                else max(actual_end, coverage_end)
            )
        _remember_shared_frame(
            key,
            merged,
            cached_coverage_start,
            cached_coverage_end,
        )
        result = merged[
            (merged.index >= requested_start)
            & (merged.index <= coverage_end)
        ].copy()
        if continuous_crypto_market and not _cached_crypto_frame_is_usable(
            result,
            requested_start,
            coverage_end,
            timeframe_seconds,
            live_request=live_request,
        ):
            logger.warning(
                "Refused stale/incomplete cached crypto frame for %s %s: "
                "requested=%s~%s, actual=%s~%s",
                symbol,
                timeframe,
                requested_start,
                coverage_end,
                result.index.min() if not result.empty else "empty",
                result.index.max() if not result.empty else "empty",
            )
            if last_failure is not None:
                raise last_failure
            return pd.DataFrame()
        if equity_market and not _covers_equity_window(
            result,
            requested_start,
            coverage_end,
            timeframe_seconds,
        ):
            logger.warning(
                "Refused incomplete cached equity frame for %s %s: "
                "requested=%s~%s, actual=%s~%s",
                symbol,
                timeframe,
                requested_start,
                coverage_end,
                result.index.min() if not result.empty else "empty",
                result.index.max() if not result.empty else "empty",
            )
            if last_failure is not None:
                raise last_failure
            return pd.DataFrame()
        if last_failure is not None:
            logger.warning(
                "Using recent cached %s %s candles after transient %s failure; "
                "latest=%s, required=%s",
                symbol,
                timeframe,
                last_failure.failure.code,
                result.index.max(),
                coverage_end,
            )
        return result


__all__ = [
    "TIMEFRAME_SECONDS",
    "clear_shared_strategy_frame_cache",
    "load_strategy_frame",
]
