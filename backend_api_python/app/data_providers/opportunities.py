"""Trading opportunity scanners across markets."""
from __future__ import annotations

import time as _time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

import requests

from app.utils.logger import get_logger
from app.data_providers import get_cached, set_cached, safe_float
from app.data_providers.crypto import fetch_crypto_prices
from app.data_providers.forex import fetch_forex_pairs

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Price fetchers for opportunity scanning
# ---------------------------------------------------------------------------

def _fetch_yahoo_chart_quote(symbol: str) -> Optional[Dict[str, Any]]:
    """US spot quote via Yahoo chart API — lighter than yfinance batch calls."""
    sym = (symbol or "").strip().upper()
    if not sym:
        return None
    try:
        resp = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}",
            params={"interval": "1d", "range": "5d"},
            headers={"User-Agent": "Mozilla/5.0 (compatible; QuantDinger/1.0)"},
            timeout=10,
        )
        resp.raise_for_status()
        result = (resp.json().get("chart") or {}).get("result") or []
        if not result:
            return None
        meta = result[0].get("meta") or {}
        price = safe_float(meta.get("regularMarketPrice") or meta.get("previousClose"))
        prev = safe_float(meta.get("chartPreviousClose") or meta.get("previousClose") or price)
        if price <= 0:
            return None
        change_pct = ((price - prev) / prev * 100.0) if prev > 0 else 0.0
        return {
            "last": price,
            "changePercent": round(change_pct, 2),
            "previousClose": prev,
        }
    except Exception as e:
        logger.debug("Yahoo chart quote failed for %s: %s", sym, e)
        return None


def _fetch_stooq_us_quote(symbol: str) -> Optional[Dict[str, Any]]:
    """US spot quote via Stooq (works when Yahoo/yfinance are blocked in Docker)."""
    sym = f"{(symbol or '').strip().lower()}.us"
    if not sym or sym == ".us":
        return None
    try:
        resp = requests.get(
            "https://stooq.com/q/l/",
            params={"s": sym, "f": "sd2t2ohlcv", "h": "", "e": "csv"},
            headers={"User-Agent": "Mozilla/5.0 (compatible; QuantDinger/1.0)"},
            timeout=8,
        )
        resp.raise_for_status()
        lines = [ln for ln in resp.text.strip().splitlines() if ln and not ln.startswith("Symbol")]
        if not lines:
            return None
        parts = lines[-1].split(",")
        if len(parts) < 7:
            return None
        open_px = safe_float(parts[3])
        close_px = safe_float(parts[6])
        if close_px <= 0:
            return None
        base = open_px if open_px > 0 else close_px
        change_pct = ((close_px - open_px) / base * 100.0) if base > 0 else 0.0
        return {"last": close_px, "changePercent": round(change_pct, 2)}
    except Exception as e:
        logger.debug("Stooq quote failed for %s: %s", symbol, e)
        return None


def _fetch_single_local_stock_quote(market: str, item: Dict[str, Any], *, fast: bool = False) -> Optional[Dict[str, Any]]:
    """Fetch one US/CN/HK stock quote row."""
    from app.data_sources import DataSourceFactory
    from app.services.symbol_name import resolve_symbol_name

    m = str(market or "").strip()
    symbol = str((item or {}).get("symbol") or "").strip()
    if not symbol:
        return None

    last = 0.0
    change_pct = 0.0
    if m == "USStock":
        yahoo = _fetch_yahoo_chart_quote(symbol)
        if yahoo:
            last = safe_float(yahoo.get("last"))
            change_pct = safe_float(yahoo.get("changePercent"))
        if last <= 0:
            stooq = _fetch_stooq_us_quote(symbol)
            if stooq:
                last = safe_float(stooq.get("last"))
                change_pct = safe_float(stooq.get("changePercent"))
        if last <= 0 and not fast:
            try:
                from app.services.kline import KlineService
                row = KlineService().get_realtime_price(m, symbol)
                last = safe_float(row.get("price"))
                change_pct = row.get("changePercent")
                if change_pct is None:
                    prev_close = safe_float(row.get("previousClose"))
                    change_pct = ((last - prev_close) / prev_close * 100.0) if prev_close > 0 else 0.0
            except Exception as e:
                logger.debug("Kline realtime price failed for %s:%s: %s", m, symbol, e)
    else:
        source = DataSourceFactory.get_source(m)
        ticker = source.get_ticker(symbol) or {}
        last = safe_float(ticker.get("last") or ticker.get("close") or ticker.get("price"))
        change_pct = ticker.get("changePercent")
        if change_pct is None:
            prev_close = safe_float(ticker.get("previousClose"))
            change_pct = ((last - prev_close) / prev_close * 100.0) if prev_close > 0 else 0.0

    if last <= 0:
        return None
    return {
        "symbol": symbol,
        "name": (item.get("name") or resolve_symbol_name(m, symbol) or symbol).strip(),
        "price": round(last, 4 if m != "USStock" else 2),
        "change": round(safe_float(change_pct), 2),
        "market": m,
    }


def fetch_stock_opportunity_prices() -> List[Dict[str, Any]]:
    """Fetch popular US stock prices for opportunity scanning."""
    return fetch_local_stock_opportunity_prices("USStock", limit=15)


def fetch_local_stock_opportunity_prices(market: str, limit: int = 15, *, fast: bool = False) -> List[Dict[str, Any]]:
    """Fetch US/CN/HK stock prices for opportunity scanning and heatmaps."""
    m = str(market or "").strip()
    if m not in ("CNStock", "HKStock", "USStock"):
        return []

    _FALLBACK_SYMBOLS = {
        "USStock": [
            {"symbol": "AAPL", "name": "Apple"}, {"symbol": "MSFT", "name": "Microsoft"},
            {"symbol": "GOOGL", "name": "Alphabet"}, {"symbol": "AMZN", "name": "Amazon"},
            {"symbol": "TSLA", "name": "Tesla"}, {"symbol": "NVDA", "name": "NVIDIA"},
            {"symbol": "META", "name": "Meta"}, {"symbol": "NFLX", "name": "Netflix"},
            {"symbol": "AMD", "name": "AMD"}, {"symbol": "CRM", "name": "Salesforce"},
            {"symbol": "COIN", "name": "Coinbase"}, {"symbol": "JPM", "name": "JPMorgan"},
            {"symbol": "V", "name": "Visa"}, {"symbol": "INTC", "name": "Intel"},
            {"symbol": "PLTR", "name": "Palantir"}, {"symbol": "ORCL", "name": "Oracle"},
            {"symbol": "QCOM", "name": "Qualcomm"},
        ],
        "CNStock": [
            {"symbol": "600519", "name": "贵州茅台"}, {"symbol": "000001", "name": "平安银行"},
            {"symbol": "300750", "name": "宁德时代"}, {"symbol": "601318", "name": "中国平安"},
            {"symbol": "600036", "name": "招商银行"}, {"symbol": "002594", "name": "比亚迪"},
            {"symbol": "600276", "name": "恒瑞医药"}, {"symbol": "601899", "name": "紫金矿业"},
            {"symbol": "000858", "name": "五粮液"}, {"symbol": "000333", "name": "美的集团"},
            {"symbol": "600900", "name": "长江电力"}, {"symbol": "601398", "name": "工商银行"},
            {"symbol": "600030", "name": "中信证券"}, {"symbol": "300059", "name": "东方财富"},
            {"symbol": "603259", "name": "药明康德"}, {"symbol": "002475", "name": "立讯精密"},
            {"symbol": "600887", "name": "伊利股份"}, {"symbol": "000568", "name": "泸州老窖"},
            {"symbol": "601012", "name": "隆基绿能"}, {"symbol": "002415", "name": "海康威视"},
        ],
        "HKStock": [
            {"symbol": "00700", "name": "腾讯控股"}, {"symbol": "09988", "name": "阿里巴巴-W"},
            {"symbol": "03690", "name": "美团-W"}, {"symbol": "01810", "name": "小米集团-W"},
            {"symbol": "01299", "name": "友邦保险"}, {"symbol": "00939", "name": "建设银行"},
            {"symbol": "02318", "name": "中国平安"}, {"symbol": "09618", "name": "京东集团-SW"},
            {"symbol": "09888", "name": "百度集团-SW"}, {"symbol": "01024", "name": "快手-W"},
            {"symbol": "02015", "name": "理想汽车-W"}, {"symbol": "09868", "name": "小鹏汽车-W"},
            {"symbol": "00388", "name": "香港交易所"}, {"symbol": "02269", "name": "药明生物"},
            {"symbol": "00005", "name": "汇丰控股"}, {"symbol": "01398", "name": "工商银行"},
            {"symbol": "00883", "name": "中国海洋石油"},
        ],
    }

    try:
        from app.data.market_symbols_seed import get_hot_symbols

        symbols = get_hot_symbols(m, limit=max(int(limit or 15), 1)) or []
        fallback = _FALLBACK_SYMBOLS.get(m, [])
        seen = set()
        merged = []
        for item in list(symbols) + list(fallback):
            sym = str((item or {}).get("symbol") or "").strip()
            if not sym or sym in seen:
                continue
            seen.add(sym)
            merged.append(item)
        # Pull a few extra symbols so partial upstream failures still yield `limit` rows.
        fetch_count = max(int(limit or 15), 1) + 4
        items = merged[:fetch_count]
        if not items:
            return []

        result: List[Dict[str, Any]] = []
        if m in ("USStock", "HKStock", "CNStock") and len(items) > 1:
            workers = min(8, len(items))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [pool.submit(_fetch_single_local_stock_quote, m, item, fast=fast) for item in items]
                for fut in as_completed(futures):
                    try:
                        row = fut.result()
                        if row:
                            result.append(row)
                    except Exception as e:
                        logger.debug("Parallel stock quote failed for %s: %s", m, e)
            return result[:max(int(limit or 15), 1)]

        for item in items:
            try:
                row = _fetch_single_local_stock_quote(m, item, fast=fast)
                if row:
                    result.append(row)
            except Exception as e:
                logger.debug("Failed to fetch %s opportunity price %s: %s", m, item.get("symbol"), e)
        return result[:max(int(limit or 15), 1)]
    except Exception as e:
        logger.error("Failed to fetch %s opportunity prices: %s", m, e)
        return []


# ---------------------------------------------------------------------------
# Opportunity analysers
# ---------------------------------------------------------------------------

def analyze_opportunities_crypto(opportunities: list):
    """Scan crypto market for trading opportunities."""
    crypto_data = get_cached("crypto_prices")
    if not crypto_data:
        crypto_data = fetch_crypto_prices()
        if crypto_data:
            set_cached("crypto_prices", crypto_data)
    if not crypto_data:
        logger.warning("analyze_opportunities_crypto: No crypto data available")
        return

    for coin in (crypto_data or [])[:20]:
        change = safe_float(coin.get("change_24h", 0))
        change_7d = safe_float(coin.get("change_7d", 0))
        symbol = coin.get("symbol", "")
        name = coin.get("name", "")
        price = safe_float(coin.get("price", 0))

        signal = strength = reason = None
        impact = "neutral"

        if change > 15:
            signal, strength = "overbought", "strong"
            reason = f"24h涨幅{change:.1f}%，7日涨幅{change_7d:.1f}%，短期超买风险"
            impact = "bearish"
        elif change > 5:
            signal, strength = "bullish_momentum", "medium"
            reason = f"24h涨幅{change:.1f}%，上涨动能强劲"
            impact = "bullish"
        elif change < -15:
            signal, strength = "oversold", "strong"
            reason = f"24h跌幅{abs(change):.1f}%，可能超卖反弹"
            impact = "bullish"
        elif change < -5:
            signal, strength = "bearish_momentum", "medium"
            reason = f"24h跌幅{abs(change):.1f}%，下跌趋势明显"
            impact = "bearish"

        if signal:
            opportunities.append({
                "symbol": symbol, "name": name, "price": price,
                "change_24h": change, "change_7d": change_7d,
                "signal": signal, "strength": strength, "reason": reason,
                "impact": impact, "market": "Crypto", "timestamp": int(_time.time()),
            })


def analyze_opportunities_stocks(opportunities: list):
    """Scan US stocks for trading opportunities."""
    stock_data = get_cached("stock_opportunity_prices")
    if not stock_data:
        stock_data = fetch_stock_opportunity_prices()
        if stock_data:
            set_cached("stock_opportunity_prices", stock_data, 3600)
    if not stock_data:
        logger.warning("analyze_opportunities_stocks: No stock data available")
        return

    for stock in (stock_data or []):
        change = safe_float(stock.get("change", 0))
        symbol, name, price = stock.get("symbol", ""), stock.get("name", ""), safe_float(stock.get("price", 0))

        signal = strength = reason = None
        impact = "neutral"

        if change > 5:
            signal, strength = "overbought", "strong"
            reason = f"日涨幅{change:.1f}%，短期涨幅较大，注意回调风险"; impact = "bearish"
        elif change > 2:
            signal, strength = "bullish_momentum", "medium"
            reason = f"日涨幅{change:.1f}%，上涨动能强劲"; impact = "bullish"
        elif change < -5:
            signal, strength = "oversold", "strong"
            reason = f"日跌幅{abs(change):.1f}%，可能超卖反弹"; impact = "bullish"
        elif change < -2:
            signal, strength = "bearish_momentum", "medium"
            reason = f"日跌幅{abs(change):.1f}%，下跌趋势明显"; impact = "bearish"

        if signal:
            opportunities.append({
                "symbol": symbol, "name": name, "price": price,
                "change_24h": change, "signal": signal, "strength": strength,
                "reason": reason, "impact": impact, "market": "USStock",
                "timestamp": int(_time.time()),
            })


def analyze_opportunities_local_stocks(opportunities: list, market: str):
    """Scan CN/HK stocks for trading opportunities."""
    m = str(market or "").strip()
    if m not in ("CNStock", "HKStock"):
        return

    cache_key = "cn_stock_opportunity_prices" if m == "CNStock" else "hk_stock_opportunity_prices"
    stock_data = get_cached(cache_key)
    if not stock_data:
        stock_data = fetch_local_stock_opportunity_prices(m, limit=25)
        if stock_data:
            set_cached(cache_key, stock_data, 3600)
    if not stock_data:
        logger.warning("analyze_opportunities_local_stocks: No %s data available", m)
        return

    if m == "CNStock":
        strong_th, medium_th, mild_th = 5.0, 2.0, 1.0
    else:
        strong_th, medium_th, mild_th = 4.0, 1.5, 0.8
    market_cn = "A股" if m == "CNStock" else "港股"

    for stock in stock_data:
        change = safe_float(stock.get("change", 0))
        symbol, name, price = stock.get("symbol", ""), stock.get("name", ""), safe_float(stock.get("price", 0))
        abs_change = abs(change)

        signal = strength = reason = None
        impact = "neutral"

        if change > strong_th:
            signal, strength = "overbought", "strong"
            reason = f"{market_cn}日涨幅{change:.1f}%，短期涨幅较大，注意回调风险"; impact = "bearish"
        elif change > medium_th:
            signal, strength = "bullish_momentum", "medium"
            reason = f"{market_cn}日涨幅{change:.1f}%，上涨动能较强"; impact = "bullish"
        elif change > mild_th:
            signal, strength = "bullish_momentum", "weak"
            reason = f"{market_cn}日涨幅{change:.1f}%，温和上涨"; impact = "bullish"
        elif change < -strong_th:
            signal, strength = "oversold", "strong"
            reason = f"{market_cn}日跌幅{abs_change:.1f}%，可能超卖反弹"; impact = "bullish"
        elif change < -medium_th:
            signal, strength = "bearish_momentum", "medium"
            reason = f"{market_cn}日跌幅{abs_change:.1f}%，下跌趋势明显"; impact = "bearish"
        elif change < -mild_th:
            signal, strength = "bearish_momentum", "weak"
            reason = f"{market_cn}日跌幅{abs_change:.1f}%，温和下跌"; impact = "bearish"
        elif abs_change <= mild_th:
            signal, strength = "consolidation", "weak"
            reason = f"{market_cn}{name}窄幅震荡({change:+.1f}%)，等待方向选择"; impact = "neutral"

        if signal:
            opportunities.append({
                "symbol": symbol, "name": name, "price": price,
                "change_24h": change, "signal": signal, "strength": strength,
                "reason": reason, "impact": impact, "market": m,
                "timestamp": int(_time.time()),
            })


def analyze_opportunities_forex(opportunities: list):
    """Scan forex pairs for trading opportunities."""
    forex_data = get_cached("forex_pairs")
    if not forex_data:
        forex_data = fetch_forex_pairs()
        if forex_data:
            set_cached("forex_pairs", forex_data, 3600)
    if not forex_data:
        logger.warning("analyze_opportunities_forex: No forex data available")
        return

    for pair in (forex_data or []):
        change = safe_float(pair.get("change", 0))
        symbol = pair.get("symbol", pair.get("name", ""))
        name = pair.get("name_cn", pair.get("name", ""))
        price = safe_float(pair.get("price", 0))

        signal = strength = reason = None
        impact = "neutral"

        if change > 1.5:
            signal, strength = "overbought", "strong"
            reason = f"日涨幅{change:.2f}%，汇率波动剧烈，注意回调"; impact = "bearish"
        elif change > 0.5:
            signal, strength = "bullish_momentum", "medium"
            reason = f"日涨幅{change:.2f}%，上涨动能较强"; impact = "bullish"
        elif change < -1.5:
            signal, strength = "oversold", "strong"
            reason = f"日跌幅{abs(change):.2f}%，汇率波动剧烈，可能反弹"; impact = "bullish"
        elif change < -0.5:
            signal, strength = "bearish_momentum", "medium"
            reason = f"日跌幅{abs(change):.2f}%，下跌趋势明显"; impact = "bearish"

        if signal:
            opportunities.append({
                "symbol": symbol, "name": name, "price": price,
                "change_24h": change, "signal": signal, "strength": strength,
                "reason": reason, "impact": impact, "market": "Forex",
                "timestamp": int(_time.time()),
            })

