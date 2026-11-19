# QuantDinger Backend Architecture

This document is the practical map for contributors and AI coding agents. It
explains where code belongs today and the target direction for gradual cleanup.
It complements, but does not replace, `MODULE_BOUNDARIES.md`,
`CONCURRENCY_MODEL.md`, and `API_CONVENTIONS.md`.

## Goals

- Keep the project easy to understand for open-source contributors.
- Preserve existing API paths and runtime behavior while refactoring.
- Put new code behind clear boundaries instead of growing large legacy files.
- Make AI-assisted changes safer by giving agents explicit ownership rules.

## Runtime Surfaces

| Surface | Path | Purpose |
| --- | --- | --- |
| Human Web API | `backend_api_python/app/routes`, `app/openapi` | Web and mobile UI endpoints under `/api/...` |
| Agent Gateway | `backend_api_python/app/routes/agent_v1` | Scoped agent/MCP API under `/api/agent/v1/...` |
| Strategy Runtime | `backend_api_python/app/services/trading_executor.py` and related services | Strategy loops, signal handling, pending order generation |
| Untrusted Code Boundary | `backend_api_python/app/services/strategy_v2/sandbox.py`, `app/utils/safe_exec_worker.py`, `app/utils/strategy_runtime_worker.py` | Credential-free indicator, discovery, backtest, and live callback execution |
| Market Data | `backend_api_python/app/data_sources`, `app/data_providers` | K-line, quote, symbol, fundamentals, macro and news data |
| Live Trading | `backend_api_python/app/services/live_trading` | Exchange and broker REST adapters |
| Background Workers | `backend_api_python/app/startup.py`, worker services | Pending orders, portfolio monitor, grid fill poller, USDT watchers |

## Current Directory Ownership

| Directory | Owns | Do not put here |
| --- | --- | --- |
| `app/routes` | HTTP request parsing, auth checks, response shape | Exchange-specific trading rules, long loops, large DB workflows |
| `app/openapi` | OpenAPI export, route registration, tag metadata | Business behavior |
| `app/services` | Use-case orchestration and domain workflows | Flask `request`/`g` objects except at route boundary |
| `app/services/live_trading` | Broker/exchange adapters and normalized order APIs | Strategy lifecycle or frontend response formatting |
| `app/data_sources` | Raw market data adapters | Order placement or strategy state mutation |
| `app/data_providers` | Aggregated market/global data providers | Trading decisions |
| `app/utils` | Small reusable infrastructure helpers | Feature workflows |
| `app/config` | Environment-backed configuration | Runtime side effects |
| `migrations` | Schema and seed data | Python runtime behavior |

## Target Backend Shape

New code should move toward this domain layout even if legacy files still exist:

```text
app/
  routes/                 # HTTP facades kept thin
  openapi/                # API documentation/export metadata
  services/
    quick_trade/          # manual trade order, balance, position, and error workflows
    trading/              # order intents, risk guards, execution state
    backtest/             # data loading, simulation, metrics
    ai/                   # skills, prompts, provider orchestration
    account/              # account snapshots, positions, portfolio read models
    billing/              # credits, plans, USDT order lifecycle
  integrations/
    exchanges/            # future home for exchange adapters
    brokers/              # future home for broker adapters
  data_sources/           # current market-data adapters
  utils/                  # small low-level helpers only
```

Do not move everything at once. Add a new module only when it removes real
complexity from an active change.

Prefer feature packages over flat service files. For example, use
`app/services/quick_trade/errors.py` instead of `app/services/quick_trade_errors.py`
when the feature is expected to grow into orders, balances, positions, and
history modules.

## Route Rules

- A route should usually fit on one screen.
- A route may validate input, call a service, and map service results to HTTP.
- A route must not start background threads.
- A route must not contain exchange-specific sizing, precision, or retry logic.
- A route must not perform long multi-step transactions inline.
- Streaming routes must define cancellation, heartbeat, timeout, and billing behavior.

When a route starts to need helpers, move the helpers to a service module first.

## Service Rules

- Services accept plain Python values and return plain dicts, dataclasses, or typed results.
- Services own use-case flow, idempotency, transaction boundaries, and retries.
- Services should not import Flask `request`, `g`, or `jsonify`.
- Services that mutate state must document idempotency and failure behavior.
- Long-running work should be callable from a worker without HTTP context.
- User-authored indicator and strategy code must execute through the isolated
  worker boundary. Trusted services may inspect manifests, prepare bounded
  snapshots, validate returned intents, and persist state, but must not load or
  invoke user handlers in the API, evaluator, backtest, or trading process.
- Feature services should live in packages such as `services/quick_trade/` when
  the domain has more than one workflow.

## Adapter Rules

- Adapters normalize third-party APIs into QuantDinger contracts.
- Adapters should not know about users, JWTs, Flask, or frontend wording.
- Exchange-specific error handling stays near the adapter.
- Business services decide whether an adapter error is retryable or user-facing.

## Data Rules

- Market data adapters return normalized OHLCV/quote rows.
- Symbol search and master data should prefer database-backed sources.
- Cache keys must include market, exchange, market type, symbol, timeframe, and limit when those values affect results.
- Do not mix live order account state with market data fetchers.

## File Size Guidance

These are soft limits for new work:

| File Type | Preferred Limit | Action Above Limit |
| --- | ---: | --- |
| Route module | 400 lines | Split by endpoint family |
| Service module | 800 lines | Extract workflow components |
| Adapter module | 900 lines | Split public data, account, order, fill logic |
| Utility module | 300 lines | Reconsider ownership |

Legacy files exceed these limits. Do not add unrelated behavior to them; extract
a small module when touching the area.

## High-Risk Legacy Files

Treat these as refactor targets, not places to keep adding features:

- `app/services/trading_executor.py`
- `app/services/backtest.py`
- `app/routes/ai_chat.py`
- `app/routes/settings.py`
- `app/routes/quick_trade.py`
- `app/routes/user.py`
- `app/services/pending_order_worker.py`
- `app/services/fast_analysis.py`

## Language and Comment Policy

- Code comments, docstrings, logs, module names, and internal errors should be English.
- Chinese is allowed for localized UI text, prompt examples, translation maps, exchange column names, and backward-compatible response content.
- When touching legacy Chinese comments, translate them without changing behavior.
- Do not mix wording cleanup with risky logic refactors in the same patch.

## Safe Refactor Workflow

1. Identify the existing route/service behavior.
2. Add or update a small test if behavior is risky.
3. Extract pure helpers or service functions first.
4. Keep API paths, response fields, and import compatibility.
5. Regenerate OpenAPI when API metadata changes.
6. Run focused checks and document any skipped verification.

## Required Reading Before Larger Changes

- `docs/architecture/MODULE_BOUNDARIES.md`
- `docs/architecture/CONCURRENCY_MODEL.md`
- `docs/architecture/API_CONVENTIONS.md`
- `docs/architecture/EXTENSION_GUIDE.md`
- `docs/REFACTOR_PLAN_4_0.md`
