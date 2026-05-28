"""Commodity price data fetchers with multi-source fallback."""
from __future__ import annotations

import requests
from datetime import datetime, timedelta
from typing import Any, Dict, List

from app.utils.logger import get_logger
from app.data_providers import safe_float

logger = get_logger(__name__)

COMMODITIES = [
    {"td": "GC", "yf": "GC=F", "tiingo": "xauusd", "name_cn": "黄金", "name_en": "Gold", "unit": "USD/oz"},
    {"td": "SI", "yf": "SI=F", "tiingo": "xagusd", "name_cn": "白银", "name_en": "Silver", "unit": "USD/oz"},
    {"td": "CL", "yf": "CL=F", "tiingo": None, "name_cn": "原油 WTI", "name_en": "Crude Oil WTI", "unit": "USD/bbl"},
    {"td": "BZ", "yf": "BZ=F", "tiingo": None, "name_cn": "原油 Brent", "name_en": "Brent Oil", "unit": "USD/bbl"},
    {"td": "HG", "yf": "HG=F", "tiingo": None, "name_cn": "铜", "name_en": "Copper", "unit": "USD/lb"},
    {"td": "NG", "yf": "NG=F", "tiingo": None, "name_cn": "天然气", "name_en": "Natural Gas", "unit": "USD/MMBtu"},
    {"td": "PL", "yf": "PL=F", "tiingo": None, "name_cn": "铂金", "name_en": "Platinum", "unit": "USD/oz"},
    {"td": "PA", "yf": "PA=F", "tiingo": None, "name_cn": "钯金", "name_en": "Palladium", "unit": "USD/oz"},
    {"td": "ALI", "yf": "ALI=F", "tiingo": None, "name_cn": "铝", "name_en": "Aluminum", "unit": "USD/lb"},
    {"td": "ZW", "yf": "ZW=F", "tiingo": None, "name_cn": "小麦", "name_en": "Wheat", "unit": "USD/bu"},
    {"td": "ZC", "yf": "ZC=F", "tiingo": None, "name_cn": "玉米", "name_en": "Corn", "unit": "USD/bu"},
    {"td": "SB", "yf": "SB=F", "tiingo": None, "name_cn": "糖", "name_en": "Sugar", "unit": "USD/lb"},
]


def _fetch_td(commodities: list) -> List[Dict[str, Any]]:
    """Fetch commodity quotes from Twelve Data."""
    try:
        from app.config import APIKeys
        api_key = (APIKeys.TWELVE_DATA_API_KEY or "").strip()
        if not api_key:
            return []
        result = []
        for c in commodities:
            try:
                resp = requests.get("https://api.twelvedata.com/quote", params={
                    "symbol": c["td"], "apikey": api_key,
                }, timeout=10)
                data = resp.json()
                if data.get("status") == "error" or not data.get("close"):
                    continue
                current = float(data.get("close") or 0)
                prev = float(data.get("previous_close") or 0)
                change = ((current - prev) / prev * 100) if prev else 0
                result.append({
                    "symbol": c["yf"],
                    "name_cn": c["name_cn"],
                    "name_en": c["name_en"],
                    "price": round(current, 2),
                    "change": round(change, 2),
                    "unit": c["unit"],
                    "category": "commodity",
                })
            except Exception as e:
                logger.debug("TwelveData commodity %s failed: %s", c["td"], e)
        if result:
            logger.info("Fetched %d commodities via Twelve Data", len(result))
        return result
    except Exception as e:
        logger.debug("TwelveData commodities batch failed: %s", e)
        return []


def _fetch_yf(commodities: list) -> List[Dict[str, Any]]:
    """Fetch commodity prices from yfinance (fallback)."""
    result = []
    try:
        import yfinance as yf
        symbols = [c["yf"] for c in commodities]
        tickers = yf.Tickers(" ".join(symbols))
        for c in commodities:
            try:
                ticker = tickers.tickers.get(c["yf"])
                if not ticker:
                    continue
                hist = ticker.history(period="2d")
                if len(hist) >= 2:
                    prev_close = hist["Close"].iloc[-2]
                    current = hist["Close"].iloc[-1]
                    change = ((current - prev_close) / prev_close) * 100
                elif len(hist) == 1:
                    current = safe_float(hist["Close"].iloc[-1], 0)
                    change = 0.0
                    try:
                        fast_info = getattr(ticker, "fast_info", {}) or {}
                        prev_close = safe_float(fast_info.get("previousClose"), 0)
                        if prev_close > 0 and current > 0:
                            change = ((current - prev_close) / prev_close) * 100
                    except Exception:
                        pass
                else:
                    continue
                result.append({
                    "symbol": c["yf"],
                    "name_cn": c["name_cn"],
                    "name_en": c["name_en"],
                    "price": round(current, 2),
                    "change": round(change, 2),
                    "unit": c["unit"],
                    "category": "commodity",
                })
            except Exception as e:
                logger.debug("yfinance commodity %s failed: %s", c["yf"], e)
        if result:
            logger.info("Fetched %d commodities via yfinance", len(result))
    except Exception as e:
        logger.error("yfinance commodities batch failed: %s", e)
    return result


def _fetch_tiingo(commodities: list) -> List[Dict[str, Any]]:
    """Fetch precious metal commodity prices via Tiingo FX (gold/silver only)."""
    try:
        from app.config import TiingoConfig, APIKeys
        api_key = APIKeys.TIINGO_API_KEY
        if not api_key:
            return []
        result = []
        for c in commodities:
            tiingo_sym = c.get("tiingo")
            if not tiingo_sym:
                continue
            try:
                resp = requests.get(f"{TiingoConfig.BASE_URL}/fx/{tiingo_sym}/prices", params={
                    "startDate": (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d"),
                    "endDate": datetime.now().strftime("%Y-%m-%d"),
                    "resampleFreq": "1day",
                    "token": api_key,
                }, timeout=TiingoConfig.TIMEOUT)
                if resp.status_code != 200:
                    continue
                data = resp.json()
                if not data or len(data) < 1:
                    continue
                current = float(data[-1].get("close", 0) or 0)
                prev = float(data[-2].get("close", 0) or 0) if len(data) >= 2 else 0
                change = ((current - prev) / prev * 100) if prev else 0
                if current > 0:
                    result.append({
                        "symbol": c["yf"],
                        "name_cn": c["name_cn"],
                        "name_en": c["name_en"],
                        "price": round(current, 2),
                        "change": round(change, 2),
                        "unit": c["unit"],
                        "category": "commodity",
                    })
            except Exception as e:
                logger.debug("Tiingo commodity %s failed: %s", tiingo_sym, e)
        if result:
            logger.info("Fetched %d commodities via Tiingo", len(result))
        return result
    except Exception as e:
        logger.debug("Tiingo commodities batch failed: %s", e)
        return []


def fetch_commodities() -> List[Dict[str, Any]]:
    """Fetch commodity prices.  Priority: Twelve Data → yfinance → Tiingo."""
    commodities = COMMODITIES
    result: List[Dict[str, Any]] = []
    for fetcher in (_fetch_td, _fetch_yf, _fetch_tiingo):
        try:
            batch = fetcher(commodities)
        except Exception as e:
            logger.debug("Commodities fetcher %s failed: %s", fetcher.__name__, e)
            batch = []
        if batch:
            existing = {r["symbol"] for r in result}
            for r in batch:
                if r["symbol"] not in existing:
                    result.append(r)
        if len(result) >= len(commodities) // 2:
            break

    if not result:
        logger.warning("Commodities fetch all tiers failed, returning placeholder data")
        for c in commodities:
            result.append({
                "symbol": c["yf"], "name_cn": c["name_cn"], "name_en": c["name_en"],
                "price": 0, "change": 0, "unit": c["unit"], "category": "commodity",
            })
    return result
