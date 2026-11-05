"""
Community APIs — indicator marketplace.

REST endpoints for marketplace listings, purchases, comments, author dashboard,
and admin review.
"""

from typing import Any
from uuid import uuid4

from flask import g, jsonify, request
from app.openapi.blueprint import HumanBlueprint as Blueprint

from app.utils.auth import login_required
from app.utils.logger import get_logger
from app.services.community_service import get_community_service
from app.services.billing_service import get_billing_service
from app.services.market_share import generate_market_share_draft, normalize_share_format
from app.services.settings.capabilities import is_marketplace_enabled

logger = get_logger(__name__)

community_blp = Blueprint("community", __name__)


@community_blp.before_request
def require_marketplace_enabled():
    if not is_marketplace_enabled():
        return jsonify({'code': 0, 'msg': 'marketplace_disabled'}), 404


# ==========================================
# Marketplace
# ==========================================

@community_blp.route("/indicators", methods=["GET"])
@login_required
def get_market_indicators():
    """
    List published marketplace indicators.

    Query params:
        page: Page number (default 1)
        page_size: Page size (default 12)
        keyword: Search keyword
        pricing_type: 'free' / 'paid' / empty (all)
        vip_free: 1/true to show VIP-free paid assets only
        code_visibility: 'visible' / 'hidden' / empty (all)
        min_price, max_price: Numeric price bounds
        sort_by: 'score' (default) / 'newest' / 'hot' / 'price_asc' /
                 'price_desc' / 'rating' / 'views' / 'installs' /
                 'annual_return' / 'max_drawdown' / 'sharpe' / 'users'.
        sort_order: 'asc' / 'desc' for sortable catalog columns.
                 'score' sorts by the composite Strategy API V2 backtest score
                 (see services/community_kpis.py) and is now the
                 default — putting genuinely well-performing indicators
                 at the top of the marketplace, not just the newest ones.
    """
    try:
        page = int(request.args.get('page', 1))
        page_size = int(request.args.get('page_size', 12))
        keyword = request.args.get('keyword', '').strip()
        pricing_type = request.args.get('pricing_type', '').strip() or None
        vip_free_raw = request.args.get('vip_free', '').strip().lower()
        vip_free = vip_free_raw in ('1', 'true', 'yes', 'y', 'on')
        code_visibility = request.args.get('code_visibility', '').strip().lower() or None
        def _parse_price_bound(value):
            value = (value or '').strip()
            if not value:
                return None
            try:
                parsed = float(value)
                return parsed if parsed >= 0 else None
            except (TypeError, ValueError):
                return None

        min_price = _parse_price_bound(request.args.get('min_price'))
        max_price = _parse_price_bound(request.args.get('max_price'))
        if min_price is not None and max_price is not None and min_price > max_price:
            min_price, max_price = max_price, min_price
        sort_by = request.args.get('sort_by', 'score').strip()
        sort_order = request.args.get('sort_order', '').strip().lower()
        if sort_order not in {'asc', 'desc'}:
            sort_order = None
        asset_type = request.args.get('asset_type', '').strip() or None
        market = request.args.get('market', '').strip() or None
        market_type = request.args.get('market_type', '').strip().lower() or None
        binding_mode = request.args.get('binding_mode', '').strip().lower() or None
        strategy_type = request.args.get('strategy_type', '').strip().lower() or None
        direction_mode = request.args.get('direction_mode', '').strip().lower() or None
        leverage = request.args.get('leverage', '').strip().lower() or None
        
        page_size = min(max(page_size, 1), 50)
        
        accept_lang = (
            request.headers.get('X-App-Lang')
            or request.headers.get('Accept-Language', '').split(',')[0].strip()
            or 'en-US'
        )

        service = get_community_service()
        result = service.get_market_indicators(
            page=page,
            page_size=page_size,
            keyword=keyword if keyword else None,
            pricing_type=pricing_type,
            vip_free=vip_free,
            code_visibility=code_visibility,
            min_price=min_price,
            max_price=max_price,
            sort_by=sort_by,
            sort_order=sort_order,
            user_id=g.user_id,
            accept_language=accept_lang,
            asset_type=asset_type,
            market=market,
            market_type=market_type,
            binding_mode=binding_mode,
            strategy_type=strategy_type,
            direction_mode=direction_mode,
            leverage=leverage,
        )
        
        return jsonify({'code': 1, 'msg': 'success', 'data': result})
        
    except Exception as e:
        logger.error(f"get_market_indicators failed: {e}")
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@community_blp.route("/indicators/<int:indicator_id>", methods=["GET"])
@login_required
def get_indicator_detail(indicator_id: int):
    """Get marketplace indicator detail."""
    try:
        accept_lang = (
            request.headers.get('X-App-Lang')
            or request.headers.get('Accept-Language', '').split(',')[0].strip()
            or 'en-US'
        )
        service = get_community_service()
        result = service.get_indicator_detail(
            indicator_id,
            user_id=g.user_id,
            accept_language=accept_lang,
            is_admin=_is_admin(),
        )
        
        if not result:
            return jsonify({'code': 0, 'msg': 'indicator_not_found', 'data': None}), 404
        
        return jsonify({'code': 1, 'msg': 'success', 'data': result})
        
    except Exception as e:
        logger.error(f"get_indicator_detail failed: {e}")
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@community_blp.route("/indicators/<int:indicator_id>/compatibility", methods=["GET"])
@login_required
def check_strategy_compatibility(indicator_id: int):
    result = get_community_service().check_strategy_compatibility(
        indicator_id,
        target_instrument=request.args.get('target_instrument', '').strip(),
    )
    if result is None:
        return jsonify({'code': 0, 'msg': 'indicator_not_found', 'data': None}), 404
    return jsonify({'code': 1, 'msg': 'success', 'data': result})


@community_blp.route("/indicators/<int:indicator_id>/adapt", methods=["POST"])
@login_required
def adapt_marketplace_strategy(indicator_id: int):
    payload = request.get_json(silent=True) or {}
    ok, msg, data = get_community_service().adapt_marketplace_strategy(
        int(g.user_id),
        indicator_id,
        target_instrument=str(payload.get('target_instrument') or payload.get('targetInstrument') or '').strip(),
    )
    return jsonify({'code': 1 if ok else 0, 'msg': msg, 'data': data}), (200 if ok else 400)


# ==========================================
# Purchases
# ==========================================

@community_blp.route("/indicators/<int:indicator_id>/purchase", methods=["POST"])
@login_required
def purchase_indicator(indicator_id: int):
    """
    Purchase a marketplace indicator.

    Side effects:
        1. Verify buyer has enough credits
        2. Debit buyer credits and credit seller
        3. Create purchase record
        4. Copy indicator into buyer account
    """
    try:
        service = get_community_service()
        success, message, data = service.purchase_indicator(
            buyer_id=g.user_id,
            indicator_id=indicator_id
        )
        
        if success:
            return jsonify({'code': 1, 'msg': message, 'data': data})
        else:
            return jsonify({'code': 0, 'msg': message, 'data': data}), 400
            
    except Exception as e:
        logger.error(f"purchase_indicator failed: {e}")
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@community_blp.route("/indicators/<int:indicator_id>/sync", methods=["POST"])
@login_required
def sync_purchased_indicator(indicator_id: int):
    """
    Sync latest code for a purchased indicator.

    Use when the publisher updated code after the buyer purchased; the buyer
    pulls the newest version into their local copy.

    Preconditions:
        - Caller must have purchased the indicator
        - Published listings sync from the latest marketplace code
        - Unpublished/deleted listings can still restore from the purchase snapshot
    """
    try:
        service = get_community_service()
        success, message, data = service.sync_purchased_indicator(
            buyer_id=g.user_id,
            indicator_id=indicator_id
        )

        if success:
            return jsonify({'code': 1, 'msg': message, 'data': data})
        else:
            status = 400
            if message in (
                'indicator_not_found',
                'indicator_unpublished',
                'indicator_unavailable',
                'local_copy_not_found',
            ):
                status = 404
            elif message == 'not_purchased':
                status = 403
            return jsonify({'code': 0, 'msg': message, 'data': data}), status

    except Exception as e:
        logger.error(f"sync_purchased_indicator failed: {e}")
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@community_blp.route("/my-purchases", methods=["GET"])
@login_required
def get_my_purchases():
    """List indicators purchased by the current user."""
    try:
        page = int(request.args.get('page', 1))
        page_size = int(request.args.get('page_size', 20))
        page_size = min(max(page_size, 1), 50)
        
        service = get_community_service()
        result = service.get_my_purchases(
            user_id=g.user_id,
            page=page,
            page_size=page_size
        )
        
        return jsonify({'code': 1, 'msg': 'success', 'data': result})
        
    except Exception as e:
        logger.error(f"get_my_purchases failed: {e}")
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


# ==========================================
# Author dashboard
# ==========================================

@community_blp.route("/author/summary", methods=["GET"])
@login_required
def get_author_summary():
    """Author overview: published / approved / pending / sales / revenue / avg rating."""
    try:
        service = get_community_service()
        result = service.get_author_summary(user_id=g.user_id)
        return jsonify({'code': 1, 'msg': 'success', 'data': result})
    except Exception as e:
        logger.error(f"get_author_summary failed: {e}")
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@community_blp.route("/author/published", methods=["GET"])
@login_required
def get_author_published():
    """List indicators published by the author with per-item sales, revenue, and rating."""
    try:
        page = int(request.args.get('page', 1))
        page_size = int(request.args.get('page_size', 20))
        page_size = min(max(page_size, 1), 50)

        service = get_community_service()
        result = service.get_author_published(
            user_id=g.user_id,
            page=page,
            page_size=page_size,
        )
        return jsonify({'code': 1, 'msg': 'success', 'data': result})
    except Exception as e:
        logger.error(f"get_author_published failed: {e}")
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@community_blp.route("/author/sales", methods=["GET"])
@login_required
def get_author_sales():
    """Paginated author sales ledger; optional indicator_id filter."""
    try:
        page = int(request.args.get('page', 1))
        page_size = int(request.args.get('page_size', 20))
        page_size = min(max(page_size, 1), 100)
        indicator_id_raw = request.args.get('indicator_id', '').strip()
        indicator_id = int(indicator_id_raw) if indicator_id_raw else None

        service = get_community_service()
        result = service.get_author_sales(
            user_id=g.user_id,
            page=page,
            page_size=page_size,
            indicator_id=indicator_id,
        )
        return jsonify({'code': 1, 'msg': 'success', 'data': result})
    except Exception as e:
        logger.error(f"get_author_sales failed: {e}")
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@community_blp.route("/author/indicators/<int:indicator_id>/unpublish", methods=["POST"])
@login_required
def author_unpublish_indicator(indicator_id: int):
    """Let an author unpublish their own marketplace asset."""
    try:
        data = request.get_json() or {}
        note = (data.get("note") or "").strip()
        service = get_community_service()
        success, message = service.author_unpublish_asset(
            user_id=g.user_id,
            indicator_id=indicator_id,
            note=note,
        )
        if success:
            return jsonify({'code': 1, 'msg': message, 'data': None})
        status = 404 if message == 'indicator_not_found' else 400
        return jsonify({'code': 0, 'msg': message, 'data': None}), status
    except Exception as e:
        logger.error(f"author_unpublish_indicator failed: {e}")
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


# ==========================================
# Comments
# ==========================================

@community_blp.route("/indicators/<int:indicator_id>/comments", methods=["GET"])
@login_required
def get_comments(indicator_id: int):
    """List comments for an indicator."""
    try:
        page = int(request.args.get('page', 1))
        page_size = int(request.args.get('page_size', 20))
        page_size = min(max(page_size, 1), 50)
        
        service = get_community_service()
        result = service.get_comments(
            indicator_id=indicator_id,
            page=page,
            page_size=page_size
        )
        
        return jsonify({'code': 1, 'msg': 'success', 'data': result})
        
    except Exception as e:
        logger.error(f"get_comments failed: {e}")
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@community_blp.route("/indicators/<int:indicator_id>/comments", methods=["POST"])
@login_required
def add_comment(indicator_id: int):
    """
    Add a comment.

    Request body:
        rating: 1-5 star rating
        content: Optional comment text (max 500 chars)

    Only purchasers may comment, and only once per indicator.
    """
    try:
        data = request.get_json() or {}
        rating = int(data.get('rating', 5))
        content = (data.get('content') or '').strip()
        
        service = get_community_service()
        success, message, result = service.add_comment(
            user_id=g.user_id,
            indicator_id=indicator_id,
            rating=rating,
            content=content
        )
        
        if success:
            return jsonify({'code': 1, 'msg': message, 'data': result})
        else:
            return jsonify({'code': 0, 'msg': message, 'data': result}), 400
            
    except Exception as e:
        logger.error(f"add_comment failed: {e}")
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@community_blp.route("/indicators/<int:indicator_id>/comments/<int:comment_id>", methods=["PUT"])
@login_required
def update_comment(indicator_id: int, comment_id: int):
    """
    Update own comment.

    Request body:
        rating: 1-5 star rating
        content: Comment text (max 500 chars)
    """
    try:
        data = request.get_json() or {}
        rating = int(data.get('rating', 5))
        content = (data.get('content') or '').strip()
        
        service = get_community_service()
        success, message, result = service.update_comment(
            user_id=g.user_id,
            comment_id=comment_id,
            indicator_id=indicator_id,
            rating=rating,
            content=content
        )
        
        if success:
            return jsonify({'code': 1, 'msg': message, 'data': result})
        else:
            return jsonify({'code': 0, 'msg': message, 'data': result}), 400
            
    except Exception as e:
        logger.error(f"update_comment failed: {e}")
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@community_blp.route("/indicators/<int:indicator_id>/my-comment", methods=["GET"])
@login_required
def get_my_comment(indicator_id: int):
    """Get current user's comment on an indicator (for edit form)."""
    try:
        service = get_community_service()
        result = service.get_user_comment(
            user_id=g.user_id,
            indicator_id=indicator_id
        )
        
        return jsonify({'code': 1, 'msg': 'success', 'data': result})
        
    except Exception as e:
        logger.error(f"get_my_comment failed: {e}")
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


# ==========================================
# Market performance
# ==========================================

@community_blp.route("/indicators/<int:indicator_id>/performance", methods=["GET"])
@login_required
def get_indicator_performance(indicator_id: int):
    """Get marketplace performance statistics for an asset."""
    try:
        service = get_community_service()
        result = service.get_indicator_performance(indicator_id)
        
        return jsonify({'code': 1, 'msg': 'success', 'data': result})
        
    except Exception as e:
        logger.error(f"get_indicator_performance failed: {e}")
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


def _consume_market_share_credits(user_id: int, indicator_id: int) -> tuple[Any, dict[str, Any]]:
    billing = get_billing_service()
    enabled = bool(billing.is_billing_enabled())
    cost = max(0, int(billing.get_feature_cost("ai_copilot_chat") or 0))
    reference_id = f"market_share:{indicator_id}:{uuid4().hex}"
    charge = {
        "enabled": enabled,
        "feature": "ai_copilot_chat",
        "cost": cost,
        "charged": 0,
        "remaining": float(billing.get_user_credits(user_id)),
        "referenceId": reference_id,
    }
    if not enabled or cost <= 0:
        return billing, charge

    success, message = billing.check_and_consume(user_id, "ai_copilot_chat", reference_id)
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
        return billing, {**charge, "error": "billing_error"}
    charge["charged"] = cost
    charge["remaining"] = float(billing.get_user_credits(user_id))
    return billing, charge


def _refund_market_share_credits(
    billing: Any,
    user_id: int,
    charge: dict[str, Any],
) -> None:
    cost = int(charge.get("charged") or 0)
    if not billing or cost <= 0:
        return
    refunded, message = billing.add_credits(
        user_id=user_id,
        amount=cost,
        action="refund",
        remark="market_share_ai_generation_refund",
        reference_id=str(charge.get("referenceId") or ""),
    )
    if not refunded:
        logger.error("Marketplace share credit refund failed for user %s: %s", user_id, message)
        return
    charge["charged"] = 0
    charge["refunded"] = cost
    charge["remaining"] = float(billing.get_user_credits(user_id))


@community_blp.route("/indicators/<int:indicator_id>/share-draft", methods=["POST"])
@login_required
def generate_indicator_share_draft(indicator_id: int):
    """Generate grounded social copy from a public marketplace listing."""
    billing = None
    charge: dict[str, Any] = {}
    user_id = int(g.user_id)
    try:
        payload = request.get_json(silent=True) or {}
        share_format = normalize_share_format(payload.get("format"))
        locale = (
            payload.get("locale")
            or request.headers.get("X-App-Lang")
            or request.headers.get("Accept-Language")
            or "en-US"
        )
        service = get_community_service()
        detail = service.get_indicator_detail(
            indicator_id,
            user_id=user_id,
            accept_language=str(locale),
            is_admin=_is_admin(),
            increment_view=False,
        )
        if not detail:
            return jsonify({"code": 0, "msg": "indicator_not_found", "data": None}), 404
        performance = service.get_indicator_performance(indicator_id)

        billing, charge = _consume_market_share_credits(user_id, indicator_id)
        if charge.get("error") == "insufficient_credits":
            return jsonify({
                "code": 0,
                "msg": "insufficient_credits",
                "data": {
                    "error_type": "INSUFFICIENT_CREDITS",
                    "feature": "ai_copilot_chat",
                    "current": charge["current"],
                    "required": charge["required"],
                    "shortage": charge["shortage"],
                },
            }), 402
        if charge.get("error"):
            return jsonify({"code": 0, "msg": "billing_error", "data": None}), 503

        draft = generate_market_share_draft(
            detail,
            performance,
            share_format=share_format,
            locale=locale,
        )
        return jsonify({
            "code": 1,
            "msg": "success",
            "data": {"draft": draft, "billing": charge},
        })
    except ValueError as exc:
        _refund_market_share_credits(billing, user_id, charge)
        error_key = str(exc)
        status = 400 if error_key in {"unsupported_share_format", "listing_name_missing"} else 502
        logger.warning("Marketplace share generation rejected: %s", error_key)
        return jsonify({"code": 0, "msg": error_key, "data": None}), status
    except Exception as exc:
        _refund_market_share_credits(billing, user_id, charge)
        logger.exception("generate_indicator_share_draft failed: %s", exc)
        return jsonify({"code": 0, "msg": "ai_share_generation_failed", "data": None}), 500


# ==========================================
# Admin review
# ==========================================

def _is_admin():
    """Return True when the current user is an admin."""
    role = getattr(g, 'user_role', None)
    return role == 'admin'


@community_blp.route("/admin/pending-indicators", methods=["GET"])
@login_required
def get_pending_indicators():
    """
    List indicators pending review (admin only).

    Query params:
        page: Page number (default 1)
        page_size: Page size (default 20)
        review_status: 'pending' / 'approved' / 'rejected' / 'all'
        keyword: Search name, description or author
        asset_type: 'indicator' / 'script_template' / empty
        pricing_type: 'free' / 'paid' / 'vip_free' / empty
        sort_by: 'newest' / 'oldest' / 'price_asc' / 'price_desc'
    """
    try:
        if not _is_admin():
            return jsonify({'code': 0, 'msg': 'admin_required', 'data': None}), 403
        
        page = int(request.args.get('page', 1))
        page_size = int(request.args.get('page_size', 20))
        review_status = request.args.get('review_status', 'pending').strip() or 'pending'
        keyword = request.args.get('keyword', '').strip() or None
        asset_type = request.args.get('asset_type', '').strip() or None
        pricing_type = request.args.get('pricing_type', '').strip() or None
        sort_by = request.args.get('sort_by', 'newest').strip() or 'newest'
        page_size = min(max(page_size, 1), 100)
        
        service = get_community_service()
        result = service.get_pending_indicators(
            page=page,
            page_size=page_size,
            review_status=review_status,
            keyword=keyword,
            asset_type=asset_type,
            pricing_type=pricing_type,
            sort_by=sort_by,
        )
        
        return jsonify({'code': 1, 'msg': 'success', 'data': result})
        
    except Exception as e:
        logger.error(f"get_pending_indicators failed: {e}")
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@community_blp.route("/admin/review-stats", methods=["GET"])
@login_required
def get_review_stats():
    """Review queue statistics for the active admin filters."""
    try:
        if not _is_admin():
            return jsonify({'code': 0, 'msg': 'admin_required', 'data': None}), 403

        keyword = request.args.get('keyword', '').strip() or None
        asset_type = request.args.get('asset_type', '').strip() or None
        pricing_type = request.args.get('pricing_type', '').strip() or None

        service = get_community_service()
        result = service.get_review_stats(
            keyword=keyword,
            asset_type=asset_type,
            pricing_type=pricing_type,
        )
        
        return jsonify({'code': 1, 'msg': 'success', 'data': result})
        
    except Exception as e:
        logger.error(f"get_review_stats failed: {e}")
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@community_blp.route("/admin/indicators/<int:indicator_id>/review", methods=["POST"])
@login_required
def review_indicator(indicator_id: int):
    """
    Approve or reject an indicator (admin only).

    Request body:
        action: 'approve' / 'reject'
        note: Optional review note
    """
    try:
        if not _is_admin():
            return jsonify({'code': 0, 'msg': 'admin_required', 'data': None}), 403
        
        data = request.get_json() or {}
        action = data.get('action', '').strip()
        note = data.get('note', '').strip()
        
        if action not in ('approve', 'reject'):
            return jsonify({'code': 0, 'msg': 'invalid_action', 'data': None}), 400
        
        service = get_community_service()
        success, message = service.review_indicator(
            admin_id=g.user_id,
            indicator_id=indicator_id,
            action=action,
            note=note
        )
        
        if success:
            return jsonify({'code': 1, 'msg': message, 'data': None})
        else:
            return jsonify({'code': 0, 'msg': message, 'data': None}), 400
            
    except Exception as e:
        logger.error(f"review_indicator failed: {e}")
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@community_blp.route("/admin/indicators/<int:indicator_id>/unpublish", methods=["POST"])
@login_required
def unpublish_indicator(indicator_id: int):
    """
    Unpublish an indicator (admin only).

    Request body:
        note: Optional unpublish reason
    """
    try:
        if not _is_admin():
            return jsonify({'code': 0, 'msg': 'admin_required', 'data': None}), 403
        
        data = request.get_json() or {}
        note = data.get('note', '').strip()
        
        service = get_community_service()
        success, message = service.unpublish_indicator(
            admin_id=g.user_id,
            indicator_id=indicator_id,
            note=note
        )
        
        if success:
            return jsonify({'code': 1, 'msg': message, 'data': None})
        else:
            return jsonify({'code': 0, 'msg': message, 'data': None}), 400
            
    except Exception as e:
        logger.error(f"unpublish_indicator failed: {e}")
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@community_blp.route("/admin/indicators/<int:indicator_id>", methods=["DELETE"])
@login_required
def admin_delete_indicator(indicator_id: int):
    """Delete an indicator (admin only)."""
    try:
        if not _is_admin():
            return jsonify({'code': 0, 'msg': 'admin_required', 'data': None}), 403
        
        service = get_community_service()
        success, message = service.admin_delete_indicator(
            admin_id=g.user_id,
            indicator_id=indicator_id
        )
        
        if success:
            return jsonify({'code': 1, 'msg': message, 'data': None})
        else:
            return jsonify({'code': 0, 'msg': message, 'data': None}), 400
            
    except Exception as e:
        logger.error(f"admin_delete_indicator failed: {e}")
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


# openapi-compat: legacy import name
community_bp = community_blp
