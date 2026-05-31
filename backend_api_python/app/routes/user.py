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
    """Add qd_users.chart_templates when upgrading existing databases."""
    try:
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                ALTER TABLE qd_users
                ADD COLUMN IF NOT EXISTS chart_templates TEXT DEFAULT ''
                """
            )
            db.commit()
            cur.close()
    except Exception as e:
        logger.warning(f"ensure chart_templates column skipped: {e}")

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
    """
    Get current user's notification settings.
    
    Returns:
        notification_settings: {
            default_channels: ['browser', 'telegram', ...],
            telegram_chat_id: str,
            email: str (optional, override for notifications),
            discord_webhook: str (optional)
        }
    """
    try:
        import json
        from app.utils.db import get_db_connection
        
        user_id = getattr(g, 'user_id', None)
        if not user_id:
            return jsonify({'code': 0, 'msg': 'Not authenticated', 'data': None}), 401
        
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute("SELECT notification_settings, email FROM qd_users WHERE id = ?", (user_id,))
            row = cur.fetchone()
            cur.close()
        
        if not row:
            return jsonify({'code': 0, 'msg': 'User not found', 'data': None}), 404
        
        # Parse notification_settings JSON
        settings_str = row.get('notification_settings') or ''
        settings = {}
        if settings_str:
            try:
                settings = json.loads(settings_str)
            except Exception:
                settings = {}
        
        # Default values
        if 'default_channels' not in settings:
            settings['default_channels'] = ['browser']
        if 'email' not in settings:
            settings['email'] = row.get('email') or ''
        
        return jsonify({
            'code': 1,
            'msg': 'success',
            'data': settings
        })
    except Exception as e:
        logger.error(f"get_notification_settings failed: {e}")
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@user_blp.route('/notification-settings', methods=['PUT'])
@login_required
def update_notification_settings():
    """
    Update current user's notification settings.
    
    Request body:
        default_channels: list of str (optional, e.g. ['browser', 'telegram'])
        telegram_bot_token: str (optional, user's own Telegram bot token)
        telegram_chat_id: str (optional)
        email: str (optional, for notification override)
        discord_webhook: str (optional)
    """
    try:
        import json
        from app.utils.db import get_db_connection
        
        user_id = getattr(g, 'user_id', None)
        if not user_id:
            return jsonify({'code': 0, 'msg': 'Not authenticated', 'data': None}), 401
        
        data = request.get_json() or {}
        
        # Validate channels
        valid_channels = ['browser', 'email', 'telegram', 'discord', 'webhook', 'phone']
        default_channels = data.get('default_channels', [])
        if not isinstance(default_channels, list):
            default_channels = ['browser']
        default_channels = [c for c in default_channels if c in valid_channels]
        if not default_channels:
            default_channels = ['browser']
        
        # Build settings object
        #
        # webhook_signing_secret is optional and only meaningful for
        # specific dialects (Feishu in-body sign / DingTalk URL sign).
        # Generic self-hosted webhooks can use it for HMAC header
        # signing — see signal_notifier._notify_webhook for the full
        # semantics.
        settings = {
            'default_channels': default_channels,
            'telegram_bot_token': str(data.get('telegram_bot_token') or '').strip(),
            'telegram_chat_id': str(data.get('telegram_chat_id') or '').strip(),
            'email': str(data.get('email') or '').strip(),
            'discord_webhook': str(data.get('discord_webhook') or '').strip(),
            'webhook_url': str(data.get('webhook_url') or '').strip(),
            'webhook_token': str(data.get('webhook_token') or '').strip(),
            'webhook_signing_secret': str(data.get('webhook_signing_secret') or '').strip(),
            'phone': str(data.get('phone') or '').strip(),
        }
        
        # Remove empty values (but keep default_channels and telegram_bot_token even if partially filled)
        settings = {k: v for k, v in settings.items() if v or k == 'default_channels'}
        
        settings_json = json.dumps(settings, ensure_ascii=False)
        
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                "UPDATE qd_users SET notification_settings = ?, updated_at = NOW() WHERE id = ?",
                (settings_json, user_id)
            )
            db.commit()
            cur.close()
        
        return jsonify({
            'code': 1,
            'msg': 'Notification settings updated',
            'data': settings
        })
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

        _ensure_chart_templates_column()
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute("SELECT chart_templates FROM qd_users WHERE id = ?", (user_id,))
            row = cur.fetchone()
            cur.close()

        raw = (row.get('chart_templates') if row else '') or ''
        templates = []
        if raw:
            try:
                templates = json.loads(raw)
            except Exception:
                templates = []
        if not isinstance(templates, list):
            templates = []

        templates = sorted(
            [tpl for tpl in templates if isinstance(tpl, dict)],
            key=lambda x: str(x.get('updated_at') or ''),
            reverse=True
        )
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

        data = request.get_json() or {}
        name = str(data.get('name') or '').strip()
        template_id = str(data.get('template_id') or '').strip()
        indicators = data.get('indicators') or []

        if not name:
            return jsonify({'code': 0, 'msg': 'Template name is required', 'data': None}), 400
        if len(name) > 80:
            return jsonify({'code': 0, 'msg': 'Template name is too long', 'data': None}), 400
        if not isinstance(indicators, list):
            return jsonify({'code': 0, 'msg': 'Indicators must be a list', 'data': None}), 400

        sanitized = []
        for item in indicators:
            if not isinstance(item, dict):
                continue
            indicator_id = str(item.get('id') or '').strip()
            instance_id = str(item.get('instanceId') or '').strip()
            indicator_type = str(item.get('type') or '').strip()
            if not indicator_id or not instance_id or not indicator_type:
                continue
            params = item.get('params') if isinstance(item.get('params'), dict) else {}
            style = item.get('style') if isinstance(item.get('style'), dict) else {}
            sanitized.append({
                'id': indicator_id,
                'instanceId': instance_id,
                'name': str(item.get('name') or '').strip(),
                'shortName': str(item.get('shortName') or '').strip(),
                'type': indicator_type,
                'visible': bool(item.get('visible', True)),
                'params': params,
                'style': {
                    'color': str(style.get('color') or '').strip(),
                    'lineWidth': int(style.get('lineWidth') or 2)
                }
            })

        now_iso = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        _ensure_chart_templates_column()
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute("SELECT chart_templates FROM qd_users WHERE id = ?", (user_id,))
            row = cur.fetchone()
            raw = (row.get('chart_templates') if row else '') or ''
            templates = []
            if raw:
                try:
                    templates = json.loads(raw)
                except Exception:
                    templates = []
            if not isinstance(templates, list):
                templates = []

            saved = None
            updated_templates = []
            if template_id:
                for tpl in templates:
                    if isinstance(tpl, dict) and str(tpl.get('id') or '') == template_id:
                        tpl = {
                            **tpl,
                            'id': template_id,
                            'name': name,
                            'indicators': sanitized,
                            'updated_at': now_iso
                        }
                        saved = tpl
                    updated_templates.append(tpl)
            else:
                updated_templates = [tpl for tpl in templates if isinstance(tpl, dict)]

            if saved is None:
                saved = {
                    'id': f"tpl_{int(time.time() * 1000)}",
                    'name': name,
                    'indicators': sanitized,
                    'created_at': now_iso,
                    'updated_at': now_iso
                }
                updated_templates.append(saved)

            updated_templates = sorted(updated_templates, key=lambda x: str(x.get('updated_at') or ''), reverse=True)[:20]
            cur.execute(
                "UPDATE qd_users SET chart_templates = ?, updated_at = NOW() WHERE id = ?",
                (json.dumps(updated_templates, ensure_ascii=False), user_id)
            )
            db.commit()
            cur.close()

        return jsonify({'code': 1, 'msg': 'Chart template saved', 'data': saved})
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

        template_id = str(request.args.get('template_id') or '').strip()
        if not template_id:
            return jsonify({'code': 0, 'msg': 'template_id is required', 'data': None}), 400

        _ensure_chart_templates_column()
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute("SELECT chart_templates FROM qd_users WHERE id = ?", (user_id,))
            row = cur.fetchone()
            raw = (row.get('chart_templates') if row else '') or ''
            templates = []
            if raw:
                try:
                    templates = json.loads(raw)
                except Exception:
                    templates = []
            if not isinstance(templates, list):
                templates = []

            updated_templates = [
                tpl for tpl in templates
                if not (isinstance(tpl, dict) and str(tpl.get('id') or '') == template_id)
            ]
            cur.execute(
                "UPDATE qd_users SET chart_templates = ?, updated_at = NOW() WHERE id = ?",
                (json.dumps(updated_templates, ensure_ascii=False), user_id)
            )
            db.commit()
            cur.close()

        return jsonify({'code': 1, 'msg': 'Chart template deleted', 'data': {'template_id': template_id}})
    except Exception as e:
        logger.error(f"delete_chart_template failed: {e}")
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@user_blp.route('/notification-settings/test', methods=['POST'])
@login_required
def test_notification_settings():
    """
    Send a test notification using the current user's saved notification_settings
    (save settings first via PUT /notification-settings).
    """
    try:
        import json
        from app.services.signal_notifier import SignalNotifier
        from app.utils.db import get_db_connection

        user_id = getattr(g, 'user_id', None)
        if not user_id:
            return jsonify({'code': 0, 'msg': 'Not authenticated', 'data': None}), 401

        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute("SELECT notification_settings, email FROM qd_users WHERE id = ?", (user_id,))
            row = cur.fetchone()
            cur.close()

        if not row:
            return jsonify({'code': 0, 'msg': 'User not found', 'data': None}), 404

        settings_str = row.get('notification_settings') or ''
        account_email = (row.get('email') or '').strip()
        settings = {}
        if settings_str:
            try:
                settings = json.loads(settings_str)
            except Exception:
                settings = {}

        channels = settings.get('default_channels') or ['browser']
        if not isinstance(channels, list) or not channels:
            channels = ['browser']

        notify_email = (settings.get('email') or '').strip() or account_email
        targets = {
            'telegram': (settings.get('telegram_chat_id') or '').strip(),
            'telegram_bot_token': (settings.get('telegram_bot_token') or '').strip(),
            'email': notify_email,
            'phone': (settings.get('phone') or '').strip(),
            'discord': (settings.get('discord_webhook') or '').strip(),
            'webhook': (settings.get('webhook_url') or '').strip(),
            'webhook_token': (settings.get('webhook_token') or '').strip(),
            'webhook_signing_secret': (settings.get('webhook_signing_secret') or '').strip(),
        }

        accept = (request.headers.get('Accept-Language') or '') + ' ' + (request.headers.get('X-Locale') or '')
        language = 'zh-CN' if 'zh' in accept.lower() else 'en-US'

        notifier = SignalNotifier()
        results = notifier.send_profile_test_notifications(
            user_id=int(user_id),
            channels=channels,
            targets=targets,
            language=language,
        )

        any_ok = any((v or {}).get('ok') for v in results.values())
        failed = [k for k, v in results.items() if not (v or {}).get('ok')]
        if failed:
            err_detail = {k: (results.get(k) or {}).get('error', '') for k in failed}
            logger.warning("notification_settings test: user_id=%s failed_channels=%s errors=%s", user_id, failed, err_detail)

        if not any_ok:
            detail = '; '.join(f"{k}: {(results[k] or {}).get('error', '')}" for k in failed) or 'all channels failed'
            return jsonify({'code': 0, 'msg': detail, 'data': {'results': results}})

        msg = 'Test notification sent'
        if failed:
            msg = f"Sent OK; failed: {', '.join(failed)}"
        return jsonify({'code': 1, 'msg': msg, 'data': {'results': results}})
    except Exception as e:
        logger.error(f"test_notification_settings failed: {e}")
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@user_blp.route('/change-password', methods=['POST'])
@login_required
def change_password():
    """
    Change current user's password.
    
    Request body:
        old_password: str (required)
        new_password: str (required)
    """
    try:
        user_id = getattr(g, 'user_id', None)
        if not user_id:
            return jsonify({'code': 0, 'msg': 'Not authenticated', 'data': None}), 401
        
        data = request.get_json() or {}
        old_password = data.get('old_password', '')
        new_password = data.get('new_password', '')
        
        if not new_password:
            return jsonify({'code': 0, 'msg': 'New password required', 'data': None}), 400
        
        if len(new_password) < 6:
            return jsonify({'code': 0, 'msg': 'New password must be at least 6 characters', 'data': None}), 400
        
        # Check if user has a password set
        user_service = get_user_service()
        user = user_service.get_user_by_id(user_id)
        if not user:
            return jsonify({'code': 0, 'msg': 'User not found', 'data': None}), 404
        
        # Get password_hash to check if user has no password
        from app.utils.db import get_db_connection
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute("SELECT password_hash FROM qd_users WHERE id = ?", (user_id,))
            row = cur.fetchone()
            cur.close()
        
        password_hash = row.get('password_hash', '') if row else ''
        has_password = password_hash and password_hash.strip() != ''
        
        # If user has no password, allow setting password without old password
        if not has_password:
            if not old_password:
                # No old password required for users without password
                success = user_service.reset_password(user_id, new_password)
                if success:
                    return jsonify({'code': 1, 'msg': 'Password set successfully', 'data': None})
                else:
                    return jsonify({'code': 0, 'msg': 'Failed to set password', 'data': None}), 500
            else:
                # If old_password is provided but user has no password, ignore it
                success = user_service.reset_password(user_id, new_password)
                if success:
                    return jsonify({'code': 1, 'msg': 'Password set successfully', 'data': None})
                else:
                    return jsonify({'code': 0, 'msg': 'Failed to set password', 'data': None}), 500
        else:
            # User has existing password, require old password verification
            if not old_password:
                return jsonify({'code': 0, 'msg': 'Old password required', 'data': None}), 400
            
            success = user_service.change_password(user_id, old_password, new_password)
            
            if success:
                return jsonify({'code': 1, 'msg': 'Password changed successfully', 'data': None})
            else:
                return jsonify({'code': 0, 'msg': 'Old password incorrect', 'data': None}), 400
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
        execution_mode: str (optional, live/signal — omit or all for any)
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
                " + COALESCE((SELECT SUM(COALESCE(t.profit, 0) - COALESCE(t.commission, 0)) FROM qd_strategy_trades t WHERE t.strategy_id = s.id), 0))"
            ),
            'trade_count': '(SELECT COUNT(*) FROM qd_strategy_trades t WHERE t.strategy_id = s.id)',
            'position_count': '(SELECT COUNT(*) FROM qd_strategy_positions p WHERE p.strategy_id = s.id)',
            'total_equity': (
                'COALESCE((SELECT SUM(equity) FROM qd_strategy_positions p WHERE p.strategy_id = s.id), 0)'
            ),
        }
        direction = 'ASC' if sort_order == 'asc' else 'DESC'

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
                        "OR u.username ILIKE ? OR u.nickname ILIKE ?)"
                    )
                    params.extend([num, num, like_val, like_val, like_val, like_val])
                else:
                    conditions.append(
                        "(s.strategy_name ILIKE ? OR s.symbol ILIKE ? OR u.username ILIKE ? OR u.nickname ILIKE ?"
                        " OR CAST(s.id AS TEXT) ILIKE ? OR CAST(s.user_id AS TEXT) ILIKE ?)"
                    )
                    params.extend([like_val, like_val, like_val, like_val, like_val, like_val])

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
                    s.strategy_mode,
                    s.market_category,
                    s.execution_mode,
                    s.status,
                    s.symbol,
                    s.timeframe,
                    s.initial_capital,
                    s.leverage,
                    s.market_type,
                    s.indicator_config,
                    s.trading_config,
                    s.exchange_config,
                    s.decide_interval,
                    s.created_at,
                    s.updated_at,
                    u.username,
                    u.nickname
                FROM qd_strategies_trading s
                LEFT JOIN qd_users u ON u.id = s.user_id
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
                    SELECT strategy_id, 
                           COUNT(*) as trade_count, 
                           COALESCE(SUM(COALESCE(profit, 0) - COALESCE(commission, 0)), 0) as total_realized_pnl
                    FROM qd_strategy_trades
                    WHERE strategy_id IN ({placeholders})
                    GROUP BY strategy_id
                    """,
                    tuple(strategy_ids)
                )
                for row in (cur.fetchall() or []):
                    trade_stats_map[row['strategy_id']] = {
                        'trade_count': row['trade_count'],
                        'total_realized_pnl': float(row['total_realized_pnl'] or 0)
                    }

            cur.close()

        # Build response — batch-resolve exchange names for credential_id-only configs.
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
            indicator_config = _safe_json_loads(s.get('indicator_config'), {})
            trading_config = _safe_json_loads(s.get('trading_config'), {})
            exchange_config = _safe_json_loads(s.get('exchange_config'), {})

            # Extract indicator name
            indicator_name = ''
            if isinstance(indicator_config, dict):
                indicator_name = indicator_config.get('indicator_name') or indicator_config.get('name') or ''
            if not indicator_name and str(s.get('strategy_mode') or '').strip().lower() == 'bot':
                if isinstance(trading_config, dict):
                    indicator_name = (
                        trading_config.get('bot_name')
                        or s.get('strategy_name')
                        or trading_config.get('bot_type')
                        or ''
                    )
                else:
                    indicator_name = s.get('strategy_name') or ''

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

            # Cross-sectional info
            cs_type = ''
            symbol_list = []
            if isinstance(trading_config, dict):
                cs_type = trading_config.get('cs_strategy_type') or 'single'
                symbol_list = trading_config.get('symbol_list') or []

            # Timestamps are emitted as UTC ISO by SafeJSONProvider — pass
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
                'cs_strategy_type': cs_type,
                'market_category': s.get('market_category') or '',
                'execution_mode': s.get('execution_mode') or '',
                'status': s.get('status') or 'stopped',
                'symbol': s.get('symbol') or '',
                'symbol_list': symbol_list,
                'timeframe': s.get('timeframe') or '',
                'initial_capital': initial_capital,
                'leverage': int(s.get('leverage') or 1),
                'market_type': s.get('market_type') or '',
                'indicator_name': indicator_name,
                'exchange_name': exchange_name,
                'decide_interval': s.get('decide_interval') or 300,
                'position_count': position_count,
                'total_unrealized_pnl': round(total_unrealized_pnl, 4),
                'total_realized_pnl': round(total_realized_pnl, 4),
                'total_pnl': round(total_pnl, 4),
                'total_equity': round(total_equity, 4),
                'roi': round(roi, 2),
                'trade_count': trade_count,
                'positions': positions,
                'created_at': created_at,
                'updated_at': updated_at
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
                    COALESCE(SUM(CASE WHEN s.status = 'running' AND s.execution_mode = 'live' THEN 1 ELSE 0 END), 0) AS running_live_strategies,
                    COALESCE(SUM(CASE WHEN s.status = 'running' AND s.execution_mode = 'signal' THEN 1 ELSE 0 END), 0) AS running_signal_strategies,
                    COALESCE(SUM(CASE WHEN s.execution_mode = 'live' THEN s.initial_capital ELSE 0 END), 0) AS live_capital,
                    COALESCE(SUM(CASE WHEN s.execution_mode = 'signal' THEN s.initial_capital ELSE 0 END), 0) AS signal_capital
                FROM qd_strategies_trading s
                LEFT JOIN qd_users u ON u.id = s.user_id
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
                {where_clause}
            """
            cur.execute(unreal_sql, tuple(params))
            unreal_row = cur.fetchone() or {}

            # Aggregate realized pnl from trade history.
            realized_sql = f"""
                SELECT COALESCE(SUM(COALESCE(t.profit, 0) - COALESCE(t.commission, 0)), 0) AS total_realized,
                       COALESCE(SUM(CASE WHEN s.execution_mode = 'live' THEN COALESCE(t.profit, 0) - COALESCE(t.commission, 0) ELSE 0 END), 0) AS live_realized,
                       COALESCE(SUM(CASE WHEN s.execution_mode = 'signal' THEN COALESCE(t.profit, 0) - COALESCE(t.commission, 0) ELSE 0 END), 0) AS signal_realized
                FROM qd_strategy_trades t
                JOIN qd_strategies_trading s ON s.id = t.strategy_id
                LEFT JOIN qd_users u ON u.id = s.user_id
                {where_clause}
            """
            cur.execute(realized_sql, tuple(params))
            realized_row = cur.fetchone() or {}
            cur.close()

        total_capital = float(agg_row.get('total_capital') or 0)
        total_running = int(agg_row.get('running_strategies') or 0)
        total_system_pnl = float(unreal_row.get('total_unrealized') or 0) + float(realized_row.get('total_realized') or 0)
        live_pnl = float(unreal_row.get('live_unrealized') or 0) + float(realized_row.get('live_realized') or 0)
        signal_pnl = float(unreal_row.get('signal_unrealized') or 0) + float(realized_row.get('signal_realized') or 0)

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
                    'running_live_strategies': int(agg_row.get('running_live_strategies') or 0),
                    'running_signal_strategies': int(agg_row.get('running_signal_strategies') or 0),
                    'live_capital': round(float(agg_row.get('live_capital') or 0), 2),
                    'signal_capital': round(float(agg_row.get('signal_capital') or 0), 2),
                    'live_pnl': round(live_pnl, 4),
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
        action: optional ``start`` | ``stop`` — omit to toggle current status
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
                msg = f'策略启动后立即退出: {hint}'
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
            executor.stop_strategy(strategy_id)
            svc.update_strategy_status(strategy_id, 'stopped')
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


# ==================== Admin Orders ====================


def _ensure_usdt_admin_columns():
    """Best-effort: extend qd_usdt_orders with admin-audit columns introduced
    by the manual-confirm flow. ``ADD COLUMN IF NOT EXISTS`` is idempotent
    on PostgreSQL, so this is effectively a no-op after the first hit.

    Failures are swallowed (logged at debug level) so a running DB user
    without DDL privileges doesn't block the read paths — the SELECTs
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
          on re-submit — already-confirmed orders only refresh the audit
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

        _ensure_usdt_admin_columns()

        # Load order in a short read txn (don't hold a lock across the
        # billing call below — purchase_membership opens its own conn).
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
            # Cancelled orders are deliberately retired — surfacing this
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
        # the first time — re-submits (already confirmed) should only
        # update the audit fields above, never grant another membership.
        billing_msg = ''
        if not already_confirmed:
            try:
                from app.services.billing_service import get_billing_service
                billing = get_billing_service()
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

            cur.close()

        # Build per-user items
        from app.utils.timeutil import to_utc_iso

        user_items = []
        for row in user_rows:
            uid = row.get('user_id')
            if not uid:  # Skip rows with NULL user_id
                continue

            ms = memory_stats_map.get(uid, {})
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
                'first_analysis_at': first_at
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

        return jsonify({
            'code': 1,
            'msg': 'success',
            'data': {
                'user_stats': user_items,
                'user_total': user_total,
                'page': page,
                'page_size': page_size,
                'recent': recent_items,
                'summary': {
                    'total_analyses': int(task_summary.get('total_tasks') or 0),
                    'unique_users': int(task_summary.get('unique_users') or 0),
                    'unique_symbols': int(task_summary.get('unique_symbols') or 0),
                    'unique_markets': int(task_summary.get('unique_markets') or 0),
                    'total_memory': int(memory_summary.get('total_memory') or 0),
                    'correct_count': int(memory_summary.get('correct_count') or 0),
                    'incorrect_count': int(memory_summary.get('incorrect_count') or 0),
                    'helpful_count': int(memory_summary.get('helpful_count') or 0),
                    'not_helpful_count': int(memory_summary.get('not_helpful_count') or 0)
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
