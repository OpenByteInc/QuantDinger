"""Database-backed Settings page values shared by every process.

PostgreSQL is the source of truth. Redis holds a short-lived snapshot so every
process can poll for changes cheaply; a missing or evicted snapshot is rebuilt
from the database. Secret values stay Fernet-encrypted in both places and are
decrypted only inside the process that applies them.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

from app.utils.cache import CacheManager
from app.utils.credential_crypto import decrypt_credential_blob, encrypt_credential_blob
from app.utils.db import get_db_connection
from app.utils.logger import get_logger

logger = get_logger(__name__)

CACHE_KEY = "runtime_settings:v1"
# ponytail: concurrent saves can race their snapshot writes; the TTL bounds the
# staleness. Switch to Redis pub/sub if settings saves ever become frequent.
CACHE_TTL_SEC = 60


def _load_snapshot_from_db() -> Dict[str, Any]:
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute("SELECT setting_key, value, is_secret, version FROM qd_runtime_settings")
        rows = cur.fetchall() or []
        cur.close()
    return {
        "version": max((int(row["version"]) for row in rows), default=0),
        "rows": {
            row["setting_key"]: {"value": row["value"] or "", "secret": bool(row["is_secret"])}
            for row in rows
        },
    }


def load_snapshot() -> Dict[str, Any]:
    """Return {version, rows} from the shared cache, falling back to the database."""
    cache = CacheManager()
    if cache.is_redis:
        cached = cache.get(CACHE_KEY)
        if isinstance(cached, dict) and "version" in cached and "rows" in cached:
            return cached
    snapshot = _load_snapshot_from_db()
    # An in-process memory cache cannot see other processes' saves, so only
    # Redis is trusted as the shared snapshot.
    if cache.is_redis:
        cache.set(CACHE_KEY, snapshot, CACHE_TTL_SEC)
    return snapshot


def snapshot_values(snapshot: Dict[str, Any]) -> Dict[str, str]:
    """Plaintext values from a snapshot; undecryptable secrets are skipped."""
    values: Dict[str, str] = {}
    for key, row in (snapshot.get("rows") or {}).items():
        value = str(row.get("value") or "")
        if row.get("secret") and value:
            try:
                value = decrypt_credential_blob(value)
            except ValueError as exc:
                logger.error("Runtime setting %s cannot be decrypted: %s", key, exc)
                continue
        values[key] = value
    return values


def load_values() -> Dict[str, str]:
    return snapshot_values(load_snapshot())


def read_settings() -> Dict[str, str]:
    """Effective Settings page values: the .env baseline overlaid with saved values."""
    from app.services.settings.env_file import read_env_file

    values = read_env_file()
    try:
        values.update(load_values())
    except Exception as exc:
        logger.warning("Saved runtime settings unavailable; showing .env values: %s", exc)
    return values


def save_values(
    updates: Dict[str, str],
    secret_keys: Iterable[str] = (),
    operator_id: Optional[int] = None,
) -> None:
    """Persist values under one new version and refresh the shared snapshot."""
    if not updates:
        return
    secret_keys = set(secret_keys)
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute("SELECT nextval('qd_runtime_settings_version_seq') AS version")
        version = int(cur.fetchone()["version"])
        for key, value in updates.items():
            value = "" if value is None else str(value)
            secret = key in secret_keys
            cur.execute(
                """
                INSERT INTO qd_runtime_settings
                    (setting_key, value, is_secret, version, updated_by, updated_at)
                VALUES (?, ?, ?, ?, ?, NOW())
                ON CONFLICT (setting_key) DO UPDATE SET
                    value = EXCLUDED.value,
                    is_secret = EXCLUDED.is_secret,
                    version = EXCLUDED.version,
                    updated_by = EXCLUDED.updated_by,
                    updated_at = NOW()
                """,
                (key, encrypt_credential_blob(value) if secret and value else value, secret, version, operator_id),
            )
        db.commit()
        cur.close()

    cache = CacheManager()
    if cache.is_redis:
        cache.set(CACHE_KEY, _load_snapshot_from_db(), CACHE_TTL_SEC)
