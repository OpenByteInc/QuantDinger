# Error knowledge base and diagnostic path

Identify the failing layer before changing configuration. Do not disable security checks, delete volumes, or repeatedly reinstall merely to remove an error message.

## Collect this first

```bash
docker compose ps
docker compose logs --tail=200 backend
docker compose logs --tail=200 migration
docker compose logs --tail=200 postgres redis redis-jobs
```

Also record the timestamp and timezone, page and action, request ID when available, version or image digest, browser, and recent changes. Redact tokens, cookies, API keys, email addresses, complete account numbers, and sensitive order fields before sharing logs.

## Fast classification

| Symptom | Common cause | First check |
| --- | --- | --- |
| UI does not open | Frontend stopped, port conflict, proxy error | `docker compose ps`, port 8888, proxy upstream |
| UI opens but APIs fail | Backend unhealthy, CORS, or wrong URL | `/api/health`, `FRONTEND_URL`, backend logs |
| First start never completes | Database unavailable or migration failure | `migration` and `postgres` logs |
| Login fails | Admin record, clock, cookie, or OAuth callback | System time, domain, HTTPS, auth settings |
| AI produces no result | Wrong key/model/endpoint, rate limit, or insufficient balance | AI / LLM connection test and provider response |
| Data is empty or stale | Provider missing, sync failed, or market closed | Data status & sync, symbol/timezone/frequency |
| Backtest fails | Insufficient data, strategy parameters, or resource limit | Job detail, Celery logs, date range |
| Broker will not connect | Gateway unreachable, permission, allowlist, or clock | Network path, account scopes, paper environment |
| Jobs remain queued | `redis-jobs` or a worker is unhealthy | Workers, Celery, disk, and memory |
| Upgrade broke the service | Missing environment, migration, or version mismatch | Release notes, migration logs, backup and rollback criteria |

## Diagnose by layer

1. **Browser**: hard refresh and inspect Console and Network; confirm the intended domain and HTTPS.
2. **Edge**: inspect DNS, certificate, reverse proxy, upload limits, and timeouts.
3. **Application**: inspect `/api/health`, backend logs, deployment mode, and environment.
4. **Jobs**: inspect Celery, scheduler, strategy workers, and `redis-jobs`.
5. **Data**: inspect migration, PostgreSQL, disk space, and connection count.
6. **Provider**: inspect provider status, scopes, balance, limits, allowlists, and system clock.

## Safe response sequence

1. Preserve evidence and logs.
2. Pause strategies or entry points that can expand impact.
3. Change one likely cause and record before/after state.
4. Validate with health checks and a minimal reproduction.
5. After recovery, document cause, impact, correction, and prevention.

## Do not

- Do not run `docker compose down -v` for an ordinary incident.
- Do not paste real secrets into an issue or chat.
- Do not edit migration history or production order data to unblock a task.
- Do not downgrade the application without a backup and compatibility decision.

See [Installation troubleshooting](INSTALL_TROUBLESHOOTING.md), [Administrator and settings troubleshooting](ADMIN_AND_SETTINGS_TROUBLESHOOTING_EN.md), and [Observability](OBSERVABILITY.md) for deeper guidance.
