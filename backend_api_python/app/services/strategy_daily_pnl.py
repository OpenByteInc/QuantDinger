"""Daily strategy P&L snapshots and API metrics.

``today_pnl`` is an equity delta, not a sum of today's close rows.  That
distinction matters for positions carried across midnight: their full
unrealized P&L belongs to the strategy's lifetime result, while only the change
since the user's local day began belongs to today's result.
"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable

from app.utils.db import get_db_connection
from app.utils.logger import get_logger
from app.utils.pnl import POSITION_UNREALIZED_PNL_SQL

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore[misc,assignment]


logger = get_logger(__name__)

_CAPTURE_INTERVAL_SECONDS = 300
_OPENING_SNAPSHOT_TOLERANCE_SECONDS = 15 * 60
_capture_lock = threading.Lock()
_last_capture_monotonic: Dict[int, float] = {}


def resolve_business_day_window(
    *,
    now: datetime | None = None,
    timezone_name: str = "UTC",
) -> tuple[datetime, datetime, str]:
    """Return the user's current local-day window as naive UTC datetimes."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    tz, resolved_name = _zone(timezone_name)
    local_now = current.astimezone(tz)
    local_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    local_end = local_start + timedelta(days=1)
    return (
        local_start.astimezone(timezone.utc).replace(tzinfo=None),
        local_end.astimezone(timezone.utc).replace(tzinfo=None),
        resolved_name,
    )


def choose_opening_equity(
    *,
    day_start: datetime,
    before: Dict[str, Any] | None,
    after: Dict[str, Any] | None,
    reconstructed: float,
) -> tuple[float, bool, str]:
    """Choose the closest day-opening equity and report whether it is exact."""
    candidates: list[tuple[float, Dict[str, Any], str]] = []
    for row, source in ((before, "snapshot_before"), (after, "snapshot_after")):
        if not row or row.get("captured_at") is None:
            continue
        captured_at = row["captured_at"]
        if getattr(captured_at, "tzinfo", None) is not None:
            captured_at = captured_at.astimezone(timezone.utc).replace(tzinfo=None)
        distance = abs((captured_at - day_start).total_seconds())
        candidates.append((distance, row, source))
    if candidates:
        distance, row, source = min(candidates, key=lambda item: item[0])
        return (
            float(row.get("equity") or 0.0),
            distance > _OPENING_SNAPSHOT_TOLERANCE_SECONDS,
            source,
        )
    return float(reconstructed or 0.0), True, "ledger_reconstruction"


def load_strategy_daily_metrics(
    strategies: Iterable[Dict[str, Any]],
    *,
    user_id: int,
    client_timezone: str = "",
) -> Dict[int, Dict[str, Any]]:
    """Capture current equity and return lifetime plus daily metrics in bulk."""
    rows = [dict(row) for row in (strategies or [])]
    strategy_ids = sorted({int(row.get("id") or 0) for row in rows if int(row.get("id") or 0) > 0})
    if not strategy_ids:
        return {}

    timezone_name = _load_user_timezone(int(user_id), client_timezone)
    day_start, day_end, resolved_timezone = resolve_business_day_window(timezone_name=timezone_name)
    current = _load_current_equity(strategy_ids, user_id=int(user_id))
    if not current:
        return {}

    before = _load_boundary_snapshots(strategy_ids, day_start, before=True)
    after = _load_boundary_snapshots(strategy_ids, day_start, before=False, day_end=day_end)
    capital_resets = _load_latest_capital_resets(strategy_ids, day_start, day_end)
    reconstructed = _load_reconstructed_opening(strategy_ids, int(user_id), day_start)

    metrics: Dict[int, Dict[str, Any]] = {}
    for strategy_id, item in current.items():
        opening, estimated, source = choose_opening_equity(
            day_start=day_start,
            before=before.get(strategy_id),
            after=after.get(strategy_id),
            reconstructed=float(reconstructed.get(strategy_id, item["initial_capital"])),
        )
        reset = capital_resets.get(strategy_id)
        if reset:
            opening = float(reset.get("equity") or 0.0)
            estimated = False
            source = "capital_change"
        current_equity = float(item["equity"])
        metrics[strategy_id] = {
            "current_equity": round(current_equity, 8),
            "total_pnl": round(current_equity - float(item["initial_capital"]), 8),
            "today_pnl": round(current_equity - opening, 8),
            "today_pnl_estimated": bool(estimated),
            "today_pnl_source": source,
            "today_pnl_timezone": resolved_timezone,
            "today_opening_equity": round(opening, 8),
            "completed_trades": int(item.get("completed_trades") or 0),
            "wins": int(item.get("wins") or 0),
            "win_rate": round(float(item.get("win_rate") or 0.0), 8),
        }
    return metrics


def maybe_capture_strategy_equity_snapshot(strategy_id: int) -> None:
    """Best-effort five-minute mark-to-market capture for running strategies."""
    sid = int(strategy_id or 0)
    if sid <= 0 or os.getenv("SKIP_STARTUP_HOOKS") == "1":
        return
    now_mono = time.monotonic()
    with _capture_lock:
        last = float(_last_capture_monotonic.get(sid, 0.0))
        if now_mono - last < _CAPTURE_INTERVAL_SECONDS:
            return
        _last_capture_monotonic[sid] = now_mono
    try:
        current = _load_current_equity([sid])
        _capture_rows(current.values())
    except Exception as exc:  # runtime monitoring must never stop trading
        logger.debug("strategy equity snapshot skipped: %s", exc)


def _load_user_timezone(user_id: int, client_timezone: str) -> str:
    saved = ""
    try:
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute("SELECT COALESCE(timezone, '') AS timezone FROM qd_users WHERE id = %s", (int(user_id),))
            row = cur.fetchone() or {}
            saved = str(row.get("timezone") or "").strip()
            cur.close()
    except Exception:
        saved = ""
    for candidate in (saved, client_timezone, os.getenv("TZ", ""), "UTC"):
        if candidate and _zone(candidate)[1] == candidate:
            return candidate
    return "UTC"


def _zone(name: str):
    candidate = str(name or "UTC").strip() or "UTC"
    if ZoneInfo is not None:
        try:
            return ZoneInfo(candidate), candidate
        except Exception:
            pass
    return timezone.utc, "UTC"


def _load_current_equity(strategy_ids: Iterable[int], user_id: int | None = None) -> Dict[int, Dict[str, Any]]:
    ids = sorted({int(value) for value in strategy_ids if int(value or 0) > 0})
    if not ids:
        return {}
    placeholders = ",".join(["%s"] * len(ids))
    params: list[Any] = list(ids)
    user_filter = ""
    if user_id is not None:
        user_filter = " AND s.user_id = %s"
        params.append(int(user_id))
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            f"""
            WITH trade_totals AS (
                SELECT strategy_id,
                       SUM(COALESCE(profit, 0) - COALESCE(commission_quote, commission, 0)) AS realized_net,
                       COUNT(*) FILTER (
                           WHERE LOWER(COALESCE(type, '')) NOT LIKE 'open%%'
                             AND LOWER(COALESCE(type, '')) NOT LIKE 'add%%'
                             AND profit IS NOT NULL
                       ) AS completed_trades,
                       COUNT(*) FILTER (
                           WHERE LOWER(COALESCE(type, '')) NOT LIKE 'open%%'
                             AND LOWER(COALESCE(type, '')) NOT LIKE 'add%%'
                             AND profit IS NOT NULL
                             AND profit > 0
                       ) AS wins
                FROM qd_strategy_trades
                WHERE strategy_id IN ({placeholders})
                GROUP BY strategy_id
            ), funding_totals AS (
                SELECT strategy_id, SUM(COALESCE(amount, 0)) AS funding_payment
                FROM qd_strategy_funding_fees
                WHERE strategy_id IN ({placeholders})
                GROUP BY strategy_id
            ), broker_activity_totals AS (
                SELECT strategy_id, SUM(COALESCE(amount, 0)) AS broker_activity_payment
                FROM qd_strategy_broker_activities
                WHERE strategy_id IN ({placeholders})
                GROUP BY strategy_id
            ), position_totals AS (
                SELECT strategy_id,
                       SUM({POSITION_UNREALIZED_PNL_SQL}) AS unrealized,
                       SUM(CASE WHEN ABS(COALESCE(size, 0)) > 0 THEN 1 ELSE 0 END) AS open_positions
                FROM qd_strategy_positions
                WHERE strategy_id IN ({placeholders})
                GROUP BY strategy_id
            ), virtual_account_totals AS (
                SELECT strategy_id, COALESCE(realized_pnl, 0) AS realized_net
                FROM qd_strategy_virtual_accounts
                WHERE strategy_id IN ({placeholders})
            ), virtual_trade_totals AS (
                SELECT strategy_id,
                       COUNT(*) FILTER (
                           WHERE LOWER(COALESCE(type, '')) NOT LIKE 'open%%'
                             AND LOWER(COALESCE(type, '')) NOT LIKE 'add%%'
                             AND profit IS NOT NULL
                       ) AS completed_trades,
                       COUNT(*) FILTER (
                           WHERE LOWER(COALESCE(type, '')) NOT LIKE 'open%%'
                             AND LOWER(COALESCE(type, '')) NOT LIKE 'add%%'
                             AND profit IS NOT NULL
                             AND profit > 0
                       ) AS wins
                FROM qd_strategy_virtual_trades
                WHERE strategy_id IN ({placeholders})
                GROUP BY strategy_id
            ), virtual_position_totals AS (
                SELECT strategy_id,
                       SUM(COALESCE(unrealized_pnl, 0)) AS unrealized,
                       SUM(CASE WHEN ABS(COALESCE(size, 0)) > 0 THEN 1 ELSE 0 END) AS open_positions
                FROM qd_strategy_virtual_positions
                WHERE strategy_id IN ({placeholders})
                GROUP BY strategy_id
            )
            SELECT s.id AS strategy_id, s.user_id, s.created_at, s.status, s.execution_mode,
                   COALESCE(s.initial_capital, 0) AS initial_capital,
                   COALESCE(t.realized_net, 0) AS realized_net,
                   COALESCE(f.funding_payment, 0) AS funding_payment,
                   COALESCE(b.broker_activity_payment, 0) AS broker_activity_payment,
                   COALESCE(p.unrealized, 0) AS unrealized,
                   COALESCE(p.open_positions, 0) AS open_positions,
                   COALESCE(va.realized_net, 0) AS virtual_realized_net,
                   COALESCE(t.completed_trades, 0) AS completed_trades,
                   COALESCE(t.wins, 0) AS wins,
                   COALESCE(vt.completed_trades, 0) AS virtual_completed_trades,
                   COALESCE(vt.wins, 0) AS virtual_wins,
                   COALESCE(vp.unrealized, 0) AS virtual_unrealized,
                   COALESCE(vp.open_positions, 0) AS virtual_open_positions
            FROM qd_strategies_trading s
            LEFT JOIN trade_totals t ON t.strategy_id = s.id
            LEFT JOIN funding_totals f ON f.strategy_id = s.id
            LEFT JOIN broker_activity_totals b ON b.strategy_id = s.id
            LEFT JOIN position_totals p ON p.strategy_id = s.id
            LEFT JOIN virtual_account_totals va ON va.strategy_id = s.id
            LEFT JOIN virtual_trade_totals vt ON vt.strategy_id = s.id
            LEFT JOIN virtual_position_totals vp ON vp.strategy_id = s.id
            WHERE s.id IN ({placeholders}){user_filter}
            """,
            tuple(ids + ids + ids + ids + ids + ids + ids + params),
        )
        rows = cur.fetchall() or []
        cur.close()
    output: Dict[int, Dict[str, Any]] = {}
    for raw in rows:
        row = dict(raw)
        strategy_id = int(row.get("strategy_id") or 0)
        initial = float(row.get("initial_capital") or 0.0)
        realized = float(row.get("realized_net") or 0.0)
        funding_payment = float(row.get("funding_payment") or 0.0)
        broker_activity_payment = float(row.get("broker_activity_payment") or 0.0)
        unrealized = float(row.get("unrealized") or 0.0)
        completed_trades = int(row.get("completed_trades") or 0)
        wins = int(row.get("wins") or 0)
        if str(row.get("execution_mode") or "").strip().lower() == "signal":
            realized = float(row.get("virtual_realized_net") or 0.0)
            funding_payment = 0.0
            broker_activity_payment = 0.0
            unrealized = float(row.get("virtual_unrealized") or 0.0)
            completed_trades = int(row.get("virtual_completed_trades") or 0)
            wins = int(row.get("virtual_wins") or 0)
            row["open_positions"] = int(row.get("virtual_open_positions") or 0)
        output[strategy_id] = {
            **row,
            "initial_capital": initial,
            "realized_net": realized + funding_payment + broker_activity_payment,
            "funding_payment": funding_payment,
            "broker_activity_payment": broker_activity_payment,
            "unrealized": unrealized,
            "completed_trades": completed_trades,
            "wins": wins,
            "win_rate": (wins / completed_trades) if completed_trades else 0.0,
            "equity": initial + realized + funding_payment + broker_activity_payment + unrealized,
        }
    return output


def _capture_rows(rows: Iterable[Dict[str, Any]]) -> None:
    values = [dict(row) for row in rows]
    if not values:
        return
    try:
        with get_db_connection() as db:
            cur = db.cursor()
            for row in values:
                capture_interval = (
                    _CAPTURE_INTERVAL_SECONDS
                    if str(row.get("status") or "").lower() == "running" or int(row.get("open_positions") or 0) > 0
                    else 24 * 60 * 60
                )
                cur.execute(
                    """
                    SELECT initial_capital
                    FROM qd_strategy_equity_snapshots
                    WHERE strategy_id = %s
                    ORDER BY captured_at DESC, id DESC
                    LIMIT 1
                    """,
                    (int(row.get("strategy_id") or 0),),
                )
                previous = cur.fetchone() or {}
                previous_capital = previous.get("initial_capital")
                current_capital = float(row.get("initial_capital") or 0.0)
                capital_changed = (
                    previous_capital not in (None, "")
                    and abs(float(previous_capital or 0.0) - current_capital) > 1e-8
                )
                cur.execute(
                    """
                    INSERT INTO qd_strategy_equity_snapshots
                        (user_id, strategy_id, equity, realized_pnl, unrealized_pnl,
                         initial_capital, basis_reason, captured_at)
                    SELECT %s, %s, %s, %s, %s, %s, %s, NOW()
                    WHERE NOT EXISTS (
                        SELECT 1 FROM qd_strategy_equity_snapshots
                        WHERE strategy_id = %s
                          AND captured_at >= NOW() - (%s * INTERVAL '1 second')
                          AND NOT %s
                    )
                    """,
                    (
                        int(row.get("user_id") or 0),
                        int(row.get("strategy_id") or 0),
                        float(row.get("equity") or 0.0),
                        float(row.get("realized_net") or 0.0),
                        float(row.get("unrealized") or 0.0),
                        current_capital,
                        "capital_change" if capital_changed else "",
                        int(row.get("strategy_id") or 0),
                        capture_interval,
                        bool(capital_changed),
                    ),
                )
            db.commit()
            cur.close()
    except Exception as exc:
        logger.debug("strategy equity snapshot capture skipped: %s", exc)


def _load_boundary_snapshots(
    strategy_ids: list[int],
    day_start: datetime,
    *,
    before: bool,
    day_end: datetime | None = None,
) -> Dict[int, Dict[str, Any]]:
    placeholders = ",".join(["%s"] * len(strategy_ids))
    if before:
        condition = "captured_at <= %s"
        order = "captured_at DESC"
        params: tuple[Any, ...] = tuple(strategy_ids + [day_start])
    else:
        condition = "captured_at >= %s AND captured_at < %s"
        order = "captured_at ASC"
        params = tuple(strategy_ids + [day_start, day_end])
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            f"""
            SELECT DISTINCT ON (strategy_id) strategy_id, equity, realized_pnl,
                   unrealized_pnl, captured_at
            FROM qd_strategy_equity_snapshots
            WHERE strategy_id IN ({placeholders}) AND {condition}
            ORDER BY strategy_id, {order}
            """,
            params,
        )
        rows = cur.fetchall() or []
        cur.close()
    return {int(row.get("strategy_id") or 0): dict(row) for row in rows}


def _load_latest_capital_resets(
    strategy_ids: list[int],
    day_start: datetime,
    day_end: datetime,
) -> Dict[int, Dict[str, Any]]:
    placeholders = ",".join(["%s"] * len(strategy_ids))
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            f"""
            SELECT DISTINCT ON (strategy_id) strategy_id, equity, captured_at
            FROM qd_strategy_equity_snapshots
            WHERE strategy_id IN ({placeholders})
              AND basis_reason = 'capital_change'
              AND captured_at >= %s AND captured_at < %s
            ORDER BY strategy_id, captured_at DESC, id DESC
            """,
            tuple(strategy_ids + [day_start, day_end]),
        )
        rows = cur.fetchall() or []
        cur.close()
    return {int(row.get("strategy_id") or 0): dict(row) for row in rows}


def _load_reconstructed_opening(strategy_ids: list[int], user_id: int, day_start: datetime) -> Dict[int, float]:
    placeholders = ",".join(["%s"] * len(strategy_ids))
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            f"""
            SELECT s.id AS strategy_id,
                   COALESCE(s.initial_capital, 0) +
                   CASE WHEN LOWER(COALESCE(s.execution_mode, 'signal')) = 'signal'
                        THEN COALESCE((
                            SELECT SUM(COALESCE(v.profit, 0) - COALESCE(v.commission_quote, 0))
                            FROM qd_strategy_virtual_trades v
                            WHERE v.strategy_id = s.id AND v.created_at < %s
                        ), 0)
                        ELSE COALESCE((
                            SELECT SUM(COALESCE(t.profit, 0) - COALESCE(t.commission_quote, t.commission, 0))
                            FROM qd_strategy_trades t
                            WHERE t.strategy_id = s.id AND t.created_at < %s
                        ), 0) + COALESCE((
                            SELECT SUM(COALESCE(f.amount, 0))
                            FROM qd_strategy_funding_fees f
                            WHERE f.strategy_id = s.id AND f.occurred_at < %s
                        ), 0) + COALESCE((
                            SELECT SUM(COALESCE(b.amount, 0))
                            FROM qd_strategy_broker_activities b
                            WHERE b.strategy_id = s.id AND b.occurred_at < %s
                        ), 0)
                   END AS opening_equity
            FROM qd_strategies_trading s
            WHERE s.id IN ({placeholders}) AND s.user_id = %s
            """,
            tuple([day_start, day_start, day_start, day_start] + strategy_ids + [int(user_id)]),
        )
        rows = cur.fetchall() or []
        cur.close()
    return {
        int(row.get("strategy_id") or 0): float(row.get("opening_equity") or 0.0)
        for row in rows
    }


__all__ = [
    "choose_opening_equity",
    "load_strategy_daily_metrics",
    "maybe_capture_strategy_equity_snapshot",
    "resolve_business_day_window",
]
