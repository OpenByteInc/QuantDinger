"""
数据源基类
定义统一的数据源接口
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
    """数据源基类"""
    
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
        获取K线数据
        
        Args:
            symbol: 交易对/股票代码
            timeframe: 时间周期 (1m, 5m, 15m, 30m, 1H, 4H, 1D, 1W)
            limit: 数据条数
            before_time: 获取此时间之前的数据（Unix时间戳，秒）
            after_time: 可选，仅保留 time >= after_time 的 K 线（Unix 秒），用于回测窗口左边界
            
        Returns:
            K线数据列表，格式:
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
        volume: float
    ) -> Dict[str, Any]:
        """格式化单条K线数据"""
        return {
            'time': timestamp,
            'open': round(float(open_price), 4),
            'high': round(float(high), 4),
            'low': round(float(low), 4),
            'close': round(float(close), 4),
            'volume': round(float(volume), 2)
        }
    
    def calculate_time_range(
        self,
        timeframe: str,
        limit: int,
        buffer_ratio: float = 1.2
    ) -> int:
        """
        计算获取指定数量K线所需的时间范围（秒）
        
        Args:
            timeframe: 时间周期
            limit: K线数量
            buffer_ratio: 缓冲系数
            
        Returns:
            时间范围（秒）
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
        过滤和限制K线数据
        
        Args:
            klines: K线数据列表
            limit: 最大数量
            before_time: 过滤此时间之后的数据
            after_time: 若设置，仅保留 time >= after_time
            truncate: 为 False 时不在末尾按 limit 截断（回测需整段 [after_time, before_time) 时避免误丢左端）
            
        Returns:
            处理后的K线数据
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
        """记录获取结果日志。

        延迟判断：
        - K 线 time 为 Unix 秒（UTC），与 datetime.now(UTC) 比较，避免本地时区误差。
        - 日线/周线：最后一根通常是「上一交易日收盘」，周末/节假日可达 3～4 天，
          原先用 2×86400s（48h）会在周一早盘误报；改为日线最多容忍约 5 个自然日，周线更宽。
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

