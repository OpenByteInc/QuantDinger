"""Strategy backtest API."""

from __future__ import annotations

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
from app.services.billing_service import get_billing_service
from app.services.script_source import get_script_source_service
from app.services.strategy_v2 import (
    FactorResearchRepository,
    StrategyBacktestRepository,
    StrategyV2BacktestService,
)
from app.utils.auth import login_required
from app.utils.logger import get_logger


logger = get_logger(__name__)
backtest_center_blp = Blueprint("backtest_center", __name__)
_service: StrategyV2BacktestService | None = None
_repository: StrategyBacktestRepository | None = None
_factor_repository: FactorResearchRepository | None = None


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
        return jsonify({"code": 0, "msg": str(exc), "data": exc.details}), 400
    except ValueError as exc:
        _refund_backtest_credits(billing, user_id, charge)
        return jsonify({"code": 0, "msg": str(exc), "data": None}), 400
    except Exception as exc:
        _refund_backtest_credits(billing, user_id, charge)
        logger.exception("Strategy backtest failed")
        return jsonify({"code": 0, "msg": str(exc), "data": None}), 500


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
        code, source_id, _strategy_id, source_name = _source(payload, int(g.user_id))
        if not source_id:
            raise ValueError("strategyV2.sourceContractRequired")
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
        run_id = get_factor_research_repository().persist_run(
            user_id=int(g.user_id),
            source_id=source_id,
            source_name=source_name,
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
        return jsonify({"code": 0, "msg": str(exc), "data": exc.details}), 400
    except ValueError as exc:
        return jsonify({"code": 0, "msg": str(exc), "data": None}), 400
    except Exception as exc:
        logger.exception("Factor research failed")
        return jsonify({"code": 0, "msg": str(exc), "data": None}), 500


@backtest_center_blp.route("/factor-research/history", methods=["GET"])
@login_required
def list_factor_research_runs():
    try:
        rows = get_factor_research_repository().list_runs(
            user_id=int(g.user_id),
            source_id=_positive_int(request.args.get("sourceId")),
            limit=max(1, min(200, int(request.args.get("limit") or 50))),
            offset=max(0, int(request.args.get("offset") or 0)),
        )
        return jsonify({"code": 1, "msg": "success", "data": rows})
    except Exception as exc:
        logger.exception("Factor research history query failed")
        return jsonify({"code": 0, "msg": str(exc), "data": None}), 500


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
        return jsonify({"code": 0, "msg": str(exc), "data": None}), 400
    except Exception as exc:
        logger.exception("Factor research lookup failed")
        return jsonify({"code": 0, "msg": str(exc), "data": None}), 500


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
    except Exception as exc:
        logger.exception("Backtest history query failed")
        return jsonify({"code": 0, "msg": str(exc), "data": None}), 500


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
        return jsonify({"code": 0, "msg": str(exc), "data": None}), 400
    except Exception as exc:
        logger.exception("Backtest lookup failed")
        return jsonify({"code": 0, "msg": str(exc), "data": None}), 500


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
        return parsed if parsed > 0 else None
    except (TypeError, ValueError):
        return None
