# Compatibility matrix

Use this page to decide whether an environment is appropriate before installing. It describes the current v5 documentation baseline and does not replace regional or account requirements imposed by a broker, exchange, model provider, or app store.

## Server and browser

| Environment | Status | Guidance |
| --- | --- | --- |
| Windows 11 x86-64 + Docker Desktop/WSL 2 | Recommended for personal and development use | 16 GB RAM is preferable for concurrent AI and backtests |
| Windows 10 22H2 x86-64 | Supported | Use supported Docker Desktop and WSL 2 releases |
| Ubuntu/Debian x86-64 + Docker Engine | Recommended for production | Use Compose v2, pinned images, and HTTPS |
| macOS + Docker Desktop | Supported for personal and development use | Apple Silicon requires images with the relevant architecture |
| Other Linux distributions or ARM servers | Conditional | Validate every image, database, and broker SDK in staging |
| Current Chrome / Edge | Recommended | Enable cookies, WebSocket, and JavaScript |
| Current Firefox / Safari | Core workflows supported | Verify charts, downloads, and realtime state before rollout |
| Internet Explorer | Unsupported | Use a modern browser |

## Recommended hardware profiles

These are starting points when the application and database share one host. Actual capacity depends on historical data, concurrent backtests, strategy count, users, and log retention.

| Scenario | CPU | Memory | Free storage | Intended use |
| --- | --- | --- | --- | --- |
| Minimum evaluation | 4 x86-64 cores | 8 GB | 30 GB SSD | Single-user evaluation, limited data, and short backtests; not recommended for live trading |
| Personal daily use | 6–8 cores | 16 GB | 100 GB SSD | AI research, routine backtests, and a few paper strategies |
| Small team or live use | 8–12 cores | 32 GB | 200 GB+ SSD | Multiple users, continuous jobs, several strategies, and production monitoring |
| Large research workload | 16+ cores | 64 GB+ | 500 GB+ NVMe | Concurrent backtests, large universes, and long history |

The CPU must support 64-bit virtualization. The database, queues, and backtests write continuously, so prefer SSD/NVMe. Do not place production volumes on a mechanical disk, ephemeral disk, or desktop synchronization folder. Expand storage or reduce log and history retention before utilization reaches 70%.

### Docker allocation

- Allocate at least 4 cores and 8 GB RAM to Docker Desktop on Windows/macOS; 6 cores and 12 GB+ is recommended for daily use.
- Reserve at least 2 cores and 4 GB RAM for the host instead of assigning all resources to Docker.
- Configure reasonable swap on Linux for short peaks, but do not use swap as a substitute for memory.
- Put PostgreSQL on a durable volume for multi-user or high-concurrency deployments and monitor latency, connections, and growth.

### Is a GPU required?

The server, web app, backtests, and cloud-model APIs do not require a GPU. A discrete GPU is needed only when the same host runs a local LLM, GPU data processing, or a custom inference service. VRAM depends on the model, and local inference must not consume capacity reserved for the database and trading runtime.

### Network and clock

- Installation requires GitHub/GHCR and configured providers; prepare a trusted proxy on a restricted network.
- Public production deployments should use a stable wired connection, fixed domain, HTTPS, and reliable DNS.
- Brokers, exchanges, OAuth, and signed APIs require accurate time; enable NTP synchronization.
- Do not expose database, Redis, Kafka, or internal backend ports to the internet beyond the intended 80/443 entry points.

## Clients

| Client | Dependency | Notes |
| --- | --- | --- |
| Desktop web | QuantDinger server | Full administration, research, backtesting, and trading |
| Mobile H5 | A server URL reachable from the phone | Browser client, not a standalone backend |
| Android / iOS package | Server with a configured public HTTPS API | Follow [Mobile client deployment](MOBILE_CLIENT_DEPLOYMENT.md) for build, signing, and store release |

## External-service compatibility

- Broker capability depends on account type, region, market permissions, paper/live environment, and SDK version.
- AI model names and endpoints change; use the connection test under Settings → AI / LLM.
- Data providers differ in frequency, adjustment policy, timezone, history, and licensing.
- OAuth, email, SMS, and payment callbacks require a public HTTPS domain in production.
- Local desktop broker gateways must be reachable from the container network and restricted to trusted hosts.

## Release acceptance baseline

Each release should verify fresh installation, upgrade from the previous stable version, migration, administrator login, research and backtest, mobile H5, at least one paper broker, backup and restore, mainstream browsers, and a rollback path. Mark unverified combinations as conditional rather than calling them compatible because they start.

When reporting a compatibility issue, include the release, OS, CPU architecture, Docker/Compose versions, browser, and redacted logs. See the [Error knowledge base](ERROR_KNOWLEDGE_BASE.md).
