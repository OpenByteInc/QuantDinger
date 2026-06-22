"""
外汇数据源
三级降级: Twelve Data → Tiingo → yfinance
"""
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta
import os
import time
import requests
import threading
import yfinance as yf

from app.data_sources.base import BaseDataSource, TIMEFRAME_SECONDS
from app.utils.logger import get_logger
from app.config import TiingoConfig, APIKeys

logger = get_logger(__name__)


def normalize_forex_pair_symbol(symbol: str) -> str:
    """EUR/USD、XAU/USD、XAU-USD -> EURUSD、XAUUSD，供 Tiingo SYMBOL_MAP 与内部缓存键一致。"""
    if not symbol:
        return symbol
    s = str(symbol).strip().upper().replace(" ", "").replace("-", "")
    s = s.replace("/", "")
    aliases = {
        "XAU": "XAUUSD",
        "GOLD": "XAUUSD",
        "XAG": "XAGUSD",
        "SILVER": "XAGUSD",
    }
    return aliases.get(s, s)


_forex_cache: Dict[str, Dict[str, Any]] = {}
_forex_cache_lock = threading.Lock()
_FOREX_CACHE_TTL = 60

# ---------------------------------------------------------------------------
# Twelve Data helpers
# ---------------------------------------------------------------------------

_TD_INTERVAL_MAP = {
    '1m': '1min', '5m': '5min', '15m': '15min', '30m': '30min',
    '1H': '1h', '4H': '4h', '1D': '1day', '1W': '1week', '1M': '1month',
}

_TD_SYMBOL_MAP = {
    'XAUUSD': 'XAU/USD', 'XAGUSD': 'XAG/USD',
    'EURUSD': 'EUR/USD', 'GBPUSD': 'GBP/USD', 'USDJPY': 'USD/JPY',
    'AUDUSD': 'AUD/USD', 'USDCAD': 'USD/CAD', 'USDCHF': 'USD/CHF',
    'NZDUSD': 'NZD/USD', 'GBPJPY': 'GBP/JPY', 'EURJPY': 'EUR/JPY',
    'EURGBP': 'EUR/GBP', 'AUDNZD': 'AUD/NZD', 'USDCNH': 'USD/CNH',
}

_YF_SYMBOL_MAP = {
    'XAUUSD': 'GC=F', 'XAGUSD': 'SI=F',
    'EURUSD': 'EURUSD=X', 'GBPUSD': 'GBPUSD=X', 'USDJPY': 'USDJPY=X',
    'AUDUSD': 'AUDUSD=X', 'USDCAD': 'USDCAD=X', 'USDCHF': 'USDCHF=X',
    'NZDUSD': 'NZDUSD=X', 'GBPJPY': 'GBPJPY=X', 'EURJPY': 'EURJPY=X',
    'EURGBP': 'EURGBP=X', 'USDCNH': 'USDCNH=X',
}

_YF_TIMEFRAME_MAP = {
    '1m': '1m', '5m': '5m', '15m': '15m', '30m': '30m',
    '1H': '1h', '4H': '4h', '1D': '1d', '1W': '1wk', '1M': '1mo',
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


def _td_forex_symbol(symbol: str) -> str:
    """Convert internal symbol (e.g. EURUSD) to Twelve Data format (EUR/USD)."""
    s = symbol.upper().strip()
    if s in _TD_SYMBOL_MAP:
        return _TD_SYMBOL_MAP[s]
    if "/" in s:
        return s
    if len(s) == 6:
        return f"{s[:3]}/{s[3:]}"
    return s


def _td_request(url: str, params: dict, timeout: int = 20) -> Optional[dict]:
    for attempt in range(3):
        try:
            resp = requests.get(url, params=params, timeout=timeout)
            data = resp.json()
            if data.get("status") == "error":
                logger.debug("TwelveData error: %s", data.get("message", ""))
                return None
            return data
        except Exception as e:
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            logger.debug("TwelveData request failed: %s", e)
    return None


class ForexDataSource(BaseDataSource):
    """外汇数据源 — Twelve Data (primary) + Tiingo (fallback)"""

    name = "Forex/TwelveData+Tiingo"

    TIMEFRAME_MAP = {
        '1m': '1min', '5m': '5min', '15m': '15min', '30m': '30min',
        '1H': '1hour', '4H': '4hour', '1D': '1day',
        '1W': None, '1M': None,
    }

    SYMBOL_MAP = {
        'XAUUSD': 'xauusd', 'XAGUSD': 'xagusd',
        'EURUSD': 'eurusd', 'GBPUSD': 'gbpusd', 'USDJPY': 'usdjpy',
        'AUDUSD': 'audusd', 'USDCAD': 'usdcad', 'USDCHF': 'usdchf',
        'NZDUSD': 'nzdusd',
    }

    def __init__(self):
        self.base_url = TiingoConfig.BASE_URL
        td_key = _get_td_api_key()
        tiingo_key = APIKeys.TIINGO_API_KEY
        if not td_key and not tiingo_key:
            logger.warning("Neither Twelve Data nor Tiingo API key configured; FX data will be limited")
    
    def get_ticker(self, symbol: str) -> Dict[str, Any]:
        """
        获取外汇实时报价
        Priority: Twelve Data → Tiingo → yfinance
        """
        symbol = normalize_forex_pair_symbol(symbol)
        cache_key = f"ticker_{symbol}"
        with _forex_cache_lock:
            cached = _forex_cache.get(cache_key)
            if cached and time.time() - cached.get('_cache_time', 0) < _FOREX_CACHE_TTL:
                return cached

        for fetcher in (
            self._get_ticker_twelvedata,
            self._get_ticker_tiingo,
            self._get_ticker_yfinance,
        ):
            try:
                result = fetcher(symbol)
                if result and result.get("last", 0) > 0:
                    result["_cache_time"] = time.time()
                    with _forex_cache_lock:
                        _forex_cache[cache_key] = result
                    return result
            except Exception as e:
                logger.debug("Forex ticker fetcher %s failed for %s: %s", fetcher.__name__, symbol, e)

        return {'last': 0, 'symbol': symbol}

    def _get_ticker_twelvedata(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Fetch forex quote from Twelve Data /quote endpoint."""
        api_key = _get_td_api_key()
        if not api_key:
            return None
        td_sym = _td_forex_symbol(symbol)
        data = _td_request("https://api.twelvedata.com/quote", {
            "symbol": td_sym, "apikey": api_key,
        })
        if not data or not data.get("close"):
            return None
        try:
            last = float(data.get("close") or 0)
            prev = float(data.get("previous_close") or 0)
            change = last - prev if prev else 0
            change_pct = (change / prev * 100) if prev else 0
            return {
                "last": round(last, 5),
                "change": round(change, 5),
                "changePercent": round(change_pct, 2),
                "previousClose": round(prev, 5),
                "symbol": symbol,
            }
        except Exception as e:
            logger.debug("TwelveData forex quote parse failed %s: %s", symbol, e)
            return None

    def _get_ticker_tiingo(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Fetch forex quote from Tiingo (legacy fallback)."""
        api_key = APIKeys.TIINGO_API_KEY
        if not api_key:
            return None

        cache_key = f"ticker_{symbol}"
        try:
            tiingo_symbol = self.SYMBOL_MAP.get(symbol)
            if not tiingo_symbol:
                tiingo_symbol = symbol.lower()
            
            # Tiingo FX Top-of-Book API
            # https://api.tiingo.com/tiingo/fx/top?tickers=eurusd&token=...
            url = f"{self.base_url}/fx/top"
            params = {
                'tickers': tiingo_symbol,
                'token': api_key
            }
            
            for attempt in range(3):
                response = requests.get(url, params=params, timeout=TiingoConfig.TIMEOUT)
                if response.status_code == 429:
                    wait_time = 2 * (attempt + 1)
                    logger.warning(f"Tiingo rate limit (429), waiting {wait_time}s before retry ({attempt+1}/3)")
                    time.sleep(wait_time)
                    continue
                break
            
            if response.status_code == 429:
                logger.warning("Tiingo rate limit exceeded for ticker request")
                logger.info("Note: Tiingo 1-minute forex data requires a paid subscription")
                with _forex_cache_lock:
                    if cache_key in _forex_cache:
                        logger.info(f"Returning stale cache for {symbol} due to rate limit")
                        return _forex_cache[cache_key]
                return {'last': 0, 'symbol': symbol}
            
            response.raise_for_status()
            data = response.json()
            
            if data and isinstance(data, list) and len(data) > 0:
                item = data[0]
                # Tiingo FX top returns: ticker, quoteTimestamp, bidPrice, bidSize, askPrice, askSize, midPrice
                bid = float(item.get('bidPrice', 0) or 0)
                ask = float(item.get('askPrice', 0) or 0)
                mid = float(item.get('midPrice', 0) or 0)
                
                if not mid and bid and ask:
                    mid = (bid + ask) / 2
                
                last_price = mid or bid or ask
                
                prev_close = 0
                change = 0
                change_pct = 0
                
                try:
                    yesterday = (datetime.now() - timedelta(days=2)).strftime('%Y-%m-%d')
                    today = datetime.now().strftime('%Y-%m-%d')
                    price_url = f"{self.base_url}/fx/{tiingo_symbol}/prices"
                    price_params = {
                        'startDate': yesterday,
                        'endDate': today,
                        'resampleFreq': '1day',
                        'token': api_key
                    }
                    price_resp = requests.get(price_url, params=price_params, timeout=TiingoConfig.TIMEOUT)
                    if price_resp.status_code == 200:
                        price_data = price_resp.json()
                        if price_data and len(price_data) > 0:
                            prev_close = float(price_data[-1].get('close', 0) or 0)
                            if prev_close and last_price:
                                change = last_price - prev_close
                                change_pct = (change / prev_close) * 100
                except Exception:
                    pass  # 涨跌计算失败不影响主要功能
                
                return {
                    'last': round(last_price, 5),
                    'bid': round(bid, 5),
                    'ask': round(ask, 5),
                    'change': round(change, 5),
                    'changePercent': round(change_pct, 2),
                    'previousClose': round(prev_close, 5) if prev_close else 0,
                    'symbol': symbol,
                }

        except Exception as e:
            logger.debug("Tiingo forex ticker failed %s: %s", symbol, e)

        return None

    def _get_ticker_yfinance(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Fetch forex quote from yfinance (Tier 3 fallback)."""
        yf_sym = _YF_SYMBOL_MAP.get(symbol.upper())
        if not yf_sym:
            s = symbol.upper()
            yf_sym = f"{s}=X" if len(s) == 6 and not s.endswith("=X") else s
        try:
            t = yf.Ticker(yf_sym)
            hist = t.history(period="2d", interval="1d")
            if hist is None or hist.empty:
                return None
            current = float(hist["Close"].iloc[-1])
            prev = float(hist["Close"].iloc[-2]) if len(hist) >= 2 else 0
            change = current - prev if prev else 0
            change_pct = (change / prev * 100) if prev else 0
            return {
                "last": round(current, 5),
                "change": round(change, 5),
                "changePercent": round(change_pct, 2),
                "previousClose": round(prev, 5),
                "symbol": symbol,
            }
        except Exception as e:
            logger.debug("yfinance forex ticker failed %s: %s", symbol, e)
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
        获取外汇K线数据
        Priority: Twelve Data → Tiingo → yfinance
        """
        symbol = normalize_forex_pair_symbol(symbol)
        for fetcher in (
            self._get_kline_twelvedata,
            self._get_kline_tiingo,
            self._get_kline_yfinance,
        ):
            try:
                bars = fetcher(symbol, timeframe, limit, before_time)
                if bars:
                    return self.filter_and_limit(
                        bars,
                        limit,
                        before_time,
                        after_time=after_time,
                        truncate=(after_time is None),
                    )
            except Exception as e:
                logger.debug("Forex kline fetcher %s failed for %s: %s", fetcher.__name__, symbol, e)
        return []

    def _get_kline_twelvedata(
        self, symbol: str, timeframe: str, limit: int, before_time: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Fetch forex K-lines from Twelve Data /time_series."""
        api_key = _get_td_api_key()
        if not api_key:
            return []
        interval = _TD_INTERVAL_MAP.get(timeframe)
        if not interval:
            return []
        td_sym = _td_forex_symbol(symbol)
        params: Dict[str, Any] = {
            "symbol": td_sym,
            "interval": interval,
            "outputsize": min(int(limit), 5000),
            "apikey": api_key,
            "format": "JSON",
            "dp": "5",
        }
        if before_time:
            params["end_date"] = datetime.fromtimestamp(int(before_time)).strftime("%Y-%m-%d %H:%M:%S")

        data = _td_request("https://api.twelvedata.com/time_series", params)
        if not data:
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
                    "volume": 0.0,
                })
            except Exception:
                continue
        klines.sort(key=lambda x: x["time"])
        if len(klines) > limit:
            klines = klines[-limit:]
        logger.debug("TwelveData forex kline %s %s: %d bars", td_sym, timeframe, len(klines))
        return klines

    def _get_kline_tiingo(
        self, symbol: str, timeframe: str, limit: int, before_time: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Fetch forex K-lines from Tiingo (legacy fallback)."""
        api_key = APIKeys.TIINGO_API_KEY
        if not api_key:
            return []
            
        try:
            tiingo_symbol = self.SYMBOL_MAP.get(symbol)
            if not tiingo_symbol:
                tiingo_symbol = symbol.lower()

            resample_freq = self.TIMEFRAME_MAP.get(timeframe)
            
            aggregate_to_weekly = (timeframe == '1W')
            aggregate_to_monthly = (timeframe == '1M')
            original_limit = limit  # 保存原始请求数量
            
            if aggregate_to_weekly or aggregate_to_monthly:
                resample_freq = '1day'
                max_limit = 100 if aggregate_to_weekly else 36
                original_limit = min(original_limit, max_limit)
                limit = original_limit * (7 if aggregate_to_weekly else 30)
            
            if not resample_freq:
                logger.warning(f"Tiingo does not support timeframe: {timeframe}")
                return []
            
            if timeframe == '1m':
                logger.info(f"Note: Tiingo 1-minute forex data requires a paid subscription")
            
            if before_time:
                end_dt = datetime.fromtimestamp(before_time)
            else:
                end_dt = datetime.now()
            
            if aggregate_to_weekly or aggregate_to_monthly:
                tf_seconds = 86400  # 日线秒数
            else:
                tf_seconds = self._get_timeframe_seconds(timeframe)
            start_dt = end_dt - timedelta(seconds=limit * tf_seconds * 1.5)
            
            max_days = 365 * 3  # 最多 3 年
            if (end_dt - start_dt).days > max_days:
                start_dt = end_dt - timedelta(days=max_days)
                logger.info(f"Tiingo: Limited date range to {max_days} days")
            
            start_date_str = start_dt.strftime('%Y-%m-%d')
            end_date_str = end_dt.strftime('%Y-%m-%d')
            
            # URL: https://api.tiingo.com/tiingo/fx/{ticker}/prices
            url = f"{self.base_url}/fx/{tiingo_symbol}/prices"
            
            params = {
                'startDate': start_date_str,
                'endDate': end_date_str,
                'resampleFreq': resample_freq,
                'token': api_key,
                'format': 'json'
            }
            
            # logger.info(f"Tiingo Request: {url} params={params}")
            
            max_retries = 3
            retry_delay = 2  # 秒
            response = None
            
            for attempt in range(max_retries):
                try:
                    response = requests.get(url, params=params, timeout=TiingoConfig.TIMEOUT)
                    
                    if response.status_code == 429:
                        wait_time = retry_delay * (attempt + 1)
                        logger.warning(f"Tiingo rate limit (429), waiting {wait_time}s before retry ({attempt + 1}/{max_retries})")
                        time.sleep(wait_time)
                        continue
                    
                    break  # 成功或其他错误，退出重试循环
                    
                except requests.exceptions.Timeout:
                    if attempt < max_retries - 1:
                        logger.warning(f"Tiingo request timeout, retrying ({attempt + 1}/{max_retries})")
                        time.sleep(retry_delay)
                        continue
                    raise
            
            if response is None:
                logger.error("Tiingo API request failed after all retries")
                return []
            
            if response.status_code == 429:
                logger.error("Tiingo API rate limit exceeded. Please wait a moment before retrying.")
                return []
            
            if response.status_code == 403:
                logger.error("Tiingo API permission error (403): check whether your API key is valid and has access to this dataset.")
                return []
                 
            response.raise_for_status()
            data = response.json()
            
            # Tiingo returns a list of dicts:
            # [
            #   {
            #     "date": "2023-01-01T00:00:00.000Z",
            #     "ticker": "eurusd",
            #     "open": 1.07,
            #     "high": 1.08,
            #     "low": 1.06,
            #     "close": 1.07
            #     "mid": ... (optional, depends on settings, usually OHLC are bid or mid)
            #   }, ...
            # ]
            # Note: Tiingo FX prices objects keys: date, open, high, low, close.
            
            if not isinstance(data, list):
                logger.warning(f"Tiingo response is not a list: {data}")
                return []
                
            klines = []
            for item in data:
                dt_str = item.get('date')
                if dt_str.endswith('Z'):
                    dt_str = dt_str[:-1] + '+00:00'  # 替换 Z 为 +00:00 表示 UTC
                
                dt = datetime.fromisoformat(dt_str)
                ts = int(dt.timestamp())  # 现在会正确处理 UTC 时区
                
                klines.append({
                    'time': ts,
                    'open': float(item.get('open')),
                    'high': float(item.get('high')),
                    'low': float(item.get('low')),
                    'close': float(item.get('close')),
                    'volume': 0.0 # Tiingo FX 通常没有 volume
                })
            
            klines.sort(key=lambda x: x['time'])
            
            if aggregate_to_weekly:
                klines = self._aggregate_to_weekly(klines)
                logger.debug(f"Aggregated {len(klines)} weekly candles from daily data")
            elif aggregate_to_monthly:
                klines = self._aggregate_to_monthly(klines)
                logger.debug(f"Aggregated {len(klines)} monthly candles from daily data")
            
            if len(klines) > original_limit:
                klines = klines[-original_limit:]
            
            return klines
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Tiingo API request failed: {e}")
            return []
        except Exception as e:
            logger.error(f"Failed to process Tiingo data: {e}")
            return []
    
    def _aggregate_to_weekly(self, daily_klines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """将日线数据聚合为周线"""
        if not daily_klines:
            return []
        
        weekly_klines = []
        current_week = None
        week_data = None
        
        for kline in daily_klines:
            dt = datetime.fromtimestamp(kline['time'])
            week_start = dt - timedelta(days=dt.weekday())
            week_key = week_start.strftime('%Y-%W')
            
            if week_key != current_week:
                if week_data:
                    weekly_klines.append(week_data)
                current_week = week_key
                week_data = {
                    'time': int(week_start.timestamp()),
                    'open': kline['open'],
                    'high': kline['high'],
                    'low': kline['low'],
                    'close': kline['close'],
                    'volume': kline['volume']
                }
            else:
                week_data['high'] = max(week_data['high'], kline['high'])
                week_data['low'] = min(week_data['low'], kline['low'])
                week_data['close'] = kline['close']
                week_data['volume'] += kline['volume']
        
        if week_data:
            weekly_klines.append(week_data)
        
        return weekly_klines
    
    def _aggregate_to_monthly(self, daily_klines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """将日线数据聚合为月线"""
        if not daily_klines:
            return []
        
        monthly_klines = []
        current_month = None
        month_data = None
        
        for kline in daily_klines:
            dt = datetime.fromtimestamp(kline['time'])
            month_key = dt.strftime('%Y-%m')
            
            if month_key != current_month:
                if month_data:
                    monthly_klines.append(month_data)
                current_month = month_key
                month_start = dt.replace(day=1, hour=0, minute=0, second=0)
                month_data = {
                    'time': int(month_start.timestamp()),
                    'open': kline['open'],
                    'high': kline['high'],
                    'low': kline['low'],
                    'close': kline['close'],
                    'volume': kline['volume']
                }
            else:
                month_data['high'] = max(month_data['high'], kline['high'])
                month_data['low'] = min(month_data['low'], kline['low'])
                month_data['close'] = kline['close']
                month_data['volume'] += kline['volume']
        
        if month_data:
            monthly_klines.append(month_data)
        
        return monthly_klines

    def _get_kline_yfinance(
        self, symbol: str, timeframe: str, limit: int, before_time: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Fetch forex K-lines from yfinance (Tier 3 fallback)."""
        yf_sym = _YF_SYMBOL_MAP.get(symbol.upper())
        if not yf_sym:
            s = symbol.upper()
            yf_sym = f"{s}=X" if len(s) == 6 and not s.endswith("=X") else s

        yf_interval = _YF_TIMEFRAME_MAP.get(timeframe)
        if not yf_interval:
            return []

        try:
            if before_time:
                end_dt = datetime.fromtimestamp(before_time)
            else:
                end_dt = datetime.now()

            tf_seconds = self._get_timeframe_seconds(timeframe)
            start_dt = end_dt - timedelta(seconds=tf_seconds * limit * 1.5)
            end_dt_inclusive = end_dt + timedelta(days=1)

            t = yf.Ticker(yf_sym)
            df = t.history(start=start_dt, end=end_dt_inclusive, interval=yf_interval)
            if df is None or df.empty:
                return []

            klines = []
            for idx, row in df.iterrows():
                klines.append({
                    'time': int(idx.timestamp()),
                    'open': float(row['Open']),
                    'high': float(row['High']),
                    'low': float(row['Low']),
                    'close': float(row['Close']),
                    'volume': float(row.get('Volume', 0) or 0),
                })
            klines.sort(key=lambda x: x['time'])
            if len(klines) > limit:
                klines = klines[-limit:]
            logger.debug("yfinance forex kline %s %s: %d bars", yf_sym, timeframe, len(klines))
            return klines
        except Exception as e:
            logger.debug("yfinance forex kline failed %s: %s", symbol, e)
            return []
