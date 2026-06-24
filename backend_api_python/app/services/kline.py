"""
K线数据服务
"""
from typing import Dict, List, Any, Optional

from app.data_sources import DataSourceFactory
from app.utils.cache import CacheManager
from app.utils.logger import get_logger
from app.config import CacheConfig

logger = get_logger(__name__)


class KlineService:
    """K线数据服务"""
    
    def __init__(self):
        self.cache = CacheManager()
        self.cache_ttl = CacheConfig.KLINE_CACHE_TTL
    
    def get_kline(
        self,
        market: str,
        symbol: str,
        timeframe: str,
        limit: int = 300,
        before_time: Optional[int] = None,
        exchange_id: Optional[str] = None,
        market_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        获取K线数据
        
        Args:
            market: 市场类型 (Crypto, USStock, Forex, Futures)
            symbol: 交易对/股票代码
            timeframe: 时间周期
            limit: 数据条数
            before_time: 获取此时间之前的数据
            exchange_id: 加密货币运行中策略 — 策略绑定的交易所
            market_type: 加密货币运行中策略 — spot 或 swap
            
        Returns:
            K线数据列表
        """
        normalized_market = DataSourceFactory.normalize_market(market or "")
        ex_key = (exchange_id or "").strip().lower()
        mt_key = (market_type or "").strip().lower()
        if not before_time:
            cache_key = f"kline:{normalized_market}:{ex_key}:{mt_key}:{symbol}:{timeframe}:{limit}"
            cached = self.cache.get(cache_key)
            if cached:
                return cached
        
        klines = DataSourceFactory.get_kline(
            market=normalized_market,
            symbol=symbol,
            timeframe=timeframe,
            limit=limit,
            before_time=before_time,
            exchange_id=exchange_id,
            market_type=market_type,
        )
        
        if klines and not before_time:
            ttl = self.cache_ttl.get(timeframe, 300)
            self.cache.set(cache_key, klines, ttl)
        
        return klines
    
    def get_latest_price(self, market: str, symbol: str) -> Optional[Dict[str, Any]]:
        """获取最新价格（使用1分钟K线，已弃用，建议使用 get_realtime_price）"""
        klines = self.get_kline(market, symbol, '1m', 1)
        if klines:
            return klines[-1]
        return None
    
    def get_realtime_price(
        self,
        market: str,
        symbol: str,
        force_refresh: bool = False,
        exchange_id: Optional[str] = None,
        market_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        获取实时价格（优先使用 ticker API，降级使用分钟 K 线）
        
        Args:
            market: 市场类型 (Crypto, USStock, Forex, Futures)
            symbol: 交易对/股票代码
            force_refresh: 是否强制刷新（跳过缓存）
            
        Returns:
            实时价格数据: {
                'price': 最新价格,
                'change': 涨跌额,
                'changePercent': 涨跌幅,
                'high': 最高价,
                'low': 最低价,
                'open': 开盘价,
                'previousClose': 昨收价,
                'source': 数据来源 ('ticker' 或 'kline')
            }
        """
        ex_key = (exchange_id or "").strip().lower()
        mt_key = (market_type or "").strip().lower()
        cache_key = f"realtime_price:{market}:{ex_key}:{mt_key}:{symbol}"
        
        if not force_refresh:
            cached = self.cache.get(cache_key)
            if cached:
                return cached
        
        result = {
            'price': 0,
            'change': 0,
            'changePercent': 0,
            'high': 0,
            'low': 0,
            'open': 0,
            'previousClose': 0,
            'source': 'unknown'
        }
        
        try:
            ticker = DataSourceFactory.get_ticker(
                market, symbol, exchange_id=exchange_id, market_type=market_type
            )
            if ticker and ticker.get('last', 0) > 0:
                result = {
                    'price': ticker.get('last', 0),
                    'change': ticker.get('change', 0),
                    'changePercent': ticker.get('changePercent') or ticker.get('percentage', 0),
                    'high': ticker.get('high', 0),
                    'low': ticker.get('low', 0),
                    'open': ticker.get('open', 0),
                    'previousClose': ticker.get('previousClose', 0),
                    'source': 'ticker'
                }
                self.cache.set(cache_key, result, 30)
                return result
        except Exception as e:
            logger.debug(f"Ticker API failed for {market}:{symbol}, falling back to kline: {e}")
        
        try:
            klines = self.get_kline(
                market, symbol, '1m', 2, exchange_id=exchange_id, market_type=market_type
            )
            if klines and len(klines) > 0:
                latest = klines[-1]
                prev_close = klines[-2]['close'] if len(klines) > 1 else latest.get('open', 0)
                current_price = latest.get('close', 0)
                
                change = round(current_price - prev_close, 4) if prev_close else 0
                change_pct = round(change / prev_close * 100, 2) if prev_close and prev_close > 0 else 0
                
                result = {
                    'price': current_price,
                    'change': change,
                    'changePercent': change_pct,
                    'high': latest.get('high', 0),
                    'low': latest.get('low', 0),
                    'open': latest.get('open', 0),
                    'previousClose': prev_close,
                    'source': 'kline_1m'
                }
                self.cache.set(cache_key, result, 30)
                return result
        except Exception as e:
            logger.debug(f"1m kline failed for {market}:{symbol}, trying daily: {e}")
        
        try:
            klines = self.get_kline(market, symbol, '1D', 2)
            if klines and len(klines) > 0:
                latest = klines[-1]
                prev_close = klines[-2]['close'] if len(klines) > 1 else latest.get('open', 0)
                current_price = latest.get('close', 0)
                
                change = round(current_price - prev_close, 4) if prev_close else 0
                change_pct = round(change / prev_close * 100, 2) if prev_close and prev_close > 0 else 0
                
                result = {
                    'price': current_price,
                    'change': change,
                    'changePercent': change_pct,
                    'high': latest.get('high', 0),
                    'low': latest.get('low', 0),
                    'open': latest.get('open', 0),
                    'previousClose': prev_close,
                    'source': 'kline_1d'
                }
                self.cache.set(cache_key, result, 300)
                return result
        except Exception as e:
            logger.error(f"All price sources failed for {market}:{symbol}: {e}")
        
        return result

