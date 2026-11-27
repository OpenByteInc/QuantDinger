"""
K线数据服务
"""
from typing import Dict, List, Any, Optional

from app.data_sources import DataSourceFactory
from app.utils.cache import CacheManager
from app.utils.logger import get_logger
from app.config import CacheConfig
from app.config.data_sources import CryptoPublicConfig
from app.data_providers.gate_public_market import get_gate_spot_klines
from app.data_providers.bitget_reality_market import get_bitget_reality_klines
from app.services.market.product_catalog import get_catalog_product

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
        instrument_id: Optional[str] = None,
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
        if normalized_market == "Futures":
            from app.services.market.dated_futures import dated_future_feed, is_dated_future
            if is_dated_future(symbol):
                try:
                    dated_future_feed(symbol, required=True, allow_default=True)
                except ValueError as exc:
                    raise ValueError('brokerAccounts.forthright.feedMissing') from exc
        ex_key = (exchange_id or "").strip().lower()
        effective_ex_key = ex_key or str(CryptoPublicConfig.DEFAULT_EXCHANGE or "").strip().lower()
        mt_key = (market_type or "").strip().lower()
        if not before_time:
            native_key = (instrument_id or "").strip()
            cache_key = f"kline:{normalized_market}:{ex_key}:{mt_key}:{native_key}:{symbol}:{timeframe}:{limit}"
            latest_key = (
                f"kline:latest:{normalized_market}:{ex_key}:{mt_key}:{native_key}:"
                f"{symbol}:{timeframe}"
            )
            cached = self.cache.get(cache_key)
            if cached:
                ttl = self.cache_ttl.get(timeframe, 300)
                self.cache.set(latest_key, cached[-120:], ttl)
                return cached
        
        klines = None
        catalog_product = None
        if normalized_market == "Crypto" and ex_key and mt_key:
            try:
                catalog_product = get_catalog_product(
                    market="Crypto",
                    symbol=symbol,
                    exchange_id=ex_key,
                    market_type=mt_key,
                    instrument_id=str(instrument_id or ""),
                )
            except Exception:
                catalog_product = None
        if catalog_product and str(catalog_product.get("product_type") or "").strip().lower() == "direct_equity":
            underlying = str(catalog_product.get("underlying_symbol") or "").strip().upper()
            underlying_market = str(catalog_product.get("underlying_market") or "").strip()
            if underlying and underlying_market in {"USStock", "HKStock"}:
                klines = DataSourceFactory.get_kline(
                    market=underlying_market,
                    symbol=underlying,
                    timeframe=timeframe,
                    limit=limit,
                    before_time=before_time,
                )
        if normalized_market == "Crypto" and effective_ex_key == "bitget" and mt_key in {"", "spot"}:
            try:
                product = catalog_product or get_catalog_product(
                    market="Crypto",
                    symbol=symbol,
                    exchange_id="bitget",
                    market_type="spot",
                    instrument_id=str(instrument_id or ""),
                )
            except Exception:
                product = None
            if product and str(product.get("api_family") or "").strip().lower() == "reality":
                klines = get_bitget_reality_klines(
                    str(product.get("instrument_id") or instrument_id or symbol),
                    timeframe,
                    limit,
                    before_time=before_time,
                )
        if (
            not klines
            and
            normalized_market == "Crypto"
            and effective_ex_key == "gate"
            and mt_key in {"", "spot"}
            and not before_time
        ):
            try:
                klines = get_gate_spot_klines(symbol, timeframe, limit)
            except Exception as exc:
                logger.warning(
                    "Native Gate spot candles unavailable for %s %s; falling back to standard provider: %s",
                    symbol,
                    timeframe,
                    exc,
                )

        if not klines:
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
            self.cache.set(latest_key, klines[-120:], ttl)
        
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
        instrument_id: Optional[str] = None,
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
        native_key = (instrument_id or "").strip()
        cache_key = f"realtime_price:{market}:{ex_key}:{mt_key}:{native_key}:{symbol}"
        
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
        
        catalog_product = None
        if DataSourceFactory.normalize_market(market or "") == "Crypto" and ex_key and mt_key:
            try:
                catalog_product = get_catalog_product(
                    market="Crypto",
                    symbol=symbol,
                    exchange_id=ex_key,
                    market_type=mt_key,
                    instrument_id=native_key,
                )
            except Exception:
                catalog_product = None
        api_family = str((catalog_product or {}).get("api_family") or "").strip().lower()

        try:
            ticker = None
            if api_family not in {"reality", "stock"}:
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
                    'source': ticker.get('source') or 'ticker',
                    'timestamp': ticker.get('timestamp'),
                }
                self.cache.set(cache_key, result, 30)
                return result
        except Exception as e:
            logger.debug(f"Ticker API failed for {market}:{symbol}, falling back to kline: {e}")
        
        try:
            klines = self.get_kline(
                market,
                symbol,
                '1m',
                2,
                exchange_id=exchange_id,
                market_type=market_type,
                instrument_id=instrument_id,
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
                    'source': 'kline_1m',
                    'timestamp': latest.get('time'),
                }
                self.cache.set(cache_key, result, 30)
                return result
        except Exception as e:
            logger.debug(f"1m kline failed for {market}:{symbol}, trying daily: {e}")
        
        try:
            klines = self.get_kline(
                market,
                symbol,
                '1D',
                2,
                exchange_id=exchange_id,
                market_type=market_type,
                instrument_id=instrument_id,
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
                    'source': 'kline_1d',
                    'timestamp': latest.get('time'),
                }
                self.cache.set(cache_key, result, 300)
                return result
        except Exception as e:
            logger.error(f"All price sources failed for {market}:{symbol}: {e}")
        
        return result
