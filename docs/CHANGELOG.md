# QuantDinger Changelog

This document records version updates, new features, bug fixes, and database migration instructions.

---

## quantdinger-mcp 0.2.0 (2026-05-29) — PyPI package

Standalone MCP server release (install: `pip install quantdinger-mcp==0.2.0` / `uvx quantdinger-mcp@0.2.0`).

### Added
- **Indicator workspace tools**: `get_indicator_authoring_contract`, `validate_indicator_code`, `save_indicator`, `list_indicators`, `get_indicator`
- **Strategy tools**: `create_strategy`, `update_strategy` (blocks `status=running` client-side)
- **Job helpers**: `list_jobs`, `wait_for_job`, `stream_job_until_done` (bounded SSE)
- **Experiments**: `submit_experiment_pipeline`, `submit_ai_optimize` (requires `confirm_llm_usage=true`)
- **Portfolio (read-only)**: `list_portfolio_positions`, `list_paper_orders`
- **Connectivity**: `check_health` (no token)
- **Backtest**: `strict_mode`, `strategy_config`, `indicator_params` on `submit_backtest`

### Security
- Response redaction for credential-like JSON fields (`api_key`, `secret`, `passphrase`, …)
- Indicator Python source capped at **512 KiB** (client + Gateway)
- Trading (`quick-trade/*`) and admin APIs remain **not exposed** via MCP

### Requires
- QuantDinger backend with Agent Gateway `/api/agent/v1` (indicator routes from this repo)
- Agent token scopes: **R + W + B** recommended for full indicator workflow

---

## V3.0.17 (2026-05-28) — Login security alerts, account login history, schema consolidation, live-trading hardening

This release focuses on **account security UX**, **cleaner database migrations**, and a large batch of **live / quick-trade reliability** fixes across HTX, spot sizing, and the indicator sandbox.

### 🚀 New Features

#### Login security notifications (new device / new region)
After a successful sign-in (password, email code, or OAuth), the backend enriches `qd_security_logs` with device fingerprint and best-effort GeoIP, then alerts the user when the sign-in looks unfamiliar:
- **In-app (browser)** notification via `SignalNotifier` (`signal_type=security_login`)
- **Email** to the registered address when SMTP is configured
- First-ever login for an account is recorded but does **not** spam alerts (only subsequent new device / new region events)

#### Account login history (Profile)
- **API**: `GET /api/users/login-logs?page=&page_size=` (authenticated)
- **UI**: new **Login history** tab at the end of Profile (`/profile?tab=loginLogs`) — method, device, location, IP, and “new device / new region” badges

#### Initial-password change reminder (bootstrap admin only)
- New column `qd_users.password_changed_at` (`NULL` = still on the password issued at account creation)
- Only the **first user** (`MIN(id)`) is prompted to change password after login; OAuth / email-code users without a known password are unaffected
- Frontend: post-login modal + deep link to `/profile?tab=password`

### 🛠️ Engineering

#### Single source of truth for PostgreSQL schema
- Incremental files `v3_1_0_*` … `v3_3_1_*` were **merged into** `migrations/init.sql` and removed from the repo
- Boot still runs the full idempotent `init.sql` via `init_database()`; no separate manual migration files to track
- Old databases get missing columns via `ALTER TABLE … ADD COLUMN IF NOT EXISTS` blocks inside `init.sql` (trades `close_reason`, grid PnL columns, `qd_agent_jobs.progress`, etc.)

#### Signal execution standard (backtest ↔ live alignment)
- Documented contract in `docs/SIGNAL_EXECUTION_STANDARD.md` (+ CN)
- Executor / backtest / trade records aligned on close reasons and sizing semantics; `qd_strategy_trades.close_reason` persisted for analytics

#### Grid bot scaffolding
- `qd_grid_cells` table and `grid_cells.py` persistence for per-rung ladder state
- Trade ledger fields `matched_entry_price` / `grid_matched_profit` for realised grid-pair PnL in the UI

#### Indicator sandbox hardening
- `safe_exec.py` blocks additional file-read / frame-attribute escape paths (e.g. reading `.env` from strategy code)
- Default indicator template and code-quality checks updated for the four-way signal contract

### 🐛 Bug Fixes

- **HTX USDT-M (V5)**: quick trade / live path — position mode, `reduce_only`, margin pre-check, and clearer Chinese error messages for insufficient margin
- **Alpaca**: `account.id` as `uuid.UUID` no longer crashes log prefixes / connect flow
- **Spot sizing**: Binance / Bitget / Bybit spot lot-size and quote-qty paths improved; pending-order worker aligned where applicable
- **Quick trade**: balance checks and HTX V5 routing fixes

### 🗄️ Database

All changes ship inside `migrations/init.sql` (idempotent). On upgrade, **restart the backend** so `init_database()` reapplies `init.sql`. Fresh Docker Postgres still runs the same file once from `docker-entrypoint-initdb.d`.

No standalone `v3_*.sql` files remain in the repository.

### ⚠️ Upgrade Notes

1. **Backend**: `docker compose up -d --build backend` (or restart) so new routes (`/api/users/login-logs`) and `login_notify` logic are loaded — `restart` alone is not enough if the image was built before this tag.
2. **Frontend**: rebuild or run `npm run dev` with current `QuantDinger-Vue-src`; hard-refresh Profile to see the Login history tab.
3. **SMTP**: login alert emails require working `SMTP_*` env vars; in-app alerts require browser channel enabled in notification settings.
4. **Bootstrap admin**: if you need to re-test the initial-password prompt, set `password_changed_at = NULL` only for `id = (SELECT MIN(id) FROM qd_users)`.

### 📂 Key Files

| Area | Path |
|------|------|
| Login notify | `backend_api_python/app/services/login_notify.py` |
| Auth hooks | `backend_api_python/app/routes/auth.py` |
| Login logs API | `backend_api_python/app/routes/user.py` |
| Profile UI | `QuantDinger-Vue-src/src/views/profile/index.vue` |
| Schema | `backend_api_python/migrations/init.sql` |
| Sandbox | `backend_api_python/app/utils/safe_exec.py` |
| HTX V5 | `backend_api_python/app/services/live_trading/htx_v5.py`, `htx.py` |

### ✅ Version

Backend `APP_VERSION` and frontend `package.json` / layout fallbacks: **3.0.17** (canonical `VERSION` at repo root). Bump with `python scripts/bump_version.py X.Y.Z`.

---

## V3.0.7 (2026-05-13) — Code slimming: full removal of Polymarket prediction markets module (including frontend)

The legacy **Polymarket prediction markets** module was fully removed. This release cleans it up in one pass: 5 backend files deleted (worker / batch analyzer / single analyzer / route / data_source) + 2 backend tests + 2 frontend files (API client + modal component) + billing items + 3 database tables + the “related prediction markets” prompt context in AI analysis + the “Prediction Markets” tab on the AI asset analysis page + all translation entries across 10 locale files — **≈ 160 KB / 5600+ lines removed in total**.

### 🧹 Frontend Removed

- **“Prediction Markets” tab on the `ai-asset-analysis` page**: entire `<a-tab-pane key="polymarket">` removed, along with the mounted `<PolymarketAnalysisModal>`
- **Dialog for pasting a polymarket.com URL for single-market AI analysis**: `components/PolymarketAnalysisModal/index.vue` (24.8 KB) deleted + companion API client `api/polymarket.js` deleted (this was the source of the 404 on `/api/polymarket/history?page=1&page_size=20` when the page loaded)
- **`PredictionMarket` market enum**: removed from `ai-asset-analysis` card styles, `indicator-ide` watchlist / color mapping / CSS classes
- **AI Trading Radar** carousel PredictionMarket branch (`is-prediction` 290px card variant / `rc-prediction-title` two-line truncated title / probability + score + recommendation trio / related CSS) fully removed; cards now consistently show price / 24h change / signal
- **i18n translations**: **1087 lines** of `polymarket.*` / `PredictionMarket` / `predictionmarket` entries cleared from 10 locale files (ar / de / en / fr / ja / ko / th / vi / zh-CN / zh-TW) via one-shot script; all pass `node --check`

### 🧹 Backend Removed

- **Background LLM worker**: `polymarket_worker.py` (OpenAI batch market analysis every 30 min), `polymarket_batch_analyzer.py`, `polymarket_analyzer.py` all deleted; startup hook `start_polymarket_worker()` removed
- **API routes**: all `/api/polymarket/*` endpoints (including `/analyze`, `/history`) retired; `routes/polymarket.py` deleted + blueprint unregistered
- **Data source**: `app/data_sources/polymarket.py` (67 KB, CLOB/Gamma API client + local cache read/write) deleted
- **Polymarket context in AI asset analysis**: removed `🎯 PREDICTION MARKETS` section from `fast_analysis.py` prompt template; removed `include_polymarket` parameter and `_get_polymarket_events` / `_extract_polymarket_keywords` private methods from `market_data_collector.py` (core analysis unchanged; prompt simply loses a 30–80 token prediction-market background block)
- **Billing items**: `billing_service.py` — removed `cost_polymarket_deep_analysis` unit price, `polymarket_deep_analysis` feature name mapping, and `feature_costs` output
- **Orphan unused function**: `data_providers/opportunities.py::analyze_opportunities_polymarket`
- **Tests**: `tests/test_polymarket_slug_lookup.py`, `tests/test_polymarket_url_parsing.py`

### 🗄️ Database

`migrations/init.sql` — removed `CREATE TABLE` and indexes for 3 related tables, plus a one-time cleanup migration for existing databases (no-op on fresh deploys):

```sql
DROP TABLE IF EXISTS qd_polymarket_asset_opportunities CASCADE;
DROP TABLE IF EXISTS qd_polymarket_ai_analysis CASCADE;
DROP TABLE IF EXISTS qd_polymarket_markets CASCADE;
```

### ⚠️ Upgrade Notes

- **No manual migration required**: on backend restart, `init_database()` automatically runs `init.sql` and drops the three `qd_polymarket_*` tables; if those tables never existed (new DB), the DROP statements are no-ops
- **Frontend cache**: after upgrade, ask users to hard-refresh (Ctrl+Shift+R) to clear cached `chunk-vendors.*.js` bundles; otherwise the browser may still request `/api/polymarket/history` and get 404
- **AI analysis impact**: the “related prediction market events” block at the end of the prompt was removed. It had minimal effect on final decisions (most symbols have no matching prediction market, and prediction markets ≠ fundamentals); LLM calls save ~30–80 tokens on average with slightly faster responses
- **AI Trading Radar**: backend `trading_opportunities` no longer returns cards with `market: 'PredictionMarket'` (orphan scanner removed); frontend carousel now shows only stocks / crypto / forex opportunities
- **Third-party Adanos sentiment API `polymarket` source is unaffected** — that is Adanos’s sentiment data channel, completely independent of the Polymarket module removed here; it remains

### 💡 Why

Background LLM running continuously with no user-facing outlet = pure cost: DB rows, OpenAI tokens, Python memory, and log noise all growing for nothing. After trimming: quieter startup logs / one fewer background thread / lower OpenAI monthly bill / smaller DB footprint (exact savings depend on historical data volume).

---

## V3.0.6 (2026-05-13) — USDT payment rewrite: single address + amount suffix + four chains (TRC20 / BEP20 / ERC20 / SOL)

This release changes one area thoroughly: **the USDT payment system**. Previously each order got a unique TRC20 address (xpub HD derivation). Two pain points emerged in production — **(1)** consolidating funds from dozens of derived addresses required individual TRC20 transfers, burning ≈ 1.5 USDT per transfer in Energy/Bandwidth, so monthly consolidation cost = order count × 1.5 USDT; **(2)** derived addresses were TRC20-only, making cross-chain expansion (users had long requested BSC / ETH / SOL) a large refactor. This release solves both with **single address + amount-suffix identification** and extends support to 4 chains.

### 🚀 New Features

#### USDT payment: single address + amount suffix model (replaces xpub derivation)
Core idea — **no new address per order; everyone pays to the same main wallet; order identity is encoded in the amount suffix**. Example: a $19.9 monthly plan — order #A pays 19.901234 USDT, order #B pays 19.905678 USDT; both land in the main wallet and we match back to the original order by suffix:
- **Zero consolidation cost**: funds go directly to the main wallet; no more scanning dozens of derived addresses for consolidation transfers
- **Extra cost ≤ 0.01 USDT/order**: suffix capped within 0.01 USDT (default 6 decimal places, 10000+ slot space); visually “just a normal payment” for users
- **Collision defense**: partial unique index `UNIQUE(chain, amount_usdt) WHERE status IN ('pending','paid')` on `qd_usdt_orders` prevents active orders from sharing the same amount; on INSERT collision, auto-retry with a different seed (default 10 attempts)
- **Environment variable**: `USDT_AMOUNT_SUFFIX_DECIMALS=6` (recommended default)

#### Four chains enabled at once: TRC20 / BEP20 / ERC20 / SOL
Each chain needs one env var; unconfigured chains are hidden in the frontend:
- `USDT_TRC20_ADDRESS=Txxx...`, `USDT_BEP20_ADDRESS=0x...`, `USDT_ERC20_ADDRESS=0x...`, `USDT_SOL_ADDRESS=base58...`
- Master switch `USDT_PAY_ENABLED_CHAINS=TRC20,BEP20,ERC20,SOL` (orders on chains not in the whitelist are rejected)
- New `GET /api/billing/usdt/chains`: returns currently available chains (with `recommended` badge + typical fees); UI renders the selector from this
- Recommended chains: **BSC (≈$0.30/tx) + Solana (≈$0.0005/tx)** — cheaper than TRC20

#### Wallet deep-link URI QR codes (imToken / MetaMask / TokenPocket / Phantom scan-to-fill amount)
Each chain generates a standard protocol URI embedded in the QR code; mainstream wallets auto-fill address + amount on scan:
- **EVM (BEP20/ERC20)**: `ethereum:<contract>@<chain_id>/transfer?address=<recipient>&uint256=<raw>` (EIP-681)
- **Solana**: `solana:<recipient>?amount=...&spl-token=<mint>&label=...&message=...` (Solana Pay)
- **TRON**: `tron:<recipient>?asset=USDT&amount=<human>` (TP / imToken supported; legacy TronLink falls back to address-only)
- When wallets don’t recognize the URI, fallback is “read address + user enters amount manually” — amount copy button + highlighted suffix help avoid underpayment

#### Order page UI overhaul
- Chain selection modal: each chain + typical fee + recommended badge + one-click continue
- Payment modal: QR code with large amount display; **suffix digits highlighted in red** (the part users most often miss), one-click copy amount/address
- Wallet compatibility tooltip: scan compatibility hints per chain type
- When no chain is configured, show “contact admin to configure USDT_*_ADDRESS” instead of 500

### 🛠️ Engineering Improvements

#### Backend `app/services/usdt_payment/` package refactor
Original 830-line monolith `usdt_payment_service.py` split into a layered package:
- `chains.py`: chain metadata / URI construction / amount suffix generation (pure functions, 100% unit test coverage)
- `watchers/`: `tron.py` (TronGrid) / `evm.py` (Etherscan + BscScan same endpoint) / `solana.py` (official RPC, `getSignaturesForAddress` + `getTransaction` parsing SPL `preTokenBalances`/`postTokenBalances`)
- `service.py`: order creation + payment matching + worker
- Legacy path `app.services.usdt_payment_service` kept as shim; all existing imports remain compatible

#### Watcher common contract
All four chains share the same interface `find_incoming(address, amount, created_at) -> (IncomingTransfer | None, debug_note)`. **Exact amount matching** (±1 micro-unit tolerance for wallet trailing-zero truncation) is key to matching scheme A — no longer accepting overpayment as a match like the old version, which would break suffix identification.

#### DB schema evolution (backward compatible + one-time self-heal)
`qd_usdt_orders` in `migrations/init.sql` gains 4 columns (`currency` / `amount_suffix` / `payment_uri` / `matched_via`) + partial unique index; existing DBs self-heal via `DO $$ ... ALTER TABLE ADD COLUMN IF NOT EXISTS ...` with no downtime or manual migration. `address_index` column retained for legacy order viewing.

#### 15 new unit tests + full suite 130/130 green
- `tests/test_usdt_payment_chains.py`: amount suffix precision / retry divergence / URI construction (per-chain assertions) / chain selector env parsing (missing address auto-hidden)

### 🐛 Fixes & cleanup
- **Removed xpub HD derivation path**: `bip_utils` no longer referenced in USDT domain (still used elsewhere); new orders never use derivation
- **TRC20 USDT contract hardcoded address fix**: previous `env.example` default `TXLAQ63Xg1NAzckPwKHvzw7CSEmLMEqcdj` was an invalid placeholder (TronGrid always returned empty result); changed to official TRC20 USDT contract `TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t`

### 🐞 Post-release hotfixes (within v3.0.6)

Production screenshots surfaced two real issues, **fixed in the same version**:

1. **Total decimal drift** (`19.9 → 19.90670000` eight digits)
   `qd_usdt_orders.amount_usdt` column is `NUMERIC(20,8)`, 2 digits wider than `USDT_AMOUNT_SUFFIX_DECIMALS=6`. After `Decimal('19.901234')` quantized to 6 places and written, Postgres pads to `19.90123400` (column width zero-fill); frontend string split showed `19.90 + 123400` as 8 digits, inconsistent with the 6-digit `19.901234` returned at order creation.
   - Added `chains.format_amount_display()`: uniformly quantize API output to `suffix_decimals()` places
   - `_row_to_dict` / `create_order` return / `build_payment_uri` URI amount segment all go through this layer; frontend and backend always see consistent 6 decimal places
   - DB column width kept at `(20,8)` as headroom for future `USDT_AMOUNT_SUFFIX_DECIMALS=8`; no schema migration
2. **Close payment window and reopen = error**
   Original flow created a new order on every “Buy now” click; old pending orders lingered in DB; reopening the window after a slow backend could show a toast error.
   - `create_order` idempotency: first look up active order `(user_id, plan, chain, status='pending', expires_at > now)`; if found, **reuse and return** (same amount, address, remaining expiry) without inserting
   - Return adds `reused: bool`; frontend `confirmChain` shows light toast “Unpaid order detected — showing existing order” when `reused=true`
   - 4 new tests (`test_usdt_payment_idempotency.py`): same request reuse / different chain no reuse / different user no reuse / expired order does not block new order

### 📋 Upgrade notes
1. **Restart only**: DB schema self-migration is in `init.sql`; no manual SQL
2. **Configure env to enable**: set `USDT_TRC20_ADDRESS=` etc. to your main wallet addresses; empty chains are hidden from user options
3. **API keys optional but recommended**: Etherscan / BscScan without keys use anonymous tier (200/day), fine for low volume; Solana defaults to public RPC (enough for small traffic); high traffic — private RPC via Helius / QuickNode (`SOLANA_RPC_URL`)
4. **TRON contract default changed**: after upgrade use new default in `env.example` `TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t`; update old `.env` if it explicitly set the invalid placeholder

### 📂 Key files
| File | Purpose |
|---|---|
| `backend_api_python/app/services/usdt_payment/chains.py` | 4-chain metadata / URI construction / amount suffix |
| `backend_api_python/app/services/usdt_payment/watchers/*.py` | 4-chain incoming payment scanners |
| `backend_api_python/app/services/usdt_payment/service.py` | Order flow + worker |
| `backend_api_python/app/services/usdt_payment_service.py` | Backward-compatible shim |
| `backend_api_python/migrations/init.sql` | `qd_usdt_orders` new columns + partial unique index + self-heal migration |
| `backend_api_python/env.example` | New env vars (4-chain addresses / explorer keys / suffix precision) |
| `backend_api_python/app/routes/billing.py` | New `/usdt/chains`; `/usdt/create` accepts `chain` param |
| `QuantDinger-Vue-src/src/views/billing/index.vue` | Chain selection modal + URI QR + suffix highlight |
| `QuantDinger-Vue-src/src/api/billing.js` | `listUsdtChains()` / `createUsdtOrder(plan, chain)` |
| `backend_api_python/tests/test_usdt_payment_chains.py` | 15 new unit tests |

---

## V3.0.5 (2026-05-13) — Alpaca / Smart Tuning v2 / IS-OOS dual panel / unified broker panel / DB startup self-heal

This release merges two weeks of work: **more rigorous backtesting**, **easier multi-broker management**, and **fewer local deployment pitfalls**. **Backtest rigor** is the headline — smart tuning previously showed +36% headline numbers on the in-sample (IS) segment while applying those params to the full window produced -24%; this silent overfitting is the main fix. Also adds **Alpaca Markets** as the third **traditional broker** adapter, on par with IBKR / MT5.

### 🚀 New Features

#### Alpaca Markets adapter (US stocks / ETF / crypto · paper + live)
Third **traditional broker** integrated into QuantDinger, on par with IBKR / MT5 (from [PR #101](https://github.com/brokermr810/QuantDinger/pull/101)):
- **Coverage**: US stocks, ETFs, crypto spot; both paper (`paper-api.alpaca.markets`) and live (`api.alpaca.markets`) accounts
- **Adapter**: `backend_api_python/app/services/alpaca_trading/` (client / symbols / error normalization / OHLCV)
- **Routes**: `/api/alpaca/connect|status|account|positions|orders|symbols`
- **Multi-tenant**: with IBKR, wired into new `BrokerSessionRegistry` — per-user client, no shared global connection
- **Zero referral noise**: full adapter review — no referral / partner code hidden identifiers

#### Unified broker accounts page (`/broker-accounts`)
Previously IBKR / MT5 / Alpaca / crypto exchange connection entry points were scattered; now merged into one management page:
- Header summary + Alpaca / IBKR / MT5 panels (connection form + account KPIs + positions + open orders + one-click cancel)
- Crypto exchange credentials as a separate card, reusing existing `ExchangeAccountModal`
- Frontend `src/api/broker.js` normalizes differing endpoint structures across brokers

#### Smart Tuning v2 — multi-parameter strategies actually work
Previous “smart tuning” only swept take-profit / stop-loss / leverage (3 dimensions); RSI / MACD / EMA / ATR multi-param strategies were barely tunable. This release makes it true multi-dimensional optimization:
- **P1 · Auto-inferred sweep ranges**: write `# @param rsi_len int 14 RSI period` in the indicator — no manual `range=`; frontend auto-builds grid from default × `[0.5, 0.75, 1, 1.25, 1.75]` (int auto-rounded, deduped)
- **P2 · “Tunable dimensions” panel**: structured tuning card lists all dimensions (risk / position / leverage / `@param` declared / `@param` inferred color badges), live Cartesian product size vs candidate budget; each dimension toggleable
- **P3 · Dimension explosion auto-switches to DE**: when full Cartesian product > candidate budget × 10 (default 480), grid auto-switches to Differential Evolution; blue UI hint “Automatically switched to DE”
- **P4 · Trailing dimensions auto-added**: when `@strategy trailingEnabled=true`, auto-append `trailing.pct` and `trailing.activationPct` sweep dimensions

#### IS-OOS dual panel + split apply actions
Fixes a long-silent overfitting trap (smart tuning +36% headline on 70% train segment, -24% on full window including 30% validation — invisible in UI):
- **Best candidate card** now IS / OOS side-by-side; red alert “OOS degraded X%, possible overfit”
- **Former “Apply best params” split into three actions**:
  - Apply and validate on train segment (reproduce +36% headline)
  - Apply and run on full window (includes OOS — see real performance)
  - Apply params only (no immediate backtest)
- Backend `ExperimentRunnerService._build_best_output` exposes `oosSummary` / `oosScore` / `oosDegradation` / `oosOverfit` to frontend

#### Crypto timeframe backend resample
When an exchange lacks a native timeframe (e.g. OKX has no 30m), backend fetches finer OHLCV and exchange-aligned resample. Frontend unchanged; 13 new unit tests (from [PR #104](https://github.com/brokermr810/QuantDinger/pull/104)).

### 🛠️ Engineering Improvements

#### Database startup self-management (fixes local PG `relation does not exist` / `permission denied` spam)
Previously local PostgreSQL (non-Docker) required manual `psql -f migrations/init.sql` or workers flooded logs with `relation "pending_orders" does not exist` / `qd_strategy_positions permission denied`; Docker avoided this (entrypoint runs init.sql). Now:
- `init_database()` **auto-applies** `migrations/init.sql` on every startup (idempotent `CREATE TABLE IF NOT EXISTS`; safe to re-run in Docker)
- Lightweight **permission self-check** after startup: `SELECT 1 LIMIT 0` on five key tables; any `InsufficientPrivilege` triggers headline banner with `ALTER TABLE OWNER` fix recipe instead of drowning in worker logs
- New env `SKIP_AUTO_MIGRATE=true` escape hatch for external schema management (Flyway / Liquibase / manual DBA)

#### Multi-tenant broker sessions (`BrokerSessionRegistry`)
`backend_api_python/app/utils/broker_session.py` provides client registry cached by `(user_id, broker_name)` with `threading.Lock`. Replaces shared global `_client` for IBKR / Alpaca with per-user isolation — no “one user reconnect kicks everyone offline”.

#### OAuth `FRONTEND_URL` multi-frontend support
`backend_api_python/app/services/oauth_service.py` parses `FRONTEND_URL` as comma-separated list: first entry = default post-login redirect origin; all entries = OAuth redirect whitelist. One backend can serve `ai.quantdinger.com` + `m.quantdinger.com`.

#### Default indicators trimmed + SuperTrend example internationalized
New users previously got 4 built-in indicators; now **1 high-quality SuperTrend example**: full English comments + `@param` range annotations + standard Wilder ATR / path-dependent SuperTrend — ready to run and demonstrates recommended multi-param strategy style.

### 🐛 Bug Fixes

- **Indicator market overall scores all zero** (V3.0.4, merged here): `community_service.py` wrong field (`'overall'` vs `'overallScore'`); all listed strategies showed 0; 4 regression tests added
- **Risk-return scatter axis unit wrong**: `totalReturn` / `maxDrawdown` already percentages but multiplied by 100 again — “thousands of percent”; fixed
- **`runBacktest` date range override**: signature adds `options.dateRangeOverride` for “apply and validate on train segment” reusing full `runBacktest` path
- **Missing i18n keys**: tuning dimension panel showed raw `indicatorIde.stopLossPct` instead of “Stop loss (%)” — added 4 keys × 10 locales; label resolution uses `$te()` (translation exists) guard
- **Smart Tuning 12 IS/OOS i18n keys** synced to 10 locales

### ⚙️ Configuration Changes

| Variable | Default | Purpose |
|---|---|---|
| `SKIP_AUTO_MIGRATE` | `false` | Skip auto-apply of `migrations/init.sql` on startup (set true for external schema management) |
| `FRONTEND_URL` | `http://localhost:8080` | Comma-separated origins; all in OAuth whitelist; first = default redirect |

### ✅ Tests

14 new regression tests; full suite **115/115 passing**:
- `tests/test_db_bootstrap.py` — 6 (auto-migrate idempotency / missing `init.sql` fallback / SQL failure no crash / `_verify_table_access` green / multi-permission failure banner / `SKIP_AUTO_MIGRATE` escape)
- `tests/test_experiment_services.py::test_evolution_sweeps_indicator_level_params` — 1 (`indicator_params.atr_period` snapshot + overrides path)
- `tests/test_experiment_best_output.py` — 3 (OOS metrics: `oosSummary` / `oosScore` / `oosDegradation` / `oosOverfit`)
- `tests/test_market_indicator_score.py` — 4 (`'overall'` → `'overallScore'` typo regression)

ESLint green on all touched `.vue` / `.js` files.

### 📦 Compatibility / Upgrade Notes

- **Zero breaking changes**: all backend routes, env vars, DB schema backward compatible
- **Database**: first startup auto-applies `migrations/init.sql`; if PG user is not table owner, startup banner points to `ALTER TABLE ... OWNER TO <user>;`
- **Frontend**: build `QuantDinger-Vue-src` and replace `frontend/dist` (repo `frontend/dist` includes this build)
- **Alpaca**: add `alpaca-py>=0.30.0` to production `requirements.txt`, or `pip install alpaca-py` locally (already in `backend_api_python/requirements.txt`)

### 🗂️ Files Changed Overview

- Backend: `app/services/alpaca_trading/*`, `app/routes/alpaca.py`, `app/services/experiment/runner.py`, `app/services/builtin_indicators.py`, `app/services/oauth_service.py`, `app/utils/db.py`, `app/utils/broker_session.py`, `app/services/community_service.py`
- Frontend: `src/views/indicator-ide/index.vue`, `src/views/broker-accounts/*`, `src/api/broker.js`, `src/locales/lang/*.js` (10 locales + 16 new keys)
- Docs: `README.md` + 6× `docs/README_*.md` (badge → 3.0.5 / Alpaca added), `env.example` (new `SKIP_AUTO_MIGRATE`)

---

## V3.1.0 (2026-05-02) — AI Agent Gateway / MCP HTTP / SSE progress stream / Admin UI

Extends QuantDinger from a **Web product for human users only** to a **dual-stack product for humans and AI agents**. Equips Agent runtimes like OpenClaw / NanoBot / Claude Code / Cursor / Codex with: a controlled HTTP gateway, scope-based fine-grained authorization, async jobs + live progress, MCP access, Admin ops panel, and a machine-readable contract (OpenAPI 3.0). **All Agent entry points deny live trading by default** — T-class (Trading) tokens use the paper order book even when granted to agents; real exchange access requires an explicit server-level switch.

### 🚀 New Features

#### Agent Gateway (`/api/agent/v1`)
Brand-new machine-to-machine API fully isolated from human JWT:
- **Token model**: admins issue one-time `qd_agent_xxxx` tokens; only SHA-256 hash stored; custom scopes (`R / W / B / N / C / T`), market/instrument allowlists, `paper_only`, rate limits, expiry
- **Capability classes**: each endpoint declares one risk class — **R**(Read) / **W**(Workspace write) / **B**(Backtest) / **N**(Notify) / **C**(Credentials, admin only) / **T**(Trading, paper-only by default)
- **Audit log**: every Agent call (success, denial, 429) appended to `qd_agent_audit` with route, scope class, status, duration, redacted request/response summary
- **Rate limiting + idempotency**: in-process sliding window per token; W/B/T support `Idempotency-Key` — duplicate key returns original job without re-execution
- **Async jobs**: long tasks (backtest, experiment pipeline, AI optimize) queued via in-process `ThreadPoolExecutor` into `qd_agent_jobs`; clients use submit → poll / SSE; worker count and live switch via env
- **Tenant isolation**: `token → user_id → resources`; no Agent sees another user’s strategies, orders, audit, or jobs

Implemented endpoints (1:1 with `docs/agent/agent-openapi.json`):
| Category | Path | Class | Description |
|---|---|---|---|
| Health | `GET /health` · `GET /whoami` | – / R | Public liveness / token introspection |
| Markets | `GET /markets` · `/markets/{m}/symbols` · `/klines` · `/price` | R | Market data |
| Strategies | `GET /strategies` · `GET/POST/PATCH /strategies/{id}` | R / W | Setting status to `running` requires T |
| Backtests | `POST /backtests` | B | Async; returns `job_id` |
| Experiments | `POST /experiments/{regime/detect, pipeline, structured-tune, ai-optimize}` | B | regime sync; others async |
| Jobs | `GET /jobs` · `GET /jobs/{id}` · `GET /jobs/{id}/stream` | R | List / get / **SSE live stream** |
| Portfolio | `GET /portfolio/positions` · `/portfolio/paper-orders` | R | Positions / paper fills |
| Quick-Trade | `POST /quick-trade/orders` · `POST /quick-trade/kill-switch` | T | Paper book by default |
| Admin | `POST/GET /admin/tokens` · `DELETE /admin/tokens/{id}` · `GET /admin/audit` | – | Human JWT only |

#### SSE live progress (`GET /api/agent/v1/jobs/{id}/stream`)
Long jobs (`ai-optimize` / `structured-tune` / multi-round backtest pipelines) let LLM clients watch progress live:
- Frame types: `snapshot` (baseline first frame) → `progress` (each runner `on_progress`) → `ping` (~15s heartbeat against proxy timeouts) → `result` (terminal state then close)
- Resume: `?since=<seq>` or standard `Last-Event-ID` header
- Completed jobs emit `snapshot + result` then close — no dual client logic
- Runner contract: `runner(payload, on_progress)` second arg auto-detected; events to SSE subscribers and `qd_agent_jobs.progress` JSONB (reconnect reads latest snapshot)

#### MCP Server (`mcp_server/` — published on PyPI: [`quantdinger-mcp`](https://pypi.org/project/quantdinger-mcp/))
Standalone Python package wrapping Agent Gateway R / B subset as Model Context Protocol tools:
- One-line install (any machine, no repo clone): `uvx quantdinger-mcp` / `pipx install quantdinger-mcp` / `pip install quantdinger-mcp`
- Three transports via `QUANTDINGER_MCP_TRANSPORT`:
  - `stdio` (default) — desktop IDE (Cursor / Claude Code) subprocess
  - `sse` — SSE-only clients
  - `streamable-http` — new MCP HTTP protocol; cloud Agent / remote IDE direct connect
- HTTP mode also reads `QUANTDINGER_MCP_HOST` / `QUANTDINGER_MCP_PORT`
- Agent tokens only — **never use human JWT or exchange keys**

#### Frontend Admin UI: Agent Tokens panel (admin only)
Integrated into existing Vue admin (alongside User Management / System Settings):
- Route `/agent-tokens`, permission `permission: ['admin']`
- **Tokens tab**: list (color scope tags, market allowlist, paper-only / live-eligible, last used) + revoke
- **Issue modal**: scope multi-select, market/instrument allowlists, rate, expiry days, `paper_only`; red warning when T checked but paper-only off — needs `AGENT_LIVE_TRADING_ENABLED=true`
- **Reveal modal**: full token **shown once** with copy to clipboard
- **Audit tab**: method / route / scope class / status / duration; status color scale (5xx red, 429 orange, 4xx amber, 2xx green)
- i18n: ~30 `agentTokens.*` keys in `en-US` + `zh-CN`; other locales English fallback

#### System architecture diagram
End-to-end architecture diagram at top of README (`docs/screenshots/architecture.png`); EN + CN READMEs synced.

### 🛠️ Tooling / Docs

- `docs/agent/AGENT_ENVIRONMENT_DESIGN.md` — three-layer contract (Documentation → Command → Machine Interface) for **code-writing** Agents (Cursor / Claude Code / Codex)
- `docs/agent/AI_INTEGRATION_DESIGN.md` — Agent design doc consuming QuantDinger as a **product** (personas, capability classes, security, roadmap, progress table). Currently v0.3
- `docs/agent/AGENT_QUICKSTART.md` — ops manual: token issuance, `/whoami`, market data, backtest, SSE watch, MCP setup with step-by-step `curl` examples
- `docs/agent/agent-openapi.json` — OpenAPI 3.0 contract; all `/api/agent/v1/...` paths + `x-scope-class` extension
- `.cursor/skills/quantdinger-agent-workflow/SKILL.md` — Skill for Cursor / Claude Code: red lines, entry points, validation in this repo
- `mcp_server/README.md` — MCP three-transport deployment examples

### ⚙️ Configuration

New optional env vars (secure defaults):

| Variable | Default | Purpose |
|---|---|---|
| `AGENT_JOBS_MAX_WORKERS` | `4` | Agent async job thread pool size |
| `AGENT_LIVE_TRADING_ENABLED` | `false` | **Server-level live trading switch**. Even if token `paper_only=false`, paper only without this |
| `QUANTDINGER_MCP_TRANSPORT` | `stdio` | MCP client transport (`stdio` / `sse` / `streamable-http`) |
| `QUANTDINGER_MCP_HOST` | `127.0.0.1` | MCP HTTP bind host |
| `QUANTDINGER_MCP_PORT` | `8000` | MCP HTTP bind port |

### ✅ Tests

- `backend_api_python/tests/test_agent_v1.py` — 9 cases: missing token / unknown / inactive / expired / insufficient scope / rate limit / token format, etc.
- `backend_api_python/tests/test_agent_jobs_progress.py` — 5 cases: runner signature detection, ordered accumulation, `since_seq` resume, idle timeout, cross-thread delivery
- `mcp_server/tests/test_transport_resolution.py` — 4 cases: default transport, alias resolution, unknown graceful exit, HTTP settings shim. Runs when `mcp` installed; else `importorskip`

Backend **58 passed** (53 Gateway + 5 SSE).

### 🗄️ Database Migration

This release adds 4 tables + 1 JSONB column, all **auto-created idempotently** by `agent_auth._ensure_schema` on first Agent request — **existing deployments work with no action**. Recommended: run SQL below on upgrade to ensure indexes.

```sql
-- ============================================================
-- QuantDinger V3.1.0 Database Migration
-- Agent Gateway: tokens / async jobs / audit / paper orders
-- ============================================================

-- 1. Agent tokens (one row per issued token; only the SHA-256 hash is stored)
CREATE TABLE IF NOT EXISTS qd_agent_tokens (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE CASCADE,
    name VARCHAR(80) NOT NULL,
    token_prefix VARCHAR(24) NOT NULL,
    token_hash VARCHAR(128) NOT NULL,
    scopes TEXT NOT NULL DEFAULT 'R',
    markets TEXT NOT NULL DEFAULT '*',
    instruments TEXT NOT NULL DEFAULT '*',
    paper_only BOOLEAN NOT NULL DEFAULT TRUE,
    rate_limit_per_min INTEGER NOT NULL DEFAULT 60,
    status VARCHAR(20) NOT NULL DEFAULT 'active',
    expires_at TIMESTAMP,
    last_used_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_tokens_hash   ON qd_agent_tokens(token_hash);
CREATE INDEX        IF NOT EXISTS idx_agent_tokens_user   ON qd_agent_tokens(user_id);
CREATE INDEX        IF NOT EXISTS idx_agent_tokens_status ON qd_agent_tokens(status);

-- 2. Agent async jobs (backtests / experiments / ai-optimize / ...)
CREATE TABLE IF NOT EXISTS qd_agent_jobs (
    id BIGSERIAL PRIMARY KEY,
    job_id VARCHAR(40) NOT NULL UNIQUE,
    user_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE CASCADE,
    agent_token_id INTEGER REFERENCES qd_agent_tokens(id) ON DELETE SET NULL,
    kind VARCHAR(40) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'queued',
    request JSONB NOT NULL DEFAULT '{}'::jsonb,
    result JSONB,
    error TEXT,
    progress JSONB,                      -- NEW in V3.1.0: latest snapshot for SSE cold reconnects
    idempotency_key VARCHAR(120),
    created_at TIMESTAMP DEFAULT NOW(),
    started_at TIMESTAMP,
    finished_at TIMESTAMP
);
-- Safe to run even if the table existed (e.g. _ensure_schema already created
-- the V3.0 shape without `progress`):
ALTER TABLE qd_agent_jobs ADD COLUMN IF NOT EXISTS progress JSONB;

CREATE INDEX        IF NOT EXISTS idx_agent_jobs_user   ON qd_agent_jobs(user_id);
CREATE INDEX        IF NOT EXISTS idx_agent_jobs_status ON qd_agent_jobs(status);
CREATE INDEX        IF NOT EXISTS idx_agent_jobs_kind   ON qd_agent_jobs(kind);
CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_jobs_idem
    ON qd_agent_jobs(agent_token_id, kind, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

-- 3. Append-only audit log (every agent call, including denials)
CREATE TABLE IF NOT EXISTS qd_agent_audit (
    id BIGSERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    agent_token_id INTEGER,
    agent_name VARCHAR(80),
    route VARCHAR(160) NOT NULL,
    method VARCHAR(8) NOT NULL,
    scope_class VARCHAR(4) NOT NULL,
    status_code INTEGER NOT NULL,
    idempotency_key VARCHAR(120),
    request_summary JSONB,               -- redacted by _redact() before insert
    response_summary JSONB,
    duration_ms INTEGER,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_agent_audit_user  ON qd_agent_audit(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_audit_token ON qd_agent_audit(agent_token_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_audit_class ON qd_agent_audit(scope_class);

-- 4. Paper-only ledger so T-class tokens can simulate without ever
--    touching live exchange credentials.
CREATE TABLE IF NOT EXISTS qd_agent_paper_orders (
    id BIGSERIAL PRIMARY KEY,
    order_uid VARCHAR(40) NOT NULL UNIQUE,
    user_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE CASCADE,
    agent_token_id INTEGER REFERENCES qd_agent_tokens(id) ON DELETE SET NULL,
    market VARCHAR(40) NOT NULL,
    symbol VARCHAR(60) NOT NULL,
    side VARCHAR(8) NOT NULL,
    order_type VARCHAR(16) NOT NULL DEFAULT 'market',
    qty DECIMAL(28,10) NOT NULL,
    limit_price DECIMAL(28,10),
    fill_price DECIMAL(28,10),
    fill_value DECIMAL(28,10),
    status VARCHAR(16) NOT NULL DEFAULT 'filled',
    note TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_agent_paper_orders_user  ON qd_agent_paper_orders(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_paper_orders_token ON qd_agent_paper_orders(agent_token_id);

DO $$ BEGIN RAISE NOTICE '✅ QuantDinger V3.1.0 agent gateway schema migration completed!'; END $$;
```

**Docker one-liner example:**

```bash
docker compose exec -T postgres psql -U quantdinger -d quantdinger \
  -f /app/migrations/init.sql   # fully idempotent, safe to re-run
```

Or save the SQL above to a file first:

```bash
docker cp /path/to/v3.1.0_agent_gateway.sql quantdinger-db:/tmp/migrate.sql
docker compose exec -T postgres psql -U quantdinger -d quantdinger -f /tmp/migrate.sql
```

**Migration Notes:**
- All statements use `IF NOT EXISTS` — **safe to re-run**
- No existing data modified or deleted
- Until `AGENT_LIVE_TRADING_ENABLED=true`, T-class calls only write `qd_agent_paper_orders`; never triggers `TradingExecutor`
- All 4 tables tenant-isolated by `user_id`; user delete cascades token / job / paper-order; audit is soft-linked (`agent_token_id INTEGER`, no FK cascade) for post-hoc accountability

### 📦 Files Changed

**Backend (`backend_api_python/`):**
- `migrations/init.sql` — new Section 30 “Agent Gateway”, 4 tables + `progress` JSONB + indexes
- `app/utils/agent_auth.py` — token auth, scope/allowlist, rate limit, `_audit + _redact`, `with_idempotency`, `_ensure_schema` runtime DDL
- `app/utils/agent_jobs.py` — async job runner, `on_progress` detection, SSE event ring (`deque(maxlen=200)` + `threading.Event`), `stream_progress(...)` generator, `progress` persistence
- `app/routes/agent_v1/__init__.py` + `_helpers.py` + `health.py` + `markets.py` + `strategies.py` + `backtests.py` + `experiments.py` + `jobs.py` (incl. SSE) + `portfolio.py` + `quick_trade.py` + `admin.py`
- `app/routes/__init__.py` — register `agent_v1_bp`
- `env.example` — new `AGENT_JOBS_MAX_WORKERS`, `AGENT_LIVE_TRADING_ENABLED`
- `tests/test_agent_v1.py`, `tests/test_agent_jobs_progress.py`

**MCP server (new package):**
- `mcp_server/pyproject.toml`, `mcp_server/README.md`
- `mcp_server/src/quantdinger_mcp/{__init__.py, server.py}` — `FastMCP` + `httpx`, three transports via env
- `mcp_server/tests/test_transport_resolution.py`

**Frontend (`QuantDinger-Vue-src/` + synced to `frontend/dist/`):**
- `src/api/agent.js` — Agent admin API client
- `src/views/agent-tokens/index.vue` — Tokens / Audit dual-tab page
- `src/config/router.config.js` — new route `/agent-tokens`, `permission: ['admin']`
- `src/locales/lang/{en-US,zh-CN}.js` — `menu.agentTokens` + ~30 `agentTokens.*` keys
- `frontend/dist/` — rebuilt (101 files, ~18.9 MB; agent-tokens route + zh-CN i18n)

**Docs:**
- `docs/agent/AGENT_ENVIRONMENT_DESIGN.md`, `docs/agent/AI_INTEGRATION_DESIGN.md` (v0.3), `docs/agent/AGENT_QUICKSTART.md`, `docs/agent/agent-openapi.json`, `docs/agent/README.md`
- `.cursor/skills/quantdinger-agent-workflow/SKILL.md`
- `README.md` + `docs/README_CN.md` — architecture diagram at top + Agent doc nav links
- `docs/screenshots/architecture.png` — end-to-end architecture diagram

### 🗑️ Removed

- `.github/dependabot.yml` — Dependabot disabled to avoid ~11 noisy weekly branches (npm `vue-cli` v6 / webpack v5 upgrades clash with Vue 2 + webpack 4 stack)

### ⚠️ Operational notes

1. **First startup may skip SQL**: Agent Gateway auto-creates tables on first request (`_ensure_schema`). Still recommended to run migration above on upgrade for complete indexes
2. **Live switch off by default**: without `AGENT_LIVE_TRADING_ENABLED=true`, T-class tokens with `paper_only=false` still use `qd_agent_paper_orders` only. Product red line — do not weaken in docs/code
3. **Issued tokens non-recoverable**: DB stores SHA-256 hash only; after reveal modal closes, token is lost — revoke and re-issue
4. **MCP HTTP production**: `streamable-http` binds `127.0.0.1` by default; for external access set `QUANTDINGER_MCP_HOST=0.0.0.0` behind nginx / reverse proxy — **Agent-token clients only**

---

## V3.0.2 (2026-04-11) — Full locale file backfill (AI auto-translation)

### 🌍 i18n

Previously, aside from `zh-CN` / `en-US`, the other 7 locale files had only ~2000/4240 keys (~48% coverage); many UI fields fell back to English or raw key names. This release used DeepSeek to batch-translate all missing keys and write them back:

| Language | Before | After | New entries |
|---|---|---|---|
| `ar-SA` Arabic  | 2029 | **4573** | 2541 |
| `de-DE` German  | 2077 | **4573** | 2491 (+patch) |
| `en-US` English | 4424 | **4498** | 72 |
| `fr-FR` French  | 2029 | **4573** | 2539 (+patch) |
| `ja-JP` Japanese| 2033 | **4573** | 2537 (+patch) |
| `ko-KR` Korean  | 2034 | **4573** | 2537 (+patch) |
| `th-TH` Thai    | 2029 | **4573** | 2541 (+patch) |
| `vi-VN` Vietnamese | 1759 | **4495** | 2734 (+patch) |
| `zh-TW` Traditional | 3741 | **4499** | 758 |

All 9 locale files vs `zh-CN` baseline: **missing = 0** ✅

### 🛠️ Tooling (new)

- **`scripts/i18n-diff.js`** — scan all locale files; report missing/extra keys vs `zh-CN`; `--detail`, `--lang=xx-YY` for specifics
- **`scripts/i18n-fill-ai.js`** — incremental AI translation. DeepSeek / Anthropic / OpenAI / OpenRouter; batch (default 80) + concurrency (default 6) + local cache (`scripts/.i18n-cache/`) + auto backup (`*.js.bak`); safe append write-back. Failed batches: 3 retries + partial retain. Preserves `{foo}`, `<code>…</code>`, `\n`, HTML tags, `BTC/ETH/USDT/AI/MT5`, etc.
- **`scripts/i18n-patch-specials.js`** — one-shot fill for keys AI script misses: empty strings, nested objects (`trading-assistant.brokerNames`), Chinese measure-word singles (`dashboard.unit.trades` / `.strategies` left empty in ES/TH/VI)
- **`scripts/README.md`** — toolchain docs: usage, API keys, cost estimates, quality tips
- **`.gitignore`** — ignore `scripts/.i18n-cache/` and `QuantDinger-Vue-src/src/locales/lang/*.bak`

### Translation quality

Domain terms use standard industry wording: grid (`neutral/long/short`), Maker/Taker limit/market, add/close position, TP/SL, floating PnL, equity, position, pending/filled orders, etc. Placeholders / `<code>` / code samples preserved. Batch failure rate < 0.2%; failed keys patched by specials script.

### ⚠️ Known follow-ups

- **`ja-JP` / `zh-TW` etc.: some keys “exist but value is English” not re-translated**: script only fills fully missing keys, does not overwrite existing values. Fixing placeholder English needs a separate “detect non-target language and re-translate” pass.

### 🗄️ Database Migration

None.

### 📦 Files Changed

- `QuantDinger-Vue-src/src/locales/lang/{ar-SA,de-DE,en-US,fr-FR,ja-JP,ko-KR,th-TH,vi-VN,zh-TW}.js`
- `scripts/i18n-diff.js`、`scripts/i18n-fill-ai.js`、`scripts/i18n-patch-specials.js`、`scripts/README.md`
- `.gitignore`

---

## V3.0.2 (2026-04-11) — Trading bot end-to-end fixes (Grid / Martingale / Trend / DCA)

### 🐛 Bug Fixes — Trading bots

End-to-end audit and fixes for all four bot types (grid / martingale / trend / DCA) from frontend config, script templates, backend execution, to list/detail data:

- **[P0-1] Editing bot clears runtime state**: `StrategyService.update_strategy` previously replaced entire `trading_config` from payload, wiping `script_runtime_state` (martingale `layer`/`total_cost`, grid `bp/sp/prev_price`, DCA `total_qty`, etc.) — restart after param change felt like a new bot. Now shallow merge `{**existing, **incoming}` and protect `script_runtime_state`, `last_signal_time`, `last_execution_time`, `bot_runtime_stats`, etc.
- **[P0-2] Grid shorts not budget-controlled**: old `total_spent` only accumulated on buys; sell-to-open-short (neutral/short modes) neither checked nor accumulated — futures could grow shorts until liquidation. Grid script rewritten with independent `long_exposure` / `short_exposure`; BUY offsets shorts then opens long (skip if long side over budget); SELL symmetric
- **[P0-3] Martingale/trend default `maker` limit orders caused missed fills and duplicates**: martingale layers depend on prior fill to update `last_entry_price`; unfilled limits re-sent same price next bar — double/multiple orders on one open. Wizard `buildPayload` forces `order_mode='market'` for `martingale` / `trend`; grid/DCA keep user choice (default maker for lower fees)
- **[P0-4] Grid/DCA multi-reduce same tick local position tracking wrong**: `_script_orders_to_execution_signals` passed USDT notional directly to `ctx.position.reduce_position/add_position/open_position` (qty-based internally) — two sells same tick: second misclassified as open short not continue close long → wrong `open_short`. Fix: convert USDT via `usdt * leverage / ref_price` to approx qty before updating local ctx.position (live order qty still from `_execute_signal` unchanged)
- **[P0-5] DCA frequency swallowed by bar period**: `intervalBars = round(freqMin / tfMin)` when `freq<tf` (e.g. hourly on 4h chart) rounds to 0, `max(1,0)=1` → buy every bar (4h once). DCA script now **timestamp-based** `INTERVAL_SEC = freqMin * 60`, `now - last_buy_ts >= INTERVAL_SEC` — decoupled from bar period

### 🔧 Improvements

- **[P1] Bot list/detail return runtime metrics**: `list_strategies` / `get_strategy` batch GROUP BY `qd_strategy_trades.profit-commission` and `qd_strategy_positions.unrealized_pnl`; response includes `realized_pnl` / `unrealized_pnl` / `total_pnl` / `current_equity` — frontend KPI/cards no longer assemble manually
- **[P1] Trend bot sizing from live equity**: `_hydrate_script_ctx_from_positions` refreshes `ctx.balance` / `ctx.equity` to `initial_capital + realized + unrealized`; trend script `amt = ctx.balance * POS_PCT` tracks account NAV not stuck at initial capital
- **[P1] DCA auto-reset after external close**: each bar DCA checks `buy_count>0 && total_qty>0 but ctx.position empty` → manual/SL close, zero cumulative state, next DCA round starts clean
- **[P1] Grid/DCA frontend validation**: `GridConfig` — bounds order, geometric grid lower>0, per-cell × grid count ≤ initial capital; `DCAConfig` — single amount ≤ total budget. i18n for 10 languages

### 🗄️ Database Migration

No new columns/tables; code-only fixes. Existing deployments **need no SQL**.

### 📦 Files Changed

- `backend_api_python/app/services/strategy.py` — `update_strategy` merge, `_compute_runtime_metrics`, list/detail runtime metrics
- `backend_api_python/app/services/trading_executor.py` — `_script_orders_to_execution_signals` USDT→qty, `_hydrate_script_ctx_from_positions` balance/equity refresh
- `QuantDinger-Vue-src/src/views/trading-bot/components/BotCreateWizard.vue` — martingale/trend forced market orders
- `QuantDinger-Vue-src/src/views/trading-bot/components/botScriptTemplates.js` — grid dual budget, DCA time-based interval + external close reset
- `QuantDinger-Vue-src/src/views/trading-bot/components/configs/GridConfig.vue`, `DCAConfig.vue` — param validation
- `QuantDinger-Vue-src/src/locales/lang/*.js` — 4 new validation strings × 10 languages

---

## V3.0.2 (2026-04-17) — Indicator community “Sync code” + Martingale / backtest stability

### 🚀 New Features

- **Indicator community · Sync code**: indicator detail modal adds “Sync code” beside “Use now” for purchasers. After publisher updates and re-lists, buyers pull latest code to local copy in one click; frontend has `Tooltip`, confirm dialog, orange “update available” badge; dark theme styled
  - New endpoint: `POST /api/community/indicators/<id>/sync`
  - Detail `GET /api/community/indicators/<id>` adds: `has_update`, `local_copy_id`
  - Local copy linked to market original via new `qd_indicator_codes.source_indicator_id`; legacy data name-matched and backfilled on first sync
- **Trading bots · Parameter normalization**: Martingale / Grid / Trend / DCA unified semantics; create confirm, list, detail aligned; backend `bot_display` unified structure; frontend mapping simplified
- **Martingale duplicate open fix**: strategy start instant double-order fixed (signal dedup + in-loop notional/position check)

### 🐛 Bug Fixes

- **Backtest date range ignored**: changing start/end but same results — fixed. Root cause: `_fetch_kline_data` fell back to `df.tail(N)` when upstream coverage incomplete, ignoring `start_date`. Now strict filter on requested ∩ available range; no overlap → error; fallback logs `WARNING` and sets `fallback=True`. New diagnostic logs: `[BacktestRequest]`, `[Backtest] … requested/upstream/effective`, `[CryptoKline] …`
- **Backtest Buy/Sell markers misaligned on chart**: after indicator IDE backtest, B/S markers could shift one bar (worse in MTF). Two causes:
  1. With MTF, backend exec_tf switches to `1m` or `5m`; `trade.time` is exec_tf timestamp; chart shows user signal TF (e.g. `1h`)
  2. Frontend nearest-snap: SL/TP/trailing triggers in second half of signal bar snap to **next** bar

  Fixes:
  - Backend `_simulate_trading_mtf` adds `bar_time` per trade — floor exec_tf to signal TF for **chart bar** start (UTC, `'%Y-%m-%d %H:%M'`)
  - Frontend `renderBacktestSignals` **prefers `trade.bar_time`**; nearest → **floor-snap** (last bar containing time) — eliminates ±1 bar offset
  - Non-MTF unchanged: `trade.time` equals signal bar time; fallback to `trade.time` still correct
  - Files: `backend_api_python/app/services/backtest.py`, `QuantDinger-Vue-src/src/views/indicator-ide/index.vue`

### 🗄️ Database Migration

One new column + index for indicator community “Sync code” local copy lookup:

```sql
-- 1. New column: buyer local copy -> market original indicator link (soft FK, NULL for legacy)
ALTER TABLE qd_indicator_codes
    ADD COLUMN IF NOT EXISTS source_indicator_id INTEGER;

CREATE INDEX IF NOT EXISTS idx_indicator_codes_source
    ON qd_indicator_codes USING btree (source_indicator_id);

-- 2. Optional backfill: write source_indicator_id for existing purchased copies by name
--    Safe: only is_buy=1 AND source_indicator_id IS NULL, match (buyer_id, original name)
UPDATE qd_indicator_codes lc
SET    source_indicator_id = p.indicator_id
FROM   qd_indicator_purchases p
JOIN   qd_indicator_codes orig ON orig.id = p.indicator_id
WHERE  lc.user_id = p.buyer_id
  AND  lc.is_buy = 1
  AND  lc.source_indicator_id IS NULL
  AND  lc.name = orig.name;
```

**Already executed in dev Docker** (`ALTER TABLE` + `CREATE INDEX` success, backfill `UPDATE 4`). Fresh DBs from current `migrations/init.sql` include column — no repeat needed.

**Manual run on existing DB (Docker one-liner):**

```bash
docker compose exec -T postgres psql -U quantdinger -d quantdinger <<'SQL'
ALTER TABLE qd_indicator_codes ADD COLUMN IF NOT EXISTS source_indicator_id INTEGER;
CREATE INDEX IF NOT EXISTS idx_indicator_codes_source ON qd_indicator_codes USING btree (source_indicator_id);
UPDATE qd_indicator_codes lc
SET source_indicator_id = p.indicator_id
FROM qd_indicator_purchases p
JOIN qd_indicator_codes orig ON orig.id = p.indicator_id
WHERE lc.user_id = p.buyer_id
  AND lc.is_buy = 1
  AND lc.source_indicator_id IS NULL
  AND lc.name = orig.name;
SQL
```

> `CommunityService.__init__` also runs `ADD COLUMN IF NOT EXISTS` on startup as redundant safeguard (backward compatible).

### 🎨 Frontend / i18n

- `QuantDinger-Vue-src/package.json`, `src/config/defaultSettings.js`, `src/layouts/BasicLayout.vue` version `3.0.1 → 3.0.2`; `README.md` and `docs/README_CN.md` badges synced
- `zh-CN / zh-TW / en-US`: 12 new `community.sync*` / `community.hasUpdate` / `community.already_latest` i18n keys; other locales English fallback
- Re-ran `npm run build`, synced `dist/` to `frontend/dist/`, `docker compose build frontend`
- **Patch**: after Buy/Sell marker fix, again `npm run build` + sync `frontend/dist/` + `docker compose build backend frontend && up -d backend frontend`; no extra DB changes

---

## 2026-04-07 — Database: `qd_market_symbols` A-share / H-share hot symbols seed

Already executed in running PostgreSQL inside **Docker** (`INSERT 0 20`). **Fresh DBs** initialized from current repo `migrations/init.sql` include the same seed — no repeat needed.

**Manual run on existing DB (equivalent SQL, idempotent, `ON CONFLICT DO NOTHING`):**

```sql
INSERT INTO qd_market_symbols (market, symbol, name, exchange, currency, is_active, is_hot, sort_order) VALUES
('CNStock', '600519', '贵州茅台', 'SSE', 'CNY', 1, 1, 100),
('CNStock', '600036', '招商银行', 'SSE', 'CNY', 1, 1, 99),
('CNStock', '601318', '中国平安', 'SSE', 'CNY', 1, 1, 98),
('CNStock', '600900', '长江电力', 'SSE', 'CNY', 1, 1, 97),
('CNStock', '601899', '紫金矿业', 'SSE', 'CNY', 1, 1, 96),
('CNStock', '000858', '五粮液', 'SZSE', 'CNY', 1, 1, 95),
('CNStock', '000333', '美的集团', 'SZSE', 'CNY', 1, 1, 94),
('CNStock', '002594', '比亚迪', 'SZSE', 'CNY', 1, 1, 93),
('CNStock', '300750', '宁德时代', 'SZSE', 'CNY', 1, 1, 92),
('CNStock', '000001', '平安银行', 'SZSE', 'CNY', 1, 1, 91),
('HKStock', '00700', '腾讯控股', 'HKEX', 'HKD', 1, 1, 100),
('HKStock', '09988', '阿里巴巴-W', 'HKEX', 'HKD', 1, 1, 99),
('HKStock', '03690', '美团-W', 'HKEX', 'HKD', 1, 1, 98),
('HKStock', '01810', '小米集团-W', 'HKEX', 'HKD', 1, 1, 97),
('HKStock', '00939', '建设银行', 'HKEX', 'HKD', 1, 1, 96),
('HKStock', '01299', '友邦保险', 'HKEX', 'HKD', 1, 1, 95),
('HKStock', '02318', '中国平安', 'HKEX', 'HKD', 1, 1, 94),
('HKStock', '00388', '香港交易所', 'HKEX', 'HKD', 1, 1, 93),
('HKStock', '00883', '中国海洋石油', 'HKEX', 'HKD', 1, 1, 92),
('HKStock', '01398', '工商银行', 'HKEX', 'HKD', 1, 1, 91)
ON CONFLICT (market, symbol) DO NOTHING;
```

**Docker one-liner (file must be UTF-8):**

```bash
docker cp backend_api_python/migrations/<your>.sql quantdinger-db:/tmp/migrate.sql
docker compose exec -T postgres psql -U quantdinger -d quantdinger -f /tmp/migrate.sql
```

---

## V3.0.1 (2026-04-05) — Frontend / docs

- **Frontend version**: private Vue repo `package.json`, footer display, and `frontend/VERSION` unified at **3.0.1**.
- **Docs**: root `README.md` and `docs/README_CN.md` add QuantDinger exchange referral signup links (same as Profile “Open account”); version badge → 3.0.1.
- **Backtest Center**: dark theme icon and “Add symbol” modal styling aligned (`a-icon`, chart title area, Modal mount layer).

---

## V2.2.4 (2026-04-05)

### 🚀 New Features

- **Real strategy backtest main path**: new backtest entry by `strategyId` for saved `IndicatorStrategy` and `ScriptStrategy` — not just “fetch indicator and run indicator backtest again”
- **Strategy snapshot resolution layer**: unified backend snapshot parsing of `indicator_config`, `trading_config`, `strategy_code` into standard backtest input
- **Strategy backtest history and detail**: runs distinguish `indicator` / `strategy_indicator` / `strategy_script`; strategy history, detail view, and AI correction suggestion flow supported
- **Trading Assistant → Backtest Center**: strategy items add backtest link with `strategy_id` into Backtest Center

### 🐛 Bug Fixes

- Fixed the previous strategy backtest pseudo-flow that only reused `/api/indicator/backtest` and could not faithfully replay stored strategies.
- Fixed strategy backtest history semantics so records can be linked to concrete strategies instead of only relying on `indicator_id`.
- Fixed strategy backtest UI entry restoration in Backtest Center and wired the strategy selector/history drawer to real backend endpoints.

### 🎨 UI/UX Improvements

- Restored the Backtest Center → Strategy Backtest tab with strategy summary cards and environment override controls.
- Unified strategy backtest history display with the existing run viewer and AI suggestion modal.

### 📋 Database Migration

**Run on existing PostgreSQL database (skip if fresh DB already initialized from updated `migrations/init.sql`):**

```sql
-- ============================================================
-- QuantDinger V2.2.4 Database Migration
-- Strategy Backtest Persistence Upgrade
-- ============================================================

ALTER TABLE qd_backtest_runs ADD COLUMN IF NOT EXISTS strategy_id INTEGER;
ALTER TABLE qd_backtest_runs ADD COLUMN IF NOT EXISTS strategy_name VARCHAR(255) DEFAULT '';
ALTER TABLE qd_backtest_runs ADD COLUMN IF NOT EXISTS run_type VARCHAR(50) DEFAULT 'indicator';
ALTER TABLE qd_backtest_runs ADD COLUMN IF NOT EXISTS config_snapshot TEXT DEFAULT '';
ALTER TABLE qd_backtest_runs ADD COLUMN IF NOT EXISTS engine_version VARCHAR(50) DEFAULT '';
ALTER TABLE qd_backtest_runs ADD COLUMN IF NOT EXISTS code_hash VARCHAR(128) DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_backtest_runs_strategy_id ON qd_backtest_runs(strategy_id);
CREATE INDEX IF NOT EXISTS idx_backtest_runs_run_type ON qd_backtest_runs(run_type);

CREATE TABLE IF NOT EXISTS qd_backtest_trades (
    id SERIAL PRIMARY KEY,
    run_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL DEFAULT 1 REFERENCES qd_users(id) ON DELETE CASCADE,
    strategy_id INTEGER,
    trade_index INTEGER DEFAULT 0,
    trade_time VARCHAR(64) DEFAULT '',
    trade_type VARCHAR(64) DEFAULT '',
    side VARCHAR(32) DEFAULT '',
    price DOUBLE PRECISION DEFAULT 0,
    amount DOUBLE PRECISION DEFAULT 0,
    profit DOUBLE PRECISION DEFAULT 0,
    balance DOUBLE PRECISION DEFAULT 0,
    reason VARCHAR(64) DEFAULT '',
    payload_json TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_backtest_trades_run_id ON qd_backtest_trades(run_id);

CREATE TABLE IF NOT EXISTS qd_backtest_equity_points (
    id SERIAL PRIMARY KEY,
    run_id INTEGER NOT NULL,
    point_index INTEGER DEFAULT 0,
    point_time VARCHAR(64) DEFAULT '',
    point_value DOUBLE PRECISION DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_backtest_equity_points_run_id ON qd_backtest_equity_points(run_id);
```

### 📝 Migration Notes

- All statements are idempotent and safe to run multiple times.
- Existing backtest data is preserved.
- Existing `indicator` backtest records remain compatible; new strategy backtests will write `run_type`, `strategy_id`, `strategy_name`, `config_snapshot`, `engine_version`, and `code_hash`.
- `qd_backtest_trades` and `qd_backtest_equity_points` are introduced for future strategy-level analytics and debugging.

---

## V2.2.3 (2026-03-24)

### 🚀 New Features

- **User profile IANA timezone (`qd_users.timezone`)**: profile can save timezone (IANA id, e.g. `Asia/Shanghai`); empty = follow browser. Authenticated `/api/auth/info`, profile APIs, and frontend AI analysis pages use `toLocaleString(..., { timeZone })` (invalid/empty falls back to local timezone).

### 📋 Database Migration

**Run on existing PostgreSQL database (skip if fresh DB already initialized from updated `migrations/init.sql`):**

```sql
-- ============================================================
-- QuantDinger V2.2.3 — qd_users.timezone (user profile timezone)
-- ============================================================

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'qd_users'
          AND column_name = 'timezone'
    ) THEN
        ALTER TABLE qd_users ADD COLUMN timezone VARCHAR(64) DEFAULT '';
        RAISE NOTICE 'Added timezone column to qd_users table';
    END IF;
END $$;
```

**One-liner when column does not exist (confirm column missing before running):**

```sql
ALTER TABLE qd_users ADD COLUMN IF NOT EXISTS timezone VARCHAR(64) DEFAULT '';
```

> Note: `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` requires **PostgreSQL 11+** (repo Docker default `postgres:16` works); use either the `DO` block or one-liner above, not both.

---

## V2.2.2 (2026-02-28)

### 🚀 New Features

#### Polymarket Prediction Markets Integration 🔮
- **Prediction Market Analysis**: Integrated Polymarket prediction markets as a new data source for AI analysis
- **AI-Driven Insights**: AI analyzes prediction market events and compares AI predictions with market consensus
- **Opportunity Discovery**: Identifies undervalued prediction opportunities with AI vs market divergence analysis
- **Asset Trading Recommendations**: Links prediction market events to related asset trading opportunities (e.g., BTC/USDT, ETH/USDT)
- **Data Analysis Only**: Focuses on data analysis and trading opportunity recommendations without live trading
- **Frontend Pages**: New `/polymarket` page with market listings, filtering, sorting, and search functionality
- **Market Detail View**: Comprehensive analysis view showing market info, AI analysis results, and related asset opportunities
- **AI Trading Radar Integration**: Prediction market opportunities appear in the AI Trading Radar alongside Crypto, US Stocks, and Forex

### 🐛 Bug Fixes
- Fixed duplicate `common.refresh` key in internationalization files (`zh-CN.js` and `en-US.js`)
- Fixed OKX position `entry_price` extraction (now correctly reads `avgPx`, `avgPxEp`, or `last` from position data)
- Improved symbol normalization across all exchanges to handle edge cases (e.g., PI, TRX without quote currency)
- Enhanced LLM provider fallback mechanism to handle 403/402/404/429 errors automatically

### 🎨 UI/UX Improvements
- Added Polymarket market cards with AI analysis summaries and opportunity scores
- Enhanced AI Trading Radar to display prediction market opportunities with distinct styling
- Improved symbol selector in Quick Trade panel with watchlist integration

### 📋 Database Migration

**Run the following SQL on your PostgreSQL database before deploying V2.2.2:**

```sql
-- ============================================================
-- QuantDinger V2.2.2 Database Migration
-- Polymarket Prediction Markets Integration
-- ============================================================

-- Prediction markets table (cache)
CREATE TABLE IF NOT EXISTS qd_polymarket_markets (
    id SERIAL PRIMARY KEY,
    market_id VARCHAR(255) UNIQUE NOT NULL,
    question TEXT,
    category VARCHAR(100),  -- crypto, politics, economics, sports
    current_probability DECIMAL(5,2),  -- YES probability (0-100)
    volume_24h DECIMAL(20,2),
    liquidity DECIMAL(20,2),
    end_date_iso TIMESTAMP,
    status VARCHAR(50),  -- active, closed, resolved
    outcome_tokens JSONB,  -- YES/NO prices and volume
    slug VARCHAR(255),  -- Polymarket event slug for URL construction
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Add slug column if table exists but column missing
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'qd_polymarket_markets' AND column_name = 'slug'
    ) THEN
        ALTER TABLE qd_polymarket_markets ADD COLUMN slug VARCHAR(255);
        RAISE NOTICE 'Added slug column to qd_polymarket_markets';
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_polymarket_category ON qd_polymarket_markets(category);
CREATE INDEX IF NOT EXISTS idx_polymarket_status ON qd_polymarket_markets(status);
CREATE INDEX IF NOT EXISTS idx_polymarket_updated ON qd_polymarket_markets(updated_at DESC);

-- AI analysis records
CREATE TABLE IF NOT EXISTS qd_polymarket_ai_analysis (
    id SERIAL PRIMARY KEY,
    market_id VARCHAR(255) NOT NULL,
    user_id INTEGER,  -- optional: user-specific analysis
    ai_predicted_probability DECIMAL(5,2),
    market_probability DECIMAL(5,2),
    divergence DECIMAL(5,2),  -- AI minus market
    recommendation VARCHAR(20),  -- YES/NO/HOLD
    confidence_score DECIMAL(5,2),
    opportunity_score DECIMAL(5,2),
    reasoning TEXT,
    key_factors JSONB,
    related_assets TEXT[],  -- related asset list
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_polymarket_analysis_market ON qd_polymarket_ai_analysis(market_id);
CREATE INDEX IF NOT EXISTS idx_polymarket_analysis_opportunity ON qd_polymarket_ai_analysis(opportunity_score DESC);
CREATE INDEX IF NOT EXISTS idx_polymarket_analysis_user ON qd_polymarket_ai_analysis(user_id);

-- Asset trading opportunities (generated from prediction markets)
CREATE TABLE IF NOT EXISTS qd_polymarket_asset_opportunities (
    id SERIAL PRIMARY KEY,
    market_id VARCHAR(255) NOT NULL,
    asset_symbol VARCHAR(100),
    asset_market VARCHAR(50),
    signal VARCHAR(20),  -- BUY/SELL/HOLD
    confidence DECIMAL(5,2),
    reasoning TEXT,
    entry_suggestion JSONB,  -- entry suggestion
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_polymarket_opp_market ON qd_polymarket_asset_opportunities(market_id);
CREATE INDEX IF NOT EXISTS idx_polymarket_opp_asset ON qd_polymarket_asset_opportunities(asset_symbol, asset_market);

-- Migration Complete
DO $$
BEGIN
    RAISE NOTICE '✅ QuantDinger V2.2.2 database migration completed!';
END $$;
```

**Migration Notes:**
- All statements use `IF NOT EXISTS` — safe to run multiple times
- No existing data is modified or deleted
- New tables are created for Polymarket data caching and AI analysis
- Polymarket integration is read-only (data analysis only, no live trading)

### 📝 Configuration Notes
- No new environment variables required for basic Polymarket integration
- Polymarket data source uses placeholder/dummy data by default (can be extended with actual API integration)
- AI analysis leverages existing LLM configuration from System Settings

---

## V2.2.1 (2026-02-27)

### 🚀 New Features

#### Membership & Billing System
- **Subscription Plans**: Monthly / Yearly / Lifetime tiers with configurable pricing and credit bundles
- **Credit System**: Each plan includes credits; lifetime members receive recurring monthly credit bonuses
- **Plan Management**: All plan prices, credits, and bonus amounts configurable via System Settings → Billing Configuration
- **Membership Orders**: Order tracking with status management (paid / pending / failed / refunded)

#### USDT On-Chain Payment (TRC20)
- **HD Wallet Integration**: Per-order unique receiving address derived from xpub (BIP-32/44) — no private key on server
- **Automatic Reconciliation**: Background polling via TronGrid API detects incoming payments and confirms orders
- **Depth-Flexible xpub**: Supports both account-level (depth=3) and change-level (depth=4) xpub keys
- **Configurable Expiry**: Order expiration time and confirmation delay configurable in System Settings
- **Scan-to-Pay Modal**: Professional checkout UI with QR code, step indicator, real-time status, copy-to-clipboard, dark theme support

#### VIP Free Indicators
- **VIP Free Tag**: Admins can mark community indicators as "VIP Free" when publishing
- **Zero-Credit Access**: VIP members can use VIP-free indicators without spending credits
- **Visual Badge**: VIP Free indicators display a distinct badge in the Indicator Market

#### AI Trading Opportunities Radar
- **Multi-Market Scanning**: Auto-scans Crypto, US Stocks, and Forex markets every hour
- **Rolling Carousel**: Opportunities displayed in a rotating carousel with market-specific styling
- **Signal Classification**: BUY / SELL signals with percentage change and reason text
- **Multi-Language**: All radar card content fully internationalized

#### Simplified Strategy Creation
- **Simple / Advanced Mode Toggle**: New users start with simplified mode, power users can switch to advanced
- **Smart Defaults**: 15-minute K-line period, 5x leverage, market order, sensible TP/SL percentages
- **Live Trading Disclaimer**: Mandatory risk acknowledgment checkbox before enabling live trading

#### System Settings Simplification
- **Streamlined Configuration**: Removed redundant config groups (server, strategy); consolidated into essential categories
- **Market Order Default**: Changed default order mode to market order for reliable execution
- **Billing Config i18n**: All billing configuration items fully multi-language supported

#### Quick Trade Panel (Lightning Trade) 🆕
- **Side-Sliding Drawer**: Professional trading panel slides in from the right, allowing instant order placement without leaving the analysis page
- **Multi-Exchange Support**: Select from saved exchange credentials (Binance, OKX, Bitget, Bybit, etc.) with real-time balance display
- **Long/Short Toggle**: Color-coded direction buttons with one-click switching
- **Market / Limit Orders**: Toggle between market and limit order types; limit orders accept a specific price
- **Leverage Slider**: Interactive 1x–125x leverage control for futures trading
- **TP/SL Price Setting**: Optional take-profit and stop-loss by **absolute price** (not percentage)
- **Current Position Display**: Shows open position with side, size, entry price, unrealized PnL, and one-click close button
- **Recent Trade History**: Displays last 5 quick trades with status tags
- **AI Radar Integration**: "Trade Now" button on each AI Trading Opportunities card pre-fills symbol, direction, and price
- **Indicator Analysis Integration**: Quick Trade button in chart header and floating ⚡ button pre-fills current symbol and price
- **Auto-Polling**: Balance and position data refresh every 10 seconds
- **Full Dark Theme**: Complete dark mode support for all panel elements
- **Multi-Language**: All labels and messages fully internationalized (zh-CN / en-US)

#### Indicator Market Performance Tracking
- **Live Performance Data**: Fixed aggregation to correctly parse backtest `result_json` and include live trade data
- **Combined Metrics**: Backtest return, live PnL, and win rate now properly displayed on indicator cards

### 🐛 Bug Fixes
- Fixed `quick_trade.py` importing from non-existent `auth_utils` module (corrected to `auth`)
- Fixed "Live Performance" data showing all zeros in Indicator Market (incorrect SQL query referencing non-existent columns)
- Fixed incorrect entry price display in Position Records (was falling back to current price)
- Fixed inaccurate System Overview statistics for running strategies, total capital, and total PnL
- Fixed multiple duplicate i18n key issues in `zh-CN.js` and `en-US.js` causing ESLint build failures
- Fixed exposed i18n keys (`common.loading`, `common.noData`, `systemOverview.*`) not configured
- Fixed HTML nesting issues in trading assistant strategy creation form
- Fixed `ed25519-blake2b` build failure in Docker by adding temporary build dependencies
- Fixed "Current depth (3) is not suitable for deriving address" error for xpub — now compatible with both depth 3 and depth 4

### 🎨 UI/UX Improvements
- Removed "Total Analyses" / "Accuracy Rate" row from homepage AI Analysis section
- Removed "Search" and "Portfolio Checkup" features from AI Asset Analysis page
- Professional USDT checkout modal with custom header, step indicator, dual-column layout
- Dark theme and mobile responsive support for payment modal
- Trading Opportunities Radar carousel with smooth scrolling animation

### 📋 Database Migration

**Run the following SQL on your PostgreSQL database before deploying V2.2.1:**

```sql
-- ============================================================
-- QuantDinger V2.2.1 Database Migration
-- Membership, USDT Payment, VIP Free Indicators
-- ============================================================

-- 1. User Table: Add membership columns
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'qd_users' AND column_name = 'vip_plan'
    ) THEN
        ALTER TABLE qd_users ADD COLUMN vip_plan VARCHAR(20) DEFAULT '';
        RAISE NOTICE 'Added vip_plan column to qd_users';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'qd_users' AND column_name = 'vip_is_lifetime'
    ) THEN
        ALTER TABLE qd_users ADD COLUMN vip_is_lifetime BOOLEAN DEFAULT FALSE;
        RAISE NOTICE 'Added vip_is_lifetime column to qd_users';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'qd_users' AND column_name = 'vip_monthly_credits_last_grant'
    ) THEN
        ALTER TABLE qd_users ADD COLUMN vip_monthly_credits_last_grant TIMESTAMP;
        RAISE NOTICE 'Added vip_monthly_credits_last_grant column to qd_users';
    END IF;
END $$;

-- 2. Indicator Codes: Add VIP Free flag
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'qd_indicator_codes' AND column_name = 'vip_free'
    ) THEN
        ALTER TABLE qd_indicator_codes ADD COLUMN vip_free BOOLEAN DEFAULT FALSE;
        RAISE NOTICE 'Added vip_free column to qd_indicator_codes';
    END IF;
END $$;

-- 3. Membership Orders table
CREATE TABLE IF NOT EXISTS qd_membership_orders (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE CASCADE,
    plan VARCHAR(20) NOT NULL,
    price_usd DECIMAL(10,2) DEFAULT 0,
    status VARCHAR(20) DEFAULT 'paid',
    created_at TIMESTAMP DEFAULT NOW(),
    paid_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_membership_orders_user_id ON qd_membership_orders(user_id);

-- 4. USDT Orders table (on-chain payment tracking)
CREATE TABLE IF NOT EXISTS qd_usdt_orders (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE CASCADE,
    plan VARCHAR(20) NOT NULL,
    chain VARCHAR(20) NOT NULL DEFAULT 'TRC20',
    amount_usdt DECIMAL(20,6) NOT NULL DEFAULT 0,
    address_index INTEGER NOT NULL DEFAULT 0,
    address VARCHAR(80) NOT NULL DEFAULT '',
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    tx_hash VARCHAR(120) DEFAULT '',
    paid_at TIMESTAMP,
    confirmed_at TIMESTAMP,
    expires_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_usdt_orders_address_unique ON qd_usdt_orders(chain, address);
CREATE INDEX IF NOT EXISTS idx_usdt_orders_user_id ON qd_usdt_orders(user_id);
CREATE INDEX IF NOT EXISTS idx_usdt_orders_status ON qd_usdt_orders(status);

-- 5. Quick Trades table (manual / discretionary orders from Quick Trade Panel)
CREATE TABLE IF NOT EXISTS qd_quick_trades (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE CASCADE,
    credential_id   INTEGER DEFAULT 0,
    exchange_id     VARCHAR(40) NOT NULL DEFAULT '',
    symbol          VARCHAR(60) NOT NULL DEFAULT '',
    side            VARCHAR(10) NOT NULL DEFAULT '',       -- buy / sell
    order_type      VARCHAR(20) NOT NULL DEFAULT 'market', -- market / limit
    amount          DECIMAL(24, 8) DEFAULT 0,
    price           DECIMAL(24, 8) DEFAULT 0,
    leverage        INTEGER DEFAULT 1,
    market_type     VARCHAR(20) DEFAULT 'swap',            -- swap / spot
    tp_price        DECIMAL(24, 8) DEFAULT 0,
    sl_price        DECIMAL(24, 8) DEFAULT 0,
    status          VARCHAR(20) DEFAULT 'submitted',       -- submitted / filled / failed / cancelled
    exchange_order_id VARCHAR(120) DEFAULT '',
    filled_amount   DECIMAL(24, 8) DEFAULT 0,
    avg_fill_price  DECIMAL(24, 8) DEFAULT 0,
    error_msg       TEXT DEFAULT '',
    source          VARCHAR(40) DEFAULT 'manual',          -- ai_radar / ai_analysis / indicator / manual
    raw_result      JSONB,
    created_at      TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_quick_trades_user    ON qd_quick_trades(user_id);
CREATE INDEX IF NOT EXISTS idx_quick_trades_created ON qd_quick_trades(created_at DESC);

-- Migration Complete
DO $$
BEGIN
    RAISE NOTICE '✅ QuantDinger V2.2.1 database migration completed!';
END $$;
```

**Migration Notes:**
- All statements use `IF NOT EXISTS` — safe to run multiple times
- No existing data is modified or deleted
- New `.env` variables required for USDT payment: `USDT_PAY_ENABLED`, `USDT_TRC20_XPUB`, `TRONGRID_API_KEY`
- New `.env` variables for membership pricing: `MEMBERSHIP_MONTHLY_PRICE_USD`, `MEMBERSHIP_MONTHLY_CREDITS`, etc.
- See `backend_api_python/env.example` for all new configuration options

### 📝 Configuration Notes

New environment variables (all optional, with defaults):

| Variable | Default | Description |
|----------|---------|-------------|
| `MEMBERSHIP_MONTHLY_PRICE_USD` | `19.9` | Monthly plan price |
| `MEMBERSHIP_MONTHLY_CREDITS` | `500` | Credits included in monthly plan |
| `MEMBERSHIP_YEARLY_PRICE_USD` | `169` | Yearly plan price |
| `MEMBERSHIP_YEARLY_CREDITS` | `8000` | Credits included in yearly plan |
| `MEMBERSHIP_LIFETIME_PRICE_USD` | `499` | Lifetime plan price |
| `MEMBERSHIP_LIFETIME_CREDITS` | `30000` | Initial credits for lifetime plan |
| `MEMBERSHIP_LIFETIME_MONTHLY_BONUS` | `500` | Monthly bonus credits for lifetime members |
| `USDT_PAY_ENABLED` | `false` | Enable USDT TRC20 payment |
| `USDT_TRC20_XPUB` | _(empty)_ | TRC20 HD wallet xpub for address derivation |
| `TRONGRID_API_KEY` | _(empty)_ | TronGrid API key for on-chain monitoring |
| `USDT_ORDER_EXPIRE_MINUTES` | `30` | USDT order expiration time |

---

## V2.1.3 (2026-02-XX)

### 🚀 New Features

#### Cross-Sectional Strategy Support
- **Multi-Symbol Portfolio Management** - Added support for cross-sectional strategies that manage a portfolio of multiple symbols simultaneously
  - Strategy type selection: Single Symbol vs Cross-Sectional
  - Symbol list configuration: Select multiple symbols for portfolio management
  - Portfolio size: Configure the number of symbols to hold simultaneously
  - Long/Short ratio: Set the proportion of long vs short positions (0-1)
  - Rebalance frequency: Daily, Weekly, or Monthly portfolio rebalancing
  - Indicator execution: Indicators receive a `data` dictionary (symbol -> DataFrame) for cross-symbol analysis
  - Signal generation: Automatic buy/sell/close signals based on indicator rankings
  - Parallel execution: Multiple orders executed concurrently for efficiency
- **Backend Implementation**
  - Cross-sectional configurations stored in `trading_config` JSON field
  - New `_run_cross_sectional_strategy_loop` method in TradingExecutor
  - Automatic rebalancing based on configured frequency
  - Support for both long and short positions in the same portfolio
- **Frontend UI**
  - Strategy type selector in strategy creation/editing form
  - Conditional display of single-symbol vs cross-sectional configuration fields
  - Multi-select symbol picker for cross-sectional strategies
  - Full i18n support (Chinese and English)

See `docs/CROSS_SECTIONAL_STRATEGY_GUIDE_CN.md` or `docs/CROSS_SECTIONAL_STRATEGY_GUIDE_EN.md` for detailed usage instructions.

### 🐛 Bug Fixes
- Fixed decimal precision issues in exchange order quantities (Binance Spot LOT_SIZE filter errors)
- Improved `_dec_str` method across all exchange clients for accurate quantity formatting
- Enhanced quantity normalization to respect exchange precision requirements
- Fixed validation logic for cross-sectional strategies (now validates correct symbol list field)
- Fixed success message to show correct strategy count for cross-sectional strategies

### 📋 Database Migration

**Run the following SQL on your PostgreSQL database before deploying V2.1.3:**

```sql
-- ============================================================
-- QuantDinger V2.1.3 Database Migration
-- Cross-Sectional Strategy Support
-- ============================================================

-- Add last_rebalance_at column to track rebalancing time for cross-sectional strategies
-- Note: Cross-sectional strategy configurations (symbol_list, portfolio_size, long_ratio, rebalance_frequency)
-- are stored in the trading_config JSON field, not as separate database columns.
-- This migration only adds the last_rebalance_at timestamp field which is needed for rebalancing logic.

DO $$ 
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'qd_strategies_trading' 
        AND column_name = 'last_rebalance_at'
    ) THEN
        ALTER TABLE qd_strategies_trading 
        ADD COLUMN last_rebalance_at TIMESTAMP;
        RAISE NOTICE 'Added last_rebalance_at column to qd_strategies_trading';
    ELSE
        RAISE NOTICE 'Column last_rebalance_at already exists';
    END IF;
END $$;
```

**Migration Notes:**
- This migration is safe to run multiple times (uses IF NOT EXISTS check)
- Cross-sectional strategy configurations are stored in the `trading_config` JSON field, so no additional columns are needed
- The `last_rebalance_at` field is used to track when the last rebalancing occurred for cross-sectional strategies
- If you don't run this migration, cross-sectional strategies will still work, but rebalancing frequency checks may not function correctly

---

## V2.1.2 (2026-02-01)

### 🚀 New Features

#### Indicator Parameter Support
- **External Parameter Passing** - Indicators can now declare parameters using `# @param` syntax that can be configured per-strategy
  - Supported types: `int`, `float`, `bool`, `str`
  - Parameters are displayed in the strategy creation form after selecting an indicator
  - Different strategies using the same indicator can have different parameter values
- **Cross-Indicator Calling** - Indicators can now call other indicators using `call_indicator(id_or_name, df)` function
  - Supports calling by indicator ID (number) or name (string)
  - Maximum call depth of 5 to prevent circular dependencies
  - Only allows calling own indicators or published community indicators

#### Parameter Declaration Syntax
```
# @param <name> <type> <default> <description>
```

| Field | Description | Example |
|-------|-------------|---------|
| name | Parameter name (variable name) | `ma_fast` |
| type | Data type: `int`, `float`, `bool`, `str` | `int` |
| default | Default value | `5` |
| description | Description (shown in UI tooltip) | `Short-term MA period` |

#### Example: Dual Moving Average with Parameters
```python
# @param sma_short int 14 Short-term MA period
# @param sma_long int 28 Long-term MA period

# Get parameters
sma_short_period = params.get('sma_short', 14)
sma_long_period = params.get('sma_long', 28)

my_indicator_name = "Dual MA Strategy"
my_indicator_description = f"SMA{sma_short_period}/{sma_long_period} crossover"

df = df.copy()
sma_short = df["close"].rolling(sma_short_period).mean()
sma_long = df["close"].rolling(sma_long_period).mean()

# Golden cross / Death cross
buy = (sma_short > sma_long) & (sma_short.shift(1) <= sma_long.shift(1))
sell = (sma_short < sma_long) & (sma_short.shift(1) >= sma_long.shift(1))

df["buy"] = buy.fillna(False).astype(bool)
df["sell"] = sell.fillna(False).astype(bool)

# Chart markers
buy_marks = [df["low"].iloc[i] * 0.995 if df["buy"].iloc[i] else None for i in range(len(df))]
sell_marks = [df["high"].iloc[i] * 1.005 if df["sell"].iloc[i] else None for i in range(len(df))]

output = {
    "name": my_indicator_name,
    "plots": [
        {"name": f"SMA{sma_short_period}", "data": sma_short.tolist(), "color": "#FF9800", "overlay": True},
        {"name": f"SMA{sma_long_period}", "data": sma_long.tolist(), "color": "#3F51B5", "overlay": True}
    ],
    "signals": [
        {"type": "buy", "text": "B", "data": buy_marks, "color": "#00E676"},
        {"type": "sell", "text": "S", "data": sell_marks, "color": "#FF5252"}
    ]
}
```

#### Example: Using call_indicator()
```python
# Call another indicator by name or ID
# rsi_df = call_indicator('RSI', df)           # By name
# rsi_df = call_indicator(5, df)               # By ID
# rsi_df = call_indicator('RSI', df, {'period': 14})  # With params

# Note: The called indicator must be created first
# and accessible (own indicator or published community indicator)
```

### 🐛 Bug Fixes

#### Dashboard Fixes
- **Fixed current positions showing records from other users** - Position synchronization now correctly associates positions with the strategy owner's user_id
- **Fixed strategy distribution pie chart always showing "No Data"** - Chart now uses `strategy_stats` data which includes all strategies with trading activity
- **Removed AI strategy count from running strategies card** - Dashboard now only shows indicator strategy count since AI strategies category has been removed

---

## V2.1.1 (2026-01-31)

### 🚀 New Features

#### AI Analysis System Overhaul
- **Fast Analysis Mode**: Replaced the complex multi-agent system with a streamlined single LLM call architecture for faster and more accurate analysis
- **Progressive Loading**: Market data now loads independently - each section (sentiment, indices, heatmap, calendar) displays as soon as it's ready
- **Professional Loading Animation**: New progress bar with step indicators during AI analysis
- **Analysis Memory**: Store analysis results for history review and user feedback
- **Stop Loss/Take Profit Calculation**: Now based on ATR (Average True Range) and Support/Resistance levels with clear methodology hints

#### Global Market Integration
- Integrated Global Market data directly into AI Analysis page
- Real-time scrolling display of major global indices with flags, prices, and percentage changes
- Interactive heatmaps for Crypto, Commodities, Sectors, and Forex
- Economic calendar with bullish/bearish/neutral impact indicators
- Commodities heatmap added (Gold, Silver, Crude Oil, etc.)

#### Indicator Community Enhancements
- **Admin Review System**: Administrators can now review, approve, reject, unpublish, and delete community indicators
- **Purchase & Rating System**: Users can buy indicators, leave ratings and comments
- **Statistics Tracking**: Purchase count, average rating, rating count, view count for each indicator

#### Trading Assistant Improvements
- Improved IBKR/MT5 connection test feedback
- Added local deployment warning for external trading platforms
- Virtual profit/loss calculation for signal-only strategies

### 🐛 Bug Fixes
- Fixed progress bar and timer not animating during AI analysis
- Fixed missing i18n translations for various components
- Fixed Tiingo API rate limit issues with caching
- Fixed data fetching with multiple fallback sources
- Fixed watchlist price batch fetch timeout handling
- Fixed heatmap multi-language support for commodities and forex
- **Fixed AI analysis history not filtered by user** - All users were seeing the same history records; now each user only sees their own analysis history
- **Fixed "Missing Turnstile token" error when changing password** - Logged-in users no longer need Turnstile verification to request password change verification code

### 🎨 UI/UX Improvements
- Reorganized left menu: Indicator Market moved below Indicator Analysis, Settings moved to bottom
- Skeleton loading animations for progressive data display
- Dark theme support for all new components
- Compact market overview bar design

### 📋 Database Migration

**Run the following SQL on your PostgreSQL database before deploying V2.1.1:**

```sql
-- ============================================================
-- QuantDinger V2.1.1 Database Migration
-- ============================================================

-- 1. AI Analysis Memory Table
CREATE TABLE IF NOT EXISTS qd_analysis_memory (
    id SERIAL PRIMARY KEY,
    market VARCHAR(50) NOT NULL,
    symbol VARCHAR(50) NOT NULL,
    decision VARCHAR(10) NOT NULL,
    confidence INT DEFAULT 50,
    price_at_analysis DECIMAL(24, 8),
    entry_price DECIMAL(24, 8),
    stop_loss DECIMAL(24, 8),
    take_profit DECIMAL(24, 8),
    summary TEXT,
    reasons JSONB,
    risks JSONB,
    scores JSONB,
    indicators_snapshot JSONB,
    raw_result JSONB,
    created_at TIMESTAMP DEFAULT NOW(),
    validated_at TIMESTAMP,
    actual_outcome VARCHAR(20),
    actual_return_pct DECIMAL(10, 4),
    was_correct BOOLEAN,
    user_feedback VARCHAR(20),
    feedback_at TIMESTAMP
);

-- Add raw_result column if table exists but column doesn't
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'qd_analysis_memory' AND column_name = 'raw_result'
    ) THEN
        ALTER TABLE qd_analysis_memory ADD COLUMN raw_result JSONB;
    END IF;
END $$;

-- Add user_id column for user-specific history filtering
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'qd_analysis_memory' AND column_name = 'user_id'
    ) THEN
        ALTER TABLE qd_analysis_memory ADD COLUMN user_id INT;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_analysis_memory_symbol ON qd_analysis_memory(market, symbol);
CREATE INDEX IF NOT EXISTS idx_analysis_memory_created ON qd_analysis_memory(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_analysis_memory_validated ON qd_analysis_memory(validated_at) WHERE validated_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_analysis_memory_user ON qd_analysis_memory(user_id);

-- 2. Indicator Purchase Records
CREATE TABLE IF NOT EXISTS qd_indicator_purchases (
    id SERIAL PRIMARY KEY,
    indicator_id INTEGER NOT NULL REFERENCES qd_indicator_codes(id) ON DELETE CASCADE,
    buyer_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE CASCADE,
    seller_id INTEGER NOT NULL REFERENCES qd_users(id),
    price DECIMAL(10,2) NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(indicator_id, buyer_id)
);

CREATE INDEX IF NOT EXISTS idx_purchases_indicator ON qd_indicator_purchases(indicator_id);
CREATE INDEX IF NOT EXISTS idx_purchases_buyer ON qd_indicator_purchases(buyer_id);
CREATE INDEX IF NOT EXISTS idx_purchases_seller ON qd_indicator_purchases(seller_id);

-- 3. Indicator Comments
CREATE TABLE IF NOT EXISTS qd_indicator_comments (
    id SERIAL PRIMARY KEY,
    indicator_id INTEGER NOT NULL REFERENCES qd_indicator_codes(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE CASCADE,
    rating INTEGER DEFAULT 5 CHECK (rating >= 1 AND rating <= 5),
    content TEXT DEFAULT '',
    parent_id INTEGER REFERENCES qd_indicator_comments(id) ON DELETE CASCADE,
    is_deleted INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_comments_indicator ON qd_indicator_comments(indicator_id);
CREATE INDEX IF NOT EXISTS idx_comments_user ON qd_indicator_comments(user_id);

-- 4. Indicator Codes Extensions
DO $$
BEGIN
    -- Purchase count
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'qd_indicator_codes' AND column_name = 'purchase_count'
    ) THEN
        ALTER TABLE qd_indicator_codes ADD COLUMN purchase_count INTEGER DEFAULT 0;
    END IF;
    
    -- Average rating
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'qd_indicator_codes' AND column_name = 'avg_rating'
    ) THEN
        ALTER TABLE qd_indicator_codes ADD COLUMN avg_rating DECIMAL(3,2) DEFAULT 0;
    END IF;
    
    -- Rating count
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'qd_indicator_codes' AND column_name = 'rating_count'
    ) THEN
        ALTER TABLE qd_indicator_codes ADD COLUMN rating_count INTEGER DEFAULT 0;
    END IF;
    
    -- View count
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'qd_indicator_codes' AND column_name = 'view_count'
    ) THEN
        ALTER TABLE qd_indicator_codes ADD COLUMN view_count INTEGER DEFAULT 0;
    END IF;
    
    -- Review status
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'qd_indicator_codes' AND column_name = 'review_status'
    ) THEN
        ALTER TABLE qd_indicator_codes ADD COLUMN review_status VARCHAR(20) DEFAULT 'approved';
        UPDATE qd_indicator_codes SET review_status = 'approved' WHERE publish_to_community = 1;
    END IF;
    
    -- Review note
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'qd_indicator_codes' AND column_name = 'review_note'
    ) THEN
        ALTER TABLE qd_indicator_codes ADD COLUMN review_note TEXT DEFAULT '';
    END IF;
    
    -- Reviewed at
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'qd_indicator_codes' AND column_name = 'reviewed_at'
    ) THEN
        ALTER TABLE qd_indicator_codes ADD COLUMN reviewed_at TIMESTAMP;
    END IF;
    
    -- Reviewed by
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'qd_indicator_codes' AND column_name = 'reviewed_by'
    ) THEN
        ALTER TABLE qd_indicator_codes ADD COLUMN reviewed_by INTEGER;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_indicator_review_status ON qd_indicator_codes(review_status);

-- 5. User Table Extensions
DO $$
BEGIN
    -- Token version (for single-client login)
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'qd_users' AND column_name = 'token_version'
    ) THEN
        ALTER TABLE qd_users ADD COLUMN token_version INTEGER DEFAULT 1;
    END IF;
    
    -- Notification settings
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'qd_users' AND column_name = 'notification_settings'
    ) THEN
        ALTER TABLE qd_users ADD COLUMN notification_settings TEXT DEFAULT '{}';
    END IF;
END $$;

-- Migration Complete
DO $$
BEGIN
    RAISE NOTICE '✅ QuantDinger V2.1.1 database migration completed!';
END $$;
```

### 🗑️ Removed
- Old multi-agent AI analysis system (`backend_api_python/app/services/agents/` directory)
- Old analysis routes and services
- Standalone Global Market page (merged into AI Analysis)
- Reflection worker background process

### ⚠️ Breaking Changes
- AI Analysis API endpoints changed from `/api/analysis/*` to `/api/fast-analysis/*`
- Old analysis history data is not compatible with new format

### 📝 Configuration Notes
- No new environment variables required
- Existing LLM configuration in System Settings will be used for AI Analysis

---

## Version History

| Version | Date | Highlights |
|---------|------|------------|
| V3.1.0 | 2026-05-02 | AI Agent Gateway (`/api/agent/v1`), MCP server with stdio/SSE/HTTP transports, SSE job progress streaming, Vue Admin UI for agent tokens & audit, paper-only-by-default trading safety, 4 new DB tables |
| V2.2.2 | 2026-02-28 | Polymarket prediction markets integration, AI-driven prediction analysis, asset trading recommendations |
| V2.2.1 | 2026-02-27 | Membership & Billing, USDT TRC20 payment, VIP free indicators, AI Trading Radar, simplified strategy creation |
| V2.1.3 | 2026-02-XX | Cross-sectional strategy support |
| V2.1.2 | 2026-02-01 | Indicator parameters, cross-indicator calling |
| V2.1.1 | 2026-01-31 | AI Analysis overhaul, Global Market integration, Indicator Community enhancements |

---

*For questions or issues, please open a GitHub issue or contact the maintainers.*
