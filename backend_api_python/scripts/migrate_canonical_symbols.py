#!/usr/bin/env python3
"""
One-off migration helper: canonical symbols + merge duplicate position legs.

Run from backend_api_python/:
  python scripts/migrate_canonical_symbols.py
  python scripts/migrate_canonical_symbols.py --apply

Requires P1 columns (symbol_canonical) — apply 20260601_position_ledger_p1.sql first
or rely on ADD COLUMN IF NOT EXISTS in application startup guards.
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from typing import Any, Dict, List, Tuple

# Allow running as script from backend_api_python/
if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, ".")

from app.services.live_trading.records import normalize_strategy_symbol
from app.utils.db import get_db_connection


def _canon(symbol: str) -> str:
    return normalize_strategy_symbol(symbol) or str(symbol or "").strip()


def backfill_trades(dry_run: bool) -> int:
    updated = 0
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT id, symbol, symbol_canonical
            FROM qd_strategy_trades
            WHERE COALESCE(symbol_canonical, '') = ''
            """
        )
        rows = cur.fetchall() or []
        for r in rows:
            canon = _canon(str(r.get("symbol") or ""))
            if not canon:
                continue
            updated += 1
            if not dry_run:
                cur.execute(
                    "UPDATE qd_strategy_trades SET symbol_canonical = %s WHERE id = %s",
                    (canon, int(r["id"])),
                )
        if not dry_run:
            db.commit()
        cur.close()
    return updated


def backfill_positions(dry_run: bool) -> int:
    updated = 0
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT id, symbol, symbol_canonical
            FROM qd_strategy_positions
            WHERE COALESCE(symbol_canonical, '') = ''
            """
        )
        rows = cur.fetchall() or []
        for r in rows:
            canon = _canon(str(r.get("symbol") or ""))
            if not canon:
                continue
            updated += 1
            if not dry_run:
                cur.execute(
                    """
                    UPDATE qd_strategy_positions
                    SET symbol_canonical = %s, symbol = %s
                    WHERE id = %s
                    """,
                    (canon, canon, int(r["id"])),
                )
        if not dry_run:
            db.commit()
        cur.close()
    return updated


def find_duplicate_legs() -> List[Dict[str, Any]]:
    """Rows sharing (strategy_id, market_type, side) but different symbol / canonical."""
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT id, strategy_id, market_type, side, symbol, symbol_canonical, size, entry_price
            FROM qd_strategy_positions
            ORDER BY strategy_id, market_type, side, id
            """
        )
        rows = cur.fetchall() or []
        cur.close()

    buckets: Dict[Tuple[int, str, str], List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        sid = int(r.get("strategy_id") or 0)
        mt = str(r.get("market_type") or "swap").strip().lower()
        side = str(r.get("side") or "").strip().lower()
        canon = str(r.get("symbol_canonical") or _canon(r.get("symbol") or "")).upper()
        key = (sid, mt, side)
        buckets[key].append({**dict(r), "_canon": canon})

    dup_groups: List[Dict[str, Any]] = []
    for key, items in buckets.items():
        by_canon: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for it in items:
            by_canon[it["_canon"]].append(it)
        canon_keys = list(by_canon.keys())
        if len(items) > 1 and (len(canon_keys) < len(items) or len(canon_keys) > 1):
            dup_groups.append({"key": key, "items": items, "by_canon": dict(by_canon)})
    return dup_groups


def merge_duplicate_legs(dry_run: bool) -> int:
    """Merge multiple rows for same (strategy_id, market_type, side, canonical symbol)."""
    merged = 0
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT id, strategy_id, market_type, side, symbol, symbol_canonical,
                   size, entry_price
            FROM qd_strategy_positions
            ORDER BY strategy_id, id
            """
        )
        rows = cur.fetchall() or []
        cur.close()

    groups: Dict[Tuple[int, str, str, str], List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        sid = int(r.get("strategy_id") or 0)
        mt = str(r.get("market_type") or "swap").strip().lower()
        side = str(r.get("side") or "").strip().lower()
        canon = str(r.get("symbol_canonical") or _canon(r.get("symbol") or "")).upper()
        groups[(sid, mt, side, canon)].append(dict(r))

    with get_db_connection() as db:
        cur = db.cursor()
        for (_sid, _mt, _side, _canon), items in groups.items():
            if len(items) <= 1:
                continue
            total_size = sum(float(x.get("size") or 0.0) for x in items)
            # Weighted entry
            num = sum(float(x.get("size") or 0.0) * float(x.get("entry_price") or 0.0) for x in items)
            entry = (num / total_size) if total_size > 0 else float(items[0].get("entry_price") or 0.0)
            keep_id = min(int(x["id"]) for x in items)
            drop_ids = [int(x["id"]) for x in items if int(x["id"]) != keep_id]
            merged += 1
            print(
                f"[merge] strategy={_sid} {_mt} {_side} {_canon}: "
                f"keep id={keep_id} size={total_size:.8f} drop={drop_ids}"
            )
            if dry_run:
                continue
            cur.execute(
                """
                UPDATE qd_strategy_positions
                SET symbol = %s, symbol_canonical = %s, size = %s, entry_price = %s
                WHERE id = %s
                """,
                (_canon, _canon, total_size, entry, keep_id),
            )
            for did in drop_ids:
                cur.execute("DELETE FROM qd_strategy_positions WHERE id = %s", (did,))
        if not dry_run:
            db.commit()
        cur.close()
    return merged


def main() -> int:
    parser = argparse.ArgumentParser(description="Canonical symbol migration")
    parser.add_argument("--apply", action="store_true", help="Write changes (default: dry-run)")
    args = parser.parse_args()
    dry_run = not args.apply

    if dry_run:
        print("DRY RUN — no writes")

    t = backfill_trades(dry_run)
    p = backfill_positions(dry_run)
    print(f"backfill trades: {t}, positions: {p}")

    dups = find_duplicate_legs()
    if dups:
        print(f"potential duplicate leg groups: {len(dups)}")
        for g in dups[:20]:
            print(f"  strategy={g['key']} rows={len(g['items'])}")

    m = merge_duplicate_legs(dry_run)
    print(f"merged leg groups: {m}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
