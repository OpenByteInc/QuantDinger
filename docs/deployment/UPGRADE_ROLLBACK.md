# Upgrade and rollback

Treat an upgrade as a reversible change, not as a simple `pull`. This workflow targets self-hosted Docker Compose deployments.

## Before upgrading

1. Read the target release notes and identify database, configuration, and broker changes.
2. Record the current commit, image digest, `IMAGE_TAG`, and service state.
3. Back up the database, `.env`, `CREDENTIAL_ENCRYPTION_KEY`, and custom assets using [Backup, restore, and migration](BACKUP_RESTORE_MIGRATION.md).
4. Pause live strategies or enter a maintenance window; check for open orders and long-running jobs.
5. Rehearse with a sanitized production backup in staging.

## Standard upgrade

For prebuilt images:

```bash
docker compose pull
docker compose up -d migration
docker compose up -d
docker compose ps
```

For a source deployment, check out the intended tag or commit and rebuild using the repository workflow. Pin production to a versioned tag rather than relying indefinitely on `latest`.

Validate in order:

1. `/api/health` and container health.
2. Administrator login, authorization, and system settings.
3. Data status, research, strategy compilation, and a small backtest.
4. Broker connectivity, account queries, and paper execution.
5. Queues, notifications, logs, disk usage, and error rate.

## When to roll back

Stop expanding the change and evaluate rollback when health checks remain unhealthy, migrations fail, a core surface is unavailable, order or account state is inconsistent, error rate rises persistently, or critical queues accumulate.

## Rollback methods

### Application-image rollback only

Only when the database remains backward compatible, restore the previous `IMAGE_TAG` and restart:

```bash
docker compose pull
docker compose up -d
```

### Incompatible database migration occurred

1. Stop all writers.
2. Restore both the pre-upgrade application version and full database backup.
3. Use the same `.env` and `CREDENTIAL_ENCRYPTION_KEY`.
4. Validate on an isolated address before restoring traffic.

Do not manipulate migration records manually to simulate a rollback, and never use `docker compose down -v` as part of rollback.

## Change record

Record the operator, window, old and target versions, backup location, migration result, acceptance checks, rollback triggers, and final decision. For an incident, add a timeline and corrective actions.

Related: [Configuration reference](CONFIGURATION_REFERENCE.md) · [Compatibility matrix](COMPATIBILITY_MATRIX.md) · [Error knowledge base](ERROR_KNOWLEDGE_BASE.md)
