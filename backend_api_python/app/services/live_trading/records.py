"""
DB helpers for recording live trades and maintaining local position snapshots.

Important:
- This is a local DB snapshot, not the source of truth (exchange is).
- We keep it best-effort to support UI display and strategy state.

Live ownership:
- ``size`` / ``entry_price``: ``PendingOrderWorker.apply_fill`` + position sync.
- ``highest_price`` / ``lowest_price`` / ``current_price``: executor may patch
  existing rows only (``patch_position_markers``); never insert in live mode.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Set, Tuple, TYPE_CHECKING

from app.utils.db import get_db_connection

if TYPE_CHECKING:
    from app.services.live_trading.leg_context import LegContext


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


def strategy_has_trades_for_symbol(strategy_id: int, symbol: str) -> bool:
    """True when this strategy has at least one trade row for the symbol (any alias)."""
    sid = int(strategy_id)
    if sid <= 0:
        return False
    cands = _position_symbol_candidates(symbol)
    if not cands:
        return False
    placeholders = ", ".join(["%s"] * len(cands))
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            f"""
            SELECT 1 FROM qd_strategy_trades
            WHERE strategy_id = %s AND symbol IN ({placeholders})
            LIMIT 1
            """,
            (sid, *cands),
        )
        hit = cur.fetchone()
        cur.close()
    return bool(hit)


def _fetch_position_fuzzy(strategy_id: int, symbol: str, side: str) -> Tuple[Dict[str, Any], str]:
    """
    Find a non-empty position row; return (row, db_symbol_to_use).
    If none, db_symbol_to_use is the canonical form for new rows.
    """
    side_l = str(side or "").strip().lower()
    for sym in _position_symbol_candidates(symbol):
        row = _fetch_position(strategy_id, sym, side_l)
        if row and float(row.get("size") or 0.0) > 0:
            canon = normalize_strategy_symbol(symbol) or str(symbol or "").strip()
            return row, canon
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
    never written (older workers). For ``execution_mode='live'``, an empty
    position table usually means position sync confirmed the exchange is flat —
    do not call this from the positions API (would recreate ghost rows).
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
            SELECT type, symbol, amount, price, market_type, credential_id, inst_id, fill_source
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
    from app.services.live_trading.leg_context import LegContext

    for row in trades:
        mt = str(row.get("market_type") or "swap").strip().lower()
        if mt in ("futures", "future", "perp", "perpetual"):
            mt = "swap"
        leg = LegContext(
            market_type=mt or "swap",
            credential_id=int(row.get("credential_id") or 0),
            inst_id=str(row.get("inst_id") or ""),
            fill_source=str(row.get("fill_source") or "replay"),
        )
        apply_fill_to_local_position(
            strategy_id=sid,
            symbol=normalize_strategy_symbol(str(row.get("symbol") or "")) or str(row.get("symbol") or ""),
            signal_type=str(row.get("type") or ""),
            filled=float(row.get("amount") or 0.0),
            avg_price=float(row.get("price") or 0.0),
            leg=leg,
        )
    return True


def _resolve_write_symbol(current: Dict[str, Any], cur_size: float, input_symbol: str) -> str:
    """Canonical symbol key for qd_strategy_positions (UNIQUE with side)."""
    return normalize_strategy_symbol(input_symbol) or str(input_symbol or "").strip()


def _purge_position_symbol_duplicates(strategy_id: int, side: str, canonical: str) -> None:
    """Remove legacy alias rows (ETHUSDT vs ETH/USDT) after writing the canonical row."""
    canon = normalize_strategy_symbol(canonical) or str(canonical or "").strip()
    if not canon:
        return
    side_l = str(side or "").strip().lower()
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            "SELECT id, symbol FROM qd_strategy_positions WHERE strategy_id = %s AND side = %s",
            (int(strategy_id), side_l),
        )
        rows = cur.fetchall() or []
        for r in rows:
            sym = str(r.get("symbol") or "").strip()
            if not sym or sym == canon:
                continue
            if normalize_strategy_symbol(sym) == canon:
                cur.execute("DELETE FROM qd_strategy_positions WHERE id = %s", (int(r.get("id") or 0),))
        db.commit()
        cur.close()


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
    """Backward-compatible alias — prefer ``ensure_position_ledger_schema``."""
    ensure_position_ledger_schema()


def ensure_position_ledger_schema() -> None:
    """Idempotent schema guard for ledger / position redesign columns."""
    statements = (
        "ALTER TABLE qd_strategy_trades ADD COLUMN IF NOT EXISTS close_reason VARCHAR(64) DEFAULT ''",
        "ALTER TABLE qd_strategy_trades ADD COLUMN IF NOT EXISTS matched_entry_price DECIMAL(20,8) DEFAULT 0",
        "ALTER TABLE qd_strategy_trades ADD COLUMN IF NOT EXISTS grid_matched_profit DECIMAL(20,8) DEFAULT 0",
        "ALTER TABLE qd_strategy_trades ADD COLUMN IF NOT EXISTS market_type VARCHAR(20) DEFAULT 'swap'",
        "ALTER TABLE qd_strategy_trades ADD COLUMN IF NOT EXISTS credential_id INTEGER DEFAULT 0",
        "ALTER TABLE qd_strategy_trades ADD COLUMN IF NOT EXISTS inst_id VARCHAR(80) DEFAULT ''",
        "ALTER TABLE qd_strategy_trades ADD COLUMN IF NOT EXISTS symbol_canonical VARCHAR(50) DEFAULT ''",
        "ALTER TABLE qd_strategy_trades ADD COLUMN IF NOT EXISTS fill_source VARCHAR(32) DEFAULT ''",
        "ALTER TABLE qd_strategy_trades ADD COLUMN IF NOT EXISTS pending_order_id INTEGER DEFAULT 0",
        "ALTER TABLE qd_strategy_positions ADD COLUMN IF NOT EXISTS market_type VARCHAR(20) DEFAULT 'swap'",
        "ALTER TABLE qd_strategy_positions ADD COLUMN IF NOT EXISTS credential_id INTEGER DEFAULT 0",
        "ALTER TABLE qd_strategy_positions ADD COLUMN IF NOT EXISTS inst_id VARCHAR(80) DEFAULT ''",
        "ALTER TABLE qd_strategy_positions ADD COLUMN IF NOT EXISTS symbol_canonical VARCHAR(50) DEFAULT ''",
        "ALTER TABLE pending_orders ADD COLUMN IF NOT EXISTS credential_id INTEGER DEFAULT 0",
        "ALTER TABLE pending_orders ADD COLUMN IF NOT EXISTS inst_id VARCHAR(80) DEFAULT ''",
        """
        CREATE TABLE IF NOT EXISTS qd_account_positions (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL DEFAULT 1 REFERENCES qd_users(id) ON DELETE CASCADE,
            credential_id INTEGER NOT NULL DEFAULT 0,
            exchange_id VARCHAR(40) NOT NULL DEFAULT '',
            market_type VARCHAR(20) NOT NULL DEFAULT 'swap',
            inst_id VARCHAR(80) NOT NULL DEFAULT '',
            symbol VARCHAR(50) NOT NULL DEFAULT '',
            side VARCHAR(10) NOT NULL DEFAULT '',
            size DECIMAL(24, 8) NOT NULL DEFAULT 0,
            entry_price DECIMAL(24, 8) DEFAULT 0,
            mark_price DECIMAL(24, 8) DEFAULT 0,
            unrealized_pnl DECIMAL(24, 8) DEFAULT 0,
            raw_json JSONB DEFAULT '{}'::jsonb,
            synced_at TIMESTAMP NOT NULL DEFAULT NOW(),
            UNIQUE (credential_id, market_type, inst_id, side)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_account_pos_user ON qd_account_positions(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_account_pos_cred ON qd_account_positions(credential_id, market_type)",
        "CREATE INDEX IF NOT EXISTS idx_trades_strategy_symbol_canon ON qd_strategy_trades (strategy_id, market_type, symbol_canonical)",
        "CREATE INDEX IF NOT EXISTS idx_positions_strategy_leg ON qd_strategy_positions (strategy_id, market_type, symbol_canonical, side)",
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
    leg: Optional["LegContext"] = None,
    market_type: str = "",
    credential_id: int = 0,
    inst_id: str = "",
    fill_source: str = "",
    pending_order_id: int = 0,
) -> None:
    value = float(amount or 0.0) * float(price or 0.0)
    if user_id is None:
        user_id = _get_user_id_from_strategy(strategy_id)
    sym_out = normalize_strategy_symbol(symbol) or str(symbol or "").strip()

    mt = str(market_type or "").strip().lower()
    cred = int(credential_id or 0)
    iid = str(inst_id or "").strip()
    fsrc = str(fill_source or "").strip()
    poid = int(pending_order_id or 0)
    if leg is not None:
        mt = mt or leg.normalized_market_type()
        cred = cred or int(leg.credential_id or 0)
        iid = iid or str(leg.inst_id or "")
        fsrc = fsrc or str(leg.fill_source or "")
        poid = poid or int(leg.pending_order_id or 0)
    if mt in ("futures", "future", "perp", "perpetual"):
        mt = "swap"
    if not mt:
        mt = "swap"

    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            INSERT INTO qd_strategy_trades
            (user_id, strategy_id, symbol, symbol_canonical, type, price, amount, value, commission,
             commission_ccy, profit, close_reason,
             matched_entry_price, grid_matched_profit,
             market_type, credential_id, inst_id, fill_source, pending_order_id, created_at)
            VALUES
            (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
            """,
            (
                int(user_id),
                int(strategy_id),
                sym_out,
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
                mt,
                cred,
                iid,
                fsrc,
                poid,
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
    side_l = str(side or "").strip().lower()
    seen: Set[str] = set()
    with get_db_connection() as db:
        cur = db.cursor()
        for sym in _position_symbol_candidates(symbol):
            if sym in seen:
                continue
            seen.add(sym)
            cur.execute(
                "DELETE FROM qd_strategy_positions WHERE strategy_id = %s AND symbol = %s AND side = %s",
                (int(strategy_id), str(sym), side_l),
            )
        db.commit()
        cur.close()


def patch_position_markers(
    *,
    strategy_id: int,
    symbol: str,
    side: str,
    current_price: float,
    highest_price: float = 0.0,
    lowest_price: float = 0.0,
) -> bool:
    """
    Update trailing / mark-price fields on an existing local row.

    Never inserts rows — live ``TradingExecutor`` uses this so it cannot
    resurrect a leg that ``PendingOrderWorker`` already closed.
    """
    side_l = str(side or "").strip().lower()
    if side_l not in ("long", "short"):
        return False
    px = float(current_price or 0.0)
    if px <= 0:
        return False

    row, sym_key = _fetch_position_fuzzy(int(strategy_id), symbol, side_l)
    if not row or float(row.get("size") or 0.0) <= 0:
        return False

    hp = float(highest_price or 0.0)
    lp = float(lowest_price or 0.0)
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            UPDATE qd_strategy_positions
            SET current_price = %s,
                highest_price = CASE WHEN %s > 0 THEN %s ELSE highest_price END,
                lowest_price = CASE WHEN %s > 0 THEN %s ELSE lowest_price END,
                updated_at = NOW()
            WHERE strategy_id = %s AND symbol = %s AND side = %s AND size > 0
            """,
            (
                px,
                hp,
                hp,
                lp,
                lp,
                int(strategy_id),
                str(sym_key),
                side_l,
            ),
        )
        updated = int(getattr(cur, "rowcount", 0) or 0) > 0
        db.commit()
        cur.close()
    return updated


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
    leg: Optional["LegContext"] = None,
    market_type: str = "",
    credential_id: int = 0,
    inst_id: str = "",
) -> None:
    if user_id is None:
        user_id = _get_user_id_from_strategy(strategy_id)
    sym_key = normalize_strategy_symbol(symbol) or str(symbol or "").strip()
    mt = str(market_type or "").strip().lower()
    cred = int(credential_id or 0)
    iid = str(inst_id or "").strip()
    if leg is not None:
        mt = mt or leg.normalized_market_type()
        cred = cred or int(leg.credential_id or 0)
        iid = iid or str(leg.inst_id or "")
    if mt in ("futures", "future", "perp", "perpetual"):
        mt = "swap"
    if not mt:
        mt = "swap"

    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            INSERT INTO qd_strategy_positions
            (user_id, strategy_id, symbol, symbol_canonical, side, size, entry_price, current_price,
             highest_price, lowest_price, market_type, credential_id, inst_id, updated_at)
            VALUES
            (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT(strategy_id, symbol, side) DO UPDATE SET
                symbol_canonical = excluded.symbol_canonical,
                size = excluded.size,
                entry_price = excluded.entry_price,
                current_price = excluded.current_price,
                highest_price = CASE WHEN excluded.highest_price > 0 THEN excluded.highest_price ELSE qd_strategy_positions.highest_price END,
                lowest_price = CASE WHEN excluded.lowest_price > 0 THEN excluded.lowest_price ELSE qd_strategy_positions.lowest_price END,
                market_type = excluded.market_type,
                credential_id = CASE WHEN excluded.credential_id > 0 THEN excluded.credential_id ELSE qd_strategy_positions.credential_id END,
                inst_id = CASE WHEN excluded.inst_id <> '' THEN excluded.inst_id ELSE qd_strategy_positions.inst_id END,
                updated_at = NOW()
            """,
            (
                int(user_id),
                int(strategy_id),
                sym_key,
                sym_key,
                str(side),
                float(size or 0.0),
                float(entry_price or 0.0),
                float(current_price or 0.0),
                float(highest_price or 0.0),
                float(lowest_price or 0.0),
                mt,
                cred,
                iid,
            ),
        )
        db.commit()
        cur.close()
    _purge_position_symbol_duplicates(int(strategy_id), str(side), sym_key)


def apply_fill_to_local_position(
    *,
    strategy_id: int,
    symbol: str,
    signal_type: str,
    filled: float,
    avg_price: float,
    leg: Optional["LegContext"] = None,
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
            leg=leg,
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
            leg=leg,
        )
        return profit, _fetch_position(sid, sym_key, side), matched_entry

    return None, None, None


