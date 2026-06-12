"""
Alpaca-backed US stock data source — extends USStockDataSource.

For intraday timeframes (1m, 5m, 15m, 30m, 1H) Alpaca's free SIP feed provides
up to ~10yr of 1-min bars, far beyond yfinance's 7-day cap. Daily/weekly bars
delegate to the parent class unchanged.

Bars are cached as Parquet per (symbol, timeframe) under ALPACA_CACHE_DIR to
minimize API calls on repeated backtests. Cache is append-only: only missing
tail is re-fetched on each access.

Requires env vars: ALPACA_API_KEY, ALPACA_SECRET_KEY (paper or live keys both work;
data API always connects to data.alpaca.markets regardless of paper/live).
"""
import os
from datetime import datetime, timedelta, date, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.data_sources.us_stock import USStockDataSource
from app.utils.logger import get_logger

logger = get_logger(__name__)

_INTRADAY = {"1m", "5m", "15m", "30m", "1H"}

# Map QuantDinger timeframe → (TimeFrameUnit name, amount)
_TF_MAP = {
    "1m":  ("Minute", 1),
    "5m":  ("Minute", 5),
    "15m": ("Minute", 15),
    "30m": ("Minute", 30),
    "1H":  ("Hour",   1),
}

# Approximate trading bars per calendar day per timeframe (for range estimation)
_BARS_PER_DAY = {"1m": 390, "5m": 78, "15m": 26, "30m": 13, "1H": 7}

CACHE_DIR = Path(os.getenv("ALPACA_CACHE_DIR", "/app/data/alpaca_cache"))


class AlpacaUSStockDataSource(USStockDataSource):
    """US stock data source backed by Alpaca for intraday history."""

    name = "USStock/Alpaca"

    def __init__(self):
        super().__init__()
        from alpaca.data.historical import StockHistoricalDataClient
        self._client = StockHistoricalDataClient(
            api_key=os.environ["ALPACA_API_KEY"],
            secret_key=os.environ["ALPACA_SECRET_KEY"],
        )
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def get_kline(
        self,
        symbol: str,
        timeframe: str,
        limit: int,
        before_time: Optional[int] = None,
        after_time: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        tf = self._normalize_timeframe(timeframe)
        if tf not in _INTRADAY:
            # Daily/weekly: yfinance is fine
            return super().get_kline(symbol, timeframe, limit, before_time, after_time)

        try:
            bars = self._get_intraday(symbol, tf, limit, before_time, after_time)
            if bars:
                bars.sort(key=lambda x: x["time"])
                return self.filter_and_limit(
                    bars, limit, before_time, after_time, truncate=(after_time is None)
                )
            logger.warning(f"Alpaca returned no bars for {symbol} {tf} — falling back")
        except Exception as exc:
            logger.warning(f"Alpaca error for {symbol} {tf}: {exc} — falling back to yfinance")

        return super().get_kline(symbol, timeframe, limit, before_time, after_time)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_intraday(
        self,
        symbol: str,
        timeframe: str,
        limit: int,
        before_time: Optional[int],
        after_time: Optional[int],
    ) -> List[Dict[str, Any]]:
        import pandas as pd

        cache_file = self._cache_path(symbol, timeframe)

        # Determine the window we need
        end_dt = (
            datetime.fromtimestamp(before_time, tz=timezone.utc)
            if before_time
            else datetime.now(tz=timezone.utc)
        )
        days_needed = max(10, int(limit / _BARS_PER_DAY.get(timeframe, 390) * 7 / 5) + 14)
        start_dt = end_dt - timedelta(days=days_needed)
        if after_time:
            floor = datetime.fromtimestamp(after_time, tz=timezone.utc)
            if floor < start_dt:
                start_dt = floor

        # Load existing cache
        cached_df: Optional[object] = None
        if cache_file.exists():
            try:
                cached_df = pd.read_parquet(cache_file)
            except Exception as exc:
                logger.warning(f"Cache read failed ({cache_file}): {exc} — re-fetching")
                cached_df = None

        # Decide what date range to fetch from Alpaca
        fetch_start = start_dt
        if cached_df is not None and not cached_df.empty:
            latest_cached = cached_df.index.max()
            if hasattr(latest_cached, "tzinfo") and latest_cached.tzinfo is None:
                latest_cached = latest_cached.tz_localize("UTC")
            elif hasattr(latest_cached, "tzinfo") and latest_cached.tzinfo is not None:
                latest_cached = latest_cached.tz_convert("UTC")
            # Only re-fetch the uncached tail; always include today
            if latest_cached.date() >= start_dt.date():
                fetch_start = latest_cached + timedelta(minutes=1)

        # Fetch missing tail from Alpaca
        if fetch_start < end_dt:
            new_df = self._alpaca_fetch(symbol, timeframe, fetch_start, end_dt)
            if new_df is not None and not new_df.empty:
                if cached_df is not None and not cached_df.empty:
                    cached_df = pd.concat([cached_df, new_df])
                    cached_df = cached_df[~cached_df.index.duplicated(keep="last")]
                    cached_df.sort_index(inplace=True)
                else:
                    cached_df = new_df
                # Persist updated cache
                try:
                    cache_file.parent.mkdir(parents=True, exist_ok=True)
                    cached_df.to_parquet(cache_file)
                except Exception as exc:
                    logger.warning(f"Cache write failed ({cache_file}): {exc}")

        if cached_df is None or cached_df.empty:
            return []

        # Slice to the requested window
        start_ts = start_dt.timestamp()
        end_ts = end_dt.timestamp()
        bars: List[Dict[str, Any]] = []
        for ts, row in cached_df.iterrows():
            ts_unix = int(ts.timestamp()) if hasattr(ts, "timestamp") else int(ts)
            if ts_unix < start_ts or ts_unix > end_ts:
                continue
            bars.append(
                self.format_kline(
                    timestamp=ts_unix,
                    open_price=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row.get("volume", 0)),
                )
            )
        return bars

    def _alpaca_fetch(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ):
        """Fetch bars from Alpaca. Returns a tz-aware UTC-indexed DataFrame or None."""
        import pandas as pd
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

        unit_name, amount = _TF_MAP[timeframe]
        tf = TimeFrame(amount, TimeFrameUnit[unit_name])

        # Pre-market starts at 04:00 ET = 09:00 UTC; push start back to catch it
        fetch_start = start.replace(hour=4, minute=0, second=0, microsecond=0)
        if fetch_start > start:
            fetch_start = start

        try:
            request = StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=tf,
                start=fetch_start,
                end=end,
                feed="iex",
                adjustment="split",
            )
            result = self._client.get_stock_bars(request)
            df = result.df
        except Exception as exc:
            logger.warning(f"Alpaca API call failed {symbol} {timeframe}: {exc}")
            return None

        if df is None or df.empty:
            return None

        # Drop symbol level from MultiIndex (symbol, timestamp)
        if isinstance(df.index, pd.MultiIndex):
            try:
                df = df.xs(symbol, level="symbol")
            except KeyError:
                df = df.xs(symbol.upper(), level="symbol")

        df.index = pd.to_datetime(df.index, utc=True)
        df.sort_index(inplace=True)
        return df[["open", "high", "low", "close", "volume"]]

    def _cache_path(self, symbol: str, timeframe: str) -> Path:
        return CACHE_DIR / symbol.upper() / f"{timeframe}.parquet"
