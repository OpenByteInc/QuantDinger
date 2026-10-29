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

import copy
import os
import tempfile
import threading
import time
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError

import yfinance as yf
import pandas as pd
import requests

from app.data_sources import DataSourceFactory
from app.services.kline import KlineService
from app.services.market.technical_indicators import calculate_indicators
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
        self._fundamental_cache: Dict[str, Dict[str, Any]] = {}
        self._fundamental_cache_lock = threading.RLock()
        self._configure_yfinance_cache()
        self._init_clients()

    @staticmethod
    def _configure_yfinance_cache() -> None:
        """Point yfinance at a writable cache directory inside containers.

        yfinance caches cookies and timezone metadata.  Its platform default may
        resolve to a read-only home directory for the unprivileged API user,
        which turns every analysis into a cold request and can push financial
        statements past the collection deadline.
        """
        cache_dir = os.getenv("YFINANCE_CACHE_DIR") or os.path.join(
            tempfile.gettempdir(), "quantdinger-yfinance"
        )
        try:
            os.makedirs(cache_dir, mode=0o700, exist_ok=True)
            os.environ.setdefault("XDG_CACHE_HOME", cache_dir)
            set_tz_cache_location = getattr(yf, "set_tz_cache_location", None)
            if callable(set_tz_cache_location):
                set_tz_cache_location(cache_dir)
        except Exception as exc:
            logger.debug("Unable to configure yfinance cache at %s: %s", cache_dir, exc)
    
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
            # Evidence timestamps are instants, not server-local wall clocks.
            # A timezone-less value was previously interpreted as UTC later in
            # the report pipeline, shifting Asia deployments by eight hours.
            "collected_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "price": None,
            "kline": None,
            "indicators": {},
            "fundamental": {},
            "company": {},
            "crypto_factors": {},
            "macro": {},
            "news": [],
            "sentiment": {},
            # Optional market-specific evidence. These keys stay empty when a
            # provider is unavailable; the report then exposes the exact gap.
            "hk_security_profile": {},
            "sec_filings": [],
            "hkex_announcements": [],
            "southbound_flow": {},
            "short_selling": {},
            "ccass": {},
            "ah_premium": {},
            "analyst_expectations": {},
            "options": {},
            "short_interest": {},
            "short_volume": {},
            "insider_activity": {},
            "ownership": {},
            "earnings_transcript": {},
            "forex_drivers": {},
            "futures_curve": {},
            "futures_positioning": {},
            "_meta": {
                "success_items": [],
                "failed_items": [],
                "duration_ms": 0
            }
        }
        
        with NonBlockingThreadPoolExecutor(max_workers=5 if market in {'USStock', 'HKStock'} else 4) as executor:
            core_futures = {
                executor.submit(self._get_price, market, symbol): "price",
                executor.submit(self._get_kline, market, symbol, timeframe, 60): "kline",
            }
            
            if market in ('USStock', 'CNStock', 'HKStock'):
                core_futures[executor.submit(self._get_fundamental, market, symbol)] = "fundamental"
                core_futures[executor.submit(self._get_company, market, symbol)] = "company"
                if market == 'USStock':
                    core_futures[executor.submit(self._get_us_research, symbol)] = "us_research"
                if market == 'HKStock':
                    core_futures[executor.submit(self._get_hk_research, symbol)] = "hk_research"
            elif market == 'Crypto':
                core_futures[executor.submit(self._get_crypto_info, symbol)] = "fundamental"
            elif market in {'Forex', 'Futures'}:
                core_futures[executor.submit(self._get_cross_asset_research, market, symbol)] = "cross_asset_research"
            
            completed_futures = set()

            def record_core_result(future) -> None:
                key = core_futures[future]
                completed_futures.add(future)
                try:
                    result = future.result()
                    if result:
                        if key in {"us_research", "hk_research", "cross_asset_research"}:
                            research_keys = (
                                (
                                    "sec_filings", "analyst_expectations", "options",
                                    "short_interest", "short_volume", "insider_activity", "ownership",
                                    "earnings_transcript",
                                )
                                if key == "us_research" else (
                                (
                                    "hk_security_profile", "southbound_flow",
                                    "analyst_expectations", "hk_macro",
                                )
                                if key == "hk_research" else
                                ("forex_drivers", "futures_curve", "futures_positioning")
                                )
                            )
                            for research_key in research_keys:
                                if result.get(research_key):
                                    data[research_key] = result[research_key]
                            status_key = (
                                "us_provider_status" if key == "us_research" else
                                "hk_provider_status" if key == "hk_research" else
                                "research_provider_status"
                            )
                            data["_meta"][status_key] = result.get("_provider_status") or {}
                        else:
                            data[key] = result
                        if key not in data["_meta"]["success_items"]:
                            data["_meta"]["success_items"].append(key)
                    elif key not in data["_meta"]["failed_items"]:
                        data["_meta"]["failed_items"].append(key)
                except Exception as e:
                    logger.warning(f"Core data fetch failed ({key}): {e}")
                    if key not in data["_meta"]["failed_items"]:
                        data["_meta"]["failed_items"].append(key)

            try:
                requested_timeout = float(timeout)
            except (TypeError, ValueError):
                requested_timeout = 30.0
            # Fundamental statements are slower than quotes/K-lines on a cold
            # cache. Honour the caller's deadline instead of truncating every
            # core collection to the previous hard-coded 15 seconds.
            core_timeout = max(15.0, min(45.0, requested_timeout))

            try:
                for future in as_completed(core_futures, timeout=core_timeout):
                    record_core_result(future)
            except TimeoutError:
                pending_keys = [
                    key for future, key in core_futures.items()
                    if future not in completed_futures and not future.done()
                ]
                logger.warning(
                    "Core data fetch timed out for %s:%s after %.1fs; pending=%s",
                    market,
                    symbol,
                    core_timeout,
                    pending_keys,
                )
            finally:
                # Capture futures that completed on the timeout boundary, and
                # explicitly mark truly unfinished items for quality reporting.
                for future, key in core_futures.items():
                    if future in completed_futures:
                        continue
                    if future.done():
                        record_core_result(future)
                    else:
                        future.cancel()
                        if key not in data["_meta"]["failed_items"]:
                            data["_meta"]["failed_items"].append(key)
        
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
                if data.get("hk_macro"):
                    data["macro"]["HKMA"] = data.pop("hk_macro")
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
        
        # The evidence snapshot's retrieval timestamp represents completion,
        # so provider observations collected during this run can never appear
        # to come from the future relative to the snapshot itself.
        data["collected_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        data["_meta"]["duration_ms"] = int((time.time() - start_time) * 1000)
        logger.info(f"Market data collection completed for {market}:{symbol} in {data['_meta']['duration_ms']}ms")
        logger.info(f"  Success: {data['_meta']['success_items']}")
        logger.info(f"  Failed: {data['_meta']['failed_items']}")
        
        return data

    def _get_us_research(self, symbol: str) -> Dict[str, Any]:
        """Collect free US-specific evidence behind a bounded, cached adapter."""
        try:
            from app.data_providers.us_research import collect_us_research

            return collect_us_research(symbol)
        except Exception as exc:
            logger.info("US research enrichment unavailable for %s: %s", symbol, exc)
            return {}

    def _get_hk_research(self, symbol: str) -> Dict[str, Any]:
        """Collect free HK-specific evidence behind a bounded, cached adapter."""
        try:
            from app.data_providers.hk_research import collect_hk_research

            return collect_hk_research(symbol)
        except Exception as exc:
            logger.info("HK research enrichment unavailable for %s: %s", symbol, exc)
            return {}

    def _get_cross_asset_research(self, market: str, symbol: str) -> Dict[str, Any]:
        """Collect bounded FX or futures research enrichment."""
        try:
            from app.data_providers.cross_asset_research import collect_cross_asset_research

            return collect_cross_asset_research(market, symbol)
        except Exception as exc:
            logger.info("Cross-asset research unavailable for %s:%s: %s", market, symbol, exc)
            return {}
    
    
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
        """Calculate local technical indicators for market analysis."""
        try:
            return calculate_indicators(klines)
        except Exception as e:
            logger.warning(f"Indicator calculation failed: {e}")
            return {}
    
    
    @staticmethod
    def _merge_fundamental_payloads(
        persisted: Optional[Dict[str, Any]],
        provider: Optional[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """Merge provider evidence over a persisted fallback without losing valid fields."""
        if not persisted and not provider:
            return None
        merged = copy.deepcopy(persisted or {})
        for key, value in (provider or {}).items():
            if value in (None, ""):
                continue
            if key in {"field_metadata", "data_quality", "identity"} and isinstance(value, dict):
                current = merged.get(key) if isinstance(merged.get(key), dict) else {}
                merged[key] = {**current, **copy.deepcopy(value)}
                continue
            merged[key] = copy.deepcopy(value)
        return merged

    @staticmethod
    def _fundamental_db_ttl_seconds() -> int:
        try:
            return max(300, int(os.getenv("AI_FUNDAMENTAL_DB_TTL_SEC", "86400")))
        except (TypeError, ValueError):
            return 86_400

    def _load_persisted_fundamental(self, market: str, symbol: str) -> Optional[Dict[str, Any]]:
        """Load the latest point-in-time snapshot without making provider requests."""
        from app.services.fundamental_data import get_fundamental_data_service

        return get_fundamental_data_service().latest_for_analysis(
            market=market,
            symbol=symbol,
            max_age_seconds=self._fundamental_db_ttl_seconds(),
        )

    @staticmethod
    def _persist_fundamental_payload(market: str, symbol: str, payload: Dict[str, Any]) -> None:
        """Write provider evidence back to the shared point-in-time store."""
        from app.services.fundamental_data import get_fundamental_data_service

        get_fundamental_data_service().persist_analysis_payload(
            market=market,
            symbol=symbol,
            raw=payload,
        )

    @staticmethod
    def _mark_fundamental_storage(
        payload: Dict[str, Any],
        *,
        served_from: str,
        writeback: Optional[str] = None,
        refresh_failed: bool = False,
    ) -> None:
        data_quality = payload.get("data_quality") if isinstance(payload.get("data_quality"), dict) else {}
        storage = data_quality.get("storage") if isinstance(data_quality.get("storage"), dict) else {}
        storage["served_from"] = served_from
        if writeback:
            storage["writeback"] = writeback
        if refresh_failed:
            storage["refresh_failed"] = True
        data_quality["storage"] = storage
        payload["data_quality"] = data_quality

    def _get_fundamental(self, market: str, symbol: str) -> Optional[Dict[str, Any]]:
        """Get fundamentals from memory, persisted snapshots, then providers.

        The database is the cross-process cache used by professional analysis.
        Provider calls are reserved for missing or stale snapshots and their
        results are written back with the richer statement and earnings payload.
        """
        cache = getattr(self, "_fundamental_cache", None)
        if cache is None:
            cache = {}
            self._fundamental_cache = cache
        lock = getattr(self, "_fundamental_cache_lock", None)
        if lock is None:
            lock = threading.RLock()
            self._fundamental_cache_lock = lock

        normalized_symbol = str(symbol or "").strip().upper()
        cache_key = f"{market}:{normalized_symbol}"
        now = time.time()
        with lock:
            cached = cache.get(cache_key)
            if cached and float(cached.get("expires_at") or 0) > now:
                return copy.deepcopy(cached.get("value"))

        persisted_state: Optional[Dict[str, Any]] = None
        try:
            persisted_state = self._load_persisted_fundamental(market, normalized_symbol)
        except Exception as exc:
            logger.warning(
                "Persisted fundamental load failed for %s:%s: %s",
                market,
                normalized_symbol,
                exc,
            )
        persisted = (
            persisted_state.get("payload")
            if isinstance(persisted_state, dict) and isinstance(persisted_state.get("payload"), dict)
            else None
        )
        persisted_is_complete = bool(
            persisted
            and not persisted_state.get("refresh_required", not persisted_state.get("fresh"))
            and persisted_state.get("has_provider_payload")
        )
        if persisted_is_complete:
            result = copy.deepcopy(persisted)
            self._mark_fundamental_storage(result, served_from="database")
        else:
            provider = self._fetch_fundamental_uncached(market, normalized_symbol)
            result = self._merge_fundamental_payloads(persisted, provider)
            if provider and result:
                writeback = "success"
                try:
                    self._persist_fundamental_payload(market, normalized_symbol, result)
                except Exception as exc:
                    writeback = "failed"
                    logger.warning(
                        "Fundamental writeback failed for %s:%s: %s",
                        market,
                        normalized_symbol,
                        exc,
                    )
                self._mark_fundamental_storage(
                    result,
                    served_from="provider_refresh",
                    writeback=writeback,
                )
            elif result:
                self._mark_fundamental_storage(
                    result,
                    served_from="database_fallback",
                    refresh_failed=True,
                )
        if result:
            try:
                ttl_seconds = max(60, int(os.getenv("AI_FUNDAMENTAL_CACHE_TTL_SEC", "1800")))
            except (TypeError, ValueError):
                ttl_seconds = 1800
            with lock:
                cache[cache_key] = {
                    "expires_at": now + ttl_seconds,
                    "value": copy.deepcopy(result),
                }
        return result

    def _fetch_fundamental_uncached(self, market: str, symbol: str) -> Optional[Dict[str, Any]]:
        """Fetch fundamentals from providers without consulting the cache."""
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
          - Tencent quote for current price fields
          - Twelve Data for licensed global statistics and statements when configured
          - AkShare/Eastmoney for domestic valuation and financial statements
          - Yahoo Finance to fill remaining canonical fields and rich analysis data
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

            self._enrich_cn_hk_fundamental_with_yfinance(result, code, is_hk=is_hk)

            # Fallback: build earnings from financial_statements if /earnings failed
            if "earnings" not in result and "financial_statements" in result:
                result["earnings"] = self._build_earnings_from_statements(result["financial_statements"])

            usable = any(
                value not in (None, "", {}, [])
                for key, value in result.items()
                if key != "source"
            )
            if not usable:
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

    def _enrich_cn_hk_fundamental_with_yfinance(
        self,
        result: Dict[str, Any],
        code: str,
        *,
        is_hk: bool,
    ) -> None:
        """Fill missing CN/HK metrics and rich statements through Yahoo Finance."""
        from app.data_sources.asia_stock_kline import yf_symbol_from_tencent

        yahoo_symbol = yf_symbol_from_tencent(code, is_hk)
        provider_source = "yfinance_hk" if is_hk else "yfinance_cn"
        market_label = "HK" if is_hk else "CN"
        try:
            ticker = yf.Ticker(yahoo_symbol)
            info = ticker.info or {}
        except Exception as exc:
            logger.debug("%s yfinance info failed %s: %s", market_label, yahoo_symbol, exc)
            return

        reported_symbol = str(info.get("symbol") or "").strip().upper()
        if reported_symbol and reported_symbol != yahoo_symbol.upper():
            logger.warning(
                "Rejected mismatched %s yfinance fundamentals requested=%s reported=%s",
                market_label,
                yahoo_symbol,
                reported_symbol,
            )
            return

        field_metadata = result.get("field_metadata")
        if not isinstance(field_metadata, dict):
            field_metadata = {}
            result["field_metadata"] = field_metadata
        filled = False

        def fill(key: str, value: Any, *, unit: str, period_type: str, transform=None) -> None:
            nonlocal filled
            if result.get(key) is not None or value in (None, ""):
                return
            try:
                clean = transform(value) if transform else float(value)
            except (TypeError, ValueError, OverflowError):
                return
            if pd.isna(clean):
                return
            result[key] = clean
            field_metadata[key] = {
                "source": provider_source,
                "unit": unit,
                "period_type": period_type,
            }
            filled = True

        for key, info_key, unit, period_type, transform in (
            ("market_cap", "marketCap", "currency", "current", None),
            ("pe_ratio", "trailingPE", "multiple", "ttm", None),
            ("pb_ratio", "priceToBook", "multiple", "current", None),
            ("roe", "returnOnEquity", "percent", "ttm", lambda value: float(value) * 100.0),
            ("revenue_growth", "revenueGrowth", "percent", "ttm_yoy", lambda value: float(value) * 100.0),
            ("debt_to_equity", "debtToEquity", "multiple", "latest_quarter", lambda value: float(value) / 100.0),
            ("revenue", "totalRevenue", "currency", "ttm", None),
            ("net_income", "netIncomeToCommon", "currency", "ttm", None),
            ("net_income_ttm", "netIncomeToCommon", "currency", "ttm", None),
            ("book_value", "bookValue", "currency_per_share", "latest_quarter", None),
            ("total_debt", "totalDebt", "currency", "latest_quarter", None),
            ("free_cash_flow", "freeCashflow", "currency", "ttm", None),
            ("shares_outstanding", "sharesOutstanding", "shares", "current", None),
            ("dividend_yield", "dividendYield", "percent", "annualized", lambda value: float(value) * 100.0),
            ("eps", "trailingEps", "currency_per_share", "ttm", None),
        ):
            fill(key, info.get(info_key), unit=unit, period_type=period_type, transform=transform)

        fast_info: Any = {}
        try:
            fast_info = ticker.fast_info or {}
        except Exception as exc:
            logger.debug("%s yfinance fast info failed %s: %s", market_label, yahoo_symbol, exc)
        fill(
            "shares_outstanding",
            fast_info.get("shares"),
            unit="shares",
            period_type="current",
        )
        if result.get("market_cap") is None:
            shares = result.get("shares_outstanding")
            price = (
                info.get("currentPrice")
                or info.get("regularMarketPrice")
                or result.get("last")
                or fast_info.get("last_price")
            )
            if shares is not None and price is not None:
                fill(
                    "market_cap",
                    float(shares) * float(price),
                    unit="currency",
                    period_type="current_derived",
                )

        if result.get("shareholder_equity") is None:
            book_value = result.get("book_value")
            shares = result.get("shares_outstanding")
            if book_value is not None and shares is not None:
                fill(
                    "shareholder_equity",
                    float(book_value) * float(shares),
                    unit="currency",
                    period_type="latest_quarter",
                )

        statements = self._get_financial_statements(
            yahoo_symbol,
            ticker=ticker,
            currency=info.get("financialCurrency") or info.get("currency") or ("HKD" if is_hk else "CNY"),
        )
        if statements:
            yahoo_period = ((statements.get("latest_quarter") or {}).get("period_end"))
            local_period = ((result.get("financial_statements") or {}).get("latest_quarter") or {}).get("period_end")
            if yahoo_period or not local_period:
                result["financial_statements"] = statements
            latest_quarter = statements.get("latest_quarter") or {}
            latest_income = latest_quarter.get("income_statement") or statements.get("income_statement") or {}
            latest_balance = latest_quarter.get("balance_sheet") or statements.get("balance_sheet") or {}
            ttm = statements.get("ttm") or {}
            ttm_income = ttm.get("income_statement") or {}
            cash_flow_candidates = (
                latest_quarter.get("cash_flow") or {},
                ttm.get("cash_flow") or {},
                statements.get("cash_flow") or {},
                (statements.get("latest_annual") or {}).get("cash_flow") or {},
            )
            cash_flow = next(
                (candidate for candidate in cash_flow_candidates if candidate.get("free_cash_flow") is not None),
                {},
            )
            derived = latest_quarter.get("derived") or {}
            fill("revenue", latest_income.get("total_revenue"), unit="currency", period_type="latest_quarter")
            fill("net_income", latest_income.get("net_income"), unit="currency", period_type="latest_quarter")
            fill("net_income_ttm", ttm_income.get("net_income"), unit="currency", period_type="ttm")
            fill("shareholder_equity", latest_balance.get("total_equity"), unit="currency", period_type="latest_quarter")
            fill("total_debt", latest_balance.get("debt"), unit="currency", period_type="latest_quarter")
            fill(
                "free_cash_flow",
                cash_flow.get("free_cash_flow"),
                unit="currency",
                period_type=str(cash_flow.get("period_type") or "latest_available"),
            )
            fill("revenue_growth", derived.get("revenue_growth"), unit="percent", period_type="latest_quarter_yoy")
            filled = True
        if result.get("debt_to_equity") is None:
            debt = result.get("total_debt")
            equity = result.get("shareholder_equity")
            if debt is not None and equity not in (None, 0):
                fill(
                    "debt_to_equity",
                    float(debt) / float(equity),
                    unit="multiple",
                    period_type="latest_quarter",
                )
        earnings = self._get_earnings_data(yahoo_symbol, ticker=ticker)
        if earnings:
            result["earnings"] = earnings
            filled = True

        result["identity"] = {
            "requested_symbol": yahoo_symbol,
            "reported_symbol": reported_symbol or None,
            "company_name": info.get("longName") or info.get("shortName"),
            "industry": info.get("industry"),
            "sector": info.get("sector"),
            "country": info.get("country"),
            "exchange": info.get("exchange") or info.get("fullExchangeName"),
            "quote_type": info.get("quoteType"),
            "verified": bool(reported_symbol and reported_symbol == yahoo_symbol.upper()),
        }
        if filled and provider_source not in str(result.get("source") or ""):
            result["source"] = "+".join(filter(None, (str(result.get("source") or ""), provider_source)))

    def _enrich_hk_fundamental_with_yfinance(self, result: Dict[str, Any], code: str) -> None:
        """Backward-compatible wrapper for HK-specific callers and tests."""
        self._enrich_cn_hk_fundamental_with_yfinance(result, code, is_hk=True)

    def _get_us_fundamental(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Collect US equity fundamentals with explicit units and reporting periods.

        Canonical units used by the analysis layer:
        - market cap / statement values: absolute reporting-currency amounts
        - ROE, margins, growth and dividend yield: percentage points
        - debt-to-equity and liquidity ratios: ratio multiples
        """
        result: Dict[str, Any] = {
            "source": "",
            "field_metadata": {},
        }

        def set_metric(
            key: str,
            value: Any,
            *,
            source: str,
            unit: str,
            period_type: str,
            transform=None,
            replace: bool = False,
        ) -> None:
            if value is None or value == "" or (not replace and result.get(key) is not None):
                return
            try:
                clean = transform(value) if transform else float(value)
            except (TypeError, ValueError, OverflowError):
                return
            if pd.isna(clean):
                return
            result[key] = clean
            result["field_metadata"][key] = {
                "source": source,
                "unit": unit,
                "period_type": period_type,
            }

        source_parts: List[str] = []
        if self._finnhub_client:
            try:
                metrics = self._finnhub_client.company_basic_financials(symbol, 'all')
                if metrics and metrics.get('metric'):
                    m = metrics['metric']
                    source_parts.append("finnhub")
                    set_metric('pe_ratio', m.get('peBasicExclExtraTTM'), source="finnhub", unit="multiple", period_type="ttm")
                    set_metric('pb_ratio', m.get('pbQuarterly'), source="finnhub", unit="multiple", period_type="latest_quarter")
                    set_metric('ps_ratio', m.get('psTTM'), source="finnhub", unit="multiple", period_type="ttm")
                    # Finnhub documents marketCapitalization in millions.
                    set_metric('market_cap', m.get('marketCapitalization'), source="finnhub", unit="currency", period_type="current", transform=lambda v: float(v) * 1_000_000)
                    set_metric('dividend_yield', m.get('dividendYieldIndicatedAnnual'), source="finnhub", unit="percent", period_type="annualized")
                    set_metric('beta', m.get('beta'), source="finnhub", unit="multiple", period_type="current")
                    set_metric('52w_high', m.get('52WeekHigh'), source="finnhub", unit="currency_per_share", period_type="52_week")
                    set_metric('52w_low', m.get('52WeekLow'), source="finnhub", unit="currency_per_share", period_type="52_week")
                    set_metric('roe', m.get('roeTTM'), source="finnhub", unit="percent", period_type="ttm")
                    set_metric('eps', m.get('epsBasicExclExtraItemsTTM'), source="finnhub", unit="currency_per_share", period_type="ttm")
                    set_metric('revenue_growth', m.get('revenueGrowthTTMYoy'), source="finnhub", unit="percent", period_type="ttm_yoy")
                    set_metric('profit_margin', m.get('netProfitMarginTTM'), source="finnhub", unit="percent", period_type="ttm")
                    set_metric('debt_to_equity', m.get('totalDebtToEquityQuarterly'), source="finnhub", unit="multiple", period_type="latest_quarter", transform=lambda v: float(v) / 100.0)
                    set_metric('current_ratio', m.get('currentRatioQuarterly'), source="finnhub", unit="multiple", period_type="latest_quarter")
                    set_metric('quick_ratio', m.get('quickRatioQuarterly'), source="finnhub", unit="multiple", period_type="latest_quarter")
            except Exception as e:
                logger.debug(f"Finnhub fundamental failed for {symbol}: {e}")

        ticker = None
        info: Dict[str, Any] = {}
        identity_rejected = False
        try:
            ticker = yf.Ticker(symbol)
            info = ticker.info or {}

            reported_symbol = str(info.get("symbol") or "").strip().upper()
            expected_symbol = str(symbol or "").strip().upper()
            identity_match = not reported_symbol or reported_symbol == expected_symbol
            result["identity"] = {
                "requested_symbol": expected_symbol,
                "reported_symbol": reported_symbol or None,
                "company_name": info.get("longName") or info.get("shortName"),
                "industry": info.get("industry"),
                "sector": info.get("sector"),
                "country": info.get("country"),
                "exchange": info.get("exchange") or info.get("fullExchangeName"),
                "quote_type": info.get("quoteType"),
                "verified": bool(identity_match and reported_symbol),
            }
            if not identity_match:
                logger.warning("Rejected mismatched yfinance fundamentals requested=%s reported=%s", expected_symbol, reported_symbol)
                identity_rejected = True
                ticker = None
                info = {}
            else:
                source_parts.append("yfinance")
                set_metric('pe_ratio', info.get('trailingPE') or info.get('forwardPE'), source="yfinance", unit="multiple", period_type="ttm")
                set_metric('pb_ratio', info.get('priceToBook'), source="yfinance", unit="multiple", period_type="current")
                set_metric('market_cap', info.get('marketCap'), source="yfinance", unit="currency", period_type="current")
                set_metric('dividend_yield', info.get('dividendYield'), source="yfinance", unit="percent", period_type="annualized", transform=lambda v: float(v) * 100.0)
                set_metric('beta', info.get('beta'), source="yfinance", unit="multiple", period_type="current")
                set_metric('52w_high', info.get('fiftyTwoWeekHigh'), source="yfinance", unit="currency_per_share", period_type="52_week")
                set_metric('52w_low', info.get('fiftyTwoWeekLow'), source="yfinance", unit="currency_per_share", period_type="52_week")
                set_metric('roe', info.get('returnOnEquity'), source="yfinance", unit="percent", period_type="ttm", transform=lambda v: float(v) * 100.0)
                set_metric('eps', info.get('trailingEps'), source="yfinance", unit="currency_per_share", period_type="ttm")
                set_metric('revenue_growth', info.get('revenueGrowth'), source="yfinance", unit="percent", period_type="ttm_yoy", transform=lambda v: float(v) * 100.0)
                set_metric('operating_margin', info.get('operatingMargins'), source="yfinance", unit="percent", period_type="ttm", transform=lambda v: float(v) * 100.0)
                set_metric('profit_margin', info.get('profitMargins'), source="yfinance", unit="percent", period_type="ttm", transform=lambda v: float(v) * 100.0)
                set_metric('debt_to_equity', info.get('debtToEquity'), source="yfinance", unit="multiple", period_type="latest_quarter", transform=lambda v: float(v) / 100.0)
                set_metric('current_ratio', info.get('currentRatio'), source="yfinance", unit="multiple", period_type="latest_quarter")
                set_metric('quick_ratio', info.get('quickRatio'), source="yfinance", unit="multiple", period_type="latest_quarter")
                for key, value, unit, period_type in (
                    ('revenue', info.get('totalRevenue'), 'currency', 'ttm'),
                    ('gross_profit', info.get('grossProfits'), 'currency', 'ttm'),
                    ('ebitda', info.get('ebitda'), 'currency', 'ttm'),
                    ('debt', info.get('totalDebt'), 'currency', 'latest_quarter'),
                    ('cash', info.get('totalCash'), 'currency', 'latest_quarter'),
                    ('free_cash_flow', info.get('freeCashflow'), 'currency', 'ttm'),
                    ('operating_cash_flow', info.get('operatingCashflow'), 'currency', 'ttm'),
                    ('book_value', info.get('bookValue'), 'currency_per_share', 'latest_quarter'),
                    ('enterprise_value', info.get('enterpriseValue'), 'currency', 'current'),
                    ('shares_outstanding', info.get('sharesOutstanding'), 'shares', 'current'),
                ):
                    set_metric(key, value, source="yfinance", unit=unit, period_type=period_type)
        except Exception as e:
            logger.debug(f"yfinance fundamental failed for {symbol}: {e}")

        financial_statements = None if identity_rejected else self._get_financial_statements(
            symbol,
            ticker=ticker,
            currency=info.get("financialCurrency") or info.get("currency") or "USD",
        )
        if financial_statements:
            result['financial_statements'] = financial_statements
            source_parts.append("yfinance_statements")
            latest_q = financial_statements.get("latest_quarter") or {}
            derived = latest_q.get("derived") or {}
            for key, unit in (
                ("revenue_growth", "percent"),
                ("profit_margin", "percent"),
                ("current_ratio", "multiple"),
                ("debt_to_equity", "multiple"),
                ("roe", "percent"),
            ):
                set_metric(
                    key,
                    derived.get(key),
                    source="yfinance_statements",
                    unit=unit,
                    period_type="latest_quarter",
                )

        earnings_data = None if identity_rejected else self._get_earnings_data(symbol, ticker=ticker)
        if earnings_data:
            result['earnings'] = earnings_data
        result["source"] = "+".join(dict.fromkeys(source_parts))
        result["data_quality"] = {
            "preferred_basis": "latest_reported_quarter",
            "valuation_basis": "current_or_ttm",
            "statement_source": "yfinance",
            "periods_separated": bool(financial_statements),
            "identity_verified": bool((result.get("identity") or {}).get("verified")),
        }
        usable_keys = [key for key in result if key not in {"source", "field_metadata", "data_quality", "identity"}]
        return result if usable_keys else None
    
    def _get_financial_statements(
        self,
        symbol: str,
        *,
        ticker=None,
        currency: str = "USD",
    ) -> Optional[Dict[str, Any]]:
        """
        获取财务报表数据（资产负债表、利润表、现金流量表）
        
        使用 yfinance 获取，明确区分最新季报、TTM 与最新年报。
        """
        def pick(frame: pd.DataFrame, names: tuple, column) -> Optional[float]:
            if frame is None or frame.empty or column is None:
                return None
            for name in names:
                if name not in frame.index:
                    continue
                try:
                    value = float(frame.loc[name, column])
                except (TypeError, ValueError):
                    continue
                if not pd.isna(value):
                    return value
            return None

        def columns(frame: pd.DataFrame) -> List[Any]:
            if frame is None or frame.empty:
                return []
            return sorted(list(frame.columns), key=lambda value: pd.Timestamp(value), reverse=True)

        def statement(frame: pd.DataFrame, field_map: Dict[str, tuple], period_type: str) -> Dict[str, Any]:
            cols = columns(frame)
            if not cols:
                return {}
            col = cols[0]
            payload: Dict[str, Any] = {
                "latest_date": str(pd.Timestamp(col).date()),
                "period_end": str(pd.Timestamp(col).date()),
                "period_type": period_type,
                "currency": currency,
                "source": "yfinance",
            }
            for field, names in field_map.items():
                payload[field] = pick(frame, names, col)
            return payload

        def sum_latest(frame: pd.DataFrame, names: tuple, count: int = 4) -> Optional[float]:
            cols = columns(frame)[:count]
            values = [pick(frame, names, col) for col in cols]
            clean = [value for value in values if value is not None]
            return sum(clean) if len(clean) == count else None

        balance_fields = {
            "total_assets": ("Total Assets",),
            "total_liabilities": ("Total Liabilities Net Minority Interest", "Total Liab"),
            "total_equity": ("Stockholders Equity", "Common Stock Equity", "Total Equity Gross Minority Interest"),
            "cash": ("Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents", "Cash"),
            "debt": ("Total Debt",),
            "current_assets": ("Current Assets", "Total Current Assets"),
            "current_liabilities": ("Current Liabilities", "Total Current Liabilities"),
        }
        income_fields = {
            "total_revenue": ("Total Revenue", "Revenue", "Net Sales"),
            "gross_profit": ("Gross Profit",),
            "operating_income": ("Operating Income",),
            "net_income": ("Net Income", "Net Income Common Stockholders", "Net Income Continuous Operations"),
            "eps": ("Diluted EPS", "Basic EPS"),
        }
        cash_flow_fields = {
            "operating_cash_flow": ("Operating Cash Flow", "Total Cash From Operating Activities"),
            "capital_expenditure": ("Capital Expenditure", "Capital Expenditures"),
            "financing_cash_flow": ("Financing Cash Flow", "Total Cash From Financing Activities"),
            "free_cash_flow": ("Free Cash Flow",),
        }

        try:
            ticker = ticker or yf.Ticker(symbol)
            q_bs = getattr(ticker, "quarterly_balance_sheet", pd.DataFrame())
            q_inc = getattr(ticker, "quarterly_income_stmt", pd.DataFrame())
            q_cf = getattr(ticker, "quarterly_cash_flow", pd.DataFrame())
            a_bs = getattr(ticker, "balance_sheet", pd.DataFrame())
            a_inc = getattr(ticker, "financials", pd.DataFrame())
            a_cf = getattr(ticker, "cashflow", pd.DataFrame())

            latest_q_bs = statement(q_bs, balance_fields, "quarterly")
            latest_q_inc = statement(q_inc, income_fields, "quarterly")
            latest_q_cf = statement(q_cf, cash_flow_fields, "quarterly")
            quarter_dates = [
                payload.get("period_end")
                for payload in (latest_q_bs, latest_q_inc, latest_q_cf)
                if payload.get("period_end")
            ]
            latest_quarter: Dict[str, Any] = {
                "period_end": max(quarter_dates) if quarter_dates else None,
                "period_type": "quarterly",
                "currency": currency,
                "source": "yfinance",
                "balance_sheet": latest_q_bs,
                "income_statement": latest_q_inc,
                "cash_flow": latest_q_cf,
            }

            q_inc_cols = columns(q_inc)
            derived: Dict[str, Any] = {}
            if len(q_inc_cols) >= 5:
                latest_revenue = pick(q_inc, income_fields["total_revenue"], q_inc_cols[0])
                prior_year_revenue = pick(q_inc, income_fields["total_revenue"], q_inc_cols[4])
                if latest_revenue is not None and prior_year_revenue not in (None, 0):
                    derived["revenue_growth"] = (latest_revenue / prior_year_revenue - 1.0) * 100.0
                    derived["revenue_growth_basis"] = "latest_quarter_yoy"
            q_revenue = latest_q_inc.get("total_revenue")
            q_net_income = latest_q_inc.get("net_income")
            if q_revenue not in (None, 0) and q_net_income is not None:
                derived["profit_margin"] = q_net_income / q_revenue * 100.0
            equity = latest_q_bs.get("total_equity")
            debt = latest_q_bs.get("debt")
            if equity not in (None, 0) and debt is not None:
                derived["debt_to_equity"] = debt / equity
            current_assets = latest_q_bs.get("current_assets")
            current_liabilities = latest_q_bs.get("current_liabilities")
            if current_assets is not None and current_liabilities not in (None, 0):
                derived["current_ratio"] = current_assets / current_liabilities
            latest_quarter["derived"] = derived

            ttm_income = {
                "total_revenue": sum_latest(q_inc, income_fields["total_revenue"]),
                "gross_profit": sum_latest(q_inc, income_fields["gross_profit"]),
                "operating_income": sum_latest(q_inc, income_fields["operating_income"]),
                "net_income": sum_latest(q_inc, income_fields["net_income"]),
            }
            ttm_cash = {
                "operating_cash_flow": sum_latest(q_cf, cash_flow_fields["operating_cash_flow"]),
                "capital_expenditure": sum_latest(q_cf, cash_flow_fields["capital_expenditure"]),
                "financing_cash_flow": sum_latest(q_cf, cash_flow_fields["financing_cash_flow"]),
                "free_cash_flow": sum_latest(q_cf, cash_flow_fields["free_cash_flow"]),
            }
            ttm_derived: Dict[str, Any] = {}
            if ttm_income.get("total_revenue") not in (None, 0) and ttm_income.get("net_income") is not None:
                ttm_derived["profit_margin"] = ttm_income["net_income"] / ttm_income["total_revenue"] * 100.0
            if equity not in (None, 0) and ttm_income.get("net_income") is not None:
                ttm_derived["roe"] = ttm_income["net_income"] / equity * 100.0
            ttm = {
                "period_end": latest_quarter.get("period_end"),
                "period_type": "ttm",
                "currency": currency,
                "source": "yfinance_derived_from_quarters",
                "income_statement": ttm_income,
                "cash_flow": ttm_cash,
                "derived": ttm_derived,
                "complete_quarters": min(len(columns(q_inc)), len(columns(q_cf)), 4),
            }

            annual_bs = statement(a_bs, balance_fields, "annual")
            annual_inc = statement(a_inc, income_fields, "annual")
            annual_cf = statement(a_cf, cash_flow_fields, "annual")
            annual_dates = [payload.get("period_end") for payload in (annual_bs, annual_inc, annual_cf) if payload.get("period_end")]
            latest_annual = {
                "period_end": max(annual_dates) if annual_dates else None,
                "period_type": "annual",
                "currency": currency,
                "source": "yfinance",
                "balance_sheet": annual_bs,
                "income_statement": annual_inc,
                "cash_flow": annual_cf,
            }

            if not quarter_dates and not annual_dates:
                return None

            # Compatibility aliases now intentionally point to the latest reported quarter.
            return {
                "latest_quarter": latest_quarter,
                "ttm": ttm,
                "latest_annual": latest_annual,
                "balance_sheet": latest_q_bs or annual_bs,
                "income_statement": latest_q_inc or annual_inc,
                "cash_flow": latest_q_cf or annual_cf,
                "_meta": {
                    "preferred_basis": "latest_reported_quarter",
                    "periods_separated": True,
                    "source": "yfinance",
                    "currency": currency,
                },
            }
            
        except Exception as e:
            logger.debug(f"Financial statements fetch failed for {symbol}: {e}")
            return None
    
    def _get_earnings_data(self, symbol: str, *, ticker=None) -> Optional[Dict[str, Any]]:
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
            ticker = ticker or yf.Ticker(symbol)
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
            "volume_to_market_cap_pct": market_structure.get("volume_to_market_cap_pct"),
            "funding_rate": funding_rate,
            "funding_rate_decimal": derivatives.get("funding_rate_decimal"),
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
            },
            "metric_metadata": {
                **(market_structure.get("field_metadata") or {}),
                **(derivatives.get("field_metadata") or {}),
                **(capital_flow.get("field_metadata") or {}),
            },
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
            "volume_to_market_cap_pct": None,
            "source": "price+kline",
            "field_metadata": {},
        }
        try:
            quote_volume = self._safe_num(price_data.get("quoteVolume"))
            if quote_volume is not None:
                out["volume_24h"] = quote_volume
                out["field_metadata"]["volume_24h"] = {
                    "unit": "quote_asset",
                    "currency": price_data.get("quoteCurrency") or price_data.get("quote_currency"),
                    "provider": price_data.get("source") or "price_provider",
                    "venue": price_data.get("exchange") or price_data.get("venue"),
                    "product_type": price_data.get("market_type") or "spot",
                }
        except Exception:
            pass

        try:
            if len(kline_data) >= 2:
                latest_vol = self._safe_num(kline_data[-1].get("volume"), 0.0) or 0.0
                prev_vol = self._safe_num(kline_data[-2].get("volume"), 0.0) or 0.0
                if prev_vol > 0:
                    out["volume_change_24h"] = ((latest_vol - prev_vol) / prev_vol) * 100.0
                    out["field_metadata"]["volume_change_24h"] = {
                        "unit": "percent",
                        "provider": "kline",
                        "venue": price_data.get("exchange") or price_data.get("venue"),
                        "product_type": price_data.get("market_type") or "spot",
                    }
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
                    out["field_metadata"]["volume_24h"] = {
                        "unit": "usd",
                        "currency": "USD",
                        "provider": "coingecko",
                        "venue": "aggregate",
                        "product_type": "spot",
                    }
            if out["volume_change_24h"] is None:
                market_cap = self._safe_num(coin.get("market_cap"))
                total_volume = self._safe_num(coin.get("total_volume"))
                if total_volume is not None and market_cap and market_cap > 0:
                    # This is turnover, not a change through time.  Keeping it
                    # separate prevents the scoring layer from interpreting a
                    # 20% volume/market-cap ratio as +20% volume growth.
                    out["volume_to_market_cap_pct"] = (total_volume / market_cap) * 100.0
                    out["source"] = "coingecko+proxy"
                    out["field_metadata"]["volume_to_market_cap_pct"] = {
                        "unit": "percent",
                        "provider": "coingecko",
                        "venue": "aggregate",
                        "product_type": "spot",
                    }
        return out

    def _get_crypto_derivatives_metrics(self, symbol: str) -> Dict[str, Any]:
        result = {
            "funding_rate": None,
            "funding_rate_decimal": None,
            "open_interest": None,
            "open_interest_change_24h": None,
            "long_short_ratio": None,
            "source": "",
            "field_metadata": {},
        }

        payload = self._coinglass_get("/api/futures/fundingRate/exchange-list", {"symbol": symbol}, ttl_sec=90)
        latest = self._pick_latest_item(payload)
        result["funding_rate"] = self._pick_number(latest or payload, "oi_weighted_funding_rate", "funding_rate", "fundingRate")
        if result["funding_rate"] is not None:
            result["source"] = "coinglass"
            result["funding_rate_decimal"] = result["funding_rate"] / 100.0
            result["field_metadata"]["funding_rate"] = {
                "unit": "percent",
                "provider": "coinglass",
                "venue": "aggregate",
                "product_type": "perpetual",
            }

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
            result["field_metadata"]["open_interest"] = {
                "unit": "usd",
                "currency": "USD",
                "provider": "coinglass",
                "venue": "aggregate",
                "product_type": "perpetual",
            }
        if result["open_interest_change_24h"] is not None:
            result["field_metadata"]["open_interest_change_24h"] = {
                "unit": "percent",
                "provider": "coinglass",
                "venue": "aggregate",
                "product_type": "perpetual",
            }

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
            result["field_metadata"]["long_short_ratio"] = {
                "unit": "ratio",
                "provider": "coinglass",
                "venue": "aggregate",
                "product_type": "perpetual",
            }

        # Never fill individual fields from unrelated venues.  A funding rate
        # from one exchange and OI from another is not a coherent snapshot.
        # Prefer a complete aggregate CoinGlass snapshot; otherwise choose one
        # venue-specific public snapshot as a unit.
        if result["funding_rate"] is not None and result["open_interest"] is not None:
            return result

        candidates = [result]
        pair_gate = f"{symbol}_USDT"
        gate = self._get_gate_public_derivatives(pair_gate)
        candidates.append(gate)
        if gate.get("funding_rate") is not None and gate.get("open_interest") is not None:
            return gate

        pair_okx = f"{symbol}-USDT-SWAP"
        okx = self._get_okx_public_derivatives(pair_okx)
        candidates.append(okx)
        if okx.get("funding_rate") is not None and okx.get("open_interest") is not None:
            return okx

        pair_binance = f"{symbol}USDT"
        empty = {
            "funding_rate": None, "funding_rate_decimal": None,
            "open_interest": None, "open_interest_change_24h": None,
            "long_short_ratio": None, "source": "", "field_metadata": {},
        }
        candidates.append(self._fill_crypto_derivatives_from_binance(pair_binance, empty))
        return max(
            candidates,
            key=lambda item: sum(
                item.get(key) is not None
                for key in ("funding_rate", "open_interest", "open_interest_change_24h", "long_short_ratio")
            ),
        )

    def _get_gate_public_derivatives(self, contract: str) -> Dict[str, Any]:
        """Fetch one coherent Gate USDT perpetual snapshot without credentials."""
        cache_key = f"gate_public_derivatives|{contract}"
        cached = self._cache_get(cache_key)
        if isinstance(cached, dict):
            return cached
        result: Dict[str, Any] = {
            "funding_rate": None, "funding_rate_decimal": None,
            "open_interest": None, "open_interest_change_24h": None,
            "long_short_ratio": None, "source": "", "field_metadata": {},
        }
        try:
            contract_response = requests.get(
                f"https://api.gateio.ws/api/v4/futures/usdt/contracts/{contract}",
                headers={"Accept": "application/json"},
                timeout=6,
            )
            contract_response.raise_for_status()
            contract_payload = contract_response.json() or {}
            decimal_rate = self._safe_num(
                contract_payload.get("funding_rate")
                or contract_payload.get("funding_rate_indicative")
            )
            if decimal_rate is not None:
                result["funding_rate_decimal"] = decimal_rate
                result["funding_rate"] = decimal_rate * 100.0
                result["field_metadata"]["funding_rate"] = {
                    "unit": "percent", "source_unit": "decimal",
                    "provider": "gate_public", "venue": "gate",
                    "product_type": "perpetual",
                }
        except Exception as exc:
            logger.debug("Gate public funding failed for %s: %s", contract, exc)

        try:
            stats_response = requests.get(
                "https://api.gateio.ws/api/v4/futures/usdt/contract_stats",
                params={"contract": contract, "interval": "1d", "limit": 2},
                headers={"Accept": "application/json"},
                timeout=6,
            )
            stats_response.raise_for_status()
            stats = stats_response.json() or []
            if stats:
                latest = stats[-1]
                result["open_interest"] = self._safe_num(latest.get("open_interest_usd"))
                result["long_short_ratio"] = self._safe_num(latest.get("lsr_account"))
                if len(stats) >= 2:
                    previous_oi = self._safe_num(stats[-2].get("open_interest_usd"))
                    latest_oi = result["open_interest"]
                    if previous_oi and latest_oi is not None:
                        result["open_interest_change_24h"] = (latest_oi - previous_oi) / previous_oi * 100.0
                for key, unit in (
                    ("open_interest", "usd"),
                    ("open_interest_change_24h", "percent"),
                    ("long_short_ratio", "ratio"),
                ):
                    if result.get(key) is not None:
                        result["field_metadata"][key] = {
                            "unit": unit,
                            "currency": "USD" if unit == "usd" else None,
                            "provider": "gate_public", "venue": "gate",
                            "product_type": "perpetual",
                        }
        except Exception as exc:
            logger.debug("Gate public derivatives failed for %s: %s", contract, exc)
        if any(result.get(key) is not None for key in ("funding_rate", "open_interest", "long_short_ratio")):
            result["source"] = "gate_public"
            self._cache_set(cache_key, result, 120)
        return result

    def _get_okx_public_derivatives(self, instrument: str) -> Dict[str, Any]:
        """Fetch one coherent OKX USDT perpetual snapshot without credentials."""
        cache_key = f"okx_public_derivatives|{instrument}"
        cached = self._cache_get(cache_key)
        if isinstance(cached, dict):
            return cached
        result: Dict[str, Any] = {
            "funding_rate": None, "funding_rate_decimal": None,
            "open_interest": None, "open_interest_change_24h": None,
            "long_short_ratio": None, "source": "", "field_metadata": {},
        }
        try:
            funding_response = requests.get(
                "https://www.okx.com/api/v5/public/funding-rate-history",
                params={"instId": instrument, "limit": 2},
                timeout=6,
            )
            funding_response.raise_for_status()
            rows = (funding_response.json() or {}).get("data") or []
            if rows:
                decimal_rate = self._safe_num(rows[0].get("realizedRate") or rows[0].get("fundingRate"))
                if decimal_rate is not None:
                    result["funding_rate_decimal"] = decimal_rate
                    result["funding_rate"] = decimal_rate * 100.0
                    result["field_metadata"]["funding_rate"] = {
                        "unit": "percent", "source_unit": "decimal",
                        "provider": "okx_public", "venue": "okx",
                        "product_type": "perpetual",
                    }
        except Exception as exc:
            logger.debug("OKX public funding failed for %s: %s", instrument, exc)
        try:
            oi_response = requests.get(
                "https://www.okx.com/api/v5/public/open-interest",
                params={"instType": "SWAP", "instId": instrument},
                timeout=6,
            )
            oi_response.raise_for_status()
            rows = (oi_response.json() or {}).get("data") or []
            if rows:
                result["open_interest"] = self._safe_num(rows[0].get("oiUsd"))
                if result["open_interest"] is not None:
                    result["field_metadata"]["open_interest"] = {
                        "unit": "usd", "currency": "USD",
                        "provider": "okx_public", "venue": "okx",
                        "product_type": "perpetual",
                    }
        except Exception as exc:
            logger.debug("OKX public open interest failed for %s: %s", instrument, exc)
        if result.get("funding_rate") is not None or result.get("open_interest") is not None:
            result["source"] = "okx_public"
            self._cache_set(cache_key, result, 120)
        return result

    def _fill_crypto_derivatives_from_binance(self, pair: str, result: Dict[str, Any]) -> Dict[str, Any]:
        cache_key = f"binance_derivatives|{pair}"
        cached = self._cache_get(cache_key)
        if isinstance(cached, dict):
            merged = dict(result)
            filled_fields = set()
            for k, v in cached.items():
                if k == "field_metadata":
                    continue
                if merged.get(k) is None and v is not None:
                    merged[k] = v
                    filled_fields.add(k)
            cached_metadata = cached.get("field_metadata") or {}
            merged.setdefault("field_metadata", {}).update({
                key: cached_metadata[key]
                for key in filled_fields
                if key in cached_metadata
            })
            if any(merged.get(k) is not None for k in ("funding_rate", "open_interest", "long_short_ratio")) and not merged.get("source"):
                merged["source"] = "binance_public"
            return merged

        fallback = {"field_metadata": {}}
        try:
            funding_resp = requests.get(
                "https://fapi.binance.com/fapi/v1/fundingRate",
                params={"symbol": pair, "limit": 1},
                timeout=8,
            )
            funding_resp.raise_for_status()
            items = funding_resp.json() or []
            if items:
                decimal_rate = self._safe_num(items[-1].get("fundingRate"))
                if decimal_rate is not None:
                    fallback["funding_rate_decimal"] = decimal_rate
                    fallback["funding_rate"] = decimal_rate * 100.0
                    fallback["field_metadata"]["funding_rate"] = {
                        "unit": "percent",
                        "source_unit": "decimal",
                        "provider": "binance_public",
                        "venue": "binance",
                        "product_type": "perpetual",
                    }
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
                fallback["field_metadata"]["open_interest"] = {
                    "unit": "usd",
                    "currency": "USD",
                    "provider": "binance_public",
                    "venue": "binance",
                    "product_type": "perpetual",
                }
                if len(items) >= 2:
                    prev = self._safe_num(items[-2].get("sumOpenInterestValue"), 0.0) or 0.0
                    curr = self._safe_num(latest.get("sumOpenInterestValue"), 0.0) or 0.0
                    if prev > 0:
                        fallback["open_interest_change_24h"] = ((curr - prev) / prev) * 100.0
                        fallback["field_metadata"]["open_interest_change_24h"] = {
                            "unit": "percent",
                            "provider": "binance_public",
                            "venue": "binance",
                            "product_type": "perpetual",
                        }
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
                fallback["field_metadata"]["long_short_ratio"] = {
                    "unit": "ratio",
                    "provider": "binance_public",
                    "venue": "binance",
                    "product_type": "perpetual",
                }
        except Exception as e:
            logger.debug(f"Binance long/short fallback failed for {pair}: {e}")

        self._cache_set(cache_key, fallback, 120)
        merged = dict(result)
        filled_fields = set()
        for k, v in fallback.items():
            if k == "field_metadata":
                continue
            if merged.get(k) is None and v is not None:
                merged[k] = v
                filled_fields.add(k)
        fallback_metadata = fallback.get("field_metadata") or {}
        merged.setdefault("field_metadata", {}).update({
            key: fallback_metadata[key]
            for key in filled_fields
            if key in fallback_metadata
        })
        if any(merged.get(k) is not None for k in ("funding_rate", "open_interest", "long_short_ratio")) and not merged.get("source"):
            merged["source"] = "binance_public"
        return merged

    def _get_crypto_capital_flow(self, symbol: str) -> Dict[str, Any]:
        result = {
            "exchange_netflow": None,
            "stablecoin_netflow": None,
            "source": "",
            "field_metadata": {},
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
        if result["exchange_netflow"] is not None:
            result["field_metadata"]["exchange_netflow"] = {
                "unit": "usd",
                "currency": "USD",
                "provider": "coinglass",
                "venue": "aggregate",
                "product_type": "spot",
            }

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
            result["field_metadata"]["stablecoin_netflow"] = {
                "unit": "usd",
                "currency": "USD",
                "provider": "cryptoquant",
                "venue": "aggregate",
                "product_type": "spot",
            }

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
            from app.data_providers import get_cached as _get_cached
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
                        published_at = ""
                        try:
                            # Finnhub timestamps are Unix instants in UTC.  A
                            # timezone-naive ``fromtimestamp`` converts them
                            # through the container's local timezone (normally
                            # Asia/Shanghai) and the evidence layer then reads
                            # that wall clock as UTC, making recent news appear
                            # eight hours in the future.
                            published_at = datetime.fromtimestamp(
                                float(item.get('datetime') or 0),
                                tz=timezone.utc,
                            ).isoformat().replace("+00:00", "Z")
                        except (TypeError, ValueError, OverflowError, OSError):
                            published_at = ""
                        news_list.append({
                            "datetime": published_at,
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
        
        # Broad global headlines are disabled by default. They are useful as
        # background context, but injecting them into every asset report made
        # unrelated conflicts look like target-specific bearish evidence.
        if os.getenv("FAST_ANALYSIS_INCLUDE_GLOBAL_NEWS", "false").lower() == "true":
            global_events = self._get_global_major_events()
            for item in global_events:
                item["asset_relevance"] = "background"
            if global_events:
                news_list.extend(global_events)
                logger.info(f"Added {len(global_events)} background global events to news list")
        
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
