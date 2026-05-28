"""Heatmap data aggregator — pulls from crypto / forex / commodities / indices."""
from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List

from app.utils.logger import get_logger
from app.data_providers import get_cached, set_cached, safe_float
from app.data_providers.crypto import (
    fetch_crypto_heatmap_coingecko,
    fetch_crypto_heatmap_coincap,
    fetch_crypto_prices,
)
from app.data_providers.forex import fetch_forex_pairs
from app.data_providers.commodities import fetch_commodities
from app.data_providers.opportunities import fetch_local_stock_opportunity_prices

logger = get_logger(__name__)

HEATMAP_CELL_COUNT = 12


def _cap_heatmap_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return (rows or [])[:HEATMAP_CELL_COUNT]


def _stock_rows(stock_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = []
    for stock in stock_data or []:
        rows.append({
            "name": stock.get("symbol", ""),
            "fullName": stock.get("name", ""),
            "name_cn": stock.get("name", ""),
            "name_en": stock.get("name", ""),
            "value": stock.get("change", 0),
            "price": stock.get("price", 0),
        })
    return _cap_heatmap_rows(rows)


def _fetch_crypto_data() -> List[Dict[str, Any]]:
    for cache_key in ("crypto_heatmap", "crypto_prices"):
        cached = get_cached(cache_key)
        if cached and len(cached) >= HEATMAP_CELL_COUNT:
            return cached

    for fetcher in (fetch_crypto_heatmap_coingecko, fetch_crypto_heatmap_coincap):
        try:
            crypto_data = fetcher()
            if crypto_data and len(crypto_data) >= HEATMAP_CELL_COUNT:
                set_cached("crypto_heatmap", crypto_data, 300)
                return crypto_data
        except Exception as e:
            logger.debug("Crypto heatmap fetcher %s failed: %s", fetcher.__name__, e)
    cached = get_cached("crypto_prices") or get_cached("crypto_heatmap") or []
    if cached:
        return cached

    data = fetch_crypto_prices(fast=True)
    if data:
        set_cached("crypto_heatmap", data, 300)
    return data or []


def _build_crypto_heatmap() -> List[Dict[str, Any]]:
    crypto_data = _fetch_crypto_data()
    crypto_sorted = sorted(
        crypto_data,
        key=lambda x: safe_float(x.get("market_cap", 0)),
        reverse=True,
    )
    rows = []
    for coin in [c for c in crypto_sorted if c.get("symbol")][:HEATMAP_CELL_COUNT]:
        rows.append({
            "name": coin.get("symbol", ""),
            "fullName": coin.get("name", ""),
            "value": coin.get("change_24h", 0),
            "marketCap": coin.get("market_cap", 0),
            "volume": coin.get("volume_24h", 0),
            "price": coin.get("price", 0),
        })
    return _cap_heatmap_rows(rows)


def _fetch_us_stocks() -> List[Dict[str, Any]]:
    data = get_cached("us_stock_heatmap_prices")
    if not data or len(data) < HEATMAP_CELL_COUNT:
        data = fetch_local_stock_opportunity_prices("USStock", limit=HEATMAP_CELL_COUNT, fast=True)
        if data:
            set_cached("us_stock_heatmap_prices", data, 300)
            set_cached("stock_opportunity_prices", data, 300)
    return _stock_rows(data)


def _fetch_hk_stocks() -> List[Dict[str, Any]]:
    data = get_cached("hk_stock_heatmap_prices")
    if not data or len(data) < HEATMAP_CELL_COUNT:
        data = fetch_local_stock_opportunity_prices("HKStock", limit=HEATMAP_CELL_COUNT, fast=True)
        if data:
            set_cached("hk_stock_heatmap_prices", data, 300)
            set_cached("hk_stock_opportunity_prices", data, 300)
    return _stock_rows(data)


def _fetch_commodities_heatmap() -> List[Dict[str, Any]]:
    commodities_data = get_cached("commodities")
    if not commodities_data or len(commodities_data) < HEATMAP_CELL_COUNT:
        commodities_data = fetch_commodities()
        if commodities_data:
            set_cached("commodities", commodities_data)
    rows = []
    for comm in (commodities_data or []):
        rows.append({
            "name": comm.get("name_cn", comm.get("name_en", "")),
            "name_cn": comm.get("name_cn", ""),
            "name_en": comm.get("name_en", ""),
            "value": comm.get("change", 0),
            "price": comm.get("price", 0),
            "unit": comm.get("unit", ""),
        })
    return _cap_heatmap_rows(rows)


def _fetch_forex_heatmap() -> List[Dict[str, Any]]:
    forex_data = get_cached("forex_pairs")
    if not forex_data or len(forex_data) < HEATMAP_CELL_COUNT:
        forex_data = fetch_forex_pairs()
        if forex_data:
            set_cached("forex_pairs", forex_data, 120)
    rows = []
    for pair in forex_data or []:
        rows.append({
            "name": pair.get("name", ""),
            "name_cn": pair.get("name_cn", pair.get("name", "")),
            "name_en": pair.get("name_en", pair.get("name", "")),
            "value": pair.get("change", 0),
            "price": pair.get("price", 0),
        })
    return _cap_heatmap_rows(rows)


def _fetch_sectors_heatmap() -> List[Dict[str, Any]]:
    cached = get_cached("heatmap_sectors")
    if cached and len(cached) >= HEATMAP_CELL_COUNT:
        return _cap_heatmap_rows(cached)

    sectors = [
        {"name": "科技", "name_en": "Technology", "etf": "XLK", "value": 0, "stocks": ["AAPL", "MSFT", "GOOGL", "NVDA", "META"]},
        {"name": "金融", "name_en": "Financials", "etf": "XLF", "value": 0, "stocks": ["JPM", "BAC", "WFC", "GS", "MS"]},
        {"name": "医疗", "name_en": "Healthcare", "etf": "XLV", "value": 0, "stocks": ["JNJ", "PFE", "UNH", "MRK", "ABBV"]},
        {"name": "消费", "name_en": "Consumer", "etf": "XLY", "value": 0, "stocks": ["AMZN", "TSLA", "HD", "NKE", "MCD"]},
        {"name": "能源", "name_en": "Energy", "etf": "XLE", "value": 0, "stocks": ["XOM", "CVX", "COP", "SLB", "EOG"]},
        {"name": "工业", "name_en": "Industrials", "etf": "XLI", "value": 0, "stocks": ["CAT", "BA", "GE", "HON", "UPS"]},
        {"name": "材料", "name_en": "Materials", "etf": "XLB", "value": 0, "stocks": ["LIN", "APD", "DD", "NEM", "FCX"]},
        {"name": "公用事业", "name_en": "Utilities", "etf": "XLU", "value": 0, "stocks": ["NEE", "DUK", "SO", "D", "AEP"]},
        {"name": "房地产", "name_en": "Real Estate", "etf": "XLRE", "value": 0, "stocks": ["AMT", "PLD", "CCI", "EQIX", "SPG"]},
        {"name": "通信", "name_en": "Communication", "etf": "XLC", "value": 0, "stocks": ["GOOGL", "META", "DIS", "NFLX", "VZ"]},
        {"name": "半导体", "name_en": "Semiconductor", "etf": "SMH", "value": 0, "stocks": ["NVDA", "TSM", "AVGO", "AMD", "INTC"]},
        {"name": "生物科技", "name_en": "Biotech", "etf": "IBB", "value": 0, "stocks": ["AMGN", "GILD", "VRTX", "REGN", "BIIB"]},
    ]

    def _load_etf_values() -> List[Dict[str, Any]]:
        try:
            import yfinance as yf
            etf_symbols = [s["etf"] for s in sectors]
            tickers = yf.Tickers(" ".join(etf_symbols))
            for sector in sectors:
                try:
                    ticker = tickers.tickers.get(sector["etf"])
                    if ticker:
                        hist = ticker.history(period="2d")
                        if len(hist) >= 2:
                            prev = float(hist["Close"].iloc[-2])
                            curr = float(hist["Close"].iloc[-1])
                            if not (math.isnan(prev) or math.isnan(curr)) and prev > 0:
                                sector["value"] = round(((curr - prev) / prev) * 100, 2)
                        elif len(hist) == 1:
                            sector["value"] = 0
                except Exception:
                    pass
        except Exception as e:
            logger.debug("Failed to fetch sector ETFs: %s", e)
        return sectors

    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(_load_etf_values)
        try:
            sectors = fut.result(timeout=8)
        except Exception:
            logger.warning("Sector ETF fetch timed out for heatmap; using zero values")

    set_cached("heatmap_sectors", sectors, 300)
    return _cap_heatmap_rows(sectors)


def _fetch_indices_heatmap() -> List[Dict[str, Any]]:
    indices_data = get_cached("stock_indices") or []
    rows = []
    for idx in indices_data:
        rows.append({
            "symbol": idx.get("symbol", ""),
            "name": idx.get("name_cn", idx.get("name", "")),
            "name_cn": idx.get("name_cn", ""),
            "name_en": idx.get("name_en", ""),
            "region": idx.get("region", ""),
            "value": idx.get("change", 0),
            "price": idx.get("price", 0),
            "flag": idx.get("flag", ""),
        })
    return _cap_heatmap_rows(rows)


def generate_heatmap_data() -> Dict[str, Any]:
    """Generate heatmap data for crypto, stock sectors, forex, commodities, and indices."""
    with ThreadPoolExecutor(max_workers=6) as pool:
        fut_us = pool.submit(_fetch_us_stocks)
        fut_hk = pool.submit(_fetch_hk_stocks)
        fut_crypto = pool.submit(_build_crypto_heatmap)
        fut_comm = pool.submit(_fetch_commodities_heatmap)
        fut_forex = pool.submit(_fetch_forex_heatmap)
        fut_sectors = pool.submit(_fetch_sectors_heatmap)
        fut_indices = pool.submit(_fetch_indices_heatmap)

        return {
            "us_stocks": fut_us.result(),
            "hk_stocks": fut_hk.result(),
            "crypto": fut_crypto.result(),
            "commodities": fut_comm.result(),
            "forex": fut_forex.result(),
            "sectors": fut_sectors.result(),
            "indices": fut_indices.result(),
        }
