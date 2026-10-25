"""Build the bundled China and Hong Kong universe seed from exchange sources."""

from __future__ import annotations

import argparse
import json
import urllib.parse
import urllib.request
from pathlib import Path

import xlrd


ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "app" / "data" / "default_cn_hk_universes.json"
MIGRATION_PATH = ROOT / "migrations" / "20261025_default_cn_hk_universe_members.sql"
HISTORY_FROM = "2023-01-01"
CSI_INDEXES = {
    "csi300": "000300",
    "csi500": "000905",
    "csi1000": "000852",
    "csi800": "000906",
    "sse50": "000016",
}
CSI_CONSTITUENT_URL = (
    "https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/"
    "file/autofile/cons/{index_code}cons.xls"
)
SSE_HK_CONNECT_URL = "https://query.sse.com.cn/commonQuery.do"
SSE_HK_CONNECT_REFERER = "https://www.sse.com.cn/services/hkexsc/disclo/eligible/"


def _fetch(url: str, *, referer: str = "") -> bytes:
    headers = {"User-Agent": "Mozilla/5.0 QuantDinger/5.7.1"}
    if referer:
        headers["Referer"] = referer
    request = urllib.request.Request(url, headers=headers)
    return urllib.request.urlopen(request, timeout=90).read()


def _load_csi(code: str, index_code: str) -> tuple[str, list[dict]]:
    content = _fetch(CSI_CONSTITUENT_URL.format(index_code=index_code))
    sheet = xlrd.open_workbook(file_contents=content).sheet_by_index(0)
    members = []
    snapshot_as_of = ""
    for rank in range(1, sheet.nrows):
        row = sheet.row_values(rank)
        symbol = str(row[4]).strip().split(".")[0].zfill(6)
        if not symbol.isdigit() or len(symbol) != 6:
            continue
        raw_date = str(row[0]).strip()
        if len(raw_date) == 8 and raw_date.isdigit():
            snapshot_as_of = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
        members.append({
            "market": "CNStock",
            "symbol": symbol,
            "name": str(row[6]).strip(),
            "rank": len(members) + 1,
            "metadata": {
                "exchange": str(row[8]).strip(),
                "index_code": index_code,
                "source": "csindex_official_constituents",
            },
        })
    if code != "sse50" and len(members) != int(code.removeprefix("csi")):
        raise RuntimeError(f"{code} returned {len(members)} members")
    if code == "sse50" and len(members) != 50:
        raise RuntimeError(f"{code} returned {len(members)} members")
    return snapshot_as_of, members


def _load_hk_stock_connect() -> tuple[str, list[dict]]:
    params = urllib.parse.urlencode({
        "jsonCallBack": "callback",
        "sqlId": "COMMON_SSE_JYFW_HGT_XXPL_BDZQQD_L",
        "isPagination": "true",
        "pageHelp.pageSize": "1000",
        "pageHelp.pageNo": "1",
        "pageHelp.beginPage": "1",
        "pageHelp.cacheSize": "1",
        "pageHelp.endPage": "1",
        "keyword": "",
    })
    raw = _fetch(f"{SSE_HK_CONNECT_URL}?{params}", referer=SSE_HK_CONNECT_REFERER).decode("utf-8")
    payload = json.loads(raw[raw.index("(") + 1:raw.rindex(")")])
    source_rows = (payload.get("pageHelp") or {}).get("data") or []
    members = []
    snapshot_as_of = ""
    for item in source_rows:
        symbol = str(item.get("SECURITY_CODE") or "").strip().zfill(5)
        if not symbol.isdigit() or len(symbol) != 5:
            continue
        snapshot_as_of = str(item.get("UPDATE_DATE") or snapshot_as_of).strip()
        members.append({
            "market": "HKStock",
            "symbol": symbol,
            "name": str(item.get("ABBR_CN") or item.get("ABBR_EN") or "").strip(),
            "rank": len(members) + 1,
            "metadata": {
                "english_name": str(item.get("ABBR_EN") or "").strip(),
                "security_type": str(item.get("SECURITY_TYPE") or "").strip(),
                "source": "sse_hk_stock_connect_official",
            },
        })
    if not 500 <= len(members) <= 900:
        raise RuntimeError(f"hk_stock_connect returned {len(members)} members")
    return snapshot_as_of, members


def _sql_literal(value: object) -> str:
    if value is None:
        return "NULL"
    return "'" + str(value).replace("'", "''") + "'"


def _render_migration(document: dict) -> str:
    codes = tuple(document["universes"])
    lines = [
        "BEGIN;",
        "",
        "DELETE FROM qd_universe_members",
        "WHERE universe_id IN (",
        "  SELECT id FROM qd_universes",
        f"  WHERE is_system = TRUE AND code IN ({', '.join(_sql_literal(code) for code in codes)})",
        ");",
        "",
        "INSERT INTO qd_universe_members",
        "  (universe_id, market, symbol, name, exchange_id, market_type, instrument_id,",
        "   settle_currency, valid_from, valid_to, member_weight, member_rank, source_version, metadata_json)",
        "SELECT u.id, seed.market, seed.symbol, seed.name, '', 'spot', '',",
        "       CASE WHEN seed.market = 'CNStock' THEN 'CNY' ELSE 'HKD' END,",
        f"       DATE '{document['history_from']}', NULL, NULL, seed.member_rank,",
        "       seed.source_version, seed.metadata_json::jsonb",
        "FROM (VALUES",
    ]
    values = []
    for code, universe in document["universes"].items():
        for member in universe["members"]:
            metadata = json.dumps(member.get("metadata") or {}, ensure_ascii=False, separators=(",", ":"))
            values.append(
                "  (" + ", ".join((
                    _sql_literal(code),
                    _sql_literal(member["market"]),
                    _sql_literal(member["symbol"]),
                    _sql_literal(member.get("name") or ""),
                    str(int(member["rank"])),
                    _sql_literal(f"bundled:{universe['snapshot_as_of']}"),
                    _sql_literal(metadata),
                )) + ")"
            )
    lines.append(",\n".join(values))
    lines.extend([
        ") AS seed(code, market, symbol, name, member_rank, source_version, metadata_json)",
        "JOIN qd_universes u ON u.code = seed.code AND u.is_system = TRUE;",
        "",
        "UPDATE qd_universes u",
        "SET status = 'active',",
        "    source = 'bundled_snapshot',",
        "    metadata_json = COALESCE(u.metadata_json, '{}'::jsonb) || jsonb_build_object(",
        f"      'history_mode', 'fixed_current_constituents', 'history_assumed_from', '{document['history_from']}',",
        "      'snapshot_as_of', seed.snapshot_as_of, 'bundled_seed', TRUE,",
        "      'bundled_seed_version', seed.source_version, 'member_count', seed.member_count",
        "    ),",
        "    updated_at = NOW()",
        "FROM (VALUES",
    ])
    metadata_values = []
    for code, universe in document["universes"].items():
        metadata_values.append(
            "  (" + ", ".join((
                _sql_literal(code),
                _sql_literal(universe["snapshot_as_of"]),
                _sql_literal(document["seed_version"]),
                str(len(universe["members"])),
            )) + ")"
        )
    lines.append(",\n".join(metadata_values))
    lines.extend([
        ") AS seed(code, snapshot_as_of, source_version, member_count)",
        "WHERE u.code = seed.code AND u.is_system = TRUE;",
        "",
        "COMMIT;",
        "",
    ])
    return "\n".join(lines)


def build() -> dict:
    universes = {}
    snapshot_dates = []
    for code, index_code in CSI_INDEXES.items():
        snapshot_as_of, members = _load_csi(code, index_code)
        snapshot_dates.append(snapshot_as_of)
        universes[code] = {
            "snapshot_as_of": snapshot_as_of,
            "source_url": CSI_CONSTITUENT_URL.format(index_code=index_code),
            "members": members,
        }
    snapshot_as_of, members = _load_hk_stock_connect()
    snapshot_dates.append(snapshot_as_of)
    universes["hk_stock_connect"] = {
        "snapshot_as_of": snapshot_as_of,
        "source_url": SSE_HK_CONNECT_URL,
        "members": members,
    }
    document = {
        "seed_version": f"cn-hk:{max(snapshot_dates)}",
        "history_from": HISTORY_FROM,
        "history_mode": "fixed_current_constituents",
        "universes": universes,
    }
    DATA_PATH.write_text(json.dumps(document, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    MIGRATION_PATH.write_text(_render_migration(document), encoding="utf-8")
    return document


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.parse_args()
    document = build()
    print(json.dumps({code: len(item["members"]) for code, item in document["universes"].items()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
