"""
DB helpers for recording live trades and maintaining local position snapshots.

Important:
- This is a local DB snapshot, not the source of truth (exchange is).
- We keep it best-effort to support UI display and strategy state.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Set, Tuple

from app.utils.db import get_db_connection


def normalize_strategy_symbol(symbol: str) -> str:
    """
    Canonical symbol for qd_strategy_positions / qd_strategy_trades (e.g. BTC/USDT).

    Mixed formats (BTCUSDT vs BTC/USDT) previously broke position lookup, so closes
    had no local entry_price and profit stayed NULL.
    """
    s = str(symbol or "").strip().upper().replace("-", "")
    if not s:
        return ""
    if "/" in s:
        return s
    for quote in ("USDT", "USDC", "USD", "BUSD", "EUR"):
        if s.endswith(quote) and len(s) > len(quote):
            return f"{s[: -len(quote)]}/{quote}"
    return s


def _position_symbol_candidates(symbol: str) -> List[str]:
    """Unique symbol strings to try when resolving a position row."""
    raw = str(symbol or "").strip()
    if not raw:
        return []
    norm = normalize_strategy_symbol(raw)
    compact = norm.replace("/", "")
    raw_compact = raw.upper().replace("/", "").replace("-", "")
    out: List[str] = []
    for x in (raw, raw.upper(), norm, compact, raw_compact):
        if x and x not in out:
            out.append(x)
    return out


def fetch_position_size_for_side(strategy_id: int, symbol: str, side: str) -> float:
    """Return local DB position size for (strategy, symbol, side), trying symbol aliases."""
    row, _ = _fetch_position_fuzzy(strategy_id, symbol, side)
    if not row:
        return 0.0
    try:
        return max(0.0, float(row.get("size") or 0.0))
    except Exception:
        return 0.0


def _fetch_position_fuzzy(strategy_id: int, symbol: str, side: str) -> Tuple[Dict[str, Any], str]:
    """
    Find a non-empty position row; return (row, db_symbol_to_use).
    If none, db_symbol_to_use is the canonical form for new rows.
    """
    side_l = str(side or "").strip().lower()
    for sym in _position_symbol_candidates(symbol):
        row = _fetch_position(strategy_id, sym, side_l)
        if row and float(row.get("size") or 0.0) > 0:
            db_sym = str(row.get("symbol") or sym).strip()
            return row, db_sym or sym
    canon = normalize_strategy_symbol(symbol) or str(symbol or "").strip()
    return {}, canon


def strategy_allowed_symbols(strategy_config: Dict[str, Any]) -> Set[str]:
    """
    Symbols a strategy is allowed to own in ``qd_strategy_positions``.

    Used by position sync to avoid pulling unrelated exchange positions while
    still covering the common case where the symbol lives only in
    ``trading_config['symbol']`` (grid/bot strategies).
    """
    allowed: Set[str] = set()
    trading_config = strategy_config.get("trading_config") or {}
    if not isinstance(trading_config, dict):
        trading_config = {}

    for raw in (strategy_config.get("symbol"), trading_config.get("symbol")):
        norm = normalize_strategy_symbol(str(raw or "").strip())
        if norm:
            allowed.add(norm.upper())

    for sym in trading_config.get("symbol_list") or []:
        if not sym or not isinstance(sym, str):
            continue
        bare = sym.strip()
        if ":" in bare:
            bare = bare.split(":", 1)[-1]
        norm = normalize_strategy_symbol(bare)
        if norm:
            allowed.add(norm.upper())
    return allowed


def lookup_exchange_side_qty(
    exch_size: Dict[str, Dict[str, float]],
    symbol: str,
    side: str,
) -> float:
    """Resolve exchange size for a local row, tolerating BTC/USDT vs BTCUSDT keys."""
    side_l = str(side or "").strip().lower()
    if side_l not in ("long", "short"):
        return 0.0
    norm_index: Dict[str, Dict[str, float]] = {}
    for sym_key, sides in (exch_size or {}).items():
        norm = normalize_strategy_symbol(str(sym_key or "").strip()).upper()
        if not norm:
            continue
        bucket = norm_index.setdefault(norm, {"long": 0.0, "short": 0.0})
        for leg in ("long", "short"):
            try:
                bucket[leg] = max(float(bucket.get(leg) or 0.0), float((sides or {}).get(leg) or 0.0))
            except Exception:
                pass
    for sym in _position_symbol_candidates(symbol):
        norm = normalize_strategy_symbol(sym).upper()
        if norm in norm_index:
            try:
                return float(norm_index[norm].get(side_l) or 0.0)
            except Exception:
                return 0.0
    return 0.0


def lookup_exchange_entry_price(
    exch_entry_price: Dict[str, Dict[str, float]],
    symbol: str,
    side: str,
) -> float:
    side_l = str(side or "").strip().lower()
    if side_l not in ("long", "short"):
        return 0.0
    norm_index: Dict[str, Dict[str, float]] = {}
    for sym_key, sides in (exch_entry_price or {}).items():
        norm = normalize_strategy_symbol(str(sym_key or "").strip()).upper()
        if not norm:
            continue
        bucket = norm_index.setdefault(norm, {"long": 0.0, "short": 0.0})
        for leg in ("long", "short"):
            try:
                ep = float((sides or {}).get(leg) or 0.0)
                if ep > 0:
                    bucket[leg] = ep
            except Exception:
                pass
    for sym in _position_symbol_candidates(symbol):
        norm = normalize_strategy_symbol(sym).upper()
        if norm in norm_index:
            try:
                return float(norm_index[norm].get(side_l) or 0.0)
            except Exception:
                return 0.0
    return 0.0


def rebuild_positions_from_trades(strategy_id: int) -> bool:
    """
    Rebuild local position rows by replaying trade history.

    Best-effort repair when trades were recorded but the position snapshot was
    never written (older workers) or was cleared by a failed sync.
    """
    sid = int(strategy_id)
    if sid <= 0:
        return False
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            "SELECT COUNT(*) AS c FROM qd_strategy_positions WHERE strategy_id = %s",
            (sid,),
        )
        existing = int((cur.fetchone() or {}).get("c") or 0)
        if existing > 0:
            cur.close()
            return False
        cur.execute(
            """
            SELECT type, symbol, amount, price
            FROM qd_strategy_trades
            WHERE strategy_id = %s
            ORDER BY id ASC
            """,
            (sid,),
        )
        trades = cur.fetchall() or []
        cur.close()
    if not trades:
        return False
    for row in trades:
        apply_fill_to_local_position(
            strategy_id=sid,
            symbol=str(row.get("symbol") or ""),
            signal_type=str(row.get("type") or ""),
            filled=float(row.get("amount") or 0.0),
            avg_price=float(row.get("price") or 0.0),
        )
    return True


def _resolve_write_symbol(current: Dict[str, Any], cur_size: float, input_symbol: str) -> str:
    """Use existing DB symbol when updating a row; otherwise canonical new key."""
    if cur_size > 0 and current and str(current.get("symbol") or "").strip():
        return str(current.get("symbol") or "").strip()
    return normalize_strategy_symbol(input_symbol) or str(input_symbol or "").strip()


def _get_user_id_from_strategy(strategy_id: int) -> int:
    """Get user_id from strategy table. Defaults to 1 if not found."""
    try:
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute("SELECT user_id FROM qd_strategies_trading WHERE id = %s", (strategy_id,))
            row = cur.fetchone()
            cur.close()
        return int((row or {}).get('user_id') or 1)
    except Exception:
        return 1


def ensure_strategy_trades_close_reason_column() -> None:
    """Idempotent schema guard for late-added columns on qd_strategy_trades.

    The function is invoked at executor startup, so it doubles as the
    auto-migration for any column added after the initial release.
    """
    statements = (
        "ALTER TABLE qd_strategy_trades ADD COLUMN IF NOT EXISTS close_reason VARCHAR(64) DEFAULT ''",
        # P1-1 (May 2026): per-trade grid matched PnL + the FIFO entry price
        # the leg was retired at. ``profit`` is kept as the absolute realised
        # PnL; ``grid_matched_profit`` is the same value but the UI uses it to
        # render a dedicated "grid profit" column for grid/DCA bots.
        "ALTER TABLE qd_strategy_trades ADD COLUMN IF NOT EXISTS matched_entry_price DECIMAL(20,8) DEFAULT 0",
        "ALTER TABLE qd_strategy_trades ADD COLUMN IF NOT EXISTS grid_matched_profit DECIMAL(20,8) DEFAULT 0",
    )
    for sql in statements:
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(sql)
                db.commit()
                cur.close()
        except Exception:
            pass


def record_trade(
    *,
    strategy_id: int,
    symbol: str,
    trade_type: str,
    price: float,
    amount: float,
    commission: float = 0.0,
    commission_ccy: str = "",
    profit: Optional[float] = None,
    close_reason: str = "",
    user_id: int = None,
    matched_entry_price: Optional[float] = None,
    grid_matched_profit: Optional[float] = None,
) -> None:
    value = float(amount or 0.0) * float(price or 0.0)
    if user_id is None:
        user_id = _get_user_id_from_strategy(strategy_id)
    sym_out = normalize_strategy_symbol(symbol) or str(symbol or "").strip()
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            INSERT INTO qd_strategy_trades
            (user_id, strategy_id, symbol, type, price, amount, value, commission,
             commission_ccy, profit, close_reason,
             matched_entry_price, grid_matched_profit, created_at)
            VALUES
            (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
            """,
            (
                int(user_id),
                int(strategy_id),
                sym_out,
                str(trade_type),
                float(price or 0.0),
                float(amount or 0.0),
                float(value),
                float(commission or 0.0),
                str(commission_ccy or ""),
                profit,
                str(close_reason or "").strip(),
                float(matched_entry_price) if matched_entry_price is not None else 0.0,
                float(grid_matched_profit) if grid_matched_profit is not None else 0.0,
            ),
        )
        db.commit()
        cur.close()


def _fetch_position(strategy_id: int, symbol: str, side: str) -> Dict[str, Any]:
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            "SELECT * FROM qd_strategy_positions WHERE strategy_id = %s AND symbol = %s AND side = %s",
            (int(strategy_id), str(symbol), str(side)),
        )
        row = cur.fetchone() or {}
        cur.close()
    return row if isinstance(row, dict) else {}


def _delete_position(strategy_id: int, symbol: str, side: str) -> None:
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            "DELETE FROM qd_strategy_positions WHERE strategy_id = %s AND symbol = %s AND side = %s",
            (int(strategy_id), str(symbol), str(side)),
        )
        db.commit()
        cur.close()


def upsert_position(
    *,
    strategy_id: int,
    symbol: str,
    side: str,
    size: float,
    entry_price: float,
    current_price: float,
    highest_price: float = 0.0,
    lowest_price: float = 0.0,
    user_id: int = None,
) -> None:
    if user_id is None:
        user_id = _get_user_id_from_strategy(strategy_id)
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            INSERT INTO qd_strategy_positions
            (user_id, strategy_id, symbol, side, size, entry_price, current_price, highest_price, lowest_price, updated_at)
            VALUES
            (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT(strategy_id, symbol, side) DO UPDATE SET
                size = excluded.size,
                entry_price = excluded.entry_price,
                current_price = excluded.current_price,
                highest_price = CASE WHEN excluded.highest_price > 0 THEN excluded.highest_price ELSE qd_strategy_positions.highest_price END,
                lowest_price = CASE WHEN excluded.lowest_price > 0 THEN excluded.lowest_price ELSE qd_strategy_positions.lowest_price END,
                updated_at = NOW()
            """,
            (int(user_id), int(strategy_id), str(symbol), str(side), float(size or 0.0), float(entry_price or 0.0), float(current_price or 0.0), float(highest_price or 0.0), float(lowest_price or 0.0)),
        )
        db.commit()
        cur.close()


def apply_fill_to_local_position(
    *,
    strategy_id: int,
    symbol: str,
    signal_type: str,
    filled: float,
    avg_price: float,
) -> Tuple[Optional[float], Optional[Dict[str, Any]], Optional[float]]:
    """
    Apply a fill to the local position snapshot.

    Returns ``(profit, updated_position_row_or_none, matched_entry_price)``.
      * ``profit`` and ``matched_entry_price`` are only populated on close /
        reduce fills (best-effort, based on the local entry_price snapshot).
      * ``matched_entry_price`` is the FIFO-averaged entry price of the leg
        that was (partially) closed by this fill — i.e. the cost basis of the
        matched grid trade. Surfaced so the executor can persist it on the
        trade row and the UI can compute / show "grid profit per match".
    """
    sig = (signal_type or "").strip().lower()
    filled_qty = float(filled or 0.0)
    px = float(avg_price or 0.0)
    if filled_qty <= 0 or px <= 0:
        return None, None, None

    if "long" in sig:
        side = "long"
    elif "short" in sig:
        side = "short"
    else:
        return None, None, None

    is_open = sig.startswith("open_") or sig.startswith("add_")
    is_close = sig.startswith("close_") or sig.startswith("reduce_")

    sid = int(strategy_id)
    current, _matched = _fetch_position_fuzzy(sid, symbol, side)
    cur_size = float(current.get("size") or 0.0)
    cur_entry = float(current.get("entry_price") or 0.0)
    cur_high = float(current.get("highest_price") or 0.0)
    cur_low = float(current.get("lowest_price") or 0.0)
    sym_key = _resolve_write_symbol(current, cur_size, symbol)

    profit: Optional[float] = None
    matched_entry: Optional[float] = None

    if is_open:
        new_size = cur_size + filled_qty
        if new_size <= 0:
            return None, None
        # Weighted average entry.
        if cur_size > 0 and cur_entry > 0:
            new_entry = (cur_size * cur_entry + filled_qty * px) / new_size
        else:
            new_entry = px
        new_high = max(cur_high or px, px)
        new_low = min(cur_low or px, px)
        upsert_position(
            strategy_id=sid,
            symbol=sym_key,
            side=side,
            size=new_size,
            entry_price=new_entry,
            current_price=px,
            highest_price=new_high,
            lowest_price=new_low,
        )
        return None, _fetch_position(sid, sym_key, side), None

    if is_close:
        # Calculate PnL using local entry price.
        if cur_size > 0 and cur_entry > 0:
            close_qty = min(cur_size, filled_qty)
            if side == "long":
                profit = (px - cur_entry) * close_qty
            else:
                profit = (cur_entry - px) * close_qty
            matched_entry = cur_entry

        new_size = cur_size - filled_qty
        if new_size <= 0:
            _delete_position(sid, sym_key, side)
            return profit, None, matched_entry
        # Keep entry price for remaining position.
        new_high = max(cur_high or px, px)
        new_low = min(cur_low or px, px)
        upsert_position(
            strategy_id=sid,
            symbol=sym_key,
            side=side,
            size=new_size,
            entry_price=cur_entry if cur_entry > 0 else px,
            current_price=px,
            highest_price=new_high,
            lowest_price=new_low,
        )
        return profit, _fetch_position(sid, sym_key, side), matched_entry

    return None, None, None


