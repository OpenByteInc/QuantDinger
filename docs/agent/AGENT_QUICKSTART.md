# Agent Quickstart — using QuantDinger from an AI agent

This quickstart shows how to drive the QuantDinger Agent Gateway
(`/api/agent/v1`) from any AI / automation client. It assumes you already
have the stack running (see the root `README.md`) and admin credentials.

For the full design, see [AI_INTEGRATION_DESIGN.md](AI_INTEGRATION_DESIGN.md).
For the machine-readable contract, see [agent-openapi.json](agent-openapi.json).

---

## 1. Issue an agent token (one-time)

Tokens are minted by a logged-in human user (Profile → **My Agent Token**) or
by an admin via `/api/agent/v1/admin/tokens`. Agents never mint tokens for
themselves. Get a normal JWT first (login UI or `/api/auth/login`), then:

```bash
curl -X POST http://localhost:8888/api/agent/v1/me/tokens \
  -H "Authorization: Bearer <USER_JWT>" \
  -H "Content-Type: application/json" \
  -d '{
        "name": "my-research-bot",
        "scopes": "R,B",
        "markets": "Crypto,USStock",
        "instruments": "*",
        "rate_limit_per_min": 120,
        "expires_in_days": 30
      }'
```

Admin equivalent (may include `C` scope):

```bash
curl -X POST http://localhost:8888/api/agent/v1/admin/tokens \
  -H "Authorization: Bearer <ADMIN_JWT>" \
  -H "Content-Type: application/json" \
  -d '{
        "name": "my-research-bot",
        "scopes": "R,B",
        "markets": "Crypto,USStock",
        "instruments": "*",
        "rate_limit_per_min": 120,
        "expires_in_days": 30
      }'
```

Response (the full token is shown **once**):

```json
{
  "code": 0,
  "message": "issued",
  "data": {
    "id": 1,
    "name": "my-research-bot",
    "token": "qd_agent_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
    "token_prefix": "qd_agent_xxxxxxxx",
    "scopes": ["B","R"],
    "markets": ["Crypto","USStock"],
    "paper_only": true
  }
}
```

Store the `token` value somewhere safe (password manager, secrets store).
The server only keeps a hash — there is no way to recover it later.

### Scope cheat sheet

| Scope | Class                      | Default | Notes |
|-------|----------------------------|---------|-------|
| `R`   | Read                       | yes     | Market data, strategies, jobs |
| `W`   | Workspace write            | no      | Create / patch strategies     |
| `B`   | Backtest / simulation      | no      | Async jobs                    |
| `N`   | Notifications & misc side-effects | no | rate-limited                  |
| `C`   | Credentials                | no      | admin only; not exposed to agents |
| `T`   | Trading / capital          | no      | paper-only by default; live requires opt-in |

---

## 2. Smoke-test the token

```bash
TOKEN=qd_agent_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

curl -s http://localhost:8888/api/agent/v1/health
curl -s http://localhost:8888/api/agent/v1/whoami \
  -H "Authorization: Bearer $TOKEN"
```

`/health` is public; `/whoami` should echo your token's scopes and allowlists.

---

## 3. Read market data (class R)

```bash
curl -s "http://localhost:8888/api/agent/v1/markets" \
  -H "Authorization: Bearer $TOKEN"

curl -s "http://localhost:8888/api/agent/v1/markets/Crypto/symbols?keyword=BTC&limit=5" \
  -H "Authorization: Bearer $TOKEN"

curl -s "http://localhost:8888/api/agent/v1/klines?market=Crypto&symbol=BTC/USDT&timeframe=1D&limit=10" \
  -H "Authorization: Bearer $TOKEN"
```

---

## 4. Run a backtest (class B, async)

```bash
curl -s -X POST http://localhost:8888/api/agent/v1/backtests \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: ma-cross-2024-q1-001" \
  -d '{
        "code": "fast = SMA(close, 10)\nslow = SMA(close, 30)\ndf[\"buy\"]  = CROSSOVER(fast, slow).fillna(False).astype(bool)\ndf[\"sell\"] = CROSSUNDER(fast, slow).fillna(False).astype(bool)",
        "market": "Crypto",
        "symbol": "BTC/USDT",
        "timeframe": "1D",
        "start_date": "2024-01-01",
        "end_date": "2024-03-31",
        "strictMode": true
      }'
```

You get back `{ job_id, status: "queued" }`. Poll:

```bash
curl -s "http://localhost:8888/api/agent/v1/jobs/<job_id>" \
  -H "Authorization: Bearer $TOKEN"
```

When `status` becomes `succeeded`, the backtest result is in `result`.

Set `"strictMode": true` (default) for live-aligned next-bar-open execution;
`false` uses the aggressive MTF/bar path (matches the IDE non-strict toggle).

The `Idempotency-Key` header makes retries safe: the second call with the
same key returns the original job instead of submitting a duplicate.

### 4.1 The `code` parameter contract

`code` is a **Python script** that the backend executes inside a sandbox.
The script must mutate the pre-bound `df` DataFrame to add boolean signal
columns. It is **not** a function, callable, or expression that returns
signals — those shapes will fail validation in `_simulate_trading`.

**Pre-bound names** in the exec environment:

| Name | Type | Notes |
|------|------|-------|
| `df` | `pd.DataFrame` | Columns: `time, open, high, low, close, volume`. Mutate in place. |
| `open`, `high`, `low`, `close`, `volume` | `pd.Series` | Convenience handles for the columns above |
| `np`, `pd` | modules | Standard NumPy / pandas |
| `params` | `dict` | Indicator params parsed from `# @param` comments + caller overrides |
| `call_indicator(...)` | callable | Invoke another saved indicator from this script |
| `SMA, EMA, RSI, MACD, BOLL, ATR, CROSSOVER, CROSSUNDER` | callables | Built-in technical helpers (see `app/services/backtest.py::_get_indicator_functions`) |

**Required output** — the script must add **either** of these to `df`:

| Style | Required columns | When to use |
|-------|------------------|-------------|
| 2-way (recommended) | `df['buy']`, `df['sell']` (boolean Series) | Most strategies — simple long-only or `trade_direction='both'` |
| 4-way (advanced) | `df['open_long']`, `df['close_long']`, `df['open_short']`, `df['close_short']` (boolean Series) | When you need explicit control over each leg |

**`trade_direction='both'` mapping (must match backtest):**

| Column | Meaning at execution |
|--------|----------------------|
| `buy=True` | `open_long`; close short first if short |
| `sell=True` | `open_short`; close long first if long |

Do **not** assume `buy` is an independent `close_short` signal. Merging short tp/sl into `buy` means flip-long under `both`. See `docs/STRATEGY_DEV_GUIDE.md` §3.3.1 and §11.7 (avoid duplicate indicator exits + `trailingEnabled`).

Minimal working SMA crossover:

```python
fast = SMA(close, 10)
slow = SMA(close, 30)
df['buy']  = CROSSOVER(fast, slow).fillna(False).astype(bool)
df['sell'] = CROSSUNDER(fast, slow).fillna(False).astype(bool)
```

Trend-pullback with RSI filter (parameterized):

```python
# @param fast_len int 20 Fast EMA length
# @param slow_len int 50 Slow EMA length
# @param rsi_floor float 45 Min RSI for long entry

ema_fast = EMA(close, params['fast_len'])
ema_slow = EMA(close, params['slow_len'])
rsi = RSI(close, 14)

raw_buy  = (ema_fast > ema_slow) & (rsi >= params['rsi_floor'])
raw_sell = (ema_fast < ema_slow)

df['buy']  = (raw_buy.fillna(False)  & (~raw_buy.shift(1).fillna(False))).astype(bool)
df['sell'] = (raw_sell.fillna(False) & (~raw_sell.shift(1).fillna(False))).astype(bool)
```

See `docs/STRATEGY_DEV_GUIDE.md` for the full indicator-authoring guide,
including TP/SL/trailing-stop hooks (`# @strategy ...` comments).

### 4.1 Stream partial results (SSE)

For long-running jobs (`ai-optimize`, `structured-tune`, multi-round
pipelines) the Gateway exposes a Server-Sent Events stream so an LLM client
can react to partial results without polling:

```bash
curl -N "http://localhost:8888/api/agent/v1/jobs/<job_id>/stream" \
  -H "Authorization: Bearer $TOKEN"
```

Frame types:

| Event      | When                                             | Payload |
|------------|--------------------------------------------------|---------|
| `snapshot` | first frame; current row from `qd_agent_jobs`    | full job record |
| `progress` | each call the runner makes to `on_progress(...)` | `{seq, ts, data, terminal}` |
| `ping`     | every ~15s while idle                            | `{ts}` (keepalive) |
| `result`   | once, just before close                          | `{job_id, status, result, error}` |

Reconnect with `?since=<seq>` (or the standard `Last-Event-ID` header) to
resume from a known sequence number. If the job already finished, the server
returns the `snapshot` and `result` frames immediately and closes — your
client doesn't need a separate code path.

---

## 5. Indicators (class R / W)

Agents author indicators as **Python scripts**, not via the human SSE
`aiGenerate` endpoint. Start with the authoring contract:

```bash
curl -s http://localhost:8888/api/agent/v1/indicators/authoring-contract \
  -H "Authorization: Bearer $TOKEN"
```

Validate without saving (R):

```bash
curl -s -X POST http://localhost:8888/api/agent/v1/indicators/validate \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{ "code": "df[\"buy\"] = close > open\n..." }'
```

Save into the tenant library so it appears in the IDE list (W; max 512 KiB):

```bash
curl -s -X POST http://localhost:8888/api/agent/v1/indicators \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{ "name": "my-ma-cross", "code": "..." }'
```

When creating a strategy with embedded `indicator_code`, the Gateway auto-saves
and links the indicator (`link-config` is also exposed for normalizing configs).

---

## 6. Strategies (class R / W)

```bash
# list (R)
curl -s "http://localhost:8888/api/agent/v1/strategies" -H "Authorization: Bearer $TOKEN"

# create (W) — never auto-runs; status defaults to 'stopped'
curl -s -X POST http://localhost:8888/api/agent/v1/strategies \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{ "strategy_name": "ma-cross-bot",
        "strategy_type": "IndicatorStrategy",
        "market_category": "Crypto",
        "trading_config": { "symbol": "BTC/USDT", "timeframe": "1D",
                            "initial_capital": 10000, "leverage": 1 } }'
```

Switching `status` to `running` requires a `T` scope on the token (see
the design doc for the rationale).

---

## 7. Trading (class T) — paper-only by default

A token with `T` is hard-gated:

1. The token must explicitly set `paper_only=false` (default is `true`).
2. The deployment must set env `AGENT_LIVE_TRADING_ENABLED=true` to allow live.

Until both are set, every `T` call records a **paper** order in
`qd_agent_paper_orders` using the latest market price as the simulated fill:

```bash
curl -s -X POST http://localhost:8888/api/agent/v1/quick-trade/orders \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{ "market": "Crypto", "symbol": "BTC/USDT",
        "side": "buy", "qty": 0.001 }'
```

Cancel any open paper orders for this tenant in one call:

```bash
curl -s -X POST http://localhost:8888/api/agent/v1/quick-trade/kill-switch \
  -H "Authorization: Bearer $TOKEN"
```

---

## 8. Audit & revoke (admin)

```bash
# recent agent calls (this tenant)
curl -s "http://localhost:8888/api/agent/v1/admin/audit?limit=50" \
  -H "Authorization: Bearer <ADMIN_JWT>"

# list / revoke tokens
curl -s "http://localhost:8888/api/agent/v1/admin/tokens" \
  -H "Authorization: Bearer <ADMIN_JWT>"

curl -s -X DELETE "http://localhost:8888/api/agent/v1/admin/tokens/1" \
  -H "Authorization: Bearer <ADMIN_JWT>"
```

Revoking a token sets its status to `revoked`; subsequent calls with that
token return `401`.

---

## 9. MCP integration (optional)

For AI clients that speak MCP (Cursor, Claude-style desktops, cloud agents),
see [`mcp_server/README.md`](../../mcp_server/README.md) for the Python server
that wraps Read + Workspace write + Backtest tools from the Gateway.

**Not exposed via MCP (by design):** live/paper trading (`quick-trade/*`),
admin token issuance, and credential vault access. Use REST with appropriately
scoped tokens when you explicitly need those capabilities.

MCP long-running jobs: use `wait_for_job` or bounded `stream_job_until_done`
instead of opening raw SSE yourself.

Two transports are supported via `QUANTDINGER_MCP_TRANSPORT`:

* `stdio` (default) — desktop IDEs that spawn the server as a subprocess.
* `sse` / `streamable-http` — cloud agents and remote IDEs that connect to a
  long-running HTTP endpoint. Combine with `QUANTDINGER_MCP_HOST` /
  `QUANTDINGER_MCP_PORT`.

---

## 10. Errors

All `/api/agent/v1/...` errors share this envelope:

```json
{
  "code":      400,
  "message":   "human-readable reason",
  "details":   "...",
  "retriable": false
}
```

| HTTP | Meaning                              | Retry? |
|------|--------------------------------------|--------|
| 401  | Missing / invalid / expired token    | no (re-issue) |
| 403  | Token lacks scope or allowlist       | no |
| 404  | Resource not found in this tenant    | no |
| 429  | Rate limit (per token)               | yes (after 60s) |
| 500  | Internal error                        | sometimes |
| 502  | Upstream data source failure          | yes |
| 501  | Live trading requested but not enabled| no |
