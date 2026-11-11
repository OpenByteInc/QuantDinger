"""Strategy run identity and immutable run snapshots."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, Optional

from app.utils.db import get_db_connection
from app.utils.logger import get_logger

from .events import append_runtime_event

logger = get_logger(__name__)


@dataclass(frozen=True)
class StrategyRunSnapshot:
    strategy_run_id: int
    strategy_id: int
    user_id: int
    code_hash: str
    runtime_epoch: int
    runtime_status: str


def code_hash_for(code: str) -> str:
    return hashlib.sha256(str(code or "").encode("utf-8")).hexdigest()


def _safe_json(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        try:
            return json.loads(json.dumps(value, default=str))
        except Exception:
            return dict(value)
    return {}


def _snapshot_from_row(
    row: Dict[str, Any],
    *,
    strategy_id: int,
    code_hash: str,
) -> StrategyRunSnapshot:
    return StrategyRunSnapshot(
        strategy_run_id=int(row.get("id") or 0),
        strategy_id=int(row.get("strategy_id") or strategy_id),
        user_id=int(row.get("user_id") or 1),
        code_hash=str(row.get("code_hash") or code_hash),
        runtime_epoch=int(row.get("runtime_epoch") or 1),
        runtime_status=str(row.get("runtime_status") or "running"),
    )


def _active_run_for_strategy(
    strategy_id: int,
    code_hash: str,
    runtime_epoch: int,
) -> Optional[StrategyRunSnapshot]:
    try:
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                SELECT id, strategy_id, user_id, code_hash, runtime_epoch, runtime_status
                FROM strategy_runs
                WHERE strategy_id = %s
                  AND runtime_epoch = %s
                  AND runtime_status IN ('running', 'recovering', 'paused', 'needs_review')
                ORDER BY id DESC
                LIMIT 1
                """,
                (int(strategy_id), int(runtime_epoch)),
            )
            row = cur.fetchone() or {}
            cur.close()
        if not row:
            return None
        # If code changed while a run is active, continue the active run. The
        # immutable code_hash tells audit/recovery which source actually started it.
        return _snapshot_from_row(
            row,
            strategy_id=strategy_id,
            code_hash=code_hash,
        )
    except Exception as exc:
        logger.debug("active strategy run lookup failed: %s", exc)
        return None


def ensure_strategy_run(
    *,
    strategy_id: int,
    user_id: int = 1,
    code: str = "",
    parameter_snapshot: Dict[str, Any] | None = None,
    source_version_id: str = "",
    exchange_id: str = "",
    credential_id: int = 0,
    symbol: str = "",
    market_type: str = "swap",
    position_mode: str = "",
    runtime_epoch: int = 1,
) -> StrategyRunSnapshot:
    """Return an active run for a strategy, creating one if needed."""
    sid = int(strategy_id)
    ch = code_hash_for(code)
    desired_epoch = max(1, int(runtime_epoch or 1))
    active = _active_run_for_strategy(sid, ch, desired_epoch)
    if active is not None and active.strategy_run_id > 0:
        return active

    params = _safe_json(parameter_snapshot or {})
    try:
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute("SELECT pg_advisory_xact_lock(%s, %s)", (7414, sid))
            cur.execute(
                """
                SELECT id, strategy_id, user_id, code_hash, runtime_epoch, runtime_status
                FROM strategy_runs
                WHERE strategy_id = %s
                  AND runtime_status IN ('running', 'recovering', 'paused', 'needs_review')
                ORDER BY id DESC
                LIMIT 1
                FOR UPDATE
                """,
                (sid,),
            )
            existing = cur.fetchone() or {}
            if existing and int(existing.get("runtime_epoch") or 0) >= desired_epoch:
                snapshot = _snapshot_from_row(
                    existing,
                    strategy_id=sid,
                    code_hash=ch,
                )
                db.commit()
                cur.close()
                return snapshot
            if existing:
                cur.execute(
                    """
                    UPDATE strategy_runs
                    SET runtime_status = 'stopped',
                        stopped_at = NOW(),
                        stop_reason = CASE
                            WHEN stop_reason = '' THEN 'superseded_by_runtime_epoch'
                            ELSE stop_reason
                        END
                    WHERE strategy_id = %s
                      AND runtime_status IN ('running', 'recovering', 'paused', 'needs_review')
                    """,
                    (sid,),
                )
            cur.execute(
                """
                INSERT INTO strategy_runs
                (user_id, strategy_id, source_version_id, code_hash, parameter_snapshot_json,
                 exchange_id, credential_id, symbol, market_type, position_mode,
                 runtime_status, runtime_epoch, started_at)
                VALUES
                (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'running', %s, NOW())
                """,
                (
                    int(user_id or 1),
                    sid,
                    str(source_version_id or ""),
                    ch,
                    json.dumps(params, ensure_ascii=False),
                    str(exchange_id or ""),
                    int(credential_id or 0),
                    str(symbol or ""),
                    str(market_type or "swap"),
                    str(position_mode or ""),
                    desired_epoch,
                ),
            )
            run_id = int(cur.lastrowid or 0)
            db.commit()
            cur.close()
        append_runtime_event(
            strategy_id=sid,
            strategy_run_id=run_id,
            event_type="strategy_started",
            message="Strategy run started",
            payload={
                "code_hash": ch,
                "symbol": symbol,
                "market_type": market_type,
                "runtime_epoch": desired_epoch,
            },
        )
        return StrategyRunSnapshot(
            strategy_run_id=run_id,
            strategy_id=sid,
            user_id=int(user_id or 1),
            code_hash=ch,
            runtime_epoch=desired_epoch,
            runtime_status="running",
        )
    except Exception as exc:
        logger.warning("strategy run create failed; using ephemeral run: %s", exc)
        return StrategyRunSnapshot(
            strategy_run_id=0,
            strategy_id=sid,
            user_id=int(user_id or 1),
            code_hash=ch,
            runtime_epoch=desired_epoch,
            runtime_status="ephemeral",
        )


def finish_strategy_run(strategy_run_id: int, *, reason: str = "") -> None:
    """Close an active run so the next start receives a new immutable run identity."""
    run_id = int(strategy_run_id or 0)
    if run_id <= 0:
        return
    try:
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                UPDATE strategy_runs
                SET runtime_status = 'stopped', stopped_at = NOW(), stop_reason = %s
                WHERE id = %s AND runtime_status IN ('running', 'recovering', 'paused', 'needs_review')
                RETURNING strategy_id
                """,
                (str(reason or ""), run_id),
            )
            row = cur.fetchone() or {}
            db.commit()
            cur.close()
        strategy_id = int(row.get("strategy_id") or 0)
        if strategy_id > 0:
            append_runtime_event(
                strategy_id=strategy_id,
                strategy_run_id=run_id,
                event_type="strategy_stopped",
                message="Strategy run stopped",
                payload={"reason": str(reason or "")},
            )
    except Exception as exc:
        logger.warning("strategy run finish failed: %s", exc)
