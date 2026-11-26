"""Local symbol master synchronization.

The app searches `qd_market_symbols` first. This module keeps that table useful
without requiring users to remember ticker codes.
"""

from __future__ import annotations

import csv
import io
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence

import requests
import urllib3

from app.data_sources.tencent import normalize_hk_code
from app.services.market.instrument_products import classify_instrument_product
from app.services.symbol_name import normalize_crypto_symbol
from app.utils.db import get_db_connection
from app.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class SymbolMasterRow:
    market: str
    symbol: str
    name: str
    exchange: str = ""
    currency: str = ""
    market_type: str = "spot"
    instrument_id: str = ""
    settle_currency: str = ""
    asset_class: str = ""
    product_type: str = "crypto"
    api_family: str = "spot"
    underlying_market: str = ""
    underlying_symbol: str = ""
    product_meta: Optional[Dict[str, object]] = None
    board_lot: Optional[int] = None
    rules_as_of: Optional[str] = None
    rules_source: Optional[str] = None


STATIC_MARKET_ROWS = [
    SymbolMasterRow("Crypto", "BTC/USDT", "Bitcoin", "binance", "USDT"),
    SymbolMasterRow("Crypto", "ETH/USDT", "Ethereum", "binance", "USDT"),
    SymbolMasterRow("Crypto", "BNB/USDT", "BNB", "binance", "USDT"),
    SymbolMasterRow("Crypto", "SOL/USDT", "Solana", "binance", "USDT"),
    SymbolMasterRow("Crypto", "XRP/USDT", "XRP", "binance", "USDT"),
    SymbolMasterRow("Crypto", "DOGE/USDT", "Dogecoin", "binance", "USDT"),
    SymbolMasterRow("Crypto", "ADA/USDT", "Cardano", "binance", "USDT"),
    SymbolMasterRow("Crypto", "AVAX/USDT", "Avalanche", "binance", "USDT"),
    SymbolMasterRow("Crypto", "LINK/USDT", "Chainlink", "binance", "USDT"),
    SymbolMasterRow("Crypto", "DOT/USDT", "Polkadot", "binance", "USDT"),
    SymbolMasterRow("Crypto", "TRX/USDT", "TRON", "binance", "USDT"),
    SymbolMasterRow("Crypto", "TON/USDT", "Toncoin", "binance", "USDT"),
    SymbolMasterRow("Crypto", "LTC/USDT", "Litecoin", "binance", "USDT"),
    SymbolMasterRow("Crypto", "BCH/USDT", "Bitcoin Cash", "binance", "USDT"),
    SymbolMasterRow("Crypto", "UNI/USDT", "Uniswap", "binance", "USDT"),
    SymbolMasterRow("Crypto", "AAVE/USDT", "Aave", "binance", "USDT"),
    SymbolMasterRow("Crypto", "MATIC/USDT", "Polygon", "binance", "USDT"),
    SymbolMasterRow("Crypto", "NEAR/USDT", "NEAR Protocol", "binance", "USDT"),
    SymbolMasterRow("Crypto", "APT/USDT", "Aptos", "binance", "USDT"),
    SymbolMasterRow("Crypto", "ARB/USDT", "Arbitrum", "binance", "USDT"),
    SymbolMasterRow("Crypto", "OP/USDT", "Optimism", "binance", "USDT"),
    SymbolMasterRow("Crypto", "FIL/USDT", "Filecoin", "binance", "USDT"),
    SymbolMasterRow("Crypto", "ETC/USDT", "Ethereum Classic", "binance", "USDT"),
    SymbolMasterRow("Crypto", "ATOM/USDT", "Cosmos", "binance", "USDT"),
    SymbolMasterRow("Crypto", "INJ/USDT", "Injective", "binance", "USDT"),
    SymbolMasterRow("Crypto", "SUI/USDT", "Sui", "binance", "USDT"),
    SymbolMasterRow("Crypto", "SEI/USDT", "Sei", "binance", "USDT"),
    SymbolMasterRow("Crypto", "PEPE/USDT", "Pepe", "binance", "USDT"),
    SymbolMasterRow("Crypto", "SHIB/USDT", "Shiba Inu", "binance", "USDT"),
    SymbolMasterRow("Crypto", "WLD/USDT", "Worldcoin", "binance", "USDT"),
    SymbolMasterRow("Forex", "XAUUSD", "Gold Spot", "TwelveData", "USD"),
    SymbolMasterRow("Forex", "XAGUSD", "Silver Spot", "TwelveData", "USD"),
    SymbolMasterRow("Forex", "EURUSD", "Euro / US Dollar", "TwelveData", "USD"),
    SymbolMasterRow("Forex", "GBPUSD", "British Pound / US Dollar", "TwelveData", "USD"),
    SymbolMasterRow("Forex", "USDJPY", "US Dollar / Japanese Yen", "TwelveData", "JPY"),
    SymbolMasterRow("Forex", "AUDUSD", "Australian Dollar / US Dollar", "TwelveData", "USD"),
    SymbolMasterRow("Forex", "USDCAD", "US Dollar / Canadian Dollar", "TwelveData", "CAD"),
    SymbolMasterRow("Forex", "USDCHF", "US Dollar / Swiss Franc", "TwelveData", "CHF"),
    SymbolMasterRow("Forex", "NZDUSD", "New Zealand Dollar / US Dollar", "TwelveData", "USD"),
    SymbolMasterRow("Forex", "GBPJPY", "British Pound / Japanese Yen", "TwelveData", "JPY"),
    SymbolMasterRow("Forex", "EURJPY", "Euro / Japanese Yen", "TwelveData", "JPY"),
    SymbolMasterRow("Forex", "EURGBP", "Euro / British Pound", "TwelveData", "GBP"),
    SymbolMasterRow("Forex", "AUDNZD", "Australian Dollar / New Zealand Dollar", "TwelveData", "NZD"),
    SymbolMasterRow("Forex", "USDCNH", "US Dollar / Offshore Chinese Yuan", "TwelveData", "CNH"),
    SymbolMasterRow("Futures", "GC", "Gold Futures", "CME", "USD"),
    SymbolMasterRow("Futures", "SI", "Silver Futures", "CME", "USD"),
    SymbolMasterRow("Futures", "CL", "Crude Oil WTI Futures", "NYMEX", "USD"),
    SymbolMasterRow("Futures", "NG", "Natural Gas Futures", "NYMEX", "USD"),
    SymbolMasterRow("Futures", "HG", "Copper Futures", "COMEX", "USD"),
    SymbolMasterRow("Futures", "PL", "Platinum Futures", "NYMEX", "USD"),
    SymbolMasterRow("Futures", "ES", "E-mini S&P 500 Futures", "CME", "USD"),
    SymbolMasterRow("Futures", "NQ", "E-mini Nasdaq 100 Futures", "CME", "USD"),
    SymbolMasterRow("Futures", "YM", "E-mini Dow Futures", "CBOT", "USD"),
    SymbolMasterRow("Futures", "RTY", "E-mini Russell 2000 Futures", "CME", "USD"),
    SymbolMasterRow("Futures", "ZC", "Corn Futures", "CBOT", "USD"),
    SymbolMasterRow("Futures", "ZS", "Soybean Futures", "CBOT", "USD"),
    SymbolMasterRow("Futures", "ZW", "Wheat Futures", "CBOT", "USD"),
    SymbolMasterRow("MOEX", "SBER", "Sberbank", "MOEX", "RUB"),
    SymbolMasterRow("MOEX", "GAZP", "Gazprom", "MOEX", "RUB"),
    SymbolMasterRow("MOEX", "LKOH", "Lukoil", "MOEX", "RUB"),
    SymbolMasterRow("MOEX", "ROSN", "Rosneft", "MOEX", "RUB"),
    SymbolMasterRow("MOEX", "GMKN", "Norilsk Nickel", "MOEX", "RUB"),
    SymbolMasterRow("MOEX", "NVTK", "Novatek", "MOEX", "RUB"),
    SymbolMasterRow("MOEX", "TATN", "Tatneft", "MOEX", "RUB"),
    SymbolMasterRow("MOEX", "YDEX", "Yandex", "MOEX", "RUB"),
    SymbolMasterRow("MOEX", "VTBR", "VTB Bank", "MOEX", "RUB"),
    SymbolMasterRow("MOEX", "MGNT", "Magnit", "MOEX", "RUB"),
    SymbolMasterRow("MOEX", "SNGS", "Surgutneftegas", "MOEX", "RUB"),
    SymbolMasterRow("MOEX", "PLZL", "Polyus", "MOEX", "RUB"),
]


def _clean_text(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    suspicious = any(ch in text for ch in ("\u9a9e", "\u95be", "\u934f", "\u6d93", "\u6df1", "\u7eee", "\u60f0"))
    if not suspicious:
        return text
    for enc in ("gbk", "cp936", "latin1"):
        try:
            fixed = text.encode(enc).decode("utf-8")
            if fixed and fixed != text:
                return fixed
        except Exception:
            pass
    return text


def _clean_symbol(value: object) -> str:
    return _clean_text(value).upper()


def _unique_rows(rows: Iterable[SymbolMasterRow]) -> List[SymbolMasterRow]:
    out: List[SymbolMasterRow] = []
    seen = set()
    for row in rows:
        key = (row.market, row.symbol, row.exchange, row.market_type, row.instrument_id)
        if not row.market or not row.symbol or not row.name or key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _static_rows(market: str) -> List[SymbolMasterRow]:
    return [row for row in STATIC_MARKET_ROWS if row.market == market]


def _df_records(df) -> list:
    if df is None:
        return []
    try:
        return df.to_dict("records")
    except Exception:
        return []


def fetch_cn_stock_symbols() -> List[SymbolMasterRow]:
    """Fetch A-share code/name rows from AkShare."""
    import akshare as ak  # type: ignore

    rows = []
    for item in _df_records(ak.stock_info_a_code_name()):
        symbol = _clean_symbol(item.get("code") or item.get("代码"))
        name = _clean_text(item.get("name") or item.get("名称"))
        if re.fullmatch(r"\d{6}", symbol) and name:
            rows.append(SymbolMasterRow("CNStock", symbol, name, "CN", "CNY"))
    return _unique_rows(rows)


def fetch_hk_stock_symbols() -> List[SymbolMasterRow]:
    """Fetch Hong Kong stock code/name rows from HKEX, with AkShare fallback."""
    rows = fetch_hk_stock_symbols_hkex()
    if rows:
        return rows
    return fetch_hk_stock_symbols_akshare()


def fetch_hk_stock_symbols_hkex() -> List[SymbolMasterRow]:
    from app.services.market.hk_board_lots import _download, SOURCE
    data = _download()
    return [SymbolMasterRow("HKStock", row["symbol"], row["name"], "HKEX", row["currency"],
                            asset_class=row["asset_class"], board_lot=row["board_lot"],
                            rules_as_of=data["as_of"], rules_source=SOURCE)
            for row in data["records"]]


def fetch_hk_stock_symbols_akshare() -> List[SymbolMasterRow]:
    import akshare as ak  # type: ignore

    rows = []
    for item in _df_records(ak.stock_hk_spot_em()):
        raw_symbol = _clean_symbol(item.get("代码") or item.get("code") or item.get("symbol"))
        name = _clean_text(item.get("名称") or item.get("name"))
        digits = re.sub(r"[^0-9]", "", raw_symbol)
        if digits and name:
            symbol = normalize_hk_code(digits).replace("HK", "")
            rows.append(SymbolMasterRow("HKStock", symbol, name, "HKEX", "HKD"))
    return _unique_rows(rows)


def _fetch_nasdaq_trader_file(url: str) -> List[Dict[str, str]]:
    resp = requests.get(url, timeout=20)
    resp.raise_for_status()
    text = resp.text
    lines = [line for line in text.splitlines() if line and not line.startswith("File Creation Time")]
    return list(csv.DictReader(io.StringIO("\n".join(lines)), delimiter="|"))


def fetch_us_stock_symbols() -> List[SymbolMasterRow]:
    """Fetch US listed equities and ETFs from Nasdaq Trader symbol directories."""
    rows: List[SymbolMasterRow] = []

    nasdaq_rows = _fetch_nasdaq_trader_file("https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt")
    for item in nasdaq_rows:
        symbol = _clean_symbol(item.get("Symbol"))
        name = _clean_text(item.get("Security Name"))
        test_issue = _clean_symbol(item.get("Test Issue"))
        etf = _clean_symbol(item.get("ETF"))
        if symbol and name and test_issue != "Y":
            rows.append(
                SymbolMasterRow(
                    "USStock",
                    symbol,
                    name,
                    "NASDAQ",
                    "USD",
                    asset_class="etf" if etf == "Y" else "equity",
                )
            )

    other_rows = _fetch_nasdaq_trader_file("https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt")
    exchange_map = {
        "A": "NYSE American",
        "N": "NYSE",
        "P": "NYSE Arca",
        "Z": "Cboe BZX",
        "V": "IEX",
    }
    for item in other_rows:
        symbol = _clean_symbol(item.get("ACT Symbol"))
        name = _clean_text(item.get("Security Name"))
        exchange = exchange_map.get(_clean_symbol(item.get("Exchange")), _clean_symbol(item.get("Exchange")))
        test_issue = _clean_symbol(item.get("Test Issue"))
        etf = _clean_symbol(item.get("ETF"))
        if symbol and name and test_issue != "Y":
            rows.append(
                SymbolMasterRow(
                    "USStock",
                    symbol,
                    name,
                    exchange,
                    "USD",
                    asset_class="etf" if etf == "Y" else "equity",
                )
            )

    return _unique_rows(rows)


def fetch_crypto_symbols_with_diagnostics():
    """Fetch active USDT instruments and return per-context diagnostics."""
    from app.config.data_sources import CryptoPublicConfig
    from app.data_sources.crypto import PUBLIC_KLINE_EXCHANGE_IDS
    from app.data_sources.native_crypto import create_native_crypto_client
    from app.services.market.symbol_search import _classify_asset

    known_equity_symbols = _load_known_equity_symbols()

    def fetch_context(exchange_id: str, market_type: str):
        context_rows: List[SymbolMasterRow] = []
        try:
            exchange = create_native_crypto_client(
                exchange_id,
                market_type,
                timeout_ms=max(5000, min(int(CryptoPublicConfig.TIMEOUT or 10000), 15000)),
                proxy=CryptoPublicConfig.PROXY,
            )
            _load_native_markets_with_retry(exchange)
            for symbol, info in exchange.markets.items():
                is_target = bool(info.get("spot")) if market_type == "spot" else bool(info.get("swap"))
                quote = _clean_symbol(info.get("quote"))
                base = _clean_symbol(info.get("base"))
                if not info.get("active") or not is_target or quote != "USDT" or not base:
                    continue
                normalized_symbol = normalize_crypto_symbol(symbol)
                instrument_id = _clean_text(info.get("id") or symbol)
                profile = classify_instrument_product(
                    info,
                    exchange_id=exchange_id,
                    market_type=market_type,
                    symbol=normalized_symbol,
                    instrument_id=instrument_id,
                    known_equity_symbols=known_equity_symbols,
                )
                context_rows.append(
                    SymbolMasterRow(
                        "Crypto",
                        normalized_symbol,
                        _clean_text(info.get("displayName") or info.get("name") or base),
                        exchange_id,
                        "USDT",
                        market_type,
                        instrument_id,
                        _clean_symbol(info.get("settle") or quote),
                        profile.asset_class,
                        profile.product_type,
                        profile.api_family,
                        profile.underlying_market,
                        profile.underlying_symbol,
                        profile.product_meta,
                    )
                )
            if exchange_id == "bitget" and market_type == "spot":
                try:
                    context_rows.extend(_fetch_bitget_reality_symbol_rows())
                    context_rows = _unique_rows(context_rows)
                except Exception as direct_error:
                    logger.warning("Bitget Reality catalog unavailable: %s", direct_error)
            if exchange_id == "gate" and market_type == "spot":
                try:
                    context_rows.extend(_fetch_gate_stock_symbol_rows())
                    context_rows = _unique_rows(context_rows)
                except Exception as direct_error:
                    logger.warning("Gate stock catalog unavailable: %s", direct_error)
            context_rows = _unique_rows(context_rows)
            return context_rows, {
                "exchange": exchange_id,
                "market_type": market_type,
                "ok": True,
                "rows": len(context_rows),
                "source": "native_public_api",
            }
        except Exception as e:
            if exchange_id == "bitget" and market_type == "spot":
                try:
                    context_rows = _fetch_bitget_reality_symbol_rows()
                    return context_rows, {
                        "exchange": exchange_id,
                        "market_type": market_type,
                        "ok": True,
                        "rows": len(context_rows),
                        "source": "official_public_api",
                        "fallback": True,
                        "primary_error": str(e)[:500],
                    }
                except Exception as fallback_error:
                    e = RuntimeError(f"{e}; official fallback failed: {fallback_error}")
            if exchange_id == "gate" and market_type == "spot":
                try:
                    context_rows = _fetch_gate_stock_symbol_rows()
                    return context_rows, {
                        "exchange": exchange_id,
                        "market_type": market_type,
                        "ok": True,
                        "rows": len(context_rows),
                        "source": "official_stock_api",
                        "fallback": True,
                        "primary_error": str(e)[:500],
                    }
                except Exception as fallback_error:
                    e = RuntimeError(f"{e}; official stock fallback failed: {fallback_error}")
            if exchange_id == "okx":
                try:
                    context_rows = _fetch_okx_public_symbol_rows(market_type, _classify_asset)
                    return context_rows, {
                        "exchange": exchange_id,
                        "market_type": market_type,
                        "ok": True,
                        "rows": len(context_rows),
                        "source": "official_public_api",
                        "fallback": True,
                        "primary_error": str(e)[:500],
                    }
                except Exception as fallback_error:
                    e = RuntimeError(f"{e}; official fallback failed: {fallback_error}")
            logger.warning("crypto catalog unavailable exchange=%s type=%s: %s", exchange_id, market_type, e)
            return [], {
                "exchange": exchange_id,
                "market_type": market_type,
                "ok": False,
                "rows": 0,
                "error": str(e),
            }

    rows: List[SymbolMasterRow] = []
    contexts = []
    work = [(exchange_id, market_type) for exchange_id in PUBLIC_KLINE_EXCHANGE_IDS for market_type in ("spot", "swap")]
    worker_count = min(6, len(work))
    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="symbol-catalog") as executor:
        futures = {
            executor.submit(fetch_context, exchange_id, market_type): (exchange_id, market_type)
            for exchange_id, market_type in work
        }
        for future in as_completed(futures):
            context_rows, diagnostic = future.result()
            rows.extend(context_rows)
            contexts.append(diagnostic)
    order = {exchange_id: index for index, exchange_id in enumerate(PUBLIC_KLINE_EXCHANGE_IDS)}
    contexts.sort(key=lambda item: (order.get(item["exchange"], len(order)), item["market_type"]))
    return _unique_rows(rows), contexts


def _load_known_equity_symbols() -> set[str]:
    symbols: set[str] = {"SPCX"}
    try:
        with get_db_connection() as db:
            cur = db.cursor()
            try:
                cur.execute(
                    """
                    SELECT symbol
                      FROM qd_market_symbols
                     WHERE market IN ('USStock', 'HKStock') AND is_active = 1
                    """
                )
                symbols.update(
                    _clean_symbol(row.get("symbol"))
                    for row in cur.fetchall()
                    if _clean_symbol(row.get("symbol"))
                )
            finally:
                cur.close()
    except Exception as exc:
        logger.debug("Stored equity catalog unavailable for crypto product classification: %s", exc)
    if len(symbols) > 100:
        return symbols
    try:
        symbols.update(row.symbol for row in fetch_us_stock_symbols())
    except Exception as exc:
        logger.warning("US equity reference catalog unavailable during crypto sync: %s", exc)
    return symbols


def reclassify_stored_equity_products() -> int:
    """Upgrade stored equity rows after the product contract changes.

    Binance bStocks need the local equity reference catalog because their
    public spot symbols only expose the venue ``B`` suffix. Older catalog
    rows from every venue may already carry ``asset_class=equity`` while
    retaining the legacy generic product fields, so those rows are upgraded
    without requiring another successful remote catalog request.
    """
    known_equity_symbols = _load_known_equity_symbols()
    with get_db_connection() as db:
        cur = db.cursor()
        try:
            cur.execute(
                """
                SELECT exchange, market_type, symbol, instrument_id, asset_class
                  FROM qd_market_symbols
                 WHERE market = 'Crypto'
                   AND is_active = 1
                   AND product_type = 'crypto'
                   AND (
                       asset_class = 'equity'
                       OR (LOWER(exchange) = 'binance' AND market_type = 'spot')
                   )
                """
            )
            upgraded = 0
            for row in cur.fetchall():
                exchange = _clean_text(row.get("exchange")).lower()
                market_type = _clean_text(row.get("market_type")).lower()
                symbol = _clean_symbol(row.get("symbol"))
                instrument_id = _clean_text(row.get("instrument_id"))
                base = symbol.split("/", 1)[0]
                raw = {"symbol": instrument_id}
                if _clean_text(row.get("asset_class")).lower() == "equity":
                    raw["assetClass"] = "equity"
                profile = classify_instrument_product(
                    {"base": base, "info": raw},
                    exchange_id=exchange,
                    market_type=market_type,
                    symbol=symbol,
                    instrument_id=instrument_id,
                    known_equity_symbols=known_equity_symbols,
                )
                if profile.product_type == "crypto":
                    continue
                cur.execute(
                    """
                    UPDATE qd_market_symbols
                       SET asset_class = ?, product_type = ?, api_family = ?,
                           underlying_market = ?, underlying_symbol = ?,
                           metadata_updated_at = NOW()
                     WHERE market = 'Crypto' AND LOWER(exchange) = ?
                       AND market_type = ? AND symbol = ? AND instrument_id = ?
                    """,
                    (
                        profile.asset_class,
                        profile.product_type,
                        profile.api_family,
                        profile.underlying_market,
                        profile.underlying_symbol,
                        exchange,
                        market_type,
                        symbol,
                        instrument_id,
                    ),
                )
                upgraded += int(cur.rowcount or 0)
            db.commit()
            return upgraded
        except Exception:
            db.rollback()
            raise
        finally:
            cur.close()


def _fetch_bitget_reality_symbol_rows() -> List[SymbolMasterRow]:
    response = requests.get(
        "https://api.bitget.com/api/v3/market/instruments",
        params={"category": "SPOT"},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data") if isinstance(payload, dict) else None
    items = data if isinstance(data, list) else data.get("list", []) if isinstance(data, dict) else []
    rows: List[SymbolMasterRow] = []
    for raw in items:
        if not isinstance(raw, dict) or not _bitget_reality_marker(raw):
            continue
        base = _clean_symbol(raw.get("baseCoin") or raw.get("baseCurrency"))
        quote = _clean_symbol(raw.get("quoteCoin") or raw.get("quoteCurrency"))
        instrument_id = _clean_text(raw.get("symbol"))
        if not base or quote != "USDT" or not instrument_id:
            continue
        status = str(raw.get("status") or "").strip().lower()
        if status and status not in {"online", "normal", "trading", "live"}:
            continue
        symbol = normalize_crypto_symbol(f"{base}/{quote}")
        profile = classify_instrument_product(
            {"symbol": symbol, "id": instrument_id, "base": base, "quote": quote, "info": raw},
            exchange_id="bitget",
            market_type="spot",
            symbol=symbol,
            instrument_id=instrument_id,
        )
        if profile.product_type == "crypto":
            continue
        rows.append(
            SymbolMasterRow(
                "Crypto",
                symbol,
                _clean_text(raw.get("displayName") or raw.get("symbolName") or base),
                "bitget",
                quote,
                "spot",
                instrument_id,
                quote,
                profile.asset_class,
                profile.product_type,
                profile.api_family,
                profile.underlying_market,
                profile.underlying_symbol,
                profile.product_meta,
            )
        )
    return _unique_rows(rows)


def _fetch_gate_stock_symbol_rows() -> List[SymbolMasterRow]:
    rows: List[SymbolMasterRow] = []
    supported_exchanges = (("us", "USStock"), ("hk", "HKStock"))
    for stock_exchange, underlying_market in supported_exchanges:
        page = 1
        while True:
            response = requests.get(
                "https://api.gateio.ws/api/v4/stock/symbols/detail",
                params={"exchange": stock_exchange, "page": page, "page_size": 500},
                timeout=30,
            )
            response.raise_for_status()
            payload = response.json()
            data = payload.get("data") if isinstance(payload, dict) else None
            items = data.get("list", []) if isinstance(data, dict) else []
            for raw in items:
                if not isinstance(raw, dict):
                    continue
                ticker = _clean_symbol(raw.get("symbol"))
                quote = _clean_symbol(raw.get("quote_currency"))
                trade_mode = int(raw.get("trade_mode") or 0)
                if not ticker or not quote or trade_mode == 0:
                    continue
                status = "tradable" if trade_mode in {3, 4} else "buy_only" if trade_mode == 1 else "sell_only"
                product_meta = {
                    "instrument_id": ticker,
                    "status": status,
                    "session_status": str(raw.get("trade_status") or raw.get("status") or ""),
                    "trade_mode": trade_mode,
                    "order_fill_timing": raw.get("order_fill_timing"),
                    "category": raw.get("category"),
                    "asset_type": raw.get("asset_type"),
                    "stock_exchange": stock_exchange,
                    "quote_currency": quote,
                    "quote_currency_precision": raw.get("quote_currency_precision"),
                    "fx_rate": raw.get("fx_rate"),
                    "min_order_volume": raw.get("min_order_volume"),
                    "max_order_volume": raw.get("max_order_volume"),
                    "step_order_volume": raw.get("step_order_volume"),
                    "price_precision": raw.get("price_precision"),
                    "volume_precision": raw.get("volume_precision"),
                    "commission_rate": raw.get("commission_rate"),
                    "slippage_rate": raw.get("slippage_rate"),
                    "market_data_api_family": (
                        "underlying_hk_equity" if underlying_market == "HKStock"
                        else "underlying_us_equity"
                    ),
                }
                rows.append(
                    SymbolMasterRow(
                        "Crypto",
                        f"{ticker}/{quote}",
                        _clean_text(raw.get("symbol_desc") or ticker),
                        "gate",
                        quote,
                        "spot",
                        ticker,
                        _clean_symbol(raw.get("settlement_currency") or quote),
                        "equity",
                        "direct_equity",
                        "stock",
                        underlying_market,
                        ticker,
                        product_meta,
                    )
                )
            total_pages = int(data.get("total_page") or 1) if isinstance(data, dict) else 1
            if page >= total_pages or not items:
                break
            page += 1
    return _unique_rows(rows)


def _bitget_reality_marker(raw: Dict[str, object]) -> bool:
    if str(raw.get("isReality") or "").strip().lower() in {"1", "true", "yes", "y"}:
        return True
    base = _clean_symbol(raw.get("baseCoin") or raw.get("baseCurrency"))
    return (
        str(raw.get("areaSymbol") or "").strip().lower() == "yes"
        and base.startswith("R")
        and 1 <= len(base[1:]) <= 5
        and base[1:].isalpha()
    )


def _load_native_markets_with_retry(exchange, attempts: int = 2) -> None:
    last_error = None
    for attempt in range(max(1, int(attempts or 1))):
        try:
            exchange.load_markets(reload=attempt > 0)
            return
        except TypeError:
            exchange.load_markets()
            return
        except Exception as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(0.5 * (attempt + 1))
    if last_error is not None:
        raise last_error


def _fetch_okx_public_symbol_rows(market_type: str, classify_asset) -> List[SymbolMasterRow]:
    inst_type = "SPOT" if market_type == "spot" else "SWAP"
    path = f"/api/v5/public/instruments?instType={inst_type}"
    payload = _get_okx_public_json(path)
    if str(payload.get("code") or "") != "0":
        raise RuntimeError(f"OKX public instruments returned code={payload.get('code')}")
    return _okx_public_payload_to_rows(payload, market_type, classify_asset)


def _get_okx_public_json(path: str) -> dict:
    hostname = "openapi.okx.com"
    url = f"https://{hostname}{path}"
    try:
        response = requests.get(url, timeout=30, headers={"User-Agent": "QuantDinger/1.0"})
        response.raise_for_status()
        return response.json()
    except Exception as primary_error:
        errors = []
        for address in _resolve_ipv4_with_doh(hostname):
            pool = urllib3.HTTPSConnectionPool(
                address,
                port=443,
                server_hostname=hostname,
                assert_hostname=hostname,
                timeout=urllib3.Timeout(connect=10, read=30),
                retries=False,
            )
            try:
                response = pool.request(
                    "GET",
                    path,
                    headers={"Host": hostname, "User-Agent": "QuantDinger/1.0"},
                )
                if response.status != 200:
                    raise RuntimeError(f"HTTP {response.status}")
                payload = json.loads(response.data.decode("utf-8"))
                if not isinstance(payload, dict):
                    raise RuntimeError("invalid JSON payload")
                return payload
            except Exception as exc:
                errors.append(f"{address}: {exc}")
            finally:
                pool.close()
        detail = "; ".join(errors) or "DoH returned no IPv4 addresses"
        raise RuntimeError(f"{primary_error}; {detail}") from primary_error


def _resolve_ipv4_with_doh(hostname: str) -> List[str]:
    providers = (
        ("https://cloudflare-dns.com/dns-query", {"name": hostname, "type": "A"}),
        ("https://dns.google/resolve", {"name": hostname, "type": "A"}),
    )
    addresses: List[str] = []
    for url, params in providers:
        try:
            response = requests.get(
                url,
                params=params,
                timeout=10,
                headers={"Accept": "application/dns-json", "User-Agent": "QuantDinger/1.0"},
            )
            response.raise_for_status()
            for answer in response.json().get("Answer") or []:
                value = str(answer.get("data") or "").strip()
                if int(answer.get("type") or 0) == 1 and re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", value):
                    addresses.append(value)
            if addresses:
                break
        except Exception as exc:
            logger.debug("DoH lookup failed provider=%s host=%s: %s", url, hostname, exc)
    return list(dict.fromkeys(addresses))


def _okx_public_payload_to_rows(payload: dict, market_type: str, classify_asset) -> List[SymbolMasterRow]:
    rows: List[SymbolMasterRow] = []
    for item in payload.get("data") or []:
        if not isinstance(item, dict) or str(item.get("state") or "").lower() != "live":
            continue
        instrument_id = _clean_text(item.get("instId"))
        if market_type == "spot":
            base = _clean_symbol(item.get("baseCcy"))
            quote = _clean_symbol(item.get("quoteCcy"))
            if quote != "USDT":
                continue
        else:
            settle = _clean_symbol(item.get("settleCcy"))
            if settle != "USDT" or not instrument_id.endswith("-USDT-SWAP"):
                continue
            base = _clean_symbol(item.get("ctValCcy")) or instrument_id.removesuffix("-USDT-SWAP")
            quote = "USDT"
        if not base or not instrument_id:
            continue
        unified = {
            "symbol": f"{base}/{quote}",
            "id": instrument_id,
            "base": base,
            "quote": quote,
            "spot": market_type == "spot",
            "swap": market_type == "swap",
            "info": item,
        }
        profile = classify_instrument_product(
            unified,
            exchange_id="okx",
            market_type=market_type,
            symbol=f"{base}/{quote}",
            instrument_id=instrument_id,
        )
        rows.append(
            SymbolMasterRow(
                "Crypto",
                f"{base}/{quote}",
                base,
                "okx",
                quote,
                market_type,
                instrument_id,
                quote,
                profile.asset_class,
                profile.product_type,
                profile.api_family,
                profile.underlying_market,
                profile.underlying_symbol,
                profile.product_meta,
            )
        )
    return _unique_rows(rows)


def fetch_crypto_symbols() -> List[SymbolMasterRow]:
    """Fetch all active USDT spot and swap instruments from six venues."""
    rows, _ = fetch_crypto_symbols_with_diagnostics()
    return rows


def fetch_forex_symbols() -> List[SymbolMasterRow]:
    """Return supported forex and metals symbols."""
    return _unique_rows(_static_rows("Forex"))


def fetch_futures_symbols() -> List[SymbolMasterRow]:
    """Return supported traditional futures symbols."""
    from app.services.market.dated_futures import contract_catalog_rows, preset_contracts
    return _unique_rows(_static_rows("Futures") + contract_catalog_rows(preset_contracts()))


def fetch_moex_symbols() -> List[SymbolMasterRow]:
    """Fetch MOEX TQBR shares, with a static blue-chip fallback."""
    rows = _static_rows("MOEX")
    try:
        resp = requests.get(
            "https://iss.moex.com/iss/engines/stock/markets/shares/boards/TQBR/securities.json",
            params={"iss.meta": "off"},
            timeout=20,
            headers={"User-Agent": "QuantDinger/1.0"},
        )
        resp.raise_for_status()
        data = resp.json().get("securities", {})
        columns = data.get("columns") or []
        for values in data.get("data") or []:
            item = dict(zip(columns, values))
            symbol = _clean_symbol(item.get("SECID"))
            name = _clean_text(item.get("SECNAME") or item.get("SHORTNAME"))
            if symbol and name:
                rows.append(SymbolMasterRow("MOEX", symbol, name, "MOEX", "RUB"))
    except Exception as e:
        logger.warning("moex symbol source unavailable, using static fallback: %s", e)
    return _unique_rows(rows)


FETCHERS = {
    "CNStock": fetch_cn_stock_symbols,
    "HKStock": fetch_hk_stock_symbols,
    "USStock": fetch_us_stock_symbols,
    "Crypto": fetch_crypto_symbols,
    "Forex": fetch_forex_symbols,
    "Futures": fetch_futures_symbols,
    "MOEX": fetch_moex_symbols,
}


def upsert_symbol_master(rows: Sequence[SymbolMasterRow]) -> int:
    """Upsert rows while preserving curated hot flags and sort order."""
    if not rows:
        return 0
    with get_db_connection() as db:
        cur = db.cursor()
        count = 0
        crypto_contexts = {(row.exchange, row.market_type) for row in rows if row.market == "Crypto" and row.exchange}
        for exchange_id, market_type in crypto_contexts:
            cur.execute(
                "UPDATE qd_market_symbols SET is_active = 0 WHERE market = 'Crypto' AND exchange = ? AND market_type = ?",
                (exchange_id, market_type),
            )
        for row in rows:
            asset_class = row.asset_class or _default_asset_class(row.market)
            cur.execute(
                """
                INSERT INTO qd_market_symbols
                    (market, symbol, name, exchange, currency, market_type, instrument_id, settle_currency,
                     asset_class, product_type, api_family, underlying_market, underlying_symbol, product_meta,
                     metadata_updated_at, is_active, is_hot, sort_order, board_lot, rules_as_of, rules_source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?::jsonb, NOW(), 1, 0, 0, ?, ?, ?)
                ON CONFLICT (market, symbol, exchange, market_type, instrument_id) DO UPDATE
                  SET name = EXCLUDED.name,
                      exchange = COALESCE(NULLIF(EXCLUDED.exchange, ''), qd_market_symbols.exchange),
                      currency = COALESCE(NULLIF(EXCLUDED.currency, ''), qd_market_symbols.currency),
                      settle_currency = COALESCE(NULLIF(EXCLUDED.settle_currency, ''), qd_market_symbols.settle_currency),
                      asset_class = EXCLUDED.asset_class,
                      product_type = EXCLUDED.product_type,
                      api_family = EXCLUDED.api_family,
                      underlying_market = EXCLUDED.underlying_market,
                      underlying_symbol = EXCLUDED.underlying_symbol,
                      product_meta = EXCLUDED.product_meta,
                      board_lot = COALESCE(EXCLUDED.board_lot, qd_market_symbols.board_lot),
                      rules_as_of = COALESCE(EXCLUDED.rules_as_of, qd_market_symbols.rules_as_of),
                      rules_source = COALESCE(EXCLUDED.rules_source, qd_market_symbols.rules_source),
                      metadata_updated_at = NOW(),
                      is_active = 1
                """,
                (
                    row.market,
                    row.symbol,
                    row.name,
                    row.exchange,
                    row.currency,
                    row.market_type,
                    row.instrument_id,
                    row.settle_currency,
                    asset_class,
                    row.product_type or "crypto",
                    row.api_family or row.market_type or "spot",
                    row.underlying_market or "",
                    row.underlying_symbol or "",
                    json.dumps(row.product_meta or {}, ensure_ascii=False),
                    row.board_lot, row.rules_as_of, row.rules_source,
                ),
            )
            count += 1
        db.commit()
        cur.close()
        return count


def _default_asset_class(market: str) -> str:
    return {
        "Crypto": "crypto",
        "Forex": "forex",
        "Futures": "futures",
    }.get(str(market or ""), "equity")


def sync_symbol_master(markets: Optional[Sequence[str]] = None) -> Dict[str, Dict[str, object]]:
    """Fetch and upsert local symbol master data for the requested markets."""
    selected = list(markets or FETCHERS.keys())
    stats: Dict[str, Dict[str, object]] = {}
    for market in selected:
        fetcher = FETCHERS.get(market)
        if not fetcher:
            stats[market] = {"ok": False, "error": "unsupported market", "rows": 0}
            continue
        try:
            rows = fetcher()
            written = upsert_symbol_master(rows)
            stats[market] = {"ok": True, "rows": len(rows), "upserted": written}
        except Exception as e:
            logger.warning("symbol master sync failed market=%s: %s", market, e)
            stats[market] = {"ok": False, "error": str(e), "rows": 0}
    return stats
