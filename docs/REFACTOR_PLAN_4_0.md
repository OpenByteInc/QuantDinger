# QuantDinger 4.0 Codebase Stabilization Plan

This plan is for a cleanup and refactor pass that preserves all 4.0 product features.
The goal is not to remove capabilities; the goal is to make the backend easier to
change safely under future human and AI-assisted development.

## Non-Negotiable Goals

- Keep all existing 4.0 features unless a separate product decision explicitly removes one.
- Preserve public API paths, request fields, and response fields by default.
- Prefer compatibility wrappers over breaking changes.
- Move logic without changing behavior first; behavior changes require focused tests.
- Make high-concurrency behavior explicit for trading, jobs, payments, and background workers.
- Keep new engineering docs and agent-facing docs in English.

## Current Baseline

- Backend framework: Flask with flask-smorest registration for human web APIs.
- Agent API: mounted separately under `/api/agent/v1`.
- Extracted route count from decorators: 260 routes.
- Largest route files:
  - `app/routes/strategy.py`
  - `app/routes/quick_trade.py`
  - `app/routes/ai_chat.py`
  - `app/routes/user.py`
  - `app/routes/settings.py`
- Largest service files:
  - `app/services/trading_executor.py`
  - `app/services/backtest.py`
  - `app/services/fast_analysis.py`
  - `app/services/pending_order_worker.py`
  - `app/services/community_service.py`
- Startup currently performs app creation, JSON/CORS setup, DB bootstrap, worker startup,
  strategy restore, AI calibration, reflection, portfolio monitor, pending-order worker,
  grid poller, and USDT worker bootstrapping.

## Phase 0: Safety Baseline

1. Record current route inventory and module ownership.
2. Export OpenAPI with startup hooks disabled.
3. Run the existing test suite and record known failures before refactors.
4. Run Docker Compose build/start checks for both source-build and GHCR flows.
5. Confirm frontend API calls against current backend route paths.

Exit criteria:

- `docs/API_INVENTORY.md` exists and reflects current route families.
- `docs/MODULE_BOUNDARIES.md` defines ownership and allowed dependencies.
- `docs/CONCURRENCY_MODEL.md` defines lock/idempotency expectations.
- Known test failures are documented instead of rediscovered during refactors.

## Phase 1: Hygiene Without Behavior Changes

Scope:

- Remove committed temporary outputs and test artifacts.
- Fix mojibake comments and move new comments to English.
- Normalize Docker and environment documentation for an international default.
- Keep legacy routes and compatibility names in place.
- Mark questionable code as `legacy` only after confirming callers.

Out of scope:

- Removing features.
- Renaming live API routes.
- Changing database schema semantics.
- Rewriting trading execution logic.

## Phase 2: API Contract Cleanup

Tasks:

- Separate route families into public, private, admin, agent, and internal categories.
- Ensure OpenAPI summaries are short, English, and generated from real routes.
- Add request/response schemas for high-risk endpoints first:
  - strategy start/stop/create/update
  - quick trade place/close
  - backtest submit/history
  - billing and USDT order lifecycle
  - agent jobs and SSE
- Add compatibility notes for legacy route names such as camelCase endpoints.

Exit criteria:

- API docs can be regenerated from code.
- Frontend route usage can be mapped to backend endpoints.
- Agent Gateway spec and human web OpenAPI are clearly separated.

## Phase 3: High-Risk Module Decomposition

Refactor order:

1. `app/routes/strategy.py`: split HTTP handlers by strategy lifecycle, backtest, account view, notifications, and templates.
2. `app/routes/quick_trade.py`: split HTTP handlers from exchange routing, credential loading, sizing, and response formatting.
3. `app/services/trading_executor.py`: extract execution locks, signal evaluation, order intent building, position sync, and trade recording.
4. `app/services/backtest.py`: separate data loading, indicator execution, execution simulation, metrics, and persistence.
5. `app/routes/ai_chat.py` and AI services: separate memory, chat sessions, skills/tools, streaming, and provider calls.

Rules:

- One refactor slice at a time.
- Add or update tests before moving high-risk behavior.
- Keep import compatibility where practical.
- Do not mix formatting-only churn with logic moves.

## Phase 4: Concurrency Hardening

Focus areas:

- Strategy start/stop and restore.
- Live and paper order placement.
- Pending-order dispatch and fill polling.
- Grid order reconciliation.
- USDT payment confirmation.
- Agent job idempotency and SSE progress.
- External data provider single-flight caching and rate limits.

Required patterns:

- Idempotency keys for order-like and job-like operations.
- Strategy/symbol scoped execution locks.
- Database uniqueness for dedupe.
- Retry with dedupe, not retry with duplicate side effects.
- Worker startup guards for multi-process deployments.
- Explicit Redis/database ownership for shared state.

## Phase 5: Test and CI Stabilization

Minimum test families:

- Route smoke tests for public/private/admin/agent APIs.
- OpenAPI export test.
- Strategy lifecycle tests.
- Order idempotency and concurrent placement tests.
- Pending-order worker recovery tests.
- USDT order idempotency tests.
- Agent job replay and SSE tests.
- Docker build sanity checks.

Optional later tools:

- `ruff` for linting and import hygiene.
- `pytest-xdist` for concurrency-sensitive tests after shared-state tests are stable.
- `locust` or `k6` for endpoint load tests.

## Working Rule For Future AI Changes

Before changing code in a high-risk module, update or consult:

- `docs/MODULE_BOUNDARIES.md`
- `docs/CONCURRENCY_MODEL.md`
- `docs/API_INVENTORY.md`

If a change crosses module boundaries, document the contract being changed and add
a regression test that proves the old behavior remains compatible.
