"""Export verified public universe members into the bundled installation seed."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor


ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "app" / "data" / "default_global_universes.json"
MIGRATION_PATH = ROOT / "migrations" / "20261025_default_global_universe_members.sql"
HISTORY_FROM = "2023-01-01"
DEFAULT_CODES = (
    "sp500",
    "nasdaq100",
    "dow30",
    "sp400",
    "sp600",
    "russell1000",
    "crypto_top100",
    "hk_hsi",
    "hk_tech30",
    "hk_china_enterprises50",
    "hk_etf",
    "us_etf",
    "us_sector_information_technology",
    "us_sector_health_care",
    "us_sector_financials",
    "us_sector_consumer_discretionary",
    "us_sector_communication_services",
    "us_sector_industrials",
    "us_sector_consumer_staples",
    "us_sector_energy",
    "us_sector_utilities",
    "us_sector_real_estate",
    "us_sector_materials",
)
EXPECTED_COUNTS = {
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


def _sql_literal(value: object) -> str:
    if value is None:
        return "NULL"
    return "'" + str(value).replace("'", "''") + "'"


def _settle_currency(market: str) -> str:
    return {"USStock": "USD", "HKStock": "HKD", "Crypto": "USDT"}.get(market, "")


def _load_document(database_url: str) -> dict:
    with psycopg2.connect(database_url) as connection:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT code, source_ref, metadata_json
                FROM qd_universes
                WHERE is_system = TRUE AND code = ANY(%s)
                ORDER BY code
                """,
                (list(DEFAULT_CODES),),
            )
            universes = {row["code"]: dict(row) for row in cursor.fetchall()}
            cursor.execute(
                """
                SELECT u.code, m.market, m.symbol, m.name, m.exchange_id,
                       m.market_type, m.instrument_id, m.settle_currency,
                       m.member_weight, m.member_rank, m.metadata_json
                FROM qd_universes u
                JOIN qd_universe_members m ON m.universe_id = u.id
                WHERE u.is_system = TRUE
                  AND u.code = ANY(%s)
                  AND m.valid_to IS NULL
                ORDER BY u.code, m.member_rank NULLS LAST, m.symbol
                """,
                (list(DEFAULT_CODES),),
            )
            rows = cursor.fetchall()

    grouped = {code: [] for code in DEFAULT_CODES}
    for row in rows:
        code = row.pop("code")
        member = dict(row)
        member["rank"] = int(member.pop("member_rank") or len(grouped[code]) + 1)
        member["weight"] = member.pop("member_weight")
        member["metadata"] = member.pop("metadata_json") or {}
        grouped[code].append(member)

    missing = [code for code in DEFAULT_CODES if code not in universes]
    if missing:
        raise RuntimeError(f"missing system universes: {', '.join(missing)}")
    invalid = {
        code: len(grouped[code])
        for code in DEFAULT_CODES
        if len(grouped[code]) != EXPECTED_COUNTS[code]
    }
    if invalid:
        raise RuntimeError(f"unexpected active member counts: {invalid}")

    snapshot_dates = []
    document_universes = {}
    for code in DEFAULT_CODES:
        metadata = universes[code].get("metadata_json") or {}
        snapshot_as_of = str(metadata.get("snapshot_as_of") or "")
        if not snapshot_as_of:
            raise RuntimeError(f"missing snapshot_as_of metadata: {code}")
        snapshot_dates.append(snapshot_as_of)
        document_universes[code] = {
            "snapshot_as_of": snapshot_as_of,
            "source_ref": universes[code].get("source_ref") or "",
            "source_metadata": metadata,
            "members": grouped[code],
        }

    return {
        "seed_version": f"global:{max(snapshot_dates)}",
        "history_from": HISTORY_FROM,
        "history_mode": "fixed_current_constituents",
        "universes": document_universes,
    }


def _render_migration(document: dict) -> str:
    codes = tuple(document["universes"])
    lines = [
        "BEGIN;",
        "",
        "CREATE TEMP TABLE qd_global_universes_to_seed ON COMMIT DROP AS",
        "SELECT id, code",
        "FROM qd_universes u",
        "WHERE u.is_system = TRUE",
        f"  AND u.code IN ({', '.join(_sql_literal(code) for code in codes)})",
        "  AND NOT EXISTS (",
        "    SELECT 1 FROM qd_universe_members m WHERE m.universe_id = u.id",
        "  );",
        "",
        "INSERT INTO qd_universe_members",
        "  (universe_id, market, symbol, name, exchange_id, market_type, instrument_id,",
        "   settle_currency, valid_from, valid_to, member_weight, member_rank, source_version, metadata_json)",
        "SELECT target.id, seed.market, seed.symbol, seed.name, seed.exchange_id, seed.market_type,",
        "       seed.instrument_id, seed.settle_currency,",
        f"       DATE '{document['history_from']}', NULL, seed.member_weight::double precision, seed.member_rank::integer,",
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
                    _sql_literal(member.get("exchange_id") or ""),
                    _sql_literal(member.get("market_type") or "spot"),
                    _sql_literal(member.get("instrument_id") or ""),
                    _sql_literal(member.get("settle_currency") or _settle_currency(member["market"])),
                    "NULL" if member.get("weight") is None else str(float(member["weight"])),
                    str(int(member["rank"])),
                    _sql_literal(f"bundled:{universe['snapshot_as_of']}"),
                    _sql_literal(metadata),
                )) + ")"
            )
    lines.append(",\n".join(values))
    lines.extend([
        ") AS seed(code, market, symbol, name, exchange_id, market_type, instrument_id,",
        "          settle_currency, member_weight, member_rank, source_version, metadata_json)",
        "JOIN qd_global_universes_to_seed target ON target.code = seed.code;",
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
        "JOIN qd_global_universes_to_seed target ON target.code = seed.code",
        "WHERE u.id = target.id;",
        "",
        "COMMIT;",
        "",
    ])
    return "\n".join(lines)


def build(database_url: str) -> dict:
    document = _load_document(database_url)
    DATA_PATH.write_text(
        json.dumps(document, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    MIGRATION_PATH.write_text(_render_migration(document), encoding="utf-8")
    return document


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--database-url",
        default=os.getenv("QD_SEED_DATABASE_URL") or os.getenv("DATABASE_URL") or "",
    )
    args = parser.parse_args()
    if not args.database_url:
        parser.error("--database-url or QD_SEED_DATABASE_URL is required")
    document = build(args.database_url)
    print(json.dumps({code: len(item["members"]) for code, item in document["universes"].items()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
