"""
数据源工厂
根据市场类型返回对应的数据源
"""
from typing import Dict, List, Any, Optional

from app.data_sources.base import BaseDataSource
from app.data_sources.errors import UnsupportedMarketError
from app.utils.logger import get_logger

logger = get_logger(__name__)

_MARKET_ALIASES: Dict[str, str] = {
    "crypto": "Crypto",
    "cryptocurrency": "Crypto",
    "forex": "Forex",
    "fx": "Forex",
    "usstock": "USStock",
    "us_stocks": "USStock",
    "usstocks": "USStock",
    "us_stock": "USStock",
    "stock": "USStock",
    "stocks": "USStock",
    "equity": "USStock",
    "equities": "USStock",
    "alpaca": "USStock",
    "ibkr": "USStock",
    "cnstock": "CNStock",
    "hkstock": "HKStock",
    "futures": "Futures",
    "moex": "MOEX",
    "rustock": "MOEX",
    "rustocks": "MOEX",
    "russianstock": "MOEX",
    "russia": "MOEX",
}


class DataSourceFactory:
    """
    数据源工厂。
    K 线 / 报价 使用哪个接口完全由调用方传入的 market（与自选分类一致）决定，不做根据 symbol 字符串的推断。
    """
    
    _sources: Dict[str, BaseDataSource] = {}
    
    # Markets that pass through normalize_market unchanged.
    _CANONICAL_MARKETS = ("Crypto", "Forex", "Futures", "USStock", "CNStock", "HKStock", "MOEX")

    @classmethod
    def normalize_market(cls, market: str) -> str:
        """
        Normalize a market category string.

        IMPORTANT: empty / unknown input used to silently degrade to "Crypto",
        which made stock symbols like TSLA quietly route to CCXT/Coinbase. We
        keep that fallback for backward compatibility (some callers still rely
        on it) but emit a loud WARNING so the misroute is no longer invisible.
        Always pass a real market category from the caller.
        """
        if not market:
            logger.warning(
                "DataSourceFactory.normalize_market(): empty market category — "
                "falling back to 'Crypto'. Caller MUST supply an explicit market "
                "(USStock / Forex / Futures / Crypto / CNStock / HKStock / MOEX). "
                "This fallback is deprecated and will become a hard error.",
                stack_info=False,
            )
            return "Crypto"
        raw = str(market).strip()
        if raw in cls._CANONICAL_MARKETS:
            return raw
        key = raw.lower().replace(" ", "").replace("-", "_")
        if key in _MARKET_ALIASES:
            return _MARKET_ALIASES[key]
        logger.warning(
            "DataSourceFactory.normalize_market(): unknown market %r — "
            "passing through as-is; downstream get_source() will likely fail.",
            raw,
        )
        return raw

    @classmethod
    def get_source(cls, market: str) -> BaseDataSource:
        """
        获取指定市场的数据源
        
        Args:
            market: 市场类型 (Crypto, USStock, Forex, Futures)
            
        Returns:
            数据源实例
        """
        market = cls.normalize_market(market or "")
        if market not in cls._sources:
            cls._sources[market] = cls._create_source(market)
        return cls._sources[market]

    @classmethod
    def get_data_source(cls, name: str) -> BaseDataSource:
        """
        Backward compatible alias used by older code paths.

        Some modules historically called `get_data_source("binance")` to fetch a crypto data source.
        In the localized Python backend we primarily use `get_source("Crypto")`.
        """
        key = (name or "").strip().lower()
        if key in ("crypto", "binance", "okx", "bybit", "bitget", "gate", "mexc", "kraken", "coinbase", "alpaca_crypto"):
            return cls.get_source("Crypto")
        if key in ("futures",):
            return cls.get_source("Futures")
        if key in ("forex", "fx", "mt5"):
            return cls.get_source("Forex")
        if key in ("usstock", "us_stocks", "stock", "stocks", "ibkr", "alpaca"):
            return cls.get_source("USStock")
        # Unknown alias — log and default to Crypto (legacy behavior). Callers
        # should migrate to the explicit `get_source(market)` API.
        logger.warning(
            "DataSourceFactory.get_data_source(%r): unknown alias — falling back "
            "to Crypto. Migrate caller to get_source(market) with an explicit "
            "market category.",
            name,
        )
        return cls.get_source("Crypto")
    
    @classmethod
    def _create_source(cls, market: str) -> BaseDataSource:
        """创建数据源实例"""
        if market == 'Crypto':
            from app.data_sources.crypto import CryptoDataSource
            return CryptoDataSource()
        elif market == 'CNStock':
            from app.data_sources.cn_stock import CNStockDataSource
            return CNStockDataSource()
        elif market == 'HKStock':
            from app.data_sources.hk_stock import HKStockDataSource
            return HKStockDataSource()
        elif market == 'USStock':
            from app.data_sources.us_stock import USStockDataSource
            return USStockDataSource()
        elif market == 'Forex':
            from app.data_sources.forex import ForexDataSource
            return ForexDataSource()
        elif market == 'Futures':
            from app.data_sources.futures import FuturesDataSource
            return FuturesDataSource()
        elif market == 'MOEX':
            from app.data_sources.moex import MOEXDataSource
            return MOEXDataSource()
        else:
            raise UnsupportedMarketError(market)
    
    @classmethod
    def get_kline(
        cls,
        market: str,
        symbol: str,
        timeframe: str,
        limit: int,
        before_time: Optional[int] = None,
        after_time: Optional[int] = None,
        exchange_id: Optional[str] = None,
        market_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        获取K线数据的便捷方法
        
        Args:
            market: 市场类型
            symbol: 交易对/股票代码
            timeframe: 时间周期
            limit: 数据条数
            before_time: 获取此时间之前的数据
            after_time: 可选，Unix 秒，K 线 time 需 >= 此值（回测左边界）
            exchange_id: 加密货币运行中策略 — 与策略绑定的交易所 (binance/okx/...)
            market_type: 加密货币运行中策略 — spot 或 swap
            
        Returns:
            K线数据列表
        """
        try:
            m = cls.normalize_market(market or "")
            source = cls._resolve_source(m, exchange_id=exchange_id, market_type=market_type)
            klines = source.get_kline(symbol, timeframe, limit, before_time, after_time)
            
            klines.sort(key=lambda x: x['time'])
            
            return klines
        except Exception as e:
            logger.error(f"Failed to fetch K-lines {market}:{symbol} (normalized={cls.normalize_market(market or '')}) - {str(e)}")
            return []
    
    @classmethod
    def _resolve_source(
        cls,
        market: str,
        *,
        exchange_id: Optional[str] = None,
        market_type: Optional[str] = None,
    ) -> BaseDataSource:
        """Pick data source; crypto live strategies may scope to execution exchange."""
        ex = (exchange_id or "").strip().lower()
        mt = (market_type or "").strip().lower()
        if mt in ("futures", "future", "perp", "perpetual"):
            mt = "swap"
        if market == "Crypto" and (ex or mt == "swap"):
            from app.data_sources.crypto import CryptoDataSource

            return CryptoDataSource.for_exchange(ex, mt or "swap")
        return cls.get_source(market)

    @classmethod
    def get_ticker(cls, market: str, symbol: str, exchange_id: Optional[str] = None, market_type: Optional[str] = None) -> Dict[str, Any]:
        """
        获取实时报价的便捷方法
        
        Args:
            market: 市场类型
            symbol: 交易对/股票代码
            exchange_id: 加密货币运行中策略 — 与策略绑定的交易所
            market_type: 加密货币运行中策略 — spot 或 swap
            
        Returns:
            实时报价数据: {
                'last': 最新价,
                'change': 涨跌额,
                'changePercent': 涨跌幅,
                ...
            }
        """
        try:
            m = cls.normalize_market(market or "")
            source = cls._resolve_source(m, exchange_id=exchange_id, market_type=market_type)
            return source.get_ticker(symbol)
        except NotImplementedError:
            logger.warning(f"get_ticker not implemented for market: {market}")
            return {'last': 0, 'symbol': symbol}
        except Exception as e:
            logger.error(f"Failed to fetch ticker {market}:{symbol} - {str(e)}")
            return {'last': 0, 'symbol': symbol}

