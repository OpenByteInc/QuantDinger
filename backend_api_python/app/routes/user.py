"""
User Management API Routes

Provides endpoints for user CRUD operations, role management, etc.
Only accessible by admin users.
"""
import csv
import json
from io import StringIO
import re
from flask import Response, g, jsonify, request
from app.openapi.blueprint import HumanBlueprint as Blueprint
from app.services.user_preferences import (
    change_user_password,
    delete_chart_template as delete_chart_template_service,
    ensure_chart_templates_column,
    get_notification_settings as get_notification_settings_service,
    list_chart_templates as list_chart_templates_service,
    save_chart_template as save_chart_template_service,
    send_test_notification,
    update_notification_settings as update_notification_settings_service,
)
from app.services.user_service import get_user_service
from app.utils.auth import login_required, admin_required
from app.utils.db import get_db_connection
from app.utils.logger import get_logger

logger = get_logger(__name__)

_PROFILE_TIMEZONE_RE = re.compile(r'^[A-Za-z0-9_/+\-.]+$')


def _parse_positive_int(value) -> int:
    """Parse query-string int; return 0 when missing/invalid."""
    if value is None or value == '':
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _ensure_chart_templates_column():
    """Back-compatible wrapper for older route-local callers."""
    ensure_chart_templates_column()

user_blp = Blueprint('user_manage', __name__)


@user_blp.route('/list', methods=['GET'])
@login_required
@admin_required
def list_users():
    """
    List all users (admin only).
    
    Query params:
        page: int (default 1)
        page_size: int (default 20, max 100)
        search: str (optional, search by username/email/nickname/id)
        user_id: int (optional, exact user id filter)
    """
    try:
        page = request.args.get('page', 1, type=int)
        page_size = request.args.get('page_size', 20, type=int)
        search = request.args.get('search', '', type=str)
        user_id = _parse_positive_int(request.args.get('user_id'))
        if user_id <= 0:
            user_id = None
        page_size = min(100, max(1, page_size))
        
        result = get_user_service().list_users(
            page=page, page_size=page_size, search=search, user_id=user_id,
        )
        
        return jsonify({
            'code': 1,
            'msg': 'success',
            'data': result
        })
    except Exception as e:
        logger.error(f"list_users failed: {e}")
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@user_blp.route('/export', methods=['GET'])
@login_required
@admin_required
def export_users():
    """Export all users as an Excel-friendly CSV file (admin only)."""
    try:
        search = request.args.get('search', '', type=str)
        user_id = _parse_positive_int(request.args.get('user_id'))
        if user_id <= 0:
            user_id = None
        users = get_user_service().list_all_users_for_export(search=search, user_id=user_id)

        output = StringIO()
        output.write('\ufeff')
        writer = csv.writer(output)
        writer.writerow([
            'ID', 'Username', 'Email', 'Nickname', 'Role', 'Status',
            'Credits', 'VIP Expires At', 'Timezone', 'Register IP',
            'Last Login At', 'Created At', 'Updated At'
        ])

        for user in users:
            writer.writerow([
                user.get('id') or '',
                user.get('username') or '',
                user.get('email') or '',
                user.get('nickname') or '',
                user.get('role') or '',
                user.get('status') or '',
                user.get('credits') or 0,
                user.get('vip_expires_at') or '',
                user.get('timezone') or '',
                user.get('register_ip') or '',
                user.get('last_login_at') or '',
                user.get('created_at') or '',
                user.get('updated_at') or '',
            ])

        filename = 'quantdinger_users_export.csv'
        return Response(
            output.getvalue(),
            mimetype='text/csv; charset=utf-8',
            headers={
                'Content-Disposition': f'attachment; filename="{filename}"'
            }
        )
    except Exception as e:
        logger.error(f"export_users failed: {e}", exc_info=True)
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@user_blp.route('/detail', methods=['GET'])
@login_required
@admin_required
def get_user_detail():
    """Get user detail by ID (admin only)"""
    try:
        user_id = request.args.get('id', type=int)
        if not user_id:
            return jsonify({'code': 0, 'msg': 'Missing user id', 'data': None}), 400
        
        user = get_user_service().get_user_by_id(user_id)
        if not user:
            return jsonify({'code': 0, 'msg': 'User not found', 'data': None}), 404
        
        return jsonify({
            'code': 1,
            'msg': 'success',
            'data': user
        })
    except Exception as e:
        logger.error(f"get_user_detail failed: {e}")
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@user_blp.route('/create', methods=['POST'])
@login_required
@admin_required
def create_user():
    """
    Create a new user (admin only).
    
    Request body:
        username: str (required)
        password: str (required)
        email: str (optional)
        nickname: str (optional)
        role: str (optional, default 'user')
    """
    try:
        data = request.get_json() or {}
        
        user_id = get_user_service().create_user(data)
        
        return jsonify({
            'code': 1,
            'msg': 'User created successfully',
            'data': {'id': user_id}
        })
    except ValueError as e:
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 400
    except Exception as e:
        logger.error(f"create_user failed: {e}")
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@user_blp.route('/update', methods=['PUT'])
@login_required
@admin_required
def update_user():
    """
    Update user information (admin only).
    
    Query params:
        id: int (required)
    
    Request body:
        email: str (optional)
        nickname: str (optional)
        role: str (optional)
        status: str (optional)
    """
    try:
        user_id = request.args.get('id', type=int)
        if not user_id:
            return jsonify({'code': 0, 'msg': 'Missing user id', 'data': None}), 400
        
        data = request.get_json() or {}
        
        success = get_user_service().update_user(user_id, data)
        
        if success:
            return jsonify({'code': 1, 'msg': 'User updated successfully', 'data': None})
        else:
            return jsonify({'code': 0, 'msg': 'Update failed', 'data': None}), 400
    except Exception as e:
        logger.error(f"update_user failed: {e}")
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@user_blp.route('/delete', methods=['DELETE'])
@login_required
@admin_required
def delete_user():
    """Delete a user (admin only)"""
    try:
        user_id = request.args.get('id', type=int)
        if not user_id:
            return jsonify({'code': 0, 'msg': 'Missing user id', 'data': None}), 400
        
        # Prevent deleting self
        if hasattr(g, 'user_id') and g.user_id == user_id:
            return jsonify({'code': 0, 'msg': 'Cannot delete yourself', 'data': None}), 400
        
        success = get_user_service().delete_user(user_id)
        
        if success:
            return jsonify({'code': 1, 'msg': 'User deleted successfully', 'data': None})
        else:
            return jsonify({'code': 0, 'msg': 'Delete failed', 'data': None}), 400
    except Exception as e:
        logger.error(f"delete_user failed: {e}")
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@user_blp.route('/reset-password', methods=['POST'])
@login_required
@admin_required
def reset_user_password():
    """
    Reset a user's password (admin only).
    
    Request body:
        user_id: int (required)
        new_password: str (required)
    """
    try:
        data = request.get_json() or {}
        user_id = data.get('user_id')
        new_password = data.get('new_password', '')
        
        if not user_id:
            return jsonify({'code': 0, 'msg': 'Missing user_id', 'data': None}), 400
        
        if len(new_password) < 6:
            return jsonify({'code': 0, 'msg': 'Password must be at least 6 characters', 'data': None}), 400
        
        success = get_user_service().reset_password(user_id, new_password)
        
        if success:
            return jsonify({'code': 1, 'msg': 'Password reset successfully', 'data': None})
        else:
            return jsonify({'code': 0, 'msg': 'Reset failed', 'data': None}), 400
    except ValueError as e:
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 400
    except Exception as e:
        logger.error(f"reset_user_password failed: {e}")
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@user_blp.route('/roles', methods=['GET'])
@login_required
@admin_required
def get_roles():
    """Get available roles and their permissions"""
    service = get_user_service()
    
    roles = []
    for role in service.ROLES:
        roles.append({
            'id': role,
            'name': role.capitalize(),
            'permissions': service.get_user_permissions(role)
        })
    
    return jsonify({
        'code': 1,
        'msg': 'success',
        'data': {'roles': roles}
    })


# ==================== Billing Management (Admin) ====================

@user_blp.route('/set-credits', methods=['POST'])
@login_required
@admin_required
def set_user_credits():
    """
    Set user credits (admin only).
    
    Request body:
        user_id: int (required)
        credits: int (required)
        remark: str (optional)
    """
    try:
        from app.services.billing_service import get_billing_service
        
        data = request.get_json() or {}
        user_id = data.get('user_id')
        credits = data.get('credits')
        remark = data.get('remark', '')
        
        if not user_id:
            return jsonify({'code': 0, 'msg': 'Missing user_id', 'data': None}), 400
        
        if credits is None or credits < 0:
            return jsonify({'code': 0, 'msg': 'Credits must be a non-negative number', 'data': None}), 400
        
        operator_id = getattr(g, 'user_id', None)
        success, result = get_billing_service().set_credits(user_id, int(credits), remark, operator_id)
        
        if success:
            return jsonify({'code': 1, 'msg': 'Credits updated successfully', 'data': {'credits': result}})
        else:
            return jsonify({'code': 0, 'msg': result, 'data': None}), 400
    except Exception as e:
        logger.error(f"set_user_credits failed: {e}")
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@user_blp.route('/set-vip', methods=['POST'])
@login_required
@admin_required
def set_user_vip():
    """
    Set user VIP status (admin only).
    
    Request body:
        user_id: int (required)
        vip_days: int (optional, 0 to cancel VIP, positive number to grant VIP for days)
        vip_expires_at: str (optional, ISO format datetime, overrides vip_days if provided)
        remark: str (optional)
    """
    try:
        from datetime import datetime, timedelta, timezone
        from app.services.billing_service import get_billing_service
        
        data = request.get_json() or {}
        user_id = data.get('user_id')
        vip_days = data.get('vip_days')
        vip_expires_at_str = data.get('vip_expires_at')
        remark = data.get('remark', '')
        
        if not user_id:
            return jsonify({'code': 0, 'msg': 'Missing user_id', 'data': None}), 400
        
        # Calculate expires_at
        expires_at = None
        if vip_expires_at_str:
            try:
                expires_at = datetime.fromisoformat(vip_expires_at_str.replace('Z', '+00:00'))
            except ValueError:
                return jsonify({'code': 0, 'msg': 'Invalid vip_expires_at format', 'data': None}), 400
        elif vip_days is not None:
            if vip_days > 0:
                expires_at = datetime.now(timezone.utc) + timedelta(days=vip_days)
            else:
                expires_at = None  # Cancel VIP
        else:
            return jsonify({'code': 0, 'msg': 'Provide vip_days or vip_expires_at', 'data': None}), 400
        
        operator_id = getattr(g, 'user_id', None)
        success, result = get_billing_service().set_vip(user_id, expires_at, remark, operator_id)
        
        if success:
            return jsonify({
                'code': 1,
                'msg': 'VIP status updated successfully',
                # Let SafeJSONProvider normalize datetimes to UTC ISO (with Z).
                'data': {'vip_expires_at': expires_at if expires_at else None}
            })
        else:
            return jsonify({'code': 0, 'msg': result, 'data': None}), 400
    except Exception as e:
        logger.error(f"set_user_vip failed: {e}")
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@user_blp.route('/credits-log', methods=['GET'])
@login_required
@admin_required
def get_user_credits_log():
    """
    Get user credits log (admin only).
    
    Query params:
        user_id: int (required)
        page: int (default 1)
        page_size: int (default 20)
    """
    try:
        from app.services.billing_service import get_billing_service
        
        user_id = request.args.get('user_id', type=int)
        if not user_id:
            return jsonify({'code': 0, 'msg': 'Missing user_id', 'data': None}), 400
        
        page = request.args.get('page', 1, type=int)
        page_size = request.args.get('page_size', 20, type=int)
        page_size = min(100, max(1, page_size))
        
        result = get_billing_service().get_credits_log(user_id, page, page_size)
        
        return jsonify({'code': 1, 'msg': 'success', 'data': result})
    except Exception as e:
        logger.error(f"get_user_credits_log failed: {e}")
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


# Self-service endpoints (accessible by any logged-in user)

@user_blp.route('/login-logs', methods=['GET'])
@login_required
def get_login_logs():
    """Paginated account login history (password / email code / OAuth)."""
    try:
        user_id = getattr(g, 'user_id', None)
        if not user_id:
            return jsonify({'code': 0, 'msg': 'Not authenticated', 'data': None}), 401

        page = int(request.args.get('page') or 1)
        page_size = int(request.args.get('page_size') or 20)

        from app.services.login_notify import list_login_logs

        data = list_login_logs(int(user_id), page=page, page_size=page_size)
        return jsonify({'code': 1, 'msg': 'success', 'data': data})
    except Exception as e:
        logger.error(f"get_login_logs failed: {e}")
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@user_blp.route('/profile', methods=['GET'])
@login_required
def get_profile():
    """Get current user's profile with billing info and notification settings"""
    try:
        import json
        from app.services.billing_service import get_billing_service
        from app.utils.db import get_db_connection
        
        user_id = getattr(g, 'user_id', None)
        if not user_id:
            return jsonify({'code': 0, 'msg': 'Not authenticated', 'data': None}), 401
        
        user = get_user_service().get_user_by_id(user_id)
        if not user:
            return jsonify({'code': 0, 'msg': 'User not found', 'data': None}), 404
        
        # Add permissions
        user['permissions'] = get_user_service().get_user_permissions(user.get('role', 'user'))
        
        # Add billing info
        billing_info = get_billing_service().get_user_billing_info(user_id)
        user['billing'] = billing_info
        
        # Add notification settings
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute("SELECT notification_settings FROM qd_users WHERE id = ?", (user_id,))
            row = cur.fetchone()
            cur.close()
        
        settings_str = (row.get('notification_settings') if row else '') or ''
        notification_settings = {}
        if settings_str:
            try:
                notification_settings = json.loads(settings_str)
            except Exception:
                notification_settings = {}
        
        # Default values
        if 'default_channels' not in notification_settings:
            notification_settings['default_channels'] = ['browser']
        
        user['notification_settings'] = notification_settings
        
        return jsonify({
            'code': 1,
            'msg': 'success',
            'data': user
        })
    except Exception as e:
        logger.error(f"get_profile failed: {e}")
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@user_blp.route('/profile/update', methods=['PUT'])
@login_required
def update_profile():
    """
    Update current user's profile (limited fields).
    
    Request body:
        nickname: str (optional)
        avatar: str (optional)
        timezone: str (optional, IANA id; empty = follow client)
    
    Note: Email cannot be changed after registration (for security).
          Only admin can change user email via User Management.
    """
    try:
        user_id = getattr(g, 'user_id', None)
        if not user_id:
            return jsonify({'code': 0, 'msg': 'Not authenticated', 'data': None}), 401
        
        data = request.get_json() or {}
        
        # Only allow updating certain fields for self-service
        # Email is NOT allowed to be changed (security: bound to account)
        allowed = {}
        for field in ['nickname', 'avatar']:
            if field in data:
                allowed[field] = data[field]
        
        if 'timezone' in data:
            tz = (data.get('timezone') or '').strip()
            if tz and (len(tz) > 64 or not _PROFILE_TIMEZONE_RE.match(tz)):
                return jsonify({
                    'code': 0,
                    'msg': 'Invalid timezone identifier',
                    'data': None
                }), 400
            allowed['timezone'] = tz
        
        if not allowed:
            return jsonify({'code': 0, 'msg': 'No valid fields to update', 'data': None}), 400
        
        success = get_user_service().update_user(user_id, allowed)
        
        if success:
            return jsonify({'code': 1, 'msg': 'Profile updated successfully', 'data': None})
        else:
            return jsonify({'code': 0, 'msg': 'Update failed', 'data': None}), 400
    except Exception as e:
        logger.error(f"update_profile failed: {e}")
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@user_blp.route('/mfa/status', methods=['GET'])
@login_required
def get_mfa_status():
    """Get current user's authenticator-app MFA status."""
    try:
        user_id = getattr(g, 'user_id', None)
        if not user_id:
            return jsonify({'code': 0, 'msg': 'Not authenticated', 'data': None}), 401
        from app.services.mfa_service import get_mfa_service
        return jsonify({'code': 1, 'msg': 'success', 'data': get_mfa_service().get_status(int(user_id))})
    except Exception as e:
        logger.error(f"get_mfa_status failed: {e}")
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@user_blp.route('/mfa/setup/start', methods=['POST'])
@login_required
def start_mfa_setup():
    """Start authenticator-app binding and return QR code data."""
    try:
        user_id = getattr(g, 'user_id', None)
        if not user_id:
            return jsonify({'code': 0, 'msg': 'Not authenticated', 'data': None}), 401
        user = get_user_service().get_user_by_id(int(user_id)) or {}
        label = user.get('email') or user.get('username') or f'user-{user_id}'
        from app.services.mfa_service import get_mfa_service
        data = get_mfa_service().start_setup(int(user_id), label)
        return jsonify({'code': 1, 'msg': 'Scan the QR code with your authenticator app', 'data': data})
    except ValueError as e:
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 400
    except Exception as e:
        logger.error(f"start_mfa_setup failed: {e}")
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@user_blp.route('/mfa/setup/confirm', methods=['POST'])
@login_required
def confirm_mfa_setup():
    """Confirm authenticator-app binding with a 6-digit TOTP code."""
    try:
        user_id = getattr(g, 'user_id', None)
        if not user_id:
            return jsonify({'code': 0, 'msg': 'Not authenticated', 'data': None}), 401
        data = request.get_json() or {}
        code = data.get('code') or ''
        from app.services.mfa_service import get_mfa_service
        result = get_mfa_service().confirm_setup(int(user_id), code)
        return jsonify({'code': 1, 'msg': 'MFA enabled successfully', 'data': result})
    except ValueError as e:
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 400
    except Exception as e:
        logger.error(f"confirm_mfa_setup failed: {e}")
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@user_blp.route('/mfa/disable', methods=['POST'])
@login_required
def disable_mfa():
    """Disable current user's authenticator-app MFA after code verification."""
    try:
        user_id = getattr(g, 'user_id', None)
        if not user_id:
            return jsonify({'code': 0, 'msg': 'Not authenticated', 'data': None}), 401
        data = request.get_json() or {}
        code = data.get('code') or ''
        from app.services.mfa_service import get_mfa_service
        get_mfa_service().disable(int(user_id), code)
        return jsonify({'code': 1, 'msg': 'MFA disabled successfully', 'data': None})
    except ValueError as e:
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 400
    except Exception as e:
        logger.error(f"disable_mfa failed: {e}")
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@user_blp.route('/my-credits-log', methods=['GET'])
@login_required
def get_my_credits_log():
    """
    Get current user's credits log.
    
    Query params:
        page: int (default 1)
        page_size: int (default 20)
    """
    try:
        from app.services.billing_service import get_billing_service
        
        user_id = getattr(g, 'user_id', None)
        if not user_id:
            return jsonify({'code': 0, 'msg': 'Not authenticated', 'data': None}), 401
        
        page = request.args.get('page', 1, type=int)
        page_size = request.args.get('page_size', 20, type=int)
        page_size = min(100, max(1, page_size))
        
        result = get_billing_service().get_credits_log(user_id, page, page_size)
        
        return jsonify({'code': 1, 'msg': 'success', 'data': result})
    except Exception as e:
        logger.error(f"get_my_credits_log failed: {e}")
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@user_blp.route('/my-referrals', methods=['GET'])
@login_required
def get_my_referrals():
    """
    Get list of users referred by current user.
    
    Query params:
        page: int (default 1)
        page_size: int (default 20)
    
    Returns:
        list: Users referred by current user (id, username, nickname, avatar, created_at)
        total: Total count of referrals
        referral_code: Current user's referral code (user ID)
        referral_bonus: Credits earned per referral
        register_bonus: Credits new users get on registration
    """
    try:
        import os
        from app.utils.db import get_db_connection
        
        user_id = getattr(g, 'user_id', None)
        if not user_id:
            return jsonify({'code': 0, 'msg': 'Not authenticated', 'data': None}), 401
        
        page = request.args.get('page', 1, type=int)
        page_size = request.args.get('page_size', 20, type=int)
        page_size = min(100, max(1, page_size))
        offset = (page - 1) * page_size
        
        with get_db_connection() as db:
            cur = db.cursor()
            
            # Get total count
            cur.execute(
                "SELECT COUNT(*) as cnt FROM qd_users WHERE referred_by = ?",
                (user_id,)
            )
            total = cur.fetchone()['cnt']
            
            # Get referral list
            cur.execute(
                """
                SELECT id, username, nickname, avatar, created_at 
                FROM qd_users 
                WHERE referred_by = ?
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
                """,
                (user_id, page_size, offset)
            )
            rows = cur.fetchall()
            cur.close()
            
            referrals = []
            for row in rows:
                referrals.append({
                    'id': row['id'],
                    'username': row['username'],
                    'nickname': row['nickname'],
                    'avatar': row['avatar'],
                    # SafeJSONProvider serializes datetimes as UTC ISO.
                    'created_at': row['created_at']
                })
        
        return jsonify({
            'code': 1,
            'msg': 'success',
            'data': {
                'list': referrals,
                'total': total,
                'page': page,
                'page_size': page_size,
                'referral_code': str(user_id),
                'referral_bonus': int(os.getenv('CREDITS_REFERRAL_BONUS', '0')),
                'register_bonus': int(os.getenv('CREDITS_REGISTER_BONUS', '0'))
            }
        })
    except Exception as e:
        logger.error(f"get_my_referrals failed: {e}")
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@user_blp.route('/notification-settings', methods=['GET'])
@login_required
def get_notification_settings():
    """Get current user's notification settings."""
    try:
        user_id = getattr(g, 'user_id', None)
        if not user_id:
            return jsonify({'code': 0, 'msg': 'Not authenticated', 'data': None}), 401
        settings = get_notification_settings_service(int(user_id))
        if settings is None:
            return jsonify({'code': 0, 'msg': 'User not found', 'data': None}), 404
        return jsonify({'code': 1, 'msg': 'success', 'data': settings})
    except Exception as e:
        logger.error(f"get_notification_settings failed: {e}")
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500
@user_blp.route('/notification-settings', methods=['PUT'])
@login_required
def update_notification_settings():
    """Update current user's notification settings."""
    try:
        user_id = getattr(g, 'user_id', None)
        if not user_id:
            return jsonify({'code': 0, 'msg': 'Not authenticated', 'data': None}), 401
        settings = update_notification_settings_service(int(user_id), request.get_json() or {})
        return jsonify({'code': 1, 'msg': 'Notification settings updated', 'data': settings})
    except Exception as e:
        logger.error(f"update_notification_settings failed: {e}")
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500
@user_blp.route('/chart-templates', methods=['GET'])
@login_required
def get_chart_templates():
    """Get current user's indicator chart templates."""
    try:
        user_id = getattr(g, 'user_id', None)
        if not user_id:
            return jsonify({'code': 0, 'msg': 'Not authenticated', 'data': None}), 401
        templates = list_chart_templates_service(int(user_id))
        return jsonify({'code': 1, 'msg': 'success', 'data': templates})
    except Exception as e:
        logger.error(f"get_chart_templates failed: {e}")
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500
@user_blp.route('/chart-templates', methods=['POST'])
@login_required
def save_chart_template():
    """Create or update a user's indicator chart template."""
    try:
        user_id = getattr(g, 'user_id', None)
        if not user_id:
            return jsonify({'code': 0, 'msg': 'Not authenticated', 'data': None}), 401
        ok, msg, saved = save_chart_template_service(int(user_id), request.get_json() or {})
        if not ok:
            return jsonify({'code': 0, 'msg': msg, 'data': None}), 400
        return jsonify({'code': 1, 'msg': msg, 'data': saved})
    except Exception as e:
        logger.error(f"save_chart_template failed: {e}")
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500
@user_blp.route('/chart-templates', methods=['DELETE'])
@login_required
def delete_chart_template():
    """Delete a user's chart template by id."""
    try:
        user_id = getattr(g, 'user_id', None)
        if not user_id:
            return jsonify({'code': 0, 'msg': 'Not authenticated', 'data': None}), 401
        ok, msg, data = delete_chart_template_service(int(user_id), request.args.get('template_id'))
        if not ok:
            return jsonify({'code': 0, 'msg': msg, 'data': None}), 400
        return jsonify({'code': 1, 'msg': msg, 'data': data})
    except Exception as e:
        logger.error(f"delete_chart_template failed: {e}")
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500
@user_blp.route('/notification-settings/test', methods=['POST'])
@login_required
def test_notification_settings():
    """Send a test notification using saved notification settings."""
    try:
        user_id = getattr(g, 'user_id', None)
        if not user_id:
            return jsonify({'code': 0, 'msg': 'Not authenticated', 'data': None}), 401
        accept = (request.headers.get('Accept-Language') or '') + ' ' + (request.headers.get('X-Locale') or '')
        ok, msg, data = send_test_notification(int(user_id), accept)
        return jsonify({'code': 1 if ok else 0, 'msg': msg, 'data': data})
    except Exception as e:
        logger.error(f"test_notification_settings failed: {e}")
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500
@user_blp.route('/change-password', methods=['POST'])
@login_required
def change_password():
    """Change current user's password."""
    try:
        user_id = getattr(g, 'user_id', None)
        if not user_id:
            return jsonify({'code': 0, 'msg': 'Not authenticated', 'data': None}), 401
        data = request.get_json() or {}
        ok, msg, status = change_user_password(
            int(user_id),
            data.get('old_password', ''),
            data.get('new_password', ''),
        )
        return jsonify({'code': 1 if ok else 0, 'msg': msg, 'data': None}), status if not ok else 200
    except ValueError as e:
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 400
    except Exception as e:
        logger.error(f"change_password failed: {e}")
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500
# ==================== System Overview (Admin) ====================

def _safe_json_loads(s, default=None):
    """Safely parse JSON string."""
    if not s:
        return default
    if isinstance(s, dict):
        return s
    try:
        return json.loads(s)
    except Exception:
        return default


def _strategy_v2_admin_metadata(
    *,
    strategy_type: str,
    trading_config: dict,
    source_id: int = 0,
    source_name: str = "",
    source_asset_type: str = "",
    source_template_key: str = "",
    source_metadata: dict | None = None,
    fallback_symbol: str = "",
    fallback_market: str = "",
    fallback_market_type: str = "",
    fallback_frequency: str = "",
) -> dict:
    """Build the admin-facing Strategy API V2 contract summary."""
    config = _safe_json_loads(trading_config, {}) or {}
    source_meta = _safe_json_loads(source_metadata, {}) or {}
    manifest = config.get("strategy_manifest") or source_meta.get("strategy_manifest") or {}
    api_version = int(config.get("api_version") or manifest.get("apiVersion") or 0)
    is_v2 = str(strategy_type or "").strip().lower() == "strategyv2" or api_version == 2

    template_key = str(source_template_key or "").strip()
    source_origin = str(source_meta.get("source") or "").strip().lower()
    is_robot = template_key.startswith("robot_v2_") or source_origin == "robot_builder"
    manifest_type = str(manifest.get("strategyType") or "").strip().lower()
    is_portfolio = (
        not is_robot
        and (
            manifest_type == "portfolio"
            or str(source_asset_type or "").strip().lower() == "portfolio_strategy"
        )
    )
    strategy_class = "robot" if is_robot else ("portfolio" if is_portfolio else "cta")

    universe = manifest.get("universe") or {}
    instruments = [item for item in (universe.get("instruments") or []) if isinstance(item, dict)]
    symbols = [str(item.get("symbol") or "").strip() for item in instruments]
    symbols = [value for value in symbols if value]
    if not symbols and fallback_symbol and not str(fallback_symbol).startswith(("basket:", "universe:")):
        symbols = [str(fallback_symbol)]
    universe_reference = str(universe.get("reference") or "").strip()
    if not universe_reference and str(fallback_symbol or "").startswith("universe:"):
        universe_reference = str(fallback_symbol).split(":", 1)[1]
    instrument_count = len(instruments)
    if not instrument_count and str(fallback_symbol or "").startswith("basket:"):
        try:
            instrument_count = int(str(fallback_symbol).split(":", 1)[1])
        except (TypeError, ValueError):
            instrument_count = 0
    if not instrument_count and symbols:
        instrument_count = len(symbols)

    markets = [str(value) for value in (manifest.get("markets") or []) if value]
    if not markets and fallback_market:
        markets = [str(fallback_market)]
    market_types = sorted({
        str(item.get("market_type") or "").strip().lower()
        for item in instruments
        if str(item.get("market_type") or "").strip()
    })
    if not market_types and fallback_market_type:
        market_types = [str(fallback_market_type).strip().lower()]

    primary_frequency = str(manifest.get("primaryFrequency") or fallback_frequency or "").strip()
    driving_frequency = str(
        manifest.get("drivingFrequency") or primary_frequency
    ).strip()
    frequencies = list(
        manifest.get("frequencies")
        or ([driving_frequency] if driving_frequency else [])
    )
    schedules = [item for item in (manifest.get("schedules") or []) if isinstance(item, dict)]
    normalized_source_id = int(source_id or config.get("script_source_id") or 0)
    contract_ready = bool(
        is_v2
        and api_version == 2
        and normalized_source_id > 0
        and int(manifest.get("apiVersion") or 0) == 2
    )
    return {
        "api_version": api_version,
        "is_strategy_v2": is_v2,
        "contract_ready": contract_ready,
        "source_id": normalized_source_id,
        "source_name": str(source_name or "").strip(),
        "source_asset_type": str(source_asset_type or "").strip(),
        "template_key": template_key,
        "strategy_class": strategy_class,
        "universe_kind": str(universe.get("kind") or ("reference" if universe_reference else "static")),
        "universe_reference": universe_reference,
        "instrument_count": instrument_count,
        "universe_symbols": symbols,
        "markets": markets,
        "market_types": market_types,
        "primary_frequency": primary_frequency,
        "driving_frequency": driving_frequency,
        "frequencies": frequencies,
        "schedule_count": len(schedules),
        "warmup_bars": int(manifest.get("warmupBars") or 0),
        "leverage_allowed": bool(manifest.get("leverageAllowed")),
        "max_leverage": float(manifest.get("maxLeverage") or 1),
    }


def _strategy_exchange_display_name(
    exchange_config: dict,
    *,
    credential_map: dict,
    user_id: int = 0,
) -> str:
    """Resolve exchange label for admin strategy lists.

    Strategies often persist ``exchange_config`` as ``{credential_id: N}`` only
  (API secrets live in ``qd_exchange_credentials``).  Read inline ``exchange_id``
    first, then the credential row's ``exchange_id``, then ``resolve_exchange_config``
    as a last resort.
    """
    if not isinstance(exchange_config, dict):
        return ''

    direct = (
        exchange_config.get('exchange_id')
        or exchange_config.get('exchange')
        or exchange_config.get('broker')
        or ''
    )
    direct = str(direct or '').strip()
    if direct:
        return direct

    cred_id = exchange_config.get('credential_id') or exchange_config.get('credentials_id')
    if cred_id:
        try:
            row = credential_map.get(int(cred_id))
        except (TypeError, ValueError):
            row = None
        if row:
            ex = str(row.get('exchange_id') or '').strip()
            if ex:
                return ex

        try:
            from app.services.exchange_execution import resolve_exchange_config

            resolved = resolve_exchange_config(exchange_config, user_id=int(user_id or 1))
            ex = str(resolved.get('exchange_id') or resolved.get('exchange') or '').strip()
            if ex:
                return ex
        except Exception:
            pass

    return ''


def _batch_load_credential_exchange_map(credential_ids: set) -> dict:
    """Map credential id -> {id, exchange_id, name} for display (no decrypt)."""
    if not credential_ids:
        return {}
    ids = sorted({int(i) for i in credential_ids if i})
    if not ids:
        return {}
    placeholders = ','.join(['?'] * len(ids))
    credential_map = {}
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            f"""
            SELECT id, exchange_id, name
            FROM qd_exchange_credentials
            WHERE id IN ({placeholders})
            """,
            tuple(ids),
        )
        for row in (cur.fetchall() or []):
            credential_map[int(row['id'])] = dict(row)
        cur.close()
    return credential_map


@user_blp.route('/system-strategies', methods=['GET'])
@login_required
@admin_required
def get_system_strategies():
    """
    Get all strategies across the entire system (admin only).
    Returns strategy details with user info, positions, PnL, indicators, etc.

    Query params:
        page: int (default 1)
        page_size: int (default 20, max 100)
        status: str (optional, filter by status: running/stopped/all)
        execution_mode: str (optional, live/signal; omit or all for any)
        strategy_class: str (optional, cta/portfolio/robot; omit or all for any)
        search: str (optional, search by strategy name/symbol/username/id)
        strategy_id: int (optional, exact strategy id)
        user_id: int (optional, exact owner user id)
        sort_by: str (optional, whitelist; default status+updated_at)
        sort_order: str (optional, asc or desc; default desc when sort_by set)
    """
    try:
        page = request.args.get('page', 1, type=int)
        page_size = request.args.get('page_size', 20, type=int)
        status_filter = request.args.get('status', '', type=str).strip().lower()
        execution_filter = request.args.get('execution_mode', '', type=str).strip().lower()
        strategy_class_filter = request.args.get('strategy_class', '', type=str).strip().lower()
        search = request.args.get('search', '', type=str).strip()
        strategy_id_filter = _parse_positive_int(request.args.get('strategy_id'))
        user_id_filter = _parse_positive_int(request.args.get('user_id'))
        sort_by = request.args.get('sort_by', '', type=str).strip().lower()
        sort_order = request.args.get('sort_order', 'desc', type=str).strip().lower()
        if sort_order not in ('asc', 'desc'):
            sort_order = 'desc'
        page_size = min(100, max(1, page_size))
        offset = (page - 1) * page_size

        sort_sql_map = {
            'id': 's.id',
            'updated_at': 's.updated_at',
            'created_at': 's.created_at',
            'initial_capital': 's.initial_capital',
            'strategy_name': 's.strategy_name',
            'symbol': 's.symbol',
            'status': 's.status',
            'execution_mode': 's.execution_mode',
            'leverage': 's.leverage',
        }
        sort_expr_map = {
            'total_pnl': (
                "(COALESCE((SELECT SUM(unrealized_pnl) FROM qd_strategy_positions p WHERE p.strategy_id = s.id), 0)"
                " + COALESCE((SELECT SUM(COALESCE(t.profit, 0) - COALESCE(t.commission_quote, t.commission, 0)) FROM qd_strategy_trades t WHERE t.strategy_id = s.id), 0)"
                " + COALESCE((SELECT SUM(COALESCE(f.amount, 0)) FROM qd_strategy_funding_fees f WHERE f.strategy_id = s.id), 0)"
                " + COALESCE((SELECT SUM(COALESCE(a.amount, 0)) FROM qd_strategy_broker_activities a WHERE a.strategy_id = s.id), 0))"
            ),
            'trade_count': '(SELECT COUNT(*) FROM qd_strategy_trades t WHERE t.strategy_id = s.id)',
            'position_count': '(SELECT COUNT(*) FROM qd_strategy_positions p WHERE p.strategy_id = s.id)',
            'total_equity': (
                'COALESCE((SELECT SUM(equity) FROM qd_strategy_positions p WHERE p.strategy_id = s.id), 0)'
            ),
        }
        direction = 'ASC' if sort_order == 'asc' else 'DESC'

        source_join_sql = """
            LEFT JOIN qd_script_sources src
              ON src.id = CASE
                WHEN COALESCE((s.trading_config::jsonb)->>'script_source_id', '') ~ '^[0-9]+$'
                THEN ((s.trading_config::jsonb)->>'script_source_id')::INTEGER
                ELSE NULL
              END
        """
        v2_expr = "(s.strategy_type = 'StrategyV2' OR COALESCE((s.trading_config::jsonb)->>'api_version', '0') = '2')"
        robot_expr = "(LEFT(COALESCE(src.template_key, ''), 9) = 'robot_v2_' OR COALESCE(src.metadata->>'source', '') = 'robot_builder')"
        portfolio_expr = "(NOT {robot} AND (COALESCE(src.asset_type, '') = 'portfolio_strategy' OR COALESCE((s.trading_config::jsonb)->'strategy_manifest'->>'strategyType', '') = 'portfolio'))".format(robot=robot_expr)
        cta_expr = "({v2} AND NOT {robot} AND NOT {portfolio})".format(
            v2=v2_expr,
            robot=robot_expr,
            portfolio=portfolio_expr,
        )

        with get_db_connection() as db:
            cur = db.cursor()

            # Build WHERE clause
            conditions = []
            params = []

            if status_filter and status_filter != 'all':
                conditions.append("s.status = ?")
                params.append(status_filter)

            if execution_filter in ('live', 'signal'):
                conditions.append("s.execution_mode = ?")
                params.append(execution_filter)

            if strategy_class_filter == 'robot':
                conditions.append(robot_expr)
            elif strategy_class_filter == 'portfolio':
                conditions.append(portfolio_expr)
            elif strategy_class_filter == 'cta':
                conditions.append(cta_expr)

            if strategy_id_filter > 0:
                conditions.append("s.id = ?")
                params.append(strategy_id_filter)

            if user_id_filter > 0:
                conditions.append("s.user_id = ?")
                params.append(user_id_filter)

            if search:
                like_val = f"%{search}%"
                if search.isdigit():
                    num = int(search)
                    conditions.append(
                        "(s.id = ? OR s.user_id = ? OR s.strategy_name ILIKE ? OR s.symbol ILIKE ? "
                        "OR u.username ILIKE ? OR u.nickname ILIKE ? OR src.name ILIKE ?)"
                    )
                    params.extend([num, num, like_val, like_val, like_val, like_val, like_val])
                else:
                    conditions.append(
                        "(s.strategy_name ILIKE ? OR s.symbol ILIKE ? OR u.username ILIKE ? OR u.nickname ILIKE ?"
                        " OR src.name ILIKE ? OR CAST(s.id AS TEXT) ILIKE ? OR CAST(s.user_id AS TEXT) ILIKE ?)"
                    )
                    params.extend([like_val, like_val, like_val, like_val, like_val, like_val, like_val])

            where_clause = ""
            if conditions:
                where_clause = "WHERE " + " AND ".join(conditions)

            if sort_by in sort_sql_map:
                order_clause = f"ORDER BY {sort_sql_map[sort_by]} {direction}, s.id DESC"
            elif sort_by in sort_expr_map:
                order_clause = f"ORDER BY {sort_expr_map[sort_by]} {direction}, s.id DESC"
            else:
                order_clause = "ORDER BY s.status DESC, s.updated_at DESC, s.id DESC"

            # Get total count
            count_sql = f"""
                SELECT COUNT(*) as cnt
                FROM qd_strategies_trading s
                LEFT JOIN qd_users u ON u.id = s.user_id
                {source_join_sql}
                {where_clause}
            """
            cur.execute(count_sql, tuple(params))
            total = cur.fetchone()['cnt']

            # Get strategies with user info
            query_sql = f"""
                SELECT 
                    s.id,
                    s.user_id,
                    s.strategy_name,
                    s.strategy_type,
                    s.market_category,
                    s.execution_mode,
                    s.status,
                    s.symbol,
                    s.timeframe,
                    s.initial_capital,
                    s.leverage,
                    s.market_type,
                    s.trading_config,
                    s.exchange_config,
                    s.created_at,
                    s.updated_at,
                    src.id AS source_id,
                    src.name AS source_name,
                    src.asset_type AS source_asset_type,
                    src.template_key AS source_template_key,
                    src.metadata AS source_metadata,
                    u.username,
                    u.nickname
                FROM qd_strategies_trading s
                LEFT JOIN qd_users u ON u.id = s.user_id
                {source_join_sql}
                {where_clause}
                {order_clause}
                LIMIT ? OFFSET ?
            """
            cur.execute(query_sql, tuple(params) + (page_size, offset))
            strategies = cur.fetchall() or []

            # Collect strategy IDs
            strategy_ids = [s['id'] for s in strategies]

            # Batch load positions for these strategies
            positions_map = {}
            if strategy_ids:
                placeholders = ','.join(['?'] * len(strategy_ids))
                cur.execute(
                    f"""
                    SELECT strategy_id, symbol, side, size, entry_price, current_price, 
                           unrealized_pnl, pnl_percent, equity, updated_at
                    FROM qd_strategy_positions
                    WHERE strategy_id IN ({placeholders})
                    ORDER BY strategy_id, updated_at DESC
                    """,
                    tuple(strategy_ids)
                )
                for pos in (cur.fetchall() or []):
                    sid = pos['strategy_id']
                    if sid not in positions_map:
                        positions_map[sid] = []
                    positions_map[sid].append(dict(pos))

            # Batch load recent trade stats (realized PnL per strategy)
            trade_stats_map = {}
            if strategy_ids:
                placeholders = ','.join(['?'] * len(strategy_ids))
                cur.execute(
                    f"""
                    SELECT t.strategy_id,
                           COUNT(*) as trade_count,
                           COALESCE(SUM(COALESCE(t.profit, 0) - COALESCE(t.commission_quote, t.commission, 0)), 0)
                           + COALESCE((SELECT SUM(COALESCE(f.amount, 0))
                                       FROM qd_strategy_funding_fees f
                                       WHERE f.strategy_id = t.strategy_id), 0)
                           + COALESCE((SELECT SUM(COALESCE(a.amount, 0))
                                       FROM qd_strategy_broker_activities a
                                       WHERE a.strategy_id = t.strategy_id), 0) AS total_realized_pnl
                    FROM qd_strategy_trades t
                    WHERE t.strategy_id IN ({placeholders})
                    GROUP BY t.strategy_id
                    """,
                    tuple(strategy_ids)
                )
                for row in (cur.fetchall() or []):
                    trade_stats_map[row['strategy_id']] = {
                        'trade_count': row['trade_count'],
                        'total_realized_pnl': float(row['total_realized_pnl'] or 0)
                    }

            cur.close()

        # Build response; batch-resolve exchange names for credential_id-only configs.
        cred_ids = set()
        for s in strategies:
            ec = _safe_json_loads(s.get('exchange_config'), {})
            cid = ec.get('credential_id') or ec.get('credentials_id')
            if cid:
                try:
                    cred_ids.add(int(cid))
                except (TypeError, ValueError):
                    pass
        credential_map = _batch_load_credential_exchange_map(cred_ids)

        items = []
        for s in strategies:
            sid = s['id']
            trading_config = _safe_json_loads(s.get('trading_config'), {})
            exchange_config = _safe_json_loads(s.get('exchange_config'), {})
            source_metadata = _safe_json_loads(s.get('source_metadata'), {})

            admin_metadata = _strategy_v2_admin_metadata(
                strategy_type=s.get('strategy_type') or '',
                trading_config=trading_config,
                source_id=int(s.get('source_id') or 0),
                source_name=s.get('source_name') or '',
                source_asset_type=s.get('source_asset_type') or '',
                source_template_key=s.get('source_template_key') or '',
                source_metadata=source_metadata,
                fallback_symbol=s.get('symbol') or '',
                fallback_market=s.get('market_category') or '',
                fallback_market_type=s.get('market_type') or '',
                fallback_frequency=s.get('timeframe') or '',
            )

            indicator_name = str(trading_config.get('display_name') or '')

            # Extract exchange name (inline config or saved credential reference).
            exchange_name = _strategy_exchange_display_name(
                exchange_config,
                credential_map=credential_map,
                user_id=int(s.get('user_id') or 0),
            )

            # Positions data
            positions = positions_map.get(sid, [])
            total_unrealized_pnl = sum(float(p.get('unrealized_pnl') or 0) for p in positions)
            total_equity = sum(float(p.get('equity') or 0) for p in positions)
            position_count = len(positions)

            # Trade stats
            trade_stats = trade_stats_map.get(sid, {'trade_count': 0, 'total_realized_pnl': 0})
            total_realized_pnl = trade_stats['total_realized_pnl']
            trade_count = trade_stats['trade_count']

            # Calculate total PnL and ROI
            initial_capital = float(s.get('initial_capital') or 0)
            total_pnl = total_unrealized_pnl + total_realized_pnl
            roi = (total_pnl / initial_capital * 100) if initial_capital > 0 else 0

            # Timestamps are emitted as UTC ISO by SafeJSONProvider; pass
            # datetime objects straight through.
            created_at = s.get('created_at')
            updated_at = s.get('updated_at')

            items.append({
                'id': sid,
                'user_id': s['user_id'],
                'username': s.get('username') or '',
                'nickname': s.get('nickname') or '',
                'strategy_name': s.get('strategy_name') or '',
                'strategy_type': s.get('strategy_type') or '',
                'market_category': s.get('market_category') or '',
                'execution_mode': s.get('execution_mode') or '',
                'status': s.get('status') or 'stopped',
                'symbol': s.get('symbol') or '',
                'timeframe': s.get('timeframe') or '',
                'initial_capital': initial_capital,
                'leverage': int(s.get('leverage') or 1),
                'market_type': s.get('market_type') or '',
                'indicator_name': indicator_name,
                'exchange_name': exchange_name,
                'data_poll_seconds': trading_config.get('data_poll_seconds') or 5,
                'position_count': position_count,
                'total_unrealized_pnl': round(total_unrealized_pnl, 4),
                'total_realized_pnl': round(total_realized_pnl, 4),
                'total_pnl': round(total_pnl, 4),
                'total_equity': round(total_equity, 4),
                'roi': round(roi, 2),
                'trade_count': trade_count,
                'positions': positions,
                'created_at': created_at,
                'updated_at': updated_at,
                **admin_metadata,
            })

        # Compute summary stats from all matched strategies (not just current page items).
        with get_db_connection() as db:
            cur = db.cursor()

            # Aggregate strategy counts/capital by execution mode and running status.
            agg_sql = f"""
                SELECT
                    COUNT(*) AS total_strategies,
                    COALESCE(SUM(s.initial_capital), 0) AS total_capital,
                    COALESCE(SUM(CASE WHEN s.status = 'running' THEN 1 ELSE 0 END), 0) AS running_strategies,
                    COALESCE(SUM(CASE WHEN s.execution_mode = 'live' THEN 1 ELSE 0 END), 0) AS live_strategies,
                    COALESCE(SUM(CASE WHEN s.execution_mode = 'signal' THEN 1 ELSE 0 END), 0) AS signal_strategies,
                    COALESCE(SUM(CASE WHEN {v2_expr} THEN 1 ELSE 0 END), 0) AS v2_strategies,
                    COALESCE(SUM(CASE WHEN NOT {v2_expr} THEN 1 ELSE 0 END), 0) AS legacy_strategies,
                    COALESCE(SUM(CASE WHEN {cta_expr} THEN 1 ELSE 0 END), 0) AS cta_strategies,
                    COALESCE(SUM(CASE WHEN {portfolio_expr} THEN 1 ELSE 0 END), 0) AS portfolio_strategies,
                    COALESCE(SUM(CASE WHEN {robot_expr} THEN 1 ELSE 0 END), 0) AS robot_strategies,
                    COALESCE(SUM(CASE WHEN {v2_expr}
                        AND COALESCE((s.trading_config::jsonb)->>'api_version', '0') = '2'
                        AND COALESCE((s.trading_config::jsonb)->'strategy_manifest'->>'apiVersion', '0') = '2'
                        AND src.id IS NOT NULL THEN 1 ELSE 0 END), 0) AS contract_ready_strategies,
                    COALESCE(SUM(CASE WHEN s.status = 'running' AND s.execution_mode = 'live' THEN 1 ELSE 0 END), 0) AS running_live_strategies,
                    COALESCE(SUM(CASE WHEN s.status = 'running' AND s.execution_mode = 'signal' THEN 1 ELSE 0 END), 0) AS running_signal_strategies,
                    COALESCE(SUM(CASE WHEN s.execution_mode = 'live' THEN s.initial_capital ELSE 0 END), 0) AS live_capital,
                    COALESCE(SUM(CASE WHEN s.execution_mode = 'signal' THEN s.initial_capital ELSE 0 END), 0) AS signal_capital
                FROM qd_strategies_trading s
                LEFT JOIN qd_users u ON u.id = s.user_id
                {source_join_sql}
                {where_clause}
            """
            cur.execute(agg_sql, tuple(params))
            agg_row = cur.fetchone() or {}

            # Aggregate unrealized pnl from current positions.
            unreal_sql = f"""
                SELECT COALESCE(SUM(p.unrealized_pnl), 0) AS total_unrealized,
                       COALESCE(SUM(CASE WHEN s.execution_mode = 'live' THEN p.unrealized_pnl ELSE 0 END), 0) AS live_unrealized,
                       COALESCE(SUM(CASE WHEN s.execution_mode = 'signal' THEN p.unrealized_pnl ELSE 0 END), 0) AS signal_unrealized
                FROM qd_strategy_positions p
                JOIN qd_strategies_trading s ON s.id = p.strategy_id
                LEFT JOIN qd_users u ON u.id = s.user_id
                {source_join_sql}
                {where_clause}
            """
            cur.execute(unreal_sql, tuple(params))
            unreal_row = cur.fetchone() or {}

            # Aggregate realized pnl from trade history.
            realized_sql = f"""
                SELECT COALESCE(SUM(COALESCE(t.profit, 0) - COALESCE(t.commission_quote, t.commission, 0)), 0) AS total_realized,
                       COALESCE(SUM(CASE WHEN s.execution_mode = 'live' THEN COALESCE(t.profit, 0) - COALESCE(t.commission_quote, t.commission, 0) ELSE 0 END), 0) AS live_realized,
                       COALESCE(SUM(CASE WHEN s.execution_mode = 'signal' THEN COALESCE(t.profit, 0) - COALESCE(t.commission_quote, t.commission, 0) ELSE 0 END), 0) AS signal_realized
                FROM qd_strategy_trades t
                JOIN qd_strategies_trading s ON s.id = t.strategy_id
                LEFT JOIN qd_users u ON u.id = s.user_id
                {source_join_sql}
                {where_clause}
            """
            cur.execute(realized_sql, tuple(params))
            realized_row = cur.fetchone() or {}
            funding_sql = f"""
                SELECT COALESCE(SUM(COALESCE(f.amount, 0)), 0) AS total_realized,
                       COALESCE(SUM(CASE WHEN s.execution_mode = 'live' THEN COALESCE(f.amount, 0) ELSE 0 END), 0) AS live_realized,
                       COALESCE(SUM(CASE WHEN s.execution_mode = 'signal' THEN COALESCE(f.amount, 0) ELSE 0 END), 0) AS signal_realized
                FROM qd_strategy_funding_fees f
                JOIN qd_strategies_trading s ON s.id = f.strategy_id
                LEFT JOIN qd_users u ON u.id = s.user_id
                {source_join_sql}
                {where_clause}
            """
            cur.execute(funding_sql, tuple(params))
            funding_row = cur.fetchone() or {}
            broker_activity_sql = f"""
                SELECT COALESCE(SUM(COALESCE(a.amount, 0)), 0) AS total_realized,
                       COALESCE(SUM(CASE WHEN s.execution_mode = 'live' THEN COALESCE(a.amount, 0) ELSE 0 END), 0) AS live_realized,
                       COALESCE(SUM(CASE WHEN s.execution_mode = 'signal' THEN COALESCE(a.amount, 0) ELSE 0 END), 0) AS signal_realized
                FROM qd_strategy_broker_activities a
                JOIN qd_strategies_trading s ON s.id = a.strategy_id
                LEFT JOIN qd_users u ON u.id = s.user_id
                {source_join_sql}
                {where_clause}
            """
            cur.execute(broker_activity_sql, tuple(params))
            broker_activity_row = cur.fetchone() or {}
            cur.close()

        total_capital = float(agg_row.get('total_capital') or 0)
        total_running = int(agg_row.get('running_strategies') or 0)
        total_system_pnl = float(unreal_row.get('total_unrealized') or 0) + float(realized_row.get('total_realized') or 0) + float(funding_row.get('total_realized') or 0) + float(broker_activity_row.get('total_realized') or 0)
        live_pnl = float(unreal_row.get('live_unrealized') or 0) + float(realized_row.get('live_realized') or 0) + float(funding_row.get('live_realized') or 0) + float(broker_activity_row.get('live_realized') or 0)
        signal_pnl = float(unreal_row.get('signal_unrealized') or 0) + float(realized_row.get('signal_realized') or 0) + float(funding_row.get('signal_realized') or 0) + float(broker_activity_row.get('signal_realized') or 0)
        live_capital = float(agg_row.get('live_capital') or 0)

        return jsonify({
            'code': 1,
            'msg': 'success',
            'data': {
                'items': items,
                'total': total,
                'page': page,
                'page_size': page_size,
                'summary': {
                    'total_strategies': int(agg_row.get('total_strategies') or total),
                    'running_strategies': total_running,
                    'total_capital': round(total_capital, 2),
                    'total_pnl': round(total_system_pnl, 4),
                    'total_roi': round((total_system_pnl / total_capital * 100) if total_capital > 0 else 0, 2),
                    'live_strategies': int(agg_row.get('live_strategies') or 0),
                    'signal_strategies': int(agg_row.get('signal_strategies') or 0),
                    'v2_strategies': int(agg_row.get('v2_strategies') or 0),
                    'legacy_strategies': int(agg_row.get('legacy_strategies') or 0),
                    'contract_ready_strategies': int(agg_row.get('contract_ready_strategies') or 0),
                    'cta_strategies': int(agg_row.get('cta_strategies') or 0),
                    'portfolio_strategies': int(agg_row.get('portfolio_strategies') or 0),
                    'robot_strategies': int(agg_row.get('robot_strategies') or 0),
                    'running_live_strategies': int(agg_row.get('running_live_strategies') or 0),
                    'running_signal_strategies': int(agg_row.get('running_signal_strategies') or 0),
                    'live_capital': round(live_capital, 2),
                    'signal_capital': round(float(agg_row.get('signal_capital') or 0), 2),
                    'live_pnl': round(live_pnl, 4),
                    'live_roi': round((live_pnl / live_capital * 100) if live_capital > 0 else 0, 2),
                    'signal_pnl': round(signal_pnl, 4)
                }
            }
        })
    except Exception as e:
        logger.error(f"get_system_strategies failed: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@user_blp.route('/system-strategies/toggle', methods=['POST'])
@login_required
@admin_required
def admin_toggle_system_strategy():
    """
    Start or stop any strategy (admin only).

    Query/body:
        id / strategy_id: strategy primary key
        action: optional ``start`` | ``stop``; omit to toggle current status
    """
    try:
        data = request.get_json(silent=True) or {}
        strategy_id = (
            request.args.get('id', type=int)
            or data.get('strategy_id')
            or data.get('id')
        )
        try:
            strategy_id = int(strategy_id)
        except (TypeError, ValueError):
            strategy_id = 0
        if strategy_id <= 0:
            return jsonify({'code': 0, 'msg': 'Missing strategy id', 'data': None}), 400

        action = str(data.get('action') or request.args.get('action') or '').strip().lower()

        from app import get_trading_executor
        from app.routes.strategy import get_strategy_service

        svc = get_strategy_service()
        st = svc.get_strategy(strategy_id)
        if not st:
            return jsonify({'code': 0, 'msg': 'Strategy not found', 'data': None}), 404

        strategy_type = svc.get_strategy_type(strategy_id)
        if strategy_type == 'PromptBasedStrategy':
            return jsonify({
                'code': 0,
                'msg': 'AI strategy has been removed; cannot start/stop',
                'data': None,
            }), 400

        current = str(st.get('status') or 'stopped').strip().lower()
        if action in ('start', 'running', 'run'):
            target = 'running'
        elif action in ('stop', 'stopped', 'halt'):
            target = 'stopped'
        else:
            target = 'stopped' if current == 'running' else 'running'

        executor = get_trading_executor()
        admin_user_id = getattr(g, 'user_id', None)

        if target == 'running':
            svc.update_strategy_status(strategy_id, 'running')
            ok = executor.start_strategy(strategy_id)
            if not ok:
                svc.update_strategy_status(strategy_id, 'stopped')
                detail = getattr(executor, '_last_start_failure', '') or ''
                msg = 'Failed to start strategy executor'
                if detail:
                    msg = f'{msg}: {detail}'
                return jsonify({'code': 0, 'msg': msg, 'data': {'status': 'stopped'}}), 500
            alive, hint = executor.wait_strategy_running(strategy_id, timeout=3.0)
            if not alive:
                svc.update_strategy_status(strategy_id, 'stopped')
                msg = f'Strategy executor exited immediately after start: {hint}'
                return jsonify({
                    'code': 0,
                    'msg': msg,
                    'data': {'id': strategy_id, 'status': 'stopped', 'detail': hint},
                }), 500
            logger.info(
                'Admin %s started strategy %s (owner user_id=%s)',
                admin_user_id, strategy_id, st.get('user_id'),
            )
        else:
            status_ok = svc.update_strategy_status(strategy_id, 'stopped')
            if not status_ok:
                return jsonify({
                    'code': 0,
                    'msg': 'Failed to persist stopped status; strategy may resume on restart',
                    'data': None,
                }), 500
            executor_ok = executor.stop_strategy(strategy_id, persist_status=False)
            if not executor_ok:
                return jsonify({
                    'code': 0,
                    'msg': 'Stopped status was saved, but runtime thread stop failed; please refresh and retry',
                    'data': {'id': strategy_id, 'status': 'stopped'},
                }), 500
            latest = svc.get_strategy(strategy_id)
            if not latest or str(latest.get('status') or '').strip().lower() != 'stopped':
                return jsonify({
                    'code': 0,
                    'msg': 'Stop verification failed; strategy status is not stopped',
                    'data': {'id': strategy_id, 'status': latest.get('status') if latest else None},
                }), 500
            logger.info(
                'Admin %s stopped strategy %s (owner user_id=%s)',
                admin_user_id, strategy_id, st.get('user_id'),
            )

        return jsonify({
            'code': 1,
            'msg': 'Started successfully' if target == 'running' else 'Stopped successfully',
            'data': {'id': strategy_id, 'status': target},
        })
    except Exception as e:
        logger.error(f"admin_toggle_system_strategy failed: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@user_blp.route('/system-strategies/delete', methods=['DELETE'])
@login_required
@admin_required
def admin_delete_system_strategy():
    """Delete any strategy from the system overview (admin only)."""
    try:
        strategy_id = request.args.get('id', type=int)
        if not strategy_id:
            data = request.get_json(silent=True) or {}
            try:
                strategy_id = int(data.get('strategy_id') or data.get('id') or 0)
            except (TypeError, ValueError):
                strategy_id = 0
        if strategy_id <= 0:
            return jsonify({'code': 0, 'msg': 'Missing strategy id', 'data': None}), 400

        from app import get_trading_executor
        from app.routes.strategy import get_strategy_service

        svc = get_strategy_service()
        st = svc.get_strategy(strategy_id)
        if not st:
            return jsonify({'code': 0, 'msg': 'Strategy not found', 'data': None}), 404

        if str(st.get('status') or '').strip().lower() == 'running':
            svc.update_strategy_status(strategy_id, 'stopped')
            get_trading_executor().stop_strategy(strategy_id, persist_status=False)

        ok = svc.delete_strategy(strategy_id)
        if not ok:
            return jsonify({'code': 0, 'msg': 'Failed to delete strategy', 'data': None}), 500

        logger.info(
            'Admin %s deleted strategy %s (owner user_id=%s)',
            getattr(g, 'user_id', None), strategy_id, st.get('user_id'),
        )
        return jsonify({'code': 1, 'msg': 'Deleted successfully', 'data': {'id': strategy_id}})
    except Exception as e:
        logger.error(f"admin_delete_system_strategy failed: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


# ==================== Admin Orders ====================


def _ensure_usdt_admin_columns():
    """Best-effort: extend qd_usdt_orders with admin-audit columns introduced
    by the manual-confirm flow. ``ADD COLUMN IF NOT EXISTS`` is idempotent
    on PostgreSQL, so this is effectively a no-op after the first hit.

    Failures are swallowed (logged at debug level) so a running DB user
    without DDL privileges doesn't block the read paths; the SELECTs
    further down use ``information_schema`` checks or COALESCE to tolerate
    the columns being absent.
    """
    try:
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                "ALTER TABLE qd_usdt_orders ADD COLUMN IF NOT EXISTS admin_note TEXT DEFAULT NULL"
            )
            cur.execute(
                "ALTER TABLE qd_usdt_orders ADD COLUMN IF NOT EXISTS manual_confirmed_by INTEGER DEFAULT NULL"
            )
            db.commit()
            cur.close()
    except Exception as exc:
        logger.debug("ensure_usdt_admin_columns skipped: %s", exc)


@user_blp.route('/admin-orders', methods=['GET'])
@login_required
@admin_required
def get_admin_orders():
    """
    Get all orders across the system (admin only).
    Lists USDT on-chain membership orders only (qd_usdt_orders).

    Query params:
        page: int (default 1)
        page_size: int (default 20, max 100)
        status: str (optional, filter by status: paid/pending/confirmed/expired/all)
        search: str (optional, search by username/email)
    """
    try:
        page = request.args.get('page', 1, type=int)
        page_size = request.args.get('page_size', 20, type=int)
        status_filter = request.args.get('status', '', type=str).strip().lower()
        search = request.args.get('search', '', type=str).strip()
        page_size = min(100, max(1, page_size))
        offset = (page - 1) * page_size

        _ensure_usdt_admin_columns()

        with get_db_connection() as db:
            cur = db.cursor()

            usdt_conditions = []
            usdt_params = []

            if status_filter and status_filter != 'all':
                usdt_conditions.append("o.status = ?")
                usdt_params.append(status_filter)

            if search:
                usdt_conditions.append("(u.username ILIKE ? OR u.email ILIKE ? OR u.nickname ILIKE ?)")
                like_val = f"%{search}%"
                usdt_params.extend([like_val, like_val, like_val])

            usdt_where = ""
            if usdt_conditions:
                usdt_where = "WHERE " + " AND ".join(usdt_conditions)

            cur.execute(
                f"SELECT COUNT(*) as cnt FROM qd_usdt_orders o LEFT JOIN qd_users u ON u.id = o.user_id {usdt_where}",
                tuple(usdt_params)
            )
            total = cur.fetchone()['cnt']

            list_sql = f"""
                SELECT
                    o.id,
                    'usdt' AS order_type,
                    o.user_id,
                    u.username,
                    u.nickname,
                    u.email AS user_email,
                    o.plan,
                    o.amount_usdt AS amount,
                    'USDT' AS currency,
                    o.chain,
                    o.address,
                    o.tx_hash,
                    o.status,
                    o.matched_via,
                    o.admin_note,
                    o.manual_confirmed_by,
                    o.created_at,
                    o.paid_at,
                    o.confirmed_at,
                    o.expires_at
                FROM qd_usdt_orders o
                LEFT JOIN qd_users u ON u.id = o.user_id
                {usdt_where}
                ORDER BY o.created_at DESC
                LIMIT ? OFFSET ?
            """
            all_params = list(usdt_params) + [page_size, offset]
            cur.execute(list_sql, tuple(all_params))
            rows = cur.fetchall() or []

            # Summary stats
            cur.execute(
                f"""SELECT
                    COUNT(*) AS total_orders,
                    COALESCE(SUM(CASE WHEN status IN ('paid','confirmed') THEN 1 ELSE 0 END), 0) AS paid_orders,
                    COALESCE(SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END), 0) AS pending_orders,
                    COALESCE(SUM(CASE WHEN status IN ('expired','cancelled','failed') THEN 1 ELSE 0 END), 0) AS failed_orders,
                    COALESCE(SUM(CASE WHEN status IN ('paid','confirmed') THEN amount_usdt ELSE 0 END), 0) AS total_revenue
                FROM qd_usdt_orders"""
            )
            summary_row = cur.fetchone() or {}

            cur.close()

        items = []
        for row in rows:
            # SafeJSONProvider normalizes datetimes to UTC ISO; no manual
            # conversion needed.
            created_at = row.get('created_at')
            paid_at = row.get('paid_at')
            confirmed_at = row.get('confirmed_at')
            expires_at = row.get('expires_at')

            items.append({
                'id': row['id'],
                'order_type': row.get('order_type') or '',
                'user_id': row.get('user_id'),
                'username': row.get('username') or '',
                'nickname': row.get('nickname') or '',
                'user_email': row.get('user_email') or '',
                'plan': row.get('plan') or '',
                'amount': float(row.get('amount') or 0),
                'currency': row.get('currency') or '',
                'chain': row.get('chain') or '',
                'address': row.get('address') or '',
                'tx_hash': row.get('tx_hash') or '',
                'status': row.get('status') or '',
                'matched_via': row.get('matched_via') or '',
                'admin_note': row.get('admin_note') or '',
                'manual_confirmed_by': row.get('manual_confirmed_by'),
                'created_at': created_at,
                'paid_at': paid_at,
                'confirmed_at': confirmed_at,
                'expires_at': expires_at
            })

        return jsonify({
            'code': 1,
            'msg': 'success',
            'data': {
                'items': items,
                'total': total,
                'page': page,
                'page_size': page_size,
                'summary': {
                    'total_orders': int(summary_row.get('total_orders') or 0),
                    'paid_orders': int(summary_row.get('paid_orders') or 0),
                    'pending_orders': int(summary_row.get('pending_orders') or 0),
                    'failed_orders': int(summary_row.get('failed_orders') or 0),
                    'total_revenue': round(float(summary_row.get('total_revenue') or 0), 2)
                }
            }
        })
    except Exception as e:
        logger.error(f"get_admin_orders failed: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@user_blp.route('/admin-orders/<int:order_id>/manual-confirm', methods=['POST'])
@login_required
@admin_required
def manual_confirm_order(order_id: int):
    """
    Admin-only "rescue" lever for USDT orders.

    Use case: the buyer paid the correct amount to the correct receiving
    address, but the on-chain reconciler missed the transaction (RPC
    outage, exotic wallet, chain-specific edge case, off-chain mistake
    where the customer used a slightly different amount than the order
    suffix demanded, etc.). Without this endpoint the admin's only option
    is to ``UPDATE qd_usdt_orders ...`` by hand and then somehow trigger
    ``purchase_membership``; this surface does both atomically and leaves
    an audit trail.

    Body:
        {
            "tx_hash": "<on-chain tx hash>",     # required
            "note":    "<free-form audit note>"  # optional
        }

    Behavior:
        - Flips the order to 'confirmed'.
        - Stamps tx_hash + paid_at (if empty) + confirmed_at + admin_note
          + manual_confirmed_by + matched_via='manual_admin'.
        - Calls ``purchase_membership`` exactly once per order (idempotent
          on re-submit; already-confirmed orders only refresh the audit
          fields, no double-grant).
        - Refuses ``status='cancelled'`` orders so the admin doesn't
          accidentally resurrect a deliberately-cancelled refund.
    """
    try:
        admin_user_id = getattr(g, 'user_id', None)
        body = request.get_json(silent=True) or {}
        tx_hash = (body.get('tx_hash') or '').strip()
        note = (body.get('note') or '').strip()

        if not tx_hash:
            return jsonify({'code': 0, 'msg': 'missing_tx_hash', 'data': None}), 400
        if len(tx_hash) > 120:
            return jsonify({'code': 0, 'msg': 'tx_hash_too_long', 'data': None}), 400
        if len(note) > 1000:
            return jsonify({'code': 0, 'msg': 'note_too_long', 'data': None}), 400

        from app.services.billing_service import get_billing_service
        billing = get_billing_service()
        if not billing.is_billing_enabled():
            return jsonify({'code': 0, 'msg': 'billing_disabled', 'data': None}), 403

        _ensure_usdt_admin_columns()

        # Load order in a short read txn (don't hold a lock across the
        # billing call below; purchase_membership opens its own conn).
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                SELECT id, user_id, plan, status, chain
                FROM qd_usdt_orders WHERE id = ?
                """,
                (order_id,),
            )
            order = cur.fetchone()
            cur.close()

        if not order:
            return jsonify({'code': 0, 'msg': 'order_not_found', 'data': None}), 404

        current_status = (order.get('status') or '').lower()
        user_id = order.get('user_id')
        plan = order.get('plan')

        if current_status == 'cancelled':
            # Cancelled orders are deliberately retired; surfacing this
            # as an error forces the admin to recreate the order instead
            # of silently rescuing a refunded one.
            return jsonify({
                'code': 0,
                'msg': 'order_cancelled',
                'data': {'order_id': order_id, 'status': current_status},
            }), 400

        already_confirmed = current_status == 'confirmed'

        # Stamp confirmation + audit fields. COALESCE on paid_at /
        # confirmed_at means re-running this for amendments (e.g. fix a
        # typo in the tx hash) preserves the original timestamps.
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                UPDATE qd_usdt_orders
                SET status = 'confirmed',
                    tx_hash = ?,
                    paid_at = COALESCE(paid_at, NOW()),
                    confirmed_at = COALESCE(confirmed_at, NOW()),
                    admin_note = ?,
                    manual_confirmed_by = ?,
                    matched_via = 'manual_admin',
                    updated_at = NOW()
                WHERE id = ?
                """,
                (tx_hash, note or None, admin_user_id, order_id),
            )
            db.commit()
            cur.close()

        # Grant membership only when transitioning into 'confirmed' for
        # the first time; re-submits (already confirmed) should only
        # update the audit fields above, never grant another membership.
        billing_msg = ''
        if not already_confirmed:
            try:
                ok, billing_msg, _ = billing.purchase_membership(
                    int(user_id),
                    str(plan),
                    record_membership_order=False,
                    fulfillment_ref=f"manual_usdt:{order_id}:by_{admin_user_id}",
                )
                logger.info(
                    "[ManualConfirm] order=%s user=%s plan=%s admin=%s ok=%s msg=%s",
                    order_id, user_id, plan, admin_user_id, ok, billing_msg,
                )
                if not ok:
                    # Order row is already 'confirmed' at this point;
                    # surface the billing error so the admin knows to
                    # retry / dig in. We deliberately don't roll back the
                    # status because the on-chain payment IS real.
                    return jsonify({
                        'code': 0,
                        'msg': f'order_confirmed_but_billing_failed:{billing_msg}',
                        'data': {'order_id': order_id, 'billing_error': billing_msg},
                    }), 500
            except Exception as exc:
                logger.error(
                    "[ManualConfirm] billing exception order=%s err=%s",
                    order_id, exc, exc_info=True,
                )
                return jsonify({
                    'code': 0,
                    'msg': f'order_confirmed_but_billing_exception:{exc}',
                    'data': {'order_id': order_id},
                }), 500

        return jsonify({
            'code': 1,
            'msg': 'success',
            'data': {
                'order_id': order_id,
                'user_id': user_id,
                'plan': plan,
                'status': 'confirmed',
                'tx_hash': tx_hash,
                'admin_note': note,
                'manual_confirmed_by': admin_user_id,
                'already_confirmed': already_confirmed,
            },
        })
    except Exception as e:
        logger.error(f"manual_confirm_order failed: {e}", exc_info=True)
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


# ==================== Admin AI Analysis Stats ====================

@user_blp.route('/admin-ai-stats', methods=['GET'])
@login_required
@admin_required
def get_admin_ai_stats():
    """
    Get AI analysis usage statistics across the system (admin only).
    Does NOT expose analysis results, only aggregated counts/stats.

    Query params:
        page: int (default 1)
        page_size: int (default 20, max 100)
        search: str (optional, search by username)
    """
    try:
        page = request.args.get('page', 1, type=int)
        page_size = request.args.get('page_size', 20, type=int)
        search = request.args.get('search', '', type=str).strip()
        page_size = min(100, max(1, page_size))
        offset = (page - 1) * page_size

        with get_db_connection() as db:
            cur = db.cursor()

            # --- Overall summary (from qd_analysis_tasks + qd_analysis_memory) ---
            cur.execute("""
                SELECT
                    COUNT(*) AS total_tasks,
                    COUNT(DISTINCT user_id) AS unique_users,
                    COUNT(DISTINCT symbol) AS unique_symbols,
                    COUNT(DISTINCT market) AS unique_markets
                FROM qd_analysis_tasks
            """)
            task_summary = cur.fetchone() or {}

            memory_summary = {}
            try:
                cur.execute("""
                    SELECT
                        COUNT(*) AS total_memory,
                        COALESCE(SUM(CASE WHEN was_correct = true THEN 1 ELSE 0 END), 0) AS correct_count,
                        COALESCE(SUM(CASE WHEN was_correct = false THEN 1 ELSE 0 END), 0) AS incorrect_count,
                        COALESCE(SUM(CASE WHEN user_feedback = 'helpful' THEN 1 ELSE 0 END), 0) AS helpful_count,
                        COALESCE(SUM(CASE WHEN user_feedback = 'not_helpful' THEN 1 ELSE 0 END), 0) AS not_helpful_count
                    FROM qd_analysis_memory
                """)
                memory_summary = cur.fetchone() or {}
            except Exception as mem_err:
                logger.warning(f"qd_analysis_memory query failed (table/column may not exist): {mem_err}")
                db.rollback()
                cur = db.cursor()  # re-create cursor after rollback
                memory_summary = {}

            copilot_summary = {}
            try:
                cur.execute("""
                    SELECT
                        COUNT(*) AS total_sessions,
                        COUNT(DISTINCT user_id) AS unique_chat_users
                    FROM qd_ai_copilot_sessions
                """)
                copilot_summary = cur.fetchone() or {}
                cur.execute("""
                    SELECT COUNT(*) AS total_messages
                    FROM qd_ai_copilot_messages
                """)
                copilot_message_summary = cur.fetchone() or {}
                copilot_summary['total_messages'] = int(copilot_message_summary.get('total_messages') or 0)
            except Exception as chat_err:
                logger.warning(f"qd_ai_copilot summary query failed (table may not exist): {chat_err}")
                db.rollback()
                cur = db.cursor()
                copilot_summary = {}

            # --- Per-user stats ---
            # Build WHERE clause for user search (applied after JOIN)
            user_where_clause = ""
            user_params = []
            if search:
                user_where_clause = "WHERE (u.username ILIKE ? OR u.nickname ILIKE ? OR u.email ILIKE ?)"
                like_val = f"%{search.strip()}%"
                user_params = [like_val, like_val, like_val]

            # Count distinct users who have analysis records (matching search criteria)
            count_sql = f"""
                SELECT COUNT(DISTINCT t.user_id) AS cnt
                FROM qd_analysis_tasks t
                LEFT JOIN qd_users u ON u.id = t.user_id
                {user_where_clause}
            """
            cur.execute(count_sql, tuple(user_params))
            count_result = cur.fetchone()
            user_total = count_result['cnt'] if count_result else 0

            # Get per-user aggregated stats
            # Important: Filter by user search criteria AFTER grouping, but we need to apply it in WHERE
            # Since we're grouping by user fields, we need to filter before GROUP BY
            stats_sql = f"""
                SELECT
                    t.user_id,
                    u.username,
                    u.nickname,
                    u.email,
                    COUNT(*) AS analysis_count,
                    COUNT(DISTINCT t.symbol) AS symbol_count,
                    COUNT(DISTINCT t.market) AS market_count,
                    MAX(t.created_at) AS last_analysis_at,
                    MIN(t.created_at) AS first_analysis_at
                FROM qd_analysis_tasks t
                LEFT JOIN qd_users u ON u.id = t.user_id
                {user_where_clause}
                GROUP BY t.user_id, u.username, u.nickname, u.email
                ORDER BY analysis_count DESC
                LIMIT ? OFFSET ?
            """
            cur.execute(stats_sql, tuple(user_params) + (page_size, offset))
            user_rows = cur.fetchall() or []

            # Get per-user analysis_memory stats (correct/helpful counts)
            user_ids = [r['user_id'] for r in user_rows if r.get('user_id')]
            memory_stats_map = {}
            copilot_stats_map = {}
            if user_ids:
                try:
                    placeholders = ','.join(['?'] * len(user_ids))
                    cur.execute(
                        f"""
                        SELECT
                            user_id,
                            COUNT(*) AS memory_count,
                            COALESCE(SUM(CASE WHEN was_correct = true THEN 1 ELSE 0 END), 0) AS correct,
                            COALESCE(SUM(CASE WHEN was_correct = false THEN 1 ELSE 0 END), 0) AS incorrect,
                            COALESCE(SUM(CASE WHEN user_feedback = 'helpful' THEN 1 ELSE 0 END), 0) AS helpful,
                            COALESCE(SUM(CASE WHEN user_feedback = 'not_helpful' THEN 1 ELSE 0 END), 0) AS not_helpful
                        FROM qd_analysis_memory
                        WHERE user_id IN ({placeholders})
                        GROUP BY user_id
                        """,
                        tuple(user_ids)
                    )
                    for row in (cur.fetchall() or []):
                        memory_stats_map[row['user_id']] = {
                            'memory_count': row['memory_count'],
                            'correct': row['correct'],
                            'incorrect': row['incorrect'],
                            'helpful': row['helpful'],
                            'not_helpful': row['not_helpful']
                        }
                except Exception as mem_err:
                    logger.warning(f"qd_analysis_memory per-user query failed: {mem_err}")
                    db.rollback()
                    cur = db.cursor()  # re-create cursor after rollback
                    memory_stats_map = {}
                try:
                    placeholders = ','.join(['?'] * len(user_ids))
                    cur.execute(
                        f"""
                        SELECT
                            s.user_id,
                            COUNT(DISTINCT s.id) AS chat_session_count,
                            COUNT(m.id) AS chat_message_count,
                            MAX(s.updated_at) AS last_chat_at
                        FROM qd_ai_copilot_sessions s
                        LEFT JOIN qd_ai_copilot_messages m ON m.session_id = s.id
                        WHERE s.user_id IN ({placeholders})
                        GROUP BY s.user_id
                        """,
                        tuple(user_ids)
                    )
                    for row in (cur.fetchall() or []):
                        copilot_stats_map[row['user_id']] = {
                            'chat_session_count': int(row.get('chat_session_count') or 0),
                            'chat_message_count': int(row.get('chat_message_count') or 0),
                            'last_chat_at': row.get('last_chat_at')
                        }
                except Exception as chat_err:
                    logger.warning(f"qd_ai_copilot per-user query failed: {chat_err}")
                    db.rollback()
                    cur = db.cursor()
                    copilot_stats_map = {}

            # Get recent analysis records (last 50)
            # Ensure we get user info even if user_id is NULL or user doesn't exist
            cur.execute(
                """
                SELECT
                    t.id,
                    t.user_id,
                    COALESCE(u.username, '') AS username,
                    COALESCE(u.nickname, '') AS nickname,
                    COALESCE(u.email, '') AS email,
                    t.market,
                    t.symbol,
                    t.model,
                    t.status,
                    t.created_at,
                    t.completed_at
                FROM qd_analysis_tasks t
                LEFT JOIN qd_users u ON u.id = t.user_id
                WHERE t.user_id IS NOT NULL
                ORDER BY t.created_at DESC
                LIMIT 50
                """
            )
            recent_rows = cur.fetchall() or []

            try:
                cur.execute(
                    """
                    SELECT
                        s.id,
                        s.user_id,
                        COALESCE(u.username, '') AS username,
                        COALESCE(u.nickname, '') AS nickname,
                        COALESCE(u.email, '') AS email,
                        s.title,
                        s.context_market,
                        s.context_symbol,
                        s.created_at,
                        s.updated_at,
                        COUNT(m.id) AS message_count
                    FROM qd_ai_copilot_sessions s
                    LEFT JOIN qd_users u ON u.id = s.user_id
                    LEFT JOIN qd_ai_copilot_messages m ON m.session_id = s.id
                    WHERE s.user_id IS NOT NULL
                    GROUP BY s.id, s.user_id, u.username, u.nickname, u.email,
                             s.title, s.context_market, s.context_symbol,
                             s.created_at, s.updated_at
                    ORDER BY s.updated_at DESC
                    LIMIT 50
                    """
                )
                recent_copilot_rows = cur.fetchall() or []
            except Exception as chat_err:
                logger.warning(f"qd_ai_copilot recent query failed: {chat_err}")
                db.rollback()
                cur = db.cursor()
                recent_copilot_rows = []

            cur.close()

        # Build per-user items
        from app.utils.timeutil import to_utc_iso

        user_items = []
        for row in user_rows:
            uid = row.get('user_id')
            if not uid:  # Skip rows with NULL user_id
                continue

            ms = memory_stats_map.get(uid, {})
            cs = copilot_stats_map.get(uid, {})
            # Server stores naive TIMESTAMP in container TZ; emit UTC ISO so the
            # browser can render it in the user's locale correctly.
            last_at = to_utc_iso(row.get('last_analysis_at'))
            first_at = to_utc_iso(row.get('first_analysis_at'))

            user_items.append({
                'user_id': int(uid),
                'username': str(row.get('username') or ''),
                'nickname': str(row.get('nickname') or ''),
                'email': str(row.get('email') or ''),
                'analysis_count': int(row.get('analysis_count') or 0),
                'symbol_count': int(row.get('symbol_count') or 0),
                'market_count': int(row.get('market_count') or 0),
                'correct': int(ms.get('correct', 0)),
                'incorrect': int(ms.get('incorrect', 0)),
                'helpful': int(ms.get('helpful', 0)),
                'not_helpful': int(ms.get('not_helpful', 0)),
                'last_analysis_at': last_at,
                'first_analysis_at': first_at,
                'chat_session_count': int(cs.get('chat_session_count', 0)),
                'chat_message_count': int(cs.get('chat_message_count', 0)),
                'last_chat_at': to_utc_iso(cs.get('last_chat_at'))
            })

        # Build recent records
        recent_items = []
        for row in recent_rows:
            user_id = row.get('user_id')
            if not user_id:  # Skip rows with NULL user_id
                continue

            created_at = to_utc_iso(row.get('created_at'))
            completed_at = to_utc_iso(row.get('completed_at'))

            recent_items.append({
                'id': int(row.get('id') or 0),
                'user_id': int(user_id),
                'username': str(row.get('username') or ''),
                'nickname': str(row.get('nickname') or ''),
                'email': str(row.get('email') or ''),
                'market': str(row.get('market') or ''),
                'symbol': str(row.get('symbol') or ''),
                'model': str(row.get('model') or ''),
                'status': str(row.get('status') or ''),
                'created_at': created_at,
                'completed_at': completed_at
            })

        recent_copilot_items = []
        for row in recent_copilot_rows:
            user_id = row.get('user_id')
            if not user_id:
                continue
            recent_copilot_items.append({
                'id': int(row.get('id') or 0),
                'user_id': int(user_id),
                'username': str(row.get('username') or ''),
                'nickname': str(row.get('nickname') or ''),
                'email': str(row.get('email') or ''),
                'title': str(row.get('title') or ''),
                'market': str(row.get('context_market') or ''),
                'symbol': str(row.get('context_symbol') or ''),
                'message_count': int(row.get('message_count') or 0),
                'created_at': to_utc_iso(row.get('created_at')),
                'updated_at': to_utc_iso(row.get('updated_at'))
            })

        return jsonify({
            'code': 1,
            'msg': 'success',
            'data': {
                'user_stats': user_items,
                'user_total': user_total,
                'page': page,
                'page_size': page_size,
                'recent': recent_items,
                'recent_copilot': recent_copilot_items,
                'summary': {
                    'total_analyses': int(task_summary.get('total_tasks') or 0),
                    'unique_users': int(task_summary.get('unique_users') or 0),
                    'unique_symbols': int(task_summary.get('unique_symbols') or 0),
                    'unique_markets': int(task_summary.get('unique_markets') or 0),
                    'total_memory': int(memory_summary.get('total_memory') or 0),
                    'correct_count': int(memory_summary.get('correct_count') or 0),
                    'incorrect_count': int(memory_summary.get('incorrect_count') or 0),
                    'helpful_count': int(memory_summary.get('helpful_count') or 0),
                    'not_helpful_count': int(memory_summary.get('not_helpful_count') or 0),
                    'total_copilot_sessions': int(copilot_summary.get('total_sessions') or 0),
                    'total_copilot_messages': int(copilot_summary.get('total_messages') or 0),
                    'unique_chat_users': int(copilot_summary.get('unique_chat_users') or 0)
                }
            }
        })
    except Exception as e:
        logger.error(f"get_admin_ai_stats failed: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


# ==================== Admin User Dashboard Stats ====================

@user_blp.route('/admin/stats', methods=['GET'])
@login_required
@admin_required
def get_admin_user_stats():
    """KPI dashboard data for the User Management tab (admin only).

    Returns a single envelope with `summary`, `growth`, `activity`.
    See `app.services.user_stats_service` for the schema of each section.
    """
    try:
        from app.services.user_stats_service import get_user_admin_stats

        data = get_user_admin_stats()
        return jsonify({'code': 1, 'msg': 'success', 'data': data})
    except Exception as e:
        logger.error(f"get_admin_user_stats failed: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500
# openapi-compat: legacy import name
user_bp = user_blp
