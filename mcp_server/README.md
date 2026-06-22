# QuantDinger MCP server

[![PyPI](https://img.shields.io/pypi/v/quantdinger-mcp?style=flat-square&logo=pypi&logoColor=white)](https://pypi.org/project/quantdinger-mcp/)
[![Python](https://img.shields.io/pypi/pyversions/quantdinger-mcp?style=flat-square&logo=python&logoColor=white)](https://pypi.org/project/quantdinger-mcp/)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg?style=flat-square)](../LICENSE)

Thin Model Context Protocol server that exposes a curated subset of the
QuantDinger Agent Gateway (`/api/agent/v1`) as MCP tools, so AI clients
that support MCP (Cursor, Claude-style desktop apps, OpenClaw, NanoBot, etc.)
can drive QuantDinger without writing custom HTTP code.

This package is an **additive** integration. The Agent Gateway REST API
remains the source of truth.

## Security model

- **No live trading via MCP.** `quick-trade/*` endpoints are intentionally
  not wrapped. Use REST with an explicitly scoped `T` token if needed.
- **Scope gating stays on the server.** The MCP layer forwards your agent
  token; it cannot bypass allowlists or scopes.
- **Defense in depth:** MCP redacts known credential fields (`api_key`,
  `secret`, `passphrase`, …) in JSON responses. The Gateway also redacts
  strategy rows before returning them to agents.
- **Bounded long jobs:** `stream_job_until_done` caps event count and duration;
  `wait_for_job` caps poll time. Tune via env vars below.
- **LLM cost guard:** `submit_ai_optimize` requires `confirm_llm_usage=true`.
- **Payload limits:** indicator Python source is capped at **512 KiB** on
  both Gateway and MCP client.

## What it exposes

Read-class (R), Workspace write (W), and Backtest-class (B) tools.

| Tool | Class | Purpose |
|------|-------|---------|
| `whoami` | R | Inspect the calling token |
| `check_health` | — | Public liveness (no token) |
| `list_markets` | R | Markets the token may query |
| `search_symbols` | R | Symbols within a market |
| `get_klines` | R | OHLCV bars |
| `get_price` | R | Latest price |
| `list_strategies` | R | Tenant's strategies (compact) |
| `get_strategy` | R | One strategy (secrets redacted) |
| `list_jobs` | R | Recent async jobs |
| `get_job` | R | Poll one job |
| `wait_for_job` | R | Poll until terminal or timeout |
| `stream_job_until_done` | R | Bounded SSE consumer |
| `get_indicator_authoring_contract` | R | Indicator I/O contract + starter template |
| `validate_indicator_code` | R | Sandbox validate without save |
| `save_indicator` | W | Persist to indicator library |
| `list_indicators` | R | Tenant indicator list |
| `get_indicator` | R | One indicator with code |
| `create_strategy` | W | Create stopped strategy (+ auto-save indicator) |
| `update_strategy` | W | Patch strategy fields (blocks `status=running`) |
| `submit_backtest` | B | Queue a backtest (`strict_mode`, `strategy_config`, `indicator_params`) |
| `regime_detect` | B | Synchronous regime detection |
| `submit_experiment_pipeline` | B | Queue legacy grid pipeline |
| `submit_structured_tune` | B | Queue grid/random tuning |
| `submit_ai_optimize` | B | Queue LLM optimization (requires confirm flag) |
| `list_portfolio_positions` | R | Manual portfolio positions |
| `list_paper_orders` | R | Recent paper orders |

## Install

From PyPI (recommended — works on any machine without cloning the repo):

```bash
pipx install quantdinger-mcp
# or, no install at all (cached on first run):
uvx quantdinger-mcp
# or, into a venv:
pip install quantdinger-mcp
```

Editable install for hacking on the server itself:

```bash
cd mcp_server
pip install -e .
```

## Run

Configuration is env-only so the same binary works in desktop and cloud.

| Variable | Required | Purpose |
|----------|----------|---------|
| `QUANTDINGER_BASE_URL`     | yes | e.g. `http://localhost:8888` |
| `QUANTDINGER_AGENT_TOKEN`  | yes | a token issued via `/api/agent/v1/admin/tokens` |
| `QUANTDINGER_MCP_TRANSPORT`| no  | `stdio` (default), `sse`, or `streamable-http` |
| `QUANTDINGER_MCP_HOST`     | no  | bind host for HTTP transports (default `127.0.0.1`) |
| `QUANTDINGER_MCP_PORT`     | no  | bind port for HTTP transports (default `8000`) |
| `QUANTDINGER_TIMEOUT_S`    | no  | upstream HTTP timeout (default `60`) |
| `QUANTDINGER_MCP_JOB_STREAM_MAX_EVENTS` | no | SSE cap (default `200`) |
| `QUANTDINGER_MCP_JOB_STREAM_MAX_SECONDS` | no | SSE time cap (default `300`) |
| `QUANTDINGER_MCP_JOB_POLL_MAX_SECONDS` | no | `wait_for_job` cap (default `300`) |

### stdio (desktop IDEs)

```bash
QUANTDINGER_BASE_URL=http://localhost:8888 \
QUANTDINGER_AGENT_TOKEN=qd_agent_xxxxx \
quantdinger-mcp
```

### SSE / Streamable HTTP (cloud agents, remote IDEs)

```bash
QUANTDINGER_BASE_URL=http://localhost:8888 \
QUANTDINGER_AGENT_TOKEN=qd_agent_xxxxx \
QUANTDINGER_MCP_TRANSPORT=streamable-http \
QUANTDINGER_MCP_HOST=0.0.0.0 \
QUANTDINGER_MCP_PORT=7800 \
quantdinger-mcp
```

The server is then reachable at `http://<host>:7800/`. Use `sse` instead of
`streamable-http` for clients that only support the older SSE transport.

## Wire into a client

### Local stdio client config

```json
{
  "mcpServers": {
    "quantdinger": {
      "command": "quantdinger-mcp",
      "env": {
        "QUANTDINGER_BASE_URL": "http://localhost:8888",
        "QUANTDINGER_AGENT_TOKEN": "qd_agent_xxxxxxxx"
      }
    }
  }
}
```

### Remote HTTP client config

For clients that connect to an MCP server over HTTP/SSE rather than spawning
a subprocess, point them at the URL the server is bound to (e.g.
`http://your-host:7800`) and let the client handle protocol negotiation.

Never put production exchange keys or admin JWTs in the MCP config — only
agent tokens, scoped to the capabilities the client actually needs.

Recommended scopes for indicator authoring: **R + W + B**.
