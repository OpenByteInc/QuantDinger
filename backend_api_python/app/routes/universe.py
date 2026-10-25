"""Strategy universe APIs."""

from datetime import date

from flask import g, jsonify, request

from app.openapi.blueprint import HumanBlueprint as Blueprint
from app.services.universe import UniverseError, get_universe_service
from app.utils.auth import admin_required, login_required
from app.utils.logger import get_logger


logger = get_logger(__name__)
universe_blp = Blueprint("universe", __name__)


def _success(data=None, *, status: int = 200):
    return jsonify({"code": 1, "msg": "success", "data": data}), status


def _failure(exc: UniverseError):
    return jsonify({"code": 0, "msg": exc.code, "data": None}), exc.status_code


@universe_blp.route("", methods=["GET"])
@login_required
def list_universes():
    try:
        return _success(get_universe_service().list_universes(g.user_id))
    except Exception:
        logger.exception("list universes failed")
        return jsonify({"code": 0, "msg": "universe.listFailed", "data": None}), 500


@universe_blp.route("", methods=["POST"])
@login_required
def create_universe():
    try:
        payload = request.get_json(silent=True) or {}
        universe_type = str(payload.get("universe_type") or payload.get("universeType") or "manual")
        if universe_type != "manual":
            raise UniverseError("universe.createTypeUnsupported")
        return _success(get_universe_service().create_manual(g.user_id, payload), status=201)
    except UniverseError as exc:
        return _failure(exc)
    except Exception:
        logger.exception("create universe failed")
        return jsonify({"code": 0, "msg": "universe.createFailed", "data": None}), 500


@universe_blp.route("/<int:universe_id>/clone", methods=["POST"])
@login_required
def clone_universe(universe_id: int):
    try:
        payload = request.get_json(silent=True) or {}
        result = get_universe_service().clone_system(
            g.user_id,
            universe_id,
            name=str(payload.get("name") or ""),
        )
        return _success(result, status=201)
    except UniverseError as exc:
        return _failure(exc)
    except Exception:
        logger.exception("clone universe failed")
        return jsonify({"code": 0, "msg": "universe.cloneFailed", "data": None}), 500


@universe_blp.route("/admin/overview", methods=["GET"])
@login_required
@admin_required
def system_universe_overview():
    try:
        return _success(get_universe_service().system_overview())
    except Exception:
        logger.exception("system universe overview failed")
        return jsonify({"code": 0, "msg": "universe.overviewFailed", "data": None}), 500


@universe_blp.route("/admin/system", methods=["POST"])
@login_required
@admin_required
def create_system_universe():
    try:
        return _success(
            get_universe_service().create_system(request.get_json(silent=True) or {}),
            status=201,
        )
    except UniverseError as exc:
        return _failure(exc)
    except Exception:
        logger.exception("create system universe failed")
        return jsonify({"code": 0, "msg": "universe.createFailed", "data": None}), 500


@universe_blp.route("/admin/system/<int:universe_id>", methods=["PUT"])
@login_required
@admin_required
def update_system_universe(universe_id: int):
    try:
        return _success(
            get_universe_service().update_system(
                universe_id,
                request.get_json(silent=True) or {},
            )
        )
    except UniverseError as exc:
        return _failure(exc)
    except Exception:
        logger.exception("update system universe failed")
        return jsonify({"code": 0, "msg": "universe.updateFailed", "data": None}), 500


@universe_blp.route("/admin/system/<int:universe_id>/visibility", methods=["PUT"])
@login_required
@admin_required
def set_system_universe_visibility(universe_id: int):
    try:
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload.get("visible"), bool):
            raise UniverseError("universe.invalidVisibility")
        return _success(
            get_universe_service().set_system_visibility(universe_id, payload["visible"])
        )
    except UniverseError as exc:
        return _failure(exc)
    except Exception:
        logger.exception("set system universe visibility failed")
        return jsonify({"code": 0, "msg": "universe.updateFailed", "data": None}), 500


@universe_blp.route("/admin/system/<int:universe_id>", methods=["DELETE"])
@login_required
@admin_required
def delete_system_universe(universe_id: int):
    try:
        return _success(get_universe_service().delete_system(universe_id))
    except UniverseError as exc:
        return _failure(exc)
    except Exception:
        logger.exception("delete system universe failed")
        return jsonify({"code": 0, "msg": "universe.deleteFailed", "data": None}), 500


@universe_blp.route("/admin/sync", methods=["POST"])
@login_required
@admin_required
def sync_system_universes():
    try:
        from scripts.backfill_public_universe_history import (
            SUPPORTED_HISTORY_UNIVERSES,
            backfill_universe_history,
        )
        from scripts.refresh_public_universe_snapshots import (
            EXPECTED_MEMBER_COUNTS,
            LOADERS,
            apply_snapshot,
            backfill_current_snapshot_as_fixed,
            load_universe_members,
        )

        payload = request.get_json(silent=True) or {}
        requested = payload.get("codes") or list(LOADERS)
        codes = [str(code).strip() for code in requested if str(code).strip()]
        if not codes or any(code not in LOADERS for code in codes):
            raise UniverseError("universe.invalidSyncSelection")
        today = date.today()
        history_from_value = payload.get("history_from") or payload.get("historyFrom")
        try:
            as_of = date.fromisoformat(str(payload.get("as_of") or payload.get("asOf") or today.isoformat()))
            history_from = date.fromisoformat(str(history_from_value)) if history_from_value else None
        except ValueError as exc:
            raise UniverseError("universe.invalidHistoryDate") from exc
        if as_of > today or (history_from and history_from > as_of):
            raise UniverseError("universe.invalidHistoryDate")
        results = []
        errors = []
        warnings = []
        member_cache = {}
        history_snapshot_cache = {}
        for code in codes:
            try:
                members = load_universe_members(code, member_cache)
                count = len({item.get("symbol") for item in members if item.get("symbol")})
                minimum, maximum = EXPECTED_MEMBER_COUNTS[code]
                if not minimum <= count <= maximum:
                    raise RuntimeError(
                        f"member count failed validation: {count}; expected {minimum}..{maximum}"
                    )
                result = apply_snapshot(code, members, as_of)
                if history_from and history_from < as_of:
                    if code in SUPPORTED_HISTORY_UNIVERSES:
                        result["history"] = backfill_universe_history(
                            code,
                            history_from,
                            as_of,
                            apply=True,
                            snapshot_cache=history_snapshot_cache,
                        )
                    else:
                        result["history"] = backfill_current_snapshot_as_fixed(
                            code,
                            history_from,
                            as_of,
                        )
                        warnings.append({
                            "code": code,
                            "warning": "universe.currentMembershipBackfilled",
                        })
                results.append(result)
            except Exception as exc:
                logger.exception("system universe sync failed for code=%s", code)
                errors.append({"code": code, "error": str(exc)})
        if errors:
            message = "universe.syncPartial" if results else "universe.syncFailed"
            return jsonify({
                "code": 0,
                "msg": message,
                "data": {
                    "as_of": as_of.isoformat(),
                    "history_from": history_from.isoformat() if history_from else None,
                    "results": results,
                    "errors": errors,
                    "warnings": warnings,
                },
            }), 500
        return _success({
            "as_of": as_of.isoformat(),
            "history_from": history_from.isoformat() if history_from else None,
            "results": results,
            "warnings": warnings,
        })
    except UniverseError as exc:
        return _failure(exc)
    except Exception:
        logger.exception("system universe sync failed")
        return jsonify({"code": 0, "msg": "universe.syncFailed", "data": None}), 500


@universe_blp.route("/<int:universe_id>", methods=["GET"])
@login_required
def get_universe(universe_id: int):
    try:
        return _success(get_universe_service().get_universe(g.user_id, universe_id))
    except UniverseError as exc:
        return _failure(exc)
    except Exception:
        logger.exception("get universe failed")
        return jsonify({"code": 0, "msg": "universe.getFailed", "data": None}), 500


@universe_blp.route("/<int:universe_id>", methods=["DELETE"])
@login_required
def delete_universe(universe_id: int):
    try:
        return _success(get_universe_service().delete_manual(g.user_id, universe_id))
    except UniverseError as exc:
        return _failure(exc)
    except Exception:
        logger.exception("delete universe failed")
        return jsonify({"code": 0, "msg": "universe.deleteFailed", "data": None}), 500


@universe_blp.route("/<int:universe_id>/members", methods=["GET"])
@login_required
def get_universe_members(universe_id: int):
    try:
        members = get_universe_service().resolve_members(
            g.user_id,
            universe_id,
            as_of=request.args.get("as_of") or request.args.get("asOf"),
        )
        return _success({"universe_id": universe_id, "members": members, "count": len(members)})
    except UniverseError as exc:
        return _failure(exc)
    except Exception:
        logger.exception("get universe members failed")
        return jsonify({"code": 0, "msg": "universe.membersFailed", "data": None}), 500


@universe_blp.route("/<int:universe_id>/members", methods=["PUT"])
@login_required
def replace_universe_members(universe_id: int):
    try:
        payload = request.get_json(silent=True) or {}
        members = get_universe_service().replace_manual_members(
            g.user_id,
            universe_id,
            payload.get("members") or [],
        )
        return _success({"universe_id": universe_id, "members": members, "count": len(members)})
    except UniverseError as exc:
        return _failure(exc)
    except Exception:
        logger.exception("replace universe members failed")
        return jsonify({"code": 0, "msg": "universe.updateFailed", "data": None}), 500


@universe_blp.route("/<int:universe_id>/snapshots", methods=["POST"])
@login_required
def create_universe_snapshot(universe_id: int):
    try:
        payload = request.get_json(silent=True) or {}
        snapshot = get_universe_service().create_snapshot(
            g.user_id,
            universe_id,
            as_of=payload.get("as_of") or payload.get("asOf"),
        )
        return _success(snapshot, status=201)
    except UniverseError as exc:
        return _failure(exc)
    except Exception:
        logger.exception("create universe snapshot failed")
        return jsonify({"code": 0, "msg": "universe.snapshotFailed", "data": None}), 500
