"""Shared Agent Token issuance / listing for admin and self-service routes."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

from app.utils.agent_auth import (
    ALL_SCOPES,
    SCOPE_C,
    ensure_agent_gateway_schema,
    generate_token,
    parse_csv_list,
    parse_scopes,
)
from app.utils.db import get_db_connection
from app.utils.logger import get_logger

logger = get_logger(__name__)

_SAAS_MODE_VALUES = {"saas", "shared", "hosted", "multitenant", "multi-tenant"}
USER_SCOPES = frozenset(s for s in ALL_SCOPES if s != SCOPE_C)

_USER_FUND_RISKS = [
    "Agent instructions can trigger real orders; misconfiguration or prompt injection may cause unintended trades and financial loss.",
    "A leaked token grants API access within its scopes until revoked — treat it like an exchange API key.",
    "Live trading requires paper_only=false on the token AND AGENT_LIVE_TRADING_ENABLED=true on the server; both must be deliberately enabled.",
    "Paper-only mode simulates fills and never touches exchange credentials, but still consumes platform compute and job queue capacity.",
]

_SYSTEM_RISKS = [
    "Many concurrent agents increase load on DB connection pools, agent job workers, and background strategy threads.",
    "Burst traffic from automated clients can hit per-token rate limits and exchange API rate limits, affecting other tenants on shared infrastructure.",
    "Audit logs record calls but cannot replace human review of agent behavior or strategy changes.",
    "On multi-tenant SaaS, opening the T (Trading) scope expands platform operational and compliance responsibility for all hosted users.",
]


class TokenIssueError(Exception):
    """Validation failure while minting a token."""

    def __init__(
        self,
        message: str,
        *,
        code: int = 400,
        details: Any = None,
        http: int | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details
        self.http = http if http is not None else code


def is_saas_mode() -> bool:
    raw = (os.environ.get("QUANTDINGER_DEPLOYMENT_MODE") or "").strip().lower()
    return raw in _SAAS_MODE_VALUES


def agent_live_trading_enabled() -> bool:
    raw = (os.environ.get("AGENT_LIVE_TRADING_ENABLED") or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def deployment_mode_label() -> str:
    raw = (os.environ.get("QUANTDINGER_DEPLOYMENT_MODE") or "").strip().lower()
    return raw or "self_hosted"


def get_token_policy(*, for_admin: bool = False) -> dict[str, Any]:
    saas = is_saas_mode()
    risks = {
        "user_funds": list(_USER_FUND_RISKS),
        "system": list(_SYSTEM_RISKS) if saas else list(_SYSTEM_RISKS[:3]),
    }
    return {
        "deployment_mode": deployment_mode_label(),
        "is_saas": saas,
        "agent_live_trading_enabled": agent_live_trading_enabled(),
        "allowed_scopes": sorted(ALL_SCOPES if for_admin else USER_SCOPES),
        "c_scope_admin_only": True,
        "default_paper_only": True,
        "live_trading_requires_ack": True,
        "risk_disclosure": risks,
    }


def _normalize_expiry(days: int | None) -> datetime | None:
    if not days:
        return None
    try:
        d = int(days)
    except Exception:
        return None
    if d <= 0:
        return None
    return datetime.now(timezone.utc) + timedelta(days=d)


def _parse_issue_body(body: dict[str, Any], *, allow_c_scope: bool) -> dict[str, Any]:
    name = (body.get("name") or "").strip() or f"agent-{int(datetime.utcnow().timestamp())}"
    scopes = parse_scopes(body.get("scopes")) or {"R"}

    if not allow_c_scope:
        if SCOPE_C in scopes:
            raise TokenIssueError(
                "C-scope (credentials) tokens can only be issued by an administrator.",
                code=403,
                http=403,
            )
        scopes = scopes & set(USER_SCOPES)

    if not scopes:
        raise TokenIssueError("At least one scope is required.", code=400)
    if not scopes.issubset(set(ALL_SCOPES)):
        raise TokenIssueError(f"Unknown scope in {sorted(scopes)}", code=400)

    markets = parse_csv_list(body.get("markets"), default="*")
    instruments = parse_csv_list(body.get("instruments"), default="*")
    paper_only = bool(body.get("paper_only", True))
    if "T" in scopes and not paper_only:
        paper_only = False
        if not body.get("ack_live_trading_risk"):
            raise TokenIssueError(
                "Issuing a live-eligible T-scope token requires "
                "ack_live_trading_risk=true after reviewing the risk disclosure.",
                code=400,
                details="Set paper_only=true for paper-only trading, or pass "
                "ack_live_trading_risk=true to confirm you accept live-trading risks.",
            )

    rate_limit = int(body.get("rate_limit_per_min") or 60)
    expires_at = _normalize_expiry(body.get("expires_in_days"))

    return {
        "name": name,
        "scopes": scopes,
        "markets": markets,
        "instruments": instruments,
        "paper_only": paper_only,
        "rate_limit": rate_limit,
        "expires_at": expires_at,
    }


def issue_agent_token(user_id: int, body: dict[str, Any], *, allow_c_scope: bool) -> dict[str, Any]:
    ensure_agent_gateway_schema()
    parsed = _parse_issue_body(body, allow_c_scope=allow_c_scope)

    full_token, prefix, token_hash = generate_token()

    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            INSERT INTO qd_agent_tokens
              (user_id, name, token_prefix, token_hash, scopes, markets, instruments,
               paper_only, rate_limit_per_min, status, expires_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'active', %s)
            RETURNING id, created_at
            """,
            (
                int(user_id),
                parsed["name"],
                prefix,
                token_hash,
                ",".join(sorted(parsed["scopes"])),
                ",".join(parsed["markets"]),
                ",".join(parsed["instruments"]),
                parsed["paper_only"],
                parsed["rate_limit"],
                parsed["expires_at"],
            ),
        )
        db.commit()
        cur.execute(
            "SELECT id, created_at FROM qd_agent_tokens WHERE token_hash = %s",
            (token_hash,),
        )
        row = cur.fetchone()
        cur.close()

    logger.info(
        "agent token issued user_id=%s name=%s scopes=%s paper_only=%s saas=%s",
        user_id,
        parsed["name"],
        sorted(parsed["scopes"]),
        parsed["paper_only"],
        is_saas_mode(),
    )

    return {
        "id": row["id"],
        "name": parsed["name"],
        "token": full_token,
        "token_prefix": prefix,
        "scopes": sorted(parsed["scopes"]),
        "markets": parsed["markets"],
        "instruments": parsed["instruments"],
        "paper_only": parsed["paper_only"],
        "rate_limit_per_min": parsed["rate_limit"],
        "expires_at": parsed["expires_at"],
        "created_at": row.get("created_at"),
    }


def list_agent_tokens(user_id: int) -> list[dict[str, Any]]:
    ensure_agent_gateway_schema()
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT id, name, token_prefix, scopes, markets, instruments,
                   paper_only, rate_limit_per_min, status, expires_at,
                   last_used_at, created_at
            FROM qd_agent_tokens
            WHERE user_id = %s
            ORDER BY id DESC
            """,
            (int(user_id),),
        )
        rows = cur.fetchall() or []
        cur.close()
    return rows


def revoke_agent_token(user_id: int, token_id: int) -> bool:
    ensure_agent_gateway_schema()
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            "UPDATE qd_agent_tokens SET status = 'revoked' WHERE id = %s AND user_id = %s",
            (token_id, int(user_id)),
        )
        affected = cur.rowcount
        db.commit()
        cur.close()
    return bool(affected)


def list_agent_audit(user_id: int, limit: int = 100) -> list[dict[str, Any]]:
    ensure_agent_gateway_schema()
    lim = max(1, min(int(limit), 500))
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT id, agent_name, route, method, scope_class, status_code,
                   idempotency_key, duration_ms, created_at
            FROM qd_agent_audit
            WHERE user_id = %s
            ORDER BY id DESC LIMIT %s
            """,
            (int(user_id), lim),
        )
        rows = cur.fetchall() or []
        cur.close()
    return rows
