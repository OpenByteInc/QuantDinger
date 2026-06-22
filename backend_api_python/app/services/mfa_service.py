"""
TOTP-based multi-factor authentication service.

Users opt in individually. The system setting only enables the feature surface;
it does not force every account to bind an authenticator app.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import os
import secrets
import time
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple

from app.config.settings import Config
from app.utils.credential_crypto import decrypt_credential_blob, encrypt_credential_blob
from app.utils.db import get_db_connection
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _env_bool(key: str, default: bool = False) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _env_int(key: str, default: int, min_value: int = 1, max_value: int = 10_000) -> int:
    try:
        value = int(os.getenv(key, str(default)))
    except Exception:
        value = default
    return max(min_value, min(max_value, value))


def _hash_secret(value: str) -> str:
    salt = (Config.SECRET_KEY or os.getenv("SECRET_KEY") or "quantdinger").encode("utf-8")
    return hmac.new(salt, value.encode("utf-8"), hashlib.sha256).hexdigest()


def _mask_secret(secret: str) -> str:
    if not secret:
        return ""
    return f"{secret[:4]} {' '.join(secret[i:i+4] for i in range(4, len(secret), 4))}"


class MfaService:
    def __init__(self) -> None:
        self.ensure_schema()

    @property
    def system_enabled(self) -> bool:
        return _env_bool("MFA_ENABLED", False)

    @property
    def risk_login_only(self) -> bool:
        return _env_bool("MFA_RISK_LOGIN_ONLY", True)

    @property
    def challenge_ttl_minutes(self) -> int:
        return _env_int("MFA_CHALLENGE_EXPIRE_MINUTES", 5, 1, 60)

    @property
    def max_attempts(self) -> int:
        return _env_int("MFA_MAX_ATTEMPTS", 5, 1, 20)

    def ensure_schema(self) -> None:
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS qd_user_mfa (
                        user_id INTEGER PRIMARY KEY REFERENCES qd_users(id) ON DELETE CASCADE,
                        enabled BOOLEAN DEFAULT FALSE,
                        secret_encrypted TEXT NOT NULL,
                        recovery_codes_hash TEXT DEFAULT '',
                        last_used_counter BIGINT DEFAULT 0,
                        confirmed_at TIMESTAMP NULL,
                        created_at TIMESTAMP DEFAULT NOW(),
                        updated_at TIMESTAMP DEFAULT NOW()
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS qd_mfa_challenges (
                        id SERIAL PRIMARY KEY,
                        user_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE CASCADE,
                        challenge_hash VARCHAR(128) UNIQUE NOT NULL,
                        reason VARCHAR(50) DEFAULT 'risk_login',
                        ip_address VARCHAR(45),
                        user_agent TEXT,
                        attempts INTEGER DEFAULT 0,
                        expires_at TIMESTAMP NOT NULL,
                        consumed_at TIMESTAMP NULL,
                        created_at TIMESTAMP DEFAULT NOW()
                    )
                    """
                )
                cur.execute("CREATE INDEX IF NOT EXISTS idx_mfa_challenges_user_id ON qd_mfa_challenges(user_id)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_mfa_challenges_expires ON qd_mfa_challenges(expires_at)")
                db.commit()
                cur.close()
        except Exception as e:
            logger.warning(f"ensure MFA schema skipped: {e}")

    def get_status(self, user_id: int) -> Dict[str, Any]:
        row = self._get_mfa_row(user_id)
        return {
            "system_enabled": self.system_enabled,
            "enabled": bool(row and row.get("enabled")),
            "confirmed_at": row.get("confirmed_at") if row else None,
            "risk_login_only": self.risk_login_only,
            "challenge_ttl_minutes": self.challenge_ttl_minutes,
        }

    def start_setup(self, user_id: int, label: str) -> Dict[str, Any]:
        if not self.system_enabled:
            raise ValueError("MFA is disabled by system settings")

        pyotp, qrcode = self._load_totp_libs()
        secret = pyotp.random_base32()
        issuer = (os.getenv("BRAND_APP_NAME") or "QuantDinger").strip() or "QuantDinger"
        account_label = (label or f"user-{user_id}").strip()
        uri = pyotp.TOTP(secret).provisioning_uri(name=account_label, issuer_name=issuer)
        qr_image = self._make_qr_data_url(qrcode, uri)

        encrypted = encrypt_credential_blob(secret)
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                INSERT INTO qd_user_mfa
                    (user_id, enabled, secret_encrypted, recovery_codes_hash, last_used_counter, confirmed_at, updated_at)
                VALUES (?, FALSE, ?, '', 0, NULL, NOW())
                ON CONFLICT (user_id) DO UPDATE SET
                    enabled = FALSE,
                    secret_encrypted = EXCLUDED.secret_encrypted,
                    recovery_codes_hash = '',
                    last_used_counter = 0,
                    confirmed_at = NULL,
                    updated_at = NOW()
                """,
                (user_id, encrypted),
            )
            db.commit()
            cur.close()

        return {
            "secret": secret,
            "secret_display": _mask_secret(secret),
            "otpauth_uri": uri,
            "qr_image": qr_image,
        }

    def confirm_setup(self, user_id: int, code: str) -> Dict[str, Any]:
        row = self._require_mfa_row(user_id)
        ok, _ = self._verify_totp_row(row, code, update_counter=True)
        if not ok:
            raise ValueError("Invalid verification code")

        recovery_codes = self._generate_recovery_codes()
        recovery_hashes = [_hash_secret(code) for code in recovery_codes]

        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                UPDATE qd_user_mfa
                SET enabled = TRUE,
                    recovery_codes_hash = ?,
                    confirmed_at = NOW(),
                    updated_at = NOW()
                WHERE user_id = ?
                """,
                (json.dumps(recovery_hashes), user_id),
            )
            db.commit()
            cur.close()

        return {"recovery_codes": recovery_codes}

    def disable(self, user_id: int, code: str) -> None:
        row = self._require_mfa_row(user_id)
        if row.get("enabled"):
            ok, _ = self.verify_user_code(user_id, code)
            if not ok:
                raise ValueError("Invalid verification code")
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                UPDATE qd_user_mfa
                SET enabled = FALSE,
                    recovery_codes_hash = '',
                    last_used_counter = 0,
                    updated_at = NOW()
                WHERE user_id = ?
                """,
                (user_id,),
            )
            db.commit()
            cur.close()

    def user_has_enabled_mfa(self, user_id: int) -> bool:
        row = self._get_mfa_row(user_id)
        return bool(row and row.get("enabled"))

    def needs_login_mfa(self, user_id: int, ip_address: str, user_agent: str) -> Tuple[bool, str]:
        if not self.system_enabled or not self.user_has_enabled_mfa(user_id):
            return False, ""
        if not self.risk_login_only:
            return True, "required_by_policy"
        if self._is_risk_login(user_id, ip_address, user_agent):
            return True, "new_location"
        return False, ""

    def create_login_challenge(self, user_id: int, reason: str, ip_address: str, user_agent: str) -> Dict[str, Any]:
        raw = secrets.token_urlsafe(32)
        challenge_hash = _hash_secret(raw)
        expires_at = datetime.utcnow() + timedelta(minutes=self.challenge_ttl_minutes)
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                INSERT INTO qd_mfa_challenges
                    (user_id, challenge_hash, reason, ip_address, user_agent, expires_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (user_id, challenge_hash, reason or "risk_login", ip_address, user_agent, expires_at),
            )
            db.commit()
            cur.close()
        return {
            "challenge_id": raw,
            "expires_in": self.challenge_ttl_minutes * 60,
            "reason": reason or "risk_login",
        }

    def verify_login_challenge(self, challenge_id: str, code: str) -> Tuple[bool, str, Optional[int]]:
        if not challenge_id:
            return False, "Missing MFA challenge", None
        challenge_hash = _hash_secret(challenge_id)
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                SELECT *
                FROM qd_mfa_challenges
                WHERE challenge_hash = ?
                  AND consumed_at IS NULL
                  AND expires_at > NOW()
                """,
                (challenge_hash,),
            )
            challenge = cur.fetchone()
            if not challenge:
                cur.close()
                return False, "MFA challenge expired or invalid", None

            attempts = int(challenge.get("attempts") or 0)
            if attempts >= self.max_attempts:
                cur.close()
                return False, "Too many MFA attempts. Please login again.", None

            user_id = int(challenge["user_id"])
            ok, msg = self.verify_user_code(user_id, code)
            if not ok:
                cur.execute(
                    "UPDATE qd_mfa_challenges SET attempts = attempts + 1 WHERE id = ?",
                    (challenge["id"],),
                )
                db.commit()
                cur.close()
                return False, msg or "Invalid verification code", user_id

            cur.execute(
                "UPDATE qd_mfa_challenges SET consumed_at = NOW(), attempts = attempts + 1 WHERE id = ?",
                (challenge["id"],),
            )
            db.commit()
            cur.close()
            return True, "verified", user_id

    def verify_user_code(self, user_id: int, code: str) -> Tuple[bool, str]:
        row = self._require_mfa_row(user_id)
        if not row.get("enabled"):
            return False, "MFA is not enabled for this account"
        ok, msg = self._verify_totp_row(row, code, update_counter=True)
        if ok:
            return True, msg
        if self._consume_recovery_code(user_id, code):
            return True, "recovery_code"
        return False, msg

    def _verify_totp_row(self, row: Dict[str, Any], code: str, update_counter: bool) -> Tuple[bool, str]:
        pyotp, _ = self._load_totp_libs()
        clean = str(code or "").strip().replace(" ", "")
        if not clean.isdigit() or len(clean) != 6:
            return False, "Invalid verification code"

        secret = decrypt_credential_blob(row.get("secret_encrypted"))
        totp = pyotp.TOTP(secret)
        current_counter = int(time.time() // 30)
        last_counter = int(row.get("last_used_counter") or 0)

        for offset in (-1, 0, 1):
            counter = current_counter + offset
            if counter <= last_counter:
                continue
            if hmac.compare_digest(totp.at(counter * 30), clean):
                if update_counter:
                    with get_db_connection() as db:
                        cur = db.cursor()
                        cur.execute(
                            """
                            UPDATE qd_user_mfa
                            SET last_used_counter = ?, updated_at = NOW()
                            WHERE user_id = ?
                            """,
                            (counter, row["user_id"]),
                        )
                        db.commit()
                        cur.close()
                return True, "verified"
        return False, "Invalid or already used verification code"

    def _consume_recovery_code(self, user_id: int, code: str) -> bool:
        clean = str(code or "").strip().upper().replace(" ", "")
        if not clean:
            return False
        row = self._get_mfa_row(user_id)
        if not row:
            return False
        try:
            hashes = json.loads(row.get("recovery_codes_hash") or "[]")
        except Exception:
            hashes = []
        code_hash = _hash_secret(clean)
        if code_hash not in hashes:
            return False
        hashes = [h for h in hashes if h != code_hash]
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                "UPDATE qd_user_mfa SET recovery_codes_hash = ?, updated_at = NOW() WHERE user_id = ?",
                (json.dumps(hashes), user_id),
            )
            db.commit()
            cur.close()
        return True

    def _is_risk_login(self, user_id: int, ip_address: str, user_agent: str) -> bool:
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    """
                    SELECT ip_address, user_agent
                    FROM qd_security_logs
                    WHERE user_id = ?
                      AND action IN ('login_success', 'login_via_code', 'oauth_login', 'mfa_login_success')
                    ORDER BY created_at DESC
                    LIMIT 5
                    """,
                    (user_id,),
                )
                rows = cur.fetchall() or []
                cur.close()
        except Exception as e:
            logger.warning(f"MFA risk lookup failed for user {user_id}: {e}")
            return True

        if not rows:
            return True

        current_ip = (ip_address or "").strip()
        current_ua = self._user_agent_family(user_agent)
        for row in rows:
            if (row.get("ip_address") or "").strip() == current_ip and self._user_agent_family(row.get("user_agent") or "") == current_ua:
                return False
        return True

    def _get_mfa_row(self, user_id: int) -> Optional[Dict[str, Any]]:
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute("SELECT * FROM qd_user_mfa WHERE user_id = ?", (user_id,))
            row = cur.fetchone()
            cur.close()
            return row

    def _require_mfa_row(self, user_id: int) -> Dict[str, Any]:
        row = self._get_mfa_row(user_id)
        if not row:
            raise ValueError("MFA is not configured for this account")
        return row

    @staticmethod
    def _load_totp_libs():
        try:
            import pyotp
            import qrcode
        except ImportError as e:
            raise RuntimeError("MFA dependencies are missing. Install pyotp and qrcode[pil].") from e
        return pyotp, qrcode

    @staticmethod
    def _make_qr_data_url(qrcode_module: Any, uri: str) -> str:
        img = qrcode_module.make(uri)
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        payload = base64.b64encode(buffer.getvalue()).decode("ascii")
        return f"data:image/png;base64,{payload}"

    @staticmethod
    def _generate_recovery_codes() -> list[str]:
        return [f"{secrets.token_hex(4).upper()}-{secrets.token_hex(4).upper()}" for _ in range(8)]

    @staticmethod
    def _user_agent_family(user_agent: str) -> str:
        ua = (user_agent or "").lower()
        browser = "unknown"
        for marker in ("edg/", "chrome/", "firefox/", "safari/", "opr/"):
            if marker in ua:
                browser = marker.rstrip("/")
                break
        platform = "desktop"
        for marker in ("iphone", "ipad", "android", "windows", "mac os", "linux"):
            if marker in ua:
                platform = marker
                break
        return f"{browser}:{platform}"


_mfa_service: Optional[MfaService] = None


def get_mfa_service() -> MfaService:
    global _mfa_service
    if _mfa_service is None:
        _mfa_service = MfaService()
    return _mfa_service
