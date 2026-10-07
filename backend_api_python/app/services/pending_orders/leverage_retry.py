"""Recoverable OKX leverage / algo-order (59669) retry helpers.

OKX may reject leverage or margin-mode changes while native algo / protection
orders exist on the instrument. Account configuration already cancels and
retries once; if that still fails, pending live orders should be requeued with
backoff instead of being marked permanently failed.
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, Optional

from app.utils.db import get_db_connection
from app.utils.logger import get_logger

logger = get_logger(__name__)

MAX_LEVERAGE_RETRIES = 3


def is_recoverable_leverage_error(error: Any) -> bool:
    err_str = str(error or "")
    lower = err_str.lower()
    return (
        "59669" in err_str
        or "Cancel cross-margin trailing" in err_str
        or "algo orders" in lower
    )


def schedule_leverage_retry(
    *,
    order_id: int,
    payload: Dict[str, Any],
    attempt_count: int,
    max_retries: int,
    retry_delay_sec: int,
    err_str: str,
) -> None:
    """Requeue a live order blocked by recoverable OKX leverage errors."""
    merged_payload = dict(payload or {})
    merged_payload["leverage_retry_attempt"] = int(attempt_count)
    merged_payload["leverage_retry_not_before"] = time.time() + float(retry_delay_sec)
    note = f"leverage_retry:{attempt_count}/{max_retries} in {retry_delay_sec}s"
    last_error = f"[auto-retry-{attempt_count}]{err_str}"[:2000]
    try:
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                UPDATE pending_orders
                SET status = 'pending',
                    attempts = GREATEST(0, COALESCE(attempts, 0) - 1),
                    last_error = %s,
                    dispatch_note = %s,
                    payload_json = %s,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (
                    last_error,
                    note[:200],
                    json.dumps(merged_payload, ensure_ascii=False),
                    int(order_id),
                ),
            )
            db.commit()
            cur.close()
    except Exception as db_err:
        logger.warning(
            "Failed to schedule leverage retry pending_id=%s: %s",
            order_id,
            db_err,
        )
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    """
                    UPDATE pending_orders
                    SET status = 'pending',
                        attempts = GREATEST(0, COALESCE(attempts, 0) - 1),
                        last_error = %s,
                        updated_at = NOW()
                    WHERE id = %s AND status = 'processing'
                    """,
                    (last_error, int(order_id)),
                )
                db.commit()
                cur.close()
        except Exception:
            logger.warning(
                "Failed fallback requeue for leverage retry pending_id=%s",
                order_id,
                exc_info=True,
            )


def handle_derivatives_configuration_error(
    *,
    error: Exception,
    order_id: int,
    strategy_id: int,
    symbol: str,
    payload: Dict[str, Any],
    phases: Dict[str, Any],
    safe_cfg: Any,
    mark_failed: Callable[..., None],
    append_log: Callable[..., None],
    notify: Optional[Callable[..., None]] = None,
    console_print: Optional[Callable[[str], None]] = None,
) -> bool:
    """Handle account-configuration failures for live orders.

    Returns True when the caller should stop processing the order (retry
    scheduled or hard-failed).
    """
    err_str = str(error)
    if is_recoverable_leverage_error(error):
        attempt_count = int(payload.get("leverage_retry_attempt") or 0) + 1
        if attempt_count <= MAX_LEVERAGE_RETRIES:
            retry_delay_sec = attempt_count * 30  # 30s → 60s → 90s
            phases["leverage_retry_attempt"] = attempt_count
            phases["leverage_retry_pending"] = True
            logger.warning(
                "Leverage setup temporarily blocked by OKX algo orders "
                "(attempt %s/%s, pending_id=%s, strategy_id=%s). "
                "Scheduling retry in %ss...",
                attempt_count,
                MAX_LEVERAGE_RETRIES,
                order_id,
                strategy_id,
                retry_delay_sec,
            )
            append_log(
                strategy_id,
                "warning",
                (
                    f"Leverage setup temporarily blocked — "
                    f"retrying in {retry_delay_sec}s "
                    f"(attempt {attempt_count}/{MAX_LEVERAGE_RETRIES})"
                ),
            )
            schedule_leverage_retry(
                order_id=order_id,
                payload=payload,
                attempt_count=attempt_count,
                max_retries=MAX_LEVERAGE_RETRIES,
                retry_delay_sec=retry_delay_sec,
                err_str=err_str,
            )
            return True

        err = f"derivatives_account_configuration_failed[max_retries_exceeded]:{error}"
        logger.error(
            "Leverage setup permanently failed after %s retries: "
            "pending_id=%s, strategy_id=%s, err=%s",
            MAX_LEVERAGE_RETRIES,
            order_id,
            strategy_id,
            error,
        )
        mark_failed(order_id=order_id, error=err)
        append_log(
            strategy_id,
            "error",
            (
                f"Leverage or margin-mode setup failed for {symbol} "
                f"after {MAX_LEVERAGE_RETRIES} retries. "
                "Restart the strategy to try again."
            ),
        )
        return True

    err = f"derivatives_account_configuration_failed:{error}"
    logger.warning(
        "live leverage set failed: pending_id=%s, strategy_id=%s, cfg=%s, err=%s",
        order_id,
        strategy_id,
        safe_cfg,
        error,
    )
    mark_failed(order_id=order_id, error=err)
    if console_print is not None:
        console_print(
            f"[worker] order rejected: strategy_id={strategy_id} pending_id={order_id} {err}"
        )
    if notify is not None:
        notify(status="failed", error=err)
    append_log(
        strategy_id,
        "error",
        f"Leverage or margin-mode setup failed for {symbol}: {error}",
    )
    return True
