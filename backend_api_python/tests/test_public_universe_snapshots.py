import json
from pathlib import Path

import pandas as pd

from scripts import refresh_public_universe_snapshots as snapshots


class _RowsCursor:
    def __init__(self, rows):
        self.rows = rows
        self.params = None
        self.executions = []

    def execute(self, sql, params=None):
        self.params = params
        self.executions.append((sql, params))

    def fetchall(self):
        return self.rows

    def close(self):
        pass


class _RowsConnection:
    def __init__(self, rows):
        self.cursor_obj = _RowsCursor(rows)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        pass


def test_hsi_api_parser_uses_requested_parent_index_and_normalizes_codes():
    payload = {
        "data": {
            "viewConsDetail": {
                "00001.00": [
                    {
                        "stockCode": "700",
                        "stockName": "TENCENT",
                        "exchangeListed": "Hong Kong Stock Exchange",
                        "stockType": "HK",
                        "dateTime": "2026-10-23 16:08:30",
                    },
                    {"stockCode": "939", "stockName": "CCB"},
                ],
                "00001.01": [{"stockCode": "5", "stockName": "HSBC"}],
            },
        },
    }

    rows = snapshots._parse_hsi_constituent_payload(payload, "00001.00", "hsi")

    assert [row["symbol"] for row in rows] == ["00700", "00939"]
    assert rows[0]["metadata"]["series_code"] == "hsi"
    assert rows[0]["metadata"]["source_timestamp"] == "2026-10-23 16:08:30"


def test_sse_hk_stock_connect_parser_normalizes_codes():
    rows = snapshots._parse_sse_hk_stock_connect_payload({"pageHelp": {"data": [
        {"SECURITY_CODE": "700", "ABBR_CN": "Tencent", "ABBR_EN": "TENCENT"},
        {"SECURITY_CODE": "09988", "ABBR_CN": "Alibaba", "ABBR_EN": "BABA-W"},
        {"SECURITY_CODE": "invalid", "ABBR_CN": "Ignored"},
    ]}})

    assert [row["symbol"] for row in rows] == ["00700", "09988"]
    assert rows[0]["metadata"]["source"] == "sse_hk_stock_connect_official"


def test_szse_hk_stock_connect_parser_normalizes_codes():
    rows = snapshots._parse_szse_hk_stock_connect_report({"data": [
        {"zqdm": "700", "zqjc": "Tencent", "zqywjc": "TENCENT"},
        {"zqdm": "invalid", "zqjc": "Ignored"},
    ]})

    assert [row["symbol"] for row in rows] == ["00700"]
    assert rows[0]["metadata"]["source"] == "szse_hk_stock_connect_official"


def test_bundled_cn_hk_universes_have_expected_counts_and_history_start():
    data_path = Path(snapshots.BUNDLED_UNIVERSE_PATH)
    document = json.loads(data_path.read_text(encoding="utf-8"))

    assert document["history_from"] == "2023-01-01"
    assert {code: len(item["members"]) for code, item in document["universes"].items()} == {
        "csi300": 300,
        "csi500": 500,
        "csi1000": 1000,
        "csi800": 800,
        "sse50": 50,
        "hk_stock_connect": 697,
    }


def test_bundled_global_universes_have_expected_counts_and_history_start():
    data_path = Path(snapshots.GLOBAL_BUNDLED_UNIVERSE_PATH)
    document = json.loads(data_path.read_text(encoding="utf-8"))

    assert document["history_from"] == "2023-01-01"
    assert {code: len(item["members"]) for code, item in document["universes"].items()} == {
        "sp500": 503,
        "nasdaq100": 101,
        "dow30": 30,
        "sp400": 400,
        "sp600": 603,
        "russell1000": 1021,
        "crypto_top100": 100,
        "hk_hsi": 95,
        "hk_tech30": 30,
        "hk_china_enterprises50": 50,
        "hk_etf": 18,
        "us_etf": 31,
        "us_sector_information_technology": 75,
        "us_sector_health_care": 60,
        "us_sector_financials": 76,
        "us_sector_consumer_discretionary": 47,
        "us_sector_communication_services": 22,
        "us_sector_industrials": 83,
        "us_sector_consumer_staples": 34,
        "us_sector_energy": 21,
        "us_sector_utilities": 31,
        "us_sector_real_estate": 30,
        "us_sector_materials": 24,
    }


def test_common_public_loader_uses_bundled_snapshot_when_source_fails(monkeypatch):
    def unavailable():
        raise RuntimeError("network unavailable")

    monkeypatch.setitem(snapshots.LOADERS, "sp500", unavailable)

    rows = snapshots.load_universe_members("sp500")

    assert len(rows) == 503
    assert rows[0]["market"] == "USStock"


def test_hk_stock_connect_uses_bundled_snapshot_when_official_sources_fail(monkeypatch):
    def unavailable():
        raise RuntimeError("network unavailable")

    monkeypatch.setattr(snapshots, "_sse_hk_stock_connect", unavailable)
    monkeypatch.setattr(snapshots, "_szse_hk_stock_connect", unavailable)

    rows = snapshots.hk_stock_connect()

    assert len(rows) == 697
    assert rows[0]["market"] == "HKStock"


def test_etf_snapshot_loader_reads_hot_symbol_master_rows(monkeypatch):
    connection = _RowsConnection([
        {"market": "USStock", "symbol": "spy", "name": "SPDR S&P 500 ETF"},
        {"market": "USStock", "symbol": "qqq", "name": "Invesco QQQ"},
    ])
    monkeypatch.setattr(snapshots, "get_db_connection", lambda: connection)

    rows = snapshots.symbol_master_etfs("USStock")

    assert connection.cursor_obj.params == ("USStock",)
    repair_sql, repair_params = connection.cursor_obj.executions[0]
    assert "SET asset_class = 'etf', is_hot = 1" in repair_sql
    assert repair_params[0] == "USStock"
    assert "SPY" in repair_params[1]
    assert [row["symbol"] for row in rows] == ["SPY", "QQQ"]
    assert rows[0]["rank"] == 1
    assert rows[0]["metadata"] == {"source": "symbol_master", "asset_class": "etf"}
    assert "hk_etf" in snapshots.LOADERS
    assert "us_etf" in snapshots.LOADERS


def test_dow30_parser_selects_company_symbol_table():
    frame = pd.DataFrame({
        "Company": [f"Company {index}" for index in range(30)],
        "Symbol": [f"D{index:02d}" for index in range(30)],
        "Industry": ["Industry"] * 30,
        "Exchange": ["NYSE"] * 30,
    })

    rows = snapshots._parse_dow30_tables([pd.DataFrame({"Other": [1]}), frame])

    assert len(rows) == 30
    assert rows[0]["symbol"] == "D00"
    assert rows[0]["metadata"] == {"industry": "Industry", "exchange": "NYSE"}


def test_us_index_parser_selects_expected_table_and_normalizes_class_symbols():
    frame = pd.DataFrame({
        "Company": [f"Company {index}" for index in range(400)],
        "Symbol": ["BRK.B", *[f"M{index:03d}" for index in range(1, 400)]],
        "GICS Sector": ["Financials"] * 400,
        "GICS Sub-Industry": ["Multi-Sector Holdings"] * 400,
    })

    rows = snapshots._parse_us_index_tables([pd.DataFrame({"Other": [1]}), frame], (395, 405))

    assert len(rows) == 400
    assert rows[0]["symbol"] == "BRK-B"
    assert rows[0]["metadata"]["sector"] == "Financials"


def test_common_universe_loaders_are_registered():
    assert snapshots.EXPECTED_MEMBER_COUNTS["sse50"] == (50, 50)
    assert snapshots.EXPECTED_MEMBER_COUNTS["csi800"] == (800, 800)
    assert snapshots.EXPECTED_MEMBER_COUNTS["csi1000"] == (1000, 1000)
    assert snapshots.EXPECTED_MEMBER_COUNTS["dow30"] == (30, 30)
    assert snapshots.EXPECTED_MEMBER_COUNTS["sp400"] == (395, 405)
    assert snapshots.EXPECTED_MEMBER_COUNTS["sp600"] == (595, 610)
    assert snapshots.EXPECTED_MEMBER_COUNTS["russell1000"] == (950, 1050)
    assert {"sse50", "csi800", "csi1000", "dow30", "sp400", "sp600", "russell1000"}.issubset(
        snapshots.LOADERS
    )
    assert {"hk_hsi", "hk_tech30", "hk_china_enterprises50", "hk_stock_connect"}.issubset(
        snapshots.LOADERS
    )
    assert set(snapshots.US_SECTOR_UNIVERSES).issubset(snapshots.LOADERS)


def test_sector_loader_reuses_sp500_download_and_preserves_sector_metadata(monkeypatch):
    calls = {"count": 0}

    def fake_sp500():
        calls["count"] += 1
        return [
            {"market": "USStock", "symbol": "A", "rank": 1, "metadata": {"sector": "Energy"}},
            {"market": "USStock", "symbol": "B", "rank": 2, "metadata": {"sector": "Financials"}},
            {"market": "USStock", "symbol": "C", "rank": 3, "metadata": {"sector": "Energy"}},
        ]

    monkeypatch.setitem(snapshots.LOADERS, "sp500", fake_sp500)
    monkeypatch.setitem(snapshots.EXPECTED_MEMBER_COUNTS, "sp500", (1, 10))
    monkeypatch.setitem(snapshots.EXPECTED_MEMBER_COUNTS, "us_sector_energy", (1, 10))
    monkeypatch.setitem(snapshots.EXPECTED_MEMBER_COUNTS, "us_sector_financials", (1, 10))
    cache = {}

    energy = snapshots.load_universe_members("us_sector_energy", cache)
    financials = snapshots.load_universe_members("us_sector_financials", cache)

    assert calls["count"] == 1
    assert [row["symbol"] for row in energy] == ["A", "C"]
    assert [row["rank"] for row in energy] == [1, 2]
    assert [row["symbol"] for row in financials] == ["B"]
