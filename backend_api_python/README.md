# QuantDinger Python API (backend)

Flask-based backend for QuantDinger: market data, indicators, AI analysis, backtesting, and a strategy runtime with multi-user support.

## What you get

- **Multi-market data layer**: factory-based providers (crypto / US stocks / forex / futures, etc.)
- **Indicators + backtesting**: persisted runs/history in PostgreSQL
- **AI multi-agent analysis**: optional web search + OpenRouter LLM integration
- **Strategy runtime**: thread-based executor, with optional auto-restore on startup
- **Pending orders worker (optional)**: polls queued orders and dispatches signals (webhook/notifications)
- **Multi-user authentication**: role-based access control (admin/manager/user/viewer)
- **User management**: admin can create/edit/delete users and reset passwords

## Project layout

```text
backend_api_python/
|-- app/
|   |-- __init__.py                 # Flask app factory + startup hooks
|   |-- config/                     # Settings (env-driven)
|   |-- data_sources/               # Data sources + factory
|   |-- routes/                     # REST endpoints
|   |-- services/                   # Analysis, agents, strategies, search, user_service
|   |-- utils/                      # PostgreSQL helpers, config loader, logging, HTTP utils
|-- migrations/
|   |-- init.sql                    # PostgreSQL schema initialization
|-- env.example                     # Copy to .env for local config
|-- requirements.txt
|-- run.py                          # Entrypoint (loads .env, applies proxy env, starts Flask)
|-- gunicorn_config.py              # Optional production config
|-- README.md
```

## Architecture and quality guardrails

- Backend module boundaries: `../docs/MODULE_BOUNDARIES.md`
- Concurrency model: `../docs/CONCURRENCY_MODEL.md`
- Canonical live-trading venue matrix: `app/services/live_trading/capabilities.py`
- Stable live order contracts: `app/services/live_trading/contracts.py`
- Structural regression guard:

```bash
python scripts/backend_quality_check.py
```

Exchange integrations should add offline fixtures and pass:

```bash
python scripts/exchange_smoke_test.py --offline-contracts
```

## Quick start (Docker - Recommended)

### 1) Configure environment

Create `.env` file in project root:

```bash
# Database
POSTGRES_USER=quantdinger
POSTGRES_PASSWORD=your_secure_password
POSTGRES_DB=quantdinger

# Admin account (created on first startup)
ADMIN_USER=admin
ADMIN_PASSWORD=your_admin_password

# Optional
OPENROUTER_API_KEY=your_api_key
```

AtlasCloud is supported as an OpenAI-compatible LLM provider. See the official
[AtlasCloud LLM API docs](https://www.atlascloud.ai/docs/models/llm) and
[API key guide](https://www.atlascloud.ai/docs/api-keys), then set:

```bash
LLM_PROVIDER=atlascloud
ATLASCLOUD_API_KEY=your_api_key
ATLASCLOUD_MODEL=deepseek-v3
ATLASCLOUD_BASE_URL=https://api.atlascloud.ai/v1
```

Release builds inject the backend app version from the Git tag (`v3.0.23` ->
`3.0.23`). Local source runs fall back to `git describe` and then the repo-root
`VERSION` file; local Docker builds can override with `APP_VERSION`.

### 2) Start services

```bash
docker-compose up -d
```

This will:
- Start PostgreSQL database (port 5432)
- Initialize database schema automatically
- Start backend API (port 5000)
- Start frontend (port 8888)
- Create admin user from `ADMIN_USER`/`ADMIN_PASSWORD`

### 3) Access the system

- Frontend: `http://localhost:8888`
- Backend API: `http://localhost:5000`
- Login with your configured admin credentials

## Quick start (Local Development)

### Prerequisites

- Python 3.10+ recommended
- PostgreSQL 14+ installed and running

### 1) Setup PostgreSQL

```bash
# Create database and user
sudo -u postgres psql
CREATE DATABASE quantdinger;
CREATE USER quantdinger WITH ENCRYPTED PASSWORD 'your_password';
GRANT ALL PRIVILEGES ON DATABASE quantdinger TO quantdinger;
\q

# Initialize schema
psql -U quantdinger -d quantdinger -f migrations/init.sql
```

### 2) Install dependencies

```bash
cd backend_api_python
pip install -r requirements.txt
```

### 3) Create your local `.env`

Windows (CMD):
```bash
copy env.example .env
```

Windows (PowerShell):
```bash
Copy-Item env.example .env
```

Then edit `.env` and set:

```bash
# Required
DATABASE_URL=postgresql://quantdinger:your_password@localhost:5432/quantdinger
SECRET_KEY=your-secret-key-change-me
ADMIN_USER=admin
ADMIN_PASSWORD=your_admin_password

# Optional but recommended
OPENROUTER_API_KEY=your_api_key
```

For AtlasCloud instead, use:

```bash
LLM_PROVIDER=atlascloud
ATLASCLOUD_API_KEY=your_api_key
ATLASCLOUD_MODEL=deepseek-v3
ATLASCLOUD_BASE_URL=https://api.atlascloud.ai/v1
```

### 4) Start the API server

```bash
python run.py
```

Default address: `http://localhost:5000`

## Database (PostgreSQL)

- Connection: configured via `DATABASE_URL` environment variable
- Schema: initialized via `migrations/init.sql`
- Tables are managed with foreign key constraints and indexes for performance
- User data isolation via `user_id` column in relevant tables

## User Roles & Permissions

| Role | Permissions |
|------|-------------|
| admin | Full access + user management |
| manager | Strategy, backtest, portfolio, settings |
| user | Strategy, backtest, portfolio (own data) |
| viewer | Dashboard view only |

## API Endpoints

### Authentication
```text
POST /api/user/login      - User login
POST /api/user/logout     - User logout
GET  /api/user/info       - Get current user info
```

### User Management (Admin only)
```text
GET    /api/users/list           - List all users
POST   /api/users/create         - Create user
PUT    /api/users/update?id=     - Update user
DELETE /api/users/delete?id=     - Delete user
POST   /api/users/reset-password - Reset password
```

### Self-Service
```text
GET  /api/users/profile         - Get own profile
PUT  /api/users/profile/update  - Update own profile
POST /api/users/change-password - Change own password
```

### Other Endpoints
```text
GET  /api/health
GET  /api/indicator/kline
GET  /api/global-market/adanos-sentiment?tickers=AAPL,TSLA
POST /api/fast-analysis/analyze    - Fast AI analysis (main entry)
GET  /api/fast-analysis/history    - Analysis history
GET  /api/fast-analysis/similar-patterns - RAG similar patterns
POST /api/fast-analysis/feedback   - User feedback on analysis
```

### Optional Adanos Market Sentiment

Set `ADANOS_API_KEY` to enable optional US stock sentiment enrichment from the
Adanos Market Sentiment API. If the key is not configured, the endpoint returns
`enabled=false` and the rest of QuantDinger continues to work normally.

```bash
ADANOS_API_KEY=your_adanos_key
ADANOS_SENTIMENT_SOURCE=reddit  # reddit, x, news, or polymarket
```

Example:

```text
GET /api/global-market/adanos-sentiment?tickers=AAPL,TSLA&source=reddit&days=7
```

The response normalizes common compare fields across sources, including
`sentiment_score`, `buzz_score`, `bullish_pct`, `bearish_pct`, `mentions`,
`trend`, `trend_history`, and source-specific activity metrics such as
`subreddit_count`, `unique_tweets`, `source_count`, `trade_count`,
`market_count`, and `total_liquidity`.

### Economic Calendar Data

The global-market economic calendar is free-first. By default it uses the
no-key AkShare/WallstreetCN calendar fallback. If you configure
`TRADING_ECONOMICS_CLIENT` and `TRADING_ECONOMICS_KEY`, QuantDinger will try
Trading Economics as the official international calendar provider before
falling back. Finnhub paid-only calendar and social-sentiment endpoints are
skipped by default through `FINNHUB_FREE_ONLY=true`.

Only set `FINNHUB_FREE_ONLY=false` if your Finnhub plan explicitly includes
those paid endpoints.

## AI analysis & memory

Uses **FastAnalysisService** (single LLM call, multi-factor):

- Memory: `qd_analysis_memory` in PostgreSQL
- API: `POST /api/fast-analysis/analyze` (main), `/history`, `/similar-patterns`, `/feedback`
- Calibration: `AICalibrationService` tunes BUY/SELL thresholds from validated outcomes

## Frontend integration

For local Vue dev (private frontend repo):
- Frontend dev server URL depends on your `vue.config.js` / Vite config
- Backend: `http://localhost:5000`
- Point devServer proxy at the backend URL above

## Production (Gunicorn)

```bash
gunicorn -c gunicorn_config.py "run:app"
```

## Troubleshooting

- **Database connection failed**: Check `DATABASE_URL` format and PostgreSQL service status
- **Outbound requests fail**: Configure `PROXY_URL` in `.env`
- **Disable auto-restore**: Set `DISABLE_RESTORE_RUNNING_STRATEGIES=true`
- **Disable pending-order worker**: Set `ENABLE_PENDING_ORDER_WORKER=false`

## License

Apache License 2.0. See repository root `LICENSE`.
