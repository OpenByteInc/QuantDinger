# QuantDinger Extension Guide

This guide explains how to add features without making the backend harder to
maintain. Prefer small, boring, easy-to-review changes.

## Before You Start

1. Find the closest existing module.
2. Read `docs/architecture/ARCHITECTURE.md` and `docs/architecture/MODULE_BOUNDARIES.md`.
3. Decide whether the change is API, service, adapter, data, worker, or docs.
4. Keep route paths and response fields backward-compatible unless a breaking
   change is explicitly approved.

## Add a Human Web API Endpoint

Use this flow for routes consumed by the web or mobile UI:

1. Add the route in the closest route module, or create a small sibling module
   if the current file is already large.
2. Keep the route thin: validate input, call a service, return JSON.
3. Put workflow logic in `app/services/<feature>/...` when the feature has more
   than one workflow. Use a flat service file only for small one-off helpers.
4. Add or update OpenAPI tag metadata in `app/openapi/register.py` and
   `app/openapi/tags.py` when introducing a new route family.
5. Regenerate the human API spec:

```bash
cd backend_api_python
python scripts/export_openapi.py
```

6. Run the OpenAPI smoke test when dependencies are available:

```bash
cd backend_api_python
python -m pytest tests/test_openapi.py -q
```

## Add an Agent Gateway Endpoint

Use this flow for external AI agents, MCP clients, and automation:

1. Add code under `app/routes/agent_v1`.
2. Enforce token scopes using the existing agent security helpers.
3. Keep write/trade actions explicit and auditable.
4. Update `docs/agent/agent-openapi.json`.
5. Do not mix agent-only routes into the human OpenAPI spec.

## Add a Market Data Source

1. Implement the adapter in `app/data_sources`.
2. Return normalized rows:

```python
{
    "time": 1710000000,
    "open": 1.0,
    "high": 1.2,
    "low": 0.9,
    "close": 1.1,
    "volume": 1000.0,
}
```

3. Register selection logic in `DataSourceFactory`.
4. Keep provider-specific column names inside the adapter.
5. Include market, symbol, timeframe, exchange, and market type in cache keys
   where relevant.
6. Add a small smoke check or script if the provider has fragile symbol rules.

## Add a Symbol Master Data Source

1. Prefer database-backed master data over hardcoded symbol lists.
2. Put sync/import logic in scripts or service modules, not route files.
3. Keep seed SQL deterministic so fresh Docker installs work offline.
4. Make search tolerant of symbol, name, alias, and localized company names.
5. Do not hardcode large symbol lists in Python route modules.

## Add an Exchange or Broker Adapter

1. Put low-level API calls under `app/services/live_trading`.
2. Normalize account, position, order, fill, and error shapes.
3. Keep exchange precision and sizing logic near the adapter.
4. Keep strategy lifecycle outside the adapter.
5. Add explicit notes for market type support:
   - spot
   - swap/perpetual
   - US stock / HK stock
   - paper/live
6. If an adapter supports live orders, document idempotency and retry behavior.
7. For local-gateway brokers (IBKR TWS, FutuOpenD), also update
   `app/utils/local_brokers.py`, `broker_market_policy.py`, and the
   deployment notes under `docs/architecture/FUTU_OPEND.md` (or IBKR README).

## Add a Strategy Runtime Feature

1. Avoid adding more unrelated logic to `trading_executor.py`.
2. Extract new behavior into a focused service module first.
3. Keep order intent creation separate from order execution.
4. Keep market data reads separate from account mutation.
5. Add safeguards for duplicate starts, duplicate orders, and worker restarts.

## Add a Backtest Feature

1. Avoid growing `backtest.py` unless the change is tiny.
2. Prefer extracting:
   - data loading
   - signal evaluation
   - execution model
   - metrics
   - report formatting
3. Preserve historical result compatibility.
4. Clearly document fill assumptions.

## Add an AI Feature

1. Keep prompts and skill definitions separate from provider calls.
2. Keep provider adapters behind a service boundary.
3. Localize user-facing text through translation/i18n structures.
4. Do not let AI flows place live orders without explicit existing trade APIs,
   permissions, billing checks, and audit logs.
5. For streaming routes, handle cancellation and partial failures.

## Add Settings

1. Define the setting in the backend settings registry.
2. Choose whether it is public, admin-only, or internal.
3. Keep secrets out of public config endpoints.
4. If the setting changes OpenAPI behavior, regenerate `docs/api/openapi.yaml`.
5. Keep environment variable names stable and documented.

## Add Background Work

1. Put startup wiring in `app/startup.py`.
2. Put worker behavior in a dedicated service module.
3. Add a clear owner/lock model for multi-process deployments.
4. Make work idempotent before adding retries.
5. Log start, stop, retry, and failure states in English.

## Verification Checklist

Run the smallest useful checks for the change:

```bash
python -m py_compile path/to/changed_file.py
python scripts/check_mojibake.py
```

For API changes:

```bash
cd backend_api_python
python scripts/export_openapi.py
python -m pytest tests/test_openapi.py -q
```

For Docker/deploy changes:

```bash
docker compose config
```

For frontend-only work, use the frontend dev server. Do not run production
builds during every small iteration unless the change touches bundling,
environment injection, or release assets.
