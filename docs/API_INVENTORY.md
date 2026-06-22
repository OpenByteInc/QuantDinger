# QuantDinger API Inventory

This inventory is a static snapshot extracted from Flask route decorators on the
4.0 branch. It is a refactor guide, not a replacement for generated OpenAPI.

## Summary

- Total extracted routes: 260
- Human web APIs are registered through `app.openapi.register.register_human_blueprints`.
- Agent APIs are mounted separately under `/api/agent/v1`.
- Human OpenAPI and Agent OpenAPI are separate contracts.

## Route Families

| Route file | Route count | Main responsibility | Refactor risk |
| --- | ---: | --- | --- |
| `app/routes/strategy.py` | 16 | strategy lifecycle, templates, AI generation | very high |
| `app/routes/strategy_account_routes.py` | 2 | account snapshot and account position mirror | medium |
| `app/routes/strategy_backtest_routes.py` | 3 | strategy backtest run/history/detail facade | high |
| `app/routes/strategy_deviation_routes.py` | 1 | dry-run deviation report facade | medium |
| `app/routes/strategy_grid_routes.py` | 1 | grid resting order query facade | medium |
| `app/routes/strategy_ledger_routes.py` | 3 | strategy trades, equity curve, performance | high |
| `app/routes/strategy_positions_routes.py` | 1 | strategy live position read model | high |
| `app/routes/strategy_review_routes.py` | 2 | AI strategy review report generation/history | medium |
| `app/routes/strategy_logs_routes.py` | 1 | strategy runtime log query | medium |
| `app/routes/strategy_notifications.py` | 5 | strategy notification list/count/read/clear | medium |
| `app/routes/user.py` | 29 | admin users, profile, credits, notification settings, system strategies | high |
| `app/routes/ai_chat.py` | 18 | AI skills/tools, memory, chat sessions, streaming | high |
| `app/routes/community.py` | 18 | indicator marketplace and admin review | medium |
| `app/routes/portfolio.py` | 16 | positions, monitors, alerts, groups | medium |
| `app/routes/auth.py` | 13 | login/register/reset/OAuth/session info | high |
| `app/routes/mt5.py` | 11 | MT5 connection/account/order/quote | medium |
| `app/routes/market.py` | 10 | market config, symbols, watchlist, prices | medium |
| `app/routes/alpaca.py` | 9 | Alpaca connection/account/order/quote | medium |
| `app/routes/ibkr.py` | 9 | IBKR connection/account/order/quote | medium |
| `app/routes/global_market.py` | 8 | overview, heatmap, news, calendar, sentiment, refresh | medium |
| `app/routes/credentials.py` | 7 | exchange/broker credential CRUD | high |
| `app/routes/fast_analysis.py` | 7 | analysis, history, feedback, performance | medium |
| `app/routes/settings.py` | 7 | settings schema, values, brand/public config, connection test | high |
| `app/routes/indicator.py` | 6 | indicators, AI generation, code quality hints | high |
| `app/routes/quick_trade.py` | 5 | place order, balance, position, close position, history | very high |
| `app/routes/backtest.py` | 4 | indicator backtest API | high |
| `app/routes/billing.py` | 4 | plans and USDT order APIs | high |
| `app/routes/dashboard.py` | 3 | dashboard summary and pending orders | medium |
| `app/routes/experiment.py` | 2 | AI optimize and structured tune | high |
| `app/routes/kline.py` | 1 | K-line data | medium |
| `app/routes/policy.py` | 1 | broker-market policy | low |

## Agent Route Families

| Route file | Route count | Main responsibility |
| --- | ---: | --- |
| `app/routes/agent_v1/indicators.py` | 6 | agent indicator CRUD/validation/linking |
| `app/routes/agent_v1/me_tokens.py` | 5 | self-service agent tokens and audit |
| `app/routes/agent_v1/admin.py` | 4 | admin token and audit management |
| `app/routes/agent_v1/experiments.py` | 4 | async experiment jobs |
| `app/routes/agent_v1/markets.py` | 4 | markets, symbols, klines, price |
| `app/routes/agent_v1/strategies.py` | 4 | agent strategy list/get/create/update |
| `app/routes/agent_v1/jobs.py` | 3 | job list/get/SSE |
| `app/routes/agent_v1/health.py` | 2 | health and whoami |
| `app/routes/agent_v1/portfolio.py` | 2 | positions and paper orders |
| `app/routes/agent_v1/quick_trade.py` | 2 | agent order placement and kill switch |
| `app/routes/agent_v1/backtests.py` | 1 | async backtest submission |

## Stability Classes

### Public

Endpoints that can be used before login or by broad clients:

- health endpoints
- market config and symbols
- broker-market policy
- auth security config, login, register, reset
- selected global market data endpoints

### Private

Endpoints requiring authenticated user context:

- strategies
- quick trade
- credentials
- portfolio
- fast analysis history
- user profile
- billing order creation
- AI chat and memory

### Admin

Endpoints requiring admin permission:

- user management
- community review/admin actions
- agent token admin
- settings mutation where applicable
- system strategy toggles

### Agent

Endpoints under `/api/agent/v1`; these use token scopes, audit logging, and
idempotency helpers. They are a separate integration contract from the browser UI.

## API Refactor Rules

- Do not rename or remove existing route paths during internal decomposition.
- If a route is moved to a new file, keep the same blueprint registration prefix.
- If a new route supersedes an old one, keep the old route as a compatibility wrapper.
- OpenAPI operation summaries should be short English phrases.
- Request and response schemas should be added first for high-risk mutations.
- SSE endpoints must document event types, heartbeat, and completion behavior.

## Immediate API Documentation Gaps

- Several large routes mix request validation, business logic, and response formatting.
- Many high-risk mutation endpoints lack explicit schema classes.
- Human quick-trade endpoints do not clearly advertise idempotency behavior.
- Agent OpenAPI is separate and must stay in sync with `/api/agent/v1` routes.
- Some legacy route names use camelCase and should be documented as compatibility names,
  not silently renamed.

## Next Inventory Tasks

1. Export generated OpenAPI with `SKIP_STARTUP_HOOKS=true`.
2. Compare generated OpenAPI paths against this decorator inventory.
3. Compare frontend `src/api/*` calls against backend paths.
4. Mark each endpoint with visibility: public, private, admin, agent, internal.
5. Add schema coverage to high-risk mutation endpoints.
