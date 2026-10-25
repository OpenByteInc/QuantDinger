"""Refresh versioned current-universe snapshots from public sources."""

from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import sys
from datetime import date
from functools import lru_cache
from pathlib import Path

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.symbol_master_sync import SymbolMasterRow, upsert_symbol_master
from app.utils.db import get_db_connection


SP500_URL = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"
NASDAQ100_URL = "https://raw.githubusercontent.com/Gary-Strauss/NASDAQ100_Constituents/master/data/nasdaq100_constituents.csv"
WIKIMEDIA_PAGE_API = "https://api.wikimedia.org/core/v1/wikipedia/en/page"
DOW30_URL = f"{WIKIMEDIA_PAGE_API}/List_of_Dow_Jones_Industrial_Average_companies/html"
SP400_URL = f"{WIKIMEDIA_PAGE_API}/List_of_S%26P_400_companies/html"
SP600_URL = f"{WIKIMEDIA_PAGE_API}/List_of_S%26P_600_companies/html"
RUSSELL1000_URL = f"{WIKIMEDIA_PAGE_API}/List_of_Russell_1000_companies/html"
COINGECKO_URL = "https://api.coingecko.com/api/v3/coins/markets"
HSI_CONSTITUENT_URL = (
    "https://www.hsi.com.hk/api/wsit-hsi-ddoc-ea-public-website-proxy/"
    "v1/product-data/pub/constituent/metadata/v2"
)
SSE_HK_CONNECT_URL = "https://query.sse.com.cn/commonQuery.do"
SZSE_HK_CONNECT_URL = "https://www.szse.cn/api/report/ShowReport/data"
SSE_HK_CONNECT_REFERER = "https://www.sse.com.cn/services/hkexsc/disclo/eligible/"
SZSE_HK_CONNECT_REFERER = "https://investor.szse.cn/szhk/hkbussiness/underlylist/index.html"
BUNDLED_UNIVERSE_PATH = ROOT / "app" / "data" / "default_cn_hk_universes.json"
GLOBAL_BUNDLED_UNIVERSE_PATH = ROOT / "app" / "data" / "default_global_universes.json"
logger = logging.getLogger(__name__)

HK_HSI_SERIES = {
    "hk_hsi": ("hsi", "00001.00"),
    "hk_tech30": ("hstech", "02083.00"),
    "hk_china_enterprises50": ("hscei", "00014.00"),
}

US_SECTOR_UNIVERSES = {
    "us_sector_information_technology": "Information Technology",
    "us_sector_health_care": "Health Care",
    "us_sector_financials": "Financials",
    "us_sector_consumer_discretionary": "Consumer Discretionary",
    "us_sector_communication_services": "Communication Services",
    "us_sector_industrials": "Industrials",
    "us_sector_consumer_staples": "Consumer Staples",
    "us_sector_energy": "Energy",
    "us_sector_utilities": "Utilities",
    "us_sector_real_estate": "Real Estate",
    "us_sector_materials": "Materials",
}

CURATED_ETF_SYMBOLS = {
    "HKStock": (
        "02800", "02801", "02823", "02828", "02840", "02846", "03032", "03033", "03037",
        "03040", "03067", "03075", "03088", "03110", "03188", "03191", "03416", "03437",
    ),
    "USStock": (
        "SPY", "QQQ", "IWM", "DIA", "VTI", "VOO", "IVV", "EFA", "EEM", "AGG", "BND", "TLT",
        "IEF", "GLD", "SLV", "USO", "XLF", "XLK", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU",
        "VNQ", "ARKK", "HYG", "LQD", "SCHD", "VUG", "VTV",
    ),
}

EXPECTED_MEMBER_COUNTS = {
    "csi300": (300, 300),
    "csi500": (500, 500),
    "csi1000": (1000, 1000),
    "csi800": (800, 800),
    "sse50": (50, 50),
    "sp500": (500, 510),
    "nasdaq100": (100, 102),
    "dow30": (30, 30),
    "sp400": (395, 405),
    "sp600": (595, 610),
    "russell1000": (950, 1050),
    "crypto_top100": (95, 100),
    "hk_hsi": (80, 110),
    "hk_tech30": (30, 30),
    "hk_china_enterprises50": (50, 50),
    "hk_stock_connect": (500, 900),
    "hk_etf": (1, 1000),
    "us_etf": (1, 1000),
    **{code: (5, 150) for code in US_SECTOR_UNIVERSES},
}

def _csv_rows(url: str) -> list[dict]:
    response = requests.get(url, timeout=30, headers={"User-Agent": "QuantDinger/4.0"})
    response.raise_for_status()
    return list(csv.DictReader(io.StringIO(response.text)))


def _resilient_get(url: str, **kwargs) -> requests.Response:
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        backoff_factor=0.8,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
    )
    with requests.Session() as session:
        session.mount("https://", HTTPAdapter(max_retries=retry))
        return session.get(url, **kwargs)


@lru_cache(maxsize=1)
def _bundled_universe_document() -> dict:
    document = json.loads(BUNDLED_UNIVERSE_PATH.read_text(encoding="utf-8"))
    universes = dict(document.get("universes") or {})
    if GLOBAL_BUNDLED_UNIVERSE_PATH.exists():
        global_document = json.loads(GLOBAL_BUNDLED_UNIVERSE_PATH.read_text(encoding="utf-8"))
        universes.update(global_document.get("universes") or {})
    return {**document, "universes": universes}


def bundled_universe_members(code: str) -> list[dict]:
    universe = (_bundled_universe_document().get("universes") or {}).get(code) or {}
    members = universe.get("members") or []
    if not members:
        raise RuntimeError(f"bundled universe is unavailable: {code}")
    return [dict(item) for item in members]


def sp500() -> list[dict]:
    return [
        {
            "market": "USStock",
            "symbol": str(row.get("Symbol") or "").strip().upper(),
            "name": str(row.get("Security") or "").strip(),
            "rank": index,
            "metadata": {
                "sector": row.get("GICS Sector") or "",
                "sub_industry": row.get("GICS Sub-Industry") or "",
                "headquarters": row.get("Headquarters Location") or "",
                "date_added": row.get("Date added") or "",
                "cik": row.get("CIK") or "",
            },
        }
        for index, row in enumerate(_csv_rows(SP500_URL), start=1)
        if row.get("Symbol")
    ]


def nasdaq100() -> list[dict]:
    return [
        {
            "market": "USStock",
            "symbol": str(row.get("Ticker") or "").strip().upper(),
            "name": str(row.get("Company") or "").strip(),
            "rank": index,
            "metadata": {
                "sector": row.get("GICS_Sector") or "",
                "sub_industry": row.get("GICS_Sub_Industry") or "",
            },
        }
        for index, row in enumerate(_csv_rows(NASDAQ100_URL), start=1)
        if row.get("Ticker")
    ]


def _filter_sp500_sector(members: list[dict], sector: str) -> list[dict]:
    rows = [
        {**item, "rank": rank}
        for rank, item in enumerate(
            (
                item
                for item in members
                if str((item.get("metadata") or {}).get("sector") or "").strip() == sector
            ),
            start=1,
        )
    ]
    return rows


def load_universe_members(code: str, cache: dict[str, list[dict]] | None = None) -> list[dict]:
    member_cache = cache if cache is not None else {}
    if code in member_cache:
        return member_cache[code]
    if code in US_SECTOR_UNIVERSES:
        base_members = load_universe_members("sp500", member_cache)
        members = _filter_sp500_sector(base_members, US_SECTOR_UNIVERSES[code])
    else:
        loader = LOADERS.get(code)
        if loader is None:
            raise RuntimeError(f"unsupported universe: {code}")
        try:
            members = loader()
        except Exception as exc:
            logger.warning("Public source failed for %s; using the bundled snapshot: %s", code, exc)
            members = bundled_universe_members(code)
    minimum, maximum = EXPECTED_MEMBER_COUNTS[code]
    member_count = len({item.get("symbol") for item in members if item.get("symbol")})
    if not minimum <= member_count <= maximum:
        logger.warning(
            "Public source returned %s members for %s; using the bundled snapshot",
            member_count,
            code,
        )
        members = bundled_universe_members(code)
    member_cache[code] = members
    return members


def _parse_dow30_tables(tables: list[pd.DataFrame]) -> list[dict]:
    for frame in tables:
        frame = frame.copy()
        frame.columns = [
            " ".join(str(part) for part in column if str(part) != "nan").strip()
            if isinstance(column, tuple)
            else str(column).strip()
            for column in frame.columns
        ]
        symbol_column = next((column for column in frame.columns if column.lower() == "symbol"), "")
        company_column = next((column for column in frame.columns if column.lower() == "company"), "")
        if not symbol_column or not company_column:
            continue
        industry_column = next((column for column in frame.columns if "industry" in column.lower()), "")
        exchange_column = next((column for column in frame.columns if "exchange" in column.lower()), "")
        rows = []
        for index, item in enumerate(frame.to_dict("records"), start=1):
            symbol = str(item.get(symbol_column) or "").strip().upper().replace("NYSE:", "").replace("NASDAQ:", "")
            if not symbol or symbol.lower() == "nan":
                continue
            rows.append({
                "market": "USStock",
                "symbol": symbol,
                "name": str(item.get(company_column) or "").strip(),
                "rank": index,
                "metadata": {
                    "industry": str(item.get(industry_column) or "").strip() if industry_column else "",
                    "exchange": str(item.get(exchange_column) or "").strip() if exchange_column else "",
                },
            })
        if len(rows) == 30:
            return rows
    raise RuntimeError("unable to find a valid 30-member Dow Jones table")


def dow30() -> list[dict]:
    response = _resilient_get(DOW30_URL, timeout=(30, 120), headers={"User-Agent": "QuantDinger/5.7.1"})
    response.raise_for_status()
    return _parse_dow30_tables(pd.read_html(io.StringIO(response.text)))


def _parse_us_index_tables(tables: list[pd.DataFrame], expected_range: tuple[int, int]) -> list[dict]:
    minimum, maximum = expected_range
    for frame in tables:
        frame = frame.copy()
        frame.columns = [
            " ".join(str(part) for part in column if str(part) != "nan").strip()
            if isinstance(column, tuple)
            else str(column).strip()
            for column in frame.columns
        ]
        symbol_column = next((column for column in frame.columns if column.lower() == "symbol"), "")
        company_column = next(
            (column for column in frame.columns if column.lower() in {"security", "company"}),
            "",
        )
        if not symbol_column or not company_column or not minimum <= len(frame) <= maximum:
            continue
        sector_column = next((column for column in frame.columns if column.lower() == "gics sector"), "")
        industry_column = next((column for column in frame.columns if "sub-industry" in column.lower()), "")
        rows = []
        for index, item in enumerate(frame.to_dict("records"), start=1):
            symbol = str(item.get(symbol_column) or "").strip().upper().replace(".", "-")
            if not symbol or symbol.lower() == "nan":
                continue
            rows.append({
                "market": "USStock",
                "symbol": symbol,
                "name": str(item.get(company_column) or "").strip(),
                "rank": index,
                "metadata": {
                    "sector": str(item.get(sector_column) or "").strip() if sector_column else "",
                    "sub_industry": str(item.get(industry_column) or "").strip() if industry_column else "",
                },
            })
        if minimum <= len(rows) <= maximum:
            return rows
    raise RuntimeError("unable to find a valid US index constituent table")


def wikipedia_us_index(url: str, expected_range: tuple[int, int]) -> list[dict]:
    response = _resilient_get(url, timeout=(30, 120), headers={"User-Agent": "QuantDinger/5.7.1"})
    response.raise_for_status()
    return _parse_us_index_tables(pd.read_html(io.StringIO(response.text)), expected_range)


def _parse_hsi_constituent_payload(payload: dict, index_code: str, series_code: str) -> list[dict]:
    data = payload.get("data") if isinstance(payload, dict) else {}
    details = data.get("viewConsDetail") if isinstance(data, dict) else {}
    items = details.get(index_code) if isinstance(details, dict) else []
    rows = []
    for index, item in enumerate(items or [], start=1):
        symbol = str(item.get("stockCode") or "").strip()
        if not symbol.isdigit():
            continue
        rows.append({
            "market": "HKStock",
            "symbol": symbol.zfill(5),
            "name": str(item.get("stockName") or "").strip(),
            "rank": index,
            "metadata": {
                "series_code": series_code,
                "index_code": index_code,
                "exchange": item.get("exchangeListed") or "",
                "stock_type": item.get("stockType") or "",
                "source_timestamp": item.get("dateTime") or "",
            },
        })
    return rows


def hsi_constituents(series_code: str, index_code: str) -> list[dict]:
    response = _resilient_get(
        HSI_CONSTITUENT_URL,
        params={"data": "rt", "seriesCode": series_code, "language": "en-hk"},
        timeout=45,
        headers={"User-Agent": "QuantDinger/5.7.1"},
    )
    response.raise_for_status()
    return _parse_hsi_constituent_payload(response.json(), index_code, series_code)


def _parse_sse_hk_stock_connect_payload(payload: dict) -> list[dict]:
    rows = []
    for item in (payload.get("pageHelp") or {}).get("data") or []:
        symbol = str(item.get("SECURITY_CODE") or "").strip()
        if not symbol.isdigit():
            continue
        rows.append({
            "market": "HKStock",
            "symbol": symbol.zfill(5),
            "name": str(item.get("ABBR_CN") or item.get("ABBR_EN") or "").strip(),
            "rank": len(rows) + 1,
            "metadata": {
                "english_name": str(item.get("ABBR_EN") or "").strip(),
                "source": "sse_hk_stock_connect_official",
            },
        })
    return rows


def _parse_szse_hk_stock_connect_report(report: dict) -> list[dict]:
    rows = []
    for item in report.get("data") or []:
        symbol = str(item.get("zqdm") or "").strip()
        if not symbol.isdigit():
            continue
        rows.append({
            "market": "HKStock",
            "symbol": symbol.zfill(5),
            "name": str(item.get("zqjc") or item.get("zqywjc") or "").strip(),
            "rank": len(rows) + 1,
            "metadata": {
                "english_name": str(item.get("zqywjc") or "").strip(),
                "source": "szse_hk_stock_connect_official",
            },
        })
    return rows


def _sse_hk_stock_connect() -> list[dict]:
    params = {
        "jsonCallBack": "callback",
        "sqlId": "COMMON_SSE_JYFW_HGT_XXPL_BDZQQD_L",
        "isPagination": "true",
        "pageHelp.pageSize": 1000,
        "pageHelp.pageNo": 1,
        "pageHelp.beginPage": 1,
        "pageHelp.cacheSize": 1,
        "pageHelp.endPage": 1,
        "keyword": "",
    }
    response = _resilient_get(
        SSE_HK_CONNECT_URL,
        params=params,
        timeout=(15, 60),
        headers={"User-Agent": "QuantDinger/5.7.1", "Referer": SSE_HK_CONNECT_REFERER},
    )
    response.raise_for_status()
    text = response.text.strip()
    payload = json.loads(text[text.index("(") + 1:text.rindex(")")])
    return _parse_sse_hk_stock_connect_payload(payload)


def _szse_hk_stock_connect() -> list[dict]:
    rows_by_symbol = {}
    page = 1
    page_count = 1
    while page <= page_count:
        response = _resilient_get(
            SZSE_HK_CONNECT_URL,
            params={
                "SHOWTYPE": "JSON",
                "CATALOGID": "SGT_GGTBDQD",
                "tab1PAGENO": page,
            },
            timeout=(15, 60),
            headers={"User-Agent": "QuantDinger/5.7.1", "Referer": SZSE_HK_CONNECT_REFERER},
        )
        response.raise_for_status()
        payload = response.json()
        report = payload[0] if payload else {}
        metadata = report.get("metadata") or {}
        page_count = int(metadata.get("pagecount") or 1)
        for item in _parse_szse_hk_stock_connect_report(report):
            rows_by_symbol[item["symbol"]] = item
        page += 1
    rows = list(rows_by_symbol.values())
    for index, row in enumerate(rows, start=1):
        row["rank"] = index
    return rows


def hk_stock_connect() -> list[dict]:
    for loader in (_sse_hk_stock_connect, _szse_hk_stock_connect):
        try:
            rows = loader()
            if 500 <= len(rows) <= 900:
                return rows
            logger.warning("%s returned an invalid member count: %s", loader.__name__, len(rows))
        except Exception as exc:
            logger.warning("%s failed, trying the next source: %s", loader.__name__, exc)
    logger.warning("Official Hong Kong Stock Connect sources failed; using the bundled snapshot")
    return bundled_universe_members("hk_stock_connect")


def csi(code: str, index_code: str) -> list[dict]:
    try:
        import akshare as ak  # type: ignore

        frame = ak.index_stock_cons_weight_csindex(symbol=index_code)
    except Exception as exc:
        logger.warning("CSI source failed for %s; using the bundled snapshot: %s", code, exc)
        return bundled_universe_members(code)
    rows = []
    for index, item in enumerate(frame.to_dict("records"), start=1):
        symbol = str(item.get("品种代码") or item.get("成分券代码") or item.get("代码") or "").strip().zfill(6)
        name = str(item.get("品种名称") or item.get("成分券名称") or item.get("名称") or "").strip()
        if symbol.isdigit() and len(symbol) == 6:
            raw_weight = item.get("权重")
            weight = float(raw_weight) / 100.0 if raw_weight not in (None, "") else None
            rows.append({
                "market": "CNStock",
                "symbol": symbol,
                "name": name,
                "rank": index,
                "weight": weight,
                "metadata": {"exchange": item.get("交易所") or ""},
            })
    minimum, maximum = EXPECTED_MEMBER_COUNTS[code]
    if not minimum <= len(rows) <= maximum:
        logger.warning("CSI source returned %s members for %s; using the bundled snapshot", len(rows), code)
        return bundled_universe_members(code)
    return rows


def crypto_top100() -> list[dict]:
    response = requests.get(
        COINGECKO_URL,
        params={
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": 100,
            "page": 1,
            "sparkline": "false",
        },
        timeout=30,
        headers={"User-Agent": "QuantDinger/4.0"},
    )
    response.raise_for_status()
    rows = []
    seen = set()
    for item in response.json():
        symbol = f"{str(item.get('symbol') or '').upper()}/USDT"
        if symbol in seen or symbol == "/USDT":
            continue
        seen.add(symbol)
        rows.append({
            "market": "Crypto",
            "symbol": symbol,
            "name": str(item.get("name") or ""),
            "rank": int(item.get("market_cap_rank") or len(rows) + 1),
            "metadata": {
                "coingecko_id": item.get("id") or "",
                "market_cap_usd": item.get("market_cap"),
                "circulating_supply": item.get("circulating_supply"),
                "total_volume_usd": item.get("total_volume"),
            },
        })
    return rows


def symbol_master_etfs(market: str) -> list[dict]:
    """Build an ETF snapshot and repair legacy curated ETF metadata."""
    with get_db_connection() as db:
        cur = db.cursor()
        curated = list(CURATED_ETF_SYMBOLS.get(market, ()))
        if curated:
            cur.execute(
                """
                UPDATE qd_market_symbols
                SET asset_class = 'etf', is_hot = 1, sort_order = GREATEST(sort_order, 80)
                WHERE market = ? AND symbol = ANY(?)
                """,
                (market, curated),
            )
        cur.execute(
            """
            SELECT market, symbol, name
            FROM qd_market_symbols
            WHERE market = ? AND is_active = 1 AND is_hot = 1 AND asset_class = 'etf'
            ORDER BY sort_order DESC, symbol
            """,
            (market,),
        )
        rows = cur.fetchall() or []
        db.commit()
        cur.close()
    return [
        {
            "market": str(row.get("market") or market),
            "symbol": str(row.get("symbol") or "").strip().upper(),
            "name": str(row.get("name") or "").strip(),
            "rank": index,
            "metadata": {"source": "symbol_master", "asset_class": "etf"},
        }
        for index, row in enumerate(rows, start=1)
        if row.get("symbol")
    ]


LOADERS = {
    "sp500": sp500,
    "nasdaq100": nasdaq100,
    "dow30": dow30,
    "sp400": lambda: wikipedia_us_index(SP400_URL, EXPECTED_MEMBER_COUNTS["sp400"]),
    "sp600": lambda: wikipedia_us_index(SP600_URL, EXPECTED_MEMBER_COUNTS["sp600"]),
    "russell1000": lambda: wikipedia_us_index(RUSSELL1000_URL, EXPECTED_MEMBER_COUNTS["russell1000"]),
    "csi300": lambda: csi("csi300", "000300"),
    "csi500": lambda: csi("csi500", "000905"),
    "csi1000": lambda: csi("csi1000", "000852"),
    "csi800": lambda: csi("csi800", "000906"),
    "sse50": lambda: csi("sse50", "000016"),
    "crypto_top100": crypto_top100,
    "hk_stock_connect": hk_stock_connect,
    "hk_etf": lambda: symbol_master_etfs("HKStock"),
    "us_etf": lambda: symbol_master_etfs("USStock"),
    **{
        code: (lambda series_code=series_code, index_code=index_code: hsi_constituents(series_code, index_code))
        for code, (series_code, index_code) in HK_HSI_SERIES.items()
    },
    **{
        code: (lambda sector=sector: _filter_sp500_sector(sp500(), sector))
        for code, sector in US_SECTOR_UNIVERSES.items()
    },
}

SOURCE_METADATA = {
    "sp500": {"url": SP500_URL, "license": "ODC-PDDL", "snapshot_only": True},
    "nasdaq100": {"url": NASDAQ100_URL, "license": "MIT scraper; source data CC BY-SA", "snapshot_only": True},
    "dow30": {"url": DOW30_URL, "license": "CC BY-SA", "snapshot_only": True},
    "sp400": {"url": SP400_URL, "license": "CC BY-SA", "snapshot_only": True},
    "sp600": {"url": SP600_URL, "license": "CC BY-SA", "snapshot_only": True},
    "russell1000": {"url": RUSSELL1000_URL, "license": "CC BY-SA", "snapshot_only": True},
    "csi300": {"url": "https://www.csindex.com.cn/", "adapter": "AKShare index_stock_cons_weight_csindex", "snapshot_only": True},
    "csi500": {"url": "https://www.csindex.com.cn/", "adapter": "AKShare index_stock_cons_weight_csindex", "snapshot_only": True},
    "csi1000": {"url": "https://www.csindex.com.cn/", "adapter": "AKShare index_stock_cons_weight_csindex", "snapshot_only": True},
    "csi800": {"url": "https://www.csindex.com.cn/", "adapter": "AKShare index_stock_cons_weight_csindex", "snapshot_only": True},
    "sse50": {"url": "https://www.csindex.com.cn/", "adapter": "AKShare index_stock_cons_weight_csindex", "snapshot_only": True},
    "crypto_top100": {"url": COINGECKO_URL, "snapshot_only": True},
    "hk_stock_connect": {
        "urls": [SSE_HK_CONNECT_URL, SZSE_HK_CONNECT_URL],
        "publisher": "Shanghai Stock Exchange / Shenzhen Stock Exchange",
        "bundled_fallback": True,
        "snapshot_only": True,
    },
    "hk_etf": {"adapter": "symbol_master", "market": "HKStock", "asset_class": "etf", "snapshot_only": True},
    "us_etf": {"adapter": "symbol_master", "market": "USStock", "asset_class": "etf", "snapshot_only": True},
    **{
        code: {
            "url": HSI_CONSTITUENT_URL,
            "publisher": "Hang Seng Indexes Company Limited",
            "series_code": series_code,
            "index_code": index_code,
            "snapshot_only": True,
        }
        for code, (series_code, index_code) in HK_HSI_SERIES.items()
    },
    **{
        code: {
            "derived_from": "sp500",
            "classification": "GICS Sector",
            "sector": sector,
            "url": SP500_URL,
            "license": "ODC-PDDL",
            "snapshot_only": True,
        }
        for code, sector in US_SECTOR_UNIVERSES.items()
    },
}

FIXED_CURRENT_HISTORY_MODE = "fixed_current_constituents"


def apply_snapshot(code: str, members: list[dict], as_of: date, *, dry_run: bool = False) -> dict:
    clean = {item["symbol"]: item for item in members if item.get("symbol")}
    if dry_run:
        return {"code": code, "members": len(clean), "dry_run": True}
    hk_rows = [
        SymbolMasterRow(
            "HKStock", item["symbol"], item.get("name") or "", "HKEX", "HKD",
            asset_class="equity",
        )
        for item in clean.values()
        if item.get("market") == "HKStock"
    ]
    if hk_rows:
        upsert_symbol_master(hk_rows)
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute("SELECT id FROM qd_universes WHERE code = ? AND is_system = TRUE", (code,))
        universe = cur.fetchone() or {}
        universe_id = int(universe.get("id") or 0)
        if not universe_id:
            raise RuntimeError(f"unknown system universe: {code}")
        cur.execute(
            "SELECT symbol, valid_from FROM qd_universe_members WHERE universe_id = ? AND valid_to IS NULL",
            (universe_id,),
        )
        active_rows = cur.fetchall() or []
        active = {str(row.get("symbol") or "") for row in active_rows}
        active_from = {str(row.get("symbol") or ""): row.get("valid_from") for row in active_rows}
        removed = active - set(clean)
        if removed:
            same_day = [symbol for symbol in removed if active_from.get(symbol) and active_from[symbol] >= as_of]
            historical = sorted(removed - set(same_day))
            if same_day:
                cur.execute(
                    "DELETE FROM qd_universe_members WHERE universe_id = ? AND valid_to IS NULL AND symbol = ANY(?)",
                    (universe_id, sorted(same_day)),
                )
            if historical:
                cur.execute(
                    "UPDATE qd_universe_members SET valid_to = ? WHERE universe_id = ? AND valid_to IS NULL AND symbol = ANY(?)",
                    (as_of, universe_id, historical),
                )
        for symbol, item in clean.items():
            metadata = json.dumps(item.get("metadata") or {}, ensure_ascii=False)
            if symbol in active:
                cur.execute(
                    """
                    UPDATE qd_universe_members
                    SET name = ?, member_weight = ?, member_rank = ?, metadata_json = ?, source_version = ?
                    WHERE universe_id = ? AND symbol = ? AND valid_to IS NULL
                    """,
                    (item.get("name") or "", item.get("weight"), item.get("rank"), metadata, as_of.isoformat(), universe_id, symbol),
                )
                continue
            cur.execute(
                """
                INSERT INTO qd_universe_members
                  (universe_id, market, symbol, name, market_type, valid_from,
                   member_weight, member_rank, source_version, metadata_json)
                VALUES (?, ?, ?, ?, 'spot', ?, ?, ?, ?, ?)
                """,
                (
                    universe_id, item.get("market") or "", symbol, item.get("name") or "",
                    as_of, item.get("weight"), item.get("rank"), as_of.isoformat(), metadata,
                ),
            )
        universe_metadata = json.dumps(
            {**SOURCE_METADATA.get(code, {}), "snapshot_as_of": as_of.isoformat()},
            ensure_ascii=False,
        )
        cur.execute(
            """
            UPDATE qd_universes
            SET status = 'active', source = 'public_snapshot', metadata_json = ?, updated_at = NOW()
            WHERE id = ?
            """,
            (universe_metadata, universe_id),
        )
        db.commit()
        cur.close()
    return {"code": code, "members": len(clean), "added": len(set(clean) - active), "removed": len(removed)}


def backfill_current_snapshot_as_fixed(
    code: str,
    history_from: date,
    snapshot_as_of: date,
    *,
    dry_run: bool = False,
) -> dict:
    """Treat the current constituent set as fixed when no historical source exists."""
    if history_from > snapshot_as_of:
        raise ValueError("history_from must be on or before snapshot_as_of")

    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            "SELECT id, metadata_json FROM qd_universes WHERE code = ? AND is_system = TRUE",
            (code,),
        )
        universe = cur.fetchone() or {}
        universe_id = int(universe.get("id") or 0)
        if not universe_id:
            raise RuntimeError(f"unknown system universe: {code}")

        cur.execute(
            "SELECT COUNT(*) AS member_count FROM qd_universe_members WHERE universe_id = ? AND valid_to IS NULL",
            (universe_id,),
        )
        member_count = int((cur.fetchone() or {}).get("member_count") or 0)
        result = {
            "universe": code,
            "history_from": history_from.isoformat(),
            "snapshot_as_of": snapshot_as_of.isoformat(),
            "members": member_count,
            "mode": FIXED_CURRENT_HISTORY_MODE,
            "apply": not dry_run,
        }
        if dry_run:
            db.rollback()
            cur.close()
            return result

        cur.execute("LOCK TABLE qd_universe_members IN SHARE ROW EXCLUSIVE MODE")
        cur.execute(
            "DELETE FROM qd_universe_members WHERE universe_id = ? AND valid_to IS NOT NULL",
            (universe_id,),
        )
        cur.execute(
            """
            UPDATE qd_universe_members
            SET valid_from = ?, valid_to = NULL, updated_at = NOW()
            WHERE universe_id = ? AND valid_to IS NULL
            """,
            (history_from, universe_id),
        )

        metadata = universe.get("metadata_json")
        if isinstance(metadata, str):
            metadata = json.loads(metadata) if metadata.strip() else {}
        if not isinstance(metadata, dict):
            metadata = {}
        metadata.update({
            "snapshot_as_of": snapshot_as_of.isoformat(),
            "history_mode": FIXED_CURRENT_HISTORY_MODE,
            "history_assumed_from": history_from.isoformat(),
        })
        cur.execute(
            """
            UPDATE qd_universes
            SET metadata_json = ?, updated_at = NOW()
            WHERE id = ?
            """,
            (json.dumps(metadata, ensure_ascii=False), universe_id),
        )
        db.commit()
        cur.close()
        return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--universes", default=",".join(LOADERS))
    parser.add_argument("--as-of", default=date.today().isoformat())
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    as_of = date.fromisoformat(args.as_of)
    results = []
    member_cache: dict[str, list[dict]] = {}
    for code in [item.strip() for item in args.universes.split(",") if item.strip()]:
        members = load_universe_members(code, member_cache)
        minimum, maximum = EXPECTED_MEMBER_COUNTS[code]
        if not minimum <= len({item.get("symbol") for item in members}) <= maximum:
            raise RuntimeError(f"{code} member count failed validation: {len(members)}")
        results.append(apply_snapshot(code, members, as_of, dry_run=args.dry_run))
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
