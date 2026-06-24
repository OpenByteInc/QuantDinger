# QuantDinger Backend Concurrency Model

This document defines how the backend should behave under concurrent users,
strategies, workers, API calls, and external providers.

## Core Principle

All operations that can mutate trading, payment, job, or account state must be
idempotent or explicitly serialized. Retrying a request must never create an
unintended duplicate order, duplicate membership activation, duplicate job, or
conflicting position row.

## Concurrency Domains

| Domain | Parallelism Allowed | Serialization Key | Required Protection |
| --- | --- | --- | --- |
| Strategy lifecycle | Different strategies may run in parallel | `strategy_id` | start/stop lock, stale status repair |
| Strategy symbol execution | Different symbols may run in parallel | `strategy_id:symbol:side` | execution lock, position reconciliation |
| Quick trade order placement | Different users/symbols may run in parallel | `user_id:credential_id:market:symbol:side` | idempotency key, client order id |
| Pending order dispatch | Multiple pending orders may run in parallel | `pending_order_id` and venue order id | claim-before-dispatch, retry dedupe |
| Grid resting orders | Different grid cells may run in parallel | `strategy_id:symbol:cell_index` | DB unique cell, fill reconciliation |
| Account mirror | One row per credential/market/instrument/side | `credential_id:market_type:inst_id:side` | upsert, no blind delete on partial data |
| Backtest jobs | Many jobs may run in parallel | `job_id` | bounded worker pool, no shared mutable state |
| Agent jobs | Many jobs may run in parallel | token + kind + idempotency key | DB unique index, replay returns same job |
| USDT payments | Many users may pay in parallel | `order_id` and tx hash | order status state machine, one activation |
| Market data cache | Reads parallel, refresh single-flight per key | cache key | stale-while-revalidate, TTL |
| SSE streams | Many read streams allowed | `job_id:user_id` | heartbeat, disconnect handling |

## Existing Protections To Preserve

- Agent jobs have `job_id` and `idempotency_key` columns with a unique index.
- Agent quick-trade/backtest/experiment routes already use idempotency helpers.
- Strategy positions and account mirror tables use uniqueness on natural keys.
- Grid cells use a unique `(strategy_id, symbol, cell_index)` constraint.
- Data providers use in-process single-flight locks for cache refresh.
- USDT orders use uniqueness to avoid active amount/address collisions.

## Gaps To Audit During Refactor

- Worker startup under Gunicorn multi-worker mode: only one process should own
  singleton workers unless a distributed lock is introduced.
- In-process locks are not enough when the app runs in multiple processes or hosts.
- Quick-trade human web routes need the same idempotency posture as Agent routes.
- Strategy restore must not start duplicate execution loops after a crash/redeploy.
- Pending-order dispatch should atomically claim work before calling exchanges.
- Exchange retry code must dedupe by `client_order_id` where the venue supports it.
- Long-running LLM/backtest/experiment endpoints should avoid blocking request threads.

## Required Patterns

### Idempotency Key

Use for any user-triggered operation that may be retried by browser, mobile app,
agent, proxy, or worker:

- order placement
- close position
- backtest submit
- experiment submit
- USDT order create/confirm
- strategy start/stop when triggered by API

Recommended key shape:

```text
actor_id:operation:target:client_request_id
```

If the client does not supply a request id, generate a server-side key from
stable input only when duplicate replay is safe.

### Execution Lock

Use for operations that must not overlap:

```text
strategy:{strategy_id}:lifecycle
strategy:{strategy_id}:symbol:{symbol}:side:{side}
credential:{credential_id}:instrument:{inst_id}:order
```

In-process `threading.Lock` is acceptable only for single-process local mode.
Production-safe locks must use database row locks, advisory locks, or Redis locks.

### Claim Before Work

Workers must claim a row before external side effects:

1. Select eligible row.
2. Atomically transition from `pending` to `processing`.
3. Commit.
4. Call external exchange/payment/LLM.
5. Store result and final status.

No worker should call an exchange before it owns the row.

### State Machine

Order-like records should have explicit states. Avoid free-form status strings.

Suggested lifecycle:

```text
pending -> processing -> submitted -> filled
                         -> partially_filled
                         -> failed
                         -> cancelled
                         -> expired
```

Payment lifecycle:

```text
pending -> seen_on_chain -> paid -> confirmed -> fulfilled
                    -> expired
                    -> failed
```

## Rate Limits And Backpressure

- External market data providers must have per-provider timeout and retry policy.
- Exchange adapters must avoid unbounded retry loops.
- LLM calls should run through job workers for long outputs or streaming.
- SSE streams must send heartbeat events and release resources on disconnect.
- ThreadPoolExecutor sizes must be bounded and documented.

## Test Requirements

Before changing high-risk concurrency code, add or update tests for:

- duplicate API request returns existing result
- two concurrent starts do not create duplicate strategy loops
- two concurrent order attempts do not place duplicate venue orders
- pending order worker can retry after failure without duplicate fill records
- USDT confirmation can run twice and activate membership once
- agent job idempotent replay returns the same `job_id`

## Deployment Notes

Local single-process mode may rely on in-process locks. Docker/Gunicorn production
mode must not assume process-local state is globally unique. Any worker that can
create external side effects must have a single owner or a distributed lock.
