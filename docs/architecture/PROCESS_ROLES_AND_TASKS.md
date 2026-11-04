# Backend Process Roles and Durable Tasks

The production deployment uses one backend image with independent process roles.

| Role | Command | Responsibility |
| --- | --- | --- |
| API | `gunicorn -c gunicorn_config.py run:app` | HTTP, authentication, validation, durable command submission |
| Migration | `python -m app.commands.migrate` | Fail-fast schema application before services start |
| Kafka Init | `python -m app.commands.kafka_topics` | Idempotent versioned topic creation before consumers start |
| Trading | `python -m app.commands.trading_worker` | Control/realtime runtimes, pending-order execution, private streams, reconciliation, durable grid actors |
| Strategy Dispatcher | `python -m app.commands.strategy_dispatcher_worker` | Closed-bar consumption and strategy-shard evaluation batch publication |
| Strategy Evaluator | `python -m app.commands.strategy_evaluator_worker` | Hot distributed bar runtimes, event inboxes, fenced evaluation, checkpoints, order intents |
| Kafka Audit | `python -m app.commands.kafka_audit_worker` | Independent event-contract and delivery audit |
| Scheduler | `python -m app.commands.scheduler` | Portfolio monitoring, deployment schedules, payment scans, signal alerts |
| Celery Worker | `celery -A app.celery_app:celery_app worker -Q ai,maintenance` | AI, reports, and maintenance jobs |
| Backtest Worker | `celery -A app.celery_app:celery_app worker -Q backtest` | Backtests, factor research, and finite experiment jobs; each child exits after one task |
| Celery Beat | `celery -A app.celery_app:celery_app beat` | Periodic maintenance dispatch |

## Ownership rules

- HTTP processes never start trading or scheduler threads.
- Strategy start, stop, restart, and reconcile requests use `qd_strategy_commands`.
- Trading workers claim commands with PostgreSQL `SKIP LOCKED` semantics.
- A strategy runtime requires a renewable row in `qd_strategy_runtime_leases`.
- Fencing tokens increase when an expired runtime is taken over by another worker.
- Ordinary closed-bar Strategy V2 runtimes are dispatched through Kafka and
  owned by evaluator workers when distributed mode is enabled.
- Grid, martingale, DCA, tick-driven, and session-driven strategies remain on
  the realtime trading-worker path unless their contract explicitly supports
  distributed closed-bar evaluation.
- Grid execution events are persisted in `qd_grid_actor_events`; only the
  trading worker that owns the strategy lease may claim them. Grid actor state
  is checkpointed in `qd_grid_actor_state`.
- Exchange order submission remains behind the pending-order gateway. Runtime
  fencing tokens are validated before an order can be claimed and sent.
- Global exchange pollers and scheduler loops use `qd_process_leases` leader ownership.
- Worker health is recorded in `qd_worker_heartbeats`.

## Celery boundary

Celery owns finite jobs that can be serialized, retried, and observed independently:

- fast AI analysis;
- human and agent backtests;
- factor research, factor benchmarks, and strategy evolution;
- reflection and AI calibration;
- market catalog synchronization;
- runtime metadata cleanup.

Celery must not own long-lived strategy loops, exchange polling, broker sessions, or grid runtime state. Those remain in the trading process because they require renewable ownership, reconciliation, and controlled shutdown.

Memory-intensive jobs use the dedicated `backtest` queue. Its worker defaults
to one child, `max-tasks-per-child=1`, and a 1.5 GiB child memory threshold.
Process-local K-line and shared-frame caches are also bounded by byte budgets.
The general Celery worker does not consume this queue.

## Redis separation

The cache Redis instance may use an eviction policy. Celery uses `redis-jobs`, which enables AOF persistence and `noeviction`. Queue state must never share an evictable Redis memory policy.

## Deployment sequence

Docker Compose enforces this order:

1. PostgreSQL, both Redis instances, and Kafka become healthy.
2. Database migration and Kafka topic initialization exit successfully.
3. API, trading, dispatcher, evaluator, audit, scheduler, both Celery workers, and Celery Beat start.
4. Health checks require fresh worker heartbeats and the runtime consumers expose their role-specific health state.

For a single host, replica counts are controlled through
`TRADING_WORKER_REPLICAS`, `STRATEGY_DISPATCHER_REPLICAS`, and
`STRATEGY_EVALUATOR_REPLICAS`. A multi-host deployment must use shared external
PostgreSQL, Redis, and Kafka services plus an orchestrator; copying the default
Compose file to another host would create split-brain stateful services.

Use these endpoints for operations:

- `/api/health` for liveness;
- `/api/health/ready` for PostgreSQL and Celery Broker readiness;
- `/api/health/workers` for trading, scheduler, and Celery heartbeat summaries.
