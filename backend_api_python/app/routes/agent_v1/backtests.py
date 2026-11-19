"""Async V2 backtest endpoints (class B).

Submit returns a job_id; the agent polls /jobs/{id} until done. This endpoint
uses the same V2 execution model as the human Backtest Center.
"""
from __future__ import annotations

import os
from typing import Any

from app.services.backtest_jobs import parse_backtest_date, run_backtest_job
from app.services.billing_service import BillingError
from app.services.strategy_v2 import StrategyV2BacktestService
from app.utils.agent_auth import (
    SCOPE_B, agent_required, current_token, current_user_id,
    instrument_allowed, market_allowed, with_idempotency,
)
from app.utils.agent_jobs import _job_receipt, count_active_jobs, submit_job
from app.utils.logger import get_logger
from flask import request

from . import agent_v1_bp
from ._helpers import envelope, error, get_json_or_400
from ._security import assert_indicator_code_size

logger = get_logger(__name__)
_backtest = StrategyV2BacktestService()
_BACKTEST_FIELDS = {
    "code", "startDate", "endDate", "initialCapital", "commission", "slippage",
    "leverageEnabled", "leverage", "params",
    "instrumentRulesSnapshotId",
}


def _token_has_restricted_allowlist(field: str) -> bool:
    raw = str(current_token().get(field) or "*").strip()
    return raw not in {"", "*"}


def _validate_request(body: dict) -> tuple[Any, Any]:
    unsupported = sorted(set(body) - _BACKTEST_FIELDS)
    if unsupported:
        return None, error(400, f"Unsupported backtest fields: {', '.join(unsupported)}")
    code = str(body.get("code") or "").strip()
    if not code:
        return None, error(400, "code is required")
    try:
        assert_indicator_code_size(code)
    except ValueError as exc:
        return None, error(400, str(exc))

    try:
        start_date = _parse_date(body.get("startDate"))
        end_date = _parse_date(body.get("endDate"))
    except ValueError as exc:
        return None, error(400, str(exc))
    if not start_date or not end_date:
        return None, error(400, "startDate and endDate are required (YYYY-MM-DD)")
    if end_date < start_date:
        return None, error(400, "endDate must not be earlier than startDate")

    params = body.get("params", {})
    if not isinstance(params, dict):
        return None, error(400, "params must be an object")
    try:
        if float(body.get("initialCapital") or 10000) <= 0:
            raise ValueError
    except (TypeError, ValueError):
        return None, error(400, "initialCapital must be a positive number")

    try:
        from app.services.strategy_v2 import inspect_strategy_v2

        program = inspect_strategy_v2(code)
        manifest = program.manifest
        metadata = manifest.metadata()
    except Exception as exc:
        return None, error(400, str(exc), http=400)

    for market in manifest.markets:
        if not market_allowed(market):
            return None, error(403, f"Market not allowed: {market}", http=403)

    instruments: list[dict] = list((metadata.get("universe") or {}).get("instruments") or [])
    for subscription in metadata.get("subscriptions") or []:
        instruments.extend(subscription.get("instruments") or [])
    if isinstance(metadata.get("benchmark"), dict):
        instruments.append(metadata["benchmark"])

    seen: set[tuple[str, str]] = set()
    for instrument in instruments:
        market = str(instrument.get("market") or "")
        symbol = str(instrument.get("symbol") or "")
        key = (market, symbol)
        if key in seen:
            continue
        seen.add(key)
        if market and not market_allowed(market):
            return None, error(403, f"Market not allowed: {market}", http=403)
        if symbol and not instrument_allowed(symbol):
            return None, error(403, f"Instrument not allowed: {symbol}", http=403)

    universe = metadata.get("universe") or {}
    dynamic_references = [str(universe.get("reference") or "").strip()]
    dynamic_references.extend(
        str(item.get("universeReference") or "").strip()
        for item in metadata.get("subscriptions") or []
    )
    if any(dynamic_references) and _token_has_restricted_allowlist("instruments"):
        return None, error(
            403,
            "Dynamic universes require an unrestricted instrument allowlist",
            http=403,
        )

    return program, None


def _parse_date(s: Any) -> Any:
    return parse_backtest_date(s)


def _run_backtest(payload: dict, on_progress=None) -> Any:
    return run_backtest_job(payload, on_progress, service=_backtest)


@agent_v1_bp.route("/backtest/run", methods=["POST"])
@agent_required(SCOPE_B)
def create_backtest():
    """Submit a backtest job. Returns 202 with `job_id` for polling."""
    body, err = get_json_or_400()
    if err:
        return err

    _, validation_error = _validate_request(body)
    if validation_error:
        return validation_error

    with with_idempotency("backtest") as existing:
        if existing:
            return envelope(_job_receipt(existing, duplicate=True), message="idempotent replay")

    token_id = int(current_token().get("id") or 0)
    tenant_cap = max(1, int(os.getenv("AGENT_MAX_CONCURRENT_JOBS_PER_TENANT", "4")))
    token_cap = max(1, int(os.getenv("AGENT_MAX_CONCURRENT_JOBS_PER_TOKEN", "2")))
    if count_active_jobs(user_id=current_user_id()) >= tenant_cap:
        return error(
            429,
            "Tenant concurrent job limit reached",
            details={"limit": tenant_cap},
            retriable=True,
            http=429,
        )
    if count_active_jobs(user_id=current_user_id(), agent_token_id=token_id) >= token_cap:
        return error(
            429,
            "Token concurrent job limit reached",
            details={"limit": token_cap},
            retriable=True,
            http=429,
        )

    payload = dict(body)
    payload["__user_id"] = current_user_id()
    try:
        job = submit_job(
            user_id=current_user_id(),
            agent_token_id=token_id,
            kind="backtest",
            request_payload=payload,
            runner=_run_backtest,
            idempotency_key=request.headers.get("Idempotency-Key"),
        )
    except BillingError as exc:
        return error(exc.status, exc.code, details={"error_type": exc.code, **exc.details},
                     retriable=exc.status >= 500, http=exc.status)
    except Exception:
        logger.exception("Agent backtest submission failed")
        return error(503, "BILLING_OR_JOB_UNAVAILABLE", retriable=True, http=503)
    if job["status"] == "failed":
        return error(503, "AGENT_JOB_DISPATCH_FAILED", details=job, http=503)
    return envelope(job, message="queued", status=202)
