"""
加密货币数据源
使用 CCXT 获取数据
"""
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timedelta, timezone
import os
import threading
import time
import ccxt

from app.data_sources.base import BaseDataSource, TIMEFRAME_SECONDS
from app.utils.logger import get_logger
from app.config import CCXTConfig, APIKeys

logger = get_logger(__name__)

# Live-trading scoped instances: one CCXT client per (exchange, spot|swap).
_SCOPED_INSTANCES: Dict[str, "CryptoDataSource"] = {}
_PUBLIC_MARKET_INSTANCES: Dict[str, "CryptoDataSource"] = {}
_INVALID_SYMBOL_UNTIL: Dict[str, float] = {}
PUBLIC_KLINE_EXCHANGE_IDS = ("binance", "bitget", "bybit", "okx", "gate", "htx")
PUBLIC_KLINE_FALLBACK_IDS = ("bitget", "okx", "gate", "htx", "bybit", "binance")


class _PublicKlineUnavailable(RuntimeError):
    """Signal an empty provider result so an unscoped source can fail over."""


def apply_public_ccxt_endpoint_config(config: Dict[str, Any], exchange_id: str) -> Dict[str, Any]:
    """Apply current public REST endpoints without mutating the caller config."""
    resolved = dict(config or {})
    if (exchange_id or "").strip().lower() == "okx":
        resolved["hostname"] = (os.getenv("OKX_API_HOST") or "openapi.okx.com").strip()
    return resolved


def _invalid_symbol_ttl_sec() -> float:
    return 300.0


def _is_symbol_not_found_error(exc: Any) -> bool:
    text = str(exc or "").lower()
    return any(
        token in text
        for token in (
            "does not have market symbol",
            "symbol not found",
            "invalid symbol",
            "market does not exist",
            "trading pair not found",
        )
    )


def resolve_ccxt_for_live_trading(exchange_id: str, market_type: str) -> Tuple[str, Dict[str, Any]]:
    """Map QuantDinger exchange_id + market_type to a CCXT class id and options.

    Used for public OHLCV/ticker only (no API keys). Chart, backtest, signals,
    and live strategies can therefore resolve the same venue and product type.
    """
    e = (exchange_id or "").strip().lower()
    if not e:
        e = (CCXTConfig.DEFAULT_EXCHANGE or "binance").strip().lower()
    if e == "huobi":
        e = "htx"
    if e not in PUBLIC_KLINE_EXCHANGE_IDS:
        raise ValueError(f"Unsupported crypto exchange: {e}")
    mt = (market_type or "spot").strip().lower()
    if mt in ("futures", "future", "perp", "perpetual"):
        mt = "swap"

    opts: Dict[str, Any] = {}
    ccxt_id = e or "binance"

    if e == "binance":
        ccxt_id = "binanceusdm" if mt == "swap" else "binance"
    elif e == "okx":
        opts["defaultType"] = "swap" if mt == "swap" else "spot"
    elif e == "bybit":
        opts["defaultType"] = "linear" if mt == "swap" else "spot"
    elif e == "bitget":
        opts["defaultType"] = "swap" if mt == "swap" else "spot"
    elif e == "gate":
        opts["defaultType"] = "swap" if mt == "swap" else "spot"
    elif e == "htx" or e == "huobi":
        ccxt_id = "htx"
        opts["defaultType"] = "swap" if mt == "swap" else "spot"
    # unknown id: pass through and let ccxt raise if unsupported

    return ccxt_id, opts


def resolve_crypto_venue(
    *,
    exchange_config: Optional[Dict[str, Any]] = None,
    trading_config: Optional[Dict[str, Any]] = None,
    market_type: Optional[str] = None,
) -> Tuple[str, str]:
    """Resolve (exchange_id, spot|swap) for public crypto OHLCV/ticker."""
    cfg = exchange_config or {}
    tc = trading_config or {}
    ex = (
        cfg.get("exchange_id")
        or cfg.get("exchange")
        or cfg.get("exchangeId")
        or tc.get("exchange_id")
        or tc.get("exchange")
        or tc.get("exchangeId")
        or ""
    )
    ex = str(ex).strip().lower()
    if not ex:
        ex = (CCXTConfig.DEFAULT_EXCHANGE or "binance").strip().lower()
    if ex == "huobi":
        ex = "htx"
    if ex not in PUBLIC_KLINE_EXCHANGE_IDS:
        ex = "binance"

    mt = str(market_type or tc.get("market_type") or "spot").strip().lower()
    if mt in ("futures", "future", "perp", "perpetual"):
        mt = "swap"
    if mt not in ("spot", "swap"):
        mt = "spot"
    return ex, mt


class CryptoDataSource(BaseDataSource):
    """加密货币数据源"""
    
    name = "Crypto/CCXT"
    
    TIMEFRAME_MAP = CCXTConfig.TIMEFRAME_MAP

    _RESAMPLE_CANDIDATES: Dict[str, List[Tuple[str, int]]] = {
        '3m': [('1m', 3)],
        '4h': [('2h', 2), ('1h', 4)],
        '1w': [('1d', 7)],
    }

    _SINGLE_FETCH_HARD_CAP = 300

    _RECENT_CANDLE_LIMITS: Dict[str, int] = {
        "gate": 10000,
    }

    COMMON_QUOTES = ['USDT', 'USD', 'BTC', 'ETH', 'BUSD', 'USDC', 'BNB', 'EUR', 'GBP']
    
    def __init__(self):
        # Unscoped chart/backtest data may fail over between public providers.
        # A live-venue scoped source must always remain on its requested venue.
        self._allow_public_fallback = True
        self._scoped_exchange_id = ""
        self._scoped_market_type = "spot"
        self._preferred_public_exchange_id = ""
        self._markets_load_lock = threading.Lock()
        default_ex = (CCXTConfig.DEFAULT_EXCHANGE or "binance").strip().lower()
        if default_ex == "huobi":
            default_ex = "htx"
        if default_ex not in PUBLIC_KLINE_EXCHANGE_IDS:
            default_ex = "binance"
        self._init_ccxt_exchange(default_ex, {})

    @classmethod
    def for_exchange(cls, exchange_id: str, market_type: str = "swap") -> "CryptoDataSource":
        """Return a cached data source bound to a live-trading venue (crypto only)."""
        ccxt_id, options = resolve_ccxt_for_live_trading(exchange_id, market_type)
        mt = (market_type or "swap").strip().lower()
        if mt in ("futures", "future", "perp", "perpetual"):
            mt = "swap"
        cache_key = f"{ccxt_id}|{mt}|{sorted(options.items())}"
        cached = _SCOPED_INSTANCES.get(cache_key)
        if cached is not None:
            return cached
        inst = object.__new__(cls)
        inst._allow_public_fallback = False
        inst._scoped_exchange_id = (exchange_id or "").strip().lower()
        inst._scoped_market_type = mt
        inst._preferred_public_exchange_id = ""
        inst._markets_load_lock = threading.Lock()
        inst._init_ccxt_exchange(ccxt_id, options)
        _SCOPED_INSTANCES[cache_key] = inst
        logger.info(
            "CryptoDataSource scoped for live trading: exchange=%s market_type=%s ccxt=%s options=%s",
            inst._scoped_exchange_id,
            mt,
            ccxt_id,
            options,
        )
        return inst

    @classmethod
    def for_public_market(
        cls,
        market_type: str = "spot",
        preferred_exchange_id: str = "",
    ) -> "CryptoDataSource":
        """Return an uncredentialed source that may fail over across public venues.

        Backtests still need the requested product type (spot versus perpetual),
        but must not become unavailable merely because the default public venue is
        blocked or temporarily down.  Live-trading callers continue to use
        :meth:`for_exchange`, which intentionally never crosses venues.
        """
        mt = (market_type or "spot").strip().lower()
        if mt in ("futures", "future", "perp", "perpetual"):
            mt = "swap"
        if mt not in ("spot", "swap"):
            mt = "spot"
        exchange_id = (
            preferred_exchange_id
            or CCXTConfig.DEFAULT_EXCHANGE
            or "binance"
        ).strip().lower()
        if exchange_id == "huobi":
            exchange_id = "htx"
        if exchange_id not in PUBLIC_KLINE_EXCHANGE_IDS:
            exchange_id = "binance"
        cache_key = f"{exchange_id}|{mt}"
        cached = _PUBLIC_MARKET_INSTANCES.get(cache_key)
        if cached is not None:
            return cached
        ccxt_id, options = resolve_ccxt_for_live_trading(exchange_id, mt)
        inst = object.__new__(cls)
        inst._allow_public_fallback = True
        inst._scoped_exchange_id = exchange_id
        inst._scoped_market_type = mt
        inst._preferred_public_exchange_id = ""
        inst._markets_load_lock = threading.Lock()
        inst._init_ccxt_exchange(ccxt_id, options)
        _PUBLIC_MARKET_INSTANCES[cache_key] = inst
        return inst

    def _init_ccxt_exchange(self, ccxt_exchange_id: str, options: Optional[Dict[str, Any]] = None) -> None:
        config: Dict[str, Any] = {
            "timeout": CCXTConfig.TIMEOUT,
            "enableRateLimit": CCXTConfig.ENABLE_RATE_LIMIT,
        }
        if CCXTConfig.PROXY:
            config["proxies"] = {"http": CCXTConfig.PROXY, "https": CCXTConfig.PROXY}
        if options:
            config.setdefault("options", {}).update(dict(options))

        exchange_id = (ccxt_exchange_id or "").strip().lower()
        if not hasattr(ccxt, exchange_id):
            raise ValueError(f"Unsupported CCXT exchange: {exchange_id}")

        exchange_class = getattr(ccxt, exchange_id)
        config = apply_public_ccxt_endpoint_config(config, exchange_id)
        self.exchange = exchange_class(config)
        self._markets_loaded = False
        self._markets_cache = None

    def _symbol_for_scoped_market(self, symbol: str) -> str:
        """CCXT linear/swap symbols often need ``BASE/QUOTE:QUOTE`` (e.g. BTC/USDT:USDT)."""
        normalized = self._normalize_symbol_for_exchange(symbol)
        if not normalized:
            return symbol
        mt = getattr(self, "_scoped_market_type", "") or "spot"
        if mt != "swap":
            return normalized
        if ":" in normalized:
            return normalized
        if "/" in normalized:
            _base, quote = normalized.split("/", 1)
            if quote:
                return f"{normalized}:{quote}"
        return normalized

    def _invalid_symbol_key(self, symbol_pair: str) -> str:
        exchange_id = getattr(self.exchange, 'id', '').lower()
        mt = getattr(self, "_scoped_market_type", "") or "spot"
        return f"{exchange_id}:{mt}:{str(symbol_pair or '').upper()}"

    def _is_invalid_symbol_cached(self, symbol_pair: str) -> bool:
        key = self._invalid_symbol_key(symbol_pair)
        until = float(_INVALID_SYMBOL_UNTIL.get(key) or 0.0)
        if time.time() < until:
            return True
        _INVALID_SYMBOL_UNTIL.pop(key, None)
        return False

    def _mark_invalid_symbol(self, symbol_pair: str, error: Any) -> None:
        key = self._invalid_symbol_key(symbol_pair)
        if not self._is_invalid_symbol_cached(symbol_pair):
            logger.warning(
                "Symbol '%s' not found on %s; suppressing repeat requests for %.0fs. Error: %s",
                symbol_pair,
                getattr(self.exchange, 'id', ''),
                _invalid_symbol_ttl_sec(),
                str(error)[:160],
            )
        _INVALID_SYMBOL_UNTIL[key] = time.time() + _invalid_symbol_ttl_sec()
    
    def _ensure_markets_loaded(self) -> bool:
        """确保 markets 已加载（用于符号验证）"""
        if self._markets_loaded and self._markets_cache is not None:
            return True

        lock = getattr(self, "_markets_load_lock", None)
        if lock is None:
            lock = threading.Lock()
            self._markets_load_lock = lock
        with lock:
            if self._markets_loaded and self._markets_cache is not None:
                return True
            try:
                if hasattr(self.exchange, 'load_markets'):
                    self.exchange.load_markets(reload=False)
                self._markets_cache = getattr(self.exchange, 'markets', {})
                self._markets_loaded = True
                return True
            except Exception as e:
                logger.debug(f"Failed to load markets for {self.exchange.id}: {e}")
                return False
    
    def _normalize_symbol(self, symbol: str) -> Tuple[str, str]:
        """
        规范化符号格式，返回 (normalized_symbol, base_currency)
        
        处理各种输入格式：
        - BTC/USDT -> BTC/USDT
        - BTCUSDT -> BTC/USDT
        - BTC/USDT:USDT -> BTC/USDT
        - BTC -> BTC/USDT (默认)
        - PI, TRX -> PI/USDT, TRX/USDT
        """
        if not symbol:
            return '', ''
        
        sym = symbol.strip()
        
        if ':' in sym:
            sym = sym.split(':', 1)[0]
        
        sym = sym.upper()
        
        if '/' in sym:
            parts = sym.split('/', 1)
            base = parts[0].strip()
            quote = parts[1].strip() if len(parts) > 1 else ''
            if base and quote:
                return f"{base}/{quote}", base
        
        for quote in self.COMMON_QUOTES:
            if sym.endswith(quote) and len(sym) > len(quote):
                base = sym[:-len(quote)]
                if base:
                    return f"{base}/{quote}", base
        
        return f"{sym}/USDT", sym
    
    def _find_valid_symbol(self, base: str, preferred_quote: str = 'USDT') -> Optional[str]:
        """
        在交易所的 markets 中查找有效的符号
        
        Args:
            base: 基础货币（如 'PI', 'TRX'）
            preferred_quote: 首选的报价货币
            
        Returns:
            找到的有效符号，如果找不到则返回 None
        """
        if not self._ensure_markets_loaded():
            return None
        
        markets = self._markets_cache or {}
        if not markets:
            return None
        
        quotes_to_try = [preferred_quote] + [q for q in self.COMMON_QUOTES if q != preferred_quote]
        
        for quote in quotes_to_try:
            candidate = f"{base}/{quote}"
            if candidate in markets:
                market = markets[candidate]
                if market.get('active', True):
                    return candidate
        
        return None
    
    def _get_lot_size_and_min_notional(self, symbol: str) -> tuple[float, float]:
        """
        Get lot_size (stepSize) and min_notional for a symbol from exchange markets.
        
        Args:
            symbol: Normalized symbol (e.g., 'BTC/USDT')
        
        Returns:
            Tuple of (lot_size, min_notional) in base currency units.
            Returns (0.0, 0.0) if not available.
        """
        if not self._ensure_markets_loaded():
            return 0.0, 0.0
        
        markets = self._markets_cache or {}
        if not markets or symbol not in markets:
            return 0.0, 0.0
        
        market = markets[symbol]
        lot_size = 0.0
        min_notional = 0.0
        
        # Get lot_size from precision.amount or limits.amount.min
        precision = market.get('precision', {})
        if isinstance(precision, dict) and precision.get('amount') is not None:
            lot_size = float(precision['amount'])
        
        # Fallback to limits.amount.min
        if lot_size <= 0:
            limits = market.get('limits', {})
            amount_limits = limits.get('amount', {}) if isinstance(limits, dict) else {}
            if isinstance(amount_limits, dict) and amount_limits.get('min') is not None:
                lot_size = float(amount_limits['min'])
        
        # Get min_notional from limits.cost.min
        limits = market.get('limits', {})
        cost_limits = limits.get('cost', {}) if isinstance(limits, dict) else {}
        if isinstance(cost_limits, dict) and cost_limits.get('min') is not None:
            min_notional = float(cost_limits['min'])
        
        return lot_size, min_notional

    def _normalize_symbol_for_exchange(self, symbol: str) -> str:
        """
        根据交易所特性规范化符号
        
        不同交易所的符号格式要求：
        - Binance: BTC/USDT (标准格式)
        - OKX: BTC/USDT (标准格式，但某些币种可能不支持)
        Different providers may use different quote currencies or asset aliases.
        """
        normalized, base = self._normalize_symbol(symbol)
        
        if not normalized or not base:
            return symbol
        
        exchange_id = getattr(self.exchange, 'id', '').lower()
        
        if self._ensure_markets_loaded():
            valid_symbol = self._find_valid_symbol(base, normalized.split('/')[1] if '/' in normalized else 'USDT')
            if valid_symbol:
                return valid_symbol
        
        return normalized

    def get_ticker(self, symbol: str) -> Dict[str, Any]:
        """
        Get latest ticker for a crypto symbol via CCXT.

        Accepts common formats:
        - BTC/USDT, BTCUSDT, BTC/USDT:USDT
        - PI, TRX (will be normalized and searched across exchanges)
        - 自动适配不同交易所的符号格式要求
        """
        if not symbol or not symbol.strip():
            return {'last': 0, 'symbol': symbol}

        preferred_exchange = str(
            getattr(self, "_preferred_public_exchange_id", "") or ""
        ).strip().lower()
        current_exchange = str(
            getattr(self.exchange, "id", "") or ""
        ).strip().lower()
        fallback_market_type = str(
            getattr(self, "_scoped_market_type", "") or "spot"
        ).strip().lower()
        if (
            bool(getattr(self, "_allow_public_fallback", False))
            and preferred_exchange
            and preferred_exchange != current_exchange
        ):
            preferred_ticker = type(self).for_exchange(
                preferred_exchange,
                fallback_market_type,
            ).get_ticker(symbol)
            if float((preferred_ticker or {}).get("last") or 0) > 0:
                return preferred_ticker
            self._preferred_public_exchange_id = ""
        
        normalized = self._symbol_for_scoped_market(symbol)

        if not normalized:
            logger.warning(f"Failed to normalize symbol: {symbol}")
            return {'last': 0, 'symbol': symbol}

        if self._is_invalid_symbol_cached(normalized):
            return {'last': 0, 'symbol': symbol}
        
        try:
            ticker = self.exchange.fetch_ticker(normalized)
            if ticker and isinstance(ticker, dict):
                return ticker
        except Exception as e:
            error_msg = str(e).lower()
            is_symbol_error = _is_symbol_not_found_error(e)
            
            if is_symbol_error:
                base = normalized.split('/')[0] if '/' in normalized else normalized
                if self._ensure_markets_loaded():
                    valid_symbol = self._find_valid_symbol(base)
                    if valid_symbol and valid_symbol != normalized:
                        try:
                            logger.debug(f"Trying alternative symbol: {valid_symbol} (original: {symbol}, first attempt: {normalized})")
                            ticker = self.exchange.fetch_ticker(valid_symbol)
                            if ticker and isinstance(ticker, dict):
                                return ticker
                        except Exception as e2:
                            logger.debug(f"Alternative symbol {valid_symbol} also failed: {e2}")
            
            if is_symbol_error:
                self._mark_invalid_symbol(normalized, e)
            else:
                logger.warning(
                    f"Symbol '{symbol}' (normalized: {normalized}) not found on {self.exchange.id}. "
                    f"Error: {str(e)[:100]}"
                )
        
        if bool(getattr(self, "_allow_public_fallback", False)):
            for exchange_id in PUBLIC_KLINE_FALLBACK_IDS:
                if exchange_id == current_exchange:
                    continue
                fallback_ticker = type(self).for_exchange(
                    exchange_id,
                    fallback_market_type,
                ).get_ticker(symbol)
                if float((fallback_ticker or {}).get("last") or 0) > 0:
                    self._preferred_public_exchange_id = exchange_id
                    logger.warning(
                        "Public crypto ticker provider failed over from %s to %s for %s",
                        current_exchange or "default",
                        exchange_id,
                        symbol,
                    )
                    return fallback_ticker

        return {'last': 0, 'symbol': symbol}
    
    def get_kline(
        self,
        symbol: str,
        timeframe: str,
        limit: int,
        before_time: Optional[int] = None,
        after_time: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """获取加密货币K线数据"""
        klines = []
        symbol_pair = ""

        # A public source is cached and reused by chart/backtest/signal
        # runtimes. Once its configured venue has failed and another venue has
        # returned valid candles, reuse that known-good venue on subsequent
        # calls. Without this small circuit breaker every strategy cycle first
        # waits for the same geo-blocked/down provider and only then falls back.
        # Live venue-scoped sources never enter this path.
        preferred_exchange = str(
            getattr(self, "_preferred_public_exchange_id", "") or ""
        ).strip().lower()
        current_exchange = str(
            getattr(self.exchange, "id", "") or ""
        ).strip().lower()
        fallback_market_type = str(
            getattr(self, "_scoped_market_type", "") or "spot"
        ).strip().lower()
        if (
            bool(getattr(self, "_allow_public_fallback", False))
            and preferred_exchange
            and preferred_exchange != current_exchange
        ):
            preferred_rows = type(self).for_exchange(
                preferred_exchange,
                fallback_market_type,
            ).get_kline(symbol, timeframe, limit, before_time, after_time)
            if preferred_rows:
                return preferred_rows
            # The promoted provider has also become unavailable. Clear it and
            # run the normal ordered failover below.
            self._preferred_public_exchange_id = ""
        
        try:
            ccxt_timeframe = self.TIMEFRAME_MAP.get(timeframe, '1d')

            resample_bucket = 1
            fetch_ccxt_timeframe = ccxt_timeframe
            fetch_qd_timeframe = timeframe
            fetch_limit = limit

            exchange_timeframes = getattr(self.exchange, 'timeframes', None) or {}
            if exchange_timeframes and ccxt_timeframe not in exchange_timeframes:
                picked = self._pick_resample_source(ccxt_timeframe, exchange_timeframes)
                if picked is None:
                    logger.warning(
                        f"Exchange '{self.exchange.id}' cannot serve timeframe '{ccxt_timeframe}' "
                        f"and no finer supported granularity is available for resampling. "
                        f"Supported: {sorted(exchange_timeframes.keys())}"
                    )
                    raise _PublicKlineUnavailable
                source_ccxt_tf, bucket = picked
                fetch_ccxt_timeframe = source_ccxt_tf
                fetch_qd_timeframe = self._ccxt_to_qd_timeframe(source_ccxt_tf, timeframe)
                resample_bucket = bucket
                fetch_limit = min(limit * bucket, self._SINGLE_FETCH_HARD_CAP)
                logger.info(
                    f"Exchange '{self.exchange.id}' has no native '{ccxt_timeframe}' "
                    f"timeframe; fetching '{source_ccxt_tf}' x{bucket} candles "
                    f"({fetch_limit}) and resampling to '{ccxt_timeframe}'"
                )

            symbol_pair = self._symbol_for_scoped_market(symbol)

            if not symbol_pair:
                logger.warning(f"Failed to normalize symbol for K-line: {symbol}")
                raise _PublicKlineUnavailable

            if self._is_invalid_symbol_cached(symbol_pair):
                raise _PublicKlineUnavailable

            ohlcv = self._fetch_ohlcv(
                symbol_pair, fetch_ccxt_timeframe, fetch_limit,
                before_time, fetch_qd_timeframe, after_time,
            )

            if not ohlcv:
                logger.warning(f"CCXT returned no K-lines: {symbol_pair}")
                raise _PublicKlineUnavailable

            if resample_bucket > 1:
                ohlcv = self._resample_ohlcv(ohlcv, resample_bucket)
                if not ohlcv:
                    logger.warning(
                        f"Resampling produced no candles for {symbol_pair} "
                        f"(bucket={resample_bucket}, source len was less than one bucket)"
                    )
                    raise _PublicKlineUnavailable

            for candle in ohlcv:
                if len(candle) < 6:
                    continue
                # Get lot_size and min_notional from exchange market data
                lot_size = 0.0
                min_notional = 0.0
                if self._ensure_markets_loaded() and symbol_pair in (self._markets_cache or {}):
                    lot_size, min_notional = self._get_lot_size_and_min_notional(symbol_pair)
                
                klines.append(self.format_kline(
                    timestamp=int(candle[0] / 1000),  # 毫秒转秒
                    open_price=candle[1],
                    high=candle[2],
                    low=candle[3],
                    close=candle[4],
                    volume=candle[5],
                    lot_size=lot_size,
                    min_notional=min_notional
                ))
            
            klines = self.filter_and_limit(
                klines,
                limit,
                before_time,
                after_time,
                truncate=(after_time is None),
            )

            self.log_result(symbol, klines, timeframe)

            # Concise trace so backtest logs can correlate requested window with actual window
            if klines:
                try:
                    from datetime import datetime as _dt
                    first_ts = _dt.utcfromtimestamp(klines[0]['time']).isoformat()
                    last_ts = _dt.utcfromtimestamp(klines[-1]['time']).isoformat()
                    logger.info(
                        f"[CryptoKline] {symbol} {timeframe} returned {len(klines)} candles, "
                        f"utc_range={first_ts}~{last_ts}, limit={limit}, before_time={before_time}"
                    )
                except Exception:
                    pass

        except _PublicKlineUnavailable:
            pass
        except Exception as e:
            logger.error(f"Failed to fetch crypto K-lines {symbol}: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())

        if not klines and bool(getattr(self, "_allow_public_fallback", False)):
            current_exchange = str(getattr(self.exchange, "id", "") or "").strip().lower()
            fallback_market_type = str(
                getattr(self, "_scoped_market_type", "") or "spot"
            ).strip().lower()
            for exchange_id in PUBLIC_KLINE_FALLBACK_IDS:
                if exchange_id == current_exchange:
                    continue
                try:
                    fallback_source = type(self).for_exchange(
                        exchange_id,
                        fallback_market_type,
                    )
                    fallback_rows = fallback_source.get_kline(
                        symbol,
                        timeframe,
                        limit,
                        before_time,
                        after_time,
                    )
                    if fallback_rows:
                        self._preferred_public_exchange_id = exchange_id
                        logger.warning(
                            "Public crypto K-line provider failed over from %s to %s for %s %s",
                            current_exchange or "default",
                            exchange_id,
                            symbol,
                            timeframe,
                        )
                        return fallback_rows
                except Exception as exc:
                    logger.warning(
                        "Public crypto K-line fallback %s failed for %s %s: %s",
                        exchange_id,
                        symbol,
                        timeframe,
                        str(exc),
                    )

        return klines

    @classmethod
    def _pick_resample_source(
        cls,
        target_ccxt_timeframe: str,
        exchange_timeframes: Dict[str, Any],
    ) -> Optional[Tuple[str, int]]:
        """Pick the finest supported source timeframe to resample into `target_ccxt_timeframe`.

        Returns (source_ccxt_timeframe, bucket_size) or None if no candidate is supported.
        """
        for source, bucket in cls._RESAMPLE_CANDIDATES.get(target_ccxt_timeframe, []):
            if source in exchange_timeframes:
                return source, bucket
        return None

    @staticmethod
    def _resample_ohlcv(ohlcv: List[List[Any]], bucket_size: int) -> List[List[Any]]:
        """Aggregate every `bucket_size` consecutive CCXT OHLCV rows into one larger candle.

        Each input row is [ts_ms, open, high, low, close, volume]. Output preserves the
        first row's timestamp and open, takes max(high)/min(low), the last row's close,
        and sums volume. The trailing partial bucket is dropped so every returned candle
        represents `bucket_size` source candles.
        """
        if bucket_size <= 1 or not ohlcv:
            return list(ohlcv or [])
        out: List[List[Any]] = []
        for i in range(0, len(ohlcv), bucket_size):
            chunk = ohlcv[i:i + bucket_size]
            if len(chunk) < bucket_size:
                break  # drop incomplete trailing bucket
            out.append([
                chunk[0][0],
                chunk[0][1],
                max(c[2] for c in chunk),
                min(c[3] for c in chunk),
                chunk[-1][4],
                sum(c[5] for c in chunk),
            ])
        return out

    @classmethod
    def _ccxt_to_qd_timeframe(cls, ccxt_tf: str, fallback: str) -> str:
        """Reverse the TIMEFRAME_MAP — e.g. '1d' → '1D'. Used so downstream helpers
        that take the QuantDinger-style timeframe string get a consistent value when
        we fetch a different granularity than originally requested."""
        for qd, ccxt_value in cls.TIMEFRAME_MAP.items():
            if ccxt_value == ccxt_tf:
                return qd
        return fallback

    def _fetch_ohlcv(
        self,
        symbol_pair: str,
        ccxt_timeframe: str,
        limit: int,
        before_time: Optional[int],
        timeframe: str,
        after_time: Optional[int] = None,
    ) -> List:
        """获取OHLCV数据（支持分页获取完整数据）"""
        try:
            if before_time:
                total_seconds = self.calculate_time_range(timeframe, limit)
                now_ts = int(datetime.now(timezone.utc).timestamp())
                safe_before_ts = min(int(before_time), now_ts)
                if safe_before_ts < int(before_time):
                    logger.debug(
                        "CCXT OHLCV: clamped before_time %s -> %s (utc now cap for exchange)",
                        before_time,
                        safe_before_ts,
                    )
                end_dt = datetime.fromtimestamp(safe_before_ts, tz=timezone.utc)
                start_dt = end_dt - timedelta(seconds=total_seconds)
                if after_time is not None:
                    floor_dt = datetime.fromtimestamp(int(after_time), tz=timezone.utc)
                    start_dt = min(start_dt, floor_dt)
                timeframe_ms = TIMEFRAME_SECONDS.get(timeframe, 86400) * 1000
                now_ms = now_ts * 1000
                since = int(start_dt.timestamp() * 1000)
                if since >= now_ms:
                    since = max(0, now_ms - timeframe_ms)
                end_ms = safe_before_ts * 1000

                exchange_id = str(getattr(self.exchange, "id", "") or "").strip().lower()
                recent_limit = int(self._RECENT_CANDLE_LIMITS.get(exchange_id, 0) or 0)
                if recent_limit > 0:
                    earliest_supported_ms = max(0, now_ms - timeframe_ms * (recent_limit - 1))
                    if end_ms < earliest_supported_ms:
                        logger.info(
                            "Skipped %s %s history because the requested end precedes the exchange recent-candle window",
                            exchange_id,
                            ccxt_timeframe,
                        )
                        return []
                    if since < earliest_supported_ms:
                        logger.info(
                            "Clamped %s %s history start to the exchange recent-candle limit (%s bars)",
                            exchange_id,
                            ccxt_timeframe,
                            recent_limit,
                        )
                        since = earliest_supported_ms

                all_ohlcv: List[List[Any]] = []
                batch_limit = 300  # Conservative cross-provider request limit.
                current_since = since
                max_batches = 6000
                empty_streak = 0
                max_empty = 6
                # Long-range fetches (months of 5m candles) need to be defensive
                # about transient exchange errors and rate limits. We do per-batch
                # retries, a short sleep between batches, and a wall-clock budget
                # so the calling HTTP request can't spin forever and 500 out.
                import time as _t
                retry_per_batch = 2
                inter_batch_sleep = 0.0
                if not getattr(self.exchange, 'enableRateLimit', False):
                    # If CCXT isn't throttling for us, throttle ourselves to ~6 req/s
                    # to stay below typical exchange ceilings.
                    inter_batch_sleep = 0.15
                fetch_started_at = _t.monotonic()
                # Hard wall-clock budget. 5m / 90 days needs ~86 batches; assume
                # 1.5s/batch worst case => ~130s. We give 180s headroom.
                fetch_budget_seconds = 180.0

                for batch_idx in range(max_batches):
                    if current_since >= end_ms:
                        break
                    if (_t.monotonic() - fetch_started_at) > fetch_budget_seconds:
                        logger.warning(
                            f"CCXT paginated fetch budget exceeded for {symbol_pair} {ccxt_timeframe} "
                            f"after {batch_idx} batches ({len(all_ohlcv)} candles); returning partial."
                        )
                        break

                    batch = None
                    last_err = None
                    for attempt in range(retry_per_batch + 1):
                        try:
                            batch = self.exchange.fetch_ohlcv(
                                symbol_pair,
                                ccxt_timeframe,
                                since=current_since,
                                limit=batch_limit,
                            )
                            break
                        except Exception as exc:
                            last_err = exc
                            if attempt < retry_per_batch:
                                # Brief back-off (0.5s, 1.5s) tolerates short rate-limit / network blips
                                # without amplifying load when the exchange is genuinely down.
                                _t.sleep(0.5 + attempt * 1.0)
                                continue
                            raise
                    if batch is None:
                        # Exhausted retries — re-raise so the outer except can flip
                        # to the fallback path. Should not reach here because the
                        # last attempt re-raises directly, but kept for clarity.
                        raise last_err if last_err else RuntimeError("CCXT fetch_ohlcv failed without error")

                    if not batch:
                        empty_streak += 1
                        if empty_streak >= max_empty:
                            break
                        current_since += timeframe_ms * min(batch_limit, 64)
                        if inter_batch_sleep:
                            _t.sleep(inter_batch_sleep)
                        continue
                    empty_streak = 0
                    all_ohlcv.extend(batch)
                    last_timestamp = batch[-1][0]
                    if last_timestamp >= end_ms:
                        break
                    next_since = last_timestamp + timeframe_ms
                    if next_since <= current_since:
                        break
                    current_since = next_since
                    if inter_batch_sleep:
                        _t.sleep(inter_batch_sleep)

                by_ts = {int(row[0]): row for row in all_ohlcv if row and len(row) >= 6}
                ohlcv = sorted(by_ts.values(), key=lambda r: r[0])
                if not ohlcv:
                    return self._fetch_ohlcv_fallback(
                        symbol_pair, ccxt_timeframe, limit, before_time, timeframe, after_time
                    )
            else:
                # No window specified: ask for at most the exchange's per-call cap.
                # Passing the raw `limit` here can be tens of thousands for long
                # high-precision backtests; most exchanges respond by either
                # rejecting the request outright or silently truncating, which
                # downstream code then interprets as "empty data".
                safe_limit = min(int(limit), self._SINGLE_FETCH_HARD_CAP)
                ohlcv = self.exchange.fetch_ohlcv(symbol_pair, ccxt_timeframe, limit=safe_limit)

            return ohlcv

        except Exception as e:
            if _is_symbol_not_found_error(e):
                self._mark_invalid_symbol(symbol_pair, e)
                return []
            partial_rows = locals().get("all_ohlcv") or []
            if partial_rows:
                logger.warning(
                    "CCXT paginated fetch stopped early for %s %s; returning %s available candles: %s",
                    symbol_pair,
                    ccxt_timeframe,
                    len(partial_rows),
                    str(e),
                )
                by_ts = {int(row[0]): row for row in partial_rows if row and len(row) >= 6}
                return sorted(by_ts.values(), key=lambda row: row[0])
            logger.warning(f"CCXT fetch_ohlcv failed: {str(e)}; trying fallback")
            return self._fetch_ohlcv_fallback(
                symbol_pair, ccxt_timeframe, limit, before_time, timeframe, after_time
            )
    
    def _fetch_ohlcv_fallback(
        self,
        symbol_pair: str,
        ccxt_timeframe: str,
        limit: int,
        before_time: Optional[int],
        timeframe: str,
        after_time: Optional[int] = None,
    ) -> List:
        """备用获取方法"""
        try:
            total_seconds = self.calculate_time_range(timeframe, limit)
            
            if before_time:
                now_ts = int(datetime.now(timezone.utc).timestamp())
                safe_before_ts = min(int(before_time), now_ts)
                end_dt = datetime.fromtimestamp(safe_before_ts, tz=timezone.utc)
                start_dt = end_dt - timedelta(seconds=total_seconds)
                if after_time is not None:
                    floor_dt = datetime.fromtimestamp(int(after_time), tz=timezone.utc)
                    start_dt = min(start_dt, floor_dt)
                tf_ms = TIMEFRAME_SECONDS.get(timeframe, 86400) * 1000
                now_ms = now_ts * 1000
                since = int(start_dt.timestamp() * 1000)
                if since >= now_ms:
                    since = max(0, now_ms - tf_ms)
            else:
                since = int((datetime.now() - timedelta(seconds=total_seconds)).timestamp() * 1000)
            
            # IMPORTANT: most exchanges cap fetch_ohlcv at 300–1000 candles per
            # call. The original code forwarded the caller's `limit` verbatim
            # (often 50k+ for long-range backtests), which the exchange would
            # then reject or silently truncate — making this "fallback" useless
            # exactly when it mattered. Cap it so we at least return one valid
            # page of data, which downstream callers can then handle gracefully.
            safe_limit = min(int(limit), self._SINGLE_FETCH_HARD_CAP)
            ohlcv = self.exchange.fetch_ohlcv(symbol_pair, ccxt_timeframe, since=since, limit=safe_limit)
            if ohlcv:
                return ohlcv
        except Exception as e:
            if _is_symbol_not_found_error(e):
                self._mark_invalid_symbol(symbol_pair, e)
                return []
            logger.warning("Requested-window fallback failed for %s: %s", symbol_pair, str(e))

        try:
            recent = self.exchange.fetch_ohlcv(
                symbol_pair,
                ccxt_timeframe,
                limit=min(int(limit), self._SINGLE_FETCH_HARD_CAP),
            )
            if recent:
                logger.warning(
                    "Using the most recent %s candles for %s %s because the requested history window is unavailable",
                    len(recent),
                    symbol_pair,
                    ccxt_timeframe,
                )
                return recent
        except Exception as e:
            if _is_symbol_not_found_error(e):
                self._mark_invalid_symbol(symbol_pair, e)
            else:
                logger.error("Recent-candle fallback also failed for %s: %s", symbol_pair, str(e))
        return []
