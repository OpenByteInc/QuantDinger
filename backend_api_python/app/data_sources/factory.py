"""
数据源工厂
根据市场类型返回对应的数据源
"""
import os
import threading
import time
from typing import Dict, List, Any, Optional

from app.data_sources.base import BaseDataSource
from app.data_sources.errors import UnsupportedMarketError
from app.utils.logger import get_logger
from app.utils.resource_guard import (
    ResourceExhaustedError,
    assert_fd_available,
    is_fd_exhaustion,
    mark_fd_exhausted,
)

logger = get_logger(__name__)


def _env_positive_int(key: str, default: int) -> int:
    try:
        value = int(os.getenv(key, str(default)))
        return value if value > 0 else default
    except Exception:
        return default

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
    "cn_stock": "CNStock",
    "ashare": "CNStock",
    "a_share": "CNStock",
    "astock": "CNStock",
    "a_stock": "CNStock",
    "cn": "CNStock",
    "china": "CNStock",
    "chinastock": "CNStock",
    "hkstock": "HKStock",
    "hk_stock": "HKStock",
    "hshare": "HKStock",
    "h_share": "HKStock",
    "hkshare": "HKStock",
    "hk_share": "HKStock",
    "hk": "HKStock",
    "hongkong": "HKStock",
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
    _noise_lock = threading.Lock()
    _noise_seen: Dict[str, tuple[float, int]] = {}
    _noise_interval_sec = _env_positive_int("LOG_DEDUPE_INTERVAL_SEC", 60)
    
    # Markets that pass through normalize_market unchanged.
    _CANONICAL_MARKETS = ("Crypto", "Forex", "Futures", "USStock", "CNStock", "HKStock", "MOEX")

    @classmethod
    def _log_limited(cls, level: str, key: str, message: str, *args: Any) -> None:
        """Log noisy market-data failures at most once per key per interval."""
        now = time.monotonic()
        with cls._noise_lock:
            last, suppressed = cls._noise_seen.get(key, (0.0, 0))
            if last > 0 and now - last < cls._noise_interval_sec:
                cls._noise_seen[key] = (last, suppressed + 1)
                return
            cls._noise_seen[key] = (now, 0)

        if suppressed:
            message = f"{message} (suppressed {suppressed} duplicate log(s))"
        log_fn = getattr(logger, level, logger.warning)
        log_fn(message, *args)

    @staticmethod
    def _close_request_source(source: Optional[BaseDataSource]) -> None:
        if source is None or not getattr(source, "close_after_request", False):
            return
        try:
            source.close()
        except Exception as exc:
            logger.debug("Request-scoped data source close failed: %s", exc)

    @classmethod
    def normalize_market(cls, market: str) -> str:
        """
        Normalize a market category string.

        IMPORTANT: empty / unknown input used to silently degrade to "Crypto",
        which made stock symbols like TSLA quietly route to a crypto provider. We
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
        if key == "futu":
            raise UnsupportedMarketError(
                "futu is a broker, not a market; specify HKStock or USStock"
            )
        if key in _MARKET_ALIASES:
            return _MARKET_ALIASES[key]
        cls._log_limited(
            "warning",
            f"unknown-market:{raw}",
            "DataSourceFactory.normalize_market(): unknown market %r; "
            "passing through as-is; downstream get_source() will likely fail.",
            raw,
        )
        return raw
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
        if key in ("crypto", "binance", "okx", "bybit", "bitget", "gate", "htx", "huobi"):
            return cls.get_source("Crypto")
        if key in ("futures",):
            return cls.get_source("Futures")
        if key in ("forex", "fx"):
            return cls.get_source("Forex")
        if key in ("usstock", "us_stocks", "stock", "stocks", "ibkr", "alpaca"):
            return cls.get_source("USStock")
        if key == "futu":
            raise UnsupportedMarketError(
                "futu is a broker, not a market; specify HKStock or USStock"
            )
        if key in ("hkstock", "hk_stock"):
            return cls.get_source("HKStock")
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
        exchange_config: Optional[Dict[str, Any]] = None,
        allow_futu_fallback: bool = True,
        strict_data_source: bool = False,
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
            exchange_id: 运行中策略绑定的交易所 (binance/okx/.../futu)
            market_type: spot 或 swap
            
        Returns:
            K线数据列表
        """
        m = cls.normalize_market(market or "")
        source = None
        try:
            assert_fd_available(f"market-data kline {m}:{symbol}")
            source = cls._resolve_source(
                m,
                exchange_id=exchange_id,
                market_type=market_type,
                exchange_config=exchange_config,
                allow_futu_fallback=allow_futu_fallback,
            )
            klines = source.get_kline(symbol, timeframe, limit, before_time, after_time)
            
            klines.sort(key=lambda x: x['time'])
            
            return klines
        except ResourceExhaustedError as e:
            if strict_data_source:
                raise
            cls._log_limited(
                "error",
                f"fd-cooldown:kline:{m}:{symbol}",
                "Skipped K-lines %s:%s because resource guard is active: %s",
                market,
                symbol,
                str(e),
            )
            return []
        except Exception as e:
            if is_fd_exhaustion(e):
                mark_fd_exhausted(e)
            if strict_data_source:
                raise
            cls._log_limited(
                "error",
                f"kline:{m}:{symbol}:{type(e).__name__}:{str(e)[:160]}",
                "Failed to fetch K-lines %s:%s (normalized=%s) - %s",
                market,
                symbol,
                m,
                str(e),
            )
            return []
        finally:
            cls._close_request_source(source)
    
    @classmethod
    def _resolve_source(
        cls,
        market: str,
        *,
        exchange_id: Optional[str] = None,
        market_type: Optional[str] = None,
        exchange_config: Optional[Dict[str, Any]] = None,
        allow_futu_fallback: bool = True,
    ) -> BaseDataSource:
        """Pick data source; crypto/Futu live strategies may scope to execution venue."""
        ex = (exchange_id or "").strip().lower()
        mt = (market_type or "").strip().lower()
        if mt in ("futures", "future", "perp", "perpetual"):
            mt = "swap"
        if market == "Crypto" and ex:
            from app.data_sources.crypto import CryptoDataSource

            return CryptoDataSource.for_exchange(ex, mt or "swap")
        if market == "Crypto" and mt == "swap":
            from app.data_sources.crypto import CryptoDataSource

            return CryptoDataSource.for_public_market("swap")
        # Prefer Futu OpenD when the execution account is Futu (HK/US).
        # On OpenD/permission failure, fall back to the public multi-source
        # adapters unless callers disable fallback.
        if ex == "futu" and market in ("HKStock", "USStock"):
            from app.data_sources.futu import FutuDataSource, FutuDataSourceError

            futu_source = FutuDataSource.for_exchange_config(exchange_config or {}, market=market)
            if not allow_futu_fallback:
                return futu_source

            class _FutuWithFallback(BaseDataSource):
                name = f"Futu+fallback/{market}"
                close_after_request = True

                def close(self) -> None:
                    futu_source.close()

                def get_ticker(self, symbol: str) -> Dict[str, Any]:
                    try:
                        return futu_source.get_ticker(symbol)
                    except FutuDataSourceError as exc:
                        logger.warning(
                            "Futu ticker unavailable (%s); falling back to %s public source",
                            exc,
                            market,
                        )
                        ticker = cls.get_source(market).get_ticker(symbol)
                        if isinstance(ticker, dict):
                            ticker = dict(ticker)
                            ticker["source"] = f"fallback:{market}"
                            ticker["futu_error"] = str(exc)
                        return ticker

                def get_kline(
                    self,
                    symbol: str,
                    timeframe: str,
                    limit: int,
                    before_time: Optional[int] = None,
                    after_time: Optional[int] = None,
                ) -> List[Dict[str, Any]]:
                    try:
                        return futu_source.get_kline(symbol, timeframe, limit, before_time, after_time)
                    except FutuDataSourceError as exc:
                        logger.warning(
                            "Futu kline unavailable (%s); falling back to %s public source for %s",
                            exc,
                            market,
                            symbol,
                        )
                        rows = cls.get_source(market).get_kline(
                            symbol, timeframe, limit, before_time, after_time
                        )
                        for row in rows:
                            if isinstance(row, dict):
                                row.setdefault("source", f"fallback:{market}")
                        return rows

            return _FutuWithFallback()
        return cls.get_source(market)

    @classmethod
    def get_ticker(
        cls,
        market: str,
        symbol: str,
        exchange_id: Optional[str] = None,
        market_type: Optional[str] = None,
        exchange_config: Optional[Dict[str, Any]] = None,
        allow_futu_fallback: bool = True,
        strict_data_source: bool = False,
    ) -> Dict[str, Any]:
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
        m = cls.normalize_market(market or "")
        source = None
        try:
            assert_fd_available(f"market-data ticker {m}:{symbol}")
            source = cls._resolve_source(
                m,
                exchange_id=exchange_id,
                market_type=market_type,
                exchange_config=exchange_config,
                allow_futu_fallback=allow_futu_fallback,
            )
            return source.get_ticker(symbol)
        except ResourceExhaustedError as e:
            if strict_data_source:
                raise
            cls._log_limited(
                "error",
                f"fd-cooldown:ticker:{m}:{symbol}",
                "Skipped ticker %s:%s because resource guard is active: %s",
                market,
                symbol,
                str(e),
            )
            return {'last': 0, 'symbol': symbol}
        except NotImplementedError:
            if strict_data_source:
                raise
            cls._log_limited(
                "warning",
                f"ticker-not-implemented:{m}",
                "get_ticker not implemented for market: %s",
                market,
            )
            return {'last': 0, 'symbol': symbol}
        except Exception as e:
            if is_fd_exhaustion(e):
                mark_fd_exhausted(e)
            if strict_data_source:
                raise
            cls._log_limited(
                "error",
                f"ticker:{m}:{symbol}:{type(e).__name__}:{str(e)[:160]}",
                "Failed to fetch ticker %s:%s - %s",
                market,
                symbol,
                str(e),
            )
            return {'last': 0, 'symbol': symbol}
        finally:
            cls._close_request_source(source)
