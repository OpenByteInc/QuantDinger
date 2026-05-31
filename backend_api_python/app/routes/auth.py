"""
Authentication API Routes

Handles login, logout, registration, password reset, and OAuth authentication.
Supports both multi-user (database) and single-user (legacy) modes.
"""
import os
from flask import g, jsonify, redirect, request
from app.openapi.blueprint import HumanBlueprint as Blueprint
from urllib.parse import urlencode, urlparse, urlunparse, parse_qsl
from app.config.settings import Config
from app.utils.auth import generate_token, login_required, authenticate_legacy
from app.utils.logger import get_logger

logger = get_logger(__name__)

auth_blp = Blueprint('auth', __name__)

def _build_frontend_login_redirect(frontend_url: str, **params) -> str:
    """
    Build a redirect URL to the frontend login page for OAuth flows.

    Supports both:
    - PC (hash mode, path `/#/user/login`) — default behavior when only an origin
      is supplied via FRONTEND_URL.
    - Mobile / SPA history mode (e.g. `https://m.example.com/login`) — when the
      caller provides a full URL with a real path (and optional query/hash), we
      preserve that path instead of overwriting it with `/#/user/login`.

    The decision rule:
    1. If `frontend_url` contains a hash fragment (starts with `#/`), treat it as
       a PC hash-mode URL and normalize to `{origin}/#/user/login`.
    2. If `frontend_url` has a path other than `/` or empty, preserve the full
       URL (origin + path) and just append the OAuth params as query string.
    3. Otherwise (origin only), fall back to PC `/#/user/login`.
    """
    base = (frontend_url or '').strip().rstrip('/')
    if not base:
        base = 'http://localhost:8080'

    # Ensure we can parse the URL
    candidate = base if '://' in base else f'https://{base}'
    try:
        parsed = urlparse(candidate)
    except Exception:
        parsed = None

    origin = ''
    has_real_path = False
    has_hash_route = False

    if parsed and parsed.scheme and parsed.netloc:
        origin = f"{parsed.scheme}://{parsed.netloc}".rstrip('/')
        # Hash-mode PC login URL, e.g. https://pc.example.com/#/user/login
        if parsed.fragment:
            has_hash_route = True
        path_part = (parsed.path or '').rstrip('/')
        if path_part and path_part != '':
            has_real_path = True
    else:
        origin = base

    clean_params = {k: v for k, v in params.items() if v is not None and v != ''}
    qs = urlencode(clean_params)

    if has_hash_route:
        # PC / hash-mode: always normalize to /#/user/login
        login_url = f"{origin}/#/user/login"
        return f"{login_url}?{qs}" if qs else login_url

    if has_real_path:
        # SPA history-mode (mobile etc.) — keep the caller-provided path and
        # merge OAuth params into existing query string.
        existing_qs = dict(parse_qsl(parsed.query or '', keep_blank_values=True))
        existing_qs.update(clean_params)
        merged_qs = urlencode(existing_qs)
        rebuilt = urlunparse((
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            merged_qs,
            ''  # drop the fragment deliberately for history-mode SPAs
        ))
        return rebuilt

    # Origin only — fall back to PC hash route for backward compatibility
    login_url = f"{origin}/#/user/login"
    return f"{login_url}?{qs}" if qs else login_url


def _is_single_user_mode() -> bool:
    """Check if system is in single-user (legacy) mode"""
    return os.getenv('SINGLE_USER_MODE', 'false').lower() == 'true'


def _get_client_ip() -> str:
    """Get client IP address from request"""
    # Check for proxy headers
    if request.headers.get('X-Forwarded-For'):
        return request.headers.get('X-Forwarded-For').split(',')[0].strip()
    if request.headers.get('X-Real-IP'):
        return request.headers.get('X-Real-IP')
    return request.remote_addr or '0.0.0.0'


def _get_user_agent() -> str:
    """Get user agent from request"""
    return request.headers.get('User-Agent', '')[:500]


def _userinfo_must_change_initial_password(user_id: int) -> bool:
    """Whether the UI should prompt the user to change their bootstrap password."""
    try:
        from app.services.user_service import get_user_service
        return get_user_service().must_change_initial_password(int(user_id))
    except Exception:
        return False


# =============================================================================
# Security Config Endpoint
# =============================================================================

@auth_blp.route('/security-config', methods=['GET'])
def get_security_config():
    """
    Get public security configuration for frontend.
    
    Returns:
        turnstile_enabled: bool
        turnstile_site_key: str
        registration_enabled: bool
        oauth_google_enabled: bool
        oauth_github_enabled: bool
    """
    try:
        from app.services.security_service import get_security_service
        config = get_security_service().get_security_config()
        return jsonify({'code': 1, 'msg': 'success', 'data': config})
    except Exception as e:
        logger.error(f"get_security_config error: {e}")
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


# =============================================================================
# Login Endpoint (Enhanced with security)
# =============================================================================

@auth_blp.route('/login', methods=['POST'])
def login():
    """
    User login endpoint.
    
    Request body:
        username: str
        password: str
        turnstile_token: str (optional, required if Turnstile is enabled)
    
    Returns:
        token: JWT token
        userinfo: User information
    """
    ip_address = _get_client_ip()
    user_agent = _get_user_agent()
    
    try:
        from app.services.security_service import get_security_service
        security = get_security_service()
        
        data = request.get_json()
        if not data:
            return jsonify({'code': 400, 'msg': 'No data provided', 'data': None}), 400
        
        username = data.get('username') or data.get('account')
        password = data.get('password')
        turnstile_token = data.get('turnstile_token')
        
        if not username or not password:
            return jsonify({'code': 400, 'msg': 'Missing username/email or password', 'data': None}), 400
        
        # Step 1: Verify Turnstile (if enabled)
        turnstile_ok, turnstile_msg = security.verify_turnstile(turnstile_token, ip_address)
        if not turnstile_ok:
            return jsonify({'code': 0, 'msg': turnstile_msg, 'data': None}), 400
        
        # Step 2: Check rate limiting
        allowed, block_msg = security.check_login_allowed(username, ip_address)
        if not allowed:
            return jsonify({'code': 0, 'msg': block_msg, 'data': {'blocked': True}}), 429
        
        user = None
        
        # Step 3: Authenticate
        if not _is_single_user_mode():
            try:
                from app.services.user_service import get_user_service
                user = get_user_service().authenticate(username, password)
                
                # Check if user has no password set (code-login user)
                if user and user.get('_no_password'):
                    user.pop('_no_password', None)
                    # Record failed attempt
                    security.record_login_attempt(ip_address, 'ip', False, ip_address, user_agent)
                    security.record_login_attempt(username, 'account', False, ip_address, user_agent)
                    security.log_security_event('login_failed', user.get('id'), ip_address, user_agent, 
                                               {'username': username, 'reason': 'no_password_set'})
                    return jsonify({
                        'code': 0, 
                        'msg': 'This account was created with email verification code and has no password set. Please use email code login or set a password first in your profile settings.', 
                        'data': None
                    }), 401
            except Exception as e:
                logger.warning(f"Multi-user auth failed, trying legacy: {e}")
        
        # Fallback to legacy single-user mode
        if not user:
            user = authenticate_legacy(username, password)
        
        if not user:
            # Record failed attempt
            security.record_login_attempt(ip_address, 'ip', False, ip_address, user_agent)
            security.record_login_attempt(username, 'account', False, ip_address, user_agent)
            security.log_security_event('login_failed', None, ip_address, user_agent, 
                                       {'username': username, 'reason': 'invalid_credentials'})
            return jsonify({'code': 0, 'msg': 'Invalid credentials', 'data': None}), 401
        
        # Check user status
        if user.get('status') == 'disabled':
            security.log_security_event('login_blocked', user.get('id'), ip_address, user_agent,
                                       {'reason': 'account_disabled'})
            return jsonify({'code': 0, 'msg': 'Account is disabled', 'data': None}), 403
        
        if user.get('status') == 'pending':
            return jsonify({'code': 0, 'msg': 'Account is pending activation', 'data': None}), 403
        
        # Step 4: Increment token_version (invalidates old sessions for single-client login)
        user_id = user.get('id') or user.get('user_id', 1)
        try:
            from app.services.user_service import get_user_service
            new_token_version = get_user_service().increment_token_version(user_id)
        except Exception as e:
            logger.warning(f"Failed to increment token_version: {e}")
            new_token_version = 1
        
        # Step 5: Generate token with new token_version
        token = generate_token(
            user_id=user_id,
            username=user.get('username', username),
            role=user.get('role', 'admin'),
            token_version=new_token_version  # 包含新的 token_version
        )
        
        if not token:
            return jsonify({'code': 500, 'msg': 'Token generation error', 'data': None}), 500
        
        # Step 6: Record successful login
        security.record_login_attempt(ip_address, 'ip', True, ip_address, user_agent)
        security.record_login_attempt(username, 'account', True, ip_address, user_agent)
        security.clear_login_attempts(ip_address, 'ip')
        security.clear_login_attempts(username, 'account')
        from app.services.login_notify import notify_successful_login
        notify_successful_login(
            user_id=int(user.get('id') or user_id),
            action='login_success',
            ip_address=ip_address,
            user_agent=user_agent,
            extra_details={'method': 'password'},
        )

        # Build user info for frontend
        userinfo = {
            'id': user.get('id') or user.get('user_id', 1),
            'username': user.get('username', username),
            'nickname': user.get('nickname', 'User'),
            'avatar': user.get('avatar', '/avatar2.jpg'),
            'timezone': str(user.get('timezone') or '').strip(),
            'role': {
                'id': user.get('role', 'admin'),
                'permissions': _get_permissions(user.get('role', 'admin'))
            },
            'must_change_initial_password': _userinfo_must_change_initial_password(user_id),
        }
        
        return jsonify({
            'code': 1,
            'msg': 'Login successful',
            'data': {
                'token': token,
                'userinfo': userinfo
            }
        })
            
    except Exception as e:
        logger.error(f"Login error: {e}")
        return jsonify({'code': 500, 'msg': str(e), 'data': None}), 500


# =============================================================================
# Email Code Login
# =============================================================================

@auth_blp.route('/login-code', methods=['POST'])
def login_with_code():
    """
    Login with email verification code (quick login / register).
    If user doesn't exist, create a new account automatically.
    
    Request body:
        email: str
        code: str (verification code)
        turnstile_token: str (optional)
        referral_code: str (optional, referrer's user ID - only for new users)
    """
    ip_address = _get_client_ip()
    user_agent = _get_user_agent()
    
    try:
        from app.services.security_service import get_security_service
        from app.services.email_service import get_email_service
        from app.services.user_service import get_user_service
        from app.services.billing_service import get_billing_service
        
        security = get_security_service()
        email_service = get_email_service()
        user_service = get_user_service()
        billing_service = get_billing_service()
        
        data = request.get_json()
        if not data:
            return jsonify({'code': 0, 'msg': 'No data provided', 'data': None}), 400
        
        email = (data.get('email') or '').strip().lower()
        code = data.get('code', '').strip()
        turnstile_token = data.get('turnstile_token')
        referral_code = data.get('referral_code', '').strip()
        
        # Validate inputs
        if not email or not email_service.is_valid_email(email):
            return jsonify({'code': 0, 'msg': 'Invalid email address', 'data': None}), 400
        
        if not code:
            return jsonify({'code': 0, 'msg': 'Verification code is required', 'data': None}), 400
        
        # Verify Turnstile
        turnstile_ok, turnstile_msg = security.verify_turnstile(turnstile_token, ip_address)
        if not turnstile_ok:
            return jsonify({'code': 0, 'msg': turnstile_msg, 'data': None}), 400
        
        # Verify email code
        code_valid, code_msg = email_service.verify_code(email, code, 'login')
        if not code_valid:
            return jsonify({'code': 0, 'msg': code_msg, 'data': None}), 400
        
        # Check if user exists
        user = user_service.get_user_by_email(email)
        is_new_user = False
        
        if not user:
            # Check if registration is enabled
            if os.getenv('ENABLE_REGISTRATION', 'true').lower() != 'true':
                return jsonify({'code': 0, 'msg': 'User not found and registration is disabled', 'data': None}), 403
            
            # Auto-create user with email as username
            import re
            # Generate username from email (before @)
            base_username = re.sub(r'[^a-zA-Z0-9_]', '', email.split('@')[0])
            if not base_username or not base_username[0].isalpha():
                base_username = 'user_' + base_username
            
            # Make sure username is unique
            username = base_username
            counter = 1
            while user_service.get_user_by_username(username):
                username = f"{base_username}_{counter}"
                counter += 1
            
            # Validate referral code (user ID)
            referred_by = None
            if referral_code:
                try:
                    referrer_id = int(referral_code)
                    referrer = user_service.get_user_by_id(referrer_id)
                    if referrer and referrer.get('status') == 'active':
                        referred_by = referrer_id
                except (ValueError, TypeError):
                    pass  # Invalid referral code, ignore
            
            # Create user without password (can set later)
            user_id = user_service.create_user(
                username=username,
                password=None,  # No password for code-login users
                email=email,
                nickname=username,
                role='user',
                status='active',
                email_verified=True,
                referred_by=referred_by
            )
            
            if not user_id:
                return jsonify({'code': 0, 'msg': 'Failed to create account', 'data': None}), 500
            
            # Grant registration bonus credits
            register_bonus = int(os.getenv('CREDITS_REGISTER_BONUS', '0'))
            if register_bonus > 0:
                billing_service.add_credits(
                    user_id=user_id,
                    amount=register_bonus,
                    action='register_bonus',
                    remark='Registration bonus'
                )
            
            # Grant referral bonus to referrer
            if referred_by:
                referral_bonus = int(os.getenv('CREDITS_REFERRAL_BONUS', '0'))
                if referral_bonus > 0:
                    billing_service.add_credits(
                        user_id=referred_by,
                        amount=referral_bonus,
                        action='referral_bonus',
                        remark=f'Referral bonus for inviting user {username}',
                        reference_id=str(user_id)
                    )
            
            user = user_service.get_user_by_id(user_id)
            is_new_user = True
            
            # Log registration
            security.log_security_event('register_via_code', user_id, ip_address, user_agent, 
                                       {'email': email, 'referred_by': referred_by})
        
        # Check user status
        if user.get('status') == 'disabled':
            security.log_security_event('login_blocked', user.get('id'), ip_address, user_agent,
                                       {'reason': 'account_disabled'})
            return jsonify({'code': 0, 'msg': 'Account is disabled', 'data': None}), 403
        
        # Increment token_version (invalidates old sessions for single-client login)
        try:
            new_token_version = user_service.increment_token_version(user['id'])
        except Exception as e:
            logger.warning(f"Failed to increment token_version: {e}")
            new_token_version = 1
        
        # Generate token with new token_version
        token = generate_token(
            user_id=user['id'],
            username=user['username'],
            role=user.get('role', 'user'),
            token_version=new_token_version
        )
        
        if not token:
            return jsonify({'code': 500, 'msg': 'Token generation error', 'data': None}), 500
        
        # Update last login time
        try:
            from app.utils.db import get_db_connection
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    "UPDATE qd_users SET last_login_at = NOW() WHERE id = ?",
                    (user['id'],)
                )
                db.commit()
                affected = cur.rowcount
                cur.close()
                if affected == 0:
                    logger.error(f"Failed to update last_login_at: no rows affected for user_id={user['id']}")
                else:
                    logger.info(f"Updated last_login_at for user_id={user['id']}")
        except Exception as e:
            logger.error(f"Failed to update last_login_at for user_id={user.get('id')}: {e}")
        
        from app.services.login_notify import notify_successful_login
        notify_successful_login(
            user_id=int(user['id']),
            action='login_via_code',
            ip_address=ip_address,
            user_agent=user_agent,
            extra_details={'method': 'email_code', 'is_new_user': bool(is_new_user)},
        )

        return jsonify({
            'code': 1,
            'msg': 'Login successful' + (' (new account created)' if is_new_user else ''),
            'data': {
                'token': token,
                'is_new_user': is_new_user,
                'userinfo': {
                    'id': user['id'],
                    'username': user['username'],
                    'nickname': user.get('nickname', user['username']),
                    'email': user.get('email'),
                    'avatar': user.get('avatar', '/avatar2.jpg'),
                    'timezone': str(user.get('timezone') or '').strip(),
                    'role': {
                        'id': user.get('role', 'user'),
                        'permissions': _get_permissions(user.get('role', 'user'))
                    }
                }
            }
        })
        
    except Exception as e:
        logger.error(f"login_with_code error: {e}")
        return jsonify({'code': 0, 'msg': 'Login failed', 'data': None}), 500


# =============================================================================
# Registration Endpoints
# =============================================================================

@auth_blp.route('/send-code', methods=['POST'])
def send_verification_code():
    """
    Send verification code to email.
    
    Request body:
        email: str
        type: str (register, reset_password, change_password, change_email)
        turnstile_token: str (optional)
    """
    ip_address = _get_client_ip()
    
    try:
        from app.services.security_service import get_security_service
        from app.services.email_service import get_email_service
        
        security = get_security_service()
        email_service = get_email_service()
        
        data = request.get_json()
        if not data:
            return jsonify({'code': 0, 'msg': 'No data provided', 'data': None}), 400
        
        email = (data.get('email') or '').strip().lower()
        code_type = data.get('type', 'register')
        turnstile_token = data.get('turnstile_token')
        
        # Validate email
        if not email or not email_service.is_valid_email(email):
            return jsonify({'code': 0, 'msg': 'Invalid email address', 'data': None}), 400
        
        # For change_password type with logged-in user, skip Turnstile verification
        # because user already authenticated
        skip_turnstile = False
        if code_type == 'change_password':
            # Try to get user_id from token (this route doesn't require login)
            from app.utils.auth import verify_token
            auth_header = request.headers.get('Authorization')
            if auth_header:
                parts = auth_header.split()
                if len(parts) == 2 and parts[0].lower() == 'bearer':
                    payload = verify_token(parts[1])
                    if payload and payload.get('user_id'):
                        skip_turnstile = True
        
        # Verify Turnstile (skip for authenticated change_password requests)
        if not skip_turnstile:
            turnstile_ok, turnstile_msg = security.verify_turnstile(turnstile_token, ip_address)
            if not turnstile_ok:
                return jsonify({'code': 0, 'msg': turnstile_msg, 'data': None}), 400
        
        # Check rate limit
        can_send, rate_msg = security.can_send_verification_code(email, ip_address)
        if not can_send:
            return jsonify({'code': 0, 'msg': rate_msg, 'data': None}), 429
        
        # For registration, check if email already exists
        if code_type == 'register':
            from app.services.user_service import get_user_service
            existing = get_user_service().get_user_by_email(email)
            if existing:
                return jsonify({'code': 0, 'msg': 'Email already registered', 'data': None}), 400
        
        # For login type - always allow (will auto-create if not exists)
        # No special check needed
        
        # For reset_password, check if email exists
        if code_type == 'reset_password':
            from app.services.user_service import get_user_service
            existing = get_user_service().get_user_by_email(email)
            if not existing:
                # Don't reveal if email exists or not (security best practice)
                # But still return success to prevent email enumeration
                return jsonify({'code': 1, 'msg': 'If the email exists, a verification code has been sent', 'data': None})
        
        # Send verification code
        success, msg = email_service.send_verification_code(email, code_type, ip_address)
        
        if success:
            security.log_security_event('verification_code_sent', None, ip_address, 
                                       _get_user_agent(), {'email': email, 'type': code_type})
            return jsonify({'code': 1, 'msg': 'Verification code sent', 'data': None})
        else:
            return jsonify({'code': 0, 'msg': msg, 'data': None}), 500
            
    except Exception as e:
        logger.error(f"send_verification_code error: {e}")
        return jsonify({'code': 0, 'msg': 'Failed to send verification code', 'data': None}), 500


@auth_blp.route('/register', methods=['POST'])
def register():
    """
    Register new user with email verification.
    
    Request body:
        email: str
        code: str (verification code)
        username: str
        password: str
        turnstile_token: str (optional)
        referral_code: str (optional, referrer's user ID)
    """
    ip_address = _get_client_ip()
    user_agent = _get_user_agent()
    
    try:
        # Check if registration is enabled
        if os.getenv('ENABLE_REGISTRATION', 'true').lower() != 'true':
            return jsonify({'code': 0, 'msg': 'Registration is disabled', 'data': None}), 403
        
        from app.services.security_service import get_security_service
        from app.services.email_service import get_email_service
        from app.services.user_service import get_user_service
        from app.services.billing_service import get_billing_service
        
        security = get_security_service()
        email_service = get_email_service()
        user_service = get_user_service()
        billing_service = get_billing_service()
        
        data = request.get_json()
        if not data:
            return jsonify({'code': 0, 'msg': 'No data provided', 'data': None}), 400
        
        email = (data.get('email') or '').strip().lower()
        code = data.get('code', '').strip()
        username = (data.get('username') or '').strip()
        password = data.get('password', '')
        turnstile_token = data.get('turnstile_token')
        referral_code = data.get('referral_code', '').strip()
        
        # Validate inputs
        if not email or not email_service.is_valid_email(email):
            return jsonify({'code': 0, 'msg': 'Invalid email address', 'data': None}), 400
        
        if not code:
            return jsonify({'code': 0, 'msg': 'Verification code is required', 'data': None}), 400
        
        if not username or len(username) < 3 or len(username) > 30:
            return jsonify({'code': 0, 'msg': 'Username must be 3-30 characters', 'data': None}), 400
        
        # Validate username format (alphanumeric and underscore only)
        import re
        if not re.match(r'^[a-zA-Z][a-zA-Z0-9_]*$', username):
            return jsonify({'code': 0, 'msg': 'Username must start with letter and contain only letters, numbers, and underscores', 'data': None}), 400
        
        # Validate password strength
        pwd_valid, pwd_msg = security.validate_password_strength(password)
        if not pwd_valid:
            return jsonify({'code': 0, 'msg': pwd_msg, 'data': None}), 400
        
        # Verify Turnstile
        turnstile_ok, turnstile_msg = security.verify_turnstile(turnstile_token, ip_address)
        if not turnstile_ok:
            return jsonify({'code': 0, 'msg': turnstile_msg, 'data': None}), 400
        
        # Verify email code
        code_valid, code_msg = email_service.verify_code(email, code, 'register')
        if not code_valid:
            return jsonify({'code': 0, 'msg': code_msg, 'data': None}), 400
        
        # Check if username already exists
        existing_user = user_service.get_user_by_username(username)
        if existing_user:
            return jsonify({'code': 0, 'msg': 'Username already taken', 'data': None}), 400
        
        # Check if email already exists
        existing_email = user_service.get_user_by_email(email)
        if existing_email:
            return jsonify({'code': 0, 'msg': 'Email already registered', 'data': None}), 400
        
        # Validate referral code (user ID)
        referred_by = None
        if referral_code:
            try:
                referrer_id = int(referral_code)
                referrer = user_service.get_user_by_id(referrer_id)
                if referrer and referrer.get('status') == 'active':
                    referred_by = referrer_id
            except (ValueError, TypeError):
                pass  # Invalid referral code, ignore
        
        # Create user
        user_id = user_service.create_user(
            username=username,
            password=password,
            email=email,
            nickname=username,
            role='user',
            status='active',
            email_verified=True,
            referred_by=referred_by
        )
        
        if not user_id:
            return jsonify({'code': 0, 'msg': 'Failed to create account', 'data': None}), 500
        
        # Grant registration bonus credits
        register_bonus = int(os.getenv('CREDITS_REGISTER_BONUS', '0'))
        if register_bonus > 0:
            billing_service.add_credits(
                user_id=user_id,
                amount=register_bonus,
                action='register_bonus',
                remark='Registration bonus'
            )
        
        # Grant referral bonus to referrer
        if referred_by:
            referral_bonus = int(os.getenv('CREDITS_REFERRAL_BONUS', '0'))
            if referral_bonus > 0:
                billing_service.add_credits(
                    user_id=referred_by,
                    amount=referral_bonus,
                    action='referral_bonus',
                    remark=f'Referral bonus for inviting user {username}',
                    reference_id=str(user_id)
                )
        
        # Log registration
        security.log_security_event('register', user_id, ip_address, user_agent, 
                                   {'email': email, 'referred_by': referred_by})
        
        # Auto login after registration (get token_version for new user)
        try:
            new_token_version = user_service.get_token_version(user_id)
        except Exception as e:
            logger.warning(f"Failed to get token_version: {e}")
            new_token_version = 1
        
        token = generate_token(
            user_id=user_id, 
            username=username, 
            role='user',
            token_version=new_token_version
        )

        # Update last login time (auto-login after registration)
        try:
            from app.utils.db import get_db_connection
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    "UPDATE qd_users SET last_login_at = NOW() WHERE id = ?",
                    (user_id,)
                )
                db.commit()
                affected = cur.rowcount
                cur.close()
                if affected == 0:
                    logger.error(f"Failed to update last_login_at: no rows affected for user_id={user_id}")
                else:
                    logger.info(f"Updated last_login_at for user_id={user_id}")
        except Exception as e:
            logger.error(f"Failed to update last_login_at for user_id={user_id}: {e}")
        
        return jsonify({
            'code': 1,
            'msg': 'Registration successful',
            'data': {
                'token': token,
                'userinfo': {
                    'id': user_id,
                    'username': username,
                    'nickname': username,
                    'email': email,
                    'avatar': '/avatar2.jpg',
                    'timezone': '',
                    'role': {
                        'id': 'user',
                        'permissions': _get_permissions('user')
                    }
                }
            }
        })
        
    except Exception as e:
        logger.error(f"register error: {e}")
        return jsonify({'code': 0, 'msg': 'Registration failed', 'data': None}), 500


@auth_blp.route('/reset-password', methods=['POST'])
def reset_password():
    """
    Reset password with email verification.
    
    Request body:
        email: str
        code: str (verification code)
        new_password: str
        turnstile_token: str (optional)
    """
    ip_address = _get_client_ip()
    user_agent = _get_user_agent()
    
    try:
        from app.services.security_service import get_security_service
        from app.services.email_service import get_email_service
        from app.services.user_service import get_user_service
        
        security = get_security_service()
        email_service = get_email_service()
        user_service = get_user_service()
        
        data = request.get_json()
        if not data:
            return jsonify({'code': 0, 'msg': 'No data provided', 'data': None}), 400
        
        email = (data.get('email') or '').strip().lower()
        code = data.get('code', '').strip()
        new_password = data.get('new_password', '')
        turnstile_token = data.get('turnstile_token')
        
        # Validate inputs
        if not email or not code or not new_password:
            return jsonify({'code': 0, 'msg': 'Missing required fields', 'data': None}), 400
        
        # Validate password strength
        pwd_valid, pwd_msg = security.validate_password_strength(new_password)
        if not pwd_valid:
            return jsonify({'code': 0, 'msg': pwd_msg, 'data': None}), 400
        
        # Verify Turnstile
        turnstile_ok, turnstile_msg = security.verify_turnstile(turnstile_token, ip_address)
        if not turnstile_ok:
            return jsonify({'code': 0, 'msg': turnstile_msg, 'data': None}), 400
        
        # Verify email code
        code_valid, code_msg = email_service.verify_code(email, code, 'reset_password')
        if not code_valid:
            return jsonify({'code': 0, 'msg': code_msg, 'data': None}), 400
        
        # Get user by email
        user = user_service.get_user_by_email(email)
        if not user:
            return jsonify({'code': 0, 'msg': 'User not found', 'data': None}), 404
        
        # Update password
        success = user_service.update_password(user['id'], new_password)
        if not success:
            return jsonify({'code': 0, 'msg': 'Failed to reset password', 'data': None}), 500
        
        # Clear any existing login blocks for this account
        security.clear_login_attempts(user['username'], 'account')
        
        # Log password reset
        security.log_security_event('password_reset', user['id'], ip_address, user_agent)
        
        return jsonify({'code': 1, 'msg': 'Password reset successful', 'data': None})
        
    except Exception as e:
        logger.error(f"reset_password error: {e}")
        return jsonify({'code': 0, 'msg': 'Password reset failed', 'data': None}), 500


@auth_blp.route('/change-password', methods=['POST'])
@login_required
def change_password():
    """
    Change password with email verification (for logged-in users).
    
    Request body:
        code: str (verification code sent to user's email)
        new_password: str
    """
    ip_address = _get_client_ip()
    user_agent = _get_user_agent()
    user_id = g.user_id
    
    try:
        from app.services.security_service import get_security_service
        from app.services.email_service import get_email_service
        from app.services.user_service import get_user_service
        
        security = get_security_service()
        email_service = get_email_service()
        user_service = get_user_service()
        
        data = request.get_json()
        if not data:
            return jsonify({'code': 0, 'msg': 'No data provided', 'data': None}), 400
        
        code = data.get('code', '').strip()
        new_password = data.get('new_password', '')
        
        if not code or not new_password:
            return jsonify({'code': 0, 'msg': 'Missing required fields', 'data': None}), 400
        
        # Validate password strength
        pwd_valid, pwd_msg = security.validate_password_strength(new_password)
        if not pwd_valid:
            return jsonify({'code': 0, 'msg': pwd_msg, 'data': None}), 400
        
        # Get user
        user = user_service.get_user_by_id(user_id)
        if not user or not user.get('email'):
            return jsonify({'code': 0, 'msg': 'User email not found', 'data': None}), 400
        
        # Verify email code
        code_valid, code_msg = email_service.verify_code(user['email'], code, 'change_password')
        if not code_valid:
            return jsonify({'code': 0, 'msg': code_msg, 'data': None}), 400
        
        # Update password
        success = user_service.update_password(user_id, new_password)
        if not success:
            return jsonify({'code': 0, 'msg': 'Failed to change password', 'data': None}), 500
        
        # Log password change
        security.log_security_event('password_changed', user_id, ip_address, user_agent)
        
        return jsonify({'code': 1, 'msg': 'Password changed successfully', 'data': None})
        
    except Exception as e:
        logger.error(f"change_password error: {e}")
        return jsonify({'code': 0, 'msg': 'Password change failed', 'data': None}), 500


# =============================================================================
# OAuth Endpoints
# =============================================================================

@auth_blp.route('/oauth/google', methods=['GET'])
def oauth_google():
    """Redirect to Google OAuth authorization page.

    Query params:
        redirect: optional front-end URL (must be allow-listed). When provided,
                  after successful login the user is redirected there instead of
                  the default FRONTEND_URL. Supports multi-frontend (PC + mobile).
    """
    try:
        from app.services.oauth_service import get_oauth_service
        oauth = get_oauth_service()

        if not oauth.google_enabled:
            return jsonify({'code': 0, 'msg': 'Google OAuth is not configured', 'data': None}), 400

        redirect_url = (request.args.get('redirect') or '').strip()
        auth_url, state = oauth.get_google_auth_url(redirect_url=redirect_url)
        return redirect(auth_url)

    except Exception as e:
        logger.error(f"oauth_google error: {e}")
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@auth_blp.route('/oauth/google/callback', methods=['GET'])
def oauth_google_callback():
    """Handle Google OAuth callback"""
    ip_address = _get_client_ip()
    user_agent = _get_user_agent()
    
    try:
        from app.services.oauth_service import get_oauth_service
        from app.services.security_service import get_security_service
        
        oauth = get_oauth_service()
        security = get_security_service()
        
        code = request.args.get('code')
        state = request.args.get('state')
        error = request.args.get('error')

        # Peek the per-state redirect (set when the flow was initiated) before
        # handle_* consumes the state; fall back to default FRONTEND_URL.
        state_redirect = oauth.peek_state_redirect(state) if state else ''
        frontend_url = state_redirect or oauth.frontend_url

        if error:
            return redirect(_build_frontend_login_redirect(frontend_url, oauth_error=error))

        if not code or not state:
            return redirect(_build_frontend_login_redirect(frontend_url, oauth_error='missing_params'))

        # Handle callback
        success, result = oauth.handle_google_callback(code, state)
        if not success:
            error_msg = result.get('error', 'unknown_error')
            return redirect(_build_frontend_login_redirect(frontend_url, oauth_error=error_msg))
        
        # Get or create user
        user_success, user_result = oauth.get_or_create_user_from_oauth(result)
        if not user_success:
            error_msg = user_result.get('error', 'user_creation_failed')
            return redirect(_build_frontend_login_redirect(frontend_url, oauth_error=error_msg))
        
        # Increment token_version (invalidates old sessions for single-client login)
        from app.services.user_service import get_user_service
        user_service = get_user_service()
        try:
            new_token_version = user_service.increment_token_version(user_result['id'])
        except Exception as e:
            logger.warning(f"Failed to increment token_version: {e}")
            new_token_version = 1
        
        # Generate token with new token_version
        token = generate_token(
            user_id=user_result['id'],
            username=user_result['username'],
            role=user_result.get('role', 'user'),
            token_version=new_token_version
        )
        
        from app.services.login_notify import notify_successful_login
        notify_successful_login(
            user_id=int(user_result['id']),
            action='oauth_login',
            ip_address=ip_address,
            user_agent=user_agent,
            extra_details={'provider': 'google', 'method': 'oauth'},
        )

        # Redirect to frontend with token
        return redirect(_build_frontend_login_redirect(frontend_url, oauth_token=token))
        
    except Exception as e:
        logger.error(f"oauth_google_callback error: {e}")
        from app.services.oauth_service import get_oauth_service
        frontend_url = get_oauth_service().frontend_url
        return redirect(_build_frontend_login_redirect(frontend_url, oauth_error='server_error'))


@auth_blp.route('/oauth/github', methods=['GET'])
def oauth_github():
    """Redirect to GitHub OAuth authorization page.

    Query params:
        redirect: optional front-end URL (must be allow-listed), see oauth_google.
    """
    try:
        from app.services.oauth_service import get_oauth_service
        oauth = get_oauth_service()

        if not oauth.github_enabled:
            return jsonify({'code': 0, 'msg': 'GitHub OAuth is not configured', 'data': None}), 400

        redirect_url = (request.args.get('redirect') or '').strip()
        auth_url, state = oauth.get_github_auth_url(redirect_url=redirect_url)
        return redirect(auth_url)

    except Exception as e:
        logger.error(f"oauth_github error: {e}")
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@auth_blp.route('/oauth/github/callback', methods=['GET'])
def oauth_github_callback():
    """Handle GitHub OAuth callback"""
    ip_address = _get_client_ip()
    user_agent = _get_user_agent()
    
    try:
        from app.services.oauth_service import get_oauth_service
        from app.services.security_service import get_security_service
        
        oauth = get_oauth_service()
        security = get_security_service()
        
        code = request.args.get('code')
        state = request.args.get('state')
        error = request.args.get('error')

        state_redirect = oauth.peek_state_redirect(state) if state else ''
        frontend_url = state_redirect or oauth.frontend_url

        if error:
            return redirect(_build_frontend_login_redirect(frontend_url, oauth_error=error))

        if not code or not state:
            return redirect(_build_frontend_login_redirect(frontend_url, oauth_error='missing_params'))

        # Handle callback
        success, result = oauth.handle_github_callback(code, state)
        if not success:
            error_msg = result.get('error', 'unknown_error')
            return redirect(_build_frontend_login_redirect(frontend_url, oauth_error=error_msg))
        
        # Get or create user
        user_success, user_result = oauth.get_or_create_user_from_oauth(result)
        if not user_success:
            error_msg = user_result.get('error', 'user_creation_failed')
            return redirect(_build_frontend_login_redirect(frontend_url, oauth_error=error_msg))
        
        # Increment token_version (invalidates old sessions for single-client login)
        from app.services.user_service import get_user_service
        user_service = get_user_service()
        try:
            new_token_version = user_service.increment_token_version(user_result['id'])
        except Exception as e:
            logger.warning(f"Failed to increment token_version: {e}")
            new_token_version = 1
        
        # Generate token with new token_version
        token = generate_token(
            user_id=user_result['id'],
            username=user_result['username'],
            role=user_result.get('role', 'user'),
            token_version=new_token_version
        )
        
        from app.services.login_notify import notify_successful_login
        notify_successful_login(
            user_id=int(user_result['id']),
            action='oauth_login',
            ip_address=ip_address,
            user_agent=user_agent,
            extra_details={'provider': 'github', 'method': 'oauth'},
        )

        # Redirect to frontend with token
        return redirect(_build_frontend_login_redirect(frontend_url, oauth_token=token))
        
    except Exception as e:
        logger.error(f"oauth_github_callback error: {e}")
        from app.services.oauth_service import get_oauth_service
        frontend_url = get_oauth_service().frontend_url
        return redirect(_build_frontend_login_redirect(frontend_url, oauth_error='server_error'))


# =============================================================================
# Other Endpoints
# =============================================================================

@auth_blp.route('/logout', methods=['POST'])
def logout():
    """Logout (client removes token; server is stateless)."""
    return jsonify({'code': 1, 'msg': 'Logout successful', 'data': None})


@auth_blp.route('/info', methods=['GET'])
@login_required
def get_user_info():
    """Get current user info."""
    try:
        user_id = getattr(g, 'user_id', 1)
        username = getattr(g, 'user', Config.ADMIN_USER)
        role = getattr(g, 'user_role', 'admin')
        
        # Try to get full user info from database
        user_data = None
        if not _is_single_user_mode():
            try:
                from app.services.user_service import get_user_service
                user_data = get_user_service().get_user_by_id(user_id)
            except Exception as e:
                logger.warning(f"Failed to get user from database: {e}")
        
        if user_data:
            uid = user_data.get('id')
            return jsonify({
                'code': 1,
                'msg': 'Success',
                'data': {
                    'id': uid,
                    'username': user_data.get('username'),
                    'nickname': user_data.get('nickname', 'User'),
                    'email': user_data.get('email'),
                    'avatar': user_data.get('avatar', '/avatar2.jpg'),
                    'timezone': str(user_data.get('timezone') or '').strip(),
                    'role': {
                        'id': user_data.get('role', 'user'),
                        'permissions': _get_permissions(user_data.get('role', 'user'))
                    },
                    'must_change_initial_password': _userinfo_must_change_initial_password(uid),
                }
            })
        
        # Fallback for legacy mode
        return jsonify({
            'code': 1,
            'msg': 'Success',
            'data': {
                'id': user_id,
                'username': username,
                'nickname': 'Admin',
                'avatar': '/avatar2.jpg',
                'timezone': '',
                'role': {
                    'id': role,
                    'permissions': _get_permissions(role)
                }
            }
        })
    except Exception as e:
        logger.error(f"get_user_info error: {e}")
        return jsonify({'code': 500, 'msg': str(e), 'data': None}), 500


def _get_permissions(role: str) -> list:
    """Get permissions list for a role"""
    try:
        from app.services.user_service import get_user_service
        return get_user_service().get_user_permissions(role)
    except Exception:
        # Default permissions for admin
        if role == 'admin':
            return ['dashboard', 'view', 'indicator', 'backtest', 'strategy', 
                    'portfolio', 'settings', 'user_manage', 'credentials']
        return ['dashboard', 'view', 'indicator', 'backtest', 'strategy', 'portfolio']

# openapi-compat: legacy import name
auth_bp = auth_blp
