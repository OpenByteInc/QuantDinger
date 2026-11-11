"""
Unified auto-stop for live strategies after fatal exchange/auth/connectivity errors.

Position sync and order workers call `auto_stop_live_strategy` so DB status, executor
threads, and runtime logs stay consistent (avoids endless retry spam after restart).
"""

from __future__ import annotations

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
        "below step/min", "below lot step/minqty", "below min/precision",
        "below mintradeusdt/precision",
        '"code":-2022', "'code': -2022", "'code':-2022",
        '"code":-4118', "'code': -4118", "'code':-4118",
        "reduceonly order is rejected", "reduce only order is rejected",
        "reduceonly order failed", "reduce only order failed",
    ))


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
