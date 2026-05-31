---
name: quantdinger-agent-workflow
description: >-
  QuantDinger repo workflow for coding agents: layered contracts, safety
  boundaries, and where backend, strategies, and Docker live. Use when editing
  Python API, strategies, deployment, or docs/agent.
---

# QuantDinger — agent workflow

## When this applies

Use this skill whenever you change code or docs under this repository as a **coding agent** (Cursor, Claude Code, Codex, or similar), especially:

- `backend_api_python/` (Flask API, services, routes)
- Strategy / backtest / trading-adjacent logic
- `docker-compose.yml`, `scripts/`, `env.example`
- `docs/agent/` (keep **English only**)

## Read first

1. **`docs/agent/AGENT_ENVIRONMENT_DESIGN.md`** — SSOT for three layers: documentation contract → command contract → optional HTTP/MCP.
2. **`docs/agent/AI_INTEGRATION_DESIGN.md`** — How external AI agents consume QuantDinger as a product (Agent Gateway, scopes, MCP, trading safety). Read this **before** adding any new endpoint or tool that an AI agent might call.
3. **`docs/agent/AGENT_QUICKSTART.md`** — Operator/integrator walkthrough; mirrors the implemented `/api/agent/v1` surface.
4. **`docs/agent/agent-openapi.json`** — Machine-readable contract; update it whenever you add or change an `/api/agent/v1/...` route.
5. **`docs/agent/README.md`** — Index of agent-facing docs.

## Implemented surface (truth)

The Agent Gateway is mounted at **`/api/agent/v1`** by `app/routes/agent_v1/`.

- Auth: `app/utils/agent_auth.py` (`@agent_required(scope=...)`). Tokens are
  hashed at rest in `qd_agent_tokens`; never log or persist the raw token.
- Async jobs: `app/utils/agent_jobs.py` writes to `qd_agent_jobs`; backtests
  and experiment pipelines submit here and clients poll `/jobs/{id}` or
  subscribe to **`GET /jobs/{id}/stream`** (SSE: `snapshot` / `progress` /
  `ping` / `result`). Long-running runners can opt-in by adding a second
  positional `on_progress` parameter — `submit_job` will detect it and pipe
  events to live SSE subscribers AND persist the latest snapshot.
- Audit: every call (success **and** denial) is appended to `qd_agent_audit`.
- Trading: `quick_trade.py` enforces paper-only by default; live execution
  requires both `paper_only=false` on the token AND env
  `AGENT_LIVE_TRADING_ENABLED=true`. Do not weaken this without explicit ask.
- MCP: `mcp_server/` is a thin Python wrapper over R + W + B endpoints (no
  trading), with three transports selected by `QUANTDINGER_MCP_TRANSPORT`: `stdio` (default,
  desktop IDEs), `sse`, and `streamable-http` (cloud agents / remote IDEs;
  also bind `QUANTDINGER_MCP_HOST` / `QUANTDINGER_MCP_PORT`). Add new tools
  there only after exposing the underlying capability via REST.
- Admin UI: the Vue project at `QuantDinger-Vue-src/` ships **Profile → My Agent Token**
  for every logged-in user (`src/views/profile/components/ProfileAgentTokens.vue`,
  API `/api/agent/v1/me/tokens`). Admins retain `/agent-tokens` for audit.
  API client lives in `src/api/agent.js`.

Do not treat the marketing-heavy root `README.md` as the only onboarding doc; use it for user install paths and link out.

## Red lines (non-negotiable)

- Never commit real **secrets**, production **`.env`**, API keys, or DB passwords. Use `env.example` patterns and placeholders in examples.
- Do not add **live trading** or **order placement** automation that bypasses human review unless explicitly requested and scoped.
- Prefer **linking** to `docs/STRATEGY_DEV_GUIDE*.md` over duplicating long strategy guide text inside agent-only docs.

## Repository anchors

| Area | Path |
|------|------|
| Backend | `backend_api_python/` |
| Frontend (prebuilt UI) | `frontend/` |
| Compose stack | `docker-compose.yml`, `scripts/` |
| Strategy guides | `docs/STRATEGY_DEV_GUIDE.md` (and localized variants) |

## Verification

- Backend tests live in `backend_api_python/tests/`; run with `python -m pytest tests/ -q` from `backend_api_python/`.
- Agent Gateway tests: `tests/test_agent_v1.py` (token auth, scopes, rate limit, generator format).
- For human stack changes, follow the Docker Compose flow already documented in the root `README.md` (PowerShell or Bash).

## Language

All **new** agent-facing prose (this skill, `docs/agent/*`) must be **English** so the same material works across locales and tools.
