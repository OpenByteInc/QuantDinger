"""
OAuth Service - Handles Google and GitHub OAuth authentication.
"""
import os
import secrets
import requests
from urllib.parse import urlencode, urlparse
from datetime import datetime, timezone, timedelta
from typing import Tuple, Optional, Dict, Any
from app.utils.db import get_db_connection
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Singleton instance
_oauth_service = None


def get_oauth_service():
    """Get singleton OAuthService instance"""
    global _oauth_service
    if _oauth_service is None:
        _oauth_service = OAuthService()
    return _oauth_service


class OAuthService:
    """OAuth service for Google and GitHub authentication"""

    # Class-level cache: only create OAuth state table once per process
    _state_schema_ensured: bool = False

    def __init__(self):
        self._load_config()

    def _oauth_state_ttl_minutes(self) -> int:
        try:
            return max(5, min(120, int(float(os.getenv("OAUTH_STATE_TTL_MINUTES", "20") or 20))))
        except Exception:
            return 20

    def _ensure_oauth_state_schema(self, cur) -> None:
        """Create qd_oauth_states table if missing.

        OAuth state MUST be shared across Gunicorn workers / replicas; an
        in-memory dict breaks multi-worker deployments (authorize hits worker
        A, callback lands on worker B → Invalid state).
        """
        if OAuthService._state_schema_ensured:
            return
        try:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS qd_oauth_states (
                    state VARCHAR(128) PRIMARY KEY,
                    provider VARCHAR(20) NOT NULL,
                    redirect TEXT,
                    client_type VARCHAR(16) NOT NULL DEFAULT 'pc',
                    created_at TIMESTAMP DEFAULT NOW(),
                    expires_at TIMESTAMP NOT NULL
                )
                """
            )
            cur.execute(
                """
                ALTER TABLE qd_oauth_states
                ADD COLUMN IF NOT EXISTS client_type VARCHAR(16) NOT NULL DEFAULT 'pc'
                """
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_oauth_states_expires ON qd_oauth_states(expires_at)"
            )
            OAuthService._state_schema_ensured = True
        except Exception as e:
            logger.debug(f"_ensure_oauth_state_schema: {e}")

    def _oauth_state_save(
        self,
        state: str,
        provider: str,
        redirect: Optional[str],
        client_type: str,
    ) -> None:
        # Store naive UTC for TIMESTAMP WITHOUT TIME ZONE columns.
        exp = (
            datetime.now(timezone.utc) + timedelta(minutes=self._oauth_state_ttl_minutes())
        ).replace(tzinfo=None)
        red = (redirect or "").strip() if redirect else ""
        with get_db_connection() as db:
            cur = db.cursor()
            self._ensure_oauth_state_schema(cur)
            try:
                cur.execute("DELETE FROM qd_oauth_states WHERE expires_at < NOW()")
            except Exception:
                pass
            # IMPORTANT: include RETURNING state explicitly.  Otherwise the
            # PostgresCursor wrapper would auto-append RETURNING id, and this
            # table has no "id" column → INSERT fails with UndefinedColumn.
            cur.execute(
                """
                INSERT INTO qd_oauth_states (
                    state, provider, redirect, client_type, expires_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (state) DO UPDATE
                  SET provider = EXCLUDED.provider,
                      redirect = EXCLUDED.redirect,
                      client_type = EXCLUDED.client_type,
                      expires_at = EXCLUDED.expires_at
                RETURNING state
                """,
                (state, provider, red, client_type, exp),
            )
            try:
                cur.fetchone()
            except Exception:
                pass
            db.commit()
            cur.close()

    def _oauth_state_peek_redirect(self, state: str) -> str:
        if not state:
            return ""
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                self._ensure_oauth_state_schema(cur)
                cur.execute(
                    "SELECT redirect FROM qd_oauth_states WHERE state = ? AND expires_at > NOW()",
                    (state,),
                )
                row = cur.fetchone()
                cur.close()
                if not row:
                    return ""
                return (row.get("redirect") or "").strip()
        except Exception as e:
            logger.warning(f"OAuth state peek failed: {e}")
            return ""

    def _oauth_state_peek_client_type(self, state: str) -> str:
        if not state:
            return "pc"
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                self._ensure_oauth_state_schema(cur)
                cur.execute(
                    "SELECT client_type FROM qd_oauth_states WHERE state = ? AND expires_at > NOW()",
                    (state,),
                )
                row = cur.fetchone()
                cur.close()
                value = (row.get("client_type") or "pc").strip().lower() if row else "pc"
                return value if value in ("pc", "mobile") else "pc"
        except Exception as e:
            logger.warning(f"OAuth state client type peek failed: {e}")
            return "pc"

    def _oauth_state_consume(self, state: str, provider: str) -> bool:
        """Delete and validate state in one statement; True iff a row was removed."""
        if not state:
            return False
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                self._ensure_oauth_state_schema(cur)
                cur.execute(
                    "DELETE FROM qd_oauth_states WHERE state = ? AND provider = ? AND expires_at > NOW()",
                    (state, provider),
                )
                n = int(getattr(cur, "rowcount", 0) or 0)
                db.commit()
                cur.close()
                return n > 0
        except Exception as e:
            logger.error(f"OAuth state consume failed: {e}", exc_info=True)
            return False
    
    def _load_config(self):
        """Load OAuth configuration from environment variables"""
        # Google OAuth
        self.google_client_id = os.getenv('GOOGLE_CLIENT_ID', '')
        self.google_client_secret = os.getenv('GOOGLE_CLIENT_SECRET', '')
        self.google_redirect_uri = os.getenv('GOOGLE_REDIRECT_URI', '')
        self.google_enabled = bool(self.google_client_id and self.google_client_secret)
        
        # GitHub OAuth
        self.github_client_id = os.getenv('GITHUB_CLIENT_ID', '')
        self.github_client_secret = os.getenv('GITHUB_CLIENT_SECRET', '')
        self.github_redirect_uri = os.getenv('GITHUB_REDIRECT_URI', '')
        self.github_enabled = bool(self.github_client_id and self.github_client_secret)
        
        # Frontend URL for redirect after OAuth.
        #
        # FRONTEND_URL accepts a comma-separated list of origins (the same
        # convention used by CORS in `app/__init__.py`). This lets one backend
        # serve e.g. ai.quantdinger.com + m.quantdinger.com without forcing the
        # operator to also fill OAUTH_ALLOWED_REDIRECTS.
        #
        # The FIRST entry is the default post-login redirect target. Every
        # entry is added to the allow-list. If we ever stored the raw
        # comma-joined string as a single URL we'd build a redirect like
        # "https://a.example.com,https://b.example.com?oauth_token=..." and
        # the browser would land on a malformed page (see bug report
        # 2026-05-14: stray comma + missing colon in the second origin).
        raw_frontend = os.getenv('FRONTEND_URL', 'http://localhost:8080')
        frontend_list = [x.strip() for x in raw_frontend.split(',') if x.strip()]
        self.frontend_url = frontend_list[0] if frontend_list else 'http://localhost:8080'

        # Allow-listed origins that may be used as post-login redirect targets
        # (comma-separated). FRONTEND_URL entries are always allowed.
        raw_allowed = os.getenv('OAUTH_ALLOWED_REDIRECTS', '')
        extra = [x.strip() for x in raw_allowed.split(',') if x.strip()]
        self.allowed_redirect_origins = set()
        for item in frontend_list + extra:
            origin = self._normalize_origin(item)
            if origin:
                self.allowed_redirect_origins.add(origin)

    @staticmethod
    def _normalize_origin(url: str) -> str:
        """Return scheme://host[:port] for a URL, empty string if invalid."""
        if not url:
            return ''
        try:
            parsed = urlparse(url if '://' in url else f'https://{url}')
            if not parsed.scheme or not parsed.netloc:
                return ''
            return f"{parsed.scheme}://{parsed.netloc}".lower().rstrip('/')
        except Exception:
            return ''

    def is_redirect_allowed(self, redirect_url: str) -> bool:
        """Check whether the given URL origin is on the allow-list."""
        origin = self._normalize_origin(redirect_url)
        if not origin:
            return False
        return origin in self.allowed_redirect_origins

    def peek_state_redirect(self, state: str) -> str:
        """Read the redirect URL associated with a pending OAuth state (no deletion)."""
        return self._oauth_state_peek_redirect(state)

    def peek_state_client_type(self, state: str) -> str:
        """Read the client session class associated with a pending OAuth state."""
        return self._oauth_state_peek_client_type(state)

    # =========================================================================
    # Google OAuth
    # =========================================================================

    def get_google_auth_url(
        self,
        state: str = None,
        redirect_url: str = None,
        client_type: str = "pc",
    ) -> Tuple[str, str]:
        """
        Generate Google OAuth authorization URL.

        Args:
            state: Optional preset state token.
            redirect_url: Optional front-end URL to redirect back to after login.
                          Must be in the allow-list, otherwise ignored.
        Returns:
            (auth_url, state)
        """
        if not self.google_enabled:
            return '', ''

        state = state or secrets.token_urlsafe(32)
        red = ""
        if redirect_url and self.is_redirect_allowed(redirect_url):
            red = redirect_url.strip()
        try:
            self._oauth_state_save(state, "google", red or None, client_type)
        except Exception as e:
            logger.error(f"Failed to persist Google OAuth state: {e}")
            return '', ''

        params = {
            'client_id': self.google_client_id,
            'redirect_uri': self.google_redirect_uri,
            'response_type': 'code',
            'scope': 'openid email profile',
            'state': state,
            'access_type': 'offline',
            'prompt': 'select_account'
        }
        
        auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"
        return auth_url, state
    
    def handle_google_callback(self, code: str, state: str) -> Tuple[bool, Dict[str, Any]]:
        """
        Handle Google OAuth callback.
        
        Args:
            code: Authorization code from Google
            state: State parameter for CSRF protection
        
        Returns:
            (success, user_info_or_error)
        """
        if not self._oauth_state_consume(state, "google"):
            return False, {'error': 'Invalid state parameter'}

        try:
            # Exchange code for tokens
            token_response = requests.post(
                'https://oauth2.googleapis.com/token',
                data={
                    'code': code,
                    'client_id': self.google_client_id,
                    'client_secret': self.google_client_secret,
                    'redirect_uri': self.google_redirect_uri,
                    'grant_type': 'authorization_code'
                },
                timeout=10
            )
            
            if token_response.status_code != 200:
                logger.error(f"Google token exchange failed: {token_response.text}")
                return False, {'error': 'Failed to exchange authorization code'}
            
            tokens = token_response.json()
            access_token = tokens.get('access_token')
            
            # Get user info
            user_response = requests.get(
                'https://www.googleapis.com/oauth2/v2/userinfo',
                headers={'Authorization': f'Bearer {access_token}'},
                timeout=10
            )
            
            if user_response.status_code != 200:
                logger.error(f"Google user info failed: {user_response.text}")
                return False, {'error': 'Failed to get user information'}
            
            user_info = user_response.json()
            
            return True, {
                'provider': 'google',
                'provider_user_id': user_info.get('id'),
                'email': user_info.get('email'),
                'name': user_info.get('name'),
                'avatar': user_info.get('picture'),
                'access_token': access_token,
                'refresh_token': tokens.get('refresh_token')
            }
            
        except requests.RequestException as e:
            logger.error(f"Google OAuth error: {e}")
            return False, {'error': 'OAuth service unavailable'}
    
    # =========================================================================
    # GitHub OAuth
    # =========================================================================
    
    def get_github_auth_url(
        self,
        state: str = None,
        redirect_url: str = None,
        client_type: str = "pc",
    ) -> Tuple[str, str]:
        """
        Generate GitHub OAuth authorization URL.

        Returns:
            (auth_url, state)
        """
        if not self.github_enabled:
            return '', ''

        state = state or secrets.token_urlsafe(32)
        red = ""
        if redirect_url and self.is_redirect_allowed(redirect_url):
            red = redirect_url.strip()
        try:
            self._oauth_state_save(state, "github", red or None, client_type)
        except Exception as e:
            logger.error(f"Failed to persist GitHub OAuth state: {e}")
            return '', ''

        params = {
            'client_id': self.github_client_id,
            'redirect_uri': self.github_redirect_uri,
            'scope': 'user:email read:user',
            'state': state
        }
        
        auth_url = f"https://github.com/login/oauth/authorize?{urlencode(params)}"
        return auth_url, state
    
    def handle_github_callback(self, code: str, state: str) -> Tuple[bool, Dict[str, Any]]:
        """
        Handle GitHub OAuth callback.
        
        Args:
            code: Authorization code from GitHub
            state: State parameter for CSRF protection
        
        Returns:
            (success, user_info_or_error)
        """
        if not self._oauth_state_consume(state, "github"):
            return False, {'error': 'Invalid state parameter'}

        try:
            # Exchange code for token
            token_response = requests.post(
                'https://github.com/login/oauth/access_token',
                data={
                    'client_id': self.github_client_id,
                    'client_secret': self.github_client_secret,
                    'code': code,
                    'redirect_uri': self.github_redirect_uri
                },
                headers={'Accept': 'application/json'},
                timeout=10
            )
            
            if token_response.status_code != 200:
                logger.error(f"GitHub token exchange failed: {token_response.text}")
                return False, {'error': 'Failed to exchange authorization code'}
            
            tokens = token_response.json()
            access_token = tokens.get('access_token')
            
            if not access_token:
                error = tokens.get('error_description', 'Unknown error')
                logger.error(f"GitHub token error: {error}")
                return False, {'error': error}
            
            # Get user info
            user_response = requests.get(
                'https://api.github.com/user',
                headers={
                    'Authorization': f'Bearer {access_token}',
                    'Accept': 'application/vnd.github.v3+json'
                },
                timeout=10
            )
            
            if user_response.status_code != 200:
                logger.error(f"GitHub user info failed: {user_response.text}")
                return False, {'error': 'Failed to get user information'}
            
            user_info = user_response.json()
            
            # Get user email (might be private)
            email = user_info.get('email')
            if not email:
                email_response = requests.get(
                    'https://api.github.com/user/emails',
                    headers={
                        'Authorization': f'Bearer {access_token}',
                        'Accept': 'application/vnd.github.v3+json'
                    },
                    timeout=10
                )
                if email_response.status_code == 200:
                    emails = email_response.json()
                    # Find primary email
                    for e in emails:
                        if e.get('primary') and e.get('verified'):
                            email = e.get('email')
                            break
                    # Fallback to any verified email
                    if not email:
                        for e in emails:
                            if e.get('verified'):
                                email = e.get('email')
                                break
            
            return True, {
                'provider': 'github',
                'provider_user_id': str(user_info.get('id')),
                'email': email,
                'name': user_info.get('name') or user_info.get('login'),
                'avatar': user_info.get('avatar_url'),
                'access_token': access_token,
                'refresh_token': None  # GitHub doesn't use refresh tokens
            }
            
        except requests.RequestException as e:
            logger.error(f"GitHub OAuth error: {e}")
            return False, {'error': 'OAuth service unavailable'}
    
    # =========================================================================
    # OAuth Link Management
    # =========================================================================
    
    def get_or_create_user_from_oauth(self, oauth_info: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        """
        Get existing user or create new user from OAuth info.
        
        Args:
            oauth_info: Dict with provider, provider_user_id, email, name, avatar, tokens
        
        Returns:
            (success, user_or_error)
        """
        provider = oauth_info['provider']
        provider_user_id = oauth_info['provider_user_id']
        email = oauth_info.get('email')
        name = oauth_info.get('name', '')
        avatar = oauth_info.get('avatar', '/avatar2.jpg')
        
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                
                # Check if OAuth link exists
                cur.execute(
                    """
                    SELECT user_id FROM qd_oauth_links
                    WHERE provider = ? AND provider_user_id = ?
                    """,
                    (provider, provider_user_id)
                )
                link = cur.fetchone()
                
                if link:
                    # Existing OAuth link - get user
                    user_id = link['user_id']
                    cur.execute(
                        """
                        SELECT id, username, email, nickname, avatar, status, role
                        FROM qd_users WHERE id = ?
                        """,
                        (user_id,)
                    )
                    user = cur.fetchone()
                    
                    if user:
                        # Update OAuth tokens
                        cur.execute(
                            """
                            UPDATE qd_oauth_links 
                            SET access_token = ?, refresh_token = ?, updated_at = NOW()
                            WHERE provider = ? AND provider_user_id = ?
                            """,
                            (oauth_info.get('access_token'), oauth_info.get('refresh_token'),
                             provider, provider_user_id)
                        )
                        
                        # Update last login
                        cur.execute(
                            "UPDATE qd_users SET last_login_at = NOW() WHERE id = ?",
                            (user_id,)
                        )
                        db.commit()
                        cur.close()
                        from app.services.user_service import UserService
                        return True, UserService.normalize_user_record(user)
                    else:
                        # Orphaned OAuth link - remove it
                        cur.execute(
                            "DELETE FROM qd_oauth_links WHERE provider = ? AND provider_user_id = ?",
                            (provider, provider_user_id)
                        )
                        db.commit()
                
                # Check if user exists with same email
                if email:
                    cur.execute(
                        """
                        SELECT id, username, email, nickname, avatar, status, role
                        FROM qd_users WHERE email = ?
                        """,
                        (email,)
                    )
                    existing_user = cur.fetchone()
                    
                    if existing_user:
                        # Link OAuth to existing user
                        cur.execute(
                            """
                            INSERT INTO qd_oauth_links 
                            (user_id, provider, provider_user_id, provider_email, 
                             provider_name, provider_avatar, access_token, refresh_token)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (existing_user['id'], provider, provider_user_id, email,
                             name, avatar, oauth_info.get('access_token'), 
                             oauth_info.get('refresh_token'))
                        )
                        cur.execute(
                            "UPDATE qd_users SET last_login_at = NOW() WHERE id = ?",
                            (existing_user['id'],)
                        )
                        db.commit()
                        cur.close()
                        from app.services.user_service import UserService
                        return True, UserService.normalize_user_record(existing_user)
                
                # Create new user
                # Generate unique username from OAuth name or email
                base_username = (name or email.split('@')[0] if email else provider_user_id)
                base_username = ''.join(c for c in base_username if c.isalnum() or c in '_-')[:30]
                username = base_username
                
                # Ensure username is unique
                counter = 1
                while True:
                    cur.execute("SELECT id FROM qd_users WHERE username = ?", (username,))
                    if not cur.fetchone():
                        break
                    username = f"{base_username}_{counter}"
                    counter += 1
                
                # Generate a random password (user won't need it for OAuth login)
                import secrets
                random_password = secrets.token_urlsafe(32)
                from app.services.user_service import get_user_service
                password_hash = get_user_service().hash_password(random_password)
                
                # Ensure email is unique or generate placeholder
                if email:
                    cur.execute("SELECT id FROM qd_users WHERE email = ?", (email,))
                    if cur.fetchone():
                        email = f"{provider}_{provider_user_id}@oauth.local"
                else:
                    email = f"{provider}_{provider_user_id}@oauth.local"
                
                # Insert new user
                cur.execute(
                    """
                    INSERT INTO qd_users 
                    (username, password_hash, email, nickname, avatar, status, role, email_verified)
                    VALUES (?, ?, ?, ?, ?, 'active', 'user', TRUE)
                    """,
                    (username, password_hash, email, name or username, avatar or '/avatar2.jpg')
                )
                user_id = cur.lastrowid
                
                # Create OAuth link
                cur.execute(
                    """
                    INSERT INTO qd_oauth_links 
                    (user_id, provider, provider_user_id, provider_email, 
                     provider_name, provider_avatar, access_token, refresh_token)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (user_id, provider, provider_user_id, oauth_info.get('email'),
                     name, avatar, oauth_info.get('access_token'), 
                     oauth_info.get('refresh_token'))
                )
                
                # Update last_login_at for new OAuth users
                cur.execute(
                    "UPDATE qd_users SET last_login_at = NOW() WHERE id = ?",
                    (user_id,)
                )
                
                db.commit()
                cur.close()

                if user_id is None:
                    cur = db.cursor()
                    cur.execute("SELECT id FROM qd_users WHERE username = ?", (username,))
                    row = cur.fetchone()
                    user_id = int(row["id"]) if row and row.get("id") is not None else None
                    cur.close()

                try:
                    from app.services.builtin_indicators import seed_builtin_indicators_for_new_user

                    seed_builtin_indicators_for_new_user(db, user_id)
                except Exception as ind_err:
                    logger.warning(f"Builtin indicators seed failed for OAuth user {user_id}: {ind_err}")

                # Grant registration bonus credits for OAuth-created users
                # Keep consistent with email/password registration flows (auth.py).
                try:
                    register_bonus = int(os.getenv('CREDITS_REGISTER_BONUS', '0'))
                except (ValueError, TypeError):
                    register_bonus = 0
                if register_bonus > 0:
                    try:
                        from app.services.billing_service import get_billing_service
                        get_billing_service().add_credits(
                            user_id=user_id,
                            amount=register_bonus,
                            action='register_bonus',
                            remark=f'Registration bonus (OAuth:{provider})'
                        )
                    except Exception as e:
                        logger.warning(f"Failed to grant OAuth registration bonus: {e}")
                
                return True, {
                    'id': user_id,
                    'username': username,
                    'email': email,
                    'nickname': name or username,
                    'avatar': avatar or '/avatar2.jpg',
                    'status': 'active',
                    'role': 'user'
                }
                
        except Exception as e:
            logger.error(f"OAuth user creation failed: {e}")
            return False, {'error': 'Failed to create user account'}
    
    def get_user_oauth_links(self, user_id: int) -> list:
        """Get all OAuth links for a user"""
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    """
                    SELECT provider, provider_email, provider_name, created_at
                    FROM qd_oauth_links WHERE user_id = ?
                    """,
                    (user_id,)
                )
                links = cur.fetchall()
                cur.close()
                return [dict(link) for link in links] if links else []
        except Exception as e:
            logger.error(f"Failed to get OAuth links: {e}")
            return []
    
    def unlink_oauth(self, user_id: int, provider: str) -> Tuple[bool, str]:
        """Unlink an OAuth provider from user account"""
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                
                # Check if user has password (can't unlink last auth method)
                cur.execute(
                    "SELECT password_hash FROM qd_users WHERE id = ?",
                    (user_id,)
                )
                user = cur.fetchone()
                
                if not user or not user['password_hash']:
                    # Check if this is the only OAuth link
                    cur.execute(
                        "SELECT COUNT(*) as count FROM qd_oauth_links WHERE user_id = ?",
                        (user_id,)
                    )
                    count = cur.fetchone()['count']
                    if count <= 1:
                        cur.close()
                        return False, 'Cannot unlink the only authentication method'
                
                cur.execute(
                    "DELETE FROM qd_oauth_links WHERE user_id = ? AND provider = ?",
                    (user_id, provider)
                )
                db.commit()
                cur.close()
                return True, 'unlinked'
                
        except Exception as e:
            logger.error(f"Failed to unlink OAuth: {e}")
            return False, 'Failed to unlink account'
    
    # =========================================================================
    # Cleanup
    # =========================================================================
    
    def cleanup_expired_states(self, max_age_minutes: int = 10):
        """Delete expired OAuth state rows from the DB.
        `max_age_minutes` is kept for backward-compat but is ignored — expiry
        is driven by qd_oauth_states.expires_at which is set on insert.
        """
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                self._ensure_oauth_state_schema(cur)
                cur.execute("DELETE FROM qd_oauth_states WHERE expires_at < NOW()")
                db.commit()
                cur.close()
        except Exception as e:
            logger.debug(f"cleanup_expired_states: {e}")
