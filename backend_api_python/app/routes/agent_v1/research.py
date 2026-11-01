"""Research workspace endpoints: universes, factors, and watchlists."""
from __future__ import annotations

from datetime import datetime
from flask import request

from app.services.factors import (
    FactorError,
    TalibFactorError,
    get_factor_metadata,
    is_talib_available,
    list_factor_catalog,
)
from app.services.market.watchlist import (
    add_watchlist_item,
    list_watchlist,
    remove_watchlist_item,
)
from app.services.universe import UniverseError, UniverseService
from app.services.strategy_v2 import StrategyV2BacktestService
from app.utils.agent_auth import (
    SCOPE_B,
    SCOPE_R,
    SCOPE_W,
    agent_required,
    current_token,
    current_user_id,
    instrument_allowed,
    market_allowed,
    with_idempotency,
)
from app.utils.agent_jobs import _job_receipt, submit_job

from . import agent_v1_bp
from ._helpers import clip_int, envelope, error, get_json_or_400

_universes = UniverseService()
_factor_benchmark = StrategyV2BacktestService(universe_service=_universes)


def _run_factor_benchmark(payload: dict, on_progress=None):
    if on_progress:
        on_progress({"phase": "loading_universe", "percent": 5})
    start_date = datetime.strptime(str(payload["startDate"]), "%Y-%m-%d")
    end_date = datetime.strptime(str(payload["endDate"]), "%Y-%m-%d").replace(
        hour=23, minute=59, second=59
    )
    result = _factor_benchmark.benchmark_universe_factors(
        user_id=int(payload["__user_id"]),
        universe_id=int(payload["universeId"]),
        frequency=str(payload.get("frequency") or "1d"),
        start_date=start_date,
        end_date=end_date,
        factors=list(payload.get("factors") or []),
        groups=int(payload.get("groups") or 5),
        holding_period=int(payload.get("holdingPeriod") or 5),
        commission=float(payload.get("commission") or 0.0005),
        slippage=float(payload.get("slippage") or 0.0005),
        neutralize_industry=bool(payload.get("neutralizeIndustry", False)),
        train_fraction=float(payload.get("trainFraction") or 0.6),
        false_discovery_rate=float(payload.get("falseDiscoveryRate") or 0.1),
        minimum_oos_observations=int(payload.get("minimumOosObservations") or 5),
        minimum_abs_oos_ic=float(payload.get("minimumAbsOosIc") or 0.02),
        minimum_coverage=float(payload.get("minimumCoverage") or 0.5),
    )
    result.pop("factorResults", None)
    if on_progress:
        on_progress({"phase": "validated", "percent": 100})
    return result


@agent_v1_bp.route("/research/universes", methods=["GET"])
@agent_required(SCOPE_R)
def list_research_universes():
    return envelope(_universes.list_universes(current_user_id()))


@agent_v1_bp.route("/research/universes/<int:universe_id>", methods=["GET"])
@agent_required(SCOPE_R)
def get_research_universe(universe_id: int):
    try:
        item = _universes.get_universe(current_user_id(), universe_id)
    except UniverseError as exc:
        return error(exc.status_code, exc.code, http=exc.status_code)
    return envelope(item)


@agent_v1_bp.route("/research/universes/<int:universe_id>/members", methods=["GET"])
@agent_required(SCOPE_R)
def list_research_universe_members(universe_id: int):
    try:
        rows = _universes.resolve_members(
            current_user_id(),
            universe_id,
            as_of=request.args.get("as_of"),
        )
    except UniverseError as exc:
        return error(exc.status_code, exc.code, http=exc.status_code)
    limit = clip_int(request.args.get("limit"), default=200, lo=1, hi=500)
    cursor = clip_int(request.args.get("cursor"), default=0, lo=0, hi=1_000_000)
    items = rows[cursor:cursor + limit]
    next_cursor = cursor + len(items) if cursor + len(items) < len(rows) else None
    return envelope({"items": items, "next_cursor": next_cursor, "total": len(rows)})


@agent_v1_bp.route("/research/factors", methods=["GET"])
@agent_required(SCOPE_R)
def list_research_factors():
    rows = list_factor_catalog(
        category=(request.args.get("category") or "").strip(),
        factor_type=(request.args.get("factor_type") or "").strip(),
        include_feature_families=True,
    )
    return envelope({"items": rows, "talib_available": bool(is_talib_available())})


@agent_v1_bp.route("/research/factors/<factor_id>", methods=["GET"])
@agent_required(SCOPE_R)
def get_research_factor(factor_id: str):
    try:
        return envelope(get_factor_metadata(factor_id))
    except (FactorError, TalibFactorError) as exc:
        return error(404, exc.code, http=404)


@agent_v1_bp.route("/research/factor-benchmarks", methods=["POST"])
@agent_required(SCOPE_B)
def create_factor_benchmark():
    body, err = get_json_or_400()
    if err:
        return err
    allowed_fields = {
        "universeId", "frequency", "startDate", "endDate", "factors",
        "groups", "holdingPeriod", "commission", "slippage",
        "neutralizeIndustry", "trainFraction", "falseDiscoveryRate",
        "minimumOosObservations", "minimumAbsOosIc", "minimumCoverage",
    }
    unsupported = sorted(set(body) - allowed_fields)
    if unsupported:
        return error(400, f"Unsupported factor benchmark fields: {', '.join(unsupported)}")
    try:
        universe_id = int(body.get("universeId") or 0)
        datetime.strptime(str(body.get("startDate") or ""), "%Y-%m-%d")
        datetime.strptime(str(body.get("endDate") or ""), "%Y-%m-%d")
    except (TypeError, ValueError):
        return error(400, "universeId and YYYY-MM-DD startDate/endDate are required")
    factors = body.get("factors")
    if not isinstance(factors, list) or len(factors) < 2:
        return error(400, "factors must contain at least two factor definitions")
    try:
        universe = _universes.get_universe(current_user_id(), universe_id)
        members = _universes.candidate_members(
            current_user_id(),
            universe_id,
            start=datetime.strptime(str(body["startDate"]), "%Y-%m-%d").date(),
            end=datetime.strptime(str(body["endDate"]), "%Y-%m-%d").date(),
        )
    except UniverseError as exc:
        return error(exc.status_code, exc.code, http=exc.status_code)
    market = str(universe.get("market") or "")
    if market and not market_allowed(market):
        return error(403, f"Market not allowed: {market}", http=403)
    for member in members:
        member_market = str(member.get("market") or market)
        symbol = str(member.get("symbol") or "")
        if member_market and not market_allowed(member_market):
            return error(403, f"Market not allowed: {member_market}", http=403)
        if symbol and not instrument_allowed(symbol):
            return error(403, f"Instrument not allowed: {symbol}", http=403)

    with with_idempotency("factor_benchmark") as existing:
        if existing:
            return envelope(
                _job_receipt(existing, duplicate=True),
                message="idempotent replay",
            )
    payload = dict(body)
    payload["__user_id"] = current_user_id()
    job = submit_job(
        user_id=current_user_id(),
        agent_token_id=int(current_token().get("id") or 0),
        kind="factor_benchmark",
        request_payload=payload,
        runner=_run_factor_benchmark,
        idempotency_key=request.headers.get("Idempotency-Key"),
    )
    if job["status"] == "failed":
        return error(503, "AGENT_JOB_DISPATCH_FAILED", details=job, http=503)
    return envelope(job, message="queued", status=202)


@agent_v1_bp.route("/research/watchlist", methods=["GET"])
@agent_required(SCOPE_R)
def get_research_watchlist():
    rows = list_watchlist(current_user_id())
    limit = clip_int(request.args.get("limit"), default=100, lo=1, hi=500)
    cursor = clip_int(request.args.get("cursor"), default=0, lo=0, hi=1_000_000)
    items = rows[cursor:cursor + limit]
    next_cursor = cursor + len(items) if cursor + len(items) < len(rows) else None
    return envelope({"items": items, "next_cursor": next_cursor, "total": len(rows)})


@agent_v1_bp.route("/research/watchlist", methods=["POST"])
@agent_required(SCOPE_W)
def add_research_watchlist():
    body, err = get_json_or_400()
    if err:
        return err
    ok, message = add_watchlist_item(
        current_user_id(),
        str(body.get("market") or ""),
        str(body.get("symbol") or ""),
        str(body.get("name") or ""),
        exchange_id=str(body.get("exchange_id") or ""),
        market_type=str(body.get("market_type") or ""),
        instrument_id=str(body.get("instrument_id") or ""),
        settle_currency=str(body.get("settle_currency") or ""),
    )
    if not ok:
        return error(400, message)
    return envelope({"market": body.get("market"), "symbol": body.get("symbol")}, message="added")


@agent_v1_bp.route("/research/watchlist", methods=["DELETE"])
@agent_required(SCOPE_W)
def delete_research_watchlist():
    body, err = get_json_or_400()
    if err:
        return err
    market = str(body.get("market") or "").strip()
    symbol = str(body.get("symbol") or "").strip()
    if not market or not symbol:
        return error(400, "market and symbol are required")
    remove_watchlist_item(current_user_id(), market, symbol)
    return envelope({"market": market, "symbol": symbol}, message="removed")
