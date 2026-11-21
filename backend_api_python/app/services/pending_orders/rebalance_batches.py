"""Durable portfolio batch results; never delay or submit exchange orders."""

from contextlib import contextmanager
import hashlib

from app.services.strategy_lifecycle import counts_toward_order_failure_stop, record_exchange_failure_outcome
from app.utils.db import get_db_connection
from app.utils.json_helpers import safe_json_loads
from app.utils.logger import get_logger

logger = get_logger(__name__)


@contextmanager
def rebalance_batch(*, strategy_id, run_id, timestamp, enabled):
    group_id = hashlib.sha256(f"{strategy_id}:{run_id}:{timestamp}".encode()).hexdigest() if enabled else ""
    try:
        yield group_id
    finally:
        if group_id:
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    """UPDATE qd_strategy_order_batches SET sealed_at = COALESCE(sealed_at, NOW())
                       WHERE strategy_id = %s AND strategy_run_id = %s AND group_id = %s""",
                    (strategy_id, run_id, group_id),
                )
                db.commit()
                cur.close()


def register_order_batch(cur, request, pending_id):
    """Register only newly queued orders, in the same transaction as insertion."""
    if not request.rebalance_group_id:
        return
    cur.execute(
        """INSERT INTO qd_strategy_order_batches
           (user_id, strategy_id, strategy_run_id, group_id, execution_mode, runtime_fencing_token)
           VALUES (%s, %s, %s, %s, %s, %s)
           ON CONFLICT (strategy_run_id, strategy_id, group_id) DO UPDATE
           SET group_id = EXCLUDED.group_id
           WHERE qd_strategy_order_batches.sealed_at IS NULL
             AND qd_strategy_order_batches.user_id = EXCLUDED.user_id
             AND qd_strategy_order_batches.execution_mode = EXCLUDED.execution_mode
             AND qd_strategy_order_batches.runtime_fencing_token = EXCLUDED.runtime_fencing_token
           RETURNING id""",
        (request.user_id, request.strategy_id, request.strategy_run_id, request.rebalance_group_id,
         request.execution_mode, request.runtime_fencing_token),
    )
    if not cur.fetchone():
        raise RuntimeError("strategyV2.rebalanceBatchClosed")
    cur.execute("UPDATE pending_orders SET rebalance_group_id = %s WHERE id = %s",
                (request.rebalance_group_id, pending_id))


def individual_notification_config(config, payload):
    """Batch notifications replace per-order delivery on all selected channels."""
    result = dict(config) if isinstance(config, dict) else {}
    if payload.get("rebalance_group_id"):
        result["channels"] = []
    return result


def order_result(order):
    payload = safe_json_loads(order.get("payload_json"), {})
    status = str(order.get("status") or "")
    if status == "filled":
        return "filled"
    if float(order.get("filled") or 0) > 0:
        return "partial"
    if status in {"failed", "cancelled"}:
        return status
    if payload.get("_exchange_failure_streak_accepted") or (
        order.get("execution_mode") == "signal" and status == "sent"
    ):
        return "accepted"
    return "pending"


def batch_outcome(orders):
    """None means unresolved, ignored means no definitive operational outcome."""
    results = [order_result(order) for order in orders]
    if "pending" in results:
        return None
    if any(result in {"filled", "partial", "accepted"} for result in results):
        return "accepted"
    if any(order.get("status") == "failed" and counts_toward_order_failure_stop(
        order.get("last_error") or "", order.get("signal_type") or ""
    ) for order in orders):
        return "failed"
    return "ignored"


def reconcile_order_batches(notifier, limit=100):
    """Claim results and one notification snapshot per batch across restarts."""
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """SELECT id FROM qd_strategy_order_batches
               WHERE (evaluated_at IS NULL OR notice_claimed_at IS NULL) AND next_check_at <= NOW()
               ORDER BY next_check_at, id LIMIT %s""", (limit,),
        )
        ids = [row["id"] for row in cur.fetchall()]
        cur.close()
    for batch_id in ids:
        try:
            notice = _reconcile_batch(batch_id)
            if notice:
                batch, orders, config, name = notice
                from app.services.portfolio_monitor_notifications import resolve_notification_delivery

                delivery = resolve_notification_delivery(batch["user_id"], config)
                results = notifier.notify_rebalance_summary(
                    strategy_id=batch["strategy_id"], strategy_name=name, group_id=batch["group_id"],
                    execution_mode=batch["execution_mode"], orders=orders, notification_config=delivery,
                )
                if any(not result.get("ok") for result in results.values()):
                    logger.warning("Rebalance notification delivery failed: batch=%s channels=%s", batch_id,
                                   [channel for channel, result in results.items() if not result.get("ok")])
        except Exception:
            logger.exception("Rebalance batch reconciliation failed: batch=%s", batch_id)


def _reconcile_batch(batch_id):
    notice = None
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute("SELECT * FROM qd_strategy_order_batches WHERE id = %s FOR UPDATE SKIP LOCKED", (batch_id,))
        batch = cur.fetchone()
        if not batch:
            return None
        cur.execute("UPDATE qd_strategy_order_batches SET next_check_at = NOW() + INTERVAL '5 seconds' WHERE id = %s",
                    (batch_id,))
        if not batch["sealed_at"]:
            # A dead producer cannot append more orders. Do not use a wall-clock
            # timeout while a producer still owns its lease and may be queuing.
            cur.execute(
                """UPDATE qd_strategy_order_batches b SET sealed_at = NOW()
                   WHERE b.id = %s AND (
                     NOT EXISTS (SELECT 1 FROM strategy_runs r WHERE r.id = b.strategy_run_id
                                 AND r.strategy_id = b.strategy_id AND r.user_id = b.user_id
                                 AND r.runtime_status IN ('running', 'recovering'))
                     OR (b.runtime_fencing_token > 0 AND NOT EXISTS (
                       SELECT 1 FROM qd_strategy_runtime_leases l WHERE l.strategy_id = b.strategy_id
                         AND l.fencing_token = b.runtime_fencing_token AND l.lease_expires_at >= NOW())))
                   RETURNING sealed_at""", (batch_id,),
            )
            sealed = cur.fetchone()
            if not sealed:
                db.commit()
                return None
            batch["sealed_at"] = sealed["sealed_at"]
        cur.execute(
            """SELECT * FROM pending_orders WHERE user_id = %s AND strategy_id = %s
               AND strategy_run_id = %s AND rebalance_group_id = %s AND execution_mode = %s ORDER BY id""",
            (batch["user_id"], batch["strategy_id"], batch["strategy_run_id"], batch["group_id"], batch["execution_mode"]),
        )
        orders = [dict(row) for row in cur.fetchall()]
        outcome = batch_outcome(orders)
        if not batch["evaluated_at"] and outcome is not None:
            # Resolve batches in creation order, regardless of venue reply order.
            cur.execute(
                """SELECT 1 FROM qd_strategy_order_batches WHERE strategy_id = %s AND strategy_run_id = %s
                   AND id < %s AND evaluated_at IS NULL LIMIT 1""",
                (batch["strategy_id"], batch["strategy_run_id"], batch_id),
            )
            if not cur.fetchone():
                if batch["execution_mode"] == "live" and outcome != "ignored":
                    reason = next((order.get("last_error") for order in orders if order.get("last_error")), "")
                    record_exchange_failure_outcome(cur, batch, accepted=outcome == "accepted",
                                                    reason=reason, unit="rebalance")
                cur.execute("UPDATE qd_strategy_order_batches SET evaluated_at = NOW() WHERE id = %s", (batch_id,))
        cur.execute(
            """UPDATE qd_strategy_order_batches SET notice_claimed_at = NOW()
               WHERE id = %s AND notice_claimed_at IS NULL
                 AND (%s OR sealed_at <= NOW() - INTERVAL '30 seconds') RETURNING id""",
            (batch_id, outcome is not None),
        )
        if cur.fetchone() and orders:
            cur.execute("SELECT name, notification_config FROM qd_strategies_trading WHERE id = %s AND user_id = %s",
                        (batch["strategy_id"], batch["user_id"]))
            strategy = cur.fetchone()
            if strategy:
                config = safe_json_loads(strategy.get("notification_config"), {})
                notice = (batch, [{**order, "summary_status": order_result(order)} for order in orders],
                          config, str(strategy.get("name") or ""))
        db.commit()
        cur.close()
    return notice
