"""
Agent Gateway authentication, scopes, audit, idempotency, and rate limiting.

This module is intentionally **separate** from `app.utils.auth` (human JWT).
Agent tokens authenticate machine clients (external AI agents, MCP servers,
custom automations) against `/api/agent/v1/...` and are subject to
capability-class scoping, per-token rate limits, and an append-only audit log.

Design reference: docs/agent/AI_INTEGRATION_DESIGN.md
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from functools import wraps
from typing import Any, Callable, Iterable, Optional

from flask import g, jsonify, request

from app.utils.db import get_db_connection
from app.utils.logger import get_logger

logger = get_logger(__name__)


TOKEN_PREFIX = "qd_agent_"

# Capability classes (see AI_INTEGRATION_DESIGN.md §3).
SCOPE_R = "R"   # Read
SCOPE_W = "W"   # Workspace write
SCOPE_B = "B"   # Backtest / simulation
SCOPE_N = "N"   # Notifications & misc side-effects
SCOPE_C = "C"   # Credentials (admin only)
SCOPE_T = "T"   # Trading / capital
ALL_SCOPES = (SCOPE_R, SCOPE_W, SCOPE_B, SCOPE_N, SCOPE_C, SCOPE_T)


_schema_ready = False
_schema_lock = threading.Lock()


def _ensure_schema() -> None:
    """Idempotent runtime guard.

    The canonical schema lives in `migrations/init.sql` and is applied by the
    Postgres container's first-boot script.  For installations that upgraded
    in-place we still want the agent tables to materialize on first use so the
    gateway never fails with "relation does not exist".
    """
    global _schema_ready
    if _schema_ready:
        return
    with _schema_lock:
        if _schema_ready:
            return
        ddl = """
        CREATE TABLE IF NOT EXISTS qd_agent_tokens (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE CASCADE,
            name VARCHAR(80) NOT NULL,
            token_prefix VARCHAR(24) NOT NULL,
            token_hash VARCHAR(128) NOT NULL,
            scopes TEXT NOT NULL DEFAULT 'R',
            markets TEXT NOT NULL DEFAULT '*',
            instruments TEXT NOT NULL DEFAULT '*',
            paper_only BOOLEAN NOT NULL DEFAULT TRUE,
            rate_limit_per_min INTEGER NOT NULL DEFAULT 60,
            status VARCHAR(20) NOT NULL DEFAULT 'active',
            expires_at TIMESTAMP,
            last_used_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT NOW()
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_tokens_hash ON qd_agent_tokens(token_hash);
        CREATE INDEX IF NOT EXISTS idx_agent_tokens_user ON qd_agent_tokens(user_id);

        CREATE TABLE IF NOT EXISTS qd_agent_jobs (
            id BIGSERIAL PRIMARY KEY,
            job_id VARCHAR(40) NOT NULL UNIQUE,
            user_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE CASCADE,
            agent_token_id INTEGER REFERENCES qd_agent_tokens(id) ON DELETE SET NULL,
            kind VARCHAR(40) NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'queued',
            request JSONB NOT NULL DEFAULT '{}'::jsonb,
            result JSONB,
            error TEXT,
            progress JSONB,
            idempotency_key VARCHAR(120),
            created_at TIMESTAMP DEFAULT NOW(),
            started_at TIMESTAMP,
            finished_at TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_agent_jobs_user ON qd_agent_jobs(user_id);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_jobs_idem
            ON qd_agent_jobs(agent_token_id, kind, idempotency_key)
            WHERE idempotency_key IS NOT NULL;

        CREATE TABLE IF NOT EXISTS qd_agent_audit (
            id BIGSERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            agent_token_id INTEGER,
            agent_name VARCHAR(80),
            route VARCHAR(160) NOT NULL,
            method VARCHAR(8) NOT NULL,
            scope_class VARCHAR(4) NOT NULL,
            status_code INTEGER NOT NULL,
            idempotency_key VARCHAR(120),
            request_summary JSONB,
            response_summary JSONB,
            duration_ms INTEGER,
            created_at TIMESTAMP DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_agent_audit_user ON qd_agent_audit(user_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS qd_agent_paper_orders (
            id BIGSERIAL PRIMARY KEY,
            order_uid VARCHAR(40) NOT NULL UNIQUE,
            user_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE CASCADE,
            agent_token_id INTEGER REFERENCES qd_agent_tokens(id) ON DELETE SET NULL,
            market VARCHAR(40) NOT NULL,
            symbol VARCHAR(60) NOT NULL,
            side VARCHAR(8) NOT NULL,
            order_type VARCHAR(16) NOT NULL DEFAULT 'market',
            qty DECIMAL(28,10) NOT NULL,
            limit_price DECIMAL(28,10),
            fill_price DECIMAL(28,10),
            fill_value DECIMAL(28,10),
            status VARCHAR(16) NOT NULL DEFAULT 'filled',
            note TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_agent_paper_orders_user
            ON qd_agent_paper_orders(user_id, created_at DESC);
        """
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                for stmt in [s.strip() for s in ddl.split(";") if s.strip()]:
                    cur.execute(stmt)
                db.commit()
                cur.close()
            _schema_ready = True
        except Exception as exc:
            logger.warning(f"agent_auth: schema ensure failed (will retry): {exc}")


def ensure_agent_gateway_schema() -> None:
    """Ensure agent gateway tables exist (idempotent).

    Admin JWT routes (e.g. token issuance) bypass ``agent_required``, which
    normally triggers ``_ensure_schema()`` on first agent call. Without this,
    a fresh or partially migrated DB can hit ``INSERT`` before tables exist and
    return an unhandled 500.
    """
    _ensure_schema()


# ─────────────────────────── token primitives ───────────────────────────

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_token() -> tuple[str, str, str]:
    """Generate a new agent token.

    Returns:
        (full_token, token_prefix, token_hash). Only the hash is stored;
        the full token is shown to the operator exactly once.
    """
    body = secrets.token_urlsafe(32).rstrip("=")
    full = f"{TOKEN_PREFIX}{body}"
    prefix = full[: len(TOKEN_PREFIX) + 8]      # qd_agent_XXXXXXXX
    return full, prefix, _hash_token(full)


def parse_scopes(raw: str | Iterable[str] | None) -> set[str]:
    if raw is None:
        return {SCOPE_R}
    if isinstance(raw, str):
        items = [p.strip().upper() for p in raw.split(",") if p.strip()]
    else:
        items = [str(p).strip().upper() for p in raw if str(p).strip()]
    return {p for p in items if p in ALL_SCOPES}


def parse_csv_list(raw: str | None, default: str = "*") -> list[str]:
    if not raw:
        return [default]
    items = [p.strip() for p in str(raw).split(",") if p.strip()]
    return items or [default]


def list_matches(item: str, allowlist: list[str]) -> bool:
    if not allowlist or "*" in allowlist:
        return True
    needle = (item or "").strip().upper()
    return any(needle == a.strip().upper() for a in allowlist)


# ─────────────────────────── rate limit (in-process) ───────────────────────────

_rate_state: dict[int, list[float]] = {}
_rate_lock = threading.Lock()


def _check_rate_limit(token_id: int, limit_per_min: int) -> bool:
    now = time.time()
    window_start = now - 60.0
    with _rate_lock:
        bucket = [t for t in _rate_state.get(token_id, []) if t >= window_start]
        if len(bucket) >= max(1, int(limit_per_min)):
            _rate_state[token_id] = bucket
            return False
        bucket.append(now)
        _rate_state[token_id] = bucket
        return True


# ─────────────────────────── verification ───────────────────────────

def _extract_bearer() -> Optional[str]:
    auth_header = request.headers.get("Authorization", "")
    parts = auth_header.split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]
    return None


def _lookup_token(raw_token: str) -> Optional[dict]:
    token_hash = _hash_token(raw_token)
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT id, user_id, name, scopes, markets, instruments,
                   paper_only, rate_limit_per_min, status, expires_at
            FROM qd_agent_tokens
            WHERE token_hash = %s
            """,
            (token_hash,),
        )
        row = cur.fetchone()
        cur.close()
    return row


def _touch_token_last_used(token_id: int) -> None:
    try:
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                "UPDATE qd_agent_tokens SET last_used_at = NOW() WHERE id = %s",
                (token_id,),
            )
            db.commit()
            cur.close()
    except Exception as exc:
        logger.debug(f"agent_auth: failed to touch last_used_at: {exc}")


# ─────────────────────────── audit ───────────────────────────

_REDACT_KEYS = {
    "password",
    "secret",
    "secret_key",
    "secretkey",
    "token",
    "apikey",
    "api_key",
    "authorization",
    "passphrase",
    "private_key",
    "privatekey",
    "access_token",
    "accesstoken",
    "refresh_token",
    "refreshtoken",
    "bot_token",
    "bottoken",
    "webhook_secret",
    "webhooksecret",
    "signing_secret",
    "signingsecret",
    "client_secret",
    "clientsecret",
}


def _redact(obj: Any, depth: int = 0) -> Any:
    if depth > 3:
        return "<truncated>"
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if str(k).lower() in _REDACT_KEYS:
                out[k] = "<redacted>"
            else:
                out[k] = _redact(v, depth + 1)
        return out
    if isinstance(obj, list):
        return [_redact(v, depth + 1) for v in obj[:20]]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        if isinstance(obj, str) and len(obj) > 500:
            return obj[:500] + "..."
        return obj
    return str(type(obj).__name__)


def _audit(scope_class: str, status_code: int, response_summary: Any, duration_ms: int) -> None:
    token_row = getattr(g, "agent_token", None) or {}
    try:
        req_summary: dict[str, Any] = {
            "args": _redact(dict(request.args)),
        }
        if request.is_json:
            try:
                req_summary["json"] = _redact(request.get_json(silent=True) or {})
            except Exception:
                req_summary["json"] = "<unreadable>"
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                INSERT INTO qd_agent_audit
                  (user_id, agent_token_id, agent_name, route, method,
                   scope_class, status_code, idempotency_key,
                   request_summary, response_summary, duration_ms)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    token_row.get("user_id") or 0,
                    token_row.get("id"),
                    token_row.get("name"),
                    request.path,
                    request.method,
                    scope_class,
                    int(status_code),
                    request.headers.get("Idempotency-Key"),
                    json.dumps(req_summary, default=str)[:8000],
                    json.dumps(_redact(response_summary), default=str)[:8000] if response_summary is not None else None,
                    int(duration_ms),
                ),
            )
            db.commit()
            cur.close()
    except Exception as exc:
        logger.warning(f"agent_auth: audit insert failed: {exc}")


# ─────────────────────────── decorator ───────────────────────────

def _err(code: int, msg: str, details: Any = None, retriable: bool = False, status: int = 400):
    body = {"code": code, "message": msg, "details": details, "retriable": retriable}
    return jsonify(body), status


def agent_required(scope: str = SCOPE_R):
    """Flask decorator: enforce token auth + scope + rate limit + audit.

    Sets `g.agent_token` (dict) and `g.agent_user_id` (int) for downstream code.
    Logs every call (success or denial) into qd_agent_audit.
    """
    if scope not in ALL_SCOPES:
        raise ValueError(f"invalid scope: {scope}")

    def decorator(fn: Callable):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            _ensure_schema()
            t0 = time.time()
            raw = _extract_bearer()
            if not raw or not raw.startswith(TOKEN_PREFIX):
                resp, code = _err(401, "Missing or malformed agent token", status=401)
                _audit(scope, 401, {"reason": "missing_token"}, int((time.time() - t0) * 1000))
                return resp, code

            row = _lookup_token(raw)
            if not row:
                resp, code = _err(401, "Unknown agent token", status=401)
                _audit(scope, 401, {"reason": "unknown_token"}, int((time.time() - t0) * 1000))
                return resp, code

            if row.get("status") != "active":
                resp, code = _err(401, f"Token is {row.get('status')}", status=401)
                _audit(scope, 401, {"reason": "inactive"}, int((time.time() - t0) * 1000))
                return resp, code

            expires_at = row.get("expires_at")
            if expires_at and isinstance(expires_at, datetime) and expires_at < datetime.utcnow():
                resp, code = _err(401, "Token expired", status=401)
                _audit(scope, 401, {"reason": "expired"}, int((time.time() - t0) * 1000))
                return resp, code

            scopes = parse_scopes(row.get("scopes"))
            if scope not in scopes:
                g.agent_token = row
                resp, code = _err(403, f"Token lacks required scope: {scope}", status=403)
                _audit(scope, 403, {"granted": sorted(scopes)}, int((time.time() - t0) * 1000))
                return resp, code

            if not _check_rate_limit(row["id"], int(row.get("rate_limit_per_min") or 60)):
                g.agent_token = row
                resp, code = _err(429, "Rate limit exceeded for this token", retriable=True, status=429)
                _audit(scope, 429, {"limit_per_min": row.get("rate_limit_per_min")}, int((time.time() - t0) * 1000))
                return resp, code

            g.agent_token = row
            g.agent_user_id = int(row["user_id"])

            try:
                response = fn(*args, **kwargs)
            except Exception as exc:
                logger.error(f"agent route raised: {exc}", exc_info=True)
                _audit(scope, 500, {"error": str(exc)[:500]}, int((time.time() - t0) * 1000))
                return _err(500, "Internal server error", details=str(exc), status=500)

            status_code = 200
            payload_summary: Any = None
            if isinstance(response, tuple) and len(response) >= 2:
                status_code = int(response[1])
                first = response[0]
                if hasattr(first, "get_json"):
                    payload_summary = first.get_json(silent=True)
            elif hasattr(response, "status_code"):
                status_code = int(response.status_code)
                if hasattr(response, "get_json"):
                    payload_summary = response.get_json(silent=True)

            _touch_token_last_used(row["id"])
            _audit(scope, status_code, payload_summary, int((time.time() - t0) * 1000))
            return response

        return wrapper

    return decorator


# ─────────────────────────── idempotency ───────────────────────────

@contextmanager
def with_idempotency(kind: str):
    """Context manager that yields an existing job dict if the same agent
    already executed this kind+key, else yields None to indicate the caller
    should perform the work and persist a new job row.

    Use only on writeful (W/B/T) endpoints.  Reads are naturally idempotent.
    """
    token_row = getattr(g, "agent_token", None) or {}
    key = request.headers.get("Idempotency-Key")
    if not key or not token_row.get("id"):
        yield None
        return

    try:
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                SELECT job_id, status, result, error
                FROM qd_agent_jobs
                WHERE agent_token_id = %s AND kind = %s AND idempotency_key = %s
                ORDER BY id DESC LIMIT 1
                """,
                (token_row["id"], kind, key),
            )
            existing = cur.fetchone()
            cur.close()
    except Exception as exc:
        logger.warning(f"agent_auth: idempotency lookup failed: {exc}")
        existing = None

    yield existing


# ─────────────────────────── helpers for routes ───────────────────────────

def current_token() -> dict:
    return getattr(g, "agent_token", {}) or {}


def current_user_id() -> int:
    return int(getattr(g, "agent_user_id", 0) or 0)


def market_allowed(market: str) -> bool:
    row = current_token()
    return list_matches(market, parse_csv_list(row.get("markets"), default="*"))


def instrument_allowed(symbol: str) -> bool:
    row = current_token()
    return list_matches(symbol, parse_csv_list(row.get("instruments"), default="*"))


def paper_only() -> bool:
    row = current_token()
    return bool(row.get("paper_only", True))
