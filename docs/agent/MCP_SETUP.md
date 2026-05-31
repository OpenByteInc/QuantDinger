# MCP Setup — connect Cursor / Claude Code / Codex to QuantDinger

This is the detailed setup guide for wiring an MCP-capable AI client (Cursor,
Claude Code, Codex desktop, OpenClaw, NanoBot, …) to a QuantDinger backend.
The root [`README`](../../README.md) gives the 30-second pitch; everything
below is the actual recipe.

The QuantDinger backend exposes an **Agent Gateway** at `/api/agent/v1`, and a
small MCP server (published on PyPI as
[`quantdinger-mcp`](https://pypi.org/project/quantdinger-mcp/)) wraps that
gateway as Model Context Protocol tools. After one human-issued token, your AI
client can read markets, manage strategies, run backtests, and (paper-only by
default) place trades — without ever seeing your exchange keys or your admin
JWT.

> **Two non-negotiable safety properties.** Every agent call is
> **audit-logged**, and trading-class tokens are **paper-only by default**.
> Live execution requires *both* `paper_only=false` on the token AND
> `AGENT_LIVE_TRADING_ENABLED=true` on the server.

---

## Step 1 — Pick a backend, then issue an agent token

The MCP client config in Step 2 is **identical** for both backends — only the
value of `QUANTDINGER_BASE_URL` changes.

### Path A · Hosted ([ai.quantdinger.com](https://ai.quantdinger.com)) — 30 seconds

Best for: trying QuantDinger from Cursor / Claude Code without installing
anything; demos; research notebooks against shared datasets.

1. Sign up at [ai.quantdinger.com](https://ai.quantdinger.com).
2. Open **Profile → My Agent Token** → **Issue Token**.
3. `QUANTDINGER_BASE_URL=https://ai.quantdinger.com`.

The hosted SaaS instance allows **T** (Trading) scope for each user’s own
tenant. Tokens are **paper-only by default**; live execution still requires
`paper_only=false`, `ack_live_trading_risk=true` at issuance, and
`AGENT_LIVE_TRADING_ENABLED=true` on the server.

**SaaS risks (system + funds):**

- **User funds:** Agent misconfiguration or prompt injection can trigger real
  orders once live mode is enabled; a leaked token grants API access within its
  scopes until revoked.
- **System / multi-tenant:** Many concurrent agents stress DB pools, job workers,
  and exchange rate limits; audit logs do not replace human review; opening T on
  SaaS expands platform operational and compliance responsibility.

### Path B · Self-hosted (this repo) — production, private data, live trading

Best for: anyone with their own exchange keys, anyone with private
strategies/data, teams behind a VPN, or anyone who eventually wants live
execution.

1. Bring up the stack per the [root README's "Try in 2 minutes"](../../README.md#try-in-2-minutes).
2. Log in, open **Profile → My Agent Token** (admins may also use **Sidebar → Agent Tokens** at `/agent-tokens` for audit).
3. `QUANTDINGER_BASE_URL=http://localhost:8888` (or your LAN URL).

You decide scopes (incl. **T**), market/instrument allowlists, rate limits,
and whether `AGENT_LIVE_TRADING_ENABLED=true` is ever flipped.

### Issue the token (either path)

1. Click **Issue Token** → name it (`cursor-mcp`, `claude-research`, …).
2. Pick scopes — start with **R + B** (read + backtest); add **W** to let the
   agent save indicators and create/edit strategies.
3. Copy the token **once** — the dialog shows the full string once; the
   server only keeps a SHA-256 hash.

Prefer the CLI? See [`AGENT_QUICKSTART.md`](AGENT_QUICKSTART.md) for the
equivalent `curl`.

---

## Step 2 — Wire the MCP server into your AI client

The MCP server lives in [`mcp_server/`](../../mcp_server/). Two transports work
everywhere:

### A. Local stdio (Cursor, Claude Code, Codex desktop, etc.)

The server is published on PyPI as
[`quantdinger-mcp`](https://pypi.org/project/quantdinger-mcp/). Drop this into
`.cursor/mcp.json`, `~/.config/claude/claude_desktop_config.json`, or your
client's equivalent (template: [`cursor-mcp.example.json`](cursor-mcp.example.json)):

```json
{
  "mcpServers": {
    "quantdinger": {
      "command": "uvx",
      "args": ["quantdinger-mcp"],
      "env": {
        "QUANTDINGER_BASE_URL":    "http://localhost:8888",
        "QUANTDINGER_AGENT_TOKEN": "qd_agent_xxxxxxxx"
      }
    }
  }
}
```

`uvx` ([install uv](https://docs.astral.sh/uv/getting-started/installation/))
downloads + caches the package on first run; no virtualenv setup. If you
prefer pip:

```bash
pip install quantdinger-mcp
# then use {"command": "quantdinger-mcp", "args": []}
```

For Claude Code's CLI helper:

```bash
claude mcp add quantdinger \
  --env QUANTDINGER_BASE_URL=http://localhost:8888 \
  --env QUANTDINGER_AGENT_TOKEN=qd_agent_xxxxxxxx \
  -- uvx quantdinger-mcp
```

### B. Remote HTTP (cloud agents, browser IDEs, anything that can't spawn subprocesses)

Run the MCP server as a long-lived service, then point clients at the URL:

```bash
QUANTDINGER_BASE_URL=https://your-host \
QUANTDINGER_AGENT_TOKEN=qd_agent_xxxxxxxx \
QUANTDINGER_MCP_TRANSPORT=streamable-http \
QUANTDINGER_MCP_HOST=0.0.0.0 \
QUANTDINGER_MCP_PORT=7800 \
quantdinger-mcp
# clients connect to http://your-host:7800
```

Use `QUANTDINGER_MCP_TRANSPORT=sse` instead for clients that only speak the
older SSE transport. Put a reverse proxy in front for TLS and IP allowlisting.

---

## Step 3 — Talk to your agent

Restart the IDE, then ask things like:

- *"Call `get_indicator_authoring_contract`, then write and validate a Keltner
  breakout indicator."*
- *"Pull the last 90 daily candles for BTC/USDT and tell me what the regime
  detector says."*
- *"Backtest the indicator with `strict_mode=true` on ETH/USDT 4h between
  2024-01-01 and 2024-06-30; use `wait_for_job` for the result."*
- *"Create a strategy named **eth-trend-bot**, use the indicator I just
  saved, leave it in `stopped` state."*

### MCP tools vs REST

The MCP server wraps **Read (R), Workspace write (W), and Backtest (B)** tools
only. It does **not** expose trading (`quick-trade/*`), admin token APIs, or
credential vault access — even if your token has those scopes. That boundary
is intentional.

Long-running jobs: prefer MCP `wait_for_job` or bounded `stream_job_until_done`
(capped by `QUANTDINGER_MCP_JOB_STREAM_MAX_*` env vars). Raw Gateway SSE is
still available at `GET /api/agent/v1/jobs/{id}/stream` for custom clients.

`submit_ai_optimize` requires `confirm_llm_usage=true` in MCP to acknowledge
LLM quota consumption.

Every Gateway call shows up under **Agent Tokens → Audit log** with route,
scope class, status code, and duration.

---

## Using QuantDinger as a *coding* agent context

If you're editing this repo with Cursor / Claude Code / Codex, the repo also
ships a Cursor Skill at
[`.cursor/skills/quantdinger-agent-workflow/SKILL.md`](../../.cursor/skills/quantdinger-agent-workflow/SKILL.md)
that explains the Agent Gateway internals, red lines (no real keys, paper-only
by default), and where to verify changes. Read
[`AGENT_ENVIRONMENT_DESIGN.md`](AGENT_ENVIRONMENT_DESIGN.md) for the full
layered-contracts model.

---

## Deeper references

- [AI Integration design](AI_INTEGRATION_DESIGN.md) — the layered-contracts
  model and how external AI agents consume the Gateway.
- [Quickstart with `curl`](AGENT_QUICKSTART.md) — language-agnostic
  walkthrough that issues a token, calls a few endpoints, runs a paper trade.
- [OpenAPI 3.0 spec](agent-openapi.json) — machine-readable contract for
  `/api/agent/v1`.
- [MCP server README](../../mcp_server/README.md) — installation, env vars,
  and developer notes for the `quantdinger-mcp` package itself.
