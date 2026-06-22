"""
Fernet encryption for qd_exchange_credentials.encrypted_config.

Uses SECRET_KEY from the environment (SHA-256 digest → urlsafe base64 → Fernet key).
"""

from __future__ import annotations

import base64
import hashlib
import os
from typing import Any

from cryptography.fernet import Fernet, InvalidToken


def _fernet_from_secret() -> Fernet:
    secret = (os.getenv("SECRET_KEY") or "").strip()
    if not secret:
        try:
            from app.config.settings import Config

            secret = str(Config.SECRET_KEY or "").strip()
        except Exception:
            secret = ""
    if not secret:
        raise ValueError("SECRET_KEY is not set; cannot encrypt or decrypt exchange credentials")
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


def encrypt_credential_blob(plaintext_json: str) -> str:
    """Encrypt JSON text for storage in encrypted_config."""
    if plaintext_json is None:
        plaintext_json = ""
    f = _fernet_from_secret()
    return f.encrypt(plaintext_json.encode("utf-8")).decode("ascii")


def decrypt_credential_blob(stored: Any) -> str:
    """
    Decrypt DB value to JSON text. Empty / None yields empty string.
    """
    if stored is None:
        return ""
    s = stored.decode("utf-8") if isinstance(stored, (bytes, bytearray)) else str(stored)
    s = s.strip()
    if not s:
        return ""
    f = _fernet_from_secret()
    try:
        return f.decrypt(s.encode("ascii")).decode("utf-8")
    except InvalidToken as e:
        raise ValueError(
            "Cannot decrypt exchange credential (wrong SECRET_KEY or data not encrypted with this key)"
        ) from e
