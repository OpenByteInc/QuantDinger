"""
Billing APIs — membership plans and USDT on-chain payments.

Plan amounts and credits are read from system settings (.env).
Membership activation is triggered only after USDT payment confirmation
(see usdt_payment_service).
"""

from flask import g, jsonify, request
from app.openapi.blueprint import HumanBlueprint as Blueprint

from app.utils.auth import login_required
from app.utils.logger import get_logger
from app.services.billing_service import get_billing_service
from app.services.usdt_payment_service import get_usdt_payment_service

logger = get_logger(__name__)

billing_blp = Blueprint("billing", __name__)


@billing_blp.route("/plans", methods=["GET"])
@login_required
def get_membership_plans():
    """Get membership plan configuration + current user's billing snapshot."""
    try:
        user_id = getattr(g, "user_id", None)
        svc = get_billing_service()
        plans = svc.get_membership_plans()
        billing_info = svc.get_user_billing_info(user_id) if user_id else {}
        return jsonify({"code": 1, "msg": "success", "data": {"plans": plans, "billing": billing_info}})
    except Exception as e:
        logger.error(f"get_membership_plans failed: {e}", exc_info=True)
        return jsonify({"code": 0, "msg": str(e), "data": None}), 500


# =========================
# USDT Pay
# =========================


@billing_blp.route("/usdt/chains", methods=["GET"])
@login_required
def usdt_list_chains():
    """List USDT chains that are enabled AND have a receiving address
    configured. Chains without an address are auto-hidden by the backend
    so the frontend chain picker can render the response verbatim.
    """
    try:
        if not get_billing_service().is_billing_enabled():
            return jsonify({"code": 1, "msg": "success", "data": {"chains": [], "billing_enabled": False}})
        chains = get_usdt_payment_service().list_chains()
        return jsonify({"code": 1, "msg": "success", "data": {"chains": chains, "billing_enabled": True}})
    except Exception as e:
        logger.error(f"usdt_list_chains failed: {e}", exc_info=True)
        return jsonify({"code": 0, "msg": str(e), "data": None}), 500


@billing_blp.route("/usdt/create", methods=["POST"])
@login_required
def usdt_create_order():
    """Create a USDT membership order.

    Body:
      {
        plan:  "monthly" | "yearly" | "lifetime",
        chain: "TRC20" | "BEP20" | "ERC20" | "SOL"   # optional; defaults to
                                                     # the first enabled chain
      }
    """
    try:
        user_id = getattr(g, "user_id", None)
        data = request.get_json() or {}
        plan = (data.get("plan") or "").strip().lower()
        chain = (data.get("chain") or "").strip().upper() or None
        if not plan:
            return jsonify({"code": 0, "msg": "missing_plan", "data": None}), 400
        if not get_billing_service().is_billing_enabled():
            return jsonify({"code": 0, "msg": "billing_disabled", "data": None}), 403

        ok, msg, out = get_usdt_payment_service().create_order(user_id, plan, chain=chain)
        if ok:
            return jsonify({"code": 1, "msg": "success", "data": out})
        return jsonify({"code": 0, "msg": msg, "data": out}), 400
    except Exception as e:
        logger.error(f"usdt_create_order failed: {e}", exc_info=True)
        return jsonify({"code": 0, "msg": str(e), "data": None}), 500


@billing_blp.route("/usdt/order/<int:order_id>", methods=["GET"])
@login_required
def usdt_get_order(order_id: int):
    """Get my USDT order; refresh chain status by default."""
    try:
        user_id = getattr(g, "user_id", None)
        refresh = str(request.args.get("refresh", "1")).lower() in ("1", "true", "yes")
        ok, msg, out = get_usdt_payment_service().get_order(user_id, order_id, refresh=refresh)
        if ok:
            return jsonify({"code": 1, "msg": "success", "data": out})
        return jsonify({"code": 0, "msg": msg, "data": out}), 404
    except Exception as e:
        logger.error(f"usdt_get_order failed: {e}", exc_info=True)
        return jsonify({"code": 0, "msg": str(e), "data": None}), 500


# openapi-compat: legacy import name
billing_bp = billing_blp
