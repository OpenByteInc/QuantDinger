"""Isolated Strategy Evolution API endpoints."""

import json
import os
from datetime import datetime, timezone

from flask import g, jsonify, request

from app.openapi.blueprint import HumanBlueprint as Blueprint
from app.services.backtest_limits import BacktestRangeLimitError
from app.services.strategy_evolution import StrategyEvolutionService
from app.utils.auth import login_required
from app.utils import agent_jobs
from app.utils.logger import get_logger


logger = get_logger(__name__)
strategy_evolution_blp = Blueprint("strategy_evolution", __name__)
_service: StrategyEvolutionService | None = None

_PUBLIC_VALIDATION_MESSAGES = {
    code: code
    for code in (
        "strategyEvolution.dateRangeInvalid",
        "strategyEvolution.endDateRequired",
        "strategyEvolution.methodUnsupported",
        "strategyEvolution.observationRangeTooShort",
        "strategyEvolution.parameterChoicesRequired",
        "strategyEvolution.parameterLimitExceeded",
        "strategyEvolution.parameterNameRequired",
        "strategyEvolution.parameterNotDeclared",
        "strategyEvolution.parameterNotOptimizable",
        "strategyEvolution.parameterRangeInvalid",
        "strategyEvolution.parameterRangeOutsideDeclaration",
        "strategyEvolution.parameterRangeUndeclared",
        "strategyEvolution.parametersRequired",
        "strategyEvolution.parameterStepInvalid",
        "strategyEvolution.parameterTypeUnsupported",
        "strategyEvolution.sourceCodeRequired",
        "strategyEvolution.sourceNotFound",
        "strategyEvolution.sourceRequired",
        "strategyEvolution.startDateRequired",
    )
}


def _public_validation_message(exc: ValueError) -> str:
    key = exc.args[0] if exc.args else None
    return _PUBLIC_VALIDATION_MESSAGES.get(
        key,
        "strategyEvolution.runFailed",
    )


def get_strategy_evolution_service() -> StrategyEvolutionService:
    global _service
    if _service is None:
        _service = StrategyEvolutionService()
    return _service


@strategy_evolution_blp.route("/parameter-space", methods=["GET"])
@login_required
def get_strategy_evolution_parameter_space():
    try:
        source_id = int(request.args.get("sourceId") or 0)
        if source_id <= 0:
            raise ValueError("strategyEvolution.sourceRequired")
        data = get_strategy_evolution_service().parameter_space(
            user_id=int(g.user_id),
            source_id=source_id,
        )
        return jsonify({"code": 1, "msg": "common.success", "data": data})
    except ValueError as exc:
        return jsonify({"code": 0, "msg": _public_validation_message(exc), "data": None}), 400


@strategy_evolution_blp.route("/estimate", methods=["POST"])
@login_required
def estimate_strategy_evolution():
    try:
        payload = request.get_json(silent=True) or {}
        return jsonify({"code": 1, "msg": "common.success", "data": get_strategy_evolution_service().estimate(payload)})
    except ValueError as exc:
        return jsonify({"code": 0, "msg": _public_validation_message(exc), "data": None}), 400


@strategy_evolution_blp.route("/preflight", methods=["POST"])
@login_required
def preflight_strategy_evolution():
    try:
        payload = request.get_json(silent=True) or {}
        data = get_strategy_evolution_service().preflight(
            user_id=int(g.user_id),
            payload=payload,
        )
        return jsonify({"code": 1, "msg": "common.success", "data": data})
    except BacktestRangeLimitError as exc:
        return jsonify({"code": 0, "msg": "strategyV2.backtestRangeLimit", "data": exc.details}), 400
    except ValueError as exc:
        return jsonify({"code": 0, "msg": _public_validation_message(exc), "data": None}), 400
    except Exception:
        logger.exception("Strategy evolution preflight failed")
        return jsonify({"code": 0, "msg": "strategyEvolution.preflightFailed", "data": None}), 500


@strategy_evolution_blp.route("/run", methods=["POST"])
@login_required
def run_strategy_evolution():
    try:
        payload = request.get_json(silent=True) or {}
        job_payload = {**payload, "__userId": int(g.user_id)}
        receipt = agent_jobs.submit_job(
            user_id=int(g.user_id),
            agent_token_id=None,
            kind="strategy_evolution",
            request_payload=job_payload,
            runner=_run_evolution_job,
        )
        return jsonify({"code": 1, "msg": "common.success", "data": _public_job(receipt)}), 202
    except BacktestRangeLimitError as exc:
        return jsonify({"code": 0, "msg": "strategyV2.backtestRangeLimit", "data": exc.details}), 400
    except ValueError as exc:
        return jsonify({"code": 0, "msg": _public_validation_message(exc), "data": None}), 400
    except Exception:
        logger.exception("Strategy evolution failed")
        return jsonify({"code": 0, "msg": "strategyEvolution.runFailed", "data": None}), 500


@strategy_evolution_blp.route("/jobs/<job_id>", methods=["GET"])
@login_required
def get_strategy_evolution_job(job_id: str):
    row = agent_jobs.get_job(job_id, user_id=int(g.user_id))
    if not row or row.get("kind") != "strategy_evolution":
        return jsonify({"code": 0, "msg": "strategyEvolution.jobNotFound", "data": None}), 404
    return jsonify({"code": 1, "msg": "common.success", "data": _public_job(row)})


@strategy_evolution_blp.route("/jobs", methods=["GET"])
@login_required
def list_strategy_evolution_jobs():
    limit = max(1, min(int(request.args.get("limit") or 20), 50))
    raw_source_id = request.args.get("sourceId")
    try:
        source_id = int(raw_source_id) if raw_source_id not in {None, ""} else None
    except (TypeError, ValueError):
        return jsonify({"code": 0, "msg": "strategyEvolution.sourceRequired", "data": None}), 400
    summaries = agent_jobs.list_jobs(
        user_id=int(g.user_id),
        kind="strategy_evolution",
        request_source_id=source_id,
        limit=limit,
    )
    items = []
    for summary in summaries:
        if _is_stale_running_job(summary):
            agent_jobs._set_failure(str(summary.get("job_id") or ""), "strategyEvolution.workerInterrupted")
        row = agent_jobs.get_job(str(summary.get("job_id") or ""), user_id=int(g.user_id))
        if row and row.get("kind") == "strategy_evolution":
            items.append(_public_job(row, include_result=False))
    return jsonify({"code": 1, "msg": "common.success", "data": {"items": items}})


def _is_stale_running_job(row) -> bool:
    if row.get("status") != "running" or not row.get("started_at"):
        return False
    started_at = row["started_at"]
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    limit_seconds = max(120, int(os.getenv("CELERY_TASK_TIME_LIMIT", "3600"))) + 300
    return (datetime.now(timezone.utc) - started_at).total_seconds() > limit_seconds


@strategy_evolution_blp.route("/jobs/<job_id>/cancel", methods=["POST"])
@login_required
def cancel_strategy_evolution_job(job_id: str):
    row = agent_jobs.get_job(job_id, user_id=int(g.user_id))
    if not row or row.get("kind") != "strategy_evolution":
        return jsonify({"code": 0, "msg": "strategyEvolution.jobNotFound", "data": None}), 404
    cancelled = agent_jobs.cancel_job(job_id, user_id=int(g.user_id))
    return jsonify({"code": 1, "msg": "common.success", "data": _public_job(cancelled or row)})


def _run_evolution_job(payload, on_progress):
    request_payload = dict(payload or {})
    user_id = int(request_payload.pop("__userId"))
    return get_strategy_evolution_service().run(
        user_id=user_id,
        payload=request_payload,
        on_progress=on_progress,
    )


def _public_job(row, *, include_result: bool = True):
    if not row:
        return None
    result = row.get("result")
    progress = row.get("progress")
    request_payload = row.get("request")
    if isinstance(result, str):
        result = json.loads(result)
    if isinstance(progress, str):
        progress = json.loads(progress)
    if isinstance(request_payload, str):
        request_payload = json.loads(request_payload)
    if isinstance(request_payload, dict):
        request_payload = {
            key: value
            for key, value in request_payload.items()
            if key in {"sourceId", "startDate", "endDate", "initialCapital", "commission", "slippage", "parameterSpace", "config"}
        }
    else:
        request_payload = None
    return {
        "jobId": row.get("job_id"),
        "status": row.get("status"),
        "progress": progress or {},
        "result": result if include_result else None,
        "hasResult": bool(result),
        "request": request_payload,
        "error": row.get("error"),
        "createdAt": row.get("created_at"),
        "startedAt": row.get("started_at"),
        "finishedAt": row.get("finished_at"),
    }
