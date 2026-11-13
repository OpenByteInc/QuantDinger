"""Operational health snapshots for live strategy monitoring."""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable

from app.services.live_trading.position_ownership import (
    STATUS_BLOCKED,
    canonical_symbol,
    normalize_market_type,
)
from app.utils.db import get_db_connection
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Terminal order failures are retained for audit, so counting every historical
# failure would leave a recovered strategy permanently degraded.  Keep a short
# attention window instead: recurring failures refresh it, while a transient
# exchange error clears automatically after healthy operation resumes.
FAILED_ORDER_ATTENTION_WINDOW_SEC = 300
_heartbeat_lock = threading.Lock()
_heartbeat_writes: dict[tuple[int, int], tuple[float, tuple[object, ...]]] = {}


def _startup_grace_seconds() -> int:
    try:
        return max(30, int(float(os.getenv("STRATEGY_STARTUP_HEALTH_GRACE_SEC", "180"))))
    except (TypeError, ValueError):
        return 180


def _heartbeat_write_interval_seconds() -> float:
    try:
        return max(1.0, float(os.getenv("STRATEGY_HEARTBEAT_WRITE_INTERVAL_SEC", "10")))
    except (TypeError, ValueError):
        return 10.0


def reset_runtime_heartbeat_coalescer() -> None:
    """Reset process-local heartbeat throttling (used by tests/reloads)."""
    with _heartbeat_lock:
        _heartbeat_writes.clear()


def load_runtime_health(
    strategy_ids: Iterable[int],
    *,
    strategy_statuses: Dict[int, str] | None = None,
    strategy_runtime_epochs: Dict[int, int] | None = None,
    strategy_runtime_requested_ats: Dict[int, Any] | None = None,
    strategy_execution_modes: Dict[int, str] | None = None,
) -> Dict[int, Dict[str, Any]]:
    ids = sorted({int(value) for value in strategy_ids if int(value or 0) > 0})
    if not ids:
        return {}
    statuses = {int(key): str(value or "").lower() for key, value in (strategy_statuses or {}).items()}
    runtime_epochs = {
        int(key): max(0, int(value or 0))
        for key, value in (strategy_runtime_epochs or {}).items()
    }
    runtime_requested_ats = {
        int(key): value
        for key, value in (strategy_runtime_requested_ats or {}).items()
    }
    execution_modes = {
        int(key): str(value or "").strip().lower()
        for key, value in (strategy_execution_modes or {}).items()
    }
    snapshots = {strategy_id: _empty_snapshot() for strategy_id in ids}
    for strategy_id, snapshot in snapshots.items():
        snapshot["_execution_mode"] = execution_modes.get(strategy_id, "")
    placeholders = ",".join(["%s"] * len(ids))

    _load_latest_runs(snapshots, placeholders, ids)
    _load_runtime_leases(snapshots, placeholders, ids)
    _load_health_state(snapshots, placeholders, ids)
    _load_latest_events(snapshots, placeholders, ids)
    _load_pending_orders(snapshots, placeholders, ids)
    _load_positions(snapshots, placeholders, ids)
    _load_latest_fills(snapshots, placeholders, ids)
    _load_position_ownership(snapshots)

    now = int(time.time())
    for strategy_id, snapshot in snapshots.items():
        snapshot["_desired_runtime_epoch"] = runtime_epochs.get(strategy_id, 0)
        snapshot["_runtime_requested_at"] = runtime_requested_ats.get(strategy_id)
        snapshot["health"] = _health_state(
            snapshot,
            strategy_status=statuses.get(strategy_id, ""),
            now=now,
        )
        reasons = []
        if snapshot["health"] != "inactive":
            if int(snapshot.get("failed_orders") or 0) > 0:
                reasons.append("recent_order_failure")
            if bool(snapshot.get("position_drift_blocked")) or int(snapshot.get("position_drift_count") or 0) > 0:
                reasons.append("position_drift")
            if str(snapshot.get("last_error") or "").strip():
                reasons.append(str(snapshot.get("last_error") or ""))
            if snapshot["health"] in {"stale", "offline", "unknown"}:
                reasons.append(f"runtime_{snapshot['health']}")
        snapshot["attention_reasons"] = list(dict.fromkeys(reasons))
        snapshot["needs_attention"] = bool(reasons) or snapshot["health"] == "degraded"
        snapshot.pop("_ownership_context", None)
        snapshot.pop("_desired_runtime_epoch", None)
        snapshot.pop("_runtime_requested_at", None)
        snapshot.pop("_execution_mode", None)
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
    price_source: str = "",
    price_age_ms: int = 0,
    trigger_mode: str = "",
    fill_transport: str = "",
) -> None:
    if int(strategy_id or 0) <= 0:
        return
    from app.services.strategy_runtime.state import RuntimeStateStore

    wall_now = time.time()
    now = int(wall_now)
    identity = (int(strategy_id), int(strategy_run_id or 0))
    critical_signature = (
        str(status or "healthy"),
        str(last_error or "")[:1000],
        str(price_source or ""),
        str(trigger_mode or ""),
        str(fill_transport or ""),
    )
    with _heartbeat_lock:
        previous_at, previous_signature = _heartbeat_writes.get(
            identity,
            (0.0, ()),
        )
        should_write = bool(
            previous_at <= 0
            or wall_now - previous_at >= _heartbeat_write_interval_seconds()
            or critical_signature != previous_signature
        )
        if should_write:
            _heartbeat_writes[identity] = (wall_now, critical_signature)
    if not should_write:
        return
    saved = RuntimeStateStore(
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
        "price_source": str(price_source or ""),
        "price_age_ms": max(0, int(price_age_ms or 0)),
        "trigger_mode": str(trigger_mode or ""),
        "fill_transport": str(fill_transport or ""),
    })
    if saved is False:
        with _heartbeat_lock:
            if _heartbeat_writes.get(identity) == (wall_now, critical_signature):
                _heartbeat_writes.pop(identity, None)
        return
    # Keep a low-frequency equity mark so tomorrow's P&L can use a real
    # midnight baseline even when nobody has the monitoring page open.
    from app.services.strategy_daily_pnl import maybe_capture_strategy_equity_snapshot
    maybe_capture_strategy_equity_snapshot(int(strategy_id))


def _empty_snapshot() -> Dict[str, Any]:
    return {
        "health": "unknown",
        "run_id": 0,
        "runtime_epoch": 0,
        "runtime_status": "",
        "started_at": None,
        "last_heartbeat_at": 0,
        "heartbeat_age_sec": None,
        "loop_latency_ms": 0,
        "latency_ms": 0,
        "last_price": 0.0,
        "last_error": "",
        "price_source": "",
        "price_age_ms": 0,
        "trigger_mode": "",
        "fill_transport": "",
        "runtime_lease_active": False,
        "runtime_owner_id": "",
        "runtime_lease_expires_at": None,
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
        "exit_residual_positions": 0,
        "exit_residual_exposure": 0.0,
        "last_fill_at": None,
        "position_drift_blocked": False,
        "position_drift_count": 0,
        "position_drift_reason": "",
        "position_drift_sides": [],
        "position_drift_details": [],
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
        SELECT strategy_id, id, user_id, exchange_id, credential_id, symbol, market_type,
               runtime_status, runtime_epoch, started_at, stopped_at, stop_reason
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
            "runtime_epoch": int(row.get("runtime_epoch") or 0),
            "runtime_status": str(row.get("runtime_status") or ""),
            "started_at": row.get("started_at"),
            "stopped_at": row.get("stopped_at"),
            "stop_reason": str(row.get("stop_reason") or ""),
            "_ownership_context": {
                "user_id": int(row.get("user_id") or 0),
                "exchange_id": str(row.get("exchange_id") or "").strip().lower(),
                "credential_id": int(row.get("credential_id") or 0),
                "symbol": canonical_symbol(str(row.get("symbol") or "")),
                "market_type": normalize_market_type(str(row.get("market_type") or "swap")),
            },
        })


def _load_runtime_leases(snapshots, placeholders, ids):
    rows = _query(
        f"""
        SELECT strategy_id, owner_id, lease_expires_at
        FROM qd_strategy_runtime_leases
        WHERE strategy_id IN ({placeholders}) AND lease_expires_at >= NOW()
        """,
        tuple(ids),
    )
    for row in rows:
        strategy_id = int(row.get("strategy_id") or 0)
        if strategy_id not in snapshots:
            continue
        snapshots[strategy_id].update({
            "runtime_lease_active": True,
            "runtime_owner_id": str(row.get("owner_id") or ""),
            "runtime_lease_expires_at": row.get("lease_expires_at"),
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
        if int(row.get("strategy_run_id") or 0) != int(
            snapshots[strategy_id].get("run_id") or 0
        ):
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
            "price_source": str(state.get("price_source") or ""),
            "price_age_ms": max(0, int(state.get("price_age_ms") or 0)),
            "trigger_mode": str(state.get("trigger_mode") or ""),
            "fill_transport": str(state.get("fill_transport") or ""),
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
               SUM(CASE
                       WHEN status IN ('pending', 'processing', 'sent', 'syncing')
                       THEN 1 ELSE 0
                   END) AS pending_orders,
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
        SELECT s.id AS strategy_id,
               SUM(CASE
                   WHEN LOWER(COALESCE(s.execution_mode, 'signal')) = 'live'
                        AND COALESCE(lp.position_state, 'open') <> 'exit_residual'
                        AND ABS(COALESCE(lp.size, 0)) > 0 THEN 1
                   WHEN LOWER(COALESCE(s.execution_mode, 'signal')) = 'signal'
                        AND ABS(COALESCE(vp.size, 0)) > 0 THEN 1
                   ELSE 0 END) AS open_positions,
               SUM(CASE
                   WHEN LOWER(COALESCE(s.execution_mode, 'signal')) = 'live'
                        AND COALESCE(lp.position_state, 'open') <> 'exit_residual'
                   THEN ABS(COALESCE(lp.size, 0) * COALESCE(lp.current_price, 0))
                   WHEN LOWER(COALESCE(s.execution_mode, 'signal')) = 'signal'
                   THEN ABS(COALESCE(vp.size, 0) * COALESCE(vp.current_price, 0))
                   ELSE 0 END) AS gross_exposure,
               SUM(CASE WHEN LOWER(COALESCE(s.execution_mode, 'signal')) = 'live'
                        AND COALESCE(lp.position_state, 'open') = 'exit_residual'
                        AND ABS(COALESCE(lp.size, 0)) > 0 THEN 1 ELSE 0 END) AS exit_residual_positions,
               SUM(CASE WHEN LOWER(COALESCE(s.execution_mode, 'signal')) = 'live'
                        AND COALESCE(lp.position_state, 'open') = 'exit_residual'
                   THEN ABS(COALESCE(lp.size, 0) * COALESCE(lp.current_price, 0)) ELSE 0 END) AS exit_residual_exposure
        FROM qd_strategies_trading s
        LEFT JOIN qd_strategy_positions lp
          ON lp.strategy_id = s.id AND LOWER(COALESCE(s.execution_mode, 'signal')) = 'live'
        LEFT JOIN qd_strategy_virtual_positions vp
          ON vp.strategy_id = s.id AND LOWER(COALESCE(s.execution_mode, 'signal')) = 'signal'
        WHERE s.id IN ({placeholders})
        GROUP BY s.id
        """,
        tuple(ids),
    )
    for row in rows:
        strategy_id = int(row.get("strategy_id") or 0)
        if strategy_id in snapshots:
            snapshots[strategy_id].update({
                "open_positions": int(row.get("open_positions") or 0),
                "gross_exposure": float(row.get("gross_exposure") or 0.0),
                "exit_residual_positions": int(row.get("exit_residual_positions") or 0),
                "exit_residual_exposure": float(row.get("exit_residual_exposure") or 0.0),
            })


def _load_latest_fills(snapshots, placeholders, ids):
    rows = _query(
        f"""
        SELECT strategy_id, MAX(last_fill_at) AS last_fill_at
        FROM (
            SELECT f.strategy_id, f.filled_at AS last_fill_at
            FROM strategy_order_fills f
            JOIN qd_strategies_trading s ON s.id = f.strategy_id
            WHERE f.strategy_id IN ({placeholders})
              AND LOWER(COALESCE(s.execution_mode, 'signal')) = 'live'
            UNION ALL
            SELECT v.strategy_id, v.created_at AS last_fill_at
            FROM qd_strategy_virtual_trades v
            JOIN qd_strategies_trading s ON s.id = v.strategy_id
            WHERE v.strategy_id IN ({placeholders})
              AND LOWER(COALESCE(s.execution_mode, 'signal')) = 'signal'
        ) fills
        GROUP BY strategy_id
        """,
        tuple(ids + ids),
    )
    for row in rows:
        strategy_id = int(row.get("strategy_id") or 0)
        if strategy_id in snapshots:
            snapshots[strategy_id]["last_fill_at"] = row.get("last_fill_at")


def _load_position_ownership(snapshots: Dict[int, Dict[str, Any]]) -> None:
    """Attach blocked account-leg ownership to every matching live strategy."""
    user_ids = sorted({
        int((snapshot.get("_ownership_context") or {}).get("user_id") or 0)
        for snapshot in snapshots.values()
        if str(snapshot.get("_execution_mode") or "").strip().lower() == "live"
        and int((snapshot.get("_ownership_context") or {}).get("credential_id") or 0) > 0
        and int((snapshot.get("_ownership_context") or {}).get("user_id") or 0) > 0
    })
    if not user_ids:
        return
    placeholders = ",".join(["%s"] * len(user_ids))
    rows = _query(
        f"""
        SELECT user_id, credential_id, exchange_id, market_type, symbol,
               symbol_canonical, side, coexistence_mode, manual_reserved_qty,
               observed_account_qty, allocated_qty, status, drift_reason, updated_at
        FROM qd_position_reservations
        WHERE user_id IN ({placeholders}) AND status = %s
        ORDER BY updated_at DESC, id DESC
        """,
        (*user_ids, STATUS_BLOCKED),
    )
    for strategy_id, snapshot in snapshots.items():
        if str(snapshot.get("_execution_mode") or "").strip().lower() != "live":
            continue
        context = snapshot.get("_ownership_context") or {}
        context_credential = int(context.get("credential_id") or 0)
        if not context or context_credential <= 0:
            continue
        details = []
        for row in rows:
            row_credential = int(row.get("credential_id") or 0)
            if int(row.get("user_id") or 0) != int(context.get("user_id") or 0):
                continue
            if row_credential != context_credential:
                continue
            if normalize_market_type(str(row.get("market_type") or "swap")) != context.get("market_type"):
                continue
            if canonical_symbol(str(row.get("symbol_canonical") or row.get("symbol") or "")) != context.get("symbol"):
                continue
            row_exchange = str(row.get("exchange_id") or "").strip().lower()
            if context.get("exchange_id") and row_exchange and row_exchange != context.get("exchange_id"):
                continue
            details.append({
                "side": str(row.get("side") or ""),
                "reason": str(row.get("drift_reason") or ""),
                "account_qty": float(row.get("observed_account_qty") or 0.0),
                "strategy_qty": float(row.get("allocated_qty") or 0.0),
                "protected_qty": float(row.get("manual_reserved_qty") or 0.0),
                "coexistence_mode": str(row.get("coexistence_mode") or "strict"),
                "updated_at": row.get("updated_at"),
            })
        if not details:
            continue
        snapshot.update({
            "position_drift_blocked": True,
            "position_drift_count": len(details),
            "position_drift_reason": str(details[0].get("reason") or ""),
            "position_drift_sides": sorted({str(item.get("side") or "") for item in details if item.get("side")}),
            "position_drift_details": details,
        })


def _health_state(snapshot: Dict[str, Any], *, strategy_status: str, now: int) -> str:
    if strategy_status != "running":
        return "inactive"
    run_started_timestamp = _timestamp_seconds(snapshot.get("started_at"))
    runtime_requested_timestamp = _timestamp_seconds(snapshot.get("_runtime_requested_at"))
    startup_candidates = [
        value
        for value in (run_started_timestamp, runtime_requested_timestamp)
        if value is not None
    ]
    startup_timestamp = max(startup_candidates) if startup_candidates else None
    startup_age = max(0, now - startup_timestamp) if startup_timestamp is not None else None
    startup_grace = _startup_grace_seconds()
    snapshot["startup_grace_sec"] = startup_grace
    desired_runtime_epoch = int(snapshot.get("_desired_runtime_epoch") or 0)
    actual_runtime_epoch = int(snapshot.get("runtime_epoch") or 0)
    pending_new_run = (
        desired_runtime_epoch > 0
        and (
            int(snapshot.get("run_id") or 0) <= 0
            or actual_runtime_epoch != desired_runtime_epoch
        )
    )
    if pending_new_run:
        if startup_age is not None and startup_age <= startup_grace:
            snapshot["startup_age_sec"] = startup_age
            return "starting"
        return "degraded"
    if (
        int(snapshot.get("failed_orders") or 0) > 0
        or bool(snapshot.get("position_drift_blocked"))
        or int(snapshot.get("position_drift_count") or 0) > 0
        or str(snapshot.get("last_error") or "").strip()
    ):
        return "degraded"
    if int(snapshot.get("run_id") or 0) <= 0:
        if startup_age is not None and startup_age <= startup_grace:
            snapshot["startup_age_sec"] = startup_age
            return "starting"
        return "degraded"
    if (
        bool(snapshot.get("runtime_lease_active"))
        and str(snapshot.get("trigger_mode") or "") == "closed_bar_event"
    ):
        return "healthy"
    heartbeat = int(snapshot.get("last_heartbeat_at") or 0)
    if heartbeat <= 0:
        if startup_age is not None and startup_age <= startup_grace:
            snapshot["startup_age_sec"] = startup_age
            return "starting"
        return "unknown"
    age = max(0, now - heartbeat)
    snapshot["heartbeat_age_sec"] = age
    if age <= 90:
        return "healthy"
    if age <= 300:
        return "stale"
    return "offline"


def _timestamp_seconds(value: Any) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp())
    if isinstance(value, (int, float)):
        numeric = float(value)
        if numeric > 100_000_000_000:
            numeric /= 1000.0
        return int(numeric)
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())
