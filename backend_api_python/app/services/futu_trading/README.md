# Futu OpenAPI Adapter

QuantDinger broker adapter for [Futu OpenAPI](https://openapi.futunn.com/futu-api-doc/) via **FutuOpenD**.

## Prerequisites

1. Install and login to **FutuOpenD** (GUI or console). Default listen address: `127.0.0.1:11111`.
2. Install Python SDK: `pip install futu-api`
3. Ensure `ALLOW_LOCAL_DESKTOP_BROKERS=true` when the API runs in Docker/SaaS that should talk to a desktop OpenD.

## Credential fields

| Field | Description |
|-------|-------------|
| `futu_host` | OpenD host (default `127.0.0.1`) |
| `futu_port` | OpenD port (default `11111`) |
| `trade_env` / `environment` | `demo` → `TrdEnv.SIMULATE`, `live` → `TrdEnv.REAL` |
| `trade_market` | `HK` or `US` (filters accounts) |
| `security_firm` | e.g. `FUTUSECURITIES`, `FUTUINC`, `FUTUSG` |
| `acc_id` | Optional; auto-select first matching account when empty |
| `unlock_password` | Optional; prefer GUI unlock for live trading |

## Symbol format

| QuantDinger | Futu |
|-------------|------|
| `00700.HK` | `HK.00700` |
| `AAPL` | `US.AAPL` |

## Supported markets (MVP)

- `HKStock` spot, long-only
- `USStock` spot, long-only
- Paper (`demo`) and live (`live`)

Not supported in this adapter: short selling, options, futures, margin financing, grid/martingale bots.
