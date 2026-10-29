# QuantDinger configuration reference

QuantDinger uses both the Settings UI and the root `.env`. Manage normal product settings in **Settings**; manage ports, infrastructure, durable secrets, and startup-only values in the host `.env`. The complete machine-readable inventory is `.env.example`.

## Sources and lifecycle

| Source | Scope | Apply action |
| --- | --- | --- |
| Settings | Product, AI, data, notifications, branding | Save; restart only when prompted |
| Root `.env` | Ports, database, Redis, Kafka, secrets, workers | Recreate affected containers |
| Compose | Topology, volumes, networks, health checks | `docker compose up -d` |
| External infrastructure | DNS, TLS, database, email, payment provider | Validate with the provider |

Do not maintain conflicting `.env` files in multiple installation directories. Hardened production may mount the file read-only; edit it on the host.

## Settings structure

The UI groups Product features, Security & Authentication, AI / LLM, Live Trading, Market Modules, Professional report data, News & web search, AI Agent, Data status & sync, Email, SMS, Network & Proxy, Registration & Security, System runtime, and organization settings.

## Durable secrets

Back up and never casually regenerate `POSTGRES_PASSWORD`, `SECRET_KEY`, `CREDENTIAL_ENCRYPTION_KEY`, or `DATABASE_URL`. Protect OAuth, SMTP, and billing secrets as production credentials. Rotation requires a planned procedure and validation.

## Common groups

- **URLs and ports:** `FRONTEND_HOST`, `FRONTEND_PORT`, `MOBILE_HOST`, `MOBILE_PORT`, `BACKEND_PORT`, `FRONTEND_URL`.
- **Product and identity:** deployment mode, registration, billing, marketplace, administrator, OAuth, Turnstile.
- **AI:** `LLM_PROVIDER` and the selected provider's key, model, and Base URL.
- **Data, trading, and Agent:** providers, proxy, local-broker policy, reconciliation, execution streams, and `AGENT_*` limits.
- **Infrastructure:** database pool, Redis memory, Kafka, Celery, worker replicas, logs, and observability ports.

## Safe change workflow

Record the version and purpose, back up the database and `.env`, modify one group, run `docker compose config`, recreate only affected services, validate health/logs/business flows, and restore the previous environment and pinned image on failure. Never use volume deletion as rollback.

The canonical defaults and field comments are in [`.env.example`](../../.env.example).

