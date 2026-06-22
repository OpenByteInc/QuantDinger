"""Market module discovery endpoints."""
from __future__ import annotations

from flask import jsonify

from app.markets.registry import list_market_modules
from app.openapi.blueprint import HumanBlueprint as Blueprint
from app.utils.auth import login_required


market_modules_blp = Blueprint("market_modules", __name__)


@market_modules_blp.route("/", methods=["GET"], strict_slashes=False)
@login_required
def get_market_modules():
    """Return canonical market module status for the current deployment."""
    return jsonify({"code": 1, "msg": "success", "data": {"markets": list_market_modules()}})
