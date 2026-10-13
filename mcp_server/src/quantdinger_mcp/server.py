"""QuantDinger MCP server.

This server is intentionally a thin wrapper over the QuantDinger Agent
Gateway (`/api/agent/v1`). The REST API stays the source of truth; MCP only
exposes a curated tool surface for agent clients.
"""
from __future__ import annotations

import os
import secrets
import sys
from typing import Any

import httpx
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP

from . import __version__
from .tool_contract import register_tool
from .security import (
    assert_code_size,
    assert_indicator_code_size,
    assert_json_dict,
    consume_job_stream,
    poll_job_until_terminal,
    redact_secrets,
)


# Registered tool names (for tests / docs drift checks).
MCP_TOOL_NAMES = (
    "whoami",
    "check_health",
    "list_markets",
    "search_symbols",
    "get_klines",
    "get_price",
    "list_strategies",
    "get_strategy",
    "runtime_overview",
    "stop_strategy",
    "place_quick_order",
    "emergency_stop_trading",
    "list_jobs",
    "get_job",
    "cancel_job",
    "wait_for_job",
    "stream_job_until_done",
    "get_indicator_authoring_contract",
    "validate_indicator_code",
    "save_indicator",
    "link_indicator_config",
    "list_indicators",
    "get_indicator",
    "create_strategy",
    "update_strategy",
    "get_strategy_authoring_contract",
    "list_strategy_templates",
    "compile_strategy_code",
    "list_strategy_sources",
    "get_strategy_source",
    "save_strategy_source",
    "list_strategy_source_versions",
    "restore_strategy_source_version",
    "submit_backtest",
    "list_portfolio_positions",
    "list_paper_orders",
    "cancel_open_paper_orders",
    "list_universes",
    "get_universe",
    "list_universe_members",
    "list_factors",
    "get_factor",
    "list_watchlist",
    "add_watchlist",
    "remove_watchlist",
    "list_trading_accounts",
    "get_account_snapshot",
    "list_account_positions",
    "list_strategy_positions",
    "list_strategy_trades",
    "list_strategy_pending_orders",
    "list_agent_quick_trades",
    "list_signal_alerts",
    "create_signal_alert",
    "update_signal_alert",
    "set_signal_alert_status",
    "delete_signal_alert",
    "run_signal_alert",
)


def _env(name: str, required: bool = True) -> str:
    value = (os.environ.get(name) or "").strip()
    if not value and required:
        print(f"[quantdinger-mcp] missing required env var: {name}", file=sys.stderr)
        sys.exit(2)
    return value


def _env_float(name: str, default: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        print(f"[quantdinger-mcp] invalid {name}='{raw}', using {default}.", file=sys.stderr)
        return default


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        print(f"[quantdinger-mcp] invalid {name}='{raw}', using {default}.", file=sys.stderr)
        return default


def _truthy_env(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


BASE_URL = _env("QUANTDINGER_BASE_URL").rstrip("/")
AGENT_TOKEN = _env("QUANTDINGER_AGENT_TOKEN")
TIMEOUT_S = _env_float("QUANTDINGER_TIMEOUT_S", 60.0)
JOB_STREAM_MAX_EVENTS = _env_int("QUANTDINGER_MCP_JOB_STREAM_MAX_EVENTS", 200)
JOB_STREAM_MAX_SECONDS = _env_float("QUANTDINGER_MCP_JOB_STREAM_MAX_SECONDS", 300.0)
JOB_POLL_MAX_SECONDS = _env_float("QUANTDINGER_MCP_JOB_POLL_MAX_SECONDS", 300.0)
MCP_HOST = (os.environ.get("QUANTDINGER_MCP_HOST") or "127.0.0.1").strip()
MCP_PORT = _env_int("QUANTDINGER_MCP_PORT", 8000)
MCP_AUTH_TOKEN = _env("QUANTDINGER_MCP_AUTH_TOKEN", required=False)
MCP_PUBLIC_URL = _env("QUANTDINGER_MCP_PUBLIC_URL", required=False).rstrip("/")


class _StaticTokenVerifier:
    async def verify_token(self, token: str) -> AccessToken | None:
        if not MCP_AUTH_TOKEN or not secrets.compare_digest(token, MCP_AUTH_TOKEN):
            return None
        return AccessToken(
            token=token,
            client_id="quantdinger-mcp-client",
            scopes=["mcp"],
        )


def _http_auth_config() -> tuple[AuthSettings | None, _StaticTokenVerifier | None]:
    if not MCP_AUTH_TOKEN:
        return None, None
    public_url = MCP_PUBLIC_URL or f"http://127.0.0.1:{MCP_PORT}"
    return (
        AuthSettings(
            issuer_url=public_url,
            resource_server_url=public_url,
            required_scopes=["mcp"],
        ),
        _StaticTokenVerifier(),
    )


_client = httpx.Client(
    base_url=BASE_URL,
    timeout=TIMEOUT_S,
    headers={"Authorization": f"Bearer {AGENT_TOKEN}"},
)
_public_client = httpx.Client(base_url=BASE_URL, timeout=min(TIMEOUT_S, 15.0))


def _get(path: str, params: dict | None = None) -> Any:
    return _request("GET", path, params=params or {})


def _post(path: str, json: dict | None = None, headers: dict | None = None) -> Any:
    return _request("POST", path, json=json or {}, headers=headers or {})


def _patch(path: str, json: dict | None = None) -> Any:
    return _request("PATCH", path, json=json or {})


def _patch_with_headers(path: str, json: dict | None = None, headers: dict | None = None) -> Any:
    return _request("PATCH", path, json=json or {}, headers=headers or {})


def _delete(path: str, json: dict | None = None, headers: dict | None = None) -> Any:
    return _request("DELETE", path, json=json or {}, headers=headers or {})


def _idempotency_headers(key: str | None) -> dict[str, str]:
    value = str(key or "").strip()
    if not value:
        raise ValueError("idempotency_key is required for mutating tools")
    if len(value) > 120:
        raise ValueError("idempotency_key must not exceed 120 characters")
    return {"Idempotency-Key": value}


def _request(method: str, path: str, **kwargs: Any) -> Any:
    try:
        return _unwrap(_client.request(method, path, **kwargs))
    except httpx.TimeoutException:
        return {
            "error": True,
            "status": 504,
            "body": {"message": "QuantDinger Agent Gateway request timed out", "retriable": True},
        }
    except httpx.RequestError as exc:
        return {
            "error": True,
            "status": 503,
            "body": {
                "message": "QuantDinger Agent Gateway is unavailable",
                "retriable": True,
                "details": str(exc),
            },
        }


def _unwrap(r: httpx.Response) -> Any:
    try:
        body = r.json()
    except Exception:
        return {"error": True, "status": r.status_code, "text": r.text[:2000]}
    if r.status_code >= 400:
        return redact_secrets({"error": True, "status": r.status_code, "body": body})
    if isinstance(body, dict) and body.get("code") not in (None, 0, 200, "0", "200"):
        return redact_secrets({"error": True, "status": r.status_code, "body": body})
    if isinstance(body, dict) and "data" in body:
        data = body["data"]
        return redact_secrets(data) if isinstance(data, (dict, list)) else data
    return redact_secrets(body) if isinstance(body, (dict, list)) else body


_auth_settings, _token_verifier = _http_auth_config()

mcp = FastMCP(
    "quantdinger",
    host=MCP_HOST,
    port=MCP_PORT,
    auth=_auth_settings,
    token_verifier=_token_verifier,
    instructions=(
        "Tools for the QuantDinger self-hosted quant platform. "
        "All tools are tenant-scoped via the configured agent token. "
        "Live order placement is available only through place_quick_order with "
        "T scope, confirm_order=true, and confirm_live_trading=true when the "
        "token is not paper-only. Server-side live trading flags still apply. "
        "Runtime overview is available, and stop_strategy can stop a tenant-owned "
        "strategy when the token has T scope. "
        "SECURITY: never log or paste the agent token; responses may include "
        "redacted (***) credential placeholders; do not attempt to recover them. "
        "INDICATOR WORKFLOW: indicators are chart-only. Use "
        "get_indicator_authoring_contract, validate_indicator_code, and "
        "save_indicator for visual indicator code. Do not backtest indicator code "
        "directly. "
        "STRATEGY WORKFLOW: executable strategies use the Strategy API V2 "
        "source-owned manifest, initialize(context), and executable-handler contract. "
        "Fetch get_strategy_authoring_contract before writing source. The source owns "
        "its universe, instrument type, frequency, direction, sizing, entries, exits, "
        "risk rules, and schedules; the run request owns initial capital and date range. "
        "Use list_strategy_templates, compile_strategy_code, and "
        "save_strategy_source before create_strategy. Source versions can be "
        "listed and restored only with explicit confirmation. submit_backtest "
        "accepts Strategy API V2 Python code. "
        "Long jobs: use wait_for_job or stream_job_until_done (bounded). "
        "Never pass natural language to backtest `code`."
    ),
)
mcp._mcp_server.version = __version__
_tool = register_tool(mcp)


# Read-class tools


@_tool
def whoami() -> Any:
    """Return the calling token's identity, scopes, and allowlists."""
    return _get("/api/agent/v1/whoami")


@_tool
def check_health() -> Any:
    """Public liveness probe (no token required). Does not expose tenant data."""
    try:
        r = _public_client.get("/api/agent/v1/health")
    except httpx.TimeoutException:
        return {"error": True, "ok": False, "status": 504, "retriable": True}
    except httpx.RequestError as exc:
        return {"error": True, "ok": False, "status": 503, "retriable": True, "details": str(exc)}
    body = _unwrap(r)
    if not isinstance(body, dict) or (
        not body.get("error") and body.get("status") != "ok" and body.get("ok") is not True
    ):
        return {"error": True, "ok": False, "status": r.status_code, "body": body}
    return body


@_tool
def list_markets() -> Any:
    """List markets the configured token is allowed to query."""
    return _get("/api/agent/v1/markets")


@_tool
def search_symbols(market: str, keyword: str = "", limit: int = 20) -> Any:
    """Find symbols in a market."""
    limit = max(1, min(100, int(limit)))
    return _get(
        f"/api/agent/v1/markets/{market}/symbols",
        params={"keyword": keyword, "limit": limit},
    )


@_tool
def get_klines(
    market: str,
    symbol: str,
    timeframe: str = "1D",
    limit: int = 300,
    before_time: int | None = None,
) -> Any:
    """Return OHLCV bars for a symbol."""
    limit = max(1, min(2000, int(limit)))
    params = {"market": market, "symbol": symbol, "timeframe": timeframe, "limit": limit}
    if before_time is not None:
        params["before_time"] = int(before_time)
    return _get("/api/agent/v1/klines", params=params)


@_tool
def get_price(market: str, symbol: str) -> Any:
    """Latest price for a symbol."""
    return _get("/api/agent/v1/price", params={"market": market, "symbol": symbol})


@_tool
def list_strategies(limit: int = 50) -> Any:
    """List the tenant's strategies (compact projection)."""
    limit = max(1, min(200, int(limit)))
    return _get("/api/agent/v1/strategies", params={"limit": limit})


@_tool
def get_strategy(strategy_id: int) -> Any:
    """Get a strategy by id (tenant-scoped; secrets redacted)."""
    return _get(f"/api/agent/v1/strategies/{int(strategy_id)}")


@_tool
def runtime_overview() -> Any:
    """Compact runtime overview for this tenant."""
    return _get("/api/agent/v1/runtime/overview")


@_tool
def stop_strategy(
    strategy_id: int,
    idempotency_key: str = "",
    confirm_stop: bool = False,
) -> Any:
    """Stop one tenant-owned strategy (requires T scope and confirmation)."""
    if not confirm_stop:
        return {
            "error": True,
            "status": 400,
            "body": {
                "message": (
                    "Stopping a strategy changes runtime state. Re-call with "
                    "confirm_stop=true after explicit user approval."
                ),
            },
        }
    return _post(
        f"/api/agent/v1/strategies/{int(strategy_id)}/stop",
        headers=_idempotency_headers(idempotency_key),
    )


@_tool
def place_quick_order(
    market: str,
    symbol: str,
    side: str,
    qty: float,
    order_type: str = "market",
    limit_price: float | None = None,
    credential_id: int | None = None,
    market_type: str = "spot",
    leverage: int = 1,
    margin_mode: str | None = None,
    tp_price: float | None = None,
    sl_price: float | None = None,
    idempotency_key: str = "",
    confirm_order: bool = False,
    confirm_live_trading: bool = False,
) -> Any:
    """Place a quick order through Agent Gateway (requires T scope)."""
    if not confirm_order:
        return {
            "error": True,
            "status": 400,
            "body": {
                "message": (
                    "Order placement changes account state. Re-call with "
                    "confirm_order=true after explicit user approval."
                ),
            },
        }

    identity = _get("/api/agent/v1/whoami")
    if isinstance(identity, dict) and identity.get("paper_only") is False and not confirm_live_trading:
        return {
            "error": True,
            "status": 400,
            "body": {
                "message": (
                    "This Agent Token is live-capable. Re-call with "
                    "confirm_live_trading=true after explicit user approval."
                ),
            },
        }

    payload: dict[str, Any] = {
        "market": market,
        "symbol": symbol,
        "side": side,
        "qty": float(qty),
        "order_type": order_type,
        "market_type": market_type,
        "leverage": int(leverage or 1),
    }
    if limit_price is not None:
        payload["limit_price"] = float(limit_price)
    if credential_id is not None:
        payload["credential_id"] = int(credential_id)
    if margin_mode:
        payload["margin_mode"] = margin_mode
    if tp_price is not None:
        payload["tp_price"] = float(tp_price)
    if sl_price is not None:
        payload["sl_price"] = float(sl_price)
    headers = _idempotency_headers(idempotency_key)
    return _post("/api/agent/v1/quick-trade/orders", json=payload, headers=headers)


@_tool
def emergency_stop_trading(
    idempotency_key: str = "",
    confirm_emergency_stop: bool = False,
) -> Any:
    """Cancel agent orders best-effort and revoke all tenant T tokens."""
    if not confirm_emergency_stop:
        return {
            "error": True,
            "status": 400,
            "body": {
                "message": (
                    "The emergency stop revokes all tenant T tokens and attempts to cancel "
                    "agent-originated orders. Re-call with confirm_emergency_stop=true."
                ),
            },
        }
    return _post(
        "/api/agent/v1/quick-trade/kill-switch",
        json={"confirm": True},
        headers=_idempotency_headers(idempotency_key),
    )


@_tool
def get_job(job_id: str) -> Any:
    """Poll a previously submitted backtest job."""
    return _get(f"/api/agent/v1/jobs/{job_id}")


@_tool
def cancel_job(
    job_id: str,
    idempotency_key: str = "",
    confirm_cancel: bool = False,
) -> Any:
    """Cancel a queued/running tenant job (requires B scope and confirmation)."""
    if not confirm_cancel:
        return {
            "error": True,
            "status": 400,
            "body": {"message": "Re-call with confirm_cancel=true after explicit user approval."},
        }
    return _post(
        f"/api/agent/v1/jobs/{job_id}/cancel",
        headers=_idempotency_headers(idempotency_key),
    )


@_tool
def list_jobs(kind: str | None = None, limit: int = 50) -> Any:
    """List recent jobs for this tenant. Optional `kind` filter."""
    limit = max(1, min(200, int(limit)))
    params: dict[str, Any] = {"limit": limit}
    if kind:
        params["kind"] = kind
    return _get("/api/agent/v1/jobs", params=params)


@_tool
def wait_for_job(
    job_id: str,
    timeout_s: float | None = None,
    interval_s: float = 2.0,
) -> Any:
    """Poll a job until it succeeds/fails or timeout."""
    cap = float(timeout_s if timeout_s is not None else JOB_POLL_MAX_SECONDS)
    cap = max(5.0, min(600.0, cap))
    interval = max(0.5, min(30.0, float(interval_s)))
    return poll_job_until_terminal(
        lambda jid: _get(f"/api/agent/v1/jobs/{jid}"),
        job_id,
        timeout_s=cap,
        interval_s=interval,
    )


@_tool
def stream_job_until_done(
    job_id: str,
    since_seq: int = 0,
    max_events: int | None = None,
    max_seconds: float | None = None,
) -> Any:
    """Consume job SSE with hard caps."""
    events_cap = int(max_events if max_events is not None else JOB_STREAM_MAX_EVENTS)
    events_cap = max(1, min(500, events_cap))
    seconds_cap = float(max_seconds if max_seconds is not None else JOB_STREAM_MAX_SECONDS)
    seconds_cap = max(5.0, min(600.0, seconds_cap))
    out = consume_job_stream(
        _client,
        f"/api/agent/v1/jobs/{job_id}/stream",
        since_seq=int(since_seq or 0),
        max_events=events_cap,
        max_seconds=seconds_cap,
    )
    if isinstance(out.get("events"), list):
        out["events"] = redact_secrets(out["events"])
    if isinstance(out.get("result"), dict):
        out["result"] = redact_secrets(out["result"])
    return out


# Indicator workspace


@_tool
def get_indicator_authoring_contract() -> Any:
    """Fetch chart-only indicator I/O contract + starter Python template."""
    return _get("/api/agent/v1/indicators/authoring-contract")


@_tool
def validate_indicator_code(code: str, indicator_params: dict | None = None) -> Any:
    """Sandbox-validate chart-only indicator Python without saving."""
    assert_indicator_code_size(code)
    params = assert_json_dict("indicator_params", indicator_params)
    return _post(
        "/api/agent/v1/indicators/validate",
        json={"code": code, "indicator_params": params},
    )


@_tool
def save_indicator(
    code: str,
    name: str | None = None,
    description: str | None = None,
    indicator_id: int | None = None,
    validate: bool = True,
    idempotency_key: str = "",
) -> Any:
    """Save chart-only indicator code into the user's indicator library."""
    assert_indicator_code_size(code)
    payload: dict[str, Any] = {"code": code, "validate": validate}
    if name:
        payload["name"] = name
    if description:
        payload["description"] = description
    if indicator_id:
        payload["indicator_id"] = int(indicator_id)
    return _post(
        "/api/agent/v1/indicators",
        json=payload,
        headers=_idempotency_headers(idempotency_key),
    )


@_tool
def link_indicator_config(
    config: dict,
    idempotency_key: str = "",
) -> Any:
    """Normalize/save an indicator configuration and link its indicator id."""
    return _post(
        "/api/agent/v1/indicators/link-config",
        json={"indicator_config": assert_json_dict("config", config)},
        headers=_idempotency_headers(idempotency_key),
    )


@_tool
def list_indicators(limit: int = 50) -> Any:
    """List saved indicators for this tenant (no code bodies)."""
    limit = max(1, min(200, int(limit)))
    return _get("/api/agent/v1/indicators", params={"limit": limit})


@_tool
def get_indicator(indicator_id: int) -> Any:
    """Fetch one chart indicator including its Python source."""
    return _get(f"/api/agent/v1/indicators/{int(indicator_id)}")


# Strategy workspace


@_tool
def create_strategy(
    name: str,
    source_id: int,
    initial_capital: float,
    execution_mode: str = "signal",
    credential_id: int | None = None,
    leverage_enabled: bool = False,
    leverage: float = 1.0,
    params: dict | None = None,
    position_side: str | None = None,
    account_risk: dict | None = None,
    idempotency_key: str = "",
) -> Any:
    """Deploy one saved Strategy API V2 source in stopped state."""
    payload = {
        "name": str(name).strip(),
        "sourceId": int(source_id),
        "initialCapital": float(initial_capital),
        "executionMode": str(execution_mode),
        "credentialId": int(credential_id) if credential_id else None,
        "leverageEnabled": bool(leverage_enabled),
        "leverage": float(leverage),
        "params": assert_json_dict("params", params),
    }
    if position_side:
        payload["positionSide"] = str(position_side)
    if account_risk is not None:
        payload["accountRisk"] = assert_json_dict("account_risk", account_risk)
    return _post(
        "/api/agent/v1/strategies",
        json=payload,
        headers=_idempotency_headers(idempotency_key),
    )


@_tool
def update_strategy(strategy_id: int, patch: dict, idempotency_key: str = "") -> Any:
    """Patch the canonical deployment configuration for a strategy (scope W)."""
    body = assert_json_dict("patch", patch)
    return _patch_with_headers(
        f"/api/agent/v1/strategies/{int(strategy_id)}",
        json=body,
        headers=_idempotency_headers(idempotency_key),
    )


# Strategy API V2 source workspace


@_tool
def get_strategy_authoring_contract() -> Any:
    """Fetch the canonical Strategy API V2 runtime contract and starter Python source."""
    return _get("/api/agent/v1/strategy-sources/authoring-contract")


@_tool
def list_strategy_templates(limit: int = 20) -> Any:
    """List system Strategy API V2 templates, including starter source code."""
    limit = max(1, min(100, int(limit)))
    return _get("/api/agent/v1/strategy-sources/templates", params={"limit": limit})


@_tool
def compile_strategy_code(
    code: str | None = None,
    source_id: int | None = None,
    idempotency_key: str = "",
) -> Any:
    """Compile Strategy API V2 code and return its canonical manifest without saving."""
    payload: dict[str, Any] = {}
    if code is not None:
        assert_code_size(code, label="Strategy code")
        payload["code"] = code
    if source_id is not None:
        payload["source_id"] = int(source_id)
    if not payload:
        raise ValueError("code or source_id is required")
    return _post(
        "/api/agent/v1/strategy-sources/compile",
        json=payload,
        headers=_idempotency_headers(idempotency_key),
    )


@_tool
def list_strategy_sources(limit: int = 50) -> Any:
    """List saved Strategy API V2 sources without code bodies."""
    limit = max(1, min(200, int(limit)))
    return _get("/api/agent/v1/strategy-sources", params={"limit": limit})


@_tool
def get_strategy_source(source_id: int) -> Any:
    """Get one tenant-owned Strategy API V2 source including code."""
    return _get(f"/api/agent/v1/strategy-sources/{int(source_id)}")


@_tool
def save_strategy_source(
    name: str,
    code: str,
    description: str = "",
    source_id: int | None = None,
    template_key: str | None = None,
    param_schema: dict | None = None,
    metadata: dict | None = None,
    idempotency_key: str = "",
) -> Any:
    """Compile and save a private Strategy API V2 source, creating a version snapshot."""
    assert_code_size(code, label="Strategy code")
    payload: dict[str, Any] = {
        "name": str(name).strip(),
        "description": str(description or ""),
        "code": code,
        "param_schema": assert_json_dict("param_schema", param_schema),
        "metadata": assert_json_dict("metadata", metadata),
    }
    if template_key:
        payload["template_key"] = str(template_key)
    if source_id is None:
        return _post(
            "/api/agent/v1/strategy-sources",
            json=payload,
            headers=_idempotency_headers(idempotency_key),
        )
    return _patch_with_headers(
        f"/api/agent/v1/strategy-sources/{int(source_id)}",
        json=payload,
        headers=_idempotency_headers(idempotency_key),
    )


@_tool
def list_strategy_source_versions(source_id: int) -> Any:
    """List immutable version snapshots for one tenant-owned strategy source."""
    return _get(f"/api/agent/v1/strategy-sources/{int(source_id)}/versions")


@_tool
def restore_strategy_source_version(
    source_id: int,
    version_id: int,
    idempotency_key: str = "",
    confirm_restore: bool = False,
) -> Any:
    """Restore one source version after explicit confirmation; creates a new snapshot."""
    if not confirm_restore:
        return {
            "error": True,
            "status": 400,
            "body": {
                "message": (
                    "Restoring a source version overwrites the current draft. Re-call with "
                    "confirm_restore=true after explicit user approval."
                ),
            },
        }
    return _post(
        f"/api/agent/v1/strategy-sources/{int(source_id)}/versions/{int(version_id)}/restore",
        json={"confirm": True},
        headers=_idempotency_headers(idempotency_key),
    )


# Backtests


@_tool
def submit_backtest(
    code: str,
    start_date: str,
    end_date: str,
    initial_capital: float = 10000,
    commission: float = 0.001,
    slippage: float | None = None,
    leverage_enabled: bool = False,
    leverage: float = 1.0,
    params: dict | None = None,
    idempotency_key: str = "",
) -> Any:
    """Submit a Strategy API V2 backtest; its manifest owns market data scope."""
    assert_code_size(code, label="Strategy code")
    payload: dict[str, Any] = {
        "code": code,
        "startDate": start_date,
        "endDate": end_date,
        "initialCapital": initial_capital,
        "commission": commission,
        "leverageEnabled": bool(leverage_enabled),
        "leverage": leverage,
        "params": assert_json_dict("params", params),
    }
    if slippage is not None:
        payload["slippage"] = slippage
    headers = _idempotency_headers(idempotency_key)
    return _post("/api/agent/v1/backtest/run", json=payload, headers=headers)


# Portfolio (read-only)


@_tool
def list_portfolio_positions() -> Any:
    """Manual portfolio positions for this tenant (read-only, scope R)."""
    return _get("/api/agent/v1/portfolio/positions")


@_tool
def list_paper_orders() -> Any:
    """Recent paper orders submitted via agent trading APIs (scope R)."""
    return _get("/api/agent/v1/portfolio/paper-orders")


@_tool
def cancel_open_paper_orders(
    idempotency_key: str = "",
    confirm_cancel: bool = False,
) -> Any:
    """Compatibility alias for the tenant emergency trading stop."""
    if not confirm_cancel:
        return {
            "error": True,
            "status": 400,
            "body": {
                "message": (
                    "Cancelling open paper orders changes account state. Re-call with "
                    "confirm_cancel=true after explicit user approval."
                ),
            },
        }
    return _post(
        "/api/agent/v1/quick-trade/kill-switch",
        json={"confirm": True},
        headers=_idempotency_headers(idempotency_key),
    )


# Research workspace


@_tool
def list_universes() -> Any:
    """List visible point-in-time universes."""
    return _get("/api/agent/v1/research/universes")


@_tool
def get_universe(universe_id: int) -> Any:
    """Get universe metadata."""
    return _get(f"/api/agent/v1/research/universes/{int(universe_id)}")


@_tool
def list_universe_members(
    universe_id: int,
    as_of: str | None = None,
    limit: int = 200,
    cursor: int = 0,
) -> Any:
    """Resolve universe members as of an optional YYYY-MM-DD date."""
    params: dict[str, Any] = {
        "limit": max(1, min(500, int(limit))),
        "cursor": max(0, int(cursor)),
    }
    if as_of:
        params["as_of"] = as_of
    return _get(f"/api/agent/v1/research/universes/{int(universe_id)}/members", params=params)


@_tool
def list_factors(category: str = "", factor_type: str = "") -> Any:
    """List registered technical and fundamental factor definitions."""
    return _get(
        "/api/agent/v1/research/factors",
        params={"category": category, "factor_type": factor_type},
    )


@_tool
def get_factor(factor_id: str) -> Any:
    """Get one factor definition and parameter schema."""
    return _get(f"/api/agent/v1/research/factors/{factor_id}")


@_tool
def list_watchlist(limit: int = 100, cursor: int = 0) -> Any:
    """List the tenant watchlist."""
    return _get(
        "/api/agent/v1/research/watchlist",
        params={"limit": max(1, min(500, int(limit))), "cursor": max(0, int(cursor))},
    )


@_tool
def add_watchlist(
    market: str,
    symbol: str,
    idempotency_key: str = "",
    name: str = "",
    exchange_id: str = "",
    market_type: str = "",
) -> Any:
    """Add a validated symbol to the watchlist."""
    return _post(
        "/api/agent/v1/research/watchlist",
        json={
            "market": market,
            "symbol": symbol,
            "name": name,
            "exchange_id": exchange_id,
            "market_type": market_type,
        },
        headers=_idempotency_headers(idempotency_key),
    )


@_tool
def remove_watchlist(
    market: str,
    symbol: str,
    idempotency_key: str = "",
    confirm_remove: bool = False,
) -> Any:
    """Remove a watchlist symbol after confirmation."""
    if not confirm_remove:
        return {"error": True, "status": 400, "body": {"message": "confirm_remove=true is required"}}
    return _delete(
        "/api/agent/v1/research/watchlist",
        json={"market": market, "symbol": symbol},
        headers=_idempotency_headers(idempotency_key),
    )


# Broker and execution observations


@_tool
def list_trading_accounts() -> Any:
    """List safe broker credential metadata; never returns secrets."""
    return _get("/api/agent/v1/trading/accounts")


@_tool
def get_account_snapshot(credential_id: int) -> Any:
    """Fetch live positions and open orders for an owned credential."""
    return _get(f"/api/agent/v1/trading/accounts/{int(credential_id)}/snapshot")


@_tool
def list_account_positions(credential_id: int, market_type: str = "") -> Any:
    """Read the locally mirrored broker-account positions."""
    params = {"market_type": market_type} if market_type else None
    return _get(f"/api/agent/v1/trading/accounts/{int(credential_id)}/positions", params=params)


@_tool
def list_strategy_positions(strategy_id: int) -> Any:
    """Read positions for one tenant strategy."""
    return _get(f"/api/agent/v1/trading/strategies/{int(strategy_id)}/positions")


@_tool
def list_strategy_trades(strategy_id: int, limit: int = 50, cursor: int = 0) -> Any:
    """Read cursor-paginated strategy fills."""
    return _get(
        f"/api/agent/v1/trading/strategies/{int(strategy_id)}/trades",
        params={"limit": max(1, min(200, int(limit))), "cursor": max(0, int(cursor))},
    )


@_tool
def list_strategy_pending_orders(strategy_id: int, limit: int = 50, cursor: int = 0) -> Any:
    """Read cursor-paginated pending orders for one strategy."""
    return _get(
        f"/api/agent/v1/trading/strategies/{int(strategy_id)}/pending-orders",
        params={"limit": max(1, min(200, int(limit))), "cursor": max(0, int(cursor))},
    )


@_tool
def list_agent_quick_trades(limit: int = 50, cursor: int = 0) -> Any:
    """Read quick trades created by Agent Gateway."""
    return _get(
        "/api/agent/v1/trading/quick-trades",
        params={"limit": max(1, min(200, int(limit))), "cursor": max(0, int(cursor))},
    )


# Notification automation


@_tool
def list_signal_alerts(limit: int = 50, cursor: int = 0) -> Any:
    """List indicator signal-alert tasks (scope N)."""
    return _get(
        "/api/agent/v1/notifications/signal-alerts",
        params={"limit": max(1, min(200, int(limit))), "cursor": max(0, int(cursor))},
    )


@_tool
def create_signal_alert(payload: dict, idempotency_key: str = "") -> Any:
    """Create an indicator signal-alert task."""
    return _post(
        "/api/agent/v1/notifications/signal-alerts",
        json=assert_json_dict("payload", payload),
        headers=_idempotency_headers(idempotency_key),
    )


@_tool
def update_signal_alert(task_id: int, payload: dict, idempotency_key: str = "") -> Any:
    """Update an owned signal-alert task."""
    return _patch_with_headers(
        f"/api/agent/v1/notifications/signal-alerts/{int(task_id)}",
        json=assert_json_dict("payload", payload),
        headers=_idempotency_headers(idempotency_key),
    )


@_tool
def set_signal_alert_status(
    task_id: int,
    status: str,
    idempotency_key: str = "",
) -> Any:
    """Pause or resume a signal-alert task."""
    return _post(
        f"/api/agent/v1/notifications/signal-alerts/{int(task_id)}/status",
        json={"status": status},
        headers=_idempotency_headers(idempotency_key),
    )


@_tool
def delete_signal_alert(
    task_id: int,
    idempotency_key: str = "",
    confirm_delete: bool = False,
) -> Any:
    """Delete a signal-alert task after confirmation."""
    if not confirm_delete:
        return {"error": True, "status": 400, "body": {"message": "confirm_delete=true is required"}}
    return _delete(
        f"/api/agent/v1/notifications/signal-alerts/{int(task_id)}",
        headers=_idempotency_headers(idempotency_key),
    )


@_tool
def run_signal_alert(
    task_id: int,
    idempotency_key: str = "",
    confirm_delivery: bool = False,
) -> Any:
    """Evaluate an alert immediately; may deliver notifications."""
    if not confirm_delivery:
        return {"error": True, "status": 400, "body": {"message": "confirm_delivery=true is required"}}
    return _post(
        f"/api/agent/v1/notifications/signal-alerts/{int(task_id)}/run",
        json={"confirm": True},
        headers=_idempotency_headers(idempotency_key),
    )


_TRANSPORTS = {"stdio", "sse", "streamable-http"}


def _resolve_transport() -> str:
    raw = (os.environ.get("QUANTDINGER_MCP_TRANSPORT") or "stdio").strip().lower()
    if raw in ("http", "streaming-http", "streamable_http"):
        raw = "streamable-http"
    if raw not in _TRANSPORTS:
        print(
            f"[quantdinger-mcp] unknown transport '{raw}'. "
            f"Expected one of: {sorted(_TRANSPORTS)} (or http/streaming-http alias).",
            file=sys.stderr,
        )
        sys.exit(2)
    return raw


def _apply_http_settings_from_env() -> None:
    host = (os.environ.get("QUANTDINGER_MCP_HOST") or "").strip()
    port_raw = (os.environ.get("QUANTDINGER_MCP_PORT") or "").strip()
    settings = getattr(mcp, "settings", None)
    if settings is None:
        return
    if host:
        try:
            settings.host = host
        except Exception:
            pass
    if port_raw:
        try:
            settings.port = int(port_raw)
        except Exception:
            print(
                f"[quantdinger-mcp] invalid QUANTDINGER_MCP_PORT='{port_raw}', ignoring.",
                file=sys.stderr,
            )


def _validate_network_security(transport: str) -> None:
    if transport == "stdio":
        return
    host = str(getattr(getattr(mcp, "settings", None), "host", MCP_HOST) or "").strip().lower()
    loopback = host in {"127.0.0.1", "localhost", "::1", "[::1]"}
    if MCP_AUTH_TOKEN:
        if len(MCP_AUTH_TOKEN) < 32:
            print(
                "[quantdinger-mcp] QUANTDINGER_MCP_AUTH_TOKEN must be at least 32 characters.",
                file=sys.stderr,
            )
            sys.exit(2)
        if secrets.compare_digest(MCP_AUTH_TOKEN, AGENT_TOKEN):
            print(
                "[quantdinger-mcp] inbound MCP auth token must differ from the Agent Gateway token.",
                file=sys.stderr,
            )
            sys.exit(2)
        if not loopback:
            if not MCP_PUBLIC_URL.lower().startswith("https://") and not _truthy_env(
                "QUANTDINGER_MCP_ALLOW_HTTP"
            ):
                print(
                    "[quantdinger-mcp] non-loopback authenticated MCP requires an HTTPS "
                    "QUANTDINGER_MCP_PUBLIC_URL. Set QUANTDINGER_MCP_ALLOW_HTTP=true only "
                    "behind a trusted TLS-terminating private proxy.",
                    file=sys.stderr,
                )
                sys.exit(2)
        return
    if loopback:
        return
    if _truthy_env("QUANTDINGER_MCP_ALLOW_INSECURE_HTTP"):
        print(
            "[quantdinger-mcp] WARNING: unauthenticated MCP HTTP is enabled on a non-loopback host.",
            file=sys.stderr,
        )
        return
    print(
        "[quantdinger-mcp] refusing unauthenticated MCP HTTP on a non-loopback host. "
        "Set QUANTDINGER_MCP_AUTH_TOKEN or explicitly set "
        "QUANTDINGER_MCP_ALLOW_INSECURE_HTTP=true behind a trusted private proxy.",
        file=sys.stderr,
    )
    sys.exit(2)


def main() -> None:
    """Entrypoint."""
    transport = _resolve_transport()
    if transport in ("sse", "streamable-http"):
        _apply_http_settings_from_env()
        _validate_network_security(transport)
    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
