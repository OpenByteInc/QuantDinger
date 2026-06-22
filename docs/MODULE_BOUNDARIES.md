# QuantDinger Backend Module Boundaries

This document defines target ownership for the backend. It does not require an
immediate rewrite. It is the contract to follow as existing code is decomposed.

## Layer Model

| Layer | Owns | Must Not Own |
| --- | --- | --- |
| `routes` | HTTP parsing, auth checks, status codes, request/response mapping | trading loops, exchange-specific rules, long-running jobs |
| `openapi` | schema registration, OpenAPI export, operation metadata | business logic |
| `services` | business workflows and use-case orchestration | raw Flask request objects except at route boundary |
| `services/live_trading` | exchange and broker adapters, order API normalization | strategy lifecycle, user auth, HTTP responses |
| `services/grid` | grid engine, cell state, fill normalization, reconciliation | route parsing, frontend-specific formatting |
| `data_sources` | market data adapters and fetch policy | strategy execution or account mutation |
| `data_providers` | dashboard/global-market aggregation and cache policy | trading decisions or order placement |
| `utils` | low-level auth, db, cache, logging, time, crypto helpers | feature workflows |
| `config` | environment and settings resolution | runtime mutation side effects |
| `migrations` | schema and seed data | Python runtime behavior |

## Current Hotspots

These files mix multiple responsibilities and should be decomposed gradually:

| File | Current Risk | Target Split |
| --- | --- | --- |
| `app/__init__.py` | app factory plus core Flask wiring | keep app factory only |
| `app/startup.py` | worker boot, strategy restore, process-local singletons | distributed worker ownership guards |
| `app/routes/strategy.py` | lifecycle, templates, AI generation | route modules per subdomain |
| `app/routes/strategy_account_routes.py` | account snapshot and account position mirror | account service facade |
| `app/routes/strategy_backtest_routes.py` | strategy backtest endpoint facade | backtest request schema and service facade |
| `app/routes/strategy_deviation_routes.py` | dry-run deviation endpoint | deviation service facade |
| `app/routes/strategy_grid_routes.py` | grid resting order endpoint | grid route facade |
| `app/routes/strategy_ledger_routes.py` | trades, equity curve, performance endpoints | ledger/read-model service facade |
| `app/routes/strategy_positions_routes.py` | strategy live position read model | position query service facade |
| `app/routes/strategy_review_routes.py` | AI strategy review endpoints | review request schema and service facade |
| `app/routes/strategy_logs_routes.py` | runtime log query endpoint | observability route facade |
| `app/routes/strategy_notifications.py` | strategy notification endpoints | notification service facade |
| `app/routes/quick_trade.py` | HTTP, credential handling, exchange selection, order formatting | route facade plus quick-trade service |
| `app/routes/ai_chat.py` | memory, skills, tools, chat, streaming | separate AI route modules |
| `app/routes/settings.py` | schema, config values, brand, connection testing | settings service plus config schemas |
| `app/services/trading_executor.py` | signal loops, order placement, sync, persistence | executor core, order intents, locks, recorders |
| `app/services/backtest.py` | data loading, strategy execution, simulation, metrics | backtest pipeline components |
| `app/services/pending_order_worker.py` | worker loop plus dispatch and sync details | worker shell, dispatcher, reconciliation |

## Route Layer Rules

- Routes may validate input, call services, and shape HTTP responses.
- Routes must not start background threads directly.
- Routes must not contain exchange-specific order sizing logic.
- Routes must not perform multi-step database transactions inline unless the logic
  is being migrated and covered by tests.
- Streaming routes must define timeout, heartbeat, and cancellation behavior.

## Service Layer Rules

- Services own use-case flow and can coordinate repositories/adapters.
- Services should accept plain Python values, not Flask request objects.
- Services should return plain dicts/dataclasses or typed result objects.
- Services should define idempotency behavior when they mutate state.
- Long-running service work should be runnable as a job or worker task.

## Adapter Rules

- Exchange and broker adapters normalize external APIs into internal contracts.
- Adapters should not know about Flask, users, or frontend response shapes.
- Adapter methods should accept explicit `client_order_id` when the venue supports it.
- Adapter-specific rate limits and retry rules should be isolated from business logic.

## Startup Boundary

Target structure:

- `create_app()` creates Flask, configures JSON/CORS, registers routes.
- `startup.py` owns worker startup and process-local service singletons.
- Worker startup is disabled for OpenAPI export, tests, and one-off scripts.
- Multi-process deployments must have an explicit owner for each background worker.

## Documentation Boundary

- Root `README.md`: user-facing quick start and product overview.
- `docs/`: architecture, API, operations, integration guides.
- `docs/agent/`: agent-facing docs only, English only.
- Generated API files must say how they were generated.
- Deprecated docs must be moved to an archive or marked with a date and replacement.

## Language Boundary

- Code comments, docstrings, log messages, internal error details, module names,
  function names, variables, and engineering documentation should be English by
  default.
- Chinese is allowed only for user-facing localized text, prompts, translation
  dictionaries, examples that intentionally demonstrate Chinese output, exchange
  or market terminology that is inherently Chinese, and backward-compatible API
  fields that already expose localized content.
- Do not mix Chinese comments into core trading, concurrency, API, database, or
  deployment code. If localized text is needed, keep it behind an explicit
  language selector or i18n structure.
- When replacing legacy Chinese comments, preserve the technical meaning and
  avoid changing runtime behavior in the same edit.

## Compatibility Rule

When moving code, keep the old import path or route path until all callers are
verified. If compatibility cannot be preserved, document the break and update
frontend/API tests in the same change.
