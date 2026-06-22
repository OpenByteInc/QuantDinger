"""
QuantDinger MCP server — exposes the Agent Gateway as MCP tools.

This is intentionally a thin wrapper:
  * REST stays the source of truth (`/api/agent/v1`).
  * Exposes Read (R), Workspace write (W), and Backtest (B) tools.
  * Trading (T) is NOT exposed here — use REST directly if explicitly enabled.
  * The user-supplied agent token's scopes still gate every call server-side.

If you want to expose more (e.g. trading), prefer issuing a token with the
right scopes and keep this server unchanged — that way the security boundary
stays in the Gateway, not in the MCP layer.
"""
from __future__ import annotations

import os
import sys
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

from .security import (
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
    "list_jobs",
    "get_job",
    "wait_for_job",
    "stream_job_until_done",
    "get_indicator_authoring_contract",
    "validate_indicator_code",
    "save_indicator",
    "list_indicators",
    "get_indicator",
    "create_strategy",
    "update_strategy",
    "submit_backtest",
    "regime_detect",
    "submit_experiment_pipeline",
    "submit_structured_tune",
    "submit_ai_optimize",
    "list_portfolio_positions",
    "list_paper_orders",
)


def _env(name: str, required: bool = True) -> str:
    value = (os.environ.get(name) or "").strip()
    if not value and required:
        print(
            f"[quantdinger-mcp] missing required env var: {name}",
            file=sys.stderr,
        )
        sys.exit(2)
    return value


BASE_URL = _env("QUANTDINGER_BASE_URL").rstrip("/")
AGENT_TOKEN = _env("QUANTDINGER_AGENT_TOKEN")
TIMEOUT_S = float(os.environ.get("QUANTDINGER_TIMEOUT_S", "60"))
JOB_STREAM_MAX_EVENTS = int(os.environ.get("QUANTDINGER_MCP_JOB_STREAM_MAX_EVENTS", "200"))
JOB_STREAM_MAX_SECONDS = float(os.environ.get("QUANTDINGER_MCP_JOB_STREAM_MAX_SECONDS", "300"))
JOB_POLL_MAX_SECONDS = float(os.environ.get("QUANTDINGER_MCP_JOB_POLL_MAX_SECONDS", "300"))


_client = httpx.Client(
    base_url=BASE_URL,
    timeout=TIMEOUT_S,
    headers={"Authorization": f"Bearer {AGENT_TOKEN}"},
)

_public_client = httpx.Client(base_url=BASE_URL, timeout=min(TIMEOUT_S, 15.0))


def _get(path: str, params: dict | None = None) -> Any:
    r = _client.get(path, params=params or {})
    return _unwrap(r)


def _post(path: str, json: dict | None = None, headers: dict | None = None) -> Any:
    r = _client.post(path, json=json or {}, headers=headers or {})
    return _unwrap(r)


def _patch(path: str, json: dict | None = None) -> Any:
    r = _client.patch(path, json=json or {})
    return _unwrap(r)


def _unwrap(r: httpx.Response) -> Any:
    try:
        body = r.json()
    except Exception:
        return {
            "error": True,
            "status": r.status_code,
            "text": r.text[:2000],
        }
    if r.status_code >= 400:
        return {
            "error": True,
            "status": r.status_code,
            "body": body,
        }
    if isinstance(body, dict) and "data" in body:
        data = body["data"]
        return redact_secrets(data) if isinstance(data, (dict, list)) else data
    return redact_secrets(body) if isinstance(body, (dict, list)) else body


mcp = FastMCP(
    "quantdinger",
    instructions=(
        "Tools for the QuantDinger self-hosted quant platform. "
        "All tools are tenant-scoped via the configured agent token. "
        "Trading is intentionally NOT exposed via MCP; use the REST API for that. "
        "SECURITY: never log or paste the agent token; responses may include "
        "redacted (***) credential placeholders — do not attempt to recover them. "
        "INDICATOR WORKFLOW (required): "
        "1) get_indicator_authoring_contract — read template + I/O rules BEFORE writing code; "
        "2) validate_indicator_code — sandbox check; "
        "3) save_indicator — persist to indicator library (scope W); "
        "4) create_strategy — pass indicator_id or indicator_code (auto-saved to library); "
        "5) submit_backtest — test performance (strict_mode=true aligns with live). "
        "Long jobs: use wait_for_job or stream_job_until_done (bounded). "
        "submit_ai_optimize consumes server LLM quota — call only when explicitly requested. "
        "Never pass natural language to backtest `code`; it must be full Python."
    ),
)


# ───────────────────────────── Read-class tools ─────────────────────────────

@mcp.tool()
def whoami() -> Any:
    """Return the calling token's identity, scopes, and allowlists."""
    return _get("/api/agent/v1/whoami")


@mcp.tool()
def check_health() -> Any:
    """Public liveness probe (no token required). Does not expose tenant data."""
    r = _public_client.get("/api/agent/v1/health")
    try:
        body = r.json()
    except Exception:
        return {"ok": r.status_code == 200, "status": r.status_code}
    if isinstance(body, dict) and "data" in body:
        return body["data"]
    return body


@mcp.tool()
def list_markets() -> Any:
    """List markets the configured token is allowed to query."""
    return _get("/api/agent/v1/markets")


@mcp.tool()
def search_symbols(market: str, keyword: str = "", limit: int = 20) -> Any:
    """Find symbols in a market.

    Args:
        market: Market id, e.g. "Crypto", "USStock", "Forex".
        keyword: Substring/code; empty returns hot symbols.
        limit:   1..100, default 20.
    """
    limit = max(1, min(100, int(limit)))
    return _get(
        f"/api/agent/v1/markets/{market}/symbols",
        params={"keyword": keyword, "limit": limit},
    )


@mcp.tool()
def get_klines(
    market: str,
    symbol: str,
    timeframe: str = "1D",
    limit: int = 300,
    before_time: int | None = None,
) -> Any:
    """OHLCV bars.

    Args:
        market:      e.g. "Crypto"
        symbol:      e.g. "BTC/USDT"
        timeframe:   "1m"/"5m"/"15m"/"30m"/"1H"/"4H"/"1D"/"1W"
        limit:       1..2000
        before_time: unix seconds; for paging older bars.
    """
    limit = max(1, min(2000, int(limit)))
    params = {"market": market, "symbol": symbol, "timeframe": timeframe, "limit": limit}
    if before_time is not None:
        params["before_time"] = int(before_time)
    return _get("/api/agent/v1/klines", params=params)


@mcp.tool()
def get_price(market: str, symbol: str) -> Any:
    """Latest price for a symbol."""
    return _get("/api/agent/v1/price", params={"market": market, "symbol": symbol})


@mcp.tool()
def list_strategies(limit: int = 50) -> Any:
    """List the tenant's strategies (compact projection)."""
    limit = max(1, min(200, int(limit)))
    return _get("/api/agent/v1/strategies", params={"limit": limit})


@mcp.tool()
def get_strategy(strategy_id: int) -> Any:
    """Get a strategy by id (tenant-scoped; secrets redacted)."""
    return _get(f"/api/agent/v1/strategies/{int(strategy_id)}")


@mcp.tool()
def get_job(job_id: str) -> Any:
    """Poll a previously-submitted backtest / experiment job."""
    return _get(f"/api/agent/v1/jobs/{job_id}")


@mcp.tool()
def list_jobs(kind: str | None = None, limit: int = 50) -> Any:
    """List recent jobs for this tenant. Optional `kind` filter."""
    limit = max(1, min(200, int(limit)))
    params: dict[str, Any] = {"limit": limit}
    if kind:
        params["kind"] = kind
    return _get("/api/agent/v1/jobs", params=params)


@mcp.tool()
def wait_for_job(
    job_id: str,
    timeout_s: float | None = None,
    interval_s: float = 2.0,
) -> Any:
    """Poll a job until it succeeds/fails or timeout (safer than infinite SSE)."""
    cap = float(timeout_s if timeout_s is not None else JOB_POLL_MAX_SECONDS)
    cap = max(5.0, min(600.0, cap))
    interval = max(0.5, min(30.0, float(interval_s)))
    return poll_job_until_terminal(
        lambda jid: _get(f"/api/agent/v1/jobs/{jid}"),
        job_id,
        timeout_s=cap,
        interval_s=interval,
    )


@mcp.tool()
def stream_job_until_done(
    job_id: str,
    since_seq: int = 0,
    max_events: int | None = None,
    max_seconds: float | None = None,
) -> Any:
    """Consume job SSE with hard caps (max events / max seconds).

    Prefer this over raw REST SSE when running inside MCP clients.
    Returns `{events, result, truncated, event_count}`.
    """
    events_cap = int(max_events if max_events is not None else JOB_STREAM_MAX_EVENTS)
    events_cap = max(1, min(500, events_cap))
    seconds_cap = float(max_seconds if max_seconds is not None else JOB_STREAM_MAX_SECONDS)
    seconds_cap = max(5.0, min(600.0, seconds_cap))
    path = f"/api/agent/v1/jobs/{job_id}/stream"
    out = consume_job_stream(
        _client,
        path,
        since_seq=int(since_seq or 0),
        max_events=events_cap,
        max_seconds=seconds_cap,
    )
    if isinstance(out.get("events"), list):
        out["events"] = redact_secrets(out["events"])
    if isinstance(out.get("result"), dict):
        out["result"] = redact_secrets(out["result"])
    return out


# ───────────────────────────── Indicator workspace ─────────────────────────────

@mcp.tool()
def get_indicator_authoring_contract() -> Any:
    """Fetch QuantDinger indicator I/O contract + starter Python template.

    Call this BEFORE writing indicator code. The `code` field in backtests
    and strategies must be valid Python matching this contract — not natural
    language prompts.
    """
    return _get("/api/agent/v1/indicators/authoring-contract")


@mcp.tool()
def validate_indicator_code(code: str, indicator_params: dict | None = None) -> Any:
    """Sandbox-validate indicator Python without saving."""
    assert_indicator_code_size(code)
    params = assert_json_dict("indicator_params", indicator_params)
    return _post(
        "/api/agent/v1/indicators/validate",
        json={"code": code, "indicator_params": params},
    )


@mcp.tool()
def save_indicator(
    code: str,
    name: str | None = None,
    description: str | None = None,
    indicator_id: int | None = None,
    validate: bool = True,
) -> Any:
    """Save indicator into the user's indicator library (appears in IDE list).

    Requires agent token scope W. Validates by default before insert.
    """
    assert_indicator_code_size(code)
    payload: dict[str, Any] = {"code": code, "validate": validate}
    if name:
        payload["name"] = name
    if description:
        payload["description"] = description
    if indicator_id:
        payload["indicator_id"] = int(indicator_id)
    return _post("/api/agent/v1/indicators", json=payload)


@mcp.tool()
def list_indicators(limit: int = 50) -> Any:
    """List saved indicators for this tenant (no code bodies)."""
    limit = max(1, min(200, int(limit)))
    return _get("/api/agent/v1/indicators", params={"limit": limit})


@mcp.tool()
def get_indicator(indicator_id: int) -> Any:
    """Fetch one indicator including its Python source."""
    return _get(f"/api/agent/v1/indicators/{int(indicator_id)}")


# ───────────────────────────── Strategy workspace ─────────────────────────────

@mcp.tool()
def create_strategy(
    strategy_name: str,
    market_category: str,
    trading_config: dict,
    indicator_code: str | None = None,
    indicator_id: int | None = None,
    indicator_name: str | None = None,
    strategy_type: str = "IndicatorStrategy",
    execution_mode: str = "signal",
) -> Any:
    """Create a stopped strategy; embed or link an indicator.

    If `indicator_code` is provided without `indicator_id`, the server auto-saves
    it to the indicator library so it appears in the IDE list.
    Requires scope W. Status is always forced to stopped.
    """
    if indicator_code:
        assert_indicator_code_size(indicator_code)
    tc = assert_json_dict("trading_config", trading_config)
    payload: dict[str, Any] = {
        "strategy_name": strategy_name,
        "strategy_type": strategy_type,
        "market_category": market_category,
        "execution_mode": execution_mode,
        "trading_config": tc,
        "status": "stopped",
    }
    ic: dict[str, Any] = {}
    if indicator_id:
        ic["indicator_id"] = int(indicator_id)
    if indicator_code:
        ic["indicator_code"] = indicator_code
    if indicator_name:
        ic["indicator_name"] = indicator_name
    if ic:
        payload["indicator_config"] = ic
    return _post("/api/agent/v1/strategies", json=payload)


@mcp.tool()
def update_strategy(strategy_id: int, patch: dict) -> Any:
    """Patch a strategy (scope W). Cannot set status=running without scope T."""
    body = assert_json_dict("patch", patch)
    if str(body.get("status") or "").strip().lower() == "running":
        return {
            "error": True,
            "status": 403,
            "body": {
                "message": (
                    "Activating a strategy (status=running) requires T scope. "
                    "Use the REST quick-trade path with an explicitly scoped token."
                ),
            },
        }
    return _patch(f"/api/agent/v1/strategies/{int(strategy_id)}", json=body)


# ───────────────────────────── Backtest / experiments ─────────────────────────────

@mcp.tool()
def submit_backtest(
    code: str,
    market: str,
    symbol: str,
    timeframe: str,
    start_date: str,
    end_date: str,
    initial_capital: float = 10000,
    commission: float = 0.001,
    slippage: float | None = None,
    leverage: int = 1,
    trade_direction: str = "long",
    strict_mode: bool = True,
    strategy_config: dict | None = None,
    indicator_params: dict | None = None,
    idempotency_key: str | None = None,
) -> Any:
    """Submit a backtest. Returns `{job_id, status, ...}` — poll with `get_job`.

    Args:
        code:           Indicator code (Python).
        market/symbol/timeframe: Series identification.
        start_date/end_date:     YYYY-MM-DD.
        initial_capital, commission, slippage, leverage, trade_direction:
                       standard backtest knobs.
        strict_mode:    True = next-bar open (live-aligned); False = aggressive MTF/bar path.
        strategy_config: optional TP/SL/trailing overrides (merged with strictMode).
        indicator_params: `# @param` overrides for the indicator script.
        idempotency_key: optional; repeat calls with the same key return the
                         original job instead of submitting a duplicate.
    """
    assert_indicator_code_size(code)
    payload: dict[str, Any] = {
        "code": code,
        "market": market,
        "symbol": symbol,
        "timeframe": timeframe,
        "start_date": start_date,
        "end_date": end_date,
        "initial_capital": initial_capital,
        "commission": commission,
        "leverage": leverage,
        "trade_direction": trade_direction,
        "strictMode": strict_mode,
        "strategy_config": assert_json_dict("strategy_config", strategy_config),
        "indicator_params": assert_json_dict("indicator_params", indicator_params),
    }
    if slippage is not None:
        payload["slippage"] = slippage
    headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
    return _post("/api/agent/v1/backtests", json=payload, headers=headers)


@mcp.tool()
def regime_detect(
    market: str,
    symbol: str,
    timeframe: str,
    start_date: str,
    end_date: str,
) -> Any:
    """Detect the current market regime (synchronous)."""
    return _post(
        "/api/agent/v1/experiments/regime/detect",
        json={
            "market": market, "symbol": symbol, "timeframe": timeframe,
            "startDate": start_date, "endDate": end_date,
        },
    )


@mcp.tool()
def submit_experiment_pipeline(payload: dict, idempotency_key: str | None = None) -> Any:
    """Submit a legacy grid pipeline job (scope B). Poll with get_job / wait_for_job."""
    body = assert_json_dict("payload", payload)
    headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
    return _post("/api/agent/v1/experiments/pipeline", json=body, headers=headers)


@mcp.tool()
def submit_structured_tune(payload: dict) -> Any:
    """Submit a grid/random tuning job. Returns a job for polling.

    `payload` should include `base` (a backtest spec) and either `parameterSpace`
    (grid) or `randomTrials` (random). See `docs/AI_TRADING_SYSTEM_PLAN_CN.md`.
    """
    return _post("/api/agent/v1/experiments/structured-tune", json=assert_json_dict("payload", payload))


@mcp.tool()
def submit_ai_optimize(payload: dict, confirm_llm_usage: bool = False) -> Any:
    """Submit an LLM-driven multi-round optimization job (scope B).

    Consumes server-side LLM provider quota. You MUST pass confirm_llm_usage=true
    to acknowledge cost/rate-limit implications.
    """
    if not confirm_llm_usage:
        return {
            "error": True,
            "status": 400,
            "body": {
                "message": (
                    "submit_ai_optimize consumes LLM quota. "
                    "Re-call with confirm_llm_usage=true after explicit user approval."
                ),
            },
        }
    return _post("/api/agent/v1/experiments/ai-optimize", json=assert_json_dict("payload", payload))


# ───────────────────────────── Portfolio (read-only) ─────────────────────────────

@mcp.tool()
def list_portfolio_positions() -> Any:
    """Manual portfolio positions for this tenant (read-only, scope R)."""
    return _get("/api/agent/v1/portfolio/positions")


@mcp.tool()
def list_paper_orders() -> Any:
    """Recent paper orders submitted via agent trading APIs (scope R)."""
    return _get("/api/agent/v1/portfolio/paper-orders")


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


def main() -> None:
    """Entrypoint.

    Transport selection (env-only — works in both desktop and cloud):
      QUANTDINGER_MCP_TRANSPORT=stdio              (default; stdin/stdout)
      QUANTDINGER_MCP_TRANSPORT=sse                (SSE over HTTP)
      QUANTDINGER_MCP_TRANSPORT=streamable-http    (newer MCP HTTP transport)
      QUANTDINGER_MCP_HOST=0.0.0.0                 (bind for HTTP transports)
      QUANTDINGER_MCP_PORT=7800                    (port for HTTP transports)
    """
    transport = _resolve_transport()
    if transport in ("sse", "streamable-http"):
        _apply_http_settings_from_env()
    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
