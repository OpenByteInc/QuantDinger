"""
Security Service - Handles Turnstile verification, rate limiting, and brute-force protection.
"""
import os
import json
import requests
from datetime import datetime, timedelta
from typing import Tuple, Optional, Dict, Any
from app.utils.db import get_db_connection
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Singleton instance
_security_service = None

_TURNSTILE_PLACEHOLDER_VALUES = {
    '',
    '0',
    'false',
    'none',
    'null',
    'test',
    'dummy',
    'placeholder',
    'changeme',
    'change-me',
    'quantdinger',
    'your-turnstile-site-key',
    'your-turnstile-secret-key',
}


def _is_placeholder_turnstile_value(value: str) -> bool:
    """Return True for local/demo placeholders that should not enable Turnstile."""
    normalized = (value or '').strip().lower()
    return normalized in _TURNSTILE_PLACEHOLDER_VALUES


def get_security_service():
    """Get singleton SecurityService instance"""
    global _security_service
    if _security_service is None:
        _security_service = SecurityService()
    return _security_service


class SecurityService:
    """Security service for authentication protection"""
    
    def __init__(self):
        self._load_config()
    
    def _load_config(self):
        """Load security configuration from environment variables"""
        # Turnstile config
        self.turnstile_site_key = (os.getenv('TURNSTILE_SITE_KEY', '') or '').strip()
        self.turnstile_secret_key = (os.getenv('TURNSTILE_SECRET_KEY', '') or '').strip()
        self.turnstile_enabled = bool(
            self.turnstile_site_key
            and self.turnstile_secret_key
            and not _is_placeholder_turnstile_value(self.turnstile_site_key)
            and not _is_placeholder_turnstile_value(self.turnstile_secret_key)
        )
        if (self.turnstile_site_key or self.turnstile_secret_key) and not self.turnstile_enabled:
            logger.warning(
                "Turnstile is disabled because its site key or secret key is empty/a placeholder."
            )
        
        # IP rate limit config
        self.ip_max_attempts = int(os.getenv('SECURITY_IP_MAX_ATTEMPTS', '10'))
        self.ip_window_minutes = int(os.getenv('SECURITY_IP_WINDOW_MINUTES', '5'))
        self.ip_block_minutes = int(os.getenv('SECURITY_IP_BLOCK_MINUTES', '15'))
        
        # Account rate limit config
        self.account_max_attempts = int(os.getenv('SECURITY_ACCOUNT_MAX_ATTEMPTS', '5'))
        self.account_window_minutes = int(os.getenv('SECURITY_ACCOUNT_WINDOW_MINUTES', '60'))
        self.account_block_minutes = int(os.getenv('SECURITY_ACCOUNT_BLOCK_MINUTES', '30'))
        
        # Verification code rate limit
        self.code_rate_limit_seconds = int(os.getenv('VERIFICATION_CODE_RATE_LIMIT', '60'))
        self.code_ip_hourly_limit = int(os.getenv('VERIFICATION_CODE_IP_HOURLY_LIMIT', '10'))
    
    def get_security_config(self) -> Dict[str, Any]:
        """Get public security config for frontend"""
        mobile_ver = (os.getenv('MOBILE_APP_LATEST_VERSION') or '').strip()
        mobile_url = (os.getenv('MOBILE_APP_DOWNLOAD_URL') or '').strip() or 'https://www.quantdinger.com/download/app.apk'
        return {
            'turnstile_enabled': self.turnstile_enabled,
            'turnstile_site_key': self.turnstile_site_key,
            'registration_enabled': os.getenv('ENABLE_REGISTRATION', 'true').lower() == 'true',
            'mfa_enabled': os.getenv('MFA_ENABLED', 'false').lower() == 'true',
            'mfa_risk_login_only': os.getenv('MFA_RISK_LOGIN_ONLY', 'true').lower() == 'true',
            'oauth_google_enabled': bool(os.getenv('GOOGLE_CLIENT_ID', '')),
            'oauth_github_enabled': bool(os.getenv('GITHUB_CLIENT_ID', '')),
            # Mobile in-app version check (semver-ish string, e.g. 1.0.1)
            'mobile_app_latest_version': mobile_ver,
            'mobile_app_download_url': mobile_url,
        }
    
    # =========================================================================
    # Turnstile Verification
    # =========================================================================
    
    def verify_turnstile(self, token: str, ip_address: str = None) -> Tuple[bool, str]:
        """
        Verify Cloudflare Turnstile token.
        
        Returns:
            (success, message)
        """
        if not self.turnstile_enabled:
            # If Turnstile is not configured, skip verification
            return True, 'turnstile_disabled'
        
        if not token:
            return False, 'Missing Turnstile token'
        
        try:
            response = requests.post(
                'https://challenges.cloudflare.com/turnstile/v0/siteverify',
                data={
                    'secret': self.turnstile_secret_key,
                    'response': token,
                    'remoteip': ip_address
                },
                timeout=10
            )
            result = response.json()
            
            if result.get('success'):
                return True, 'verified'
            else:
                error_codes = result.get('error-codes', [])
                logger.warning(f"Turnstile verification failed: {error_codes}")
                return False, 'Turnstile verification failed'
                
        except requests.RequestException as e:
            logger.error(f"Turnstile API error: {e}")
            # On API error, we might want to allow (fail-open) or deny (fail-closed)
            # For security, we'll deny
            return False, 'Turnstile service unavailable'
    
    # =========================================================================
    # Rate Limiting & Brute-Force Protection
    # =========================================================================
    
    def record_login_attempt(self, identifier: str, identifier_type: str, 
                             success: bool, ip_address: str = None, 
                             user_agent: str = None) -> bool:
        """
        Record a login attempt for rate limiting.
        
        Args:
            identifier: IP address or username
            identifier_type: 'ip' or 'account'
            success: Whether the attempt was successful
            ip_address: Client IP address
            user_agent: Client user agent string
        """
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    """
                    INSERT INTO qd_login_attempts 
                    (identifier, identifier_type, success, ip_address, user_agent)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (identifier, identifier_type, success, ip_address, user_agent)
                )
                db.commit()
                cur.close()
            return True
        except Exception as e:
            logger.error(f"Failed to record login attempt: {e}")
            return False
    
    def is_blocked(self, identifier: str, identifier_type: str) -> Tuple[bool, int]:
        """
        Check if an identifier (IP or account) is blocked due to too many failed attempts.
        
        Returns:
            (is_blocked, remaining_seconds)
        """
        try:
            if identifier_type == 'ip':
                max_attempts = self.ip_max_attempts
                window_minutes = self.ip_window_minutes
                block_minutes = self.ip_block_minutes
            else:  # account
                max_attempts = self.account_max_attempts
                window_minutes = self.account_window_minutes
                block_minutes = self.account_block_minutes
            
            with get_db_connection() as db:
                cur = db.cursor()
                
                # Count failed attempts in the time window
                window_start = datetime.now() - timedelta(minutes=window_minutes)
                cur.execute(
                    """
                    SELECT COUNT(*) as count, MAX(attempt_time) as last_attempt
                    FROM qd_login_attempts
                    WHERE identifier = ? AND identifier_type = ? 
                    AND success = FALSE AND attempt_time > ?
                    """,
                    (identifier, identifier_type, window_start)
                )
                row = cur.fetchone()
                cur.close()
                
                if not row:
                    return False, 0
                
                failed_count = row['count'] or 0
                last_attempt = row['last_attempt']
                
                if failed_count >= max_attempts:
                    # Check if still in block period
                    if last_attempt:
                        block_until = last_attempt + timedelta(minutes=block_minutes)
                        if datetime.now() < block_until:
                            remaining = int((block_until - datetime.now()).total_seconds())
                            return True, remaining
                
                return False, 0
                
        except Exception as e:
            logger.error(f"Failed to check block status: {e}")
            return False, 0
    
    def check_login_allowed(self, username: str, ip_address: str) -> Tuple[bool, str]:
        """
        Check if login is allowed for the given username and IP.
        
        Returns:
            (allowed, message)
        """
        # Check IP block
        ip_blocked, ip_remaining = self.is_blocked(ip_address, 'ip')
        if ip_blocked:
            minutes = ip_remaining // 60
            return False, f'Too many failed attempts from this IP. Try again in {minutes + 1} minutes.'
        
        # Check account block
        account_blocked, account_remaining = self.is_blocked(username, 'account')
        if account_blocked:
            minutes = account_remaining // 60
            return False, f'Account temporarily locked due to too many failed attempts. Try again in {minutes + 1} minutes.'
        
        return True, 'allowed'
    
    def clear_login_attempts(self, identifier: str, identifier_type: str) -> bool:
        """
        Clear login attempts for an identifier (called after successful login).
        """
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    """
                    DELETE FROM qd_login_attempts
                    WHERE identifier = ? AND identifier_type = ?
                    """,
                    (identifier, identifier_type)
                )
                db.commit()
                cur.close()
            return True
        except Exception as e:
            logger.error(f"Failed to clear login attempts: {e}")
            return False
    
    # =========================================================================
    # Security Audit Logging
    # =========================================================================
    
    def log_security_event(self, action: str, user_id: int = None, 
                          ip_address: str = None, user_agent: str = None,
                          details: dict = None) -> bool:
        """
        Log a security-related event.
        
        Args:
            action: Event type (login, logout, register, reset_password, etc.)
            user_id: User ID if applicable
            ip_address: Client IP
            user_agent: Client user agent
            details: Additional details as dict
        """
        try:
            details_json = json.dumps(details) if details else None
            
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    """
                    INSERT INTO qd_security_logs 
                    (user_id, action, ip_address, user_agent, details)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (user_id, action, ip_address, user_agent, details_json)
                )
                db.commit()
                cur.close()
            return True
        except Exception as e:
            logger.error(f"Failed to log security event: {e}")
            return False
    
    # =========================================================================
    # Verification Code Rate Limiting
    # =========================================================================
    
    def can_send_verification_code(self, email: str, ip_address: str) -> Tuple[bool, str]:
        """
        Check if we can send a verification code to this email from this IP.
        
        Returns:
            (allowed, message)
        """
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                
                # Check email rate limit (one code per minute per email)
                rate_limit_time = datetime.now() - timedelta(seconds=self.code_rate_limit_seconds)
                cur.execute(
                    """
                    SELECT COUNT(*) as count FROM qd_verification_codes
                    WHERE email = ? AND created_at > ?
                    """,
                    (email, rate_limit_time)
                )
                row = cur.fetchone()
                if row and row['count'] > 0:
                    return False, f'Please wait {self.code_rate_limit_seconds} seconds before requesting another code'
                
                # Check IP hourly limit
                hour_ago = datetime.now() - timedelta(hours=1)
                cur.execute(
                    """
                    SELECT COUNT(*) as count FROM qd_verification_codes
                    WHERE ip_address = ? AND created_at > ?
                    """,
                    (ip_address, hour_ago)
                )
                row = cur.fetchone()
                if row and row['count'] >= self.code_ip_hourly_limit:
                    return False, 'Too many verification code requests from this IP. Try again later.'
                
                cur.close()
                return True, 'allowed'
                
        except Exception as e:
            logger.error(f"Failed to check verification code rate limit: {e}")
            return True, 'allowed'  # Fail open on DB errors
    
    # =========================================================================
    # Password Strength Validation
    # =========================================================================
    
    def validate_password_strength(self, password: str) -> Tuple[bool, str]:
        """
        Validate password meets minimum security requirements.
        
        Requirements:
        - At least 8 characters
        - Contains at least one uppercase letter
        - Contains at least one lowercase letter
        - Contains at least one digit
        
        Returns:
            (valid, message)
        """
        if len(password) < 8:
            return False, 'Password must be at least 8 characters long'
        
        if not any(c.isupper() for c in password):
            return False, 'Password must contain at least one uppercase letter'
        
        if not any(c.islower() for c in password):
            return False, 'Password must contain at least one lowercase letter'
        
        if not any(c.isdigit() for c in password):
            return False, 'Password must contain at least one digit'
        
        return True, 'valid'
    
    # =========================================================================
    # Cleanup
    # =========================================================================
    
    def cleanup_old_records(self, days: int = 7) -> int:
        """
        Clean up old login attempts and expired verification codes.
        
        Returns:
            Number of records deleted
        """
        deleted = 0
        cutoff = datetime.now() - timedelta(days=days)
        
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                
                # Clean old login attempts
                cur.execute(
                    "DELETE FROM qd_login_attempts WHERE attempt_time < ?",
                    (cutoff,)
                )
                deleted += cur.rowcount or 0
                
                # Clean expired verification codes
                cur.execute(
                    "DELETE FROM qd_verification_codes WHERE expires_at < ?",
                    (cutoff,)
                )
                deleted += cur.rowcount or 0
                
                db.commit()
                cur.close()
                
            logger.info(f"Security cleanup: deleted {deleted} old records")
            return deleted
            
        except Exception as e:
            logger.error(f"Security cleanup failed: {e}")
            return 0
