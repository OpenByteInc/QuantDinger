# QuantDinger — AI / Agent Integration (Design)

| Item | Value |
|------|--------|
| Status | Draft (for review and phased rollout) |
| Audience | Maintainers; integrators wiring external AI agents into QuantDinger |
| Depends on | [AGENT_ENVIRONMENT_DESIGN.md](AGENT_ENVIRONMENT_DESIGN.md) — three-layer contracts |
| Repository | [QuantDinger](https://github.com/brokermr810/QuantDinger) |

> Companion to the multi-agent runtime design. That doc explains how *coding* agents work **inside the repo**. **This doc** explains how external and embedded **AI agents** consume QuantDinger as a **product** — research, strategy, backtest, and (carefully) execution.

---

## 1. Goals and non-goals

### 1.1 Goals

1. Treat **AI agents as first-class API consumers**, alongside the existing human web UI and in-product AI chat.
2. Provide a **stable, documented capability surface** so the same agent can do research, backtest, and supervised execution without screen-scraping the UI.
3. Enforce **least privilege, auditability, and kill-switches** before any money-adjacent automation is allowed.
4. Allow **multiple front doors** (HTTP / MCP / event stream) without forking business logic.

### 1.2 Non-goals (this phase)

- Not a marketplace of third-party plugins.
- No fully unattended live trading by an external LLM out-of-the-box. Live order routing requires explicit per-tenant opt-in and a documented allowlist.
- Not replacing the in-product AI chat (`ai_chat`) — this design is what that chat (and external agents) will call **underneath**.

---

## 2. Personas

| Persona | Example | Primary needs |
|---------|---------|---------------|
| **P1 Human trader** (existing) | QuantDinger user in browser | UI + REST + JWT session |
| **P2 In-product AI assistant** (existing) | `ai_chat` route, code-gen helpers | Same backend services, on behalf of a logged-in user |
| **P3 External coding agent** | Cursor / Claude Code / Codex working *in the repo* | Repository contracts (covered by `AGENT_ENVIRONMENT_DESIGN.md`) |
| **P4 External AI agent / app** *(new)* | Custom LLM workflow, MCP client, third-party automation | **Authenticated, scoped** access to research / backtest / (optional) trading |
| **P5 Autonomous strategy AI** *(new, gated)* | Closed-loop generator → backtest → score → propose | Programmatic strategy CRUD, batch backtests, structured experiment results |

This document focuses on **P4 + P5**, keeping consistency with P1/P2.

---

## 3. Capability catalog

Capabilities are grouped by **risk class**. Every endpoint or MCP tool must declare exactly one class.

| Class | Examples | Default for new tokens |
|-------|----------|-------------------------|
| **R — Read** | Market data, klines, indicators, strategy listing, backtest results, account read | Allowed |
| **W — Workspace write** | Create/update **strategy code**, save indicator code, save experiment configs | Allowed (workspace-scoped) |
| **B — Backtest / simulation** | Run backtest, run experiment pipeline (regime → variants → score) | Allowed |
| **N — Notifications & misc side-effects** | Send test notification, write user prefs | Allowed (rate-limited) |
| **C — Credentials** | Store/rotate exchange or LLM credentials | **Denied** by default; admin-only |
| **T — Trading / capital** | Quick trade, place/cancel order, adjust live strategy live capital | **Denied** by default; per-tenant explicit opt-in + allowlist |

**Rule:** A new agent token **cannot** acquire class `C` or `T` without an explicit operator action. Class `T` further requires a configured **paper / sandbox** path before it can be flipped to live.

Capability set is sourced from existing route groups: `market`, `kline`, `indicator`, `backtest`, `strategy`, `experiment`, `portfolio`, `dashboard`, `quick_trade`, `ibkr`, `mt5`, `polymarket`, `credentials`, `settings`, `community`, `fast_analysis`, `ai_chat`, `health`. New code should not add a sixth way to do trading; it should expose existing services with proper class tags.

---

## 4. Architecture

### 4.1 Layered view

```
                         ┌───────────────────────────────┐
                         │      External AI agents       │  P4 / P5
                         │  (LLM apps, MCP clients, ...) │
                         └──────────────┬────────────────┘
                                        │  HTTPS + Agent token
                                        ▼
┌─────────────────────────────┐   ┌────────────────────────────┐
│  Browser UI (existing)      │   │  Agent Gateway (NEW)       │
│  /api/...  + JWT session    │   │  /api/agent/v1/...         │
└──────────────┬──────────────┘   │  • token auth + scopes     │
               │                  │  • rate limit + audit log  │
               │                  │  • idempotency-key support │
               │                  └──────────────┬─────────────┘
               │                                 │
               ▼                                 ▼
        ┌───────────────────────────────────────────────┐
        │     Existing service layer (single source)    │
        │  market / strategies / backtest / experiment  │
        │  portfolio / quick_trade / credentials / ...  │
        └───────────────────────┬───────────────────────┘
                                │
                                ▼
              ┌───────────────────────────────────┐
              │  Postgres • Redis • Workers       │
              └───────────────────────────────────┘

                Optional, additive:
                ┌────────────────────────────────────┐
                │  MCP server (read-mostly subset)   │  --> Cursor / Claude-style clients
                │  thin wrapper over /api/agent/v1   │
                └────────────────────────────────────┘
```

Key decision: **the Agent Gateway is a thin layer**, not a parallel implementation. It reuses the same Flask services; it adds **identity, scopes, rate limits, idempotency, and a stable URL/version**.

### 4.2 URL and versioning convention

- **`/api/agent/v1/...`** — agent-facing namespace, stable contract.
- The browser UI keeps using `/api/...` as today; it may continue to evolve more freely.
- Breaking changes to the agent surface bump to `/v2`; `/v1` is supported for a defined window.

### 4.3 Identity model

- A **Tenant** is the existing QuantDinger user (single-user or multi-user mode).
- An **Agent token** belongs to a Tenant and carries:
  - `agent_id` (human-readable label, e.g. `cursor-mcp`, `strategy-bot-1`)
  - `scopes` (subset of capability classes from §3)
  - `markets` allowlist (e.g. `crypto`, `ibkr`, `mt5`)
  - `instruments` allowlist (optional, for trading scope)
  - `expires_at`
  - `paper_only` flag (default true for any token with `T`)
- Tokens are **prefixed and hashed at rest** (e.g. `qd_agent_xxx`); only the prefix is shown in audit logs.
- Existing JWT user sessions are **not** valid for `/api/agent/v1` and vice versa — separate auth pipelines, no accidental cross-use.

---

## 5. Endpoint shape (illustrative)

These are *contract sketches*, not committed routes. Final names follow `AGENT_ENVIRONMENT_DESIGN.md` Layer 3 conventions.

```
GET    /api/agent/v1/health                         class R
GET    /api/agent/v1/markets                        class R
GET    /api/agent/v1/markets/{market}/symbols       class R
GET    /api/agent/v1/klines                         class R
POST   /api/agent/v1/indicators/run                 class R (compute only)

GET    /api/agent/v1/strategies                     class R
POST   /api/agent/v1/strategies                     class W
PATCH  /api/agent/v1/strategies/{id}                class W

POST   /api/agent/v1/backtests                      class B  (async, returns job_id)
GET    /api/agent/v1/backtests/{job_id}             class R

POST   /api/agent/v1/experiments/regime/detect      class B
POST   /api/agent/v1/experiments/pipeline/run       class B
GET    /api/agent/v1/experiments/{job_id}           class R

GET    /api/agent/v1/portfolio                      class R
POST   /api/agent/v1/quick-trade/orders             class T  (paper_only honored)
DELETE /api/agent/v1/quick-trade/orders/{id}        class T
```

### 5.1 Cross-cutting requirements

- **`Idempotency-Key` header** required for class `W`, `B`, `T`. Server stores the key → response for a window (e.g. 24h) keyed by `(agent_id, route, key)`.
- **Async job pattern** for backtests and experiment pipelines to avoid long-lived HTTP and let LLMs poll.
- **Pagination** is explicit (`?limit=&cursor=`); no implicit caps.
- **Errors** follow a single envelope (`code`, `message`, `details`, `retriable`).
- **`X-RateLimit-*`** headers always returned.

---

## 6. Optional MCP layer

When integrators want **tool-style** rather than REST:

- Ship an **MCP server** that wraps a curated subset of `/api/agent/v1` (start with class `R` and `B`).
- The MCP server reads an **agent token** from its own config; it never asks the model for credentials.
- Tool descriptions explicitly state risk class and scope (e.g. `run_backtest (B, paper)`).

MCP is **additive**: REST stays the source of truth, MCP only re-shapes it for clients that prefer the protocol (Cursor, Claude-style, future tools).

---

## 7. Safety, audit, and ops

### 7.1 Trading safety (class T)

- Tokens default to `paper_only=true`. Real-money flip requires:
  1. Operator confirmation in the UI.
  2. A documented **allowlist** of instruments and max notional per order / per day.
  3. A **kill switch** that revokes all `T` tokens with one click and cancels open agent-originated orders.
- The Agent Gateway tags every order with `source=agent:<agent_id>` so portfolio, audit, and kill-switch logic can scope by agent.

### 7.2 Audit log

- One append-only log per tenant: `(ts, agent_id, route, scope_class, status, idempotency_key, redacted_request_summary)`.
- Stored alongside existing user activity; viewable per agent and per class in admin UI.
- Class `T` writes additionally include `(market, instrument, side, qty, est_notional, paper_or_live)`.

### 7.3 Rate limits and quotas

- Per-token: requests/min and concurrent jobs (backtest / experiment) caps.
- Per-tenant: aggregate cap across all that tenant’s tokens.
- LLM-cost-bearing endpoints (e.g. anything proxying to `LLM_PROVIDER`) carry their **own** quota and are denied for tokens without an explicit `ai-llm` sub-scope.

### 7.4 Secrets and credentials

- Class `C` is admin-only; the Agent Gateway must never accept exchange API keys in request bodies for non-admin tokens.
- Existing encryption-at-rest (`SECRET_KEY` → Fernet for `qd_exchange_credentials.encrypted_config`) stays unchanged.

### 7.5 Multi-tenancy

- All queries through the Agent Gateway are forced through tenant-scoped service calls (no cross-tenant data leakage even on internal bugs).
- Test plan: an integration test that issues a token for tenant A and asserts every class-R route returns 404/403 for tenant B objects.

---

## 8. Deployment topologies (self-hosted vs SaaS)

QuantDinger ships as a **single backend** that intentionally supports two operational topologies. The Agent Gateway code is identical in both; the differences are entirely operator-controlled environment variables and where the database lives.

### 8.1 Topologies

| Dimension | **Self-hosted** (default) | **SaaS / shared / hosted** |
|-----------|---------------------------|----------------------------|
| Selector env var | `QUANTDINGER_DEPLOYMENT_MODE` unset (or `self`/`local`) | `QUANTDINGER_DEPLOYMENT_MODE=saas` (also `hosted`/`shared`/`multitenant`) |
| Tenants per instance | 1 (the operator) | N (one per signup) |
| Token issuance | Operator decides every field | Server forces `paper_only=true`; **T-scope rejected at issuance with 403** |
| Live trading (`AGENT_LIVE_TRADING_ENABLED`) | Operator may flip to `true` | **Must stay `false`** — the SaaS guard makes T impossible to obtain anyway |
| Exchange credentials | Operator may store + use them | Recommended: do not accept; if you do, encrypt-at-rest and never expose via Agent Gateway (class C is admin-only) |
| Rate limits | `rate_limit_per_min` per token, no global cap | Per-token + a per-tenant + per-IP outer cap (recommended; outside this code) |
| Audit visibility | Operator | SaaS operator (you) sees everyone; tenant admins see only their own (already enforced by `user_id` filter in `/admin/audit`) |
| MCP `BASE_URL` | `http://localhost:8888` (or LAN URL) | `https://ai.quantdinger.com` (or your hosted URL) |

### 8.2 The hosted-mode guard (V3.1.0+)

When `QUANTDINGER_DEPLOYMENT_MODE` is one of `saas` / `hosted` / `shared` / `multitenant` / `multi-tenant`, the `POST /admin/tokens` route applies two **belt + suspenders** safeguards (`app/routes/agent_v1/admin.py`):

1. **Loud rejection of T-scope** — any payload that includes `T` in `scopes` returns `403` with a clear message, instead of silently downgrading the scope set. This makes the constraint visible to integrators rather than mysteriously stripping their request.
2. **Forced `paper_only=true`** — even if T somehow re-entered the scope set later, the token row is written with `paper_only=true`, so `quick-trade` would still record paper orders only.

These guards run at **issuance time**, so a hosted instance never has an at-rest token capable of routing real-money trades. The guards are independent of `AGENT_LIVE_TRADING_ENABLED` (which gates execution); together they make hosted-mode live trading impossible to achieve through any combination of misconfiguration.

Tested by `tests/test_agent_v1_saas_guard.py` (13 cases: env-var spelling, T-scope rejection, paper-only force-pin, self-hosted regression).

### 8.3 Recommended outer hardening for SaaS

Beyond the in-process guard, a hosted deployment should add at the proxy / infra layer:

- **HTTPS-only** with HSTS; no plaintext Agent token traffic.
- **Per-tenant + per-IP rate limit** in front of the app (e.g. nginx `limit_req_zone` keyed on `Authorization`), in addition to the in-process per-token cap.
- **CORS**: `/api/agent/v1/*` should not be exposed to browser CORS — agents call from servers / IDE subprocess / native code, not from web pages.
- **Quota / billing hook**: subclass `agent_jobs.submit_job` (or wrap it in your billing middleware) so kinds in `{ai_optimize, ai_pipeline, structured_tune}` deduct credits the same way the human AI surfaces do.
- **Token reveal hygiene**: full token shown once in the Vue admin UI, never logged, never echoed back from `/admin/tokens` GET. Already enforced.

### 8.4 Migration between topologies

Switching a running deployment from self-hosted to SaaS is non-destructive:

```bash
# Add to the env file used by docker-compose
QUANTDINGER_DEPLOYMENT_MODE=saas
docker compose up -d backend
```

After restart:
- Existing T-scope tokens **continue to work** (the guard runs at issuance, not on each request) — the operator should `DELETE /admin/tokens/{id}` for any token they no longer want active under SaaS rules. A future enhancement may add a one-shot "revoke all T tokens on mode change" startup task.
- New issuances follow SaaS rules immediately.

### 8.5 Why a single binary serves both

We deliberately did **not** fork SaaS into a separate codebase:

- **Less drift**: every bug fix and feature ships to both topologies on the same release.
- **Self-host parity**: enterprise/private users get bit-for-bit identical Agent Gateway behavior to the hosted demo, so trust transfers.
- **Test surface**: the `_is_saas_mode()` branch is a single env-var check, easy to cover (and is, in `test_agent_v1_saas_guard.py`).

---

## 9. Mapping to existing code

| Concern | Existing code | Reuse strategy |
|---------|----------------|----------------|
| User auth (JWT) | `app/routes/auth.py`, `app/utils/auth.py` | Keep. Agent tokens live in a parallel module (`app/utils/agent_auth.py` proposed). |
| Trading | `quick_trade`, `ibkr`, `mt5`, `polymarket` | Wrap in `T`-class endpoints; reuse service layer; do **not** fork order logic. |
| Backtest / experiment | `backtest`, `experiment`, `app/services/experiment/*` | Move long-running entrypoints behind an async job table; agent endpoints become thin polls. |
| AI chat / code-gen | `ai_chat` | Refactor to call internal services; agent endpoints expose the same services without the chat shell. |
| Health | `health` | Reuse for `/api/agent/v1/health`. |

No new Python packages are required for the gateway itself; storage uses existing Postgres (new tables: `qd_agent_tokens`, `qd_agent_jobs`, `qd_agent_audit`).

---

## 10. Phased roadmap

| Phase | Deliverable | Risk class enabled | Human action required |
|-------|-------------|--------------------|------------------------|
| **A0** | Spec freeze: this doc + endpoint table + scope schema | — | Review and merge |
| **A1** | Agent token issuance + `/api/agent/v1/health`, `markets`, `symbols`, `klines`, `indicators/run` | R | Issue first token in admin UI |
| **A2** | Strategies CRUD + backtest async jobs + audit log v1 | R, W, B | Per-tenant opt-in for W |
| **A3** | Experiment pipeline endpoints + per-token rate limits | R, W, B | — |
| **A4** | Optional MCP server wrapping A1–A3 | R, B (then W) | Configure MCP client |
| **A5** | Trading endpoints in **paper-only** mode + per-agent kill switch | T (paper) | Explicit per-token opt-in |
| **A6** | Live trading promotion path: instrument allowlist, notional caps, dual-control toggle | T (live) | Operator dual confirmation |

A1–A4 are **safe to ship without trading exposure**. A5/A6 are gated and reversible.

---

## 11. Open questions

1. **Token storage location** — share `qd_users` table family vs new schema namespace?
2. **Job runner** — reuse existing worker toggles (`ENABLE_PENDING_ORDER_WORKER`, etc.) or introduce a dedicated `agent-jobs` worker? Prefer the latter for blast-radius isolation.
3. **OpenAPI generation** — auto-derive from Flask blueprints or hand-maintain a single `agent-openapi.json` checked into `docs/agent/`?
4. **MCP transport** — stdio first (simplest for desktop IDEs), HTTP later for cloud agents.
5. **Cost passthrough** — when class `B` triggers LLM use indirectly (e.g. NL→code helpers), should the response include token-cost telemetry?

---

## 12. Implementation status

| Area | Status | Where it lives |
|------|--------|----------------|
| Schema (tokens / jobs / audit / paper-orders) | Shipped | `backend_api_python/migrations/init.sql` (section 30) + runtime ensure in `app/utils/agent_auth.py` |
| Token auth + scopes + rate limit + audit | Shipped | `app/utils/agent_auth.py` |
| Async job runner | Shipped | `app/utils/agent_jobs.py` |
| Read endpoints (R) | Shipped | `app/routes/agent_v1/{health,markets,strategies,jobs,portfolio}.py` |
| Workspace endpoints (W) | Shipped | `app/routes/agent_v1/strategies.py` |
| Backtest + experiment endpoints (B) | Shipped | `app/routes/agent_v1/{backtests,experiments}.py` |
| Trading endpoints (T) — paper-only | Shipped | `app/routes/agent_v1/quick_trade.py` (`AGENT_LIVE_TRADING_ENABLED` kill switch) |
| Admin token CRUD + audit viewer | Shipped | `app/routes/agent_v1/admin.py` |
| OpenAPI 3.0 spec | Shipped | `docs/agent/agent-openapi.json` |
| MCP server (Python) | Shipped | `mcp_server/` — `stdio` (default), `sse`, and `streamable-http` transports via `QUANTDINGER_MCP_TRANSPORT` |
| Operator quickstart | Shipped | `docs/agent/AGENT_QUICKSTART.md` |
| Job progress streaming (SSE) | Shipped | `GET /api/agent/v1/jobs/{id}/stream` — `snapshot` / `progress` / `ping` / `result` frames; resume via `?since=` or `Last-Event-ID` |
| Token UI (Profile + admin audit) | Shipped | `ProfileAgentTokens.vue` at Profile → My Agent Token (`/api/agent/v1/me/tokens`); admin route `/agent-tokens` retained |
| Hosted-mode hardening (`QUANTDINGER_DEPLOYMENT_MODE=saas`) | Shipped | `app/routes/agent_v1/admin.py` — issuance-time T-scope rejection + `paper_only` force-pin; covered by `tests/test_agent_v1_saas_guard.py` |
| Published MCP package on PyPI | Shipped | [`quantdinger-mcp`](https://pypi.org/project/quantdinger-mcp/) — install via `pipx`, `uvx`, or `pip` |
| Live execution implementation (T, self-host only) | Pending | tracked under roadmap A6 |

## 13. Revision history

| Version | Date | Notes |
|---------|------|--------|
| 0.1 | 2026-05-02 | First draft: personas, capability classes, gateway, MCP, safety, roadmap |
| 0.2 | 2026-05-02 | A0–A5 implemented (schema, auth, R/W/B + paper-only T, admin, MCP, tests, OpenAPI, quickstart) |
| 0.3 | 2026-05-02 | Added: SSE progress streaming for jobs, MCP HTTP/SSE transport, Vue admin UI for token & audit management |
| 0.4 | 2026-05-02 | Added §8 Deployment topologies; shipped hosted-mode guard (`QUANTDINGER_DEPLOYMENT_MODE=saas` → T-scope rejected, `paper_only` pinned); MCP package published to PyPI; README EN/CN now documents the SaaS vs self-host paths side-by-side |
