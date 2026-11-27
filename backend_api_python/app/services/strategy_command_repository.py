"""PostgreSQL repository for durable strategy lifecycle commands and leases."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from app.utils.db import get_db_connection


TERMINAL_COMMAND_STATUSES = frozenset({"succeeded", "failed", "cancelled"})

# The acknowledgement belongs to a run AND its current lease, not just a process.
READY_RUNTIME_EXISTS_SQL = """
    EXISTS (
        SELECT 1 FROM strategy_runs AS run
        JOIN strategy_runtime_state AS ready
          ON ready.strategy_run_id = run.id AND ready.strategy_id = run.strategy_id
         AND ready.state_key = 'runtime_ready'
        JOIN qd_strategy_runtime_leases AS lease ON lease.strategy_id = run.strategy_id
        WHERE run.strategy_id = strategy.id
          AND run.runtime_epoch = strategy.desired_runtime_epoch
          AND run.runtime_status = 'running'
          AND lease.lease_expires_at > NOW()
          AND ready.state_json->>'fencing_token' = lease.fencing_token::text
    )
"""


@dataclass(frozen=True)
class StrategyCommand:
    id: int
    strategy_id: int
    user_id: int
    command_type: str
    status: str
    idempotency_key: str
    payload: dict[str, Any]
    result: dict[str, Any] | None = None
    attempts: int = 0
    error_message: str = ""
    elapsed_seconds: float = 0.0

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "StrategyCommand":
        payload = row.get("payload_json") or {}
        result = row.get("result_json") or {}
        if isinstance(payload, str):
            payload = json.loads(payload)
        if isinstance(result, str):
            result = json.loads(result)
        return cls(
            id=int(row["id"]),
            strategy_id=int(row["strategy_id"]),
            user_id=int(row.get("user_id") or 0),
            command_type=str(row["command_type"]),
            status=str(row["status"]),
            idempotency_key=str(row.get("idempotency_key") or ""),
            payload=dict(payload),
            result=dict(result),
            attempts=int(row.get("attempts") or 0),
            error_message=str(row.get("error_message") or ""),
            elapsed_seconds=float(row.get("elapsed_seconds") or 0),
        )


class StrategyCommandRepository:
    def enqueue(
        self,
        *,
        strategy_id: int,
        command_type: str,
        user_id: int = 0,
        payload: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> StrategyCommand:
        command_type = command_type.strip().lower()
        if command_type not in {"start", "stop", "restart", "reconcile"}:
            raise ValueError(f"Unsupported strategy command: {command_type}")
        key = idempotency_key or uuid.uuid4().hex
        body = json.dumps(payload or {}, default=str)

        with get_db_connection() as db:
            cur = db.cursor()
            try:
                cur.execute("SELECT pg_advisory_xact_lock(%s, %s)", (7413, int(strategy_id)))
                cur.execute(
                    """
                    SELECT * FROM qd_strategy_commands
                    WHERE strategy_id = %s AND command_type = %s
                      AND status IN ('pending', 'processing')
                    ORDER BY id DESC
                    LIMIT 1
                    FOR UPDATE
                    """,
                    (int(strategy_id), command_type),
                )
                row = cur.fetchone()
                if row:
                    command = StrategyCommand.from_row(dict(row))
                    if (
                        command_type == "stop"
                        and bool((payload or {}).get("close_positions"))
                        and not bool(command.payload.get("close_positions"))
                    ):
                        cur.execute(
                            """
                            UPDATE qd_strategy_commands
                            SET payload_json = COALESCE(payload_json, '{}'::jsonb)
                                || %s::jsonb,
                                updated_at = NOW()
                            WHERE id = %s
                            RETURNING *
                            """,
                            (json.dumps({"close_positions": True}), command.id),
                        )
                        upgraded = cur.fetchone()
                        if upgraded:
                            command = StrategyCommand.from_row(dict(upgraded))
                    db.commit()
                    return command

                cur.execute(
                    """
                    INSERT INTO qd_strategy_commands
                        (strategy_id, user_id, command_type, idempotency_key, payload_json)
                    SELECT %s, %s, %s, %s,
                           %s::jsonb || jsonb_build_object('requested_runtime_epoch', desired_runtime_epoch)
                    FROM qd_strategies_trading WHERE id = %s
                    ON CONFLICT (idempotency_key) DO UPDATE
                        SET idempotency_key = EXCLUDED.idempotency_key
                    RETURNING *
                    """,
                    (int(strategy_id), int(user_id or 0), command_type, key, body, int(strategy_id)),
                )
                row = cur.fetchone()
                db.commit()
                return StrategyCommand.from_row(dict(row))
            except Exception:
                db.rollback()
                raise
            finally:
                cur.close()

    def claim_next(self, *, owner_id: str, lease_seconds: int, max_attempts: int) -> StrategyCommand | None:
        with get_db_connection() as db:
            cur = db.cursor()
            try:
                cur.execute(
                    """
                        SELECT command.id
                        FROM qd_strategy_commands AS command
                        JOIN qd_strategies_trading AS strategy ON strategy.id = command.strategy_id
                        WHERE command.attempts < %s
                          AND (
                            (command.status = 'pending' AND command.available_at <= NOW())
                            OR
                            (command.status = 'processing' AND command.lease_expires_at < NOW())
                          )
                          AND NOT EXISTS (
                            SELECT 1 FROM qd_strategy_commands AS active
                            WHERE active.strategy_id = command.strategy_id
                              AND active.id <> command.id AND active.status = 'processing'
                              AND active.lease_expires_at >= NOW()
                          )
                        ORDER BY command.id
                        FOR UPDATE OF command, strategy SKIP LOCKED
                        LIMIT 1
                    """,
                    (int(max_attempts),),
                )
                candidate = cur.fetchone()
                if not candidate:
                    db.commit()
                    return None
                # Recheck with a fresh statement snapshot after locking the strategy;
                # another controller may have committed while candidates were scanned.
                cur.execute(
                    """
                    UPDATE qd_strategy_commands AS command
                    SET status = 'processing',
                        claimed_by = %s,
                        claimed_at = CASE WHEN command.status = 'processing'
                            AND command.claimed_by IS DISTINCT FROM %s
                            THEN NOW() ELSE COALESCE(command.claimed_at, NOW()) END,
                        lease_expires_at = NOW() + (%s * INTERVAL '1 second'),
                        attempts = command.attempts + 1,
                        updated_at = NOW()
                    WHERE command.id = %s AND NOT EXISTS (
                        SELECT 1 FROM qd_strategy_commands AS active
                        WHERE active.strategy_id = command.strategy_id
                          AND active.id <> command.id AND active.status = 'processing'
                          AND active.lease_expires_at >= NOW()
                    )
                    RETURNING command.*,
                        EXTRACT(EPOCH FROM (NOW() - command.claimed_at)) AS elapsed_seconds
                    """,
                    (owner_id, owner_id, int(lease_seconds), int(candidate["id"])),
                )
                row = cur.fetchone()
                db.commit()
                return StrategyCommand.from_row(dict(row)) if row else None
            except Exception:
                db.rollback()
                raise
            finally:
                cur.close()

    def complete(self, command_id: int, *, result: dict[str, Any] | None = None) -> None:
        self._finish(command_id, "succeeded", result=result)

    def defer_start(self, command_id: int, *, owner_id: str, result: dict) -> None:
        """Readiness polling is not a failed execution attempt."""
        with get_db_connection() as db:
            cur = db.cursor()
            try:
                cur.execute(
                    """UPDATE qd_strategy_commands
                       SET status = 'pending', result_json = %s::jsonb,
                           attempts = GREATEST(0, attempts - 1),
                           available_at = NOW() + (%s * INTERVAL '1 second'),
                           lease_expires_at = NULL, updated_at = NOW()
                       WHERE id = %s AND claimed_by = %s AND status = 'processing'""",
                    (json.dumps(result), max(2.0, float(result.get("retry_after_seconds") or 2)), int(command_id), owner_id),
                )
                db.commit()
            finally:
                cur.close()

    def begin_restart(self, command: StrategyCommand) -> int:
        """Advance the generation once, including after a command-worker crash."""
        with get_db_connection() as db:
            cur = db.cursor()
            try:
                cur.execute("SELECT payload_json FROM qd_strategy_commands WHERE id = %s FOR UPDATE", (command.id,))
                payload = (cur.fetchone() or {}).get("payload_json") or {}
                if payload.get("runtime_epoch"):
                    return int(payload["runtime_epoch"])
                cur.execute(
                    """UPDATE qd_strategies_trading
                       SET desired_runtime_epoch = GREATEST(1, desired_runtime_epoch) + 1,
                           runtime_requested_at = NOW(), updated_at = NOW()
                       WHERE id = %s AND status = 'running' AND (%s = 0 OR user_id = %s)
                       RETURNING desired_runtime_epoch""", (command.strategy_id, command.user_id, command.user_id),
                )
                row = cur.fetchone()
                if not row:
                    raise RuntimeError("Strategy is not running or no longer exists.")
                epoch = int(row["desired_runtime_epoch"])
                cur.execute(
                    """UPDATE qd_strategy_commands
                       SET payload_json = payload_json || %s::jsonb WHERE id = %s""",
                    (json.dumps({"runtime_epoch": epoch}), command.id),
                )
                cur.execute(
                    """UPDATE qd_strategy_runtime_leases
                       SET lease_expires_at = NOW() - INTERVAL '1 second',
                           fencing_token = fencing_token + 1, owner_id = '', updated_at = NOW()
                       WHERE strategy_id = %s""", (command.strategy_id,),
                )
                db.commit()
                return epoch
            except Exception:
                db.rollback()
                raise
            finally:
                cur.close()

    def runtime_request(self, strategy_id: int) -> dict:
        return self.runtime_requests([strategy_id]).get(int(strategy_id), {})

    def defer_exchange_start(self, strategy_id: int, *, epoch: int, seconds: float, token: int = 0) -> None:
        """Fence a durable startup wait to the user's current running generation."""
        with get_db_connection() as db:
            cur = db.cursor()
            try:
                cur.execute(
                    """INSERT INTO strategy_runtime_state (strategy_run_id, strategy_id, state_key, state_json)
                       SELECT 0, s.id, 'startup_cooldown', jsonb_build_object(
                           'epoch', s.desired_runtime_epoch, 'retry_at', EXTRACT(EPOCH FROM NOW()) + %s)
                       FROM qd_strategies_trading s
                       WHERE s.id = %s AND s.status = 'running' AND s.desired_runtime_epoch = %s
                         AND (%s = 0 OR EXISTS (SELECT 1 FROM qd_strategy_runtime_leases l
                             WHERE l.strategy_id = s.id AND l.fencing_token = %s AND l.lease_expires_at > NOW()))
                       FOR UPDATE OF s
                       ON CONFLICT (strategy_run_id, strategy_id, state_key) DO UPDATE
                       SET state_json = CASE WHEN strategy_runtime_state.state_json->>'epoch' = EXCLUDED.state_json->>'epoch'
                           THEN EXCLUDED.state_json || jsonb_build_object('retry_at', GREATEST(
                               (strategy_runtime_state.state_json->>'retry_at')::numeric,
                               (EXCLUDED.state_json->>'retry_at')::numeric)) ELSE EXCLUDED.state_json END,
                           updated_at = NOW(),
                           version = strategy_runtime_state.version + 1""",
                    (max(1.0, float(seconds)), int(strategy_id), int(epoch), int(token), int(token)),
                )
                db.commit()
            finally:
                cur.close()

    def runtime_requests(self, strategy_ids) -> dict[int, dict]:
        ids = sorted({int(strategy_id) for strategy_id in strategy_ids})
        if not ids:
            return {}
        with get_db_connection() as db:
            cur = db.cursor()
            try:
                cur.execute(
                    f"""SELECT strategy.id, strategy.status, strategy.desired_runtime_epoch,
                              {READY_RUNTIME_EXISTS_SQL} AS ready,
                              COALESCE((cooldown.state_json->>'retry_at')::numeric, 0) AS startup_retry_at,
                              GREATEST(0, COALESCE((cooldown.state_json->>'retry_at')::numeric, 0)
                                  - EXTRACT(EPOCH FROM NOW())) AS retry_after_seconds
                       FROM qd_strategies_trading AS strategy
                       LEFT JOIN strategy_runtime_state cooldown ON cooldown.strategy_id = strategy.id
                         AND cooldown.strategy_run_id = 0 AND cooldown.state_key = 'startup_cooldown'
                         AND cooldown.state_json->>'epoch' = strategy.desired_runtime_epoch::text
                       WHERE strategy.id = ANY(%s)""",
                    (ids,),
                )
                return {int(row["id"]): dict(row) for row in cur.fetchall()}
            finally:
                cur.close()

    def acknowledge_runtime_ready(self, *, strategy_id: int, run_id: int, epoch: int, token: int) -> bool:
        with get_db_connection() as db:
            cur = db.cursor()
            try:
                cur.execute(
                    """INSERT INTO strategy_runtime_state
                           (strategy_run_id, strategy_id, state_key, state_json)
                       SELECT run.id, run.strategy_id, 'runtime_ready', %s::jsonb
                       FROM strategy_runs AS run
                       JOIN qd_strategies_trading AS strategy ON strategy.id = run.strategy_id
                       JOIN qd_strategy_runtime_leases AS lease ON lease.strategy_id = run.strategy_id
                       WHERE run.id = %s AND strategy.id = %s AND strategy.status = 'running'
                         AND run.runtime_epoch = %s AND strategy.desired_runtime_epoch = %s
                         AND run.runtime_status = 'running'
                         AND lease.fencing_token = %s AND lease.lease_expires_at > NOW()
                       ON CONFLICT (strategy_run_id, strategy_id, state_key)
                       DO UPDATE SET state_json = EXCLUDED.state_json, updated_at = NOW()
                       RETURNING id""",
                    (json.dumps({"fencing_token": int(token)}), int(run_id), int(strategy_id),
                     int(epoch), int(epoch), int(token)),
                )
                ready = cur.fetchone() is not None
                db.commit()
                return ready
            finally:
                cur.close()

    def fail(self, command_id: int, error: str, *, retry_delay_seconds: int | None = None) -> None:
        if retry_delay_seconds is not None:
            with get_db_connection() as db:
                cur = db.cursor()
                try:
                    cur.execute(
                        """
                        UPDATE qd_strategy_commands
                        SET status = 'pending', error_message = %s,
                            available_at = NOW() + (%s * INTERVAL '1 second'),
                            lease_expires_at = NULL, updated_at = NOW()
                        WHERE id = %s
                        """,
                        (error[:4000], int(retry_delay_seconds), int(command_id)),
                    )
                    db.commit()
                finally:
                    cur.close()
            return
        self._finish(command_id, "failed", error=error)

    def _finish(
        self,
        command_id: int,
        status: str,
        *,
        result: dict[str, Any] | None = None,
        error: str = "",
    ) -> None:
        with get_db_connection() as db:
            cur = db.cursor()
            try:
                cur.execute(
                    """
                    UPDATE qd_strategy_commands
                    SET status = %s, result_json = %s::jsonb, error_message = %s,
                        completed_at = NOW(), lease_expires_at = NULL, updated_at = NOW()
                    WHERE id = %s
                    """,
                    (status, json.dumps(result or {}, default=str), error[:4000], int(command_id)),
                )
                db.commit()
            finally:
                cur.close()

    def get(self, command_id: int) -> StrategyCommand | None:
        with get_db_connection() as db:
            cur = db.cursor()
            try:
                cur.execute("SELECT * FROM qd_strategy_commands WHERE id = %s", (int(command_id),))
                row = cur.fetchone()
                return StrategyCommand.from_row(dict(row)) if row else None
            finally:
                cur.close()

    def has_active_strategy_lease(self, strategy_id: int) -> bool:
        with get_db_connection() as db:
            cur = db.cursor()
            try:
                cur.execute(
                    """
                    SELECT 1
                    FROM qd_strategy_runtime_leases
                    WHERE strategy_id = %s AND lease_expires_at >= NOW()
                    LIMIT 1
                    """,
                    (int(strategy_id),),
                )
                return cur.fetchone() is not None
            finally:
                cur.close()

    def has_pending_stop(self, strategy_id: int) -> bool:
        with get_db_connection() as db:
            cur = db.cursor()
            try:
                cur.execute(
                    """SELECT 1 FROM qd_strategy_commands
                       WHERE strategy_id = %s AND command_type = 'stop'
                         AND status IN ('pending', 'processing') LIMIT 1""",
                    (int(strategy_id),),
                )
                return cur.fetchone() is not None
            finally:
                cur.close()

    def acquire_strategy_lease(self, *, strategy_id: int, owner_id: str, lease_seconds: int) -> int | None:
        with get_db_connection() as db:
            cur = db.cursor()
            try:
                cur.execute(
                    """
                    INSERT INTO qd_strategy_runtime_leases
                        (strategy_id, owner_id, fencing_token, lease_expires_at, heartbeat_at)
                    VALUES (%s, %s, 1, NOW() + (%s * INTERVAL '1 second'), NOW())
                    ON CONFLICT (strategy_id) DO UPDATE
                    SET owner_id = EXCLUDED.owner_id,
                        fencing_token = CASE
                            WHEN qd_strategy_runtime_leases.owner_id = EXCLUDED.owner_id
                              AND qd_strategy_runtime_leases.lease_expires_at >= NOW()
                                THEN qd_strategy_runtime_leases.fencing_token
                            ELSE qd_strategy_runtime_leases.fencing_token + 1
                        END,
                        lease_expires_at = EXCLUDED.lease_expires_at,
                        heartbeat_at = NOW(),
                        updated_at = NOW()
                    WHERE qd_strategy_runtime_leases.owner_id = EXCLUDED.owner_id
                       OR qd_strategy_runtime_leases.lease_expires_at < NOW()
                    RETURNING fencing_token
                    """,
                    (int(strategy_id), owner_id, int(lease_seconds)),
                )
                row = cur.fetchone()
                db.commit()
                return int(row["fencing_token"]) if row else None
            except Exception:
                db.rollback()
                raise
            finally:
                cur.close()

    def renew_strategy_lease(self, *, strategy_id: int, owner_id: str, lease_seconds: int) -> bool:
        with get_db_connection() as db:
            cur = db.cursor()
            try:
                cur.execute(
                    """
                    UPDATE qd_strategy_runtime_leases
                    SET lease_expires_at = NOW() + (%s * INTERVAL '1 second'),
                        heartbeat_at = NOW(), updated_at = NOW()
                    WHERE strategy_id = %s AND owner_id = %s
                      AND lease_expires_at >= NOW()
                    """,
                    (int(lease_seconds), int(strategy_id), owner_id),
                )
                ok = cur.rowcount == 1
                db.commit()
                return ok
            finally:
                cur.close()

    def release_strategy_lease(self, *, strategy_id: int, owner_id: str) -> None:
        with get_db_connection() as db:
            cur = db.cursor()
            try:
                cur.execute(
                    """
                    UPDATE qd_strategy_runtime_leases
                    SET owner_id = '',
                        lease_expires_at = NOW() - INTERVAL '1 second',
                        heartbeat_at = NOW(),
                        updated_at = NOW()
                    WHERE strategy_id = %s AND owner_id = %s
                    """,
                    (int(strategy_id), owner_id),
                )
                db.commit()
            finally:
                cur.close()

    def revoke_strategy_lease(self, *, strategy_id: int) -> bool:
        with get_db_connection() as db:
            cur = db.cursor()
            try:
                cur.execute(
                    """
                    UPDATE qd_strategy_runtime_leases
                    SET owner_id = '',
                        fencing_token = fencing_token + 1,
                        lease_expires_at = NOW() - INTERVAL '1 second',
                        heartbeat_at = NOW(),
                        updated_at = NOW()
                    WHERE strategy_id = %s
                    """,
                    (int(strategy_id),),
                )
                revoked = cur.rowcount == 1
                db.commit()
                return revoked
            finally:
                cur.close()

    def record_worker_heartbeat(
        self,
        *,
        worker_id: str,
        role: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        with get_db_connection() as db:
            cur = db.cursor()
            try:
                cur.execute(
                    """
                    INSERT INTO qd_worker_heartbeats
                        (worker_id, role, status, metadata_json, started_at, heartbeat_at)
                    VALUES (%s, %s, 'running', %s::jsonb, NOW(), NOW())
                    ON CONFLICT (worker_id) DO UPDATE
                    SET role = EXCLUDED.role, status = 'running',
                        metadata_json = EXCLUDED.metadata_json,
                        heartbeat_at = NOW(), updated_at = NOW()
                    RETURNING worker_id
                    """,
                    (worker_id, role, json.dumps(metadata or {}, default=str)),
                )
                db.commit()
            finally:
                cur.close()

    def mark_worker_stopped(self, worker_id: str) -> None:
        with get_db_connection() as db:
            cur = db.cursor()
            try:
                cur.execute(
                    """
                    UPDATE qd_worker_heartbeats
                    SET status = 'stopped', heartbeat_at = NOW(), updated_at = NOW()
                    WHERE worker_id = %s
                    """,
                    (worker_id,),
                )
                db.commit()
            finally:
                cur.close()

    def acquire_process_lease(self, *, lease_key: str, owner_id: str, lease_seconds: int) -> bool:
        with get_db_connection() as db:
            cur = db.cursor()
            try:
                cur.execute(
                    """
                    INSERT INTO qd_process_leases (lease_key, owner_id, lease_expires_at, heartbeat_at)
                    VALUES (%s, %s, NOW() + (%s * INTERVAL '1 second'), NOW())
                    ON CONFLICT (lease_key) DO UPDATE
                    SET owner_id = EXCLUDED.owner_id,
                        lease_expires_at = EXCLUDED.lease_expires_at,
                        heartbeat_at = NOW(), updated_at = NOW()
                    WHERE qd_process_leases.owner_id = EXCLUDED.owner_id
                       OR qd_process_leases.lease_expires_at < NOW()
                    RETURNING lease_key
                    """,
                    (lease_key, owner_id, int(lease_seconds)),
                )
                acquired = cur.fetchone() is not None
                db.commit()
                return acquired
            finally:
                cur.close()

    def renew_process_lease(self, *, lease_key: str, owner_id: str, lease_seconds: int) -> bool:
        with get_db_connection() as db:
            cur = db.cursor()
            try:
                cur.execute(
                    """
                    UPDATE qd_process_leases
                    SET lease_expires_at = NOW() + (%s * INTERVAL '1 second'),
                        heartbeat_at = NOW(), updated_at = NOW()
                    WHERE lease_key = %s AND owner_id = %s AND lease_expires_at >= NOW()
                    """,
                    (int(lease_seconds), lease_key, owner_id),
                )
                renewed = cur.rowcount == 1
                db.commit()
                return renewed
            finally:
                cur.close()

    def release_process_lease(self, *, lease_key: str, owner_id: str) -> None:
        with get_db_connection() as db:
            cur = db.cursor()
            try:
                cur.execute(
                    "DELETE FROM qd_process_leases WHERE lease_key = %s AND owner_id = %s",
                    (lease_key, owner_id),
                )
                db.commit()
            finally:
                cur.close()

    def fail_exhausted_commands(self, max_attempts: int) -> int:
        with get_db_connection() as db:
            cur = db.cursor()
            try:
                cur.execute(
                    """
                    UPDATE qd_strategy_commands
                    SET status = 'failed',
                        error_message = CASE
                            WHEN error_message = '' THEN 'Command lease expired after maximum attempts.'
                            ELSE error_message
                        END,
                        completed_at = NOW(), lease_expires_at = NULL, updated_at = NOW()
                    WHERE status = 'processing' AND lease_expires_at < NOW() AND attempts >= %s
                    """,
                    (int(max_attempts),),
                )
                count = cur.rowcount
                db.commit()
                return count
            finally:
                cur.close()

    def cleanup_runtime_metadata(
        self,
        *,
        command_retention_days: int,
        heartbeat_retention_days: int,
    ) -> dict[str, int]:
        with get_db_connection() as db:
            cur = db.cursor()
            try:
                cur.execute(
                    """
                    DELETE FROM qd_strategy_commands
                    WHERE status IN ('succeeded', 'failed', 'cancelled')
                      AND completed_at < NOW() - (%s * INTERVAL '1 day')
                    """,
                    (int(command_retention_days),),
                )
                commands = cur.rowcount
                cur.execute(
                    """
                    DELETE FROM qd_worker_heartbeats
                    WHERE heartbeat_at < NOW() - (%s * INTERVAL '1 day')
                    """,
                    (int(heartbeat_retention_days),),
                )
                heartbeats = cur.rowcount
                cur.execute(
                    "DELETE FROM qd_process_leases WHERE lease_expires_at < NOW() - INTERVAL '1 day'"
                )
                process_leases = cur.rowcount
                cur.execute(
                    "DELETE FROM qd_strategy_runtime_leases WHERE lease_expires_at < NOW() - INTERVAL '1 day'"
                )
                strategy_leases = cur.rowcount
                db.commit()
                return {
                    "commands": commands,
                    "heartbeats": heartbeats,
                    "process_leases": process_leases,
                    "strategy_leases": strategy_leases,
                }
            finally:
                cur.close()
