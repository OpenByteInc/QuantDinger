"""
User Service - Multi-user management

Handles user CRUD operations, password hashing, and role management.
"""
import hashlib
import re
import time
import os
from typing import Optional, Dict, Any, List
from app.utils.db import get_db_connection
from app.utils.logger import get_logger

logger = get_logger(__name__)

# IANA timezone id subset check (e.g. Asia/Shanghai, America/New_York)
_TIMEZONE_ID_RE = re.compile(r'^[A-Za-z0-9_/+\-.]+$')
_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')

# Try to import bcrypt for secure password hashing
try:
    import bcrypt
    HAS_BCRYPT = True
except ImportError:
    HAS_BCRYPT = False
    logger.warning("bcrypt not installed. Using SHA256 for password hashing (less secure).")


_DEFAULT_WATCHLIST = [
    ("Crypto", "BTC/USDT", "Bitcoin"),
    ("Crypto", "ETH/USDT", "Ethereum"),
    ("Crypto", "SOL/USDT", "Solana"),
    ("USStock", "AAPL", "Apple"),
    ("USStock", "NVDA", "NVIDIA"),
    ("USStock", "TSLA", "Tesla"),
    ("USStock", "MSFT", "Microsoft"),
]


def _seed_default_watchlist(db, user_id: int):
    """Insert a starter watchlist for brand-new users (FTUE)."""
    cur = db.cursor()
    for market, symbol, name in _DEFAULT_WATCHLIST:
        cur.execute(
            """
            INSERT INTO qd_watchlist (user_id, market, symbol, name, created_at, updated_at)
            VALUES (?, ?, ?, ?, NOW(), NOW())
            ON CONFLICT (user_id, market, symbol) DO NOTHING
            """,
            (user_id, market, symbol, name),
        )
    db.commit()
    cur.close()
    logger.info(f"Seeded {len(_DEFAULT_WATCHLIST)} default watchlist items for user {user_id}")


class UserService:
    """User management service"""

    _password_changed_column_ready = False
    BOOTSTRAP_DEFAULT_PASSWORD = '123456'
    
    # Available roles (ordered by privilege level)
    ROLES = ['viewer', 'user', 'manager', 'admin']
    
    # Role permissions mapping
    ROLE_PERMISSIONS = {
        'viewer': ['dashboard', 'view'],
        'user': ['dashboard', 'view', 'indicator', 'backtest', 'strategy', 'portfolio'],
        'manager': ['dashboard', 'view', 'indicator', 'backtest', 'strategy', 'portfolio', 'settings'],
        'admin': ['dashboard', 'view', 'indicator', 'backtest', 'strategy', 'portfolio', 'settings', 'user_manage', 'credentials'],
    }
    
    def ensure_password_changed_column(self) -> None:
        """Add password_changed_at if missing (idempotent)."""
        if UserService._password_changed_column_ready:
            return
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    """
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'qd_users' AND column_name = 'password_changed_at'
                    """
                )
                if not cur.fetchone():
                    cur.execute(
                        "ALTER TABLE qd_users ADD COLUMN password_changed_at TIMESTAMP NULL"
                    )
                    # Existing accounts: assume they already passed initial setup.
                    cur.execute(
                        """
                        UPDATE qd_users
                        SET password_changed_at = COALESCE(updated_at, created_at, NOW())
                        WHERE password_changed_at IS NULL
                        """
                    )
                    db.commit()
                    logger.info("Added password_changed_at column to qd_users (existing rows backfilled)")
                cur.close()
            UserService._password_changed_column_ready = True
        except Exception as e:
            logger.warning(f"ensure_password_changed_column failed: {e}")

    def mark_password_changed(self, user_id: int) -> None:
        """Record that the user has set a non-initial password."""
        self.ensure_password_changed_column()
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    """
                    UPDATE qd_users
                    SET password_changed_at = NOW(), updated_at = NOW()
                    WHERE id = ?
                    """,
                    (user_id,),
                )
                db.commit()
                cur.close()
        except Exception as e:
            logger.warning(f"mark_password_changed failed for user {user_id}: {e}")

    @classmethod
    def _configured_admin_password(cls) -> str:
        """Return the current bootstrap admin password from env/config."""
        try:
            from app.config.settings import Config
            return str(Config.ADMIN_PASSWORD or '')
        except Exception:
            return str(os.getenv('ADMIN_PASSWORD', '') or '')

    @classmethod
    def _configured_admin_username(cls) -> str:
        """Return the configured bootstrap admin username."""
        try:
            from app.config.settings import Config
            return str(Config.ADMIN_USER or os.getenv('ADMIN_USER', 'quantdinger') or 'quantdinger').strip()
        except Exception:
            return str(os.getenv('ADMIN_USER', 'quantdinger') or 'quantdinger').strip()

    @classmethod
    def _configured_admin_email(cls) -> str:
        """Return the configured bootstrap admin email."""
        try:
            from app.config.settings import Config
            return str(getattr(Config, 'ADMIN_EMAIL', '') or os.getenv('ADMIN_EMAIL', '') or '').strip()
        except Exception:
            return str(os.getenv('ADMIN_EMAIL', '') or '').strip()

    @staticmethod
    def _normalize_email(email: Optional[str]) -> str:
        return str(email or '').strip().lower()

    @classmethod
    def _is_bootstrap_default_plain_password(cls, password: str) -> bool:
        return str(password or '') == cls.BOOTSTRAP_DEFAULT_PASSWORD

    @classmethod
    def _configured_admin_password_is_default(cls) -> bool:
        return cls._is_bootstrap_default_plain_password(cls._configured_admin_password())

    def _password_hash_matches_bootstrap_default(self, password_hash: str) -> bool:
        password_hash = str(password_hash or '').strip()
        if not password_hash:
            return False
        return self.verify_password(self.BOOTSTRAP_DEFAULT_PASSWORD, password_hash)

    def _initial_password_state(self, password_hash: str, password_changed_at: Any) -> str:
        """
        Classify bootstrap password state for the first user.

        Returns:
            ok: no reminder/action needed
            must_change: still using the unsafe built-in default password
            sync_env_password: env ADMIN_PASSWORD is non-default but DB still has 123456
            mark_changed: DB password is non-default, but password_changed_at was never set
        """
        if password_changed_at is not None:
            return 'ok'
        if not str(password_hash or '').strip():
            return 'ok'
        if self._password_hash_matches_bootstrap_default(password_hash):
            if self._configured_admin_password_is_default():
                return 'must_change'
            return 'sync_env_password'
        return 'mark_changed'

    def _sync_bootstrap_admin_password_from_env(self, user_id: int, password_hash: str) -> bool:
        """
        If operators changed ADMIN_PASSWORD in .env after DB bootstrap, migrate the
        first user's DB password away from the hard-coded default.
        """
        env_password = self._configured_admin_password()
        if self._is_bootstrap_default_plain_password(env_password):
            return False
        if not env_password:
            return False
        if not self._password_hash_matches_bootstrap_default(password_hash):
            return False

        try:
            new_hash = self.hash_password(env_password)
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    """
                    UPDATE qd_users
                    SET password_hash = ?, password_changed_at = NOW(), updated_at = NOW()
                    WHERE id = ?
                    """,
                    (new_hash, user_id),
                )
                db.commit()
                cur.close()
            logger.info("Synchronized bootstrap admin password from non-default ADMIN_PASSWORD")
            return True
        except Exception as e:
            logger.warning(f"sync bootstrap admin password failed for user {user_id}: {e}")
            return False

    def sync_bootstrap_admin_password_from_env(self) -> bool:
        """Repair bootstrap admin password if env was changed after DB creation."""
        first_id = self.get_first_user_id()
        if first_id is None:
            return False
        if self._configured_admin_password_is_default():
            return False

        self.ensure_password_changed_column()
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    """
                    SELECT password_hash, password_changed_at
                    FROM qd_users WHERE id = ?
                    """,
                    (first_id,),
                )
                row = cur.fetchone()
                cur.close()
            if not row:
                return False
            state = self._initial_password_state(
                row.get('password_hash'),
                row.get('password_changed_at'),
            )
            if state != 'sync_env_password':
                return False
            return self._sync_bootstrap_admin_password_from_env(
                int(first_id),
                str(row.get('password_hash') or ''),
            )
        except Exception as e:
            logger.warning(f"sync_bootstrap_admin_password_from_env failed: {e}")
            return False

    def sync_admin_email_from_config(
        self,
        admin_email: Optional[str] = None,
        overwrite_existing: bool = False,
    ) -> Dict[str, Any]:
        """
        Sync ADMIN_EMAIL into the bootstrap admin account.

        Startup sync only fills empty/default emails. Explicit settings saves may
        overwrite the admin account email, but never take an email used by another
        user.
        """
        email = self._normalize_email(
            admin_email if admin_email is not None else self._configured_admin_email()
        )
        if not email:
            return {'synced': False, 'reason': 'empty'}
        if email == 'admin@example.com':
            return {'synced': False, 'reason': 'placeholder'}
        if not _EMAIL_RE.match(email):
            return {'synced': False, 'reason': 'invalid_email'}

        try:
            admin_username = self._configured_admin_username()
            admin_user = self.get_user_by_username(admin_username) if admin_username else None
            if not admin_user:
                first_id = self.get_first_user_id()
                admin_user = self.get_user_by_id(first_id) if first_id is not None else None
            if not admin_user:
                return {'synced': False, 'reason': 'admin_user_not_found'}

            admin_id = int(admin_user.get('id'))
            existing = self.get_user_by_email(email)
            if existing and int(existing.get('id')) != admin_id:
                return {
                    'synced': False,
                    'reason': 'email_in_use',
                    'user_id': int(existing.get('id')),
                }

            current_email = self._normalize_email(admin_user.get('email'))
            if current_email == email:
                return {
                    'synced': False,
                    'reason': 'already_current',
                    'user_id': admin_id,
                    'email': email,
                }
            if current_email and current_email != 'admin@example.com' and not overwrite_existing:
                return {
                    'synced': False,
                    'reason': 'existing_email_kept',
                    'user_id': admin_id,
                    'email': current_email,
                }

            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    """
                    UPDATE qd_users
                    SET email = ?, email_verified = TRUE, updated_at = NOW()
                    WHERE id = ?
                    """,
                    (email, admin_id),
                )
                db.commit()
                cur.close()

            logger.info("Synchronized admin email from ADMIN_EMAIL")
            return {'synced': True, 'user_id': admin_id, 'email': email}
        except Exception as e:
            logger.warning(f"sync_admin_email_from_config failed: {e}")
            return {'synced': False, 'reason': 'error', 'message': str(e)}

    def get_first_user_id(self) -> Optional[int]:
        """Return the lowest user id (bootstrap / first account created on install)."""
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute("SELECT MIN(id) AS id FROM qd_users")
                row = cur.fetchone()
                cur.close()
            if not row or row.get('id') is None:
                return None
            return int(row['id'])
        except Exception as e:
            logger.warning(f"get_first_user_id failed: {e}")
            return None

    def must_change_initial_password(self, user_id: int) -> bool:
        """
        True only for the first user when they still use the unsafe built-in
        bootstrap password (123456). Operators may set ADMIN_PASSWORD in .env;
        if they do, a NULL password_changed_at alone must not force a prompt.
        """
        first_id = self.get_first_user_id()
        if first_id is None or int(user_id) != first_id:
            return False

        self.ensure_password_changed_column()
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    """
                    SELECT password_hash, password_changed_at
                    FROM qd_users WHERE id = ?
                    """,
                    (user_id,),
                )
                row = cur.fetchone()
                cur.close()
            if not row:
                return False
            password_hash = str(row.get('password_hash') or '').strip()
            if not password_hash:
                return False
            state = self._initial_password_state(password_hash, row.get('password_changed_at'))
            if state == 'must_change':
                return True
            if state == 'sync_env_password':
                self._sync_bootstrap_admin_password_from_env(int(user_id), password_hash)
                return False
            if state == 'mark_changed':
                self.mark_password_changed(int(user_id))
            return False
        except Exception as e:
            logger.warning(f"must_change_initial_password failed for user {user_id}: {e}")
            return False

    def hash_password(self, password: str) -> str:
        """Hash password using bcrypt (preferred) or SHA256 (fallback)"""
        if HAS_BCRYPT:
            salt = bcrypt.gensalt(rounds=12)
            return bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')
        else:
            # Fallback to SHA256 with salt
            salt = os.urandom(16).hex()
            hashed = hashlib.sha256((password + salt).encode('utf-8')).hexdigest()
            return f"sha256${salt}${hashed}"
    
    def verify_password(self, password: str, password_hash: str) -> bool:
        """Verify password against hash"""
        if password_hash.startswith('$2b$') or password_hash.startswith('$2a$'):
            # bcrypt hash
            if HAS_BCRYPT:
                try:
                    return bcrypt.checkpw(password.encode('utf-8'), password_hash.encode('utf-8'))
                except Exception:
                    return False
            return False
        elif password_hash.startswith('sha256$'):
            # SHA256 fallback hash
            parts = password_hash.split('$')
            if len(parts) != 3:
                return False
            salt = parts[1]
            stored_hash = parts[2]
            computed = hashlib.sha256((password + salt).encode('utf-8')).hexdigest()
            return computed == stored_hash
        return False
    
    def get_user_by_id(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Get user by ID"""
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    """
                    SELECT id, username, email, nickname, avatar, status, role,
                           credits, vip_expires_at, timezone,
                           COALESCE(
                               qd_users.last_login_at,
                               (
                                   SELECT MAX(sl.created_at)
                                   FROM qd_security_logs sl
                                   WHERE sl.user_id = qd_users.id
                                     AND sl.action IN ('login_success', 'login_via_code', 'oauth_login')
                               )
                           ) AS last_login_at,
                           COALESCE(
                               (
                                   SELECT sl.ip_address
                                   FROM qd_security_logs sl
                                   WHERE sl.user_id = qd_users.id
                                     AND sl.action IN ('register', 'register_via_code')
                                     AND COALESCE(sl.ip_address, '') <> ''
                                   ORDER BY sl.created_at ASC
                                   LIMIT 1
                               ),
                               (
                                   SELECT sl.ip_address
                                   FROM qd_security_logs sl
                                   WHERE sl.user_id = qd_users.id
                                     AND sl.action IN ('oauth_login', 'login_success', 'login_via_code')
                                     AND COALESCE(sl.ip_address, '') <> ''
                                   ORDER BY sl.created_at ASC
                                   LIMIT 1
                               )
                           ) AS register_ip,
                           created_at, updated_at
                    FROM qd_users WHERE id = ?
                    """,
                    (user_id,)
                )
                row = cur.fetchone()
                cur.close()
                return row
        except Exception as e:
            logger.error(f"get_user_by_id failed: {e}")
            return None
    
    def get_user_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        """Get user by username (includes password_hash for auth)"""
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    """
                    SELECT id, username, password_hash, email, nickname, avatar,
                           status, role, timezone, last_login_at, created_at, updated_at
                    FROM qd_users WHERE username = ?
                    """,
                    (username,)
                )
                row = cur.fetchone()
                cur.close()
                return row
        except Exception as e:
            logger.error(f"get_user_by_username failed: {e}")
            return None
    
    def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        """Get user by email (includes password_hash for auth)"""
        if not email:
            return None
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    """
                    SELECT id, username, password_hash, email, nickname, avatar,
                           status, role, timezone, last_login_at, created_at, updated_at
                    FROM qd_users WHERE LOWER(email) = LOWER(?)
                    """,
                    (email,)
                )
                row = cur.fetchone()
                cur.close()
                return row
        except Exception as e:
            logger.error(f"get_user_by_email failed: {e}")
            return None
    
    def authenticate(self, username: str, password: str, update_last_login: bool = True) -> Optional[Dict[str, Any]]:
        """
        Authenticate user with username/email and password.
        Supports both username and email login.
        Returns user info (without password_hash) if successful, None otherwise.
        """
        # Try username first
        user = self.get_user_by_username(username)
        
        # If not found, try email (supports both username and email login)
        if not user:
            user = self.get_user_by_email(username)
        
        if not user:
            return None
        
        if user.get('status') != 'active':
            logger.warning(f"Login attempt for disabled user: {username}")
            return None
        
        password_hash = user.get('password_hash', '')
        
        # Check if user has no password (code-login user)
        if not password_hash or password_hash.strip() == '':
            logger.info(f"Password login attempted for code-login user: {username}")
            # Return a special marker to indicate no password set
            # This allows the caller to provide a more specific error message
            return {'_no_password': True, **user}
        
        if not self.verify_password(password, password_hash):
            return None
        
        if update_last_login:
            self.touch_last_login(user['id'])
        
        # Remove password_hash from return value
        user.pop('password_hash', None)
        return user

    def touch_last_login(self, user_id: int) -> bool:
        """Update last_login_at after the full login flow has completed."""
        try:
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
                    return False
                logger.info(f"Updated last_login_at for user_id={user_id}")
                return True
        except Exception as e:
            logger.error(f"Failed to update last_login_at for user_id={user_id}: {e}")
            return False
    
    def get_token_version(self, user_id: int) -> int:
        """
        获取用户当前的 token 版本号。
        
        Args:
            user_id: 用户ID
        
        Returns:
            当前 token 版本号，默认为 1
        """
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    "SELECT token_version FROM qd_users WHERE id = ?",
                    (user_id,)
                )
                row = cur.fetchone()
                cur.close()
                if row:
                    return int(row.get('token_version') or 1)
                return 1
        except Exception as e:
            logger.error(f"get_token_version failed: {e}")
            return 1
    
    def increment_token_version(self, user_id: int) -> int:
        """
        递增用户的 token 版本号，使旧的 token 失效。
        用于实现单一客户端登录（踢出其他设备）。
        
        Args:
            user_id: 用户ID
        
        Returns:
            新的 token 版本号
        """
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    """
                    UPDATE qd_users 
                    SET token_version = COALESCE(token_version, 0) + 1, updated_at = NOW()
                    WHERE id = ?
                    """,
                    (user_id,)
                )
                db.commit()
                
                cur.execute(
                    "SELECT token_version FROM qd_users WHERE id = ?",
                    (user_id,)
                )
                row = cur.fetchone()
                cur.close()
                
                new_version = int(row.get('token_version') or 1) if row else 1
                logger.info("Incremented token_version for user")
                return new_version
        except Exception as e:
            logger.error(f"increment_token_version failed: {e}")
            return 1
    
    def create_user(self, data: Dict[str, Any] = None, **kwargs) -> Optional[int]:
        """
        Create a new user.
        
        Args:
            data: dict with user fields, OR use keyword arguments:
                username: str (required),
                password: str (optional, can be None for code-login users),
                email: str (optional),
                nickname: str (optional),
                role: str (optional, default 'user'),
                status: str (optional, default 'active'),
                email_verified: bool (optional, default False),
                referred_by: int (optional, referrer user ID)
        
        Returns:
            New user ID or None if failed
        """
        # Support both dict and kwargs style
        if data is None:
            data = kwargs
        else:
            data = {**data, **kwargs}
        
        username = (data.get('username') or '').strip()
        password = data.get('password')  # Can be None for code-login users
        
        if not username:
            raise ValueError("Username is required")
        
        if len(username) < 3 or len(username) > 50:
            raise ValueError("Username must be 3-50 characters")
        
        # Password validation only if provided
        if password and len(password) < 6:
            raise ValueError("Password must be at least 6 characters")
        
        # Check if username already exists
        existing = self.get_user_by_username(username)
        if existing:
            raise ValueError("Username already exists")
        
        # Hash password or use empty string for code-login users
        password_hash = self.hash_password(password) if password else ''
        email = (data.get('email') or '').strip() or None
        nickname = (data.get('nickname') or '').strip() or username
        role = data.get('role', 'user')
        status = data.get('status', 'active')
        email_verified = data.get('email_verified', False)
        referred_by = data.get('referred_by')  # Referrer user ID
        
        if role not in self.ROLES:
            role = 'user'
        
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    """
                    INSERT INTO qd_users 
                    (username, password_hash, email, nickname, role, status, email_verified, referred_by, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, NOW(), NOW())
                    """,
                    (username, password_hash, email, nickname, role, status, email_verified, referred_by)
                )
                db.commit()
                user_id = cur.lastrowid
                cur.close()
                
                # For PostgreSQL, get the ID differently
                if user_id is None:
                    cur = db.cursor()
                    cur.execute("SELECT id FROM qd_users WHERE username = ?", (username,))
                    row = cur.fetchone()
                    user_id = row['id'] if row else None
                    cur.close()
                
                logger.info(f"Created user: {username} (id={user_id}, referred_by={referred_by})")

                # Seed default watchlist + builtin indicator samples for new users (FTUE)
                if user_id:
                    if password and not self._is_bootstrap_default_plain_password(password):
                        self.mark_password_changed(int(user_id))
                    try:
                        _seed_default_watchlist(db, user_id)
                    except Exception as seed_err:
                        logger.warning(f"Default watchlist seed failed for user {user_id}: {seed_err}")
                    try:
                        from app.services.builtin_indicators import seed_builtin_indicators_for_new_user

                        seed_builtin_indicators_for_new_user(db, user_id)
                    except Exception as ind_err:
                        logger.warning(f"Builtin indicators seed failed for user {user_id}: {ind_err}")

                return user_id
        except Exception as e:
            logger.error(f"create_user failed: {e}")
            raise
    
    def update_user(self, user_id: int, data: Dict[str, Any]) -> bool:
        """
        Update user information.
        
        Args:
            user_id: User ID
            data: Fields to update (email, nickname, avatar, role, status)
        """
        allowed_fields = ['email', 'nickname', 'avatar', 'role', 'status', 'timezone']
        updates = []
        values = []
        
        for field in allowed_fields:
            if field in data:
                value = data[field]
                if field == 'role' and value not in self.ROLES:
                    continue
                if field == 'timezone':
                    s = '' if value is None else str(value).strip()
                    if s and (len(s) > 64 or not _TIMEZONE_ID_RE.match(s)):
                        continue
                    updates.append('timezone = ?')
                    values.append(s)
                    continue
                updates.append(f"{field} = ?")
                values.append(value)
        
        if not updates:
            return False
        
        updates.append("updated_at = NOW()")
        values.append(user_id)
        
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                sql = f"UPDATE qd_users SET {', '.join(updates)} WHERE id = ?"
                cur.execute(sql, tuple(values))
                db.commit()
                cur.close()
                return True
        except Exception as e:
            logger.error(f"update_user failed: {e}")
            return False
    
    def change_password(self, user_id: int, old_password: str, new_password: str) -> bool:
        """Change user password (requires old password verification, except for users with no password)"""
        user = self.get_user_by_id(user_id)
        if not user:
            return False
        
        # Get full user with password_hash
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute("SELECT password_hash FROM qd_users WHERE id = ?", (user_id,))
            row = cur.fetchone()
            cur.close()
            
            if not row:
                return False
            
            password_hash = row.get('password_hash', '')
            
            # If user has no password (code-login user), allow setting password without old password
            if not password_hash or password_hash.strip() == '':
                logger.info(f"Setting initial password for code-login user: {user_id}")
                return self.reset_password(user_id, new_password)
            
            # For users with existing password, verify old password
            if not self.verify_password(old_password, password_hash):
                return False
        
        return self.reset_password(user_id, new_password)
    
    def reset_password(self, user_id: int, new_password: str) -> bool:
        """Reset user password (admin operation, no old password required)"""
        if len(new_password) < 6:
            raise ValueError("Password must be at least 6 characters")
        
        password_hash = self.hash_password(new_password)
        
        try:
            self.ensure_password_changed_column()
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    """
                    UPDATE qd_users
                    SET password_hash = ?, password_changed_at = NOW(), updated_at = NOW()
                    WHERE id = ?
                    """,
                    (password_hash, user_id),
                )
                db.commit()
                cur.close()
                return True
        except Exception as e:
            logger.error(f"reset_password failed: {e}")
            return False
    
    def update_password(self, user_id: int, new_password: str) -> bool:
        """Alias for reset_password - update user password without old password verification"""
        return self.reset_password(user_id, new_password)
    
    def delete_user(self, user_id: int) -> bool:
        """Delete a user"""
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute("DELETE FROM qd_users WHERE id = ?", (user_id,))
                db.commit()
                cur.close()
                return True
        except Exception as e:
            logger.error(f"delete_user failed: {e}")
            return False
    
    @staticmethod
    def _build_user_list_filter(search: str = None, user_id: int = None) -> tuple:
        """WHERE clause + params for admin user list (supports exact user id)."""
        clauses = []
        params = []
        if user_id is not None:
            try:
                uid = int(user_id)
            except (TypeError, ValueError):
                uid = 0
            if uid > 0:
                clauses.append("id = ?")
                params.append(uid)
        if search and str(search).strip():
            raw = str(search).strip()
            like_val = f"%{raw}%"
            parts = ["username ILIKE ?", "email ILIKE ?", "nickname ILIKE ?"]
            part_params = [like_val, like_val, like_val]
            if raw.isdigit():
                parts.extend(["id = ?", "CAST(id AS TEXT) ILIKE ?"])
                part_params.extend([int(raw), f"%{raw}%"])
            else:
                parts.append("CAST(id AS TEXT) ILIKE ?")
                part_params.append(f"%{raw}%")
            clauses.append("(" + " OR ".join(parts) + ")")
            params.extend(part_params)
        if not clauses:
            return "", []
        return "WHERE " + " AND ".join(clauses), params

    def list_users(
        self,
        page: int = 1,
        page_size: int = 20,
        search: str = None,
        user_id: int = None,
    ) -> Dict[str, Any]:
        """List all users with pagination and optional search"""
        offset = (page - 1) * page_size
        
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                
                where_clause, params = self._build_user_list_filter(search, user_id)
                
                # Get total count
                count_sql = f"SELECT COUNT(*) as count FROM qd_users {where_clause}"
                cur.execute(count_sql, tuple(params))
                total = cur.fetchone()['count']
                
                # Get users
                query_sql = f"""
                    SELECT id, username, email, nickname, avatar, status, role,
                           credits, vip_expires_at, timezone,
                           COALESCE(
                               qd_users.last_login_at,
                               (
                                   SELECT MAX(sl.created_at)
                                   FROM qd_security_logs sl
                                   WHERE sl.user_id = qd_users.id
                                     AND sl.action IN ('login_success', 'login_via_code', 'oauth_login')
                               )
                           ) AS last_login_at,
                           COALESCE(
                               (
                                   SELECT sl.ip_address
                                   FROM qd_security_logs sl
                                   WHERE sl.user_id = qd_users.id
                                     AND sl.action IN ('register', 'register_via_code')
                                     AND COALESCE(sl.ip_address, '') <> ''
                                   ORDER BY sl.created_at ASC
                                   LIMIT 1
                               ),
                               (
                                   SELECT sl.ip_address
                                   FROM qd_security_logs sl
                                   WHERE sl.user_id = qd_users.id
                                     AND sl.action IN ('oauth_login', 'login_success', 'login_via_code')
                                     AND COALESCE(sl.ip_address, '') <> ''
                                   ORDER BY sl.created_at ASC
                                   LIMIT 1
                               )
                           ) AS register_ip,
                           created_at, updated_at
                    FROM qd_users
                    {where_clause}
                    ORDER BY id DESC
                    LIMIT ? OFFSET ?
                """
                cur.execute(query_sql, tuple(params + [page_size, offset]))
                users = cur.fetchall()
                cur.close()
                
                return {
                    'items': users,
                    'total': total,
                    'page': page,
                    'page_size': page_size,
                    'total_pages': (total + page_size - 1) // page_size
                }
        except Exception as e:
            logger.error(f"list_users failed: {e}")
            return {'items': [], 'total': 0, 'page': 1, 'page_size': page_size, 'total_pages': 0}

    def list_all_users_for_export(self, search: str = None, user_id: int = None) -> List[Dict[str, Any]]:
        """List all users for export with the same fields as the admin user table."""
        try:
            with get_db_connection() as db:
                cur = db.cursor()

                where_clause, params = self._build_user_list_filter(search, user_id)

                query_sql = f"""
                    SELECT id, username, email, nickname, avatar, status, role,
                           credits, vip_expires_at, timezone,
                           COALESCE(
                               qd_users.last_login_at,
                               (
                                   SELECT MAX(sl.created_at)
                                   FROM qd_security_logs sl
                                   WHERE sl.user_id = qd_users.id
                                     AND sl.action IN ('login_success', 'login_via_code', 'oauth_login')
                               )
                           ) AS last_login_at,
                           COALESCE(
                               (
                                   SELECT sl.ip_address
                                   FROM qd_security_logs sl
                                   WHERE sl.user_id = qd_users.id
                                     AND sl.action IN ('register', 'register_via_code')
                                     AND COALESCE(sl.ip_address, '') <> ''
                                   ORDER BY sl.created_at ASC
                                   LIMIT 1
                               ),
                               (
                                   SELECT sl.ip_address
                                   FROM qd_security_logs sl
                                   WHERE sl.user_id = qd_users.id
                                     AND sl.action IN ('oauth_login', 'login_success', 'login_via_code')
                                     AND COALESCE(sl.ip_address, '') <> ''
                                   ORDER BY sl.created_at ASC
                                   LIMIT 1
                               )
                           ) AS register_ip,
                           created_at, updated_at
                    FROM qd_users
                    {where_clause}
                    ORDER BY id DESC
                """
                cur.execute(query_sql, tuple(params))
                users = cur.fetchall() or []
                cur.close()
                return users
        except Exception as e:
            logger.error(f"list_all_users_for_export failed: {e}")
            return []
    
    def get_user_permissions(self, role: str) -> List[str]:
        """Get permissions for a role"""
        return self.ROLE_PERMISSIONS.get(role, self.ROLE_PERMISSIONS['viewer'])
    
    def ensure_admin_exists(self):
        """
        Ensure at least one admin user exists.
        Creates admin using ADMIN_USER/ADMIN_PASSWORD from env if no users exist.
        """
        self.ensure_password_changed_column()
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute("SELECT COUNT(*) as count FROM qd_users")
                count = cur.fetchone()['count']
                cur.close()
                
                if count == 0:
                    # Create admin using env credentials
                    from app.config.settings import Config
                    admin_user = os.getenv('ADMIN_USER', Config.ADMIN_USER)
                    admin_password = os.getenv('ADMIN_PASSWORD', Config.ADMIN_PASSWORD)
                    admin_email = os.getenv('ADMIN_EMAIL', 'admin@example.com')

                    self.create_user({
                        'username': admin_user,
                        'password': admin_password,
                        'email': admin_email,
                        'nickname': 'Administrator',
                        'role': 'admin',
                        'status': 'active',
                        'email_verified': True  # Admin email is pre-verified
                    })
                    logger.info(f"Created admin user: {admin_user} ({admin_email})")
                else:
                    self.sync_bootstrap_admin_password_from_env()
                    self.sync_admin_email_from_config(overwrite_existing=False)
        except Exception as e:
            logger.error(f"ensure_admin_exists failed: {e}")


# Global singleton
_user_service = None

def get_user_service() -> UserService:
    """Get UserService singleton"""
    global _user_service
    if _user_service is None:
        _user_service = UserService()
    return _user_service
