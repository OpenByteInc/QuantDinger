"""Operational health snapshots for live strategy monitoring."""

from __future__ import annotations

import json
import time
from typing import Any, Dict, Iterable

from app.utils.db import get_db_connection
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Terminal order failures are retained for audit, so counting every historical
# failure would leave a recovered strategy permanently degraded.  Keep a short
# attention window instead: recurring failures refresh it, while a transient
# exchange error clears automatically after healthy operation resumes.
FAILED_ORDER_ATTENTION_WINDOW_SEC = 300


def load_runtime_health(
    strategy_ids: Iterable[int],
    *,
    strategy_statuses: Dict[int, str] | None = None,
) -> Dict[int, Dict[str, Any]]:
    ids = sorted({int(value) for value in strategy_ids if int(value or 0) > 0})
    if not ids:
        return {}
    statuses = {int(key): str(value or "").lower() for key, value in (strategy_statuses or {}).items()}
    snapshots = {strategy_id: _empty_snapshot() for strategy_id in ids}
    placeholders = ",".join(["%s"] * len(ids))

    _load_latest_runs(snapshots, placeholders, ids)
    _load_health_state(snapshots, placeholders, ids)
    _load_latest_events(snapshots, placeholders, ids)
    _load_pending_orders(snapshots, placeholders, ids)
    _load_positions(snapshots, placeholders, ids)
    _load_latest_fills(snapshots, placeholders, ids)

    now = int(time.time())
    for strategy_id, snapshot in snapshots.items():
        snapshot["health"] = _health_state(
            snapshot,
            strategy_status=statuses.get(strategy_id, ""),
            now=now,
        )
    return snapshots


def record_runtime_heartbeat(
    *,
    strategy_id: int,
    strategy_run_id: int,
    symbol: str,
    price: float,
    pending_signal_count: int,
    loop_latency_ms: int = 0,
    status: str = "healthy",
    last_error: str = "",
) -> None:
    if int(strategy_id or 0) <= 0:
        return
    from app.services.strategy_runtime.state import RuntimeStateStore

    now = int(time.time())
    RuntimeStateStore(
        strategy_id=int(strategy_id),
        strategy_run_id=int(strategy_run_id or 0),
        state_key="health",
    ).save({
        "last_heartbeat_at": now,
        "symbol": str(symbol or ""),
        "last_price": float(price or 0.0),
        "pending_signal_count": max(0, int(pending_signal_count or 0)),
        "loop_latency_ms": max(0, int(loop_latency_ms or 0)),
        "latency_ms": max(0, int(loop_latency_ms or 0)),
        "status": str(status or "healthy"),
        "last_error": str(last_error or "")[:1000],
    })
    # Keep a low-frequency equity mark so tomorrow's P&L can use a real
    # midnight baseline even when nobody has the monitoring page open.
    from app.services.strategy_daily_pnl import maybe_capture_strategy_equity_snapshot
    maybe_capture_strategy_equity_snapshot(int(strategy_id))


def _empty_snapshot() -> Dict[str, Any]:
    return {
        "health": "unknown",
        "run_id": 0,
        "runtime_status": "",
        "started_at": None,
        "last_heartbeat_at": 0,
        "heartbeat_age_sec": None,
        "loop_latency_ms": 0,
        "latency_ms": 0,
        "last_price": 0.0,
        "last_error": "",
        "last_event_at": None,
        "last_event_type": "",
        "last_event_severity": "",
        "pending_orders": 0,
        "failed_orders": 0,
        "historical_failed_orders": 0,
        "last_failed_order_at": None,
        "failure_attention_window_sec": FAILED_ORDER_ATTENTION_WINDOW_SEC,
        "last_order_at": None,
        "last_signal_at": 0,
        "open_positions": 0,
        "gross_exposure": 0.0,
        "last_fill_at": None,
    }


def _query(sql: str, params: tuple) -> list[Dict[str, Any]]:
    try:
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall() or []
            cur.close()
        return rows
    except Exception as exc:
        logger.debug("runtime health query skipped: %s", exc)
        return []


def _load_latest_runs(snapshots, placeholders, ids):
    rows = _query(
        f"""
        SELECT strategy_id, id, runtime_status, started_at, stopped_at, stop_reason
        FROM strategy_runs
        WHERE strategy_id IN ({placeholders})
        ORDER BY strategy_id, id DESC
        """,
        tuple(ids),
    )
    seen = set()
    for row in rows:
        strategy_id = int(row.get("strategy_id") or 0)
        if strategy_id in seen or strategy_id not in snapshots:
            continue
        seen.add(strategy_id)
        snapshots[strategy_id].update({
            "run_id": int(row.get("id") or 0),
            "runtime_status": str(row.get("runtime_status") or ""),
            "started_at": row.get("started_at"),
            "stopped_at": row.get("stopped_at"),
            "stop_reason": str(row.get("stop_reason") or ""),
        })


def _load_health_state(snapshots, placeholders, ids):
    rows = _query(
        f"""
        SELECT strategy_id, strategy_run_id, state_json, updated_at
        FROM strategy_runtime_state
        WHERE strategy_id IN ({placeholders}) AND state_key = 'health'
        ORDER BY strategy_id, strategy_run_id DESC, updated_at DESC
        """,
        tuple(ids),
    )
    seen = set()
    for row in rows:
        strategy_id = int(row.get("strategy_id") or 0)
        if strategy_id in seen or strategy_id not in snapshots:
            continue
        seen.add(strategy_id)
        state = row.get("state_json") or {}
        if isinstance(state, str):
            try:
                state = json.loads(state)
            except Exception:
                state = {}
        if not isinstance(state, dict):
            state = {}
        snapshots[strategy_id].update({
            "last_heartbeat_at": int(state.get("last_heartbeat_at") or 0),
            "last_price": float(state.get("last_price") or 0.0),
            "last_error": str(state.get("last_error") or ""),
            "runtime_reported_status": str(state.get("status") or ""),
            "pending_signals": int(state.get("pending_signal_count") or 0),
            "loop_latency_ms": max(0, int(state.get("loop_latency_ms") or 0)),
            "latency_ms": max(0, int(state.get("latency_ms") or state.get("loop_latency_ms") or 0)),
        })


def _load_latest_events(snapshots, placeholders, ids):
    rows = _query(
        f"""
        SELECT strategy_id, event_type, severity, message, created_at
        FROM strategy_runtime_events
        WHERE strategy_id IN ({placeholders})
        ORDER BY strategy_id, created_at DESC, id DESC
        """,
        tuple(ids),
    )
    seen = set()
    for row in rows:
        strategy_id = int(row.get("strategy_id") or 0)
        if strategy_id in seen or strategy_id not in snapshots:
            continue
        seen.add(strategy_id)
        snapshots[strategy_id].update({
            "last_event_at": row.get("created_at"),
            "last_event_type": str(row.get("event_type") or ""),
            "last_event_severity": str(row.get("severity") or ""),
            "last_event_message": str(row.get("message") or ""),
        })


def _load_pending_orders(snapshots, placeholders, ids):
    rows = _query(
        f"""
        SELECT strategy_id, strategy_run_id,
               SUM(CASE WHEN status IN ('pending', 'processing') THEN 1 ELSE 0 END) AS pending_orders,
               SUM(CASE
                       WHEN status IN ('failed', 'error', 'rejected')
                        AND updated_at >= NOW() - (%s * INTERVAL '1 second')
                       THEN 1 ELSE 0
                   END) AS failed_orders,
               SUM(CASE WHEN status IN ('failed', 'error', 'rejected') THEN 1 ELSE 0 END)
                   AS historical_failed_orders,
               MAX(CASE WHEN status IN ('failed', 'error', 'rejected') THEN updated_at END)
                   AS last_failed_order_at,
               MAX(updated_at) AS last_order_at,
               MAX(signal_ts) AS last_signal_at
        FROM pending_orders
        WHERE strategy_id IN ({placeholders})
        GROUP BY strategy_id, strategy_run_id
        """,
        (FAILED_ORDER_ATTENTION_WINDOW_SEC, *ids),
    )
    for row in rows:
        strategy_id = int(row.get("strategy_id") or 0)
        strategy_run_id = int(row.get("strategy_run_id") or 0)
        if (
            strategy_id in snapshots
            and strategy_run_id == int(snapshots[strategy_id].get("run_id") or 0)
        ):
            snapshots[strategy_id].update({
                "pending_orders": int(row.get("pending_orders") or 0),
                "failed_orders": int(row.get("failed_orders") or 0),
                "historical_failed_orders": int(row.get("historical_failed_orders") or 0),
                "last_failed_order_at": row.get("last_failed_order_at"),
                "last_order_at": row.get("last_order_at"),
                "last_signal_at": int(row.get("last_signal_at") or 0),
            })


def _load_positions(snapshots, placeholders, ids):
    rows = _query(
        f"""
        SELECT strategy_id,
               SUM(CASE WHEN ABS(COALESCE(size, 0)) > 0 THEN 1 ELSE 0 END) AS open_positions,
               SUM(ABS(COALESCE(size, 0) * COALESCE(current_price, 0))) AS gross_exposure
        FROM qd_strategy_positions
        WHERE strategy_id IN ({placeholders})
        GROUP BY strategy_id
        """,
        tuple(ids),
    )
    for row in rows:
        strategy_id = int(row.get("strategy_id") or 0)
        if strategy_id in snapshots:
            snapshots[strategy_id].update({
                "open_positions": int(row.get("open_positions") or 0),
                "gross_exposure": float(row.get("gross_exposure") or 0.0),
            })


def _load_latest_fills(snapshots, placeholders, ids):
    rows = _query(
        f"""
        SELECT strategy_id, MAX(filled_at) AS last_fill_at
        FROM strategy_order_fills
        WHERE strategy_id IN ({placeholders})
        GROUP BY strategy_id
        """,
        tuple(ids),
    )
    for row in rows:
        strategy_id = int(row.get("strategy_id") or 0)
        if strategy_id in snapshots:
            snapshots[strategy_id]["last_fill_at"] = row.get("last_fill_at")


def _health_state(snapshot: Dict[str, Any], *, strategy_status: str, now: int) -> str:
    if strategy_status != "running":
        return "inactive"
    if int(snapshot.get("run_id") or 0) <= 0:
        return "degraded"
    if int(snapshot.get("failed_orders") or 0) > 0 or str(snapshot.get("last_error") or "").strip():
        return "degraded"
    heartbeat = int(snapshot.get("last_heartbeat_at") or 0)
    if heartbeat <= 0:
        return "unknown"
    age = max(0, now - heartbeat)
    snapshot["heartbeat_age_sec"] = age
    if age <= 90:
        return "healthy"
    if age <= 300:
        return "stale"
    return "offline"
