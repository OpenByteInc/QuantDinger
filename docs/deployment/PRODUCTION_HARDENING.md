# Production Hardening

## Preflight

Create the project-root `.env`, then replace every default credential. Validate it before deployment:

```bash
python backend_api_python/scripts/check_production_config.py \
  --env-file .env \
  --require-grafana
```

The guard rejects default database, administrator, Grafana, JWT, and credential-encryption secrets. Omit `--require-grafana` when the optional observability overlay is not deployed.

## Locked runtime

The production override runs backend processes as UID/GID `10001`, drops Linux capabilities, makes the root filesystem read-only, constrains memory/CPU, and mounts the backend environment file read-only:

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.production.yml \
  -f docker-compose.observability.yml \
  up -d --build
```

Prepare both backend secrets before the first locked start because the non-root containers cannot generate or persist them. Run migrations through the bundled migration service; do not run migrations concurrently from API workers.

## Untrusted-code sandbox

Indicator and Strategy API V2 source is not executed in the API, trading,
evaluator, or backtest owner process. The official image starts clean-environment
child processes and applies seccomp, Landlock, resource limits, and JSON-only
transport before user handlers run.

- Use a supported `x86_64` or `aarch64` Linux host. A missing kernel boundary is
  a fatal strategy/indicator execution error, not a reason to enable an
  in-process fallback.
- Do not pass the project `.env`, Docker socket, cloud credential directories,
  database sockets, or exchange credential files to sandbox children.
- Keep the strategy timeout and memory settings in `.env` bounded. Account for
  one persistent child per active live Strategy API V2 deployment and one
  disposable child per backtest.
- After upgrading the sandbox implementation, recreate the API, trading,
  evaluator, and backtest-worker containers. A frontend-only restart does not
  activate the new boundary.

## Redis tiers

`redis` is a disposable cache with `allkeys-lru`. `redis-jobs` is a durable Celery broker/result store with AOF, periodic snapshots, and `noeviction`. They use separate passwords (`REDIS_PASSWORD` and `CELERY_REDIS_PASSWORD`), separate databases, and separate memory limits.

Back up the `celery_redis_data` volume when queued work must survive a host loss. Cache data does not require backup. Monitor job Redis memory and increase `REDIS_JOBS_MAXMEMORY` before it reaches the limit; `noeviction` intentionally rejects new writes instead of silently losing queued work.

Keep PostgreSQL and Redis ports on their default loopback bindings. Public access should terminate at a TLS reverse proxy in front of the frontend and backend only.
