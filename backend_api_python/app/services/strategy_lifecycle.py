"""
Unified auto-stop for live strategies after fatal exchange/auth/connectivity errors.

Position sync and order workers call `auto_stop_live_strategy` so DB status, executor
threads, and runtime logs stay consistent (avoids endless retry spam after restart).
"""

from __future__ import annotations

import json
import threading
from typing import Set

from app.utils.db import get_db_connection
from app.utils.logger import get_logger
from app.utils.strategy_runtime_logs import append_strategy_log

logger = get_logger(__name__)

_quiet_lock = threading.Lock()
_quiet_sids: Set[int] = set()


def is_fatal_exchange_error(msg: str) -> bool:
    """Return True when the strategy should not keep retrying exchange/private APIs."""
    m = (msg or "").lower()
    if not m:
        return False
    if "unsupported market type" in m or "unsupported market" in m:
        return True
    tokens = (
        # Programming/adapter contract failures are deterministic. Retrying on
        # every candle can enqueue an entire robot order ladder without any
        # chance of recovery until the service is upgraded.
        "got an unexpected keyword argument",
        "missing 1 required positional argument",
        "binance http 401",
        '"code":-2015',
        "-2015",
        "okx http 401",
        '"code":"50111"',
        "50111",
        "invalid api-key",
        "invalid api key",
        "api-signature-not-valid",
        "invalid ok-access-key",
        "invalid ip",
        "invalid_ip",
        "incorrect ip address",
        "ip地址错误",
        "40018",
        "permissions for action",
        "unauthorized",
        "forbidden",
        " http 401",
        "authentication",
        "signature mismatch",
        "invalid_signature",
        "permission denied",
        "connection refused",
        "connect call failed",
        "errno 111",
        "make sure api port on tws",
        "failed to connect to ibkr",
        "ibkr connection failed",
        "live trading error",
        "single-asset collateral mode is temporarily unavailable",
        "disabled ibkr",
        "已关闭 ibkr",
        "missing okx",
        "api_key/secret_key",
        "secret_key/passphrase",
        "missing api_key",
        "missing secret",
        "missing passphrase",
        "missing credential",
        "no credential",
        "exchange credential",
        "unsupported client for grid",
        "trading_venue_disabled",
    )
    return any(t in m for t in tokens)


def is_recoverable_position_error(reason: str) -> bool:
    from app.services.pending_orders.error_classification import is_exchange_price_band_error

    if is_exchange_price_band_error(reason):
        return True
    return not is_fatal_exchange_error(reason) and any(code in str(reason or "").lower() for code in (
        "position_drift_detected", "minimum_trade_unit", "min_notional",
        "position_ownership_drift", "target_already_met",
        "opposite_account_inventory_would_be_netted",
        "one-way position mode would net opposite",
        "below step/min", "below lot step/minqty", "below min/precision",
        "below mintradeusdt/precision",
        '"code":-2022', "'code': -2022", "'code':-2022",
        '"code":-4118', "'code': -4118", "'code':-4118",
        "reduceonly order is rejected", "reduce only order is rejected",
        "reduceonly order failed", "reduce only order failed",
    ))


def counts_toward_order_failure_stop(reason: str, signal_type: str) -> bool:
    """Count definitive configuration/funding rejections, not safety guards."""
    from app.services.pending_orders.error_classification import classify_exchange_order_error

    if is_recoverable_position_error(reason):
        return False
    category = classify_exchange_order_error(reason).get("category")
    if category == "order_size":
        return str(signal_type).startswith(("open_", "add_"))
    return category in {"insufficient_funds", "credentials", "invalid_symbol", "account_configuration"}


def record_pending_order_outcome(cur, order: dict, *, accepted: bool, reason: str = "") -> None:
    """Record a first definitive outcome in the pending-order transaction.

    The caller locks the order and skips repeated terminal outcomes. Locking the
    strategy serializes different orders and fences explicit restart requests.
    """
    sid, run_id = int(order.get("strategy_id") or 0), int(order.get("strategy_run_id") or 0)
    if not sid or not run_id or order.get("execution_mode") != "live":
        return
    from app.utils.json_helpers import safe_json_loads

    payload = safe_json_loads(order.get("payload_json"), {})
    if accepted and payload.get("_exchange_failure_streak_accepted"):
        return
    if not accepted and not counts_toward_order_failure_stop(reason, order.get("signal_type", "")):
        return
    if accepted:
        cur.execute(
            """
            UPDATE pending_orders SET payload_json =
                (COALESCE(NULLIF(payload_json, ''), '{}')::jsonb
                    || '{"_exchange_failure_streak_accepted": true}'::jsonb)::text
            WHERE id = %s
            """,
            (int(order["id"]),),
        )
    if payload.get("rebalance_group_id"):
        return
    record_exchange_failure_outcome(cur, order, accepted=accepted, reason=reason)


def record_exchange_failure_outcome(cur, order: dict, *, accepted: bool, reason: str = "", unit: str = "order") -> None:
    """Apply one order or completed rebalance result under the run's fence."""
    sid, run_id = int(order["strategy_id"]), int(order["strategy_run_id"])
    cur.execute(
        """
        SELECT s.id, s.user_id, s.desired_runtime_epoch
        FROM qd_strategies_trading s JOIN strategy_runs r ON r.strategy_id = s.id
        WHERE s.id = %s AND s.user_id = %s AND r.id = %s AND r.user_id = s.user_id
          AND s.status = 'running' AND s.execution_mode = 'live'
          AND r.runtime_status IN ('running', 'recovering')
          AND r.runtime_epoch = s.desired_runtime_epoch
        FOR UPDATE OF s
        """,
        (sid, int(order.get("user_id") or 0), run_id),
    )
    strategy = cur.fetchone()
    if not strategy:
        return
    cur.execute(
        """
        INSERT INTO strategy_runtime_state (strategy_run_id, strategy_id, state_key, state_json)
        VALUES (%s, %s, 'exchange_failures', jsonb_build_object('count', %s::integer, 'unit', %s::text))
        ON CONFLICT (strategy_run_id, strategy_id, state_key) DO UPDATE
        SET state_json = jsonb_build_object('count', CASE WHEN %s THEN 0
                WHEN COALESCE(strategy_runtime_state.state_json->>'unit', 'order') <> %s THEN 1
                ELSE COALESCE((strategy_runtime_state.state_json->>'count')::integer, 0) + 1 END, 'unit', %s::text),
            version = strategy_runtime_state.version + 1, updated_at = NOW()
        RETURNING state_json
        """,
        (run_id, sid, 0 if accepted else 1, unit, accepted, unit, unit),
    )
    state = (cur.fetchone() or {}).get("state_json") or {}
    if isinstance(state, str):
        state = json.loads(state)
    count = int(state.get("count") or 0)
    if count < 5:
        return
    stop_reason = f"Repeated definitive {unit} failures ({count}): {reason}"
    cur.execute("UPDATE qd_strategies_trading SET status = 'stopped', updated_at = NOW() WHERE id = %s", (sid,))
    cur.execute(
        "UPDATE strategy_runs SET runtime_status = 'stopped', stopped_at = NOW(), stop_reason = %s WHERE id = %s",
        (stop_reason, run_id),
    )
    cur.execute(
        """
        INSERT INTO qd_strategy_commands (strategy_id, user_id, command_type, idempotency_key, payload_json)
        VALUES (%s, %s, 'stop', %s, %s::jsonb) ON CONFLICT (idempotency_key) DO NOTHING
        """,
        (sid, strategy["user_id"], f"order-failure-stop:{sid}:{run_id}", json.dumps({
            "auto_stop": True, "strategy_run_id": run_id,
            "expected_runtime_epoch": strategy["desired_runtime_epoch"],
            "reason": stop_reason, "consecutive_failures": count, "failure_unit": unit, "close_positions": False,
        })),
    )


def complete_order_failure_stop(command, stop_strategy) -> dict:
    """Clean up the stopped runtime without stopping a later user-started run."""
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            "SELECT status, desired_runtime_epoch, strategy_name, notification_config FROM qd_strategies_trading WHERE id = %s AND user_id = %s",
            (command.strategy_id, command.user_id),
        )
        strategy = cur.fetchone() or {}
        cur.close()
    if (strategy.get("status") != "stopped" or
            int(strategy.get("desired_runtime_epoch") or 0) != int(command.payload["expected_runtime_epoch"])):
        return {"strategy_id": command.strategy_id, "status": "superseded"}
    result = stop_strategy(command.strategy_id, close_positions=bool(command.payload.get("close_positions")),
                           persist_status=False)
    reason = str(command.payload.get("reason") or "")
    append_strategy_log(command.strategy_id, "error", f"Auto-stopped (pending_order): {reason}")
    notify_order_failure_stop(command, strategy)
    return result


def notify_order_failure_stop(command, strategy: dict) -> None:
    """Claim one best-effort stop notice per run, independent of order notices."""
    try:
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                INSERT INTO strategy_runtime_state (strategy_run_id, strategy_id, state_key, state_json)
                VALUES (%s, %s, 'order_failure_stop_notice', '{}'::jsonb)
                ON CONFLICT (strategy_run_id, strategy_id, state_key) DO NOTHING RETURNING id
                """,
                (int(command.payload["strategy_run_id"]), command.strategy_id),
            )
            claimed = cur.fetchone()
            db.commit()
            cur.close()
        if not claimed:
            return
        from app.services.portfolio_monitor_notifications import resolve_notification_delivery
        from app.services.signal_notifier import SignalNotifier

        config = strategy.get("notification_config") or {}
        if isinstance(config, str):
            config = json.loads(config)
        delivery = resolve_notification_delivery(command.user_id, config)
        results = SignalNotifier().notify_strategy_stopped(
            strategy_id=command.strategy_id, strategy_name=str(strategy.get("strategy_name") or ""),
            reason=str(command.payload.get("reason") or ""),
            consecutive_failures=int(command.payload.get("consecutive_failures") or 5),
            failure_unit=str(command.payload.get("failure_unit") or "order"),
            notification_config={**config, **delivery},
        )
        if any(not result.get("ok") for result in results.values()):
            logger.warning("Auto-stop notice delivery failed: strategy=%s channels=%s", command.strategy_id, results)
    except Exception:
        logger.exception("Auto-stop notice failed: strategy=%s", command.strategy_id)


def maybe_auto_stop_on_exchange_error(
    strategy_id: int,
    msg: str,
    *,
    source: str = "exchange",
    consecutive_failures: int = 0,
    consecutive_threshold: int = 5,
    perform_stop: bool = True,
) -> bool:
    """
    Stop a live strategy after a fatal exchange/auth error or repeated failures.
    Returns True if auto-stop was triggered (or already quieted for this run).
    """
    sid = int(strategy_id or 0)
    if sid <= 0:
        return False
    reason = (msg or "").strip()
    if not reason:
        return False
    if is_fatal_exchange_error(reason):
        if perform_stop:
            auto_stop_live_strategy(sid, reason, source=source)
        return True
    if is_recoverable_position_error(reason):
        # Reject the individual entry/undersized order while keeping position
        # monitoring and reduce-only protection alive.
        return False
    if consecutive_failures >= max(1, int(consecutive_threshold or 5)):
        if perform_stop:
            auto_stop_live_strategy(
                sid,
                f"Repeated exchange errors ({consecutive_failures}): {reason}",
                source=source,
            )
        return True
    return False


def should_skip_position_sync(strategy_id: int) -> bool:
    """In-process guard: skip sync for strategies already auto-stopped this run."""
    with _quiet_lock:
        return int(strategy_id) in _quiet_sids


def auto_stop_live_strategy(
    strategy_id: int,
    reason: str,
    *,
    source: str = "position_sync",
) -> bool:
    """
    Mark strategy stopped in DB, stop executor thread if running, append runtime log.
    Safe to call multiple times for the same strategy_id.
    """
    sid = int(strategy_id)
    if sid <= 0:
        return False

    reason = (reason or "").strip() or "fatal exchange error"
    with _quiet_lock:
        already = sid in _quiet_sids
        _quiet_sids.add(sid)
    if already:
        return True

    log_msg = f"Auto-stopped ({source}): {reason}"
    logger.error("[Strategy %s] %s", sid, log_msg)
    try:
        append_strategy_log(sid, "error", log_msg)
    except Exception:
        pass

    try:
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                "UPDATE qd_strategies_trading SET status = 'stopped' WHERE id = %s",
                (sid,),
            )
            db.commit()
            cur.close()
    except Exception as e:
        logger.warning("auto_stop: DB update failed for strategy %s: %s", sid, e)

    try:
        from app.services.strategy_runtime.identity import finish_strategy_run

        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                SELECT id FROM strategy_runs
                WHERE strategy_id = %s
                  AND runtime_status IN ('running', 'recovering', 'paused', 'needs_review')
                ORDER BY id DESC LIMIT 1
                """,
                (sid,),
            )
            row = cur.fetchone() or {}
            cur.close()
        finish_strategy_run(int(row.get("id") or 0), reason=log_msg)
    except Exception as e:
        logger.debug("auto_stop: strategy run finalization failed for strategy %s: %s", sid, e)

    try:
        from app import get_trading_executor

        get_trading_executor().stop_strategy(sid)
    except Exception as e:
        logger.debug("auto_stop: executor stop_strategy(%s): %s", sid, e)

    return True
