# Futu OpenD Integration

QuantDinger connects to [Futu OpenAPI](https://openapi.futunn.com/futu-api-doc/) through a local **FutuOpenD** gateway (same operational model as IBKR TWS).

## What is supported (MVP)

| Area | Support |
|------|---------|
| Markets | `HKStock`, `USStock` spot |
| Direction | Long-only |
| Environments | `demo` → `TrdEnv.SIMULATE`, `live` → `TrdEnv.REAL` |
| Orders | Market / limit / cancel |
| Data | History K-line, snapshot quote, quote feed for risk ticks |
| Bots | DCA / Trend only (no Grid / Martingale) |

## Prerequisites

1. Install and log into **FutuOpenD** (GUI recommended).
2. Default listen: `127.0.0.1:11111`.
3. Backend dependency: `pip install futu-api`.
4. Set `ALLOW_LOCAL_DESKTOP_BROKERS=true` on self-hosted deployments.

## Docker / network topology

| Runtime | OpenD host tip |
|---------|----------------|
| Same host (native Python) | `127.0.0.1` |
| Docker Compose on Docker Desktop / WSL2 | `host.docker.internal` |
| LAN server | Private IP of the OpenD machine (`192.168.x.x` / `10.x`) |

Open the OpenD API port in the host firewall. Keep `FUTU_ALLOW_REMOTE_OPEND=false` unless you intentionally point at a non-LAN OpenD.

## Credential fields

Stored encrypted in `qd_exchange_credentials`:

- `futu_host`, `futu_port`
- `trade_env` / `environment`: `demo` \| `live`
- `trade_market`: `HK` \| `US`
- `security_firm`: e.g. `FUTUSECURITIES`, `FUTUINC`, `FUTUSG`
- `acc_id` (optional)
- `unlock_password` (optional — prefer GUI unlock for live)

## Operator APIs

- `POST /api/futu/connect` — session connect
- `POST /api/futu/probe` — account + quote permission probe (**no orders**)
- `GET /api/futu/account|positions|orders|quote`
- `POST /api/credentials/test` with `exchange_id=futu`
- `GET /api/policy/broker-market` — includes `futu` matrix

Live strategy orders still go through `pending_orders` → `trading-worker` → `FutuClient`.

## Unlock / live trading

1. Prefer unlocking in the OpenD GUI.
2. Headless: store `unlock_password` in the credential vault (encrypted).
3. Never log unlock passwords or full credential blobs.

## Failure modes

| Symptom | Likely cause |
|---------|----------------|
| `FUTU_OPEND_UNREACHABLE` | OpenD not running / wrong host:port / Docker networking |
| `FUTU_QUOTE_PERMISSION_DENIED` | Account lacks quote rights for that market |
| `FUTU_QUOTE_QUOTA_EXCEEDED` | History K-line quota exhausted |
| `FUTU_TRADE_LOCKED` | Live trade not unlocked |
| `FUTU_INVALID_LOT_SIZE` | HK qty not a multiple of lot size |

When OpenD quote fails for a Futu execution account, K-line/ticker falls back to the public HK/US sources and tags `source=fallback:...` — never silently pretend Futu succeeded.

## Connection budget

OpenD caps TCP sessions at **128**. QuantDinger now reuses a process-wide `FutuSessionPool`:

| Consumer | Contexts | Typical count per credential |
|----------|----------|------------------------------|
| Live orders / REST fill sync / position sync | `OpenSecTradeContext` only | 1 shared `trade` session |
| Execution push adapter | same pooled trade session | 0 extra if `trade` already live |
| K-line / ticker (`FutuQuoteClient`) | `OpenQuoteContext` only | 1 `quote` session |
| Order placement with lot-size check | quote + trade (`both`) | 1 `both` session (trade callers reuse it) |

Budget rule of thumb: **about 2–3 TCP connections per credential**, not one pair per worker tick.

`create_futu_client(..., pooled=False)` is reserved for one-shot probes (`POST /api/credentials/test`). Worker `stop()` and process exit call `drain()` to close leftover sessions.

### Docker start order

1. Start **FutuOpenD** on the host, bound to `127.0.0.1:11111`.
2. Start `scripts/opend_relay.py` so Docker containers can reach OpenD on the bridge IP (auto-detected `docker0` / `host-gateway`, default port `11112`).
3. Start Compose (`trading-worker` / API). Point credentials at `host.docker.internal:11112` (relay) or `host.docker.internal:11111` if OpenD already listens on the Docker gateway.

### When the 128 cap is hit

Symptoms: `FUTU_OPEND_UNREACHABLE`, OpenD logs “too many connections”, or `ss -tn sport = :11111` / `:11112` climbing linearly.

1. Restart `trading-worker` (pool `drain()` on stop) and the OpenD process.
2. Confirm connection count is stable in the low single digits during one running strategy + REST sync.
3. Check `qd_execution_stream_health`: `state` should be `connected` or `orphaned` (never two Futu adapters for the same key). `last_error` includes pool snapshot + OpenD target.
4. Do not stack extra probes (`pooled=False`) in a tight loop.

## Acceptance checklist

1. `POST /api/futu/probe` succeeds on simulate env.
2. Demo market/limit order fills and appears in strategy ledger.
3. Restart `trading-worker` — open `sent` orders reconcile without duplicate fills.
4. Position sync matches OpenD positions for the credential account.
5. Only then enable `trade_env=live` with whitelist symbols and small size limits.
