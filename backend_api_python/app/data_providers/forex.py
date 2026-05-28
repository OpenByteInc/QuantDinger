"""Forex pair data fetchers with multi-source fallback."""
from __future__ import annotations

import requests
from datetime import datetime, timedelta
from typing import Any, Dict, List

from app.utils.logger import get_logger

logger = get_logger(__name__)

# Canonical pair definitions shared by all fetchers
FOREX_PAIRS = [
    {"td": "EUR/USD", "yf": "EURUSD=X", "tiingo": "eurusd", "name_cn": "欧元/美元", "name_en": "EUR/USD", "base": "EUR", "quote": "USD"},
    {"td": "GBP/USD", "yf": "GBPUSD=X", "tiingo": "gbpusd", "name_cn": "英镑/美元", "name_en": "GBP/USD", "base": "GBP", "quote": "USD"},
    {"td": "USD/JPY", "yf": "USDJPY=X", "tiingo": "usdjpy", "name_cn": "美元/日元", "name_en": "USD/JPY", "base": "USD", "quote": "JPY"},
    {"td": "USD/CNH", "yf": "USDCNH=X", "tiingo": "usdcnh", "name_cn": "美元/离岸人民币", "name_en": "USD/CNH", "base": "USD", "quote": "CNH"},
    {"td": "AUD/USD", "yf": "AUDUSD=X", "tiingo": "audusd", "name_cn": "澳元/美元", "name_en": "AUD/USD", "base": "AUD", "quote": "USD"},
    {"td": "USD/CAD", "yf": "USDCAD=X", "tiingo": "usdcad", "name_cn": "美元/加元", "name_en": "USD/CAD", "base": "USD", "quote": "CAD"},
    {"td": "USD/CHF", "yf": "USDCHF=X", "tiingo": "usdchf", "name_cn": "美元/瑞郎", "name_en": "USD/CHF", "base": "USD", "quote": "CHF"},
    {"td": "NZD/USD", "yf": "NZDUSD=X", "tiingo": "nzdusd", "name_cn": "纽元/美元", "name_en": "NZD/USD", "base": "NZD", "quote": "USD"},
    {"td": "EUR/GBP", "yf": "EURGBP=X", "tiingo": "eurgbp", "name_cn": "欧元/英镑", "name_en": "EUR/GBP", "base": "EUR", "quote": "GBP"},
    {"td": "EUR/JPY", "yf": "EURJPY=X", "tiingo": "eurjpy", "name_cn": "欧元/日元", "name_en": "EUR/JPY", "base": "EUR", "quote": "JPY"},
    {"td": "GBP/JPY", "yf": "GBPJPY=X", "tiingo": "gbpjpy", "name_cn": "英镑/日元", "name_en": "GBP/JPY", "base": "GBP", "quote": "JPY"},
    {"td": "USD/HKD", "yf": "USDHKD=X", "tiingo": "usdhkd", "name_cn": "美元/港币", "name_en": "USD/HKD", "base": "USD", "quote": "HKD"},
]


def _fetch_td(pairs: list) -> List[Dict[str, Any]]:
    """Fetch forex quotes from Twelve Data."""
    try:
        from app.config import APIKeys
        api_key = (APIKeys.TWELVE_DATA_API_KEY or "").strip()
        if not api_key:
            return []
        result = []
        for pair in pairs:
            try:
                resp = requests.get("https://api.twelvedata.com/quote", params={
                    "symbol": pair["td"], "apikey": api_key,
                }, timeout=10)
                data = resp.json()
                if data.get("status") == "error" or not data.get("close"):
                    continue
                current = float(data.get("close") or 0)
                prev = float(data.get("previous_close") or 0)
                change = ((current - prev) / prev * 100) if prev else 0
                result.append({
                    "symbol": pair["td"],
                    "name": pair["td"],
                    "name_cn": pair["name_cn"],
                    "name_en": pair["name_en"],
                    "price": round(current, 5),
                    "change": round(change, 2),
                    "base": pair["base"],
                    "quote": pair["quote"],
                    "category": "forex",
                })
            except Exception as e:
                logger.debug("TwelveData forex quote %s failed: %s", pair["td"], e)
        if result:
            logger.info("Fetched %d forex pairs via Twelve Data", len(result))
        return result
    except Exception as e:
        logger.debug("TwelveData forex pairs batch failed: %s", e)
        return []


def _fetch_yf(pairs: list) -> List[Dict[str, Any]]:
    """Fetch forex quotes from yfinance (fallback)."""
    try:
        import yfinance as yf
        symbols = [p["yf"] for p in pairs]
        tickers = yf.Tickers(" ".join(symbols))
        result = []
        for pair in pairs:
            try:
                ticker = tickers.tickers.get(pair["yf"])
                if ticker:
                    hist = ticker.history(period="2d")
                    if len(hist) >= 2:
                        prev_close = hist["Close"].iloc[-2]
                        current = hist["Close"].iloc[-1]
                        change = ((current - prev_close) / prev_close) * 100
                    elif len(hist) == 1:
                        current = hist["Close"].iloc[-1]
                        change = 0
                    else:
                        continue
                    result.append({
                        "symbol": pair["td"],
                        "name": pair["td"],
                        "name_cn": pair["name_cn"],
                        "name_en": pair["name_en"],
                        "price": round(current, 5),
                        "change": round(change, 2),
                        "base": pair["base"],
                        "quote": pair["quote"],
                        "category": "forex",
                    })
            except Exception as e:
                logger.debug("yfinance forex %s failed: %s", pair["yf"], e)
        return result
    except Exception as e:
        logger.error("yfinance forex batch failed: %s", e)
        return []


def _fetch_tiingo(pairs: list) -> List[Dict[str, Any]]:
    """Fetch forex quotes from Tiingo FX (Tier 3 fallback)."""
    try:
        from app.config import TiingoConfig, APIKeys
        api_key = APIKeys.TIINGO_API_KEY
        if not api_key:
            return []
        result = []
        for pair in pairs:
            tiingo_sym = pair.get("tiingo")
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
                        "symbol": pair["td"],
                        "name": pair["td"],
                        "name_cn": pair["name_cn"],
                        "name_en": pair["name_en"],
                        "price": round(current, 5),
                        "change": round(change, 2),
                        "base": pair["base"],
                        "quote": pair["quote"],
                        "category": "forex",
                    })
            except Exception as e:
                logger.debug("Tiingo forex %s failed: %s", tiingo_sym, e)
        if result:
            logger.info("Fetched %d forex pairs via Tiingo", len(result))
        return result
    except Exception as e:
        logger.debug("Tiingo forex batch failed: %s", e)
        return []


def fetch_forex_pairs() -> List[Dict[str, Any]]:
    """Fetch major forex pairs.  Priority: Twelve Data → yfinance → Tiingo."""
    pairs = FOREX_PAIRS
    result: List[Dict[str, Any]] = []
    for fetcher in (_fetch_td, _fetch_yf, _fetch_tiingo):
        try:
            batch = fetcher(pairs)
        except Exception as e:
            logger.debug("Forex overview fetcher %s failed: %s", fetcher.__name__, e)
            batch = []
        if batch:
            existing = {r["symbol"] for r in result}
            for r in batch:
                if r["symbol"] not in existing:
                    result.append(r)
        if len(result) >= len(pairs) // 2:
            break

    if not result:
        logger.warning("Forex fetch all tiers failed, returning placeholder data")
        for pair in pairs:
            result.append({
                "symbol": pair["td"],
                "name": pair["td"],
                "name_cn": pair["name_cn"],
                "name_en": pair["name_en"],
                "price": 0,
                "change": 0,
                "base": pair["base"],
                "quote": pair["quote"],
                "category": "forex",
            })
    return result
