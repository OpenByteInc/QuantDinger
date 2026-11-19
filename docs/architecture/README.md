# Architecture Overview

QuantDinger v5 separates HTTP request handling, trading control and execution,
closed-bar dispatch/evaluation, scheduling, finite background jobs, and schema
migration into explicit process roles. PostgreSQL coordinates durable ownership,
Kafka carries versioned runtime events, and the two Redis tiers serve different
reliability requirements.

## Runtime ownership

| Surface | Owns | Must not own |
| --- | --- | --- |
| `backend` | Authentication, validation, queries, durable command submission | Trading loops and long-running schedules |
| `trading-worker` | Control and realtime runtimes, broker sessions, fenced order submission, reconciliation, durable grid actors | Public HTTP request handling and distributed bar evaluation |
| `strategy-dispatcher-worker` | Closed-bar consumption and stable strategy-shard batch publication | Strategy code execution and exchange orders |
| `strategy-evaluator-worker` | Hot distributed bar runtimes, inbox claims, runtime leases, checkpoints, order intents | Direct exchange submission |
| Strategy sandbox child | Untrusted indicator, discovery, backtest, and live callback execution; serializable strategy state | Leases, credentials, database access, or direct exchange submission |
| `kafka-audit-worker` | Independent event-contract and delivery audit | Strategy decisions and orders |
| `scheduler-worker` | Portfolio, deployment, payment, and notification schedules | General Celery work |
| `celery-worker` | Finite, serializable, retryable jobs | Persistent trading runtimes |
| `migration` | Ordered schema updates | Concurrent API serving |
| `kafka-init` | Versioned topic creation | Runtime event processing |

## State and coordination

- PostgreSQL is the source of truth for commands, leases, heartbeats, audit
  records, event inboxes, actor mailboxes, strategies, and trading state.
- Kafka is the ordered inter-process event backbone. Consumer offsets are not a
  replacement for PostgreSQL business state or order idempotency.
- `redis` is an evictable cache and must not hold durable queue state.
- `redis-jobs` is the Celery broker/result tier, uses AOF and `noeviction`, and
  must be monitored for memory pressure.
- Idempotency keys, database claims, renewable leases, and fencing tokens
  protect duplicate or stale execution.
- Strategy sandbox children are subordinate execution boundaries, not durable
  service owners. A trusted worker creates them, supplies bounded snapshots,
  validates their output, and remains responsible for recovery and order
  authorization.

## Read by task

| Task | Document |
| --- | --- |
| Understand package and process ownership | [Backend architecture](ARCHITECTURE.md) |
| Preserve dependency direction | [Module boundaries](MODULE_BOUNDARIES.md) |
| Change concurrent or durable work | [Concurrency model](CONCURRENCY_MODEL.md) |
| Decide which process owns work | [Process roles](PROCESS_ROLES_AND_TASKS.md) |
| Deploy or scale the event runtime | [Distributed runtime scaling](../deployment/DISTRIBUTED_RUNTIME_SCALING.md) |
| Review the proposed V6 100K-strategy architecture | [V6 hyperscale architecture plan](V6_HYPERSCALE_ARCHITECTURE_PLAN.md) |
| Add routes, adapters, tasks, or services | [Extension guide](EXTENSION_GUIDE.md) |
| Change an HTTP contract | [API conventions](API_CONVENTIONS.md) |

Before a large change, identify the owner process, source of truth, retry and
idempotency behavior, and the test that proves the boundary remains intact.
