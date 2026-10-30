"""Celery execution for persistent Agent Gateway jobs."""

from __future__ import annotations

import copy
import json
import time
from datetime import datetime

from app.celery_app import celery_app


SUPPORTED_KINDS = frozenset(
    {
        "backtest",
        "strategy_evolution",
    }
)


def supports_kind(kind: str) -> bool:
    return str(kind or "") in SUPPORTED_KINDS


def _execute(kind: str, payload: dict, on_progress):
    request_payload = copy.deepcopy(payload)
    if kind == "backtest":
        from app.routes.agent_v1.backtests import _run_backtest

        return _run_backtest(request_payload, on_progress)
    if kind == "strategy_evolution":
        from app.routes.strategy_evolution import _run_evolution_job

        return _run_evolution_job(request_payload, on_progress)

    raise ValueError(f"Unsupported durable agent job kind: {kind}")


_INTERRUPTED_ERRORS = {"strategy_evolution": "strategyEvolution.workerInterrupted"}


def _fail_interrupted_job(job_id: str, kind: str) -> None:
    from app.utils import agent_jobs

    error = _INTERRUPTED_ERRORS.get(kind, "WORKER_INTERRUPTED")
    if agent_jobs._set_failure(job_id, error):
        agent_jobs._publish_progress(
            job_id,
            {"phase": "failed", "error": error, "ts": time.time()},
            terminal=True,
        )
    else:
        agent_jobs._publish_terminal_state(job_id)


@celery_app.task(name="quantdinger.tasks.agent_job", acks_late=True)
def execute_agent_job(job_id: str) -> None:
    from app.utils import agent_jobs

    row = agent_jobs.get_job_for_worker(job_id)
    if row is None:
        raise ValueError(f"Agent job does not exist: {job_id}")
    if row.get("status") in {"succeeded", "failed", "cancelled"}:
        return

    kind = str(row.get("kind") or "")
    if not supports_kind(kind):
        raise ValueError(f"Unsupported durable agent job kind: {kind}")

    if not agent_jobs._set_status(job_id, "running", started_at=datetime.utcnow()):
        if row.get("status") == "running":
            # Redelivered while marked running: the broker visibility timeout exceeds the
            # hard time limit, so the previous attempt died (OOM, SIGKILL, redeploy).
            _fail_interrupted_job(job_id, kind)
        return
    agent_jobs._publish_progress(job_id, {"phase": "running", "ts": time.time()})
    try:
        request_payload = row.get("request") or {}
        if isinstance(request_payload, str):
            request_payload = json.loads(request_payload)
        def _progress(event):
            if agent_jobs.is_job_cancelled(job_id):
                raise agent_jobs.JobCancelledError("JOB_CANCELLED")
            agent_jobs._publish_progress(job_id, event)

        result = _execute(kind, dict(request_payload), _progress)
        if agent_jobs._set_result(job_id, result):
            agent_jobs._publish_progress(
                job_id,
                {"phase": "succeeded", "ts": time.time()},
                terminal=True,
            )
        else:
            agent_jobs._publish_terminal_state(job_id)
    except agent_jobs.JobCancelledError:
        agent_jobs._publish_terminal_state(job_id)
    except Exception as exc:
        failure = getattr(exc, "details", None)
        event = {"phase": "failed", "error": str(exc)[:500], "ts": time.time()}
        if isinstance(failure, dict):
            event["failure"] = failure
        if agent_jobs._set_failure(job_id, str(exc)):
            agent_jobs._publish_progress(
                job_id,
                event,
                terminal=True,
            )
        else:
            agent_jobs._publish_terminal_state(job_id)
        raise


@celery_app.task(name="quantdinger.tasks.expire_agent_jobs")
def expire_agent_jobs() -> int:
    from app.utils.agent_jobs import expire_billed_jobs

    return expire_billed_jobs()
