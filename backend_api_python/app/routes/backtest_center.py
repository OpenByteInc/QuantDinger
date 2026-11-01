"""Strategy backtest API."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime
from typing import Any
from uuid import uuid4

from flask import g, jsonify, request

from app.openapi.blueprint import HumanBlueprint as Blueprint
from app.routes.strategy_services import get_strategy_service
from app.services.backtest_execution import (
    default_commission_if_missing,
    default_slippage_if_missing,
    parse_rate,
)
from app.services.backtest_limits import BacktestRangeLimitError
from app.services.billing_service import BillingError, get_billing_service
from app.services.backtest_jobs import run_backtest_job
from app.services.script_source import get_script_source_service
from app.services.strategy_v2 import (
    FactorResearchRepository,
    StrategyBacktestRepository,
    StrategyV2BacktestService,
)
from app.services.strategy_v2.factor_benchmark import FactorBenchmarkEngine
from app.utils.auth import login_required
from app.utils.logger import get_logger
from app.utils import agent_jobs


logger = get_logger(__name__)
backtest_center_blp = Blueprint("backtest_center", __name__)
_service: StrategyV2BacktestService | None = None
_repository: StrategyBacktestRepository | None = None
_factor_repository: FactorResearchRepository | None = None

_PUBLIC_REQUEST_ERROR_KEYS = frozenset({
    "factor.outputNotFound",
    "strategyV2.codeRequired",
    "strategyV2.dateRangeRequired",
    "strategyV2.factorBenchmarkFactorsRequired",
    "strategyV2.factorBenchmarkIdRequired",
    "strategyV2.factorResearchInsufficientObservations",
    "strategyV2.factorResearchNoData",
    "strategyV2.factorResearchUniverseRequired",
    "strategyV2.runIdRequired",
    "strategyV2.sourceContractRequired",
    "strategyV2.sourceNotFound",
    "strategyV2.strategyNotFound",
})


def _public_request_error(exc: BaseException) -> str:
    value = exc.args[0] if exc.args else None
    if isinstance(value, str) and value in _PUBLIC_REQUEST_ERROR_KEYS:
        return value
    return "strategyV2.generationInvalid"


def get_strategy_backtest_service() -> StrategyV2BacktestService:
    global _service
    if _service is None:
        _service = StrategyV2BacktestService()
    return _service


def get_strategy_backtest_repository() -> StrategyBacktestRepository:
    global _repository
    if _repository is None:
        _repository = StrategyBacktestRepository()
    return _repository


def get_factor_research_repository() -> FactorResearchRepository:
    global _factor_repository
    if _factor_repository is None:
        _factor_repository = FactorResearchRepository()
    return _factor_repository


def _source(payload: dict[str, Any], user_id: int) -> tuple[str, int | None, int | None, str]:
    code = str(payload.get("code") or "").strip()
    source_id = _positive_int(payload.get("sourceId"))
    strategy_id = _positive_int(payload.get("strategyId"))
    strategy_name = str(payload.get("strategyName") or "").strip()
    if strategy_id:
        strategy = get_strategy_service().get_strategy(strategy_id, user_id=user_id)
        if not strategy:
            raise ValueError("strategyV2.strategyNotFound")
        strategy_name = strategy_name or str(strategy.get("strategy_name") or "")
        config = strategy.get("trading_config") or {}
        source_id = source_id or _positive_int(config.get("script_source_id"))
    if source_id and not code:
        source = get_script_source_service().get_source(source_id, user_id=user_id)
        if not source:
            raise ValueError("strategyV2.sourceNotFound")
        code = str(source.get("code") or "").strip()
        strategy_name = strategy_name or str(source.get("name") or "")
    if not code:
        raise ValueError("strategyV2.codeRequired")
    return code, source_id, strategy_id, strategy_name


def _prepare_run(payload: dict[str, Any], user_id: int) -> dict[str, Any]:
    code, source_id, strategy_id, strategy_name = _source(payload, user_id)
    start_raw = str(payload.get("startDate") or "").strip()
    end_raw = str(payload.get("endDate") or "").strip()
    if not start_raw or not end_raw:
        raise ValueError("strategyV2.dateRangeRequired")
    start_date = datetime.strptime(start_raw, "%Y-%m-%d")
    end_date = datetime.strptime(end_raw, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
    leverage_enabled = bool(payload.get("leverageEnabled", False))
    return {
        "user_id": user_id,
        "code": code,
        "start_date": start_date,
        "end_date": end_date,
        "initial_capital": float(payload.get("initialCapital") or 10_000),
        "leverage_enabled": leverage_enabled,
        "leverage": float(payload.get("leverage") or 1),
        "commission": parse_rate(payload.get("commission"), default=default_commission_if_missing(None)),
        "slippage": parse_rate(payload.get("slippage"), default=default_slippage_if_missing(None)),
        "params": dict(payload.get("params") or {}),
        "strategy_id": strategy_id,
        "source_id": source_id,
        "strategy_name": strategy_name,
        "instrument_rules_snapshot_id": str(
            payload.get("instrumentRulesSnapshotId") or ""
        ).strip(),
    }


def _run_prepared(prepared: dict[str, Any], *, persist: bool) -> tuple[int | None, dict[str, Any]]:
    return get_strategy_backtest_service().run(**prepared, persist=persist)


def _run(payload: dict[str, Any], user_id: int, *, persist: bool) -> tuple[int | None, dict[str, Any]]:
    return _run_prepared(_prepare_run(payload, user_id), persist=persist)


def _consume_backtest_credits(user_id: int) -> tuple[Any, dict[str, Any]]:
    """Charge one backtest run and return a response-safe billing snapshot."""
    billing = get_billing_service()
    enabled = bool(billing.is_billing_enabled())
    cost = max(0, int(billing.get_feature_cost("backtest") or 0))
    reference_id = f"backtest:{uuid4().hex}"
    charge = {
        "enabled": enabled,
        "cost": cost,
        "charged": 0,
        "remaining": float(billing.get_user_credits(user_id)),
        "referenceId": reference_id,
    }
    if not enabled or cost <= 0:
        return billing, charge

    success, message = billing.check_and_consume(
        user_id=user_id,
        feature="backtest",
        reference_id=reference_id,
    )
    if not success:
        current = float(billing.get_user_credits(user_id))
        if str(message).startswith("insufficient_credits:"):
            return billing, {
                **charge,
                "error": "insufficient_credits",
                "current": current,
                "required": cost,
                "shortage": max(0, cost - current),
            }
        return billing, {**charge, "error": "billing_error", "message": str(message)}

    charge["charged"] = cost
    charge["remaining"] = float(billing.get_user_credits(user_id))
    return billing, charge


def _refund_backtest_credits(billing: Any, user_id: int, charge: dict[str, Any]) -> None:
    cost = int(charge.get("charged") or 0)
    if not billing or cost <= 0:
        return
    refunded, message = billing.add_credits(
        user_id=user_id,
        amount=cost,
        action="refund",
        remark="Automatic refund: backtest execution failed",
        reference_id=str(charge.get("referenceId") or ""),
    )
    if not refunded:
        logger.error("Backtest credit refund failed for user %s: %s", user_id, message)


def _job_payload(prepared: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "code": prepared["code"],
        "startDate": prepared["start_date"].strftime("%Y-%m-%d"),
        "endDate": prepared["end_date"].strftime("%Y-%m-%d"),
        "initialCapital": prepared["initial_capital"],
        "commission": prepared["commission"],
        "slippage": prepared["slippage"],
        "leverageEnabled": prepared["leverage_enabled"],
        "leverage": prepared["leverage"],
        "params": prepared["params"],
        "instrumentRulesSnapshotId": prepared["instrument_rules_snapshot_id"],
        "persist": bool(payload.get("persist", True)),
        "sourceId": prepared["source_id"],
        "strategyId": prepared["strategy_id"],
        "strategyName": prepared["strategy_name"],
        "__user_id": prepared["user_id"],
        "__source_id": prepared["source_id"],
        "__strategy_id": prepared["strategy_id"],
        "__strategy_name": prepared["strategy_name"],
    }


def _public_backtest_job(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    result = row.get("result")
    progress = row.get("progress")
    if isinstance(result, str):
        result = json.loads(result)
    if isinstance(progress, str):
        progress = json.loads(progress)
    request_payload = row.get("request") or {}
    if isinstance(request_payload, str):
        request_payload = json.loads(request_payload)
    billing = row.get("billing")
    if billing is None and isinstance(request_payload, dict):
        billing = request_payload.get("__billing")
    return {
        "jobId": row.get("job_id"),
        "status": row.get("status"),
        "progress": progress or {},
        "result": result,
        "error": row.get("error"),
        "billing": billing,
        "createdAt": row.get("created_at"),
        "startedAt": row.get("started_at"),
        "finishedAt": row.get("finished_at"),
    }


@backtest_center_blp.route("/jobs", methods=["POST"])
@login_required
def create_strategy_backtest_job():
    try:
        payload = request.get_json(silent=True) or {}
        prepared = _prepare_run(payload, int(g.user_id))
        receipt = agent_jobs.submit_job(
            user_id=int(g.user_id),
            agent_token_id=None,
            kind="backtest",
            request_payload=_job_payload(prepared, payload),
            runner=run_backtest_job,
        )
        return jsonify({
            "code": 1,
            "msg": "common.success",
            "data": _public_backtest_job(receipt),
        }), 202
    except BillingError as exc:
        return jsonify({
            "code": 0,
            "msg": exc.code,
            "data": {"error_type": exc.code, **exc.details},
        }), exc.status
    except BacktestRangeLimitError as exc:
        return jsonify({
            "code": 0,
            "msg": "strategyV2.backtestRangeLimit",
            "data": exc.details,
        }), 400
    except ValueError as exc:
        return jsonify({"code": 0, "msg": _public_request_error(exc), "data": None}), 400
    except Exception:
        logger.exception("Strategy backtest job submission failed")
        return jsonify({"code": 0, "msg": "strategyV2.backtest.runFailed", "data": None}), 500


@backtest_center_blp.route("/jobs/<job_id>", methods=["GET"])
@login_required
def get_strategy_backtest_job(job_id: str):
    row = agent_jobs.get_job(job_id, user_id=int(g.user_id))
    if not row or row.get("kind") != "backtest":
        return jsonify({"code": 0, "msg": "strategyV2.backtest.jobNotFound", "data": None}), 404
    return jsonify({"code": 1, "msg": "common.success", "data": _public_backtest_job(row)})


@backtest_center_blp.route("/jobs/<job_id>/cancel", methods=["POST"])
@login_required
def cancel_strategy_backtest_job(job_id: str):
    row = agent_jobs.get_job(job_id, user_id=int(g.user_id))
    if not row or row.get("kind") != "backtest":
        return jsonify({"code": 0, "msg": "strategyV2.backtest.jobNotFound", "data": None}), 404
    cancelled = agent_jobs.cancel_job(job_id, user_id=int(g.user_id))
    return jsonify({
        "code": 1,
        "msg": "common.success",
        "data": _public_backtest_job(cancelled or row),
    })


@backtest_center_blp.route("/run", methods=["POST"])
@login_required
def run_strategy_backtest():
    billing = None
    charge: dict[str, Any] = {}
    user_id = int(g.user_id)
    try:
        payload = request.get_json(silent=True) or {}
        prepared = _prepare_run(payload, user_id)
        billing, charge = _consume_backtest_credits(user_id)
        if charge.get("error") == "insufficient_credits":
            return jsonify({
                "code": 0,
                "msg": "insufficient_credits",
                "data": {
                    "error_type": "INSUFFICIENT_CREDITS",
                    "feature": "backtest",
                    "current": charge["current"],
                    "required": charge["required"],
                    "shortage": charge["shortage"],
                },
            }), 402
        if charge.get("error"):
            return jsonify({
                "code": 0,
                "msg": charge.get("message") or "Failed to deduct credits",
                "data": {"error_type": "BILLING_ERROR", "feature": "backtest"},
            }), 500

        run_id, result = _run_prepared(prepared, persist=bool(payload.get("persist", True)))
        billing_data = {
            key: charge.get(key)
            for key in ("enabled", "cost", "charged", "remaining")
        }
        return jsonify({
            "code": 1,
            "msg": "success",
            "data": {**result, "runId": run_id, "billing": billing_data},
        })
    except BacktestRangeLimitError as exc:
        _refund_backtest_credits(billing, user_id, charge)
        return jsonify({
            "code": 0,
            "msg": "strategyV2.backtestRangeLimit",
            "data": exc.details,
        }), 400
    except ValueError as exc:
        _refund_backtest_credits(billing, user_id, charge)
        return jsonify({"code": 0, "msg": _public_request_error(exc), "data": None}), 400
    except Exception:
        _refund_backtest_credits(billing, user_id, charge)
        logger.exception("Strategy backtest failed")
        return jsonify({"code": 0, "msg": "strategyV2.backtest.runFailed", "data": None}), 500


@backtest_center_blp.route("/factor-research", methods=["POST"])
@backtest_center_blp.doc(
    summary="Run factor research with built-in or TA-Lib 0.8.1 factors",
    description=(
        "Accepts factorId and optional factorParams. TA-Lib factors use the "
        "talib: namespace and support explicit output selection for "
        "multi-output functions."
    ),
)
@login_required
def run_factor_research():
    try:
        payload = request.get_json(silent=True) or {}
        start_raw = str(payload.get("startDate") or "").strip()
        end_raw = str(payload.get("endDate") or "").strip()
        if not start_raw or not end_raw:
            raise ValueError("strategyV2.dateRangeRequired")
        factor_id = str(payload.get("factorId") or "momentum_20")
        factor_params = payload.get("factorParams") if isinstance(payload.get("factorParams"), dict) else {}
        groups = max(2, min(10, int(payload.get("groups") or 5)))
        holding_period = max(1, int(payload.get("holdingPeriod") or 5))
        commission = parse_rate(payload.get("commission"), default=default_commission_if_missing(None))
        slippage = parse_rate(payload.get("slippage"), default=default_slippage_if_missing(None))
        neutralize_industry = bool(payload.get("neutralizeIndustry", False))
        universe_id = _positive_int(payload.get("universeId"))
        source_id = None
        source_name = ""
        code = ""
        if universe_id:
            result = get_strategy_backtest_service().research_universe_factor(
                user_id=int(g.user_id),
                universe_id=universe_id,
                frequency=str(payload.get("frequency") or "1d"),
                start_date=datetime.strptime(start_raw, "%Y-%m-%d"),
                end_date=datetime.strptime(end_raw, "%Y-%m-%d").replace(hour=23, minute=59, second=59),
                factor_id=factor_id,
                factor_params=factor_params,
                groups=groups,
                holding_period=holding_period,
                commission=commission,
                slippage=slippage,
                neutralize_industry=neutralize_industry,
            )
        else:
            code, source_id, _strategy_id, source_name = _source(payload, int(g.user_id))
            if not source_id:
                raise ValueError("strategyV2.sourceContractRequired")
            result = get_strategy_backtest_service().research_factor(
                user_id=int(g.user_id),
                code=code,
                start_date=datetime.strptime(start_raw, "%Y-%m-%d"),
                end_date=datetime.strptime(end_raw, "%Y-%m-%d").replace(hour=23, minute=59, second=59),
                factor_id=factor_id,
                factor_params=factor_params,
                groups=groups,
                holding_period=holding_period,
                commission=commission,
                slippage=slippage,
                neutralize_industry=neutralize_industry,
            )
        manifest = dict(result.get("manifest") or {})
        universe = dict(result.get("universe") or {})
        run_id = get_factor_research_repository().persist_run(
            user_id=int(g.user_id),
            source_id=source_id,
            source_name=source_name,
            universe_id=universe_id,
            universe_name=str(universe.get("name") or universe.get("code") or ""),
            research_mode="universe" if universe_id else "strategy",
            market=",".join(manifest.get("markets") or []),
            timeframe=str(
                manifest.get("drivingFrequency")
                or manifest.get("primaryFrequency")
                or ""
            ),
            start_date=start_raw,
            end_date=end_raw,
            factor_id=factor_id,
            groups=int(result.get("effectiveGroups") or groups),
            holding_period=holding_period,
            commission=commission,
            slippage=slippage,
            neutralize_industry=neutralize_industry,
            manifest=manifest,
            result=result,
            code=code,
        )
        return jsonify({"code": 1, "msg": "success", "data": {**result, "runId": run_id}})
    except BacktestRangeLimitError as exc:
        return jsonify({
            "code": 0,
            "msg": "strategyV2.backtestRangeLimit",
            "data": exc.details,
        }), 400
    except ValueError as exc:
        return jsonify({"code": 0, "msg": _public_request_error(exc), "data": None}), 400
    except Exception:
        logger.exception("Factor research failed")
        return jsonify({"code": 0, "msg": "strategyV2.backtest.runFailed", "data": None}), 500


@backtest_center_blp.route("/factor-research/history", methods=["GET"])
@login_required
def list_factor_research_runs():
    try:
        research_type = str(request.args.get("researchType") or "all").strip().lower()
        if research_type not in {"all", "single", "benchmark"}:
            research_type = "all"
        rows = get_factor_research_repository().list_runs(
            user_id=int(g.user_id),
            source_id=_positive_int(request.args.get("sourceId")),
            research_type=research_type,
            limit=max(1, min(200, int(request.args.get("limit") or 50))),
            offset=max(0, int(request.args.get("offset") or 0)),
        )
        return jsonify({"code": 1, "msg": "success", "data": rows})
    except Exception:
        logger.exception("Factor research history query failed")
        return jsonify({"code": 0, "msg": "strategyV2.backtest.runFailed", "data": None}), 500


@backtest_center_blp.route("/factor-benchmark", methods=["POST"])
@backtest_center_blp.doc(
    summary="Benchmark multiple factors with out-of-sample validation",
    description=(
        "Loads a managed universe once, evaluates multiple factors, applies "
        "out-of-sample validation and Benjamini-Hochberg false-discovery control, "
        "then persists every successful factor run."
    ),
)
@login_required
def run_factor_benchmark():
    try:
        payload = request.get_json(silent=True) or {}
        start_raw = str(payload.get("startDate") or "").strip()
        end_raw = str(payload.get("endDate") or "").strip()
        universe_id = _positive_int(payload.get("universeId"))
        if not start_raw or not end_raw:
            raise ValueError("strategyV2.dateRangeRequired")
        if not universe_id:
            raise ValueError("strategyV2.factorResearchUniverseRequired")
        factors = payload.get("factors")
        if not isinstance(factors, list):
            raise ValueError("strategyV2.factorBenchmarkFactorsRequired")
        groups = max(2, min(10, int(payload.get("groups") or 5)))
        holding_period = max(1, int(payload.get("holdingPeriod") or 5))
        commission = parse_rate(
            payload.get("commission"),
            default=default_commission_if_missing(None),
        )
        slippage = parse_rate(
            payload.get("slippage"),
            default=default_slippage_if_missing(None),
        )
        neutralize_industry = bool(payload.get("neutralizeIndustry", False))
        result = get_strategy_backtest_service().benchmark_universe_factors(
            user_id=int(g.user_id),
            universe_id=universe_id,
            frequency=str(payload.get("frequency") or "1d"),
            start_date=datetime.strptime(start_raw, "%Y-%m-%d"),
            end_date=datetime.strptime(end_raw, "%Y-%m-%d").replace(
                hour=23, minute=59, second=59
            ),
            factors=factors,
            groups=groups,
            holding_period=holding_period,
            commission=commission,
            slippage=slippage,
            neutralize_industry=neutralize_industry,
            train_fraction=float(payload.get("trainFraction") or 0.6),
            false_discovery_rate=float(payload.get("falseDiscoveryRate") or 0.1),
            minimum_oos_observations=int(
                payload.get("minimumOosObservations") or 5
            ),
            minimum_abs_oos_ic=float(payload.get("minimumAbsOosIc") or 0.02),
            minimum_coverage=float(payload.get("minimumCoverage") or 0.5),
        )
        benchmark_id = uuid4().hex
        result["benchmarkId"] = benchmark_id
        benchmark_snapshot = deepcopy(result)
        benchmark_snapshot.pop("factorResults", None)
        manifest = dict(result.get("manifest") or {})
        universe = dict(result.get("universe") or {})
        ranking_by_factor = {
            str(item.get("factorId") or ""): item
            for item in result.get("rankings") or []
        }
        run_ids: dict[str, int | None] = {}
        for factor_result in result.get("factorResults") or []:
            factor_id = str(factor_result.get("factorId") or "")
            ranking = ranking_by_factor.get(factor_id, {})
            factor_result.update({
                "benchmarkId": benchmark_id,
                "benchmarkSnapshot": benchmark_snapshot,
                "benchmarkRank": ranking.get("rank"),
                "benchmarkClassification": ranking.get("classification"),
                "benchmarkScore": ranking.get("score"),
                "benchmarkPValue": ranking.get("pValue"),
                "benchmarkQValue": ranking.get("qValue"),
                "manifest": manifest,
                "universeId": universe_id,
                "universe": universe,
                "frequency": result.get("frequency"),
                "symbolsRequested": result.get("symbolsRequested"),
                "symbolsUsed": result.get("symbolsUsed"),
                "symbolsSkipped": result.get("symbolsSkipped"),
            })
            run_id = get_factor_research_repository().persist_run(
                user_id=int(g.user_id),
                source_id=None,
                source_name="",
                universe_id=universe_id,
                universe_name=str(universe.get("name") or universe.get("code") or ""),
                research_mode="benchmark",
                market=",".join(manifest.get("markets") or []),
                timeframe=str(result.get("frequency") or ""),
                start_date=start_raw,
                end_date=end_raw,
                factor_id=factor_id,
                groups=int(factor_result.get("effectiveGroups") or groups),
                holding_period=holding_period,
                commission=commission,
                slippage=slippage,
                neutralize_industry=neutralize_industry,
                manifest=manifest,
                result=factor_result,
                code="",
            )
            run_ids[factor_id] = run_id
        for ranking in result.get("rankings") or []:
            ranking["runId"] = run_ids.get(str(ranking.get("factorId") or ""))
        result.pop("factorResults", None)
        return jsonify({"code": 1, "msg": "success", "data": result})
    except BacktestRangeLimitError as exc:
        return jsonify({
            "code": 0,
            "msg": "strategyV2.backtestRangeLimit",
            "data": exc.details,
        }), 400
    except ValueError as exc:
        return jsonify({"code": 0, "msg": _public_request_error(exc), "data": None}), 400
    except Exception:
        logger.exception("Factor benchmark failed")
        return jsonify({"code": 0, "msg": "strategyV2.backtest.runFailed", "data": None}), 500


def _benchmark_snapshot_from_runs(
    benchmark_id: str,
    runs: list[dict[str, Any]],
) -> dict[str, Any]:
    for run in runs:
        result = dict(run.get("result") or {})
        snapshot = result.get("benchmarkSnapshot")
        if isinstance(snapshot, dict) and snapshot:
            output = deepcopy(snapshot)
            output["benchmarkId"] = benchmark_id
            output["researchMode"] = "benchmark"
            return output

    factor_results = [
        dict(run.get("result") or {})
        for run in runs
        if isinstance(run.get("result"), dict)
    ]
    if not factor_results:
        return {}
    benchmark = FactorBenchmarkEngine().run(factor_results)
    first_run = runs[0]
    first_result = factor_results[0]
    benchmark.update({
        "benchmarkId": benchmark_id,
        "researchMode": "benchmark",
        "manifest": first_run.get("manifest") or first_result.get("manifest") or {},
        "universe": first_result.get("universe") or {
            "id": first_run.get("universe_id"),
            "name": first_run.get("universe_name"),
        },
        "frequency": first_result.get("frequency") or first_run.get("timeframe"),
        "symbolsRequested": first_result.get("symbolsRequested"),
        "symbolsUsed": first_result.get("symbolsUsed") or first_run.get("universe_size"),
        "symbolsSkipped": first_result.get("symbolsSkipped") or [],
        "errors": [],
    })
    benchmark["summary"]["requestedFactorCount"] = len(factor_results)
    return benchmark


def _factor_benchmark_history_items(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for run in runs:
        result = dict(run.get("result") or {})
        benchmark_id = str(result.get("benchmarkId") or "").strip()
        if benchmark_id:
            grouped.setdefault(benchmark_id, []).append(run)

    items: list[dict[str, Any]] = []
    for benchmark_id, group in grouped.items():
        snapshot = _benchmark_snapshot_from_runs(benchmark_id, group)
        rankings = list(snapshot.get("rankings") or [])
        summary = dict(snapshot.get("summary") or {})
        first = group[0]
        validated_count = sum(
            str(row.get("classification") or "") in {"alive", "reversed"}
            for row in rankings
        )
        items.append({
            "id": benchmark_id,
            "benchmark_id": benchmark_id,
            "history_type": "benchmark",
            "universe_id": first.get("universe_id"),
            "universe_name": first.get("universe_name"),
            "market": first.get("market"),
            "timeframe": first.get("timeframe"),
            "start_date": first.get("start_date"),
            "end_date": first.get("end_date"),
            "created_at": first.get("created_at"),
            "factor_count": int(summary.get("requestedFactorCount") or len(group)),
            "completed_count": len(rankings) or len(group),
            "validated_count": validated_count,
            "best_factor_id": summary.get("bestFactorId") or (
                rankings[0].get("factorId") if rankings else None
            ),
            "best_score": rankings[0].get("score") if rankings else None,
            "failed_count": len(snapshot.get("errors") or []),
        })
    return sorted(items, key=lambda item: str(item.get("created_at") or ""), reverse=True)


@backtest_center_blp.route("/factor-benchmark/history", methods=["GET"])
@login_required
def list_factor_benchmark_runs():
    try:
        limit = max(1, min(100, int(request.args.get("limit") or 24)))
        runs = get_factor_research_repository().list_benchmark_runs(
            user_id=int(g.user_id),
            limit=min(5000, limit * 50),
        )
        return jsonify({
            "code": 1,
            "msg": "success",
            "data": _factor_benchmark_history_items(runs)[:limit],
        })
    except Exception:
        logger.exception("Factor benchmark history query failed")
        return jsonify({"code": 0, "msg": "strategyV2.backtest.runFailed", "data": None}), 500


@backtest_center_blp.route("/factor-benchmark/get", methods=["GET"])
@login_required
def get_factor_benchmark_run():
    try:
        benchmark_id = str(request.args.get("benchmarkId") or "").strip()
        if not benchmark_id:
            raise ValueError("strategyV2.factorBenchmarkIdRequired")
        runs = get_factor_research_repository().list_benchmark_runs(
            user_id=int(g.user_id),
            benchmark_id=benchmark_id,
        )
        if not runs:
            return jsonify({
                "code": 0,
                "msg": "strategyV2.factorBenchmarkRunNotFound",
                "data": None,
            }), 404
        first = runs[0]
        return jsonify({
            "code": 1,
            "msg": "success",
            "data": {
                "id": benchmark_id,
                "benchmark_id": benchmark_id,
                "research_mode": "benchmark",
                "universe_id": first.get("universe_id"),
                "universe_name": first.get("universe_name"),
                "market": first.get("market"),
                "timeframe": first.get("timeframe"),
                "start_date": first.get("start_date"),
                "end_date": first.get("end_date"),
                "commission": first.get("commission"),
                "slippage": first.get("slippage"),
                "groups_count": first.get("groups_count"),
                "holding_period": first.get("holding_period"),
                "neutralize_industry": first.get("neutralize_industry"),
                "created_at": first.get("created_at"),
                "result": _benchmark_snapshot_from_runs(benchmark_id, runs),
            },
        })
    except ValueError as exc:
        return jsonify({"code": 0, "msg": _public_request_error(exc), "data": None}), 400
    except Exception:
        logger.exception("Factor benchmark detail query failed")
        return jsonify({"code": 0, "msg": "strategyV2.backtest.runFailed", "data": None}), 500


@backtest_center_blp.route("/factor-research/get", methods=["GET"])
@login_required
def get_factor_research_run():
    try:
        run_id = _positive_int(request.args.get("runId"))
        if not run_id:
            raise ValueError("strategyV2.runIdRequired")
        row = get_factor_research_repository().get_run(
            user_id=int(g.user_id),
            run_id=run_id,
        )
        if not row:
            return jsonify({"code": 0, "msg": "strategyV2.factorResearchRunNotFound", "data": None}), 404
        return jsonify({"code": 1, "msg": "success", "data": row})
    except ValueError as exc:
        return jsonify({"code": 0, "msg": _public_request_error(exc), "data": None}), 400
    except Exception:
        logger.exception("Factor research lookup failed")
        return jsonify({"code": 0, "msg": "strategyV2.backtest.runFailed", "data": None}), 500


@backtest_center_blp.route("/history", methods=["GET"])
@login_required
def list_strategy_backtests():
    try:
        rows = get_strategy_backtest_repository().list_runs(
            user_id=int(g.user_id),
            source_id=_positive_int(request.args.get("sourceId")),
            symbol=str(request.args.get("symbol") or "").strip(),
            market=str(request.args.get("market") or "").strip(),
            timeframe=str(request.args.get("timeframe") or "").strip(),
            limit=max(1, min(200, int(request.args.get("limit") or 50))),
            offset=max(0, int(request.args.get("offset") or 0)),
        )
        return jsonify({"code": 1, "msg": "success", "data": rows})
    except Exception:
        logger.exception("Backtest history query failed")
        return jsonify({"code": 0, "msg": "strategyV2.backtest.runFailed", "data": None}), 500


@backtest_center_blp.route("/get", methods=["GET"])
@login_required
def get_strategy_backtest():
    try:
        run_id = _positive_int(request.args.get("runId"))
        if not run_id:
            raise ValueError("strategyV2.runIdRequired")
        row = get_strategy_backtest_repository().get_run(user_id=int(g.user_id), run_id=run_id)
        if not row:
            return jsonify({"code": 0, "msg": "strategyV2.runNotFound", "data": None}), 404
        return jsonify({"code": 1, "msg": "success", "data": row})
    except ValueError as exc:
        return jsonify({"code": 0, "msg": _public_request_error(exc), "data": None}), 400
    except Exception:
        logger.exception("Backtest lookup failed")
        return jsonify({"code": 0, "msg": "strategyV2.backtest.runFailed", "data": None}), 500


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
        return parsed if parsed > 0 else None
    except (TypeError, ValueError):
        return None
