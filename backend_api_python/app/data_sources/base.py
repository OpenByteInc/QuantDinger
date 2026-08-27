"""
Base market data source interfaces.

All market data adapters should normalize K-line rows to the shape defined here.
"""
from abc import ABC, abstractmethod
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta, timezone

from app.utils.logger import get_logger

logger = get_logger(__name__)


TIMEFRAME_SECONDS = {
    '1m': 60,
    '3m': 180,
    '5m': 300,
    '15m': 900,
    '30m': 1800,
    '1H': 3600,
    '4H': 14400,
    '1D': 86400,
    '1W': 604800
}


class BaseDataSource(ABC):
    """Base class for market data sources."""
    
    name: str = "base"
    
    @abstractmethod
    def get_kline(
        self,
        symbol: str,
        timeframe: str,
        limit: int,
        before_time: Optional[int] = None,
        after_time: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Fetch K-line data.
        
        Args:
            symbol: Trading pair or ticker.
            timeframe: Candle interval (1m, 5m, 15m, 30m, 1H, 4H, 1D, 1W).
            limit: Number of rows to fetch.
            before_time: Fetch rows before this Unix timestamp, in seconds.
            after_time: Optional left boundary. Keep only rows with time >= after_time.
            
        Returns:
            K-line rows in this normalized shape:
            [{"time": int, "open": float, "high": float, "low": float, "close": float, "volume": float}, ...]
        """
        pass

    def get_ticker(self, symbol: str) -> Dict[str, Any]:
        """
        Get latest ticker for a symbol (best-effort).

        This is an optional interface used by the strategy executor for fetching current price.
        Implementations may return a dict compatible with CCXT `fetch_ticker` shape (e.g. {'last': ...}).
        """
        raise NotImplementedError("get_ticker is not implemented for this data source")
    
    def format_kline(
        self,
        timestamp: int,
        open_price: float,
        high: float,
        low: float,
        close: float,
        volume: float,
        lot_size: float = 0.0,
        min_notional: float = 0.0
    ) -> Dict[str, Any]:
        """Normalize one K-line row while preserving provider price precision; volume keeps two decimals."""
        return {
            'time': timestamp,
            'open': float(open_price),
            'high': float(high),
            'low': float(low),
            'close': float(close),
            'volume': round(float(volume), 2),
            'lot_size': lot_size,
            'min_notional': min_notional,
        }
    
    def calculate_time_range(
        self,
        timeframe: str,
        limit: int,
        buffer_ratio: float = 1.2
    ) -> int:
        """
        Calculate the time range required to fetch the requested candle count.
        
        Args:
            timeframe: Candle interval.
            limit: Number of candles.
            buffer_ratio: Extra range multiplier.
            
        Returns:
            Time range in seconds.
        """
        seconds_per_candle = TIMEFRAME_SECONDS.get(timeframe, 86400)
        return int(seconds_per_candle * limit * buffer_ratio)
    
    def filter_and_limit(
        self,
        klines: List[Dict[str, Any]],
        limit: int,
        before_time: Optional[int] = None,
        after_time: Optional[int] = None,
        truncate: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Filter and limit K-line rows.
        
        Args:
            klines: K-line rows.
            limit: Maximum number of rows.
            before_time: Keep rows before this timestamp.
            after_time: Keep rows with time >= after_time when set.
            truncate: When False, do not trim the tail by limit. Backtests need the
                full [after_time, before_time) window and must not lose the left edge.
            
        Returns:
            Filtered K-line rows.
        """
        klines.sort(key=lambda x: x['time'])
        
        if before_time:
            klines = [k for k in klines if k['time'] < before_time]
        if after_time is not None:
            klines = [k for k in klines if k['time'] >= after_time]
        
        if truncate and len(klines) > limit:
            klines = klines[-limit:]
        
        return klines
    
    def log_result(
        self,
        symbol: str,
        klines: List[Dict[str, Any]],
        timeframe: str
    ):
        """Log fetch result quality.

        Delay checks:
        - K-line time is a UTC Unix timestamp. Compare with datetime.now(UTC) to
          avoid local timezone drift.
        - Daily and weekly bars often represent the previous market close. Weekends
          and holidays can create a 3-4 day gap, so daily bars allow about 5
          calendar days and weekly bars allow a wider threshold.
        """
        if klines:
            latest_ts = int(klines[-1]["time"])
            latest_utc = datetime.fromtimestamp(latest_ts, tz=timezone.utc)
            now_utc = datetime.now(timezone.utc)
            time_diff = (now_utc - latest_utc).total_seconds()

            tf_sec = TIMEFRAME_SECONDS.get(timeframe, 3600)
            if tf_sec < 86400:
                max_diff = tf_sec * 2
            elif tf_sec == 86400:
                max_diff = 5 * 86400
            else:
                max_diff = max(tf_sec * 2, 21 * 86400)

            if time_diff > max_diff:
                logger.warning(
                    f"Warning: {symbol} data is delayed ({time_diff:.0f}s, "
                    f"latest_bar_utc={latest_utc.isoformat()}, threshold={max_diff:.0f}s, tf={timeframe})"
                )
        else:
            logger.warning(f"{self.name}: no data for {symbol}")
