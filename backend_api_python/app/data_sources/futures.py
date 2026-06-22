"""
期货数据源
支持：
1. 加密货币期货（Binance Futures via CCXT）
2. 传统期货（三级降级: Twelve Data → yfinance → Tiingo(贵金属)）
"""
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta
import os
import time
import ccxt
import requests
import yfinance as yf

from app.data_sources.base import BaseDataSource, TIMEFRAME_SECONDS
from app.utils.logger import get_logger
from app.config import CCXTConfig, TiingoConfig, APIKeys

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Twelve Data helpers for traditional futures
# ---------------------------------------------------------------------------

_TD_INTERVAL_MAP = {
    '1m': '1min', '5m': '5min', '15m': '15min', '30m': '30min',
    '1H': '1h', '4H': '4h', '1D': '1day', '1W': '1week',
}

_TD_FUTURES_SYMBOLS = {
    'GC': 'GC', 'SI': 'SI', 'CL': 'CL', 'NG': 'NG',
    'ZC': 'ZC', 'ZW': 'ZW', 'HG': 'HG', 'PL': 'PL',
    'ES': 'ES', 'NQ': 'NQ', 'YM': 'YM', 'RTY': 'RTY',
}


def _get_td_api_key() -> str:
    try:
        from app.utils.config_loader import load_addon_config
        key = load_addon_config().get("twelve_data", {}).get("api_key", "")
        if key:
            return key
    except Exception:
        pass
    return (os.getenv("TWELVE_DATA_API_KEY") or "").strip()


# Tiingo FX covers precious metals as spot forex (XAUUSD, XAGUSD)
_TIINGO_PRECIOUS_METALS_MAP = {
    'GC': 'xauusd',
    'SI': 'xagusd',
}

_TIINGO_TIMEFRAME_MAP = {
    '5m': '5min', '15m': '15min', '30m': '30min',
    '1H': '1hour', '4H': '4hour', '1D': '1day',
}


class FuturesDataSource(BaseDataSource):
    """期货数据源"""
    
    name = "Futures"
    
    YF_TIMEFRAME_MAP = {
        '1m': '1m',
        '5m': '5m',
        '15m': '15m',
        '30m': '30m',
        '1H': '1h',
        '4H': '4h',
        '1D': '1d',
        '1W': '1wk'
    }
    
    CCXT_TIMEFRAME_MAP = CCXTConfig.TIMEFRAME_MAP
    
    YF_SYMBOLS = {
        'GC': 'GC=F',   # 黄金期货
        'SI': 'SI=F',   # 白银期货
        'CL': 'CL=F',   # 原油期货
        'NG': 'NG=F',   # 天然气期货
        'ZC': 'ZC=F',   # 玉米期货
        'ZW': 'ZW=F',   # 小麦期货
    }
    
    def __init__(self):
        config = {
            'timeout': CCXTConfig.TIMEOUT,
            'enableRateLimit': CCXTConfig.ENABLE_RATE_LIMIT,
            'options': {
                'defaultType': 'future'
            }
        }
        
        if CCXTConfig.PROXY:
            config['proxies'] = {
                'http': CCXTConfig.PROXY,
                'https': CCXTConfig.PROXY
            }
        
        self.exchange = ccxt.binance(config)

    def get_ticker(self, symbol: str) -> Dict[str, Any]:
        """
        Get latest ticker for futures symbol.
        Traditional futures: Twelve Data → yfinance fallback.
        Crypto futures: CCXT.
        """
        sym = (symbol or "").strip()
        is_traditional = sym in self.YF_SYMBOLS or sym.endswith("=F") or sym in _TD_FUTURES_SYMBOLS
        if is_traditional:
            for fetcher in (self._get_ticker_twelvedata, self._get_ticker_yfinance, self._get_ticker_tiingo):
                try:
                    result = fetcher(sym)
                    if result and result.get("last", 0) > 0:
                        return result
                except Exception as e:
                    logger.debug("Futures ticker %s failed for %s: %s", fetcher.__name__, sym, e)
            return {"symbol": sym, "last": 0.0}

        if ":" in sym:
            sym = sym.split(":", 1)[0]
        sym = sym.upper()
        if "/" not in sym:
            if sym.endswith("USDT") and len(sym) > 4:
                sym = f"{sym[:-4]}/USDT"
            elif sym.endswith("USD") and len(sym) > 3:
                sym = f"{sym[:-3]}/USD"
        return self.exchange.fetch_ticker(sym)

    def _get_ticker_twelvedata(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Fetch traditional futures quote from Twelve Data."""
        api_key = _get_td_api_key()
        if not api_key:
            return None
        td_sym = _TD_FUTURES_SYMBOLS.get(symbol.replace("=F", ""), symbol.replace("=F", ""))
        try:
            resp = requests.get("https://api.twelvedata.com/quote", params={
                "symbol": td_sym, "apikey": api_key,
            }, timeout=15)
            data = resp.json()
            if data.get("status") == "error" or not data.get("close"):
                return None
            last = float(data.get("close") or 0)
            prev = float(data.get("previous_close") or 0)
            change = last - prev if prev else 0
            change_pct = (change / prev * 100) if prev else 0
            return {
                "symbol": symbol,
                "last": round(last, 4),
                "change": round(change, 4),
                "changePercent": round(change_pct, 2),
                "previousClose": round(prev, 4),
            }
        except Exception as e:
            logger.debug("TwelveData futures quote failed %s: %s", symbol, e)
            return None

    def _get_ticker_yfinance(self, symbol: str) -> Dict[str, Any]:
        """Fetch traditional futures quote from yfinance (fallback)."""
        try:
            yf_symbol = self.YF_SYMBOLS.get(symbol, symbol)
            if not yf_symbol.endswith("=F"):
                yf_symbol = yf_symbol + "=F"
            t = yf.Ticker(yf_symbol)
            last = None
            try:
                last = getattr(t, "fast_info", {}).get("last_price")
            except Exception:
                last = None
            if last is None:
                hist = t.history(period="2d", interval="1d")
                if hist is not None and not hist.empty:
                    last = float(hist["Close"].iloc[-1])
            return {"symbol": yf_symbol, "last": float(last or 0.0)}
        except Exception:
            return {"symbol": symbol, "last": 0.0}

    def _get_ticker_tiingo(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Fetch precious metals quote via Tiingo FX (Tier 3 — GC→XAUUSD, SI→XAGUSD only)."""
        tiingo_sym = _TIINGO_PRECIOUS_METALS_MAP.get(symbol.replace("=F", ""))
        if not tiingo_sym:
            return None
        api_key = APIKeys.TIINGO_API_KEY
        if not api_key:
            return None
        try:
            resp = requests.get(f"{TiingoConfig.BASE_URL}/fx/top", params={
                "tickers": tiingo_sym, "token": api_key,
            }, timeout=TiingoConfig.TIMEOUT)
            if resp.status_code != 200:
                return None
            data = resp.json()
            if not data or not isinstance(data, list) or len(data) == 0:
                return None
            item = data[0]
            mid = float(item.get("midPrice") or 0)
            bid = float(item.get("bidPrice") or 0)
            ask = float(item.get("askPrice") or 0)
            last = mid or ((bid + ask) / 2 if bid and ask else bid or ask)
            if not last:
                return None
            return {"symbol": symbol, "last": round(last, 4)}
        except Exception as e:
            logger.debug("Tiingo futures ticker failed %s: %s", symbol, e)
            return None

    def _get_timeframe_seconds(self, timeframe: str) -> int:
        """获取时间周期对应的秒数"""
        return TIMEFRAME_SECONDS.get(timeframe, 86400)
    
    def get_kline(
        self,
        symbol: str,
        timeframe: str,
        limit: int,
        before_time: Optional[int] = None,
        after_time: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        获取期货K线数据
        
        Args:
            symbol: 期货合约代码
            timeframe: 时间周期
            limit: 数据条数
            before_time: 结束时间戳
            after_time: 预留与基类一致（当前期货链路未使用）
        """
        _ = after_time
        if symbol in self.YF_SYMBOLS or symbol.endswith('=F'):
            return self._get_traditional_futures(symbol, timeframe, limit, before_time)
        else:
            return self._get_crypto_futures(symbol, timeframe, limit, before_time)
    
    def _get_traditional_futures(
        self,
        symbol: str,
        timeframe: str,
        limit: int,
        before_time: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Twelve Data → yfinance → Tiingo(precious metals) for traditional futures K-lines."""
        for fetcher in (
            self._get_traditional_futures_td,
            self._get_traditional_futures_yf,
            self._get_traditional_futures_tiingo,
        ):
            try:
                bars = fetcher(symbol, timeframe, limit, before_time)
                if bars:
                    return bars
            except Exception as e:
                logger.debug("Futures kline %s failed for %s: %s", fetcher.__name__, symbol, e)
        return []

    def _get_traditional_futures_td(
        self, symbol: str, timeframe: str, limit: int, before_time: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Fetch traditional futures K-lines from Twelve Data."""
        api_key = _get_td_api_key()
        if not api_key:
            return []
        interval = _TD_INTERVAL_MAP.get(timeframe)
        if not interval:
            return []
        td_sym = _TD_FUTURES_SYMBOLS.get(symbol.replace("=F", ""), symbol.replace("=F", ""))
        params: Dict[str, Any] = {
            "symbol": td_sym,
            "interval": interval,
            "outputsize": min(int(limit), 5000),
            "apikey": api_key,
            "format": "JSON",
            "dp": "4",
        }
        if before_time:
            params["end_date"] = datetime.fromtimestamp(int(before_time)).strftime("%Y-%m-%d %H:%M:%S")
        try:
            resp = requests.get("https://api.twelvedata.com/time_series", params=params, timeout=20)
            data = resp.json()
            if data.get("status") == "error":
                logger.debug("TwelveData futures kline error %s: %s", td_sym, data.get("message", ""))
                return []
            values = data.get("values") or []
            if not values:
                return []
            klines = []
            for v in values:
                try:
                    dt = datetime.fromisoformat(v["datetime"])
                    klines.append({
                        "time": int(dt.timestamp()),
                        "open": float(v["open"]),
                        "high": float(v["high"]),
                        "low": float(v["low"]),
                        "close": float(v["close"]),
                        "volume": float(v.get("volume") or 0),
                    })
                except Exception:
                    continue
            klines.sort(key=lambda x: x["time"])
            if len(klines) > limit:
                klines = klines[-limit:]
            logger.debug("TwelveData futures kline %s %s: %d bars", td_sym, timeframe, len(klines))
            return klines
        except Exception as e:
            logger.debug("TwelveData futures kline failed %s: %s", symbol, e)
            return []

    def _get_traditional_futures_yf(
        self, symbol: str, timeframe: str, limit: int, before_time: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Fetch traditional futures K-lines from yfinance (fallback)."""
        try:
            yf_symbol = self.YF_SYMBOLS.get(symbol, symbol)
            if not yf_symbol.endswith('=F'):
                yf_symbol = symbol + '=F'

            yf_interval = self.YF_TIMEFRAME_MAP.get(timeframe, '1d')

            if before_time:
                end_time = datetime.fromtimestamp(before_time)
            else:
                end_time = datetime.now()

            tf_seconds = self._get_timeframe_seconds(timeframe)
            start_time = end_time - timedelta(seconds=tf_seconds * limit * 1.5)
            end_time_inclusive = end_time + timedelta(days=1)

            ticker = yf.Ticker(yf_symbol)
            df = ticker.history(start=start_time, end=end_time_inclusive, interval=yf_interval)

            if df.empty:
                logger.warning("No yfinance data: %s", yf_symbol)
                return []

            klines = []
            for index, row in df.iterrows():
                klines.append({
                    'time': int(index.timestamp()),
                    'open': float(row['Open']),
                    'high': float(row['High']),
                    'low': float(row['Low']),
                    'close': float(row['Close']),
                    'volume': float(row['Volume']),
                })

            klines.sort(key=lambda x: x['time'])
            if len(klines) > limit:
                klines = klines[-limit:]
            return klines

        except Exception as e:
            logger.error("yfinance futures kline failed: %s", e)
            return []

    def _get_traditional_futures_tiingo(
        self, symbol: str, timeframe: str, limit: int, before_time: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Fetch precious metals K-lines via Tiingo FX (Tier 3 — GC→XAUUSD, SI→XAGUSD only)."""
        tiingo_sym = _TIINGO_PRECIOUS_METALS_MAP.get(symbol.replace("=F", ""))
        if not tiingo_sym:
            return []
        api_key = APIKeys.TIINGO_API_KEY
        if not api_key:
            return []
        resample = _TIINGO_TIMEFRAME_MAP.get(timeframe)
        if not resample:
            return []
        try:
            if before_time:
                end_dt = datetime.fromtimestamp(before_time)
            else:
                end_dt = datetime.now()
            tf_seconds = self._get_timeframe_seconds(timeframe)
            start_dt = end_dt - timedelta(seconds=tf_seconds * limit * 1.5)
            resp = requests.get(f"{TiingoConfig.BASE_URL}/fx/{tiingo_sym}/prices", params={
                "startDate": start_dt.strftime("%Y-%m-%d"),
                "endDate": end_dt.strftime("%Y-%m-%d"),
                "resampleFreq": resample,
                "token": api_key,
                "format": "json",
            }, timeout=TiingoConfig.TIMEOUT)
            if resp.status_code != 200:
                return []
            data = resp.json()
            if not isinstance(data, list):
                return []
            klines = []
            for item in data:
                dt_str = item.get("date", "")
                if dt_str.endswith("Z"):
                    dt_str = dt_str[:-1] + "+00:00"
                dt = datetime.fromisoformat(dt_str)
                klines.append({
                    "time": int(dt.timestamp()),
                    "open": float(item.get("open", 0)),
                    "high": float(item.get("high", 0)),
                    "low": float(item.get("low", 0)),
                    "close": float(item.get("close", 0)),
                    "volume": 0.0,
                })
            klines.sort(key=lambda x: x["time"])
            if len(klines) > limit:
                klines = klines[-limit:]
            logger.debug("Tiingo futures kline %s %s: %d bars", tiingo_sym, timeframe, len(klines))
            return klines
        except Exception as e:
            logger.debug("Tiingo futures kline failed %s: %s", symbol, e)
            return []

    def _get_crypto_futures(
        self,
        symbol: str,
        timeframe: str,
        limit: int,
        before_time: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """使用CCXT获取加密货币期货数据"""
        try:
            ccxt_symbol = symbol if '/' in symbol else f"{symbol}/USDT"
            ccxt_timeframe = self.CCXT_TIMEFRAME_MAP.get(timeframe, '1d')
            
            
            if before_time:
                since_time = before_time - limit * self._get_timeframe_seconds(timeframe)
                ohlcv = self.exchange.fetch_ohlcv(
                    ccxt_symbol, 
                    ccxt_timeframe, 
                    since=since_time * 1000,
                    limit=limit
                )
            else:
                ohlcv = self.exchange.fetch_ohlcv(
                    ccxt_symbol, 
                    ccxt_timeframe, 
                    limit=limit
                )
            
            klines = []
            for candle in ohlcv:
                klines.append({
                    'time': int(candle[0] / 1000),
                    'open': float(candle[1]),
                    'high': float(candle[2]),
                    'low': float(candle[3]),
                    'close': float(candle[4]),
                    'volume': float(candle[5])
                })
            
            return klines
            
        except Exception as e:
            logger.error(f"Failed to fetch crypto futures data: {e}")
            return []

