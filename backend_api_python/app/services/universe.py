"""Point-in-time strategy universe management."""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import date, datetime, timezone
from typing import Any, Iterable, Optional

from app.services.market.watchlist import VALID_MARKETS, normalize_symbol
from app.services.market_context import MarketContext
from app.services.symbol_name import normalize_crypto_symbol
from app.utils.db import get_db_connection


STATIC_START = date(1900, 1, 1)
SUPPORTED_UNIVERSE_TYPES = frozenset({
    "manual", "watchlist", "index", "etf", "market_cap", "market", "industry",
})
EDITABLE_UNIVERSE_TYPES = frozenset({"manual"})
MAX_MANUAL_MEMBERS = 5000
MAX_SYSTEM_MEMBERS = 1200
_CODE_RE = re.compile(r"[^a-z0-9_-]+")


class UniverseError(ValueError):
    """A stable API-facing universe validation error."""

    def __init__(self, code: str, *, status_code: int = 400):
        super().__init__(code)
        self.code = code
        self.status_code = status_code


def parse_as_of(value: Any) -> date:
    """Normalize an as-of value without accepting time-zone ambiguous strings."""
    if value is None or value == "":
        return date.today()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise UniverseError("universe.invalidAsOf") from exc


def normalize_universe_code(name: str) -> str:
    """Build a stable user-universe code from an arbitrary display name."""
    cleaned = _CODE_RE.sub("-", str(name or "").strip().lower()).strip("-_")
    return cleaned[:48] or "manual"


def normalize_member(raw: dict, *, default_market: str = "") -> dict:
    """Validate and canonicalize one universe member."""
    if not isinstance(raw, dict):
        raise UniverseError("universe.invalidMember")
    market = str(raw.get("market") or default_market or "").strip()
    if market not in VALID_MARKETS:
        raise UniverseError("universe.invalidMarket")
    symbol = normalize_symbol(raw.get("symbol"))
    if market in {"USStock", "CNStock", "HKStock"} and "/" in symbol:
        raise UniverseError("universe.memberMarketMismatch")
    if market == "Crypto":
        symbol = normalize_crypto_symbol(symbol)
        if "/" not in symbol:
            raise UniverseError("universe.invalidCryptoSymbol")
    if not symbol or len(symbol) > 80:
        raise UniverseError("universe.invalidSymbol")

    context = MarketContext.from_mapping({
        "market": market,
        "symbol": symbol,
        "exchange_id": raw.get("exchange_id") or raw.get("exchangeId") or "",
        "market_type": raw.get("market_type") or raw.get("marketType") or "",
        "instrument_id": raw.get("instrument_id") or raw.get("instrumentId") or "",
        "settle_currency": raw.get("settle_currency") or raw.get("settleCurrency") or "",
    })
    return {
        "market": market,
        "symbol": symbol,
        "name": str(raw.get("name") or "").strip()[:160],
        "exchange_id": context.exchange_id,
        "market_type": context.market_type,
        "instrument_id": context.instrument_id,
        "settle_currency": context.settle_currency,
        "weight": _optional_float(raw.get("weight")),
        "rank": _optional_int(raw.get("rank")),
        "metadata": raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {},
    }


def normalize_members(raw_members: Iterable[dict], *, default_market: str = "") -> list[dict]:
    """Normalize, deduplicate, and deterministically order universe members."""
    if not isinstance(raw_members, (list, tuple)):
        raise UniverseError("universe.membersRequired")
    if len(raw_members) > MAX_MANUAL_MEMBERS:
        raise UniverseError("universe.tooManyMembers")
    deduped: dict[tuple, dict] = {}
    for raw in raw_members:
        member = normalize_member(raw, default_market=default_market)
        key = (
            member["market"], member["symbol"], member["exchange_id"],
            member["market_type"], member["instrument_id"],
        )
        deduped[key] = member
    return [deduped[key] for key in sorted(deduped)]


def normalize_manual_members(market: str, raw_members: Iterable[dict]) -> list[dict]:
    """Normalize a personal universe while enforcing a single market."""
    if market not in VALID_MARKETS:
        raise UniverseError("universe.invalidMarket")
    members = normalize_members(raw_members, default_market=market)
    if any(member["market"] != market for member in members):
        raise UniverseError("universe.memberMarketMismatch")
    return members


def member_content_hash(members: Iterable[dict]) -> str:
    """Hash only canonical execution identity and optional index metadata."""
    canonical = []
    for member in members:
        canonical.append({
            "market": member.get("market") or "",
            "symbol": member.get("symbol") or "",
            "exchange_id": member.get("exchange_id") or "",
            "market_type": member.get("market_type") or "",
            "instrument_id": member.get("instrument_id") or "",
            "weight": member.get("weight"),
            "rank": member.get("rank"),
        })
    canonical.sort(key=lambda item: (
        item["market"], item["symbol"], item["exchange_id"],
        item["market_type"], item["instrument_id"],
    ))
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class UniverseService:
    """CRUD, point-in-time resolution, and immutable snapshot operations."""

    def list_universes(self, user_id: int) -> list[dict]:
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                SELECT u.*,
                       (SELECT MIN(m.valid_from) FROM qd_universe_members m WHERE m.universe_id = u.id) AS history_from,
                       CASE WHEN u.universe_type = 'watchlist' THEN
                         (SELECT COUNT(*) FROM qd_watchlist w WHERE w.user_id = ?)
                       ELSE
                         (SELECT COUNT(*) FROM qd_universe_members m
                          WHERE m.universe_id = u.id
                            AND m.valid_from <= CURRENT_DATE
                            AND (m.valid_to IS NULL OR m.valid_to > CURRENT_DATE))
                       END AS member_count
                FROM qd_universes u
                WHERE (
                    (u.is_system = TRUE AND u.status NOT IN ('hidden', 'deprecated'))
                    OR (u.user_id = ? AND u.status <> 'deprecated')
                )
                ORDER BY u.is_system DESC, u.id ASC
                """,
                (int(user_id), int(user_id)),
            )
            rows = cur.fetchall() or []
            cur.close()
        items = [_serialize_universe(row) for row in rows]
        for item in items:
            if item.get("source") == "symbol_master":
                item["member_count"] = len(self._symbol_master_members(item.get("source_ref") or ""))
        return items

    def get_universe(self, user_id: int, universe_id: int) -> dict:
        with get_db_connection() as db:
            row = self._get_visible_universe(db, user_id, universe_id)
            cur = db.cursor()
            cur.execute("SELECT MIN(valid_from) AS history_from FROM qd_universe_members WHERE universe_id = ?", (universe_id,))
            row = dict(row, **(cur.fetchone() or {}))
            cur.close()
        row['member_count'] = len(self.resolve_members(user_id, universe_id))
        return _serialize_universe(row)

    def create_manual(self, user_id: int, payload: dict) -> dict:
        name = str((payload or {}).get("name") or "").strip()
        market = str((payload or {}).get("market") or "").strip()
        if not name or len(name) > 160:
            raise UniverseError("universe.invalidName")
        members = normalize_manual_members(market, (payload or {}).get("members") or [])
        code = f"{normalize_universe_code(name)}-{uuid.uuid4().hex[:10]}"
        metadata = (payload or {}).get("metadata") if isinstance((payload or {}).get("metadata"), dict) else {}

        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                INSERT INTO qd_universes
                  (user_id, code, name, market, universe_type, source, is_system, status, metadata_json)
                VALUES (?, ?, ?, ?, 'manual', 'manual', FALSE, 'active', ?)
                RETURNING id
                """,
                (int(user_id), code, name, market, json.dumps(metadata, ensure_ascii=False)),
            )
            created = cur.fetchone() or {}
            universe_id = int(created.get("id") or 0)
            self._replace_static_members(cur, universe_id, members)
            db.commit()
            cur.close()
        return self.get_universe(user_id, universe_id)

    def create_system(self, payload: dict) -> dict:
        payload = payload or {}
        name = str(payload.get("name") or "").strip()
        code = normalize_universe_code(payload.get("code") or name)
        market = str(payload.get("market") or "").strip()
        universe_type = str(payload.get("universe_type") or payload.get("universeType") or "manual").strip()
        history_from = parse_as_of(payload.get("history_from") or payload.get("historyFrom"))
        if not name or len(name) > 160:
            raise UniverseError("universe.invalidName")
        if not code or len(code) > 80:
            raise UniverseError("universe.invalidCode")
        if market not in VALID_MARKETS:
            raise UniverseError("universe.invalidMarket")
        if universe_type not in SUPPORTED_UNIVERSE_TYPES or universe_type == "watchlist":
            raise UniverseError("universe.createTypeUnsupported")
        if history_from > date.today():
            raise UniverseError("universe.invalidHistoryDate")
        members = normalize_manual_members(market, payload.get("members") or [])
        if len(members) > MAX_SYSTEM_MEMBERS:
            raise UniverseError("universe.tooManyMembers")
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        metadata = {
            **metadata,
            "admin_managed": True,
            "history_mode": "fixed_current_constituents",
            "snapshot_as_of": date.today().isoformat(),
        }

        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                "SELECT id FROM qd_universes WHERE is_system = TRUE AND code = ?",
                (code,),
            )
            if cur.fetchone():
                cur.close()
                raise UniverseError("universe.codeExists", status_code=409)
            cur.execute(
                """
                INSERT INTO qd_universes
                  (user_id, code, name, market, universe_type, source, is_system,
                   status, metadata_json)
                VALUES (NULL, ?, ?, ?, ?, 'manual', TRUE, 'active', ?)
                RETURNING id
                """,
                (code, name, market, universe_type, json.dumps(metadata, ensure_ascii=False)),
            )
            created = cur.fetchone() or {}
            universe_id = int(created.get("id") or 0)
            self._replace_system_members(cur, universe_id, members, history_from)
            db.commit()
            cur.close()
        return self.get_system(universe_id)

    def get_system(self, universe_id: int) -> dict:
        with get_db_connection() as db:
            universe = self._get_system_universe(db, universe_id)
            cur = db.cursor()
            cur.execute(
                "SELECT MIN(valid_from) AS history_from FROM qd_universe_members WHERE universe_id = ?",
                (int(universe_id),),
            )
            universe = dict(universe, **(cur.fetchone() or {}))
            cur.close()
        universe["member_count"] = len(self._resolve_system_members(universe_id))
        return _serialize_universe(universe)

    def update_system(self, universe_id: int, payload: dict) -> dict:
        payload = payload or {}
        with get_db_connection() as db:
            universe = self._get_system_universe(db, universe_id)
            name = str(payload.get("name") if "name" in payload else universe.get("name") or "").strip()
            market = str(payload.get("market") if "market" in payload else universe.get("market") or "").strip()
            universe_type = str(
                payload.get("universe_type") or payload.get("universeType")
                or universe.get("universe_type") or "manual"
            ).strip()
            if not name or len(name) > 160:
                raise UniverseError("universe.invalidName")
            if market not in VALID_MARKETS:
                raise UniverseError("universe.invalidMarket")
            if universe_type not in SUPPORTED_UNIVERSE_TYPES or universe_type == "watchlist":
                raise UniverseError("universe.createTypeUnsupported")

            members = None
            history_from = None
            if "members" in payload:
                members = normalize_manual_members(market, payload.get("members") or [])
                if len(members) > MAX_SYSTEM_MEMBERS:
                    raise UniverseError("universe.tooManyMembers")
                history_from = parse_as_of(payload.get("history_from") or payload.get("historyFrom"))
                if history_from > date.today():
                    raise UniverseError("universe.invalidHistoryDate")

            metadata = _json_value(universe.get("metadata_json"), {})
            metadata.update({
                "admin_managed": True,
                "snapshot_as_of": date.today().isoformat(),
            })
            if members is not None:
                metadata["history_mode"] = "fixed_current_constituents"
            cur = db.cursor()
            if members is None:
                cur.execute(
                    """
                    UPDATE qd_universes
                    SET name = ?, name_i18n_key = '', market = ?, universe_type = ?,
                        metadata_json = ?, updated_at = NOW()
                    WHERE id = ? AND is_system = TRUE
                    """,
                    (
                        name, market, universe_type,
                        json.dumps(metadata, ensure_ascii=False), int(universe_id),
                    ),
                )
            else:
                cur.execute(
                    """
                    UPDATE qd_universes
                    SET name = ?, name_i18n_key = '', market = ?, universe_type = ?, source = 'manual',
                        source_ref = '', metadata_json = ?, updated_at = NOW()
                    WHERE id = ? AND is_system = TRUE
                    """,
                    (
                        name, market, universe_type,
                        json.dumps(metadata, ensure_ascii=False), int(universe_id),
                    ),
                )
            if members is not None:
                self._replace_system_members(cur, int(universe_id), members, history_from)
            db.commit()
            cur.close()
        return self.get_system(universe_id)

    def set_system_visibility(self, universe_id: int, visible: bool) -> dict:
        with get_db_connection() as db:
            universe = self._get_system_universe(db, universe_id)
            cur = db.cursor()
            cur.execute(
                "UPDATE qd_universes SET status = ?, updated_at = NOW() WHERE id = ? AND is_system = TRUE",
                ("active" if visible else "hidden", int(universe_id)),
            )
            db.commit()
            cur.close()
        return {
            "id": int(universe_id),
            "code": str(universe.get("code") or ""),
            "status": "active" if visible else "hidden",
        }

    def delete_system(self, universe_id: int) -> dict:
        with get_db_connection() as db:
            universe = self._get_system_universe(db, universe_id)
            cur = db.cursor()
            cur.execute("DELETE FROM qd_universes WHERE id = ? AND is_system = TRUE", (int(universe_id),))
            db.commit()
            cur.close()
        return {"id": int(universe_id), "code": str(universe.get("code") or "")}

    def clone_system(self, user_id: int, universe_id: int, *, name: str = "") -> dict:
        with get_db_connection() as db:
            source = self._get_visible_universe(db, user_id, universe_id)
        if not source.get("is_system"):
            raise UniverseError("universe.cloneSystemOnly", status_code=409)
        members = self.resolve_members(user_id, universe_id)
        clone_name = str(name or source.get("name") or source.get("code") or "").strip()
        if not clone_name:
            raise UniverseError("universe.invalidName")
        clone_market = str(source.get("market") or "")
        if clone_market == "Mixed":
            member_markets = {str(member.get("market") or "") for member in members}
            if len(member_markets) != 1:
                raise UniverseError("universe.memberMarketMismatch")
            clone_market = member_markets.pop()
        return self.create_manual(user_id, {
            "name": clone_name,
            "market": clone_market,
            "members": members,
            "metadata": {
                "cloned_from_universe_id": int(universe_id),
                "cloned_from_code": source.get("code") or "",
                "cloned_at": datetime.now(timezone.utc).isoformat(),
            },
        })

    def system_overview(self) -> dict:
        history_syncable_codes = [
            "sp500",
            "nasdaq100",
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
        ]
        standard_symbol_limit = max(
            1,
            int(os.getenv("STRATEGY_V2_MAX_SYMBOLS", "600") or 600),
        )
        daily_symbol_limit = max(
            standard_symbol_limit,
            int(os.getenv("STRATEGY_V2_MAX_DAILY_SYMBOLS", "1200") or 1200),
        )
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                SELECT u.*,
                       COUNT(m.id) FILTER (
                         WHERE m.valid_from <= CURRENT_DATE
                           AND (m.valid_to IS NULL OR m.valid_to > CURRENT_DATE)
                       ) AS member_count,
                       MIN(m.valid_from) AS history_from,
                       MAX(COALESCE(m.source_version, '')) AS source_version
                FROM qd_universes u
                LEFT JOIN qd_universe_members m ON m.universe_id = u.id
                WHERE u.is_system = TRUE
                  AND u.status <> 'deprecated'
                  AND u.universe_type <> 'watchlist'
                GROUP BY u.id
                ORDER BY u.id
                """
            )
            rows = cur.fetchall() or []
            cur.close()
        items = []
        for row in rows:
            item = _serialize_universe(row)
            if item.get("source") == "symbol_master":
                item["member_count"] = len(self._symbol_master_members(item.get("source_ref") or ""))
            item["history_from"] = _iso(row.get("history_from"))
            item["source_version"] = str(row.get("source_version") or "")
            item["history_sync_supported"] = item.get("code") in history_syncable_codes
            member_count = int(item.get("member_count") or 0)
            item["standard_frequency_eligible"] = member_count <= standard_symbol_limit
            item["daily_frequency_eligible"] = member_count <= daily_symbol_limit
            items.append(item)
        return {
            "universes": items,
            "history_syncable_codes": history_syncable_codes,
            "execution_limits": {
                "standard": standard_symbol_limit,
                "daily": daily_symbol_limit,
            },
            "syncable_codes": [
                "sse50", "csi300", "csi500", "csi800", "csi1000",
                "sp500", "nasdaq100", "dow30", "sp400", "sp600", "russell1000",
                "us_sector_information_technology", "us_sector_health_care",
                "us_sector_financials", "us_sector_consumer_discretionary",
                "us_sector_communication_services", "us_sector_industrials",
                "us_sector_consumer_staples", "us_sector_energy",
                "us_sector_utilities", "us_sector_real_estate", "us_sector_materials",
                "crypto_top100",
                "hk_etf", "us_etf",
                "hk_hsi", "hk_tech30", "hk_china_enterprises50", "hk_stock_connect",
            ],
        }

    def replace_manual_members(self, user_id: int, universe_id: int, raw_members: list) -> list[dict]:
        with get_db_connection() as db:
            universe = self._get_visible_universe(db, user_id, universe_id, require_owner=True)
            if universe.get("universe_type") not in EDITABLE_UNIVERSE_TYPES:
                raise UniverseError("universe.readOnly", status_code=409)
            members = normalize_manual_members(str(universe.get("market") or ""), raw_members)
            cur = db.cursor()
            self._replace_static_members(cur, int(universe_id), members)
            cur.execute("UPDATE qd_universes SET updated_at = NOW() WHERE id = ?", (int(universe_id),))
            db.commit()
            cur.close()
        return self.resolve_members(user_id, universe_id, as_of=STATIC_START)

    def delete_manual(self, user_id: int, universe_id: int) -> dict:
        with get_db_connection() as db:
            universe = self._get_visible_universe(db, user_id, universe_id, require_owner=True)
            if universe.get("universe_type") not in EDITABLE_UNIVERSE_TYPES:
                raise UniverseError("universe.readOnly", status_code=409)
            cur = db.cursor()
            cur.execute("DELETE FROM qd_universes WHERE id = ?", (int(universe_id),))
            db.commit()
            cur.close()
        return {
            "id": int(universe_id),
            "code": str(universe.get("code") or ""),
        }

    def resolve_members(self, user_id: int, universe_id: int, *, as_of: Any = None) -> list[dict]:
        as_of_date = parse_as_of(as_of)
        with get_db_connection() as db:
            universe = self._get_visible_universe(db, user_id, universe_id)
            cur = db.cursor()
            if universe.get("universe_type") == "watchlist":
                cur.execute(
                    """
                    SELECT market, symbol, name, exchange_id, market_type,
                           instrument_id, settle_currency
                    FROM qd_watchlist
                    WHERE user_id = ?
                    ORDER BY market, symbol, exchange_id, market_type, instrument_id
                    """,
                    (int(user_id),),
                )
            elif universe.get("source") == "symbol_master":
                cur.close()
                return self._symbol_master_members(str(universe.get("source_ref") or ""))
            else:
                cur.execute(
                    """
                    SELECT market, symbol, name, exchange_id, market_type,
                           instrument_id, settle_currency,
                           member_weight AS weight, member_rank AS rank,
                           valid_from, valid_to, source_version, metadata_json AS metadata
                    FROM qd_universe_members
                    WHERE universe_id = ?
                      AND valid_from <= ?
                      AND (valid_to IS NULL OR valid_to > ?)
                    ORDER BY market, symbol, exchange_id, market_type, instrument_id
                    """,
                    (int(universe_id), as_of_date, as_of_date),
                )
            rows = cur.fetchall() or []
            cur.close()
        return [_serialize_member(row) for row in rows]

    def _resolve_system_members(self, universe_id: int, *, as_of: Any = None) -> list[dict]:
        as_of_date = parse_as_of(as_of)
        with get_db_connection() as db:
            self._get_system_universe(db, universe_id)
            cur = db.cursor()
            cur.execute(
                """
                SELECT market, symbol, name, exchange_id, market_type,
                       instrument_id, settle_currency,
                       member_weight AS weight, member_rank AS rank,
                       valid_from, valid_to, source_version, metadata_json AS metadata
                FROM qd_universe_members
                WHERE universe_id = ?
                  AND valid_from <= ?
                  AND (valid_to IS NULL OR valid_to > ?)
                ORDER BY market, symbol, exchange_id, market_type, instrument_id
                """,
                (int(universe_id), as_of_date, as_of_date),
            )
            rows = cur.fetchall() or []
            cur.close()
        return [_serialize_member(row) for row in rows]

    @staticmethod
    def _symbol_master_members(source_ref: str) -> list[dict]:
        parts = str(source_ref or "").split(":")
        if len(parts) != 3:
            return []
        market, scope, asset_class = parts
        query = """
            SELECT market, symbol, name, exchange AS exchange_id, market_type,
                   instrument_id, settle_currency
            FROM qd_market_symbols
            WHERE market = ? AND is_active = 1 AND asset_class = ?
        """
        if scope == "hot":
            query += " AND is_hot = 1"
        query += " ORDER BY sort_order DESC, symbol"
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(query, (market, asset_class))
            rows = cur.fetchall() or []
            cur.close()
        return [_serialize_member(row) for row in rows]

    def candidate_members(
        self,
        user_id: int,
        universe_id: int,
        *,
        start: Any,
        end: Any,
    ) -> list[dict]:
        """Return the union of members active at any time in a date range."""
        start_date = parse_as_of(start)
        end_date = parse_as_of(end)
        if end_date < start_date:
            raise UniverseError("universe.invalidDateRange")
        with get_db_connection() as db:
            universe = self._get_visible_universe(db, user_id, universe_id)
        if universe.get("universe_type") == "watchlist" or universe.get("source") == "symbol_master":
            return self.resolve_members(user_id, universe_id, as_of=end_date)
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                SELECT market, symbol, name, exchange_id, market_type,
                       instrument_id, settle_currency,
                       member_weight AS weight, member_rank AS rank,
                       valid_from, valid_to, source_version, metadata_json AS metadata
                FROM qd_universe_members
                WHERE universe_id = ?
                  AND valid_from <= ?
                  AND (valid_to IS NULL OR valid_to > ?)
                ORDER BY market, symbol, exchange_id, market_type, instrument_id, valid_from
                """,
                (int(universe_id), end_date, start_date),
            )
            rows = cur.fetchall() or []
            cur.close()
        deduped = {}
        for row in rows:
            member = _serialize_member(row)
            key = (
                member["market"], member["symbol"], member["exchange_id"],
                member["market_type"], member["instrument_id"],
            )
            period = {
                "valid_from": member.get("valid_from"),
                "valid_to": member.get("valid_to"),
            }
            if key not in deduped:
                deduped[key] = {**member, "membership_periods": [period]}
            else:
                deduped[key]["membership_periods"].append(period)
        return [deduped[key] for key in sorted(deduped)]

    def create_snapshot(self, user_id: int, universe_id: int, *, as_of: Any = None) -> dict:
        as_of_date = parse_as_of(as_of)
        members = self.resolve_members(user_id, universe_id, as_of=as_of_date)
        content_hash = member_content_hash(members)
        snapshot_id = str(uuid.uuid4())
        source_versions = sorted({
            str(member.get("source_version") or "") for member in members
            if member.get("source_version")
        })
        source_version = ",".join(source_versions)[:120]

        with get_db_connection() as db:
            self._get_visible_universe(db, user_id, universe_id)
            cur = db.cursor()
            cur.execute(
                """
                INSERT INTO qd_universe_snapshots
                  (snapshot_id, universe_id, user_id, as_of_date, source_version,
                   content_hash, member_count, members_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (universe_id, user_id, as_of_date, content_hash)
                DO UPDATE SET source_version = excluded.source_version
                RETURNING snapshot_id, created_at
                """,
                (
                    snapshot_id, int(universe_id), int(user_id), as_of_date,
                    source_version, content_hash, len(members),
                    json.dumps(members, ensure_ascii=False),
                ),
            )
            row = cur.fetchone() or {}
            db.commit()
            cur.close()
        return {
            "snapshot_id": str(row.get("snapshot_id") or snapshot_id),
            "universe_id": int(universe_id),
            "as_of": as_of_date.isoformat(),
            "content_hash": content_hash,
            "member_count": len(members),
            "members": members,
            "source_version": source_version,
            "created_at": _iso(row.get("created_at")),
        }

    @staticmethod
    def _get_visible_universe(db, user_id: int, universe_id: int, *, require_owner: bool = False) -> dict:
        cur = db.cursor()
        if require_owner:
            cur.execute(
                "SELECT * FROM qd_universes WHERE id = ? AND user_id = ? AND is_system = FALSE",
                (int(universe_id), int(user_id)),
            )
        else:
            cur.execute(
                "SELECT * FROM qd_universes WHERE id = ? AND (is_system = TRUE OR user_id = ?)",
                (int(universe_id), int(user_id)),
            )
        row = cur.fetchone()
        cur.close()
        if not row:
            raise UniverseError("universe.notFound", status_code=404)
        return row

    @staticmethod
    def _get_system_universe(db, universe_id: int) -> dict:
        cur = db.cursor()
        cur.execute(
            "SELECT * FROM qd_universes WHERE id = ? AND is_system = TRUE AND status <> 'deprecated'",
            (int(universe_id),),
        )
        row = cur.fetchone()
        cur.close()
        if not row:
            raise UniverseError("universe.notFound", status_code=404)
        return row

    @staticmethod
    def _replace_system_members(cur, universe_id: int, members: list[dict], effective_from: date) -> None:
        cur.execute(
            "DELETE FROM qd_universe_members WHERE universe_id = ? AND valid_from >= ?",
            (int(universe_id), effective_from),
        )
        cur.execute(
            """
            UPDATE qd_universe_members
            SET valid_to = ?, updated_at = NOW()
            WHERE universe_id = ?
              AND valid_from < ?
              AND (valid_to IS NULL OR valid_to > ?)
            """,
            (effective_from, int(universe_id), effective_from, effective_from),
        )
        source_version = f"admin:{effective_from.isoformat()}"
        for member in members:
            cur.execute(
                """
                INSERT INTO qd_universe_members
                  (universe_id, market, symbol, name, exchange_id, market_type,
                   instrument_id, settle_currency, valid_from, valid_to,
                   member_weight, member_rank, source_version, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)
                """,
                (
                    int(universe_id), member["market"], member["symbol"], member["name"],
                    member["exchange_id"], member["market_type"], member["instrument_id"],
                    member["settle_currency"], effective_from, member.get("weight"),
                    member.get("rank"), source_version,
                    json.dumps(member.get("metadata") or {}, ensure_ascii=False),
                ),
            )

    @staticmethod
    def _replace_static_members(cur, universe_id: int, members: list[dict]) -> None:
        cur.execute("DELETE FROM qd_universe_members WHERE universe_id = ?", (int(universe_id),))
        for member in members:
            cur.execute(
                """
                INSERT INTO qd_universe_members
                  (universe_id, market, symbol, name, exchange_id, market_type,
                   instrument_id, settle_currency, valid_from, valid_to,
                   member_weight, member_rank, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)
                """,
                (
                    int(universe_id), member["market"], member["symbol"], member["name"],
                    member["exchange_id"], member["market_type"], member["instrument_id"],
                    member["settle_currency"], STATIC_START, member.get("weight"),
                    member.get("rank"), json.dumps(member.get("metadata") or {}, ensure_ascii=False),
                ),
            )


def _serialize_universe(row: dict) -> dict:
    return {
        "id": int(row.get("id") or 0),
        "user_id": int(row.get("user_id")) if row.get("user_id") is not None else None,
        "code": str(row.get("code") or ""),
        "name": str(row.get("name") or ""),
        "name_i18n_key": str(row.get("name_i18n_key") or ""),
        "market": str(row.get("market") or ""),
        "universe_type": str(row.get("universe_type") or ""),
        "source": str(row.get("source") or ""),
        "source_ref": str(row.get("source_ref") or ""),
        "is_system": bool(row.get("is_system")),
        "status": str(row.get("status") or ""),
        "member_count": int(row.get("member_count") or 0),
        "history_from": _iso(row.get("history_from")),
        "metadata": _json_value(row.get("metadata_json"), {}),
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
    }


def _serialize_member(row: dict) -> dict:
    return {
        "market": str(row.get("market") or ""),
        "symbol": str(row.get("symbol") or ""),
        "name": str(row.get("name") or ""),
        "exchange_id": str(row.get("exchange_id") or ""),
        "market_type": str(row.get("market_type") or ""),
        "instrument_id": str(row.get("instrument_id") or ""),
        "settle_currency": str(row.get("settle_currency") or ""),
        "weight": _optional_float(row.get("weight")),
        "rank": _optional_int(row.get("rank")),
        "source_version": str(row.get("source_version") or ""),
        "valid_from": _iso(row.get("valid_from")),
        "valid_to": _iso(row.get("valid_to")),
        "metadata": _json_value(row.get("metadata"), {}),
    }


def _json_value(value: Any, fallback: Any) -> Any:
    if value is None or value == "":
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def _optional_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise UniverseError("universe.invalidWeight") from exc


def _optional_int(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise UniverseError("universe.invalidRank") from exc


def _iso(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


_service: Optional[UniverseService] = None


def get_universe_service() -> UniverseService:
    global _service
    if _service is None:
        _service = UniverseService()
    return _service
