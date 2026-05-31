"""Persistence for resting grid limit orders."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.utils.db import get_db_connection
from app.utils.logger import get_logger

logger = get_logger(__name__)

OPEN_STATUSES = ("pending", "open", "partial")


def _ensure_table() -> None:
    try:
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS qd_grid_resting_orders (
                    id SERIAL PRIMARY KEY,
                    strategy_id INTEGER NOT NULL,
                    symbol VARCHAR(50) NOT NULL,
                    cell_index INTEGER NOT NULL DEFAULT 0,
                    purpose VARCHAR(24) NOT NULL,
                    side VARCHAR(8) NOT NULL,
                    pos_side VARCHAR(8) NOT NULL DEFAULT '',
                    reduce_only BOOLEAN NOT NULL DEFAULT FALSE,
                    price DECIMAL(24, 8) NOT NULL,
                    quantity DECIMAL(24, 8) NOT NULL DEFAULT 0,
                    quote_amount DECIMAL(24, 8) NOT NULL DEFAULT 0,
                    client_order_id VARCHAR(64) NOT NULL DEFAULT '',
                    exchange_order_id VARCHAR(64) NOT NULL DEFAULT '',
                    status VARCHAR(20) NOT NULL DEFAULT 'pending',
                    filled_quantity DECIMAL(24, 8) NOT NULL DEFAULT 0,
                    avg_fill_price DECIMAL(24, 8) NOT NULL DEFAULT 0,
                    processed_fill_qty DECIMAL(24, 8) NOT NULL DEFAULT 0,
                    extra JSONB DEFAULT '{}'::jsonb,
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                )
                """
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_grid_resting_strategy ON qd_grid_resting_orders(strategy_id, status)"
            )
            cur.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS uniq_grid_resting_client_oid "
                "ON qd_grid_resting_orders(strategy_id, client_order_id) WHERE client_order_id <> ''"
            )
            db.commit()
            cur.close()
    except Exception as e:
        logger.warning("ensure qd_grid_resting_orders failed: %s", e)


@dataclass
class GridRestingOrder:
    id: Optional[int] = None
    strategy_id: int = 0
    symbol: str = ""
    cell_index: int = 0
    purpose: str = ""
    side: str = ""
    pos_side: str = ""
    reduce_only: bool = False
    price: float = 0.0
    quantity: float = 0.0
    quote_amount: float = 0.0
    client_order_id: str = ""
    exchange_order_id: str = ""
    status: str = "pending"
    filled_quantity: float = 0.0
    avg_fill_price: float = 0.0
    processed_fill_qty: float = 0.0
    extra: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "GridRestingOrder":
        extra = row.get("extra") or {}
        if isinstance(extra, str):
            try:
                extra = json.loads(extra)
            except Exception:
                extra = {}
        return cls(
            id=int(row["id"]) if row.get("id") is not None else None,
            strategy_id=int(row.get("strategy_id") or 0),
            symbol=str(row.get("symbol") or ""),
            cell_index=int(row.get("cell_index") or 0),
            purpose=str(row.get("purpose") or ""),
            side=str(row.get("side") or ""),
            pos_side=str(row.get("pos_side") or ""),
            reduce_only=bool(row.get("reduce_only")),
            price=float(row.get("price") or 0),
            quantity=float(row.get("quantity") or 0),
            quote_amount=float(row.get("quote_amount") or 0),
            client_order_id=str(row.get("client_order_id") or ""),
            exchange_order_id=str(row.get("exchange_order_id") or ""),
            status=str(row.get("status") or "pending"),
            filled_quantity=float(row.get("filled_quantity") or 0),
            avg_fill_price=float(row.get("avg_fill_price") or 0),
            processed_fill_qty=float(row.get("processed_fill_qty") or 0),
            extra=extra if isinstance(extra, dict) else {},
        )


class GridRestingOrderRepository:
    def __init__(self) -> None:
        _ensure_table()

    def insert(self, order: GridRestingOrder) -> Optional[int]:
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    """
                    INSERT INTO qd_grid_resting_orders
                    (strategy_id, symbol, cell_index, purpose, side, pos_side, reduce_only,
                     price, quantity, quote_amount, client_order_id, exchange_order_id,
                     status, filled_quantity, avg_fill_price, processed_fill_qty, extra)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    RETURNING id
                    """,
                    (
                        int(order.strategy_id),
                        str(order.symbol),
                        int(order.cell_index),
                        str(order.purpose),
                        str(order.side),
                        str(order.pos_side),
                        bool(order.reduce_only),
                        float(order.price),
                        float(order.quantity),
                        float(order.quote_amount),
                        str(order.client_order_id or ""),
                        str(order.exchange_order_id or ""),
                        str(order.status or "open"),
                        float(order.filled_quantity or 0),
                        float(order.avg_fill_price or 0),
                        float(order.processed_fill_qty or 0),
                        json.dumps(order.extra or {}),
                    ),
                )
                row = cur.fetchone()
                db.commit()
                cur.close()
                return int((row or {}).get("id") or 0) or None
        except Exception as e:
            logger.warning("grid resting insert failed: %s", e)
            return None

    def update_status(
        self,
        order_id: int,
        *,
        status: str,
        filled_quantity: Optional[float] = None,
        avg_fill_price: Optional[float] = None,
        processed_fill_qty: Optional[float] = None,
        exchange_order_id: Optional[str] = None,
    ) -> None:
        sets = ["status = %s", "updated_at = NOW()"]
        args: List[Any] = [str(status)]
        if filled_quantity is not None:
            sets.append("filled_quantity = %s")
            args.append(float(filled_quantity))
        if avg_fill_price is not None:
            sets.append("avg_fill_price = %s")
            args.append(float(avg_fill_price))
        if processed_fill_qty is not None:
            sets.append("processed_fill_qty = %s")
            args.append(float(processed_fill_qty))
        if exchange_order_id is not None:
            sets.append("exchange_order_id = %s")
            args.append(str(exchange_order_id))
        args.append(int(order_id))
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    f"UPDATE qd_grid_resting_orders SET {', '.join(sets)} WHERE id = %s",
                    tuple(args),
                )
                db.commit()
                cur.close()
        except Exception as e:
            logger.warning("grid resting update failed id=%s: %s", order_id, e)

    def list_open(self, strategy_id: Optional[int] = None) -> List[GridRestingOrder]:
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                if strategy_id is not None:
                    cur.execute(
                        """
                        SELECT * FROM qd_grid_resting_orders
                        WHERE strategy_id = %s AND status = ANY(%s)
                        ORDER BY id ASC
                        """,
                        (int(strategy_id), list(OPEN_STATUSES)),
                    )
                else:
                    cur.execute(
                        """
                        SELECT * FROM qd_grid_resting_orders
                        WHERE status = ANY(%s)
                        ORDER BY strategy_id ASC, id ASC
                        """,
                        (list(OPEN_STATUSES),),
                    )
                rows = cur.fetchall() or []
                cur.close()
        except Exception as e:
            logger.warning("grid resting list_open failed: %s", e)
            return []
        return [GridRestingOrder.from_row(dict(r)) for r in rows]

    def list_for_strategy(
        self,
        strategy_id: int,
        *,
        status: Optional[str] = None,
        limit: int = 200,
    ) -> List[GridRestingOrder]:
        """List resting orders for a strategy (open by default)."""
        lim = max(1, min(int(limit or 200), 500))
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                if status and str(status).lower() not in ("", "all", "any"):
                    st = str(status).lower()
                    cur.execute(
                        """
                        SELECT * FROM qd_grid_resting_orders
                        WHERE strategy_id = %s AND status = %s
                        ORDER BY id DESC
                        LIMIT %s
                        """,
                        (int(strategy_id), st, lim),
                    )
                elif status and str(status).lower() == "all":
                    cur.execute(
                        """
                        SELECT * FROM qd_grid_resting_orders
                        WHERE strategy_id = %s
                        ORDER BY id DESC
                        LIMIT %s
                        """,
                        (int(strategy_id), lim),
                    )
                else:
                    cur.execute(
                        """
                        SELECT * FROM qd_grid_resting_orders
                        WHERE strategy_id = %s AND status = ANY(%s)
                        ORDER BY id DESC
                        LIMIT %s
                        """,
                        (int(strategy_id), list(OPEN_STATUSES), lim),
                    )
                rows = cur.fetchall() or []
                cur.close()
        except Exception as e:
            logger.warning("grid resting list_for_strategy failed: %s", e)
            return []
        return [GridRestingOrder.from_row(dict(r)) for r in rows]

    def has_open_for_cell(self, strategy_id: int, cell_index: int, purpose: str) -> bool:
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    """
                    SELECT 1 FROM qd_grid_resting_orders
                    WHERE strategy_id = %s AND cell_index = %s AND purpose = %s
                      AND status = ANY(%s)
                    LIMIT 1
                    """,
                    (int(strategy_id), int(cell_index), str(purpose), list(OPEN_STATUSES)),
                )
                row = cur.fetchone()
                cur.close()
                return bool(row)
        except Exception:
            return False

    def cancel_all(self, strategy_id: int, symbol: Optional[str] = None) -> int:
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                if symbol:
                    cur.execute(
                        """
                        UPDATE qd_grid_resting_orders SET status = 'cancelled', updated_at = NOW()
                        WHERE strategy_id = %s AND symbol = %s AND status = ANY(%s)
                        """,
                        (int(strategy_id), str(symbol), list(OPEN_STATUSES)),
                    )
                else:
                    cur.execute(
                        """
                        UPDATE qd_grid_resting_orders SET status = 'cancelled', updated_at = NOW()
                        WHERE strategy_id = %s AND status = ANY(%s)
                        """,
                        (int(strategy_id), list(OPEN_STATUSES)),
                    )
                n = int(cur.rowcount or 0)
                db.commit()
                cur.close()
                return n
        except Exception as e:
            logger.warning("grid resting cancel_all failed: %s", e)
            return 0
