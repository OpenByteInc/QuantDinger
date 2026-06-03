"""
Grid cell state machine + persistent repository (P2 scaffolding, May 2026).

A *cell* is a single rung of a grid bot's price ladder. When the future
"true pre-placed limit orders" mode lands, the executor will:

  1. On strategy start, compute one cell per grid line and persist the IDLE
     rows here.
  2. Decide for each cell whether to place a resting BUY @ lower_price or a
     resting SELL @ upper_price (depends on current price vs cell range +
     ``gridDirection``).
  3. Subscribe to the exchange's user-data stream and transition cell state
     on every order ack / fill, **without** waiting for a polling tick.
  4. On a fill, place the paired counter-order so the cell is always ready
     to capture the next price oscillation.

This module currently provides ONLY the schema bridge — dataclass + CRUD
helpers — so the rest of the codebase can read and write cells safely.
The actual user-stream wiring will be added in a follow-up PR; until then
``GridCellRepository.bootstrap_idle_cells()`` is the only sanctioned writer
and is invoked from the (read-only) grid-bot dashboard endpoint.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from app.utils.db import get_db_connection
from app.utils.logger import get_logger

logger = get_logger(__name__)


class GridCellState(str, Enum):
    """Lifecycle states for one cell."""

    IDLE = "idle"
    BUY_OPEN = "buy_open"
    LONG_HELD = "long_held"
    SELL_OPEN = "sell_open"
    SHORT_HELD = "short_held"

    @classmethod
    def parse(cls, raw: Any) -> "GridCellState":
        if isinstance(raw, GridCellState):
            return raw
        s = str(raw or "").strip().lower()
        for st in cls:
            if st.value == s:
                return st
        return cls.IDLE


@dataclass
class GridCell:
    """In-memory representation of a single grid cell."""

    strategy_id: int
    symbol: str
    cell_index: int
    lower_price: float
    upper_price: float
    state: GridCellState = GridCellState.IDLE
    leg_size: float = 0.0
    leg_entry_price: float = 0.0
    working_order_id: str = ""
    last_event_ts: float = field(default_factory=time.time)
    extra: Dict[str, Any] = field(default_factory=dict)
    id: Optional[int] = None

    def to_db_tuple(self) -> tuple:
        return (
            int(self.strategy_id),
            str(self.symbol),
            int(self.cell_index),
            float(self.lower_price),
            float(self.upper_price),
            str(self.state.value),
            float(self.leg_size or 0.0),
            float(self.leg_entry_price or 0.0),
            str(self.working_order_id or ""),
            json.dumps(self.extra or {}),
        )

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "GridCell":
        extra = row.get("extra") or {}
        if isinstance(extra, str):
            try:
                extra = json.loads(extra)
            except Exception:
                extra = {}
        ts = row.get("last_event_ts")
        ts_f: float
        try:
            ts_f = float(ts.timestamp()) if hasattr(ts, "timestamp") else float(ts or time.time())
        except Exception:
            ts_f = time.time()
        return cls(
            id=int(row["id"]) if row.get("id") is not None else None,
            strategy_id=int(row["strategy_id"]),
            symbol=str(row["symbol"]),
            cell_index=int(row["cell_index"]),
            lower_price=float(row["lower_price"]),
            upper_price=float(row["upper_price"]),
            state=GridCellState.parse(row.get("state")),
            leg_size=float(row.get("leg_size") or 0.0),
            leg_entry_price=float(row.get("leg_entry_price") or 0.0),
            working_order_id=str(row.get("working_order_id") or ""),
            last_event_ts=ts_f,
            extra=extra if isinstance(extra, dict) else {},
        )


def _ensure_table() -> None:
    """Idempotent schema guard so old deployments still pick up the table."""
    try:
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS qd_grid_cells (
                    id SERIAL PRIMARY KEY,
                    strategy_id INTEGER NOT NULL REFERENCES qd_strategies_trading(id) ON DELETE CASCADE,
                    symbol VARCHAR(50) NOT NULL,
                    cell_index INTEGER NOT NULL,
                    lower_price DECIMAL(20,8) NOT NULL,
                    upper_price DECIMAL(20,8) NOT NULL,
                    state VARCHAR(24) NOT NULL DEFAULT 'idle',
                    leg_size DECIMAL(20,8) DEFAULT 0,
                    leg_entry_price DECIMAL(20,8) DEFAULT 0,
                    working_order_id VARCHAR(64) DEFAULT '',
                    last_event_ts TIMESTAMP DEFAULT NOW(),
                    extra JSONB DEFAULT '{}'::jsonb,
                    CONSTRAINT uniq_grid_cell UNIQUE(strategy_id, symbol, cell_index)
                )
                """
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_grid_cells_strategy ON qd_grid_cells(strategy_id)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_grid_cells_state ON qd_grid_cells(strategy_id, state)"
            )
            db.commit()
            cur.close()
    except Exception as e:
        logger.warning(f"Failed to ensure qd_grid_cells table: {e}")


class GridCellRepository:
    """Persistence helpers for ``qd_grid_cells``.

    All methods are safe to call from synchronous code paths (no async).
    Bootstrapping is idempotent — calling it multiple times for the same
    strategy/symbol will only insert missing rows.
    """

    def __init__(self) -> None:
        _ensure_table()

    def bootstrap_idle_cells(
        self,
        strategy_id: int,
        symbol: str,
        levels: List[float],
    ) -> int:
        """Create one IDLE cell per pair of consecutive grid lines.

        ``levels`` must be ordered ascending. With N levels we get N-1 cells.
        Cell *i* spans [levels[i], levels[i+1]] and is keyed by ``cell_index = i``.

        Returns the number of newly inserted rows (existing cells are kept
        untouched so partial bootstraps don't clobber working orders).
        """
        if not levels or len(levels) < 2:
            return 0
        sorted_levels = sorted(float(x) for x in levels if x is not None and float(x) > 0)
        if len(sorted_levels) < 2:
            return 0

        inserted = 0
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                for idx in range(len(sorted_levels) - 1):
                    lo = sorted_levels[idx]
                    hi = sorted_levels[idx + 1]
                    if hi <= lo:
                        continue
                    cur.execute(
                        """
                        INSERT INTO qd_grid_cells
                        (strategy_id, symbol, cell_index, lower_price, upper_price, state, last_event_ts)
                        VALUES (%s, %s, %s, %s, %s, %s, NOW())
                        ON CONFLICT (strategy_id, symbol, cell_index) DO NOTHING
                        """,
                        (int(strategy_id), str(symbol), int(idx), lo, hi, GridCellState.IDLE.value),
                    )
                    try:
                        inserted += int(cur.rowcount or 0)
                    except Exception:
                        pass
                db.commit()
                cur.close()
        except Exception as e:
            logger.warning(f"bootstrap_idle_cells failed: strategy={strategy_id}, err={e}")
        return inserted

    def list_cells(self, strategy_id: int, symbol: Optional[str] = None) -> List[GridCell]:
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                if symbol:
                    cur.execute(
                        """
                        SELECT id, strategy_id, symbol, cell_index, lower_price, upper_price,
                               state, leg_size, leg_entry_price, working_order_id, last_event_ts, extra
                        FROM qd_grid_cells
                        WHERE strategy_id = %s AND symbol = %s
                        ORDER BY cell_index ASC
                        """,
                        (int(strategy_id), str(symbol)),
                    )
                else:
                    cur.execute(
                        """
                        SELECT id, strategy_id, symbol, cell_index, lower_price, upper_price,
                               state, leg_size, leg_entry_price, working_order_id, last_event_ts, extra
                        FROM qd_grid_cells
                        WHERE strategy_id = %s
                        ORDER BY symbol ASC, cell_index ASC
                        """,
                        (int(strategy_id),),
                    )
                rows = cur.fetchall() or []
                cur.close()
        except Exception as e:
            logger.warning(f"list_cells failed: strategy={strategy_id}, err={e}")
            return []
        out = []
        for row in rows:
            try:
                out.append(GridCell.from_row(dict(row)))
            except Exception as exc:
                logger.warning(f"list_cells skipped malformed row: {exc}")
        return out

    def update_state(
        self,
        strategy_id: int,
        symbol: str,
        cell_index: int,
        *,
        state: GridCellState,
        leg_size: Optional[float] = None,
        leg_entry_price: Optional[float] = None,
        working_order_id: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Idempotent partial update of a single cell."""
        sets = ["state = %s", "last_event_ts = NOW()"]
        args: List[Any] = [GridCellState.parse(state).value]
        if leg_size is not None:
            sets.append("leg_size = %s")
            args.append(float(leg_size))
        if leg_entry_price is not None:
            sets.append("leg_entry_price = %s")
            args.append(float(leg_entry_price))
        if working_order_id is not None:
            sets.append("working_order_id = %s")
            args.append(str(working_order_id))
        if extra is not None:
            sets.append("extra = %s")
            args.append(json.dumps(extra))
        args.extend([int(strategy_id), str(symbol), int(cell_index)])
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    f"""
                    UPDATE qd_grid_cells
                    SET {', '.join(sets)}
                    WHERE strategy_id = %s AND symbol = %s AND cell_index = %s
                    """,
                    tuple(args),
                )
                db.commit()
                affected = int(cur.rowcount or 0)
                cur.close()
            return affected > 0
        except Exception as e:
            logger.warning(f"update_state failed: strategy={strategy_id}, cell={cell_index}, err={e}")
            return False

    def release_cancelled_working_orders(self, strategy_id: int, symbol: Optional[str] = None) -> int:
        """Release cell state after strategy stop cancels all exchange working orders.

        Entry cells with cancelled limits become IDLE again so restart can re-hang
        them. Held inventory cells keep their held state and cost basis, but lose
        the stale working order reference so restart can re-create take-profit
        limits.
        """
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                filters = ["strategy_id = %s"]
                args: List[Any] = [int(strategy_id)]
                if symbol:
                    filters.append("symbol = %s")
                    args.append(str(symbol))
                filters_sql = " AND ".join(filters)
                cur.execute(
                    f"""
                    UPDATE qd_grid_cells
                    SET
                        state = CASE
                            WHEN state IN (%s, %s) THEN %s
                            ELSE state
                        END,
                        leg_size = CASE
                            WHEN state IN (%s, %s) THEN 0
                            ELSE leg_size
                        END,
                        leg_entry_price = CASE
                            WHEN state IN (%s, %s) THEN 0
                            ELSE leg_entry_price
                        END,
                        working_order_id = '',
                        last_event_ts = NOW()
                    WHERE {filters_sql}
                      AND state IN (%s, %s, %s, %s)
                    """,
                    (
                        GridCellState.BUY_OPEN.value,
                        GridCellState.SELL_OPEN.value,
                        GridCellState.IDLE.value,
                        GridCellState.BUY_OPEN.value,
                        GridCellState.SELL_OPEN.value,
                        GridCellState.BUY_OPEN.value,
                        GridCellState.SELL_OPEN.value,
                        *args,
                        GridCellState.BUY_OPEN.value,
                        GridCellState.SELL_OPEN.value,
                        GridCellState.LONG_HELD.value,
                        GridCellState.SHORT_HELD.value,
                    ),
                )
                n = int(cur.rowcount or 0)
                db.commit()
                cur.close()
                return n
        except Exception as e:
            logger.warning(f"release_cancelled_working_orders failed: strategy={strategy_id}, err={e}")
            return 0

    def delete_cells(self, strategy_id: int, symbol: Optional[str] = None) -> int:
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                if symbol:
                    cur.execute(
                        "DELETE FROM qd_grid_cells WHERE strategy_id = %s AND symbol = %s",
                        (int(strategy_id), str(symbol)),
                    )
                else:
                    cur.execute(
                        "DELETE FROM qd_grid_cells WHERE strategy_id = %s",
                        (int(strategy_id),),
                    )
                deleted = int(cur.rowcount or 0)
                db.commit()
                cur.close()
            return deleted
        except Exception as e:
            logger.warning(f"delete_cells failed: strategy={strategy_id}, err={e}")
            return 0
