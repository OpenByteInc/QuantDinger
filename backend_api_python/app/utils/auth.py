"""
Authentication Utilities

JWT token generation, verification, and middleware decorators.
Supports multi-user authentication with role-based access control.
"""
import datetime
import os
import warnings
from functools import wraps

import jwt
from flask import g, jsonify, request

from app.config.settings import Config
from app.utils.logger import get_logger

logger = get_logger(__name__)
_RECOMMENDED_JWT_SECRET_BYTES = 32
_jwt_secret_warning_configured = False


def _configure_jwt_secret_warnings() -> None:
    """Validate the signing key and configure the legacy-length warning.

    The historical name is retained for compatibility with the application
    factory and any third-party integrations that import it.
    """
    global _jwt_secret_warning_configured

    secret = Config.SECRET_KEY
    if _jwt_secret_warning_configured:
        return
    key_len = len(secret.encode('utf-8'))
    if key_len < _RECOMMENDED_JWT_SECRET_BYTES:
        logger.warning(
            "SECRET_KEY is %d bytes; accepted for legacy compatibility, "
            "but 32+ random bytes are recommended",
            key_len,
        )
    try:
        from jwt.warnings import InsecureKeyLengthWarning

        warnings.filterwarnings("ignore", category=InsecureKeyLengthWarning)
    except (ImportError, AttributeError):
        # Older PyJWT releases do not expose this warning category.
        warnings.filterwarnings(
            "ignore",
            message=r".*HMAC key is .*bytes long.*",
            category=Warning,
        )
    _jwt_secret_warning_configured = True


def generate_token(user_id: int, username: str, role: str = 'user', token_version: int = 1) -> str:
    """
    Generate JWT token with user information.
    
    Args:
        user_id: User ID
        username: Username
        role: User role (admin/user/viewer)
        token_version: Token version for single-client enforcement
    
    Returns:
        JWT token string
    """
    try:
        _configure_jwt_secret_warnings()
        now = datetime.datetime.now(datetime.timezone.utc)
        payload = {
            'exp': now + datetime.timedelta(days=7),
            'iat': now,
            'sub': username,
            'user_id': user_id,
            'role': role,
            'token_version': token_version,  # 用于单一客户端登录控制
        }
        return jwt.encode(
            payload,
            Config.SECRET_KEY,
            algorithm='HS256'
        )
    except Exception as e:
        logger.error(f"Token generation failed: {e}")
        return None


def verify_token(token: str) -> dict:
    """
    Verify JWT token and return payload.
    
    Args:
        token: JWT token string
    
    Returns:
        Token payload dict or None if invalid
    """
    try:
        _configure_jwt_secret_warnings()
        payload = jwt.decode(
            token,
            Config.SECRET_KEY,
            algorithms=['HS256'],
            options={
                'require': ['exp', 'iat', 'sub', 'user_id', 'token_version'],
            },
        )

        user_id = payload.get('user_id')
        token_version = payload.get('token_version')

        if (
            not isinstance(user_id, int)
            or isinstance(user_id, bool)
            or user_id <= 0
            or not isinstance(token_version, int)
            or isinstance(token_version, bool)
            or token_version < 1
        ):
            logger.debug("Token contains invalid user_id or token_version")
            return None

        # Keep the session-version check in one independently testable
        # helper.  The authoritative user row is still loaded below for the
        # role/existence check, so a JWT claim can never supply authorization.
        if not _verify_token_version(user_id, token_version):
            logger.debug(
                "Token version mismatch for user %s: expected current, got %s",
                user_id,
                token_version,
            )
            return None

        auth_user = _get_user_auth_state(user_id)
        if not auth_user or auth_user.get('status') != 'active':
            logger.debug("Token subject does not exist or is not active: %s", user_id)
            return None

        # Re-check the version from the same authoritative row used for role
        # authorization. This closes the small race where a logout/version
        # bump happens between the helper check and the role lookup.
        db_token_version = auth_user.get('token_version')
        if db_token_version is None:
            db_token_version = 1
        try:
            if token_version != int(db_token_version):
                logger.debug(
                    "Token version changed during verification for user %s",
                    user_id,
                )
                return None
        except (TypeError, ValueError):
            logger.warning("Rejecting user %s with invalid database token_version", user_id)
            return None

        from app.services.user_service import UserService

        role = UserService.normalize_role(auth_user.get('role'))
        if role not in ('admin', 'user', 'viewer'):
            logger.warning("Rejecting user %s with invalid database role", user_id)
            return None

        # These values are deliberately separate from attacker-controlled JWT
        # claims. Authorization middleware consumes only the database-backed
        # fields populated after token/session validation succeeds.
        payload['_verified_username'] = auth_user.get('username') or payload['sub']
        payload['_verified_user_role'] = role
        return payload
    except jwt.ExpiredSignatureError:
        logger.debug("Token expired")
        return None
    except jwt.InvalidTokenError as e:
        logger.debug(f"Invalid token: {e}")
        return None


def _get_user_auth_state(user_id: int) -> dict:
    """Load the authoritative session and authorization state for a user."""
    try:
        from app.utils.db import get_db_connection

        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                SELECT username, role, status, token_version
                FROM qd_users WHERE id = ?
                """,
                (user_id,),
            )
            row = cur.fetchone()
            cur.close()
            return row
    except Exception as e:
        logger.error(f"_get_user_auth_state failed: {e}")
        return None


def _verify_token_version(user_id: int, token_version: int) -> bool:
    """
    验证 token 版本是否与数据库中存储的版本匹配。
    用于实现单一客户端登录（踢出重复登录）。
    
    Args:
        user_id: 用户ID
        token_version: Token中的版本号
    
    Returns:
        True if version matches, False otherwise
    """
    row = _get_user_auth_state(user_id)
    if not row:
        return False
    db_token_version = row.get('token_version')
    if db_token_version is None:
        db_token_version = 1
    try:
        return int(token_version) == int(db_token_version)
    except (TypeError, ValueError):
        return False


def get_current_user_id() -> int:
    """Get current user ID from flask.g context"""
    return getattr(g, 'user_id', None)


def get_current_user_role() -> str:
    """Get current user role from flask.g context"""
    return getattr(g, 'user_role', 'user')


def login_required(f):
    """
    Decorator that enforces Bearer token auth.
    
    Sets g.user, g.user_id, g.user_role on successful auth.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        
        # Read token from Authorization: Bearer <token>
        auth_header = request.headers.get('Authorization')
        if auth_header:
            parts = auth_header.split()
            if len(parts) == 2 and parts[0].lower() == 'bearer':
                token = parts[1]
        
        if not token:
            return jsonify({'code': 401, 'msg': 'Token missing', 'data': None}), 401
        
        payload = verify_token(token)
        if not payload:
            return jsonify({'code': 401, 'msg': 'Token invalid or expired', 'data': None}), 401
        
        verified_role = payload.get('_verified_user_role')
        if verified_role not in ('admin', 'user', 'viewer'):
            logger.warning("Verified token missing authoritative database role")
            return jsonify({'code': 401, 'msg': 'Token invalid or expired', 'data': None}), 401

        # Store only database-backed identity and authorization state in g.
        g.user = payload.get('_verified_username')
        g.user_id = payload.get('user_id')
        g.user_role = verified_role
        
        return f(*args, **kwargs)
        
    return decorated


def admin_required(f):
    """
    Decorator that requires admin role.
    Must be used after @login_required.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        role = getattr(g, 'user_role', None)
        if role != 'admin':
            return jsonify({'code': 403, 'msg': 'Admin access required', 'data': None}), 403
        return f(*args, **kwargs)
    return decorated


def permission_required(permission: str):
    """
    Decorator factory that checks for a specific permission.
    Must be used after @login_required.
    
    Usage:
        @login_required
        @permission_required('strategy')
        def my_endpoint():
            ...
    """
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            role = getattr(g, 'user_role', 'user')
            
            # Import here to avoid circular import
            from app.services.user_service import get_user_service
            permissions = get_user_service().get_user_permissions(role)
            
            if permission not in permissions:
                return jsonify({
                    'code': 403, 
                    'msg': f'Permission denied: {permission}', 
                    'data': None
                }), 403
            
            return f(*args, **kwargs)
        return decorated
    return decorator


# Legacy compatibility: single-user mode fallback
def _is_single_user_mode() -> bool:
    """Check if system is in single-user (legacy) mode"""
    return os.getenv('SINGLE_USER_MODE', 'false').lower() == 'true'


def authenticate_legacy(username: str, password: str) -> dict:
    """
    Legacy single-user authentication (for backward compatibility).
    Uses ADMIN_USER and ADMIN_PASSWORD from environment.
    """
    if username == Config.ADMIN_USER and password == Config.ADMIN_PASSWORD:
        return {
            'user_id': 1,
            'username': username,
            'role': 'admin',
            'nickname': 'Admin',
        }
    return None
