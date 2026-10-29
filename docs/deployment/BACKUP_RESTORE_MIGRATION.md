# Backup, restore, and migration

This guide is for self-hosted administrators. A useful backup must restore a working QuantDinger deployment, not merely produce a database file.

## What to preserve

| Asset | Required | Purpose |
| --- | --- | --- |
| PostgreSQL database | Yes | Users, strategies, backtests, orders, and durable settings |
| `.env` | Yes | Ports, deployment mode, and external-service configuration |
| `CREDENTIAL_ENCRYPTION_KEY` | Yes | Decrypts saved broker, exchange, and provider credentials |
| Reverse-proxy and certificate configuration | Production | Domains, HTTPS, and proxy rules |
| Custom strategies, scripts, and mounted directories | As used | Assets outside the database and images |
| Image version or `IMAGE_TAG` | Recommended | Reproduces the runtime that created the backup |

Never commit `.env`, keys, or database backups to Git. Encrypt backups and store them separately from the production host.

## Create a consistent backup

1. Record the current services and images:

   ```bash
   docker compose ps
   docker compose images
   ```

2. Export PostgreSQL in custom format:

   ```bash
   mkdir -p backups
   docker exec quantdinger-db pg_dump -U quantdinger -Fc -d quantdinger -f /tmp/quantdinger.dump
   docker cp quantdinger-db:/tmp/quantdinger.dump ./backups/quantdinger.dump
   ```

3. Copy `.env`, proxy configuration, and custom mounts to encrypted backup storage.
4. Record the timestamp, QuantDinger version, database image version, and checksums.
5. Perform a restore drill in an isolated environment. A backup that has not been restored successfully is not yet proven.

For a busy production deployment, pause strategies and new job submission until the database export completes.

## Restore into a new environment

1. Install Docker and a compatible QuantDinger version.
2. Restore `.env`, retaining the original `CREDENTIAL_ENCRYPTION_KEY`.
3. Start PostgreSQL and stop application services that can write to it.
4. Copy and restore the dump:

   ```bash
   docker cp ./backups/quantdinger.dump quantdinger-db:/tmp/restore.dump
   docker exec quantdinger-db pg_restore -U quantdinger -d quantdinger --clean --if-exists /tmp/restore.dump
   ```

5. Run the target version's migrations and start the stack:

   ```bash
   docker compose up -d migration
   docker compose up -d
   ```

6. Verify health, administrator login, strategy and backtest history, broker accounts, notifications, and queues in that order.

Restore into staging first whenever possible. Do not run `docker compose down -v` during recovery because it deletes data volumes.

## Move to another host

Use an old-host backup, new-host restore, acceptance test, and traffic cutover:

1. Lower DNS TTL and record the old version.
2. Pause new strategies, schedules, and write entry points on the old host.
3. Create the final database backup and copy configuration and custom assets.
4. Restore on the new host and validate through a temporary domain.
5. Switch DNS or the load balancer and monitor health, logs, and order state.
6. Keep the old environment as a read-only rollback point until the new one is stable.

For a cross-version move, restore the old version first and then follow [Upgrade and rollback](UPGRADE_ROLLBACK.md). Do not attach a restored database to an unverified older application version.

## Recurring checklist

- Back up daily or according to the required recovery point objective.
- Create a non-overwriting full backup before every upgrade.
- Complete an isolated restore drill at least quarterly.
- Verify checksums, encryption keys, and retention regularly.
- Restrict backup credentials and audit download and restore operations.

Related: [Upgrade and rollback](UPGRADE_ROLLBACK.md) · [Production hardening](PRODUCTION_HARDENING.md) · [Observability](OBSERVABILITY.md)
