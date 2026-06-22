"""
Community APIs — indicator marketplace.

REST endpoints for marketplace listings, purchases, comments, author dashboard,
and admin review.
"""

from flask import g, jsonify, request
from app.openapi.blueprint import HumanBlueprint as Blueprint

from app.utils.auth import login_required
from app.utils.logger import get_logger
from app.services.community_service import get_community_service

logger = get_logger(__name__)

community_blp = Blueprint("community", __name__)


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
        sort_by: 'score' (default) / 'newest' / 'hot' / 'price_asc' /
                 'price_desc' / 'rating'.
                 'score' sorts by the composite multi-factor backtest score
                 (see services/experiment/scoring.py) and is now the
                 default — putting genuinely well-performing indicators
                 at the top of the marketplace, not just the newest ones.
    """
    try:
        page = int(request.args.get('page', 1))
        page_size = int(request.args.get('page_size', 12))
        keyword = request.args.get('keyword', '').strip()
        pricing_type = request.args.get('pricing_type', '').strip() or None
        sort_by = request.args.get('sort_by', 'score').strip()
        asset_type = request.args.get('asset_type', '').strip() or None
        
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
            sort_by=sort_by,
            user_id=g.user_id,
            accept_language=accept_lang,
            asset_type=asset_type,
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
        )
        
        if not result:
            return jsonify({'code': 0, 'msg': 'indicator_not_found', 'data': None}), 404
        
        return jsonify({'code': 1, 'msg': 'success', 'data': result})
        
    except Exception as e:
        logger.error(f"get_indicator_detail failed: {e}")
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


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
        - Original listing must still be published
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
# Live performance
# ==========================================

@community_blp.route("/indicators/<int:indicator_id>/performance", methods=["GET"])
@login_required
def get_indicator_performance(indicator_id: int):
    """Get live performance statistics for an indicator."""
    try:
        service = get_community_service()
        result = service.get_indicator_performance(indicator_id)
        
        return jsonify({'code': 1, 'msg': 'success', 'data': result})
        
    except Exception as e:
        logger.error(f"get_indicator_performance failed: {e}")
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


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
    """
    try:
        if not _is_admin():
            return jsonify({'code': 0, 'msg': 'admin_required', 'data': None}), 403
        
        page = int(request.args.get('page', 1))
        page_size = int(request.args.get('page_size', 20))
        review_status = request.args.get('review_status', 'pending').strip() or 'pending'
        page_size = min(max(page_size, 1), 100)
        
        service = get_community_service()
        result = service.get_pending_indicators(
            page=page,
            page_size=page_size,
            review_status=review_status
        )
        
        return jsonify({'code': 1, 'msg': 'success', 'data': result})
        
    except Exception as e:
        logger.error(f"get_pending_indicators failed: {e}")
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@community_blp.route("/admin/review-stats", methods=["GET"])
@login_required
def get_review_stats():
    """Review queue statistics (admin only)."""
    try:
        if not _is_admin():
            return jsonify({'code': 0, 'msg': 'admin_required', 'data': None}), 403
        
        service = get_community_service()
        result = service.get_review_stats()
        
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
