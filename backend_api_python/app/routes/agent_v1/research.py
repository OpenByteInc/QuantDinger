"""Research workspace endpoints: universes, factors, and watchlists."""
from __future__ import annotations

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
from app.utils.agent_auth import SCOPE_R, SCOPE_W, agent_required, current_user_id

from . import agent_v1_bp
from ._helpers import clip_int, envelope, error, get_json_or_400

_universes = UniverseService()


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
    )
    return envelope({"items": rows, "talib_available": bool(is_talib_available())})


@agent_v1_bp.route("/research/factors/<factor_id>", methods=["GET"])
@agent_required(SCOPE_R)
def get_research_factor(factor_id: str):
    try:
        return envelope(get_factor_metadata(factor_id))
    except (FactorError, TalibFactorError) as exc:
        return error(404, exc.code, http=404)


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
