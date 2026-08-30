# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

QuantDinger is a self-hosted "AI Trading OS": AI research → strategy code → backtest → paper/live execution → monitoring. This repo contains the **backend only**: Flask API + all worker processes, Compose deployment stacks, ops config (Prometheus/Grafana/Alertmanager), docs, and a standalone MCP server. The web/mobile frontends live in separate private repos (QuantDinger-Vue, QuantDinger-Mobile) and are consumed here as published GHCR images. There is no frontend source in this tree.

Python 3.12 backend (`backend_api_python/`), PostgreSQL 18, two Redis 8 instances, Docker Compose for everything.

## Common commands

All backend commands run from `backend_api_python/` unless noted.

### Development setup

```bash
cd backend_api_python
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp env.example .env        # set SECRET_KEY, ADMIN_USER/ADMIN_PASSWORD at minimum
python run.py              # dev server with auto-reload on http://localhost:5000
```

Tests need PostgreSQL + Redis (`DATABASE_URL`, `REDIS_HOST/REDIS_PORT` env) — CI and the compose stack both provide them (`SKIP_STARTUP_HOOKS=1` in CI). Apply migrations first:

```bash
QD_PROCESS_ROLE=migration python -m app.commands.migrate
```

### Tests

```bash
# Default suite (unit + contract; skips live-exchange and stress markers, and release gates)
python -m pytest -m "not integration and not stress" --ignore=tests/release_gate -q

# Single test
python -m pytest tests/test_agent_v1.py::test_whoami_requires_token -q

# Release gates (live-execution safety — run separately)
python -m pytest tests/release_gate -q
```

`integration` = live exchange smoke tests needing real testnet keys; `stress` = long-running synthetic market tests (see `pytest.ini`).

### Lint and guardrails (all run in CI: `.github/workflows/basic-ci.yml`)

```bash
ruff check app scripts tests                                   # ruff ~0.16, line-length 120, critical rules only (E9/F63/F7/F82)
python -m compileall -q app scripts tests                      # syntax check
python scripts/backend_quality_check.py                        # legacy-hotspot regression guard (baseline: backend_quality_baseline.json)
python scripts/check_requirements_lock.py                      # requirements.lock consistency
```

### Repo-level checks (run from repo root)

```bash
python scripts/check_version.py      # VERSION file vs package/artifact versions
python scripts/check_mojibake.py     # text encoding
python scripts/check_docs.py         # docs structure and links
docker compose -f docker-compose.yml config -q
docker compose -f docker-compose.yml -f docker-compose.production.yml -f docker-compose.observability.yml config -q
```

### Docker stacks

- `docker compose up -d --build` — local/source stack (web :8888, mobile H5 :8889, API :5000; observability not started by default)
- `-f docker-compose.observability.yml` — adds Prometheus/Grafana/Alertmanager
- `-f docker-compose.production.yml` — hardened non-root/read-only overlay
- `docker-compose.ghcr.yml` — prebuilt-image install stack (what `install.sh` deploys)
- `docker-compose.build.yml` — build frontend from a gitignored `./QuantDinger-Vue/` clone at repo root; pin consumed images via `IMAGE_TAG` / `FRONTEND_TAG` in root `.env`
- Validate production config: `python backend_api_python/scripts/check_production_config.py --env-file .env --env-file backend_api_python/.env`

### MCP server

```bash
cd mcp_server
pip install -e ".[dev]"
pytest
```

Package `quantdinger-mcp` (thin HTTP wrapper over the Agent Gateway). Transport selected by `QUANTDINGER_MCP_TRANSPORT`: `stdio` (default, desktop IDEs), `sse`, or `streamable-http` (remote agents; also set `QUANTDINGER_MCP_HOST/PORT`).

## Architecture: the big picture

### One image, six process roles

The single backend Dockerfile is reused by containers with different `command:` entries plus a `QD_PROCESS_ROLE` env var, validated in `app/runtime/roles.py`:

| Role | Entrypoint | Owns |
| --- | --- | --- |
| `migration` | `python -m app.commands.migrate` | Schema migrations, exits before app services start |
| `api` | Gunicorn (`run.py`) | HTTP, auth, validation, durable command submission |
| `trading` | `python -m app.commands.trading_worker` | Strategy runtimes, pending orders, broker sessions, reconciliation |
| `scheduler` | `python -m app.commands.scheduler` | Portfolio/deployment/payment/signal schedules |
| `celery` (worker + beat) | `celery -A app.celery_app:celery_app worker/beat` | Finite retryable jobs (AI, backtest, experiment, report, maintenance), queues `jobs,ai,maintenance` |

**Ownership rules (hard invariant, verified by `tests/release_gate/test_live_execution_release_gate.py`; detailed in `docs/architecture/PROCESS_ROLES_AND_TASKS.md`):** HTTP routes validate and delegate — they must never own trading loops, exchange-specific behavior, or large DB workflows. Long-lived trading loops belong to the trading worker (commands flow: API route → PostgreSQL command record → trading-worker → strategy runtime/broker adapter). Finite retryable work belongs to Celery via the **jobs Redis**, which is a separate instance from the disposable **cache Redis** — never use cache Redis as the Celery broker.

### Module map (`backend_api_python/app/`)

- `routes/` — thin HTTP route facades (human API + `routes/agent_v1/` = Agent Gateway under `/api/agent/v1`)
- `services/` — domain workflows: `strategy_v2/` (versioned strategy contracts), `strategy_runtime/` (signals, intents, execution, state), `live_trading/` (normalized crypto exchange adapters: Binance, OKX, Bybit, Bitget, Gate, HTX + factory), `ibkr_trading/`, `alpaca_trading/`, `backtest_engine/`
- `data_sources/` — raw market-data source adapters (CCXT, yfinance, ...); `data_providers/` — aggregated dashboard/macro/news/sentiment providers that fan out across sources
- `markets/` — symbol/catalog normalization
- `tasks/` — Celery jobs (registered in `celery_app.py`); `workers/`, `commands/`, `runtime/` — process shells and role/ownership helpers
- `openapi/` — flask-smorest schemas/blueprints; `observability/` — metrics, request IDs, JSON logs
- `migrations/` — **raw date-prefixed SQL files**, applied in order by the migration role

### OpenAPI is the contract SSOT — CI enforces it

- Human web API: `docs/api/openapi.yaml`, regenerated with `python scripts/export_openapi.py`
- Agent Gateway: `docs/agent/agent-openapi.json`, updated by hand
- Any route change must update the corresponding artifact; `.github/workflows/openapi-ci.yml` runs Spectral lint, export diff, and oasdiff breaking-change checks. Read `docs/architecture/API_CONVENTIONS.md` before adding public endpoints.

### Agent Gateway and MCP

Auth: `app/utils/agent_auth.py` `@agent_required(scope=...)`; tokens hashed at rest in `qd_agent_tokens` (never log raw tokens); every call (success and denial) audited to `qd_agent_audit`; rate-limited. Async jobs go through `app/utils/agent_jobs.py` with SSE progress streaming (`GET /jobs/{id}/stream`). **Trading is paper-only by default** — live execution requires token `paper_only=false` AND server env `AGENT_LIVE_TRADING_ENABLED=true` AND operator limits/allowlists. The MCP server exposes R+W+B endpoints only (no trading); add an MCP tool only after the capability exists as a REST endpoint. Read `docs/agent/AGENT_ENVIRONMENT_DESIGN.md` and `docs/agent/AI_INTEGRATION_DESIGN.md` before changing any agent-facing surface.

## Conventions

- Code comments, docstrings, and log messages in **English** (docs/agent must be English-only).
- Branch naming: `fix/`, `feat/`, `docs/`, `chore/` prefixes.
- Keep routes thin: validate → call service → return JSON. Put new behavior in focused sibling modules rather than growing legacy hotspot files.
- Security red lines (do not weaken without an explicit request): agent live-trading gates, credential encryption (`CREDENTIAL_ENCRYPTION_KEY` — stable, separate from `SECRET_KEY`), hashed tokens, non-root production runtime, loopback-only published ports.
- Never commit secrets or production `.env` files; use `env.example` patterns. Settings UI writes runtime config to `/app/.env` (host `backend.env` in GHCR stack, `backend_api_python/.env` in source deployments) — that file is mode 600, owned by UID 10001.
- Version changes touch `VERSION` (root) — `scripts/check_version.py` verifies consistency across package files; release tags are `vX.Y.Z`.

## Where to read deeper

Start from the docs index `docs/README.md`. The docs are the authoritative design record:

- `docs/architecture/` — ARCHITECTURE.md, MODULE_BOUNDARIES.md, PROCESS_ROLES_AND_TASKS.md, CONCURRENCY_MODEL.md, API_CONVENTIONS.md, EXTENSION_GUIDE.md
- `docs/trading/` — STRATEGY_DEV_GUIDE.md (Strategy API V2), INDICATOR_DEV_GUIDE.md
- `docs/agent/` — AGENT_ENVIRONMENT_DESIGN.md, AI_INTEGRATION_DESIGN.md, MCP_SETUP.md, agent-openapi.json
- `docs/deployment/` — INSTALL_TROUBLESHOOTING.md, CLOUD_DEPLOYMENT_EN.md, PRODUCTION_HARDENING.md, OBSERVABILITY.md
- Root README.md — user-facing overview, install paths, and a "Where changes belong" table for routing edits to the right module
