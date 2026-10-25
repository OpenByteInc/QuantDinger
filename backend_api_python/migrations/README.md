# Database bootstrap layout

QuantDinger uses a repeatable bootstrap plus explicit upgrade components. The
files in this directory do not all have the same lifecycle.

## Canonical schema

`init.sql` is the canonical schema for a fresh installation. Docker mounts it
into PostgreSQL's `/docker-entrypoint-initdb.d` directory, and the backend also
applies it through the bootstrap ledger. Keep current table, index, constraint,
and small reference-row definitions here.

When a schema change must also update an already deployed database, add an
idempotent dated migration and register it in `app/utils/db.py`. The final
schema still belongs in `init.sql` so a fresh database does not have to replay
the full migration history.

## Upgrade migrations

Files named `YYYYMMDD_description.sql` contain upgrade-time changes or data
repairs for existing installations. Only components explicitly registered in
`app/utils/db.py` run automatically. Do not rely on PostgreSQL executing every
file in this directory.

Upgrade SQL must be idempotent and safe to retry. Use `IF EXISTS`,
`IF NOT EXISTS`, guarded updates, or conflict handling where appropriate.

## Bulk seed components

Large catalogues and bundled datasets stay in separate SQL files instead of
being appended to `init.sql`:

- `market_symbols_master.sql`
- `strategy_v2_templates.sql`
- `20261025_default_cn_hk_universe_members.sql`
- `20261025_default_global_universe_members.sql`

The universe definitions and their schema remain in `init.sql`; the two
universe member files only install bundled constituent data. Their generated
source documents live under `app/data/`.

Keeping bulk data separate prevents an unrelated schema edit from reparsing a
multi-megabyte seed on every existing installation. It also lets the backend
install new bundled data into an existing PostgreSQL volume, where Docker's
first-boot initialization scripts no longer run.

## Execution and tracking

`app/utils/db.py` creates `qd_bootstrap_migrations` and records each component
by name and SHA-256 checksum. An unchanged component is skipped. A changed
component runs again, so every registered SQL file must remain retry-safe.

The normal deployment path requires `SKIP_AUTO_MIGRATE=false` (the default).
Container logs expose each component as `applying`, `complete`, or `skipped`.

