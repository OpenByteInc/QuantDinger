"""Experiment / regime / structured tuning endpoints (class B)."""
from __future__ import annotations

import time

from app.services.billing_service import get_billing_service
from app.services.experiment.runner import ExperimentRunnerService
from app.utils.agent_auth import (
    SCOPE_B, agent_required, current_token, current_user_id, with_idempotency,
)
from app.utils.agent_jobs import submit_job
from app.utils.logger import get_logger

from . import agent_v1_bp
from ._helpers import envelope, error, get_json_or_400

logger = get_logger(__name__)
_runner = ExperimentRunnerService()


@agent_v1_bp.route("/experiments/regime/detect", methods=["POST"])
@agent_required(SCOPE_B)
def regime_detect():
    """Synchronous market-regime detection (cheap; no async needed)."""
    body, err = get_json_or_400()
    if err:
        return err
    try:
        data = _runner.detect_regime(body)
        return envelope(data)
    except Exception as exc:
        logger.error(f"agent_v1 regime/detect failed: {exc}", exc_info=True)
        return error(400, str(exc))


@agent_v1_bp.route("/experiments/pipeline", methods=["POST"])
@agent_required(SCOPE_B)
def submit_pipeline():
    """Async legacy grid pipeline. Returns 202 + job_id."""
    body, err = get_json_or_400()
    if err:
        return err

    with with_idempotency("experiment_pipeline") as existing:
        if existing:
            return envelope({
                "job_id": existing["job_id"],
                "status": existing["status"],
                "duplicate": True,
            }, message="idempotent replay")

    payload = dict(body)
    payload["__user_id"] = current_user_id()

    def _run(p):
        return _runner.run_pipeline(user_id=int(p.pop("__user_id", 1)), payload=p)

    job = submit_job(
        user_id=current_user_id(),
        agent_token_id=int(current_token().get("id")),
        kind="experiment_pipeline",
        request_payload=payload,
        runner=_run,
    )
    return envelope(job, message="queued", status=202)


@agent_v1_bp.route("/experiments/structured-tune", methods=["POST"])
@agent_required(SCOPE_B)
def submit_structured_tune():
    """Async structured (grid/random) tune. Returns 202 + job_id."""
    body, err = get_json_or_400()
    if err:
        return err

    user_id = current_user_id()
    ok, billing_msg = get_billing_service().check_and_consume(
        user_id=user_id,
        feature="ai_tuning",
        reference_id=f"agent_ai_tuning_{user_id}_{int(time.time())}",
    )
    if not ok:
        return error(402, f"Insufficient credits: {billing_msg}", http=402)

    payload = dict(body)
    payload["__user_id"] = user_id

    def _run(p):
        return _runner.run_structured_tune(user_id=int(p.pop("__user_id", 1)), payload=p)

    job = submit_job(
        user_id=user_id,
        agent_token_id=int(current_token().get("id")),
        kind="structured_tune",
        request_payload=payload,
        runner=_run,
    )
    return envelope(job, message="queued", status=202)


@agent_v1_bp.route("/experiments/ai-optimize", methods=["POST"])
@agent_required(SCOPE_B)
def submit_ai_optimize():
    """Async LLM-driven multi-round optimization (consumes provider quota).

    Real-time progress is published via SSE on `/jobs/{job_id}/stream`
    (the runner already supports `on_progress`).  Polling `/jobs/{job_id}`
    for the latest snapshot still works for clients that prefer it.
    """
    body, err = get_json_or_400()
    if err:
        return err

    payload = dict(body)
    payload["__user_id"] = current_user_id()

    def _run(p, on_progress):
        # Forward the streaming callback into the runner so each LLM round
        # surfaces incrementally to SSE clients.
        return _runner.run_ai_pipeline(
            user_id=int(p.pop("__user_id", 1)),
            payload=p,
            on_progress=lambda data: on_progress(data if isinstance(data, dict) else {"value": data}),
        )

    job = submit_job(
        user_id=current_user_id(),
        agent_token_id=int(current_token().get("id")),
        kind="ai_optimize",
        request_payload=payload,
        runner=_run,
    )
    return envelope(job, message="queued", status=202)
