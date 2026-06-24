"""
市场数据采集服务 - AI分析专用

设计理念：
1. 数据为王 - 先把数据获取做好、做稳定
2. 统一数据源 - 完全复用 DataSourceFactory 和 kline_service
3. 复用全球金融板块 - 宏观数据、情绪数据复用 global_market.py 的缓存
4. 快速稳定 - 不依赖慢速外部服务（如Jina Reader）

数据源映射：
- 价格/K线: DataSourceFactory (已验证，与K线模块、自选列表一致)
- 宏观数据: 复用 global_market.py (VIX, DXY, TNX, Fear&Greed等，带缓存)
- 新闻: Finnhub API (结构化数据，无需深度阅读)
- 基本面: Finnhub (美股) / 固定描述 (加密)
"""

import time
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError

import yfinance as yf
import pandas as pd
import requests

from app.data_sources import DataSourceFactory
from app.services.kline import KlineService
from app.utils.logger import get_logger
from app.config import APIKeys, FinnhubConfig

logger = get_logger(__name__)


class NonBlockingThreadPoolExecutor(ThreadPoolExecutor):
    """Thread pool that does not wait for slow optional data providers on exit."""

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.shutdown(wait=False, cancel_futures=True)
        return False


class MarketDataCollector:
    """
    市场数据采集器
    
    职责：为AI分析提供完整、准确、及时的市场数据
    
    数据层次：
    1. 核心数据 (必须成功): 价格、K线
    2. 分析数据 (增强): 技术指标、基本面
    3. 宏观数据 (可选): 复用 global_market.py (VIX, DXY, TNX, Fear&Greed等)
    4. 情绪数据 (可选): 新闻、市场情绪
    """
    
    def __init__(self):
        self.kline_service = KlineService()
        self._finnhub_client = None
        self._ak = None
        self._crypto_metric_cache: Dict[str, Dict[str, Any]] = {}
        self._init_clients()
    
    def _init_clients(self):
        """初始化外部API客户端"""
        # Finnhub
        finnhub_key = APIKeys.FINNHUB_API_KEY
        if finnhub_key:
            try:
                import finnhub
                self._finnhub_client = finnhub.Client(api_key=finnhub_key)
            except Exception as e:
                logger.warning(f"Finnhub client init failed: {e}")
        
        # akshare (optional, for supplementary data)
        try:
            import akshare as ak
            self._ak = ak
        except ImportError:
            logger.info("akshare not installed")
    
    def collect_all(
        self,
        market: str,
        symbol: str,
        timeframe: str = "1D",
        include_macro: bool = True,
        include_news: bool = True,
        timeout: int = 30
    ) -> Dict[str, Any]:
        """
        采集所有市场数据
        
        Args:
            market: 市场类型 (USStock, Crypto, Forex, Futures)
            symbol: 标的代码
            timeframe: K线周期
            include_macro: 是否包含宏观数据
            include_news: 是否包含新闻
            timeout: 总超时时间(秒)
            
        Returns:
            完整的市场数据字典
        """
        start_time = time.time()
        
        data = {
            "market": market,
            "symbol": symbol,
            "timeframe": timeframe,
            "collected_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "price": None,
            "kline": None,
            "indicators": {},
            "fundamental": {},
            "company": {},
            "crypto_factors": {},
            "macro": {},
            "news": [],
            "sentiment": {},
            "_meta": {
                "success_items": [],
                "failed_items": [],
                "duration_ms": 0
            }
        }
        
        with NonBlockingThreadPoolExecutor(max_workers=4) as executor:
            core_futures = {
                executor.submit(self._get_price, market, symbol): "price",
                executor.submit(self._get_kline, market, symbol, timeframe, 60): "kline",
            }
            
            if market in ('USStock', 'CNStock', 'HKStock'):
                core_futures[executor.submit(self._get_fundamental, market, symbol)] = "fundamental"
                core_futures[executor.submit(self._get_company, market, symbol)] = "company"
            elif market == 'Crypto':
                core_futures[executor.submit(self._get_crypto_info, symbol)] = "fundamental"
            
            try:
                for future in as_completed(core_futures, timeout=15):
                    key = core_futures[future]
                    try:
                        result = future.result(timeout=3)
                        if result:
                            data[key] = result
                            data["_meta"]["success_items"].append(key)
                        else:
                            data["_meta"]["failed_items"].append(key)
                    except Exception as e:
                        logger.warning(f"Core data fetch failed ({key}): {e}")
                        data["_meta"]["failed_items"].append(key)
            except TimeoutError:
                logger.warning(f"Core data fetch timed out for {market}:{symbol}")
        
        if data.get("kline"):
            data["indicators"] = self._calculate_indicators(data["kline"])
            data["_meta"]["success_items"].append("indicators")

        if market == 'Crypto':
            try:
                data["crypto_factors"] = self._get_crypto_factors(
                    symbol=symbol,
                    price_data=data.get("price") or {},
                    kline_data=data.get("kline") or [],
                )
                if data["crypto_factors"]:
                    data["_meta"]["success_items"].append("crypto_factors")
                else:
                    data["_meta"]["failed_items"].append("crypto_factors")
            except Exception as e:
                logger.warning(f"Crypto factor fetch failed for {symbol}: {e}")
                data["_meta"]["failed_items"].append("crypto_factors")
        
        if include_macro:
            try:
                data["macro"] = self._get_macro_data(market, timeout=10)
                if data["macro"]:
                    data["_meta"]["success_items"].append("macro")
            except Exception as e:
                logger.warning(f"Macro data fetch failed: {e}")
                data["_meta"]["failed_items"].append("macro")
        
        if include_news:
            try:
                company_name = None
                if data.get("company"):
                    company_name = data["company"].get("name")
                
                news_result = self._get_news(market, symbol, company_name, timeout=8)
                data["news"] = news_result.get("news", [])
                data["sentiment"] = news_result.get("sentiment", {})
                
                if data["news"]:
                    data["_meta"]["success_items"].append("news")
            except Exception as e:
                logger.warning(f"News fetch failed: {e}")
                data["_meta"]["failed_items"].append("news")
        
        data["_meta"]["duration_ms"] = int((time.time() - start_time) * 1000)
        logger.info(f"Market data collection completed for {market}:{symbol} in {data['_meta']['duration_ms']}ms")
        logger.info(f"  Success: {data['_meta']['success_items']}")
        logger.info(f"  Failed: {data['_meta']['failed_items']}")
        
        return data
    
    
    def _get_price(self, market: str, symbol: str) -> Optional[Dict[str, Any]]:
        """
        获取实时价格 - 使用 kline_service (与自选列表一致)
        """
        try:
            price_data = self.kline_service.get_realtime_price(market, symbol, force_refresh=True)
            if price_data and price_data.get('price', 0) > 0:
                def safe_float(val, default=0.0):
                    if val is None:
                        return default
                    try:
                        return float(val)
                    except (ValueError, TypeError):
                        return default
                
                price = safe_float(price_data.get('price'))
                return {
                    "price": price,
                    "change": safe_float(price_data.get('change')),
                    "changePercent": safe_float(price_data.get('changePercent')),
                    "high": safe_float(price_data.get('high'), price),
                    "low": safe_float(price_data.get('low'), price),
                    "open": safe_float(price_data.get('open'), price),
                    "previousClose": safe_float(price_data.get('previousClose'), price),
                    "source": price_data.get('source', 'unknown')
                }
        except Exception as e:
            logger.warning(f"Price fetch failed for {market}:{symbol}: {e}")
        
        try:
            klines = DataSourceFactory.get_kline(market, symbol, "1D", 2)
            if klines and len(klines) > 0:
                latest = klines[-1]
                price = float(latest.get('close', 0))
                if price > 0:
                    prev_close = float(klines[-2].get('close', price)) if len(klines) > 1 else price
                    change = price - prev_close
                    change_pct = (change / prev_close * 100) if prev_close > 0 else 0
                    
                    logger.info(f"Price fetched from K-line fallback for {market}:{symbol}: ${price}")
                    return {
                        "price": price,
                        "change": round(change, 6),
                        "changePercent": round(change_pct, 2),
                        "high": float(latest.get('high', price)),
                        "low": float(latest.get('low', price)),
                        "open": float(latest.get('open', price)),
                        "previousClose": prev_close,
                        "source": "kline_fallback"
                    }
        except Exception as e:
            logger.warning(f"K-line fallback price fetch also failed for {market}:{symbol}: {e}")
        
        return None
    
    def _get_kline(
        self, market: str, symbol: str, timeframe: str, limit: int = 60
    ) -> Optional[List[Dict[str, Any]]]:
        """
        获取K线数据 - 使用 DataSourceFactory (与K线模块一致)
        """
        try:
            klines = DataSourceFactory.get_kline(market, symbol, timeframe, limit)
            if klines and len(klines) > 0:
                return klines
        except Exception as e:
            logger.warning(f"Kline fetch failed for {market}:{symbol}: {e}")
        return None
    
    def _calculate_indicators(self, klines: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        计算技术指标 (本地计算，无外部依赖)
        
        返回格式符合前端 FastAnalysisReport.vue 的期望。
        口径说明（与常见行情终端对齐）：
        - RSI(14)：Wilder 平滑（首段均幅为前 14 期简单平均，其后递推）。
        - MACD：收盘 EMA12/EMA26（首值=前 N 日 SMA），信号线=MACD 的 EMA9（SMA 种子）。
        - MA：SMA。枢轴：上一根 K 的 H/L/C。摆动高低：近 20 根 H/L 窗口极值。
        - 布林：20 收盘 SMA ± 2×总体标准差。ATR(14)：Wilder（首 ATR=前 14 期 TR 简单平均，其后递推）。
        """
        if not klines or len(klines) < 5:
            return {}
        
        try:
            closes = [float(k.get('close', 0)) for k in klines]
            highs = [float(k.get('high', 0)) for k in klines]
            lows = [float(k.get('low', 0)) for k in klines]
            volumes = [float(k.get('volume', 0)) for k in klines]
            
            if not closes:
                return {}
            
            current_price = closes[-1]
            indicators = {}
            
            # ========== RSI ==========
            if len(closes) >= 15:
                rsi_value = self._calc_rsi(closes, 14)
                if rsi_value < 30:
                    rsi_signal = "oversold"
                elif rsi_value > 70:
                    rsi_signal = "overbought"
                else:
                    rsi_signal = "neutral"
                indicators['rsi'] = {
                    'value': round(rsi_value, 2),
                    'signal': rsi_signal,
                }
            
            if len(closes) >= 34:
                macd_raw = self._calc_macd(closes)
                macd_val = macd_raw.get('MACD', 0)
                macd_sig = macd_raw.get('MACD_signal', 0)
                macd_hist = macd_raw.get('MACD_histogram', 0)
                
                if macd_val > macd_sig and macd_hist > 0:
                    macd_signal = "bullish"
                    macd_trend = "golden_cross" if macd_hist > 0 else "bullish"
                elif macd_val < macd_sig and macd_hist < 0:
                    macd_signal = "bearish"
                    macd_trend = "death_cross" if macd_hist < 0 else "bearish"
                else:
                    macd_signal = "neutral"
                    macd_trend = "consolidating"
                
                indicators['macd'] = {
                    'value': round(macd_val, 6),
                    'signal_line': round(macd_sig, 6),
                    'histogram': round(macd_hist, 6),
                    'signal': macd_signal,
                    'trend': macd_trend,
                }
            
            ma5 = sum(closes[-5:]) / 5 if len(closes) >= 5 else current_price
            ma10 = sum(closes[-10:]) / 10 if len(closes) >= 10 else current_price
            ma20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else current_price
            
            if current_price > ma5 > ma10 > ma20:
                ma_trend = "strong_uptrend"
            elif current_price > ma20:
                ma_trend = "uptrend"
            elif current_price < ma5 < ma10 < ma20:
                ma_trend = "strong_downtrend"
            elif current_price < ma20:
                ma_trend = "downtrend"
            else:
                ma_trend = "sideways"
            
            indicators['moving_averages'] = {
                'ma5': round(ma5, 6),
                'ma10': round(ma10, 6),
                'ma20': round(ma20, 6),
                'trend': ma_trend,
            }

            bb_for_levels: Dict[str, Any] = {}
            if len(closes) >= 20:
                bb_for_levels = self._calc_bollinger(closes, 20, 2) or {}
            
            if len(klines) >= 2:
                prev_high = float(klines[-2].get('high', highs[-2]) if len(highs) >= 2 else current_price * 1.02)
                prev_low = float(klines[-2].get('low', lows[-2]) if len(lows) >= 2 else current_price * 0.98)
                prev_close = float(klines[-2].get('close', closes[-2]) if len(closes) >= 2 else current_price)
                
                pivot = (prev_high + prev_low + prev_close) / 3
                r1 = 2 * pivot - prev_low  # 阻力位1
                s1 = 2 * pivot - prev_high  # 支撑位1
                r2 = pivot + (prev_high - prev_low)  # 阻力位2
                s2 = pivot - (prev_high - prev_low)  # 支撑位2
            else:
                pivot = current_price
                r1 = r2 = current_price * 1.02
                s1 = s2 = current_price * 0.98
            
            recent_highs = highs[-20:] if len(highs) >= 20 else highs
            recent_lows = lows[-20:] if len(lows) >= 20 else lows
            swing_high = max(recent_highs) if recent_highs else current_price * 1.05
            swing_low = min(recent_lows) if recent_lows else current_price * 0.95
            
            bb_upper = bb_for_levels.get('BB_upper', swing_high)
            bb_lower = bb_for_levels.get('BB_lower', swing_low)
            
            resistance = round((r1 + swing_high + bb_upper) / 3, 6)
            support = round((s1 + swing_low + bb_lower) / 3, 6)
            
            indicators['levels'] = {
                'support': support,
                'resistance': resistance,
                'pivot': round(pivot, 6),
                's1': round(s1, 6),
                'r1': round(r1, 6),
                's2': round(s2, 6),
                'r2': round(r2, 6),
                'swing_high': round(swing_high, 6),
                'swing_low': round(swing_low, 6),
                'method': 'pivot_swing_bb_avg'  # 标注计算方法
            }
            
            atr = 0.0
            if len(klines) >= 14:
                atr = float(self._calc_atr_wilder(klines, period=14))
                volatility_pct = (atr / current_price * 100) if current_price > 0 else 0
                
                if volatility_pct > 5:
                    volatility_level = "high"
                elif volatility_pct > 2:
                    volatility_level = "medium"
                else:
                    volatility_level = "low"
            else:
                volatility_level = "unknown"
                volatility_pct = 0
            
            indicators['volatility'] = {
                'level': volatility_level,
                'pct': round(volatility_pct, 2),
                'atr': round(atr, 6),  # 添加 ATR 绝对值
            }
            
            atr_stop_loss = current_price - (2 * atr) if atr > 0 else current_price * 0.95
            support_stop = indicators['levels']['support']
            suggested_stop_loss = max(atr_stop_loss, support_stop * 0.99)  # 略低于支撑位
            
            atr_take_profit = current_price + (3 * atr) if atr > 0 else current_price * 1.05
            resistance_tp = indicators['levels']['resistance']
            suggested_take_profit = min(atr_take_profit, resistance_tp * 1.01)  # 略高于阻力位
            
            risk = current_price - suggested_stop_loss
            reward = suggested_take_profit - current_price
            risk_reward_ratio = round(reward / risk, 2) if risk > 0 else 0
            
            indicators['trading_levels'] = {
                'suggested_stop_loss': round(suggested_stop_loss, 6),
                'suggested_take_profit': round(suggested_take_profit, 6),
                'risk_reward_ratio': risk_reward_ratio,
                'atr_multiplier_sl': 2.0,  # 止损使用 2x ATR
                'atr_multiplier_tp': 3.0,  # 止盈使用 3x ATR
                'method': 'atr_support_resistance'
            }
            
            if bb_for_levels:
                indicators['bollinger'] = bb_for_levels
            
            if len(volumes) >= 20:
                avg_vol = sum(volumes[-20:]) / 20
                indicators['volume_ratio'] = round(volumes[-1] / avg_vol, 2) if avg_vol > 0 else 1.0
            
            if len(closes) >= 20:
                high_20 = max(highs[-20:])
                low_20 = min(lows[-20:])
                if high_20 > low_20:
                    indicators['price_position'] = round((current_price - low_20) / (high_20 - low_20) * 100, 1)
                else:
                    indicators['price_position'] = 50.0
            
            indicators['trend'] = ma_trend
            indicators['current_price'] = round(current_price, 6)
            
            return indicators
            
        except Exception as e:
            logger.warning(f"Indicator calculation failed: {e}")
            return {}
    
    def _calc_rsi(self, closes: List[float], period: int = 14) -> float:
        """Wilder RSI：首段均幅为前 period 期涨跌简单平均，之后按 Wilder 平滑递推。"""
        if len(closes) < period + 1:
            return 50.0

        deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        gains = [d if d > 0 else 0.0 for d in deltas]
        losses = [-d if d < 0 else 0.0 for d in deltas]

        if len(gains) < period:
            return 50.0

        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period

        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        return round(100.0 - (100.0 / (1.0 + rs)), 2)

    def _ema_series_sma_seed(self, data: List[float], period: int) -> List[Optional[float]]:
        """
        标准 EMA：首值 = 前 period 根简单平均（SMA），之后 EMA_t = (P_t - EMA_{t-1}) * k + EMA_{t-1}，k=2/(period+1)。
        前 period-1 根无定义，返回 None。
        """
        n = len(data)
        out: List[Optional[float]] = [None] * n
        if n < period:
            return out
        k = 2.0 / (period + 1)
        out[period - 1] = sum(data[:period]) / period
        for i in range(period, n):
            prev = out[i - 1]
            if prev is None:
                break
            out[i] = (data[i] - prev) * k + prev
        return out

    def _calc_macd(self, closes: List[float]) -> Dict[str, float]:
        """
        MACD(12,26,9)：DIF = EMA12(close) − EMA26(close)，DEA = EMA9(DIF)，柱 = DIF − DEA。
        各 EMA 均采用 SMA 种子；DIF 自第 26 根 K 起有定义，信号线对 DIF 子序列再算 EMA9。
        """
        n = len(closes)
        ema12 = self._ema_series_sma_seed(closes, 12)
        ema26 = self._ema_series_sma_seed(closes, 26)
        if n < 26 or ema12[-1] is None or ema26[-1] is None:
            return {'MACD': 0.0, 'MACD_signal': 0.0, 'MACD_histogram': 0.0}

        macd_sub: List[float] = []
        for i in range(25, n):
            v12 = ema12[i]
            v26 = ema26[i]
            if v12 is not None and v26 is not None:
                macd_sub.append(v12 - v26)

        if not macd_sub:
            return {'MACD': 0.0, 'MACD_signal': 0.0, 'MACD_histogram': 0.0}

        sig_series = self._ema_series_sma_seed(macd_sub, 9)
        last_macd = macd_sub[-1]
        last_sig = sig_series[-1]
        if last_sig is None:
            last_sig = last_macd

        return {
            'MACD': round(last_macd, 6),
            'MACD_signal': round(last_sig, 6),
            'MACD_histogram': round(last_macd - last_sig, 6),
        }

    def _true_ranges(self, klines: List[Dict[str, Any]]) -> List[float]:
        """每根 K 的 True Range（首根仅 H−L）。"""
        trs: List[float] = []
        for i, k in enumerate(klines):
            h = float(k.get('high', 0))
            l = float(k.get('low', 0))
            if h <= 0 or l <= 0:
                trs.append(0.0)
                continue
            if i == 0:
                trs.append(h - l)
            else:
                pc = float(klines[i - 1].get('close', 0))
                trs.append(max(h - l, abs(h - pc), abs(l - pc)))
        return trs

    def _calc_atr_wilder(self, klines: List[Dict[str, Any]], period: int = 14) -> float:
        """Wilder ATR：首 ATR = 前 period 期 TR 简单平均，之后 ATR_t = (ATR_{t-1}*(period-1)+TR_t)/period。"""
        trs = self._true_ranges(klines)
        if len(trs) < period:
            return 0.0
        atr = sum(trs[:period]) / period
        for i in range(period, len(trs)):
            atr = (atr * (period - 1) + trs[i]) / period
        return atr
    
    def _calc_bollinger(self, closes: List[float], period: int = 20, std_dev: int = 2) -> Dict[str, float]:
        """布林带：中轨为 period 收盘 SMA，σ 为总体标准差（方差/period），上下轨=中轨±std_dev×σ。"""
        if len(closes) < period:
            return {}
        
        recent = closes[-period:]
        middle = sum(recent) / period
        
        variance = sum((x - middle) ** 2 for x in recent) / period
        std = variance ** 0.5
        
        return {
            'BB_upper': round(middle + std_dev * std, 4),
            'BB_middle': round(middle, 4),
            'BB_lower': round(middle - std_dev * std, 4),
            'BB_width': round((std_dev * std * 2) / middle * 100, 2) if middle > 0 else 0
        }
    
    
    def _get_fundamental(self, market: str, symbol: str) -> Optional[Dict[str, Any]]:
        """获取基本面数据"""
        try:
            if market == 'USStock':
                return self._get_us_fundamental(symbol)
            if market in ('CNStock', 'HKStock'):
                return self._get_cn_hk_fundamental(market, symbol)
        except Exception as e:
            logger.warning(f"Fundamental data fetch failed for {market}:{symbol}: {e}")
        return None

    def _get_cn_hk_fundamental(self, market: str, symbol: str) -> Optional[Dict[str, Any]]:
        """
        CN/HK fundamentals — multi-tier:
          Tier 1: Twelve Data /statistics (globally stable, paid)
          Tier 2: AkShare / Eastmoney (fragile overseas)
          Tier 3: AkShare financial statements (revenue growth, debt, FCF)
          + Tencent quote for live price fields
        """
        try:
            from app.data_sources.tencent import (
                normalize_cn_code,
                normalize_hk_code,
                fetch_quote,
                parse_quote_to_ticker,
            )
            from app.data_sources.cn_hk_fundamentals import (
                fetch_twelvedata_fundamental,
                fetch_twelvedata_statements,
                fetch_twelvedata_earnings,
                fetch_cn_fundamental_akshare,
                fetch_hk_fundamental_akshare,
                fetch_cn_financial_indicators,
                fetch_cn_financial_statements,
                fetch_hk_financial_indicators,
                fetch_hk_financial_statements,
            )

            code = normalize_cn_code(symbol) if market == 'CNStock' else normalize_hk_code(symbol)
            is_hk = market == 'HKStock'

            parts = fetch_quote(code)
            t = parse_quote_to_ticker(parts) if parts else {}
            result: Dict[str, Any] = {
                "pe_ratio": None,
                "pb_ratio": None,
                "ps_ratio": None,
                "market_cap": None,
                "dividend_yield": None,
                "beta": None,
                "52w_high": None,
                "52w_low": None,
                "roe": None,
                "eps": None,
                "revenue_growth": None,
                "profit_margin": None,
                "debt_to_equity": None,
                "current_ratio": None,
                "free_cash_flow": None,
                "last": t.get("last"),
                "previous_close": t.get("previousClose"),
                "change_percent": t.get("changePercent"),
                "source": "tencent_quote",
            }

            # Tier 1: Twelve Data
            td = {}
            try:
                td = fetch_twelvedata_fundamental(code, is_hk)
            except Exception as e:
                logger.debug("TwelveData fundamental failed %s:%s: %s", market, symbol, e)

            if td:
                result["source"] = "tencent_quote+twelvedata"
                for k, v in td.items():
                    if k == "source":
                        continue
                    if v is not None:
                        result[k] = v

            # Tier 2: AkShare valuation (fill any remaining None fields)
            has_valuation = result.get("pe_ratio") is not None or result.get("pb_ratio") is not None
            if not has_valuation:
                try:
                    ak_data = fetch_cn_fundamental_akshare(code) if not is_hk else fetch_hk_fundamental_akshare(code)
                except Exception as e:
                    logger.debug("AkShare CN/HK fundamental failed %s:%s: %s", market, symbol, e)
                    ak_data = {}
                if ak_data:
                    if "twelvedata" not in result.get("source", ""):
                        result["source"] = "tencent_quote+akshare_em"
                    else:
                        result["source"] += "+akshare_em"
                    for k, v in ak_data.items():
                        if k == "source":
                            continue
                        if v is not None and result.get(k) is None:
                            result[k] = v

            # Tier 3: Twelve Data financial statements (globally stable, priority for overseas)
            _growth_keys = ("revenue_growth", "debt_to_equity", "current_ratio", "free_cash_flow")
            needs_financials = any(result.get(k) is None for k in _growth_keys)
            has_statements = "financial_statements" in result
            if needs_financials or not has_statements:
                try:
                    td_stmts = fetch_twelvedata_statements(code, is_hk)
                except Exception as e:
                    logger.debug("TwelveData statements failed %s:%s: %s", market, symbol, e)
                    td_stmts = {}
                if td_stmts:
                    stmts_obj = td_stmts.pop("financial_statements", None)
                    if stmts_obj and not has_statements:
                        result["financial_statements"] = stmts_obj
                        result["source"] += "+twelvedata_stmts"
                    for k, v in td_stmts.items():
                        if v is not None and result.get(k) is None:
                            result[k] = v
                    filled_td = sum(1 for k in _growth_keys if result.get(k) is not None)
                    logger.info("TwelveData statements for %s:%s: %d/%d growth keys filled",
                                market, symbol, filled_td, len(_growth_keys))

            # Tier 4: AkShare financial indicators (fallback for domestic servers)
            needs_financials = any(result.get(k) is None for k in _growth_keys)
            if needs_financials:
                try:
                    if is_hk:
                        fin_data = fetch_hk_financial_indicators(code)
                    else:
                        fin_data = fetch_cn_financial_indicators(code)
                except Exception as e:
                    logger.debug("AkShare CN/HK financial indicators failed %s:%s: %s", market, symbol, e)
                    fin_data = {}
                if fin_data:
                    result["source"] += "+akshare_financials"
                    for k, v in fin_data.items():
                        if v is not None and result.get(k) is None:
                            result[k] = v
                    filled = sum(1 for k in _growth_keys if result.get(k) is not None)
                    logger.info("CN/HK AkShare financial indicators for %s:%s: %d/%d growth keys filled",
                                market, symbol, filled, len(_growth_keys))

            # Tier 5: Structured financial statements via AkShare (if Twelve Data didn't fill)
            if "financial_statements" not in result:
                try:
                    if is_hk:
                        stmts = fetch_hk_financial_statements(code)
                    else:
                        stmts = fetch_cn_financial_statements(code)
                    if stmts:
                        result["financial_statements"] = stmts
                        result["source"] += "+akshare_stmts"
                        logger.debug("CN/HK financial statements (AkShare) for %s: %s", symbol, list(stmts.keys()))
                except Exception as e:
                    logger.debug("CN/HK financial statements (AkShare) failed %s: %s", symbol, e)

            # Tier 6: Earnings data (quarterly EPS history) — Twelve Data /earnings
            if "earnings" not in result:
                try:
                    td_earnings = fetch_twelvedata_earnings(code, is_hk)
                    if td_earnings:
                        result["earnings"] = td_earnings
                        result["source"] += "+twelvedata_earnings"
                except Exception as e:
                    logger.debug("TwelveData earnings failed %s:%s: %s", market, symbol, e)

            # Fallback: build earnings from financial_statements if /earnings failed
            if "earnings" not in result and "financial_statements" in result:
                result["earnings"] = self._build_earnings_from_statements(result["financial_statements"])

            if not parts and not td and not has_valuation:
                return None
            return result
        except Exception as e:
            logger.debug(f"CN/HK fundamental failed: {market}:{symbol}: {e}")
            return None

    @staticmethod
    def _build_earnings_from_statements(stmts: Dict[str, Any]) -> Dict[str, Any]:
        """Construct an 'earnings' dict from structured financial_statements for CN/HK."""
        earnings: Dict[str, Any] = {}

        inc = stmts.get("income_statement") or {}
        latest_date = inc.get("latest_date")
        revenue = inc.get("total_revenue")
        net_income = inc.get("net_income")
        eps = inc.get("eps_diluted")

        if latest_date or revenue or net_income:
            earnings["quarterly"] = {
                "latest_quarter": latest_date,
                "revenue": revenue,
                "earnings": net_income,
            }
            earnings["history"] = [{
                "date": latest_date or "N/A",
                "eps_actual": eps,
                "eps_estimate": None,
                "surprise": None,
            }]

        cf = stmts.get("cash_flow") or {}
        bs = stmts.get("balance_sheet") or {}
        if cf or bs:
            summary_parts = []
            if cf.get("operating_cash_flow") is not None:
                summary_parts.append(f"Operating CF: {cf['operating_cash_flow']:,.0f}")
            if cf.get("free_cash_flow") is not None:
                summary_parts.append(f"FCF: {cf['free_cash_flow']:,.0f}")
            if bs.get("total_assets") is not None:
                summary_parts.append(f"Total Assets: {bs['total_assets']:,.0f}")
            if summary_parts:
                earnings["financial_summary"] = "; ".join(summary_parts)

        return earnings if earnings else {}

    def _get_us_fundamental(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        美股基本面 - Finnhub + yfinance
        包括：基础财务指标 + 财报数据（资产负债表、利润表、现金流量表）
        """
        result = {}
        
        if self._finnhub_client:
            try:
                metrics = self._finnhub_client.company_basic_financials(symbol, 'all')
                if metrics and metrics.get('metric'):
                    m = metrics['metric']
                    result.update({
                        'pe_ratio': m.get('peBasicExclExtraTTM'),
                        'pb_ratio': m.get('pbQuarterly'),
                        'ps_ratio': m.get('psTTM'),
                        'market_cap': m.get('marketCapitalization'),
                        'dividend_yield': m.get('dividendYieldIndicatedAnnual'),
                        'beta': m.get('beta'),
                        '52w_high': m.get('52WeekHigh'),
                        '52w_low': m.get('52WeekLow'),
                        'roe': m.get('roeTTM'),
                        'eps': m.get('epsBasicExclExtraItemsTTM'),
                        'revenue_growth': m.get('revenueGrowthTTMYoy'),
                        'profit_margin': m.get('netProfitMarginTTM'),
                        'debt_to_equity': m.get('totalDebtToEquityQuarterly'),
                        'current_ratio': m.get('currentRatioQuarterly'),
                        'quick_ratio': m.get('quickRatioQuarterly'),
                    })
            except Exception as e:
                logger.debug(f"Finnhub fundamental failed for {symbol}: {e}")
        
        try:
            ticker = yf.Ticker(symbol)
            info = ticker.info or {}
            
            if not result.get('pe_ratio'):
                result['pe_ratio'] = info.get('trailingPE') or info.get('forwardPE')
            if not result.get('pb_ratio'):
                result['pb_ratio'] = info.get('priceToBook')
            if not result.get('market_cap'):
                result['market_cap'] = info.get('marketCap')
            if not result.get('dividend_yield'):
                result['dividend_yield'] = info.get('dividendYield')
            if not result.get('beta'):
                result['beta'] = info.get('beta')
            if not result.get('52w_high'):
                result['52w_high'] = info.get('fiftyTwoWeekHigh')
            if not result.get('52w_low'):
                result['52w_low'] = info.get('fiftyTwoWeekLow')
            if not result.get('roe'):
                result['roe'] = info.get('returnOnEquity')
            if not result.get('eps'):
                result['eps'] = info.get('trailingEps')
            
            result.update({
                'revenue': info.get('totalRevenue'),
                'gross_profit': info.get('grossProfits'),
                'operating_margin': info.get('operatingMargins'),
                'profit_margin': result.get('profit_margin') or info.get('profitMargins'),
                'ebitda': info.get('ebitda'),
                'debt': info.get('totalDebt'),
                'cash': info.get('totalCash'),
                'free_cash_flow': info.get('freeCashflow'),
                'operating_cash_flow': info.get('operatingCashflow'),
                'book_value': info.get('bookValue'),
                'enterprise_value': info.get('enterpriseValue'),
            })
        except Exception as e:
            logger.debug(f"yfinance fundamental failed for {symbol}: {e}")
        
        financial_statements = self._get_financial_statements(symbol)
        if financial_statements:
            result['financial_statements'] = financial_statements
        
        earnings_data = self._get_earnings_data(symbol)
        if earnings_data:
            result['earnings'] = earnings_data
        
        return result if result else None
    
    def _get_financial_statements(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        获取财务报表数据（资产负债表、利润表、现金流量表）
        
        使用 yfinance 获取，包含最近几个季度的数据
        """
        try:
            ticker = yf.Ticker(symbol)
            statements = {}
            
            try:
                balance_sheet = ticker.balance_sheet
                if balance_sheet is not None and not balance_sheet.empty:
                    latest_quarters = balance_sheet.columns[:4] if len(balance_sheet.columns) >= 4 else balance_sheet.columns
                    statements['balance_sheet'] = {
                        'latest_date': str(latest_quarters[0]) if len(latest_quarters) > 0 else None,
                        'total_assets': float(balance_sheet.loc['Total Assets', latest_quarters[0]]) if 'Total Assets' in balance_sheet.index and len(latest_quarters) > 0 else None,
                        'total_liabilities': float(balance_sheet.loc['Total Liab', latest_quarters[0]]) if 'Total Liab' in balance_sheet.index and len(latest_quarters) > 0 else None,
                        'total_equity': float(balance_sheet.loc['Stockholders Equity', latest_quarters[0]]) if 'Stockholders Equity' in balance_sheet.index and len(latest_quarters) > 0 else None,
                        'cash': float(balance_sheet.loc['Cash', latest_quarters[0]]) if 'Cash' in balance_sheet.index and len(latest_quarters) > 0 else None,
                        'debt': float(balance_sheet.loc['Total Debt', latest_quarters[0]]) if 'Total Debt' in balance_sheet.index and len(latest_quarters) > 0 else None,
                        'current_assets': float(balance_sheet.loc['Current Assets', latest_quarters[0]]) if 'Current Assets' in balance_sheet.index and len(latest_quarters) > 0 else None,
                        'current_liabilities': float(balance_sheet.loc['Current Liabilities', latest_quarters[0]]) if 'Current Liabilities' in balance_sheet.index and len(latest_quarters) > 0 else None,
                    }
            except Exception as e:
                logger.debug(f"Balance sheet fetch failed for {symbol}: {e}")
            
            try:
                income_stmt = ticker.financials
                if income_stmt is not None and not income_stmt.empty:
                    latest_quarters = income_stmt.columns[:4] if len(income_stmt.columns) >= 4 else income_stmt.columns
                    statements['income_statement'] = {
                        'latest_date': str(latest_quarters[0]) if len(latest_quarters) > 0 else None,
                        'total_revenue': float(income_stmt.loc['Total Revenue', latest_quarters[0]]) if 'Total Revenue' in income_stmt.index and len(latest_quarters) > 0 else None,
                        'gross_profit': float(income_stmt.loc['Gross Profit', latest_quarters[0]]) if 'Gross Profit' in income_stmt.index and len(latest_quarters) > 0 else None,
                        'operating_income': float(income_stmt.loc['Operating Income', latest_quarters[0]]) if 'Operating Income' in income_stmt.index and len(latest_quarters) > 0 else None,
                        'net_income': float(income_stmt.loc['Net Income', latest_quarters[0]]) if 'Net Income' in income_stmt.index and len(latest_quarters) > 0 else None,
                        'eps': float(income_stmt.loc['Basic EPS', latest_quarters[0]]) if 'Basic EPS' in income_stmt.index and len(latest_quarters) > 0 else None,
                    }
            except Exception as e:
                logger.debug(f"Income statement fetch failed for {symbol}: {e}")
            
            try:
                cashflow = ticker.cashflow
                if cashflow is not None and not cashflow.empty:
                    latest_quarters = cashflow.columns[:4] if len(cashflow.columns) >= 4 else cashflow.columns
                    statements['cash_flow'] = {
                        'latest_date': str(latest_quarters[0]) if len(latest_quarters) > 0 else None,
                        'operating_cash_flow': float(cashflow.loc['Operating Cash Flow', latest_quarters[0]]) if 'Operating Cash Flow' in cashflow.index and len(latest_quarters) > 0 else None,
                        'investing_cash_flow': float(cashflow.loc['Capital Expenditure', latest_quarters[0]]) if 'Capital Expenditure' in cashflow.index and len(latest_quarters) > 0 else None,
                        'financing_cash_flow': float(cashflow.loc['Financing Cash Flow', latest_quarters[0]]) if 'Financing Cash Flow' in cashflow.index and len(latest_quarters) > 0 else None,
                        'free_cash_flow': float(cashflow.loc['Free Cash Flow', latest_quarters[0]]) if 'Free Cash Flow' in cashflow.index and len(latest_quarters) > 0 else None,
                    }
            except Exception as e:
                logger.debug(f"Cash flow statement fetch failed for {symbol}: {e}")
            
            return statements if statements else None
            
        except Exception as e:
            logger.debug(f"Financial statements fetch failed for {symbol}: {e}")
            return None
    
    def _get_earnings_data(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        获取盈利报告数据（Earnings）

        使用 quarterly_income_stmt 替代已弃用的 Ticker.earnings / quarterly_earnings，
        历史季度摘要从利润表推导；盈利日历仍用 ticker.calendar（若可用）。
        """
        def _pick_float(stmt: pd.DataFrame, row_names: tuple, col) -> Optional[float]:
            for name in row_names:
                if name in stmt.index:
                    raw = stmt.loc[name, col]
                    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
                        continue
                    try:
                        return float(raw)
                    except (TypeError, ValueError):
                        continue
            return None

        try:
            ticker = yf.Ticker(symbol)
            earnings_data: Dict[str, Any] = {}

            try:
                q_inc = ticker.quarterly_income_stmt
                if q_inc is not None and not q_inc.empty and len(q_inc.columns) > 0:
                    cols = list(q_inc.columns)[:4]
                    latest_q = cols[0]

                    rev = _pick_float(
                        q_inc,
                        ("Total Revenue", "Revenue", "Total Revenues", "Net Sales"),
                        latest_q,
                    )
                    ni = _pick_float(
                        q_inc,
                        (
                            "Net Income",
                            "Net Income Common Stockholders",
                            "Net Income Continuous Operations",
                            "Net Income Including Noncontrolling Interests",
                        ),
                        latest_q,
                    )
                    earnings_data["quarterly"] = {
                        "latest_quarter": str(latest_q),
                        "revenue": rev,
                        "earnings": ni,
                    }

                    earnings_data["history"] = []
                    for col in cols:
                        eps = _pick_float(q_inc, ("Diluted EPS", "Basic EPS"), col)
                        earnings_data["history"].append({
                            "date": str(col),
                            "eps_actual": eps,
                            "eps_estimate": None,
                            "surprise": None,
                        })
            except Exception as e:
                logger.debug(f"Quarterly income statement (earnings) fetch failed for {symbol}: {e}")

            try:
                earnings_calendar = ticker.calendar
                if earnings_calendar is not None and not earnings_calendar.empty:
                    idx0 = earnings_calendar.index[0]
                    earnings_data["upcoming"] = {
                        "next_earnings_date": str(idx0),
                        "eps_estimate": float(earnings_calendar.loc[idx0, "Earnings Estimate"])
                        if "Earnings Estimate" in earnings_calendar.columns
                        else None,
                        "revenue_estimate": float(earnings_calendar.loc[idx0, "Revenue Estimate"])
                        if "Revenue Estimate" in earnings_calendar.columns
                        else None,
                    }
            except Exception as e:
                logger.debug(f"Earnings calendar fetch failed for {symbol}: {e}")

            return earnings_data if earnings_data else None

        except Exception as e:
            logger.debug(f"Earnings data fetch failed for {symbol}: {e}")
            return None
    
    def _get_crypto_info(self, symbol: str) -> Optional[Dict[str, Any]]:
        """加密货币信息 (固定描述为主)"""
        crypto_info = {
            'BTC': {
                'name': 'Bitcoin',
                'description': '比特币，数字黄金，市值第一的加密货币，作为价值存储和避险资产',
                'category': 'Store of Value',
            },
            'ETH': {
                'name': 'Ethereum',
                'description': '以太坊，智能合约平台，DeFi和NFT生态的基础设施',
                'category': 'Smart Contract Platform',
            },
            'BNB': {
                'name': 'Binance Coin',
                'description': '币安币，全球最大交易所的平台代币',
                'category': 'Exchange Token',
            },
            'SOL': {
                'name': 'Solana',
                'description': '高性能公链，主打高TPS和低Gas费',
                'category': 'Smart Contract Platform',
            },
            'XRP': {
                'name': 'Ripple',
                'description': '瑞波币，专注跨境支付解决方案',
                'category': 'Payment',
            },
            'DOGE': {
                'name': 'Dogecoin',
                'description': '狗狗币，Meme币代表，社区驱动',
                'category': 'Meme',
            },
        }
        
        base = symbol.split('/')[0] if '/' in symbol else symbol
        base = base.upper()
        
        if base in crypto_info:
            return crypto_info[base]
        
        return {
            'name': base,
            'description': f'{base} 是一种加密货币',
            'category': 'Unknown',
        }

    def _get_crypto_factors(self, symbol: str, price_data: Dict[str, Any], kline_data: List[Dict[str, Any]]) -> Dict[str, Any]:
        """采集加密货币专属交易大数据因子。"""
        base_symbol = self._normalize_crypto_base_symbol(symbol)
        if not base_symbol:
            return {}

        market_structure = self._get_crypto_market_structure(base_symbol, price_data, kline_data)
        derivatives = self._get_crypto_derivatives_metrics(base_symbol)
        capital_flow = self._get_crypto_capital_flow(base_symbol)

        volume_24h = market_structure.get("volume_24h")
        volume_change_24h = market_structure.get("volume_change_24h")
        funding_rate = derivatives.get("funding_rate")
        oi_change = derivatives.get("open_interest_change_24h")
        long_short_ratio = derivatives.get("long_short_ratio")
        exchange_netflow = capital_flow.get("exchange_netflow")
        stablecoin_netflow = capital_flow.get("stablecoin_netflow")

        signals = {
            "derivatives_bias": self._derive_derivatives_bias(funding_rate, oi_change, long_short_ratio),
            "flow_bias": self._derive_flow_bias(exchange_netflow, stablecoin_netflow),
            "squeeze_risk": self._derive_squeeze_risk(funding_rate, long_short_ratio, oi_change),
            "volume_state": self._derive_volume_state(volume_change_24h),
        }

        summary = self._build_crypto_factor_summary(
            volume_change_24h=volume_change_24h,
            funding_rate=funding_rate,
            open_interest_change_24h=oi_change,
            exchange_netflow=exchange_netflow,
            stablecoin_netflow=stablecoin_netflow,
            signals=signals,
        )

        return {
            "symbol": base_symbol,
            "volume_24h": volume_24h,
            "volume_change_24h": volume_change_24h,
            "funding_rate": funding_rate,
            "open_interest": derivatives.get("open_interest"),
            "open_interest_change_24h": oi_change,
            "long_short_ratio": long_short_ratio,
            "exchange_netflow": exchange_netflow,
            "stablecoin_netflow": stablecoin_netflow,
            "signals": signals,
            "summary": summary,
            "sources": {
                "market_structure": market_structure.get("source"),
                "derivatives": derivatives.get("source"),
                "capital_flow": capital_flow.get("source"),
            }
        }

    def _normalize_crypto_base_symbol(self, symbol: str) -> str:
        raw = str(symbol or "").strip().upper()
        if not raw:
            return ""
        if "/" in raw:
            raw = raw.split("/", 1)[0]
        if ":" in raw:
            raw = raw.split(":", 1)[0]
        raw = raw.replace("-USD", "").replace("-USDT", "")
        return raw

    def _cache_get(self, key: str) -> Optional[Any]:
        item = self._crypto_metric_cache.get(key)
        if not item:
            return None
        if float(item.get("expires_at") or 0) <= time.time():
            self._crypto_metric_cache.pop(key, None)
            return None
        return item.get("value")

    def _cache_set(self, key: str, value: Any, ttl_sec: int) -> Any:
        self._crypto_metric_cache[key] = {
            "value": value,
            "expires_at": time.time() + max(1, int(ttl_sec or 60)),
        }
        return value

    def _coinglass_get(self, path: str, params: Dict[str, Any], ttl_sec: int = 120) -> Optional[Dict[str, Any]]:
        api_key = (APIKeys.COINGLASS_API_KEY or "").strip()
        if not api_key:
            return None

        clean_params = {k: v for k, v in (params or {}).items() if v not in (None, "", [])}
        cache_key = f"coinglass|{path}|{tuple(sorted(clean_params.items()))}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        try:
            resp = requests.get(
                f"https://open-api-v4.coinglass.com{path}",
                params=clean_params,
                headers={"CG-API-KEY": api_key},
                timeout=8,
            )
            resp.raise_for_status()
            payload = resp.json() or {}
            return self._cache_set(cache_key, payload, ttl_sec)
        except Exception as e:
            logger.debug(f"Coinglass request failed {path}: {e}")
            return None

    def _cryptoquant_get(self, path: str, params: Dict[str, Any], ttl_sec: int = 300) -> Optional[Dict[str, Any]]:
        api_key = (APIKeys.CRYPTOQUANT_API_KEY or "").strip()
        if not api_key:
            return None

        clean_params = {k: v for k, v in (params or {}).items() if v not in (None, "", [])}
        cache_key = f"cryptoquant|{path}|{tuple(sorted(clean_params.items()))}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        try:
            resp = requests.get(
                f"https://api.cryptoquant.com{path}",
                params=clean_params,
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=8,
            )
            resp.raise_for_status()
            payload = resp.json() or {}
            return self._cache_set(cache_key, payload, ttl_sec)
        except Exception as e:
            logger.debug(f"CryptoQuant request failed {path}: {e}")
            return None

    def _extract_latest_items(self, payload: Any) -> List[Dict[str, Any]]:
        if isinstance(payload, list):
            return [x for x in payload if isinstance(x, dict)]
        if isinstance(payload, dict):
            for key in ("data", "result", "items", "list"):
                val = payload.get(key)
                if isinstance(val, list):
                    return [x for x in val if isinstance(x, dict)]
                if isinstance(val, dict):
                    nested = self._extract_latest_items(val)
                    if nested:
                        return nested
        return []

    def _pick_latest_item(self, payload: Any) -> Dict[str, Any]:
        items = self._extract_latest_items(payload)
        if items:
            return items[-1]
        if isinstance(payload, dict):
            return payload
        return {}

    def _safe_num(self, value: Any, default: Optional[float] = None) -> Optional[float]:
        if value is None or value == "":
            return default
        try:
            return float(str(value).replace(",", ""))
        except Exception:
            return default

    def _pick_number(self, payload: Any, *keys: str, default: Optional[float] = None) -> Optional[float]:
        if isinstance(payload, dict):
            for key in keys:
                if key in payload:
                    val = self._safe_num(payload.get(key), None)
                    if val is not None:
                        return val
            for val in payload.values():
                found = self._pick_number(val, *keys, default=None)
                if found is not None:
                    return found
        elif isinstance(payload, list):
            for item in payload:
                found = self._pick_number(item, *keys, default=None)
                if found is not None:
                    return found
        return default

    def _get_crypto_market_structure(self, symbol: str, price_data: Dict[str, Any], kline_data: List[Dict[str, Any]]) -> Dict[str, Any]:
        out = {
            "volume_24h": None,
            "volume_change_24h": None,
            "source": "price+kline",
        }
        try:
            quote_volume = self._safe_num(price_data.get("quoteVolume"))
            if quote_volume is not None:
                out["volume_24h"] = quote_volume
        except Exception:
            pass

        try:
            if len(kline_data) >= 2:
                latest_vol = self._safe_num(kline_data[-1].get("volume"), 0.0) or 0.0
                prev_vol = self._safe_num(kline_data[-2].get("volume"), 0.0) or 0.0
                if prev_vol > 0:
                    out["volume_change_24h"] = ((latest_vol - prev_vol) / prev_vol) * 100.0
        except Exception:
            pass

        cg_cache_key = f"coingecko|coin|{symbol}"
        cached = self._cache_get(cg_cache_key)
        coin = cached
        if coin is None:
            try:
                resp = requests.get(
                    "https://api.coingecko.com/api/v3/coins/markets",
                    params={
                        "vs_currency": "usd",
                        "symbols": symbol.lower(),
                        "price_change_percentage": "24h",
                    },
                    timeout=8,
                )
                resp.raise_for_status()
                data = resp.json() or []
                coin = data[0] if data and isinstance(data[0], dict) else {}
                self._cache_set(cg_cache_key, coin, 180)
            except Exception as e:
                logger.debug(f"CoinGecko volume fetch failed for {symbol}: {e}")
                coin = {}

        if isinstance(coin, dict):
            if out["volume_24h"] is None:
                out["volume_24h"] = self._safe_num(coin.get("total_volume"))
                if out["volume_24h"] is not None:
                    out["source"] = "coingecko"
            if out["volume_change_24h"] is None:
                market_cap = self._safe_num(coin.get("market_cap"))
                total_volume = self._safe_num(coin.get("total_volume"))
                if total_volume is not None and market_cap and market_cap > 0:
                    out["volume_change_24h"] = (total_volume / market_cap) * 100.0
                    out["source"] = "coingecko+proxy"
        return out

    def _get_crypto_derivatives_metrics(self, symbol: str) -> Dict[str, Any]:
        result = {
            "funding_rate": None,
            "open_interest": None,
            "open_interest_change_24h": None,
            "long_short_ratio": None,
            "source": "",
        }

        payload = self._coinglass_get("/api/futures/fundingRate/exchange-list", {"symbol": symbol}, ttl_sec=90)
        latest = self._pick_latest_item(payload)
        result["funding_rate"] = self._pick_number(latest or payload, "oi_weighted_funding_rate", "funding_rate", "fundingRate")
        if result["funding_rate"] is not None:
            result["source"] = "coinglass"

        payload = self._coinglass_get("/api/futures/open-interest/exchange-list", {"symbol": symbol}, ttl_sec=90)
        latest = self._pick_latest_item(payload)
        result["open_interest"] = self._pick_number(
            latest or payload,
            "open_interest_usd",
            "openInterestUsd",
            "open_interest",
            "openInterest",
        )
        result["open_interest_change_24h"] = self._pick_number(
            latest or payload,
            "open_interest_change_percent_24h",
            "openInterestCh24h",
            "openInterestChangePercent24h",
            "open_interest_change_24h",
        )
        if result["open_interest"] is not None:
            result["source"] = "coinglass"

        payload = self._coinglass_get(
            "/api/futures/global-long-short-account-ratio/history",
            {"symbol": symbol, "interval": "1d", "limit": 1},
            ttl_sec=120,
        )
        latest = self._pick_latest_item(payload)
        result["long_short_ratio"] = self._pick_number(
            latest or payload,
            "long_short_ratio",
            "longShortRatio",
            "global_account_long_short_ratio",
        )
        if result["long_short_ratio"] is not None:
            result["source"] = "coinglass"

        if result["funding_rate"] is None or result["open_interest"] is None or result["long_short_ratio"] is None:
            pair = f"{symbol}USDT"
            result = self._fill_crypto_derivatives_from_binance(pair, result)
        return result

    def _fill_crypto_derivatives_from_binance(self, pair: str, result: Dict[str, Any]) -> Dict[str, Any]:
        cache_key = f"binance_derivatives|{pair}"
        cached = self._cache_get(cache_key)
        if isinstance(cached, dict):
            merged = dict(result)
            for k, v in cached.items():
                if merged.get(k) is None and v is not None:
                    merged[k] = v
            if any(merged.get(k) is not None for k in ("funding_rate", "open_interest", "long_short_ratio")) and not merged.get("source"):
                merged["source"] = "binance_public"
            return merged

        fallback = {}
        try:
            funding_resp = requests.get(
                "https://fapi.binance.com/fapi/v1/fundingRate",
                params={"symbol": pair, "limit": 1},
                timeout=8,
            )
            funding_resp.raise_for_status()
            items = funding_resp.json() or []
            if items:
                fallback["funding_rate"] = self._safe_num(items[-1].get("fundingRate"))
        except Exception as e:
            logger.debug(f"Binance funding fallback failed for {pair}: {e}")

        try:
            oi_resp = requests.get(
                "https://fapi.binance.com/futures/data/openInterestHist",
                params={"symbol": pair, "period": "1d", "limit": 2},
                timeout=8,
            )
            oi_resp.raise_for_status()
            items = oi_resp.json() or []
            if items:
                latest = items[-1]
                fallback["open_interest"] = self._safe_num(latest.get("sumOpenInterestValue"))
                if len(items) >= 2:
                    prev = self._safe_num(items[-2].get("sumOpenInterestValue"), 0.0) or 0.0
                    curr = self._safe_num(latest.get("sumOpenInterestValue"), 0.0) or 0.0
                    if prev > 0:
                        fallback["open_interest_change_24h"] = ((curr - prev) / prev) * 100.0
        except Exception as e:
            logger.debug(f"Binance open interest fallback failed for {pair}: {e}")

        try:
            ratio_resp = requests.get(
                "https://fapi.binance.com/futures/data/globalLongShortAccountRatio",
                params={"symbol": pair, "period": "1d", "limit": 1},
                timeout=8,
            )
            ratio_resp.raise_for_status()
            items = ratio_resp.json() or []
            if items:
                fallback["long_short_ratio"] = self._safe_num(items[-1].get("longShortRatio"))
        except Exception as e:
            logger.debug(f"Binance long/short fallback failed for {pair}: {e}")

        self._cache_set(cache_key, fallback, 120)
        merged = dict(result)
        for k, v in fallback.items():
            if merged.get(k) is None and v is not None:
                merged[k] = v
        if any(merged.get(k) is not None for k in ("funding_rate", "open_interest", "long_short_ratio")) and not merged.get("source"):
            merged["source"] = "binance_public"
        return merged

    def _get_crypto_capital_flow(self, symbol: str) -> Dict[str, Any]:
        result = {
            "exchange_netflow": None,
            "stablecoin_netflow": None,
            "source": "",
        }

        payload = self._coinglass_get("/api/futures/coin/netflow", {"symbol": symbol}, ttl_sec=180)
        latest = self._pick_latest_item(payload)
        inflow = self._pick_number(latest or payload, "inflow", "inflowUsd", "inflow_usd")
        outflow = self._pick_number(latest or payload, "outflow", "outflowUsd", "outflow_usd")
        if inflow is not None and outflow is not None:
            result["exchange_netflow"] = inflow - outflow
            result["source"] = "coinglass"
        else:
            result["exchange_netflow"] = self._pick_number(latest or payload, "netflow", "netFlow", "net_flow")
            if result["exchange_netflow"] is not None:
                result["source"] = "coinglass"

        payload = self._cryptoquant_get(
            "/v1/stablecoin/exchange-flows/netflow",
            {"exchange": "all_exchange", "symbol": "all", "window": "day", "limit": 1},
            ttl_sec=600,
        )
        latest = self._pick_latest_item(payload)
        result["stablecoin_netflow"] = self._pick_number(
            latest or payload,
            "netflow",
            "netFlow",
            "exchange_netflow_total",
            "value",
        )
        if result["stablecoin_netflow"] is not None:
            result["source"] = (result["source"] + "+cryptoquant").strip("+")

        return result

    def _derive_derivatives_bias(self, funding_rate: Optional[float], oi_change: Optional[float], long_short_ratio: Optional[float]) -> str:
        score = 0
        if funding_rate is not None:
            if funding_rate > 0:
                score += 1
            elif funding_rate < 0:
                score -= 1
        if oi_change is not None:
            if oi_change > 3:
                score += 1
            elif oi_change < -3:
                score -= 1
        if long_short_ratio is not None:
            if long_short_ratio > 1.2:
                score += 1
            elif long_short_ratio < 0.85:
                score -= 1
        if score >= 2:
            return "bullish"
        if score <= -2:
            return "bearish"
        return "neutral"

    def _derive_flow_bias(self, exchange_netflow: Optional[float], stablecoin_netflow: Optional[float]) -> str:
        score = 0
        if exchange_netflow is not None:
            if exchange_netflow < 0:
                score += 1
            elif exchange_netflow > 0:
                score -= 1
        if stablecoin_netflow is not None:
            if stablecoin_netflow > 0:
                score += 1
            elif stablecoin_netflow < 0:
                score -= 1
        if score >= 1:
            return "bullish"
        if score <= -1:
            return "bearish"
        return "neutral"

    def _derive_squeeze_risk(self, funding_rate: Optional[float], long_short_ratio: Optional[float], oi_change: Optional[float]) -> str:
        hot_long = (
            funding_rate is not None and funding_rate > 0.03 and
            long_short_ratio is not None and long_short_ratio > 1.5 and
            oi_change is not None and oi_change > 8
        )
        hot_short = (
            funding_rate is not None and funding_rate < -0.03 and
            long_short_ratio is not None and long_short_ratio < 0.75 and
            oi_change is not None and oi_change > 8
        )
        if hot_long or hot_short:
            return "high"
        if (
            (funding_rate is not None and abs(funding_rate) > 0.015) or
            (long_short_ratio is not None and (long_short_ratio > 1.3 or long_short_ratio < 0.85))
        ):
            return "medium"
        return "low"

    def _derive_volume_state(self, volume_change_24h: Optional[float]) -> str:
        if volume_change_24h is None:
            return "unknown"
        if volume_change_24h > 20:
            return "expanding"
        if volume_change_24h < -20:
            return "shrinking"
        return "stable"

    def _build_crypto_factor_summary(
        self,
        *,
        volume_change_24h: Optional[float],
        funding_rate: Optional[float],
        open_interest_change_24h: Optional[float],
        exchange_netflow: Optional[float],
        stablecoin_netflow: Optional[float],
        signals: Dict[str, Any],
    ) -> str:
        parts: List[str] = []
        if open_interest_change_24h is not None:
            parts.append(f"OI {'上升' if open_interest_change_24h >= 0 else '回落'} {abs(open_interest_change_24h):.1f}%")
        if funding_rate is not None:
            parts.append(f"资金费率{'偏正' if funding_rate >= 0 else '偏负'}")
        if exchange_netflow is not None:
            parts.append("交易所净流出" if exchange_netflow < 0 else "交易所净流入")
        if stablecoin_netflow is not None:
            parts.append("稳定币净流入增强" if stablecoin_netflow > 0 else "稳定币净流出")
        if volume_change_24h is not None:
            parts.append(f"成交活跃度{'放大' if volume_change_24h > 0 else '回落'}")
        direction = signals.get("derivatives_bias", "neutral")
        flow = signals.get("flow_bias", "neutral")
        squeeze = signals.get("squeeze_risk", "low")
        outlook = "偏多" if direction == "bullish" or flow == "bullish" else ("偏空" if direction == "bearish" or flow == "bearish" else "中性")
        risk_text = {"high": "拥挤风险高", "medium": "拥挤度抬升", "low": "拥挤风险低"}.get(squeeze, "风险未知")
        base = "、".join(parts[:4]) if parts else "链上与衍生品数据有限"
        return f"{base}，整体{outlook}，{risk_text}"
    
    def _get_company(self, market: str, symbol: str) -> Optional[Dict[str, Any]]:
        """获取公司信息"""
        try:
            if market == 'USStock' and self._finnhub_client:
                profile = self._finnhub_client.company_profile2(symbol=symbol)
                if profile:
                    return {
                        'name': profile.get('name'),
                        'industry': profile.get('finnhubIndustry'),
                        'country': profile.get('country'),
                        'exchange': profile.get('exchange'),
                        'ipo_date': profile.get('ipo'),
                        'market_cap': profile.get('marketCapitalization'),
                        'website': profile.get('weburl'),
                    }
            if market in ('CNStock', 'HKStock'):
                return self._get_cn_hk_company(market, symbol)
            
        except Exception as e:
            logger.debug(f"Company info fetch failed for {market}:{symbol}: {e}")
        
        return None

    def _get_cn_hk_company(self, market: str, symbol: str) -> Optional[Dict[str, Any]]:
        """
        CN/HK company info — multi-tier:
          Tier 1: Twelve Data /profile (globally stable)
          Tier 2: AkShare / Eastmoney (fragile overseas)
          + Tencent quote for Chinese name
        """
        try:
            from app.data_sources.tencent import (
                normalize_cn_code,
                normalize_hk_code,
                fetch_quote,
            )
            from app.data_sources.cn_hk_fundamentals import (
                fetch_twelvedata_profile,
                fetch_cn_company_extras,
                fetch_hk_company_extras,
            )

            code = normalize_cn_code(symbol) if market == 'CNStock' else normalize_hk_code(symbol)
            is_hk = market == 'HKStock'

            parts = fetch_quote(code)
            cn_name = ""
            if parts:
                cn_name = (parts[1] or "").strip() if len(parts) > 1 else ""

            row: Dict[str, Any] = {
                "name": cn_name or code,
                "country": "CN" if market == "CNStock" else "HK",
                "exchange": "SSE/SZSE" if market == "CNStock" else "HKEX",
                "symbol": code,
                "source": "tencent_quote",
            }

            # Tier 1: Twelve Data /profile
            td_profile = {}
            try:
                td_profile = fetch_twelvedata_profile(code, is_hk)
            except Exception as e:
                logger.debug("TwelveData profile failed %s:%s: %s", market, symbol, e)

            if td_profile:
                row["source"] = "tencent_quote+twelvedata"
                for k in ("industry", "sector", "website", "description", "employees", "full_name"):
                    v = td_profile.get(k)
                    if v is not None:
                        row[k] = v
                if not cn_name and td_profile.get("name"):
                    row["name"] = td_profile["name"]

            # Tier 2: AkShare (fill remaining gaps)
            if not row.get("industry"):
                try:
                    ex = fetch_cn_company_extras(code) if not is_hk else fetch_hk_company_extras(code)
                except Exception:
                    ex = {}
                if ex:
                    if "twelvedata" not in row.get("source", ""):
                        row["source"] = "tencent_quote+akshare_em"
                    else:
                        row["source"] += "+akshare_em"
                    for k in ("industry", "ipo_date", "website", "full_name"):
                        if ex.get(k) and not row.get(k):
                            row[k] = ex[k]

            if not parts and not td_profile and not row.get("industry"):
                return None
            return row
        except Exception:
            return None
    
    
    def _get_macro_data(self, market: str, timeout: int = 10) -> Dict[str, Any]:
        """
        获取宏观经济数据 - 复用 global_market.py 的函数和缓存
        
        优势：
        1. 数据与全球金融页面一致
        2. 复用30秒/5分钟缓存，降低API调用
        3. 已有完整的数据解读和级别判断
        """
        try:
            from app.data_providers import get_cached as _get_cached, set_cached as _set_cached
            from app.data_providers.sentiment import (
                fetch_vix as _fetch_vix,
                fetch_dollar_index as _fetch_dollar_index,
                fetch_yield_curve as _fetch_yield_curve,
                fetch_fear_greed_index as _fetch_fear_greed_index,
            )
            
            result = {}
            
            MACRO_CACHE_TTL = 21600  # 6 hours
            cached_sentiment = _get_cached("market_sentiment", MACRO_CACHE_TTL)
            if cached_sentiment:
                logger.info("Using cached sentiment data from global_market (6h cache)")
                if cached_sentiment.get('vix'):
                    vix = cached_sentiment['vix']
                    result['VIX'] = {
                        'name': 'VIX恐慌指数',
                        'description': vix.get('interpretation', ''),
                        'price': vix.get('value', 0),
                        'change': vix.get('change', 0),
                        'changePercent': vix.get('change', 0),
                        'level': vix.get('level', 'unknown'),
                    }
                
                if cached_sentiment.get('dxy'):
                    dxy = cached_sentiment['dxy']
                    result['DXY'] = {
                        'name': '美元指数',
                        'description': dxy.get('interpretation', ''),
                        'price': dxy.get('value', 0),
                        'change': dxy.get('change', 0),
                        'changePercent': dxy.get('change', 0),
                        'level': dxy.get('level', 'unknown'),
                    }
                
                if cached_sentiment.get('yield_curve'):
                    yc = cached_sentiment['yield_curve']
                    result['TNX'] = {
                        'name': '美债10年收益率',
                        'description': yc.get('interpretation', ''),
                        'price': yc.get('yield_10y', 0),
                        'change': yc.get('change', 0),
                        'changePercent': 0,
                        'spread': yc.get('spread', 0),
                        'level': yc.get('level', 'unknown'),
                    }
                
                if cached_sentiment.get('fear_greed'):
                    fg = cached_sentiment['fear_greed']
                    result['FEAR_GREED'] = {
                        'name': '恐惧贪婪指数',
                        'description': fg.get('classification', 'Neutral'),
                        'price': fg.get('value', 50),
                        'change': 0,
                        'changePercent': 0,
                    }
                
                if result:
                    return result
            
            logger.info("Fetching macro data from global_market functions")
            
            with NonBlockingThreadPoolExecutor(max_workers=4) as executor:
                futures = {
                    executor.submit(_fetch_vix): "VIX",
                    executor.submit(_fetch_dollar_index): "DXY",
                    executor.submit(_fetch_yield_curve): "TNX",
                    executor.submit(_fetch_fear_greed_index): "FEAR_GREED",
                }
                
                try:
                    for future in as_completed(futures, timeout=timeout):
                        key = futures[future]
                        try:
                            data = future.result(timeout=5)
                            if data:
                                if key == 'VIX':
                                    result[key] = {
                                        'name': 'VIX恐慌指数',
                                        'description': data.get('interpretation', ''),
                                        'price': data.get('value', 0),
                                        'change': data.get('change', 0),
                                        'changePercent': data.get('change', 0),
                                        'level': data.get('level', 'unknown'),
                                    }
                                elif key == 'DXY':
                                    result[key] = {
                                        'name': '美元指数',
                                        'description': data.get('interpretation', ''),
                                        'price': data.get('value', 0),
                                        'change': data.get('change', 0),
                                        'changePercent': data.get('change', 0),
                                        'level': data.get('level', 'unknown'),
                                    }
                                elif key == 'TNX':
                                    result[key] = {
                                        'name': '美债10年收益率',
                                        'description': data.get('interpretation', ''),
                                        'price': data.get('yield_10y', 0),
                                        'change': data.get('change', 0),
                                        'changePercent': 0,
                                        'spread': data.get('spread', 0),
                                        'level': data.get('level', 'unknown'),
                                    }
                                elif key == 'FEAR_GREED':
                                    result[key] = {
                                        'name': '恐惧贪婪指数',
                                        'description': data.get('classification', 'Neutral'),
                                        'price': data.get('value', 50),
                                        'change': 0,
                                        'changePercent': 0,
                                    }
                        except Exception as e:
                            logger.debug(f"Macro indicator {key} fetch failed: {e}")
                except TimeoutError:
                    logger.warning("Macro data fetch timed out")
            
            pass
            
            return result
            
        except ImportError as e:
            logger.warning(f"Could not import from global_market: {e}")
            return {}
        except Exception as e:
            logger.error(f"_get_macro_data failed: {e}")
            return {}
    
    
    def _get_news(
        self, market: str, symbol: str, company_name: str = None, timeout: int = 8
    ) -> Dict[str, Any]:
        """
        获取新闻和情绪数据
        
        策略（按优先级）：
        1. 结构化API (Finnhub) - 美股首选
        2. 搜索引擎 (Tavily/Google/Bing/SerpAPI) - 补充搜索
        3. 情绪分析 - Finnhub 社交媒体情绪
        """
        news_list = []
        sentiment = {}
        
        if self._finnhub_client:
            try:
                end_date = datetime.now().strftime('%Y-%m-%d')
                start_date = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
                
                raw_news = []
                
                if market == 'USStock':
                    raw_news = self._finnhub_client.company_news(symbol, _from=start_date, to=end_date)
                elif market == 'Crypto':
                    raw_news = self._finnhub_client.general_news('crypto', min_id=0)
                else:
                    raw_news = self._finnhub_client.general_news('general', min_id=0)
                
                if raw_news:
                    for item in raw_news[:10]:
                        if not item.get('headline'):
                            continue
                        news_list.append({
                            "datetime": datetime.fromtimestamp(item.get('datetime', 0)).strftime('%Y-%m-%d %H:%M'),
                            "headline": item.get('headline', ''),
                            "summary": item.get('summary', '')[:300] if item.get('summary') else '',
                            "source": item.get('source', 'Finnhub'),
                            "url": item.get('url', ''),
                            "sentiment": item.get('sentiment', 'neutral'),
                        })
                    logger.info(f"Finnhub 新闻获取成功: {len(news_list)} 条")
            except Exception as e:
                logger.debug(f"Finnhub news fetch failed: {e}")
        
        if self._finnhub_client and market == 'USStock' and not FinnhubConfig.FREE_ONLY:
            try:
                social = self._finnhub_client.stock_social_sentiment(symbol)
                if social:
                    sentiment['reddit'] = social.get('reddit', {})
                    sentiment['twitter'] = social.get('twitter', {})
            except Exception as e:
                logger.debug(f"Finnhub sentiment fetch failed: {e}")
        elif self._finnhub_client and market == 'USStock':
            sentiment['finnhub_social_skipped'] = {
                'reason': 'FINNHUB_FREE_ONLY=true',
                'message': 'Finnhub social sentiment is skipped in free-only mode.',
            }
        
        if len(news_list) < 5:
            search_news = self._get_news_from_search(market, symbol, company_name)
            news_list.extend(search_news)
        
        global_events = self._get_global_major_events()
        if global_events:
            news_list.extend(global_events)
            logger.info(f"Added {len(global_events)} global major events to news list")
        
        seen_titles = set()
        unique_news = []
        for item in news_list:
            title = item.get('headline', '')
            if title and title not in seen_titles:
                seen_titles.add(title)
                unique_news.append(item)
        
        unique_news.sort(key=lambda x: x.get('datetime', ''), reverse=True)
        
        return {
            "news": unique_news[:15],  # 最多15条
            "sentiment": sentiment,
        }
    
    def _get_news_from_search(
        self, market: str, symbol: str, company_name: str = None
    ) -> List[Dict[str, Any]]:
        """
        从搜索引擎获取新闻
        
        使用增强的搜索服务 (Tavily/Google/Bing/SerpAPI)
        """
        news_list = []
        
        try:
            from app.services.search import get_search_service
            search_service = get_search_service()
            
            if not search_service.is_available:
                return news_list
            
            search_name = company_name or symbol
            
            response = search_service.search_stock_news(
                stock_code=symbol,
                stock_name=search_name,
                market=market,
                max_results=5
            )
            
            if response.success and response.results:
                for result in response.results:
                    news_list.append({
                        "datetime": result.published_date or datetime.now().strftime('%Y-%m-%d'),
                        "headline": result.title,
                        "summary": result.snippet[:200] if result.snippet else '',
                        "source": f"搜索:{result.source}",
                        "url": result.url,
                        "sentiment": result.sentiment,
                    })
                logger.info(f"搜索引擎新闻补充: {len(news_list)} 条 (来源: {response.provider})")
        except Exception as e:
            logger.debug(f"搜索引擎新闻获取失败: {e}")
        
        return news_list
    
    def _get_global_major_events(self) -> List[Dict]:
        """
        获取全球重大事件新闻（地缘政治、战争、重大政策等）
        这些事件会影响所有市场，特别是加密货币
        
        Returns:
            全球重大事件新闻列表
        """
        news_list = []
        
        try:
            from app.services.search import get_search_service
            search_service = get_search_service()
            
            if not search_service.is_available:
                return news_list
            
            global_event_queries = [
                "war conflict breaking news today"  # 只搜索最重要的查询，减少API调用
            ]
            
            for query in global_event_queries:
                try:
                    response = search_service.search_with_fallback(
                        query=query,
                        max_results=2,
                        days=1  # 只搜索最近1天的新闻
                    )
                    
                    if response.success and response.results:
                        for result in response.results:
                            title_lower = result.title.lower()
                            snippet_lower = (result.snippet or "").lower()
                            text = f"{title_lower} {snippet_lower}"
                            
                            major_event_keywords = [
                                "war", "conflict", "military", "attack", "strike", "sanctions",
                                "geopolitical", "crisis", "tension", "iran", "israel", "russia",
                                "ukraine", "middle east", "nato", "united states",
                                "战争", "冲突", "军事", "袭击", "制裁", "地缘政治", "危机"
                            ]
                            
                            if any(keyword in text for keyword in major_event_keywords):
                                news_list.append({
                                    "datetime": result.published_date or datetime.now().strftime('%Y-%m-%d %H:%M'),
                                    "headline": result.title,
                                    "summary": result.snippet[:300] if result.snippet else '',
                                    "source": f"全球事件:{result.source}",
                                    "url": result.url,
                                    "sentiment": "negative" if any(kw in text for kw in ["war", "conflict", "attack", "战争", "冲突", "袭击"]) else "neutral",
                                    "is_global_event": True  # 标记为全球事件
                                })
                                logger.info(f"Found global major event: {result.title[:60]}")
                except Exception as e:
                    logger.debug(f"Failed to search global events with query '{query}': {e}")
                    continue
            
            seen_titles = set()
            unique_events = []
            for item in news_list:
                title = item.get('headline', '')
                if title and title not in seen_titles:
                    seen_titles.add(title)
                    unique_events.append(item)
            
            return unique_events[:5]  # 最多返回5条全球重大事件
            
        except Exception as e:
            logger.debug(f"Failed to get global major events: {e}")
            return []
    
_collector: Optional[MarketDataCollector] = None

def get_market_data_collector() -> MarketDataCollector:
    """获取市场数据采集器单例"""
    global _collector
    if _collector is None:
        _collector = MarketDataCollector()
    return _collector
