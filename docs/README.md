# QuantDinger Documentation

QuantDinger is a self-hosted AI trading platform that connects market research,
Strategy API V2 development, backtesting, paper/live execution, operations, and
AI-agent access. This documentation describes the current v5 release.

[中文文档](../README_CN.md) · [Official website](https://quantdinger.com) ·
[Web app](https://ai.quantdinger.com) · [GitHub](https://github.com/OpenByteInc/QuantDinger)

> Live trading can submit real orders. Start in paper mode, grant exchange keys
> the minimum permissions required, and confirm the legal and operational
> requirements for your jurisdiction. QuantDinger does not provide investment
> advice.

## Choose your goal

| Goal | Start here | Continue with |
| --- | --- | --- |
| First use | [First-run guide](getting-started/FIRST_RUN.md) | [Installation troubleshooting](deployment/INSTALL_TROUBLESHOOTING.md) |
| Use AI research | [AI Research guide](product/AI_RESEARCH.md) | [Strategy workflow](product/STRATEGY_WORKFLOW.md) |
| Run a backtest | [Backtest Center guide](product/BACKTEST_CENTER.md) | [Strategy API V2 guide](trading/STRATEGY_DEV_GUIDE.md) |
| Prepare live trading | [Live-trading safety](trading/LIVE_TRADING_SAFETY.md) | [Security and reporting](security/README.md) |
| Prepare production | [Production hardening](deployment/PRODUCTION_HARDENING.md) | [Observability](deployment/OBSERVABILITY.md) |
| Release or scale runtime workers | [Distributed runtime scaling](deployment/DISTRIBUTED_RUNTIME_SCALING.md) | [V6 capacity plan](architecture/V6_HYPERSCALE_ARCHITECTURE_PLAN.md) |
| Connect an AI agent | [MCP setup](agent/MCP_SETUP.md) | [Agent Gateway quickstart](agent/AGENT_QUICKSTART.md) |
| Integrate over HTTP | [Human OpenAPI](api/README.md) | [API conventions](architecture/API_CONVENTIONS.md) |
| Extend the backend | [Architecture overview](architecture/README.md) | [Extension guide](architecture/EXTENSION_GUIDE.md) |

## Five-minute installation

Prerequisites: Docker with Compose v2. The installer asks for an administrator
account, generates the required secrets, writes the deployment environment, and
starts the prebuilt GHCR stack.

Linux or macOS:

```bash
curl -fsSL https://raw.githubusercontent.com/OpenByteInc/QuantDinger/main/install.sh | bash
```

Windows PowerShell:

```powershell
irm https://raw.githubusercontent.com/OpenByteInc/QuantDinger/main/install.ps1 | iex
```

After startup:

| Surface | Default local address |
| --- | --- |
| Desktop web app | `http://127.0.0.1:8888` |
| Mobile web app | `http://127.0.0.1:8889` |
| Backend health check | `http://127.0.0.1:5000/api/health` |

For source builds, reverse proxies, HTTPS, upgrades, and proxy settings, use the
[cloud deployment guide](deployment/CLOUD_DEPLOYMENT_EN.md).

## System map

The v5 runtime separates long-lived ownership from request handling:

| Process | Responsibility |
| --- | --- |
| `migration` | Applies database schema changes before dependent services start. |
| `kafka-init` | Creates the versioned runtime topics before event consumers start. |
| `backend` | HTTP authentication, validation, queries, and durable command submission. |
| `trading-worker` | Control/realtime runtimes, broker sessions, fenced order execution, reconciliation, and grid actors. |
| `strategy-dispatcher-worker` | Converts closed bars into stable strategy-shard evaluation batches. |
| `strategy-evaluator-worker` | Hosts hot distributed bar runtimes and produces fenced order intents. |
| `kafka-audit-worker` | Audits the versioned runtime event stream. |
| `scheduler-worker` | Portfolio, deployment, payment, and notification schedules. |
| `celery-worker` | Finite retryable work such as AI jobs, backtests, reports, and maintenance. |
| `celery-beat` | Periodically enqueues Celery work. |

PostgreSQL is the system of record. `redis` is an evictable cache;
`redis-jobs` is the durable Celery broker/result tier, and Kafka is the ordered
runtime event backbone. See the
[architecture overview](architecture/README.md) before changing process
ownership or shared state.

## Documentation map

### Getting started and product guides

- [First run with QuantDinger](getting-started/FIRST_RUN.md)
- [AI Research guide](product/AI_RESEARCH.md)
- [From strategy idea to operation](product/STRATEGY_WORKFLOW.md)
- [Backtest Center guide](product/BACKTEST_CENTER.md)
- [Live-trading safety guide](trading/LIVE_TRADING_SAFETY.md)
- [Security, versions, and vulnerability reporting](security/README.md)

### Open source assurance

- [Compliance and security assurance program](compliance/README.md)
- [OpenChain ISO/IEC 5230 self-assessment](compliance/OPENCHAIN_ISO_5230_SELF_ASSESSMENT.md)
- [OpenChain ISO/IEC 18974 self-assessment](compliance/OPENCHAIN_ISO_18974_SELF_ASSESSMENT.md)
- [Release compliance checklist](compliance/RELEASE_COMPLIANCE_CHECKLIST.md)

### Deployment and operations

- [Cloud deployment](deployment/CLOUD_DEPLOYMENT_EN.md)
- [Distributed runtime deployment and scaling](deployment/DISTRIBUTED_RUNTIME_SCALING.md)
- [分布式运行时部署与扩容](deployment/DISTRIBUTED_RUNTIME_SCALING_CN.md)
- [Installation troubleshooting](deployment/INSTALL_TROUBLESHOOTING.md)
- [Production hardening](deployment/PRODUCTION_HARDENING.md)
- [Observability](deployment/OBSERVABILITY.md)
- [Multi-user operation](deployment/MULTI_USER_SETUP.md)
- [OAuth configuration](deployment/OAUTH_CONFIG_EN.md)
- [Administrator and settings troubleshooting](deployment/ADMIN_AND_SETTINGS_TROUBLESHOOTING_EN.md)
- Notifications: [Email](deployment/NOTIFICATION_EMAIL_CONFIG_EN.md),
  [SMS](deployment/NOTIFICATION_SMS_CONFIG_EN.md), and
  [Telegram](deployment/NOTIFICATION_TELEGRAM_CONFIG_EN.md)

### Architecture and scale planning

- [Architecture overview](architecture/README.md)
- [Concurrency model](architecture/CONCURRENCY_MODEL.md)
- [Process and task ownership](architecture/PROCESS_ROLES_AND_TASKS.md)
- [V6 hyperscale architecture plan](architecture/V6_HYPERSCALE_ARCHITECTURE_PLAN.md)

### Trading and research

- [Strategy API V2 development](trading/STRATEGY_DEV_GUIDE.md)
- [Chart indicator development](trading/INDICATOR_DEV_GUIDE.md)
- [Interactive Brokers](trading/IBKR_TRADING_GUIDE_EN.md)
- [Live-trading safety](trading/LIVE_TRADING_SAFETY.md)
- [Public universes and point-in-time fundamentals (Chinese)](trading/PUBLIC_UNIVERSE_AND_FUNDAMENTALS_CN.md)
- [Fundamental-data preparation operations (Chinese)](strategies/FUNDAMENTAL_DATA_PREPARATION_CN.md)
- Runnable examples in [`examples/`](examples/)

### APIs and agents

- [Human Web API and OpenAPI](api/README.md)
- [Agent documentation](agent/README.md)
- [MCP setup](agent/MCP_SETUP.md)
- [Agent Gateway quickstart](agent/AGENT_QUICKSTART.md)
- Machine-readable contracts:
  [`api/openapi.yaml`](api/openapi.yaml) and
  [`agent/agent-openapi.json`](agent/agent-openapi.json)

## Language and maintenance policy

Each human-readable page uses one primary language. English and Chinese pages
are paired when both are maintained; the website language switch links directly
to the paired page. Machine-readable schemas, identifiers, environment variable
names, and code remain in their canonical English form.

The repository `docs/` directory is the source of truth. Update the relevant
index and counterpart language page when a contract or workflow changes. Do not
publish planning notes, completed roadmaps, or temporary audits as product
documentation.
