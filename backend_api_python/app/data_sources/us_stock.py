"""
美股数据源
使用 yfinance 和 finnhub 获取数据
"""
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta

import yfinance as yf
import requests

from app.data_sources.base import BaseDataSource
from app.utils.logger import get_logger
from app.config import APIKeys, YFinanceConfig

logger = get_logger(__name__)


class USStockDataSource(BaseDataSource):
    """美股数据源"""
    
    name = "USStock/yfinance"
    
    INTERVAL_MAP = {
        '1m': '1m',
        '3m': '1m',
        '5m': '5m',
        '15m': '15m',
        '30m': '30m',
        '1H': '1h',
        '4H': '4h',
        '1D': '1d',
        '1W': '1wk'
    }
    
    DAYS_MAP = {
        '1m': lambda limit: min(7, max(1, (limit // 390) + 2)),
        '3m': lambda limit: min(7, max(1, (limit // 130) + 2)),
        '5m': lambda limit: min(60, max(1, (limit // 78) + 2)),
        '15m': lambda limit: min(60, max(2, (limit // 26) + 3)),
        '30m': lambda limit: min(60, max(2, (limit // 13) + 3)),
        '1H': lambda limit: min(730, max(5, int(limit / 6.5 * 7 / 5 * 1.5) + 5)),
        '4H': lambda limit: min(730, max(10, int(limit / 1.625 * 7 / 5 * 1.5) + 5)),
        '1D': lambda limit: min(3650, limit + 1),
        '1W': lambda limit: min(3650, (limit * 7) + 7)
    }

    MERGE_FACTOR_MAP = {
        '3m': 3,
    }

    TIMEFRAME_ALIASES = {
        '1h': '1H',
        '1hour': '1H',
        '60m': '1H',
        '4h': '4H',
        '1d': '1D',
        '1day': '1D',
        'd': '1D',
        '1w': '1W',
        '1wk': '1W',
        'w': '1W',
    }

    NASDAQ_HEADERS = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://www.nasdaq.com",
        "Referer": "https://www.nasdaq.com/market-activity/stocks",
    }
    
    def __init__(self):
        self.finnhub_client = None
        try:
            import finnhub
            if APIKeys.is_configured('FINNHUB_API_KEY'):
                self.finnhub_client = finnhub.Client(api_key=APIKeys.FINNHUB_API_KEY)
                logger.info("Finnhub client initialized")
        except Exception as e:
            logger.warning(f"Finnhub init failed: {e}")

    def _normalize_timeframe(self, timeframe: str) -> str:
        raw = str(timeframe or "1D").strip()
        return self.TIMEFRAME_ALIASES.get(raw.lower(), raw)
    
    def get_ticker(self, symbol: str) -> Dict[str, Any]:
        """
        获取美股实时报价
        
        优先使用 Finnhub（更实时），降级使用 yfinance fast_info
        
        Returns:
            dict: {
                'last': 当前价格,
                'change': 涨跌额,
                'changePercent': 涨跌幅,
                'high': 最高价,
                'low': 最低价,
                'open': 开盘价,
                'previousClose': 昨收价
            }
        """
        symbol = (symbol or '').strip().upper()
        
        if self.finnhub_client:
            try:
                quote = self.finnhub_client.quote(symbol)
                if quote and quote.get('c'):
                    return {
                        'last': quote.get('c', 0),           # 当前价格
                        'change': quote.get('d', 0),         # 涨跌额
                        'changePercent': quote.get('dp', 0), # 涨跌幅
                        'high': quote.get('h', 0),           # 日内最高
                        'low': quote.get('l', 0),            # 日内最低
                        'open': quote.get('o', 0),           # 开盘价
                        'previousClose': quote.get('pc', 0)  # 昨收价
                    }
            except Exception as e:
                msg = str(e).lower()
                if "403" in str(e) or "don't have access" in msg or "no access" in msg:
                    logger.debug(f"Finnhub quote skipped (no access): {symbol}: {e}")
                else:
                    logger.warning(f"Finnhub quote failed for {symbol}: {e}")

        nasdaq_quote = self._fetch_nasdaq_quote(symbol)
        if nasdaq_quote:
            return nasdaq_quote

        yahoo_quote = self._fetch_yahoo_chart_quote(symbol)
        if yahoo_quote:
            return yahoo_quote
        
        try:
            ticker = yf.Ticker(symbol)
            
            try:
                fast_info = ticker.fast_info
                last_price = fast_info.get('lastPrice') or fast_info.get('last_price')
                prev_close = fast_info.get('previousClose') or fast_info.get('previous_close') or fast_info.get('regularMarketPreviousClose')
                
                if last_price:
                    change = (last_price - prev_close) if prev_close else 0
                    change_pct = (change / prev_close * 100) if prev_close else 0
                    return {
                        'last': float(last_price),
                        'change': round(change, 4),
                        'changePercent': round(change_pct, 2),
                        'high': float(fast_info.get('dayHigh') or fast_info.get('day_high') or last_price),
                        'low': float(fast_info.get('dayLow') or fast_info.get('day_low') or last_price),
                        'open': float(fast_info.get('open') or fast_info.get('regularMarketOpen') or last_price),
                        'previousClose': float(prev_close) if prev_close else 0
                    }
            except Exception as e:
                logger.debug(f"yfinance fast_info failed for {symbol}: {e}")
            
            try:
                info = ticker.info
                last_price = info.get('regularMarketPrice') or info.get('currentPrice')
                prev_close = info.get('regularMarketPreviousClose') or info.get('previousClose')
                
                if last_price:
                    change = (last_price - prev_close) if prev_close else 0
                    change_pct = (change / prev_close * 100) if prev_close else 0
                    return {
                        'last': float(last_price),
                        'change': round(change, 4),
                        'changePercent': round(change_pct, 2),
                        'high': float(info.get('regularMarketDayHigh') or info.get('dayHigh') or last_price),
                        'low': float(info.get('regularMarketDayLow') or info.get('dayLow') or last_price),
                        'open': float(info.get('regularMarketOpen') or info.get('open') or last_price),
                        'previousClose': float(prev_close) if prev_close else 0
                    }
            except Exception as e:
                logger.debug(f"yfinance info failed for {symbol}: {e}")
            
            try:
                hist = ticker.history(period='1d', interval='1m')
                if hist is not None and not hist.empty:
                    last_row = hist.iloc[-1]
                    first_row = hist.iloc[0]
                    last_price = float(last_row['Close'])
                    open_price = float(first_row['Open'])
                    
                    return {
                        'last': last_price,
                        'change': round(last_price - open_price, 4),
                        'changePercent': round((last_price - open_price) / open_price * 100, 2) if open_price else 0,
                        'high': float(hist['High'].max()),
                        'low': float(hist['Low'].min()),
                        'open': open_price,
                        'previousClose': open_price  # 近似
                    }
            except Exception as e:
                logger.debug(f"yfinance history fallback failed for {symbol}: {e}")
                
        except Exception as e:
            logger.error(f"Failed to get ticker for {symbol}: {e}")
        
        return {'last': 0, 'symbol': symbol}
    
    def get_kline(
        self,
        symbol: str,
        timeframe: str,
        limit: int,
        before_time: Optional[int] = None,
        after_time: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """获取美股K线数据"""
        klines = []
        timeframe = self._normalize_timeframe(timeframe)
        
        try:
            interval = self.INTERVAL_MAP.get(timeframe, '1d')
            days_func = self.DAYS_MAP.get(timeframe, lambda x: x + 1)
            merge_factor = self.MERGE_FACTOR_MAP.get(timeframe, 1)
            effective_limit = limit * merge_factor
            days = days_func(effective_limit)
            
            if before_time:
                end_date = datetime.fromtimestamp(before_time)
                start_date = end_date - timedelta(days=days)
            else:
                end_date = datetime.now()
                start_date = end_date - timedelta(days=days)
            if after_time is not None:
                floor = datetime.fromtimestamp(after_time)
                start_date = min(start_date, floor)
            
            
            klines = self._fetch_yahoo_chart(symbol, interval, start_date, end_date, effective_limit)
            if not klines:
                if timeframe in ('1m', '3m', '5m', '15m', '30m', '1H', '4H'):
                    klines = self._fetch_nasdaq_intraday_chart(symbol, timeframe, effective_limit)
                else:
                    klines = self._fetch_nasdaq_historical(symbol, start_date, end_date, effective_limit)
                    if timeframe == '1W' and klines:
                        klines = self._merge_every_n_sorted_bars(klines, 5)
            df = None
            if not klines:
                df = self._fetch_yfinance(symbol, interval, start_date, end_date)
            
            if not klines and (df is None or df.empty):
                if self.finnhub_client and timeframe == '1D':
                    klines = self._fetch_finnhub(symbol, start_date, end_date, limit)
                    if klines:
                        return self.filter_and_limit(
                            klines,
                            limit,
                            before_time,
                            after_time,
                            truncate=(after_time is None),
                        )
            elif not klines:
                klines = self._convert_dataframe(df, effective_limit)
                if merge_factor > 1:
                    klines = self._merge_every_n_sorted_bars(klines, merge_factor)
            
            klines = self.filter_and_limit(
                klines,
                limit,
                before_time,
                after_time,
                truncate=(after_time is None),
            )
            
            self.log_result(symbol, klines, timeframe)
            
        except Exception as e:
            logger.error(f"Failed to fetch US stock K-lines {symbol}: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
        
        return klines

    @staticmethod
    def _parse_nasdaq_number(value, default: float = 0.0) -> float:
        if value is None:
            return default
        try:
            text = str(value).strip()
            if not text or text in {"--", "N/A"}:
                return default
            text = text.replace("$", "").replace(",", "").replace("%", "").replace("+", "")
            return float(text)
        except Exception:
            return default

    def _fetch_nasdaq_quote(self, symbol: str) -> Optional[Dict[str, Any]]:
        try:
            resp = requests.get(
                f"https://api.nasdaq.com/api/quote/{symbol}/info",
                params={"assetclass": "stocks"},
                timeout=10,
                headers=self.NASDAQ_HEADERS,
            )
            resp.raise_for_status()
            payload = resp.json()
            primary = (((payload.get("data") or {}).get("primaryData")) or {})
            if not primary:
                return None

            last_price = self._parse_nasdaq_number(primary.get("lastSalePrice"))
            if last_price <= 0:
                return None
            change = self._parse_nasdaq_number(primary.get("netChange"))
            change_pct = self._parse_nasdaq_number(primary.get("percentageChange"))
            return {
                "last": last_price,
                "change": round(change, 4),
                "changePercent": round(change_pct, 2),
                "high": last_price,
                "low": last_price,
                "open": last_price,
                "previousClose": round(last_price - change, 4) if change else 0,
            }
        except Exception as e:
            logger.debug(f"Nasdaq quote failed for {symbol}: {e}")
            return None

    def _fetch_nasdaq_intraday_chart(
        self,
        symbol: str,
        timeframe: str,
        limit: int,
    ) -> List[Dict[str, Any]]:
        try:
            resp = requests.get(
                f"https://api.nasdaq.com/api/quote/{symbol}/chart",
                params={"assetclass": "stocks"},
                timeout=10,
                headers=self.NASDAQ_HEADERS,
            )
            resp.raise_for_status()
            payload = resp.json()
            points = (((payload.get("data") or {}).get("chart")) or [])
            bars: List[Dict[str, Any]] = []
            for point in points:
                ts_ms = point.get("x")
                price = point.get("y")
                if price is None:
                    price = (point.get("z") or {}).get("value")
                price_f = self._parse_nasdaq_number(price)
                if not ts_ms or price_f <= 0:
                    continue
                ts = int(int(ts_ms) / 1000)
                bars.append(self.format_kline(
                    timestamp=ts,
                    open_price=price_f,
                    high=price_f,
                    low=price_f,
                    close=price_f,
                    volume=0,
                ))

            if not bars:
                return []
            merge_n = {
                "3m": 3,
                "5m": 5,
                "15m": 15,
                "30m": 30,
                "1H": 60,
                "4H": 240,
            }.get(timeframe, 1)
            if merge_n > 1:
                bars = self._merge_every_n_sorted_bars(bars, merge_n)
            return bars[-limit:] if limit and len(bars) > limit else bars
        except Exception as e:
            logger.debug(f"Nasdaq intraday chart failed for {symbol}: {e}")
            return []

    def _fetch_nasdaq_historical(
        self,
        symbol: str,
        start_date: datetime,
        end_date: datetime,
        limit: int,
    ) -> List[Dict[str, Any]]:
        try:
            resp = requests.get(
                f"https://api.nasdaq.com/api/quote/{symbol}/historical",
                params={
                    "assetclass": "stocks",
                    "fromdate": start_date.strftime("%Y-%m-%d"),
                    "todate": end_date.strftime("%Y-%m-%d"),
                    "limit": max(int(limit or 100), 100),
                },
                timeout=12,
                headers=self.NASDAQ_HEADERS,
            )
            resp.raise_for_status()
            payload = resp.json()
            rows = ((((payload.get("data") or {}).get("tradesTable")) or {}).get("rows")) or []
            bars: List[Dict[str, Any]] = []
            for row in rows:
                try:
                    dt = datetime.strptime(str(row.get("date")), "%m/%d/%Y")
                    open_price = self._parse_nasdaq_number(row.get("open"))
                    high = self._parse_nasdaq_number(row.get("high"))
                    low = self._parse_nasdaq_number(row.get("low"))
                    close = self._parse_nasdaq_number(row.get("close"))
                    if min(open_price, high, low, close) <= 0:
                        continue
                    bars.append(self.format_kline(
                        timestamp=int(dt.timestamp()),
                        open_price=open_price,
                        high=high,
                        low=low,
                        close=close,
                        volume=self._parse_nasdaq_number(row.get("volume")),
                    ))
                except Exception:
                    continue

            bars.sort(key=lambda x: x["time"])
            return bars[-limit:] if limit and len(bars) > limit else bars
        except Exception as e:
            logger.debug(f"Nasdaq historical failed for {symbol}: {e}")
            return []

    def _fetch_yahoo_chart_quote(self, symbol: str) -> Optional[Dict[str, Any]]:
        try:
            resp = requests.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
                params={"range": "1d", "interval": "1m", "includePrePost": "false"},
                timeout=8,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            resp.raise_for_status()
            payload = resp.json()
            result = ((payload.get("chart") or {}).get("result") or [None])[0]
            if not result:
                return None

            meta = result.get("meta") or {}
            quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
            closes = [float(v) for v in (quote.get("close") or []) if v is not None]
            opens = [float(v) for v in (quote.get("open") or []) if v is not None]
            highs = [float(v) for v in (quote.get("high") or []) if v is not None]
            lows = [float(v) for v in (quote.get("low") or []) if v is not None]

            last_price = meta.get("regularMarketPrice") or (closes[-1] if closes else None)
            prev_close = meta.get("chartPreviousClose") or meta.get("previousClose") or (opens[0] if opens else None)
            if not last_price:
                return None

            last_price = float(last_price)
            prev_close_f = float(prev_close) if prev_close else 0.0
            change = last_price - prev_close_f if prev_close_f else 0.0
            change_pct = (change / prev_close_f * 100) if prev_close_f else 0.0
            return {
                "last": last_price,
                "change": round(change, 4),
                "changePercent": round(change_pct, 2),
                "high": max(highs) if highs else float(meta.get("regularMarketDayHigh") or last_price),
                "low": min(lows) if lows else float(meta.get("regularMarketDayLow") or last_price),
                "open": opens[0] if opens else float(meta.get("regularMarketOpen") or last_price),
                "previousClose": prev_close_f,
            }
        except Exception as e:
            logger.debug(f"Yahoo chart quote failed for {symbol}: {e}")
            return None

    def _fetch_yahoo_chart(
        self,
        symbol: str,
        interval: str,
        start_date: datetime,
        end_date: datetime,
        limit: int,
    ) -> List[Dict[str, Any]]:
        try:
            resp = requests.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
                params={
                    "period1": int(start_date.timestamp()),
                    "period2": int((end_date + timedelta(days=1)).timestamp()),
                    "interval": interval,
                    "includePrePost": "false",
                    "events": "history",
                },
                timeout=10,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            resp.raise_for_status()
            payload = resp.json()
            result = ((payload.get("chart") or {}).get("result") or [None])[0]
            if not result:
                return []

            timestamps = result.get("timestamp") or []
            quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
            opens = quote.get("open") or []
            highs = quote.get("high") or []
            lows = quote.get("low") or []
            closes = quote.get("close") or []
            volumes = quote.get("volume") or []

            bars: List[Dict[str, Any]] = []
            for i, ts in enumerate(timestamps):
                try:
                    open_price = opens[i]
                    high = highs[i]
                    low = lows[i]
                    close = closes[i]
                    if open_price is None or high is None or low is None or close is None:
                        continue
                    bars.append(self.format_kline(
                        timestamp=int(ts),
                        open_price=float(open_price),
                        high=float(high),
                        low=float(low),
                        close=float(close),
                        volume=float(volumes[i] or 0) if i < len(volumes) else 0,
                    ))
                except Exception:
                    continue
            return bars[-limit:] if limit and len(bars) > limit else bars
        except Exception as e:
            logger.debug(f"Yahoo chart kline failed for {symbol}: {e}")
            return []
    
    def _fetch_yfinance(self, symbol: str, interval: str, start_date: datetime, end_date: datetime):
        """使用 yfinance 获取数据"""
        try:
            ticker = yf.Ticker(symbol)
            
            end_date_inclusive = end_date + timedelta(days=1)
            
            df = ticker.history(
                start=start_date.strftime('%Y-%m-%d'),
                end=end_date_inclusive.strftime('%Y-%m-%d'),
                interval=interval
            )
            return df
        except Exception as e:
            logger.warning(f"yfinance fetch failed: {e}")
            return None

    def _merge_every_n_sorted_bars(self, bars: List[Dict[str, Any]], n: int) -> List[Dict[str, Any]]:
        if n <= 1 or len(bars) < n:
            return bars
        bars = sorted(bars, key=lambda x: x['time'])
        out = []
        for i in range(0, len(bars) - len(bars) % n, n):
            chunk = bars[i:i + n]
            out.append({
                'time': chunk[0]['time'],
                'open': chunk[0]['open'],
                'high': max(b['high'] for b in chunk),
                'low': min(b['low'] for b in chunk),
                'close': chunk[-1]['close'],
                'volume': round(sum(b['volume'] for b in chunk), 2),
            })
        return out

    def _fetch_finnhub(
        self,
        symbol: str,
        start_date: datetime,
        end_date: datetime,
        limit: int
    ) -> List[Dict[str, Any]]:
        """使用 finnhub 获取日线数据"""
        klines = []
        try:
            start_ts = int(start_date.timestamp())
            end_ts = int(end_date.timestamp())
            
            candles = self.finnhub_client.stock_candles(symbol, 'D', start_ts, end_ts)
            
            if candles and candles.get('s') == 'ok':
                for i in range(len(candles['t'])):
                    klines.append(self.format_kline(
                        timestamp=candles['t'][i],
                        open_price=candles['o'][i],
                        high=candles['h'][i],
                        low=candles['l'][i],
                        close=candles['c'][i],
                        volume=candles['v'][i]
                    ))
        except Exception as e:
            msg = str(e).lower()
            # Free tier / plan: 403 "You don't have access to this resource" is common; avoid ERROR spam.
            if "403" in str(e) or "don't have access" in msg or "no access" in msg:
                logger.debug(f"Finnhub candles skipped (no access): {symbol}: {e}")
            else:
                logger.warning(f"Finnhub fetch failed: {e}")
        
        return klines
    
    def _convert_dataframe(self, df, limit: int) -> List[Dict[str, Any]]:
        """转换 DataFrame 为K线列表"""
        klines = []
        df = df.tail(limit).reset_index()
        
        time_col = None
        if 'Datetime' in df.columns:
            time_col = 'Datetime'
        elif 'Date' in df.columns:
            time_col = 'Date'
        elif 'index' in df.columns:
            time_col = 'index'
        
        if time_col is None:
            logger.warning(f"Unable to determine time column; available columns: {df.columns.tolist()}")
            return klines
        
        for _, row in df.iterrows():
            try:
                time_value = row[time_col]
                if hasattr(time_value, 'timestamp'):
                    ts = int(time_value.timestamp())
                else:
                    continue
                
                klines.append(self.format_kline(
                    timestamp=ts,
                    open_price=row['Open'],
                    high=row['High'],
                    low=row['Low'],
                    close=row['Close'],
                    volume=row['Volume']
                ))
            except Exception as e:
                logger.debug(f"Failed to parse row data: {e}")
                continue
        
        return klines

