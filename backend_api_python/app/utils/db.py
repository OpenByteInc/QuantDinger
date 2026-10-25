"""
Database Connection Utility - PostgreSQL Only

Provides unified interface for PostgreSQL database operations.

Usage:
    from app.utils.db import get_db_connection
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))
        row = cursor.fetchone()
        conn.commit()

Configuration:
    DATABASE_URL=postgresql://user:password@host:port/dbname
"""

import hashlib
import os
from pathlib import Path

# Re-export from PostgreSQL module
from app.utils.db_postgres import (
    get_pg_connection as get_db_connection,
    get_pg_transaction as get_db_transaction,  # noqa: F401 - public compatibility export
    get_pg_connection_sync as get_db_connection_sync,
    is_postgres_available,
    close_pool as close_db,
)

# Tables that every backend worker / route touches in the hot path.  If the
# configured Postgres user can't `SELECT` from these, the server starts but
# everything past auth quietly errors with InsufficientPrivilege; we surface
# that loudly at boot instead of silently spamming worker logs forever.
_CRITICAL_TABLES = (
    'qd_users',
    'pending_orders',
    'qd_strategy_equity_snapshots',
    'qd_strategy_positions',
    'qd_position_reservations',
    'qd_strategies_trading',
    'qd_analysis_memory',
    'qd_strategy_commands',
    'qd_strategy_runtime_leases',
    'qd_worker_heartbeats',
    'qd_grid_actor_state',
    'qd_grid_actor_events',
)


def get_db_type() -> str:
    """Get database type (always postgresql)"""
    return 'postgresql'


def is_postgres() -> bool:
    """Check if using PostgreSQL (always True)"""
    return True


def init_database(*, strict_migrations: bool = False):
    """Initialize the database connection, apply schema, and probe permissions.

    Two deployment styles have to land here without diverging:

    1. **Docker compose** — the Postgres container's entrypoint runs
       ``/docker-entrypoint-initdb.d/init.sql`` on first boot. The backend then
       applies the canonical schema and registered upgrade/seed components
       through ``qd_bootstrap_migrations`` on every start.
    2. **Bare-metal / Windows local PG** — nothing runs ``init.sql`` for the
       operator. Previously they had to manually ``psql -f migrations/init.sql``
       before starting the backend, otherwise every worker exploded with
       ``relation does not exist``. We now apply it ourselves.

    Bulk catalogues stay in separate registered components so existing Docker
    volumes receive them without inflating the canonical schema script.

    After the bootstrap we ping every critical table with ``SELECT 1
    LIMIT 0``. This catches the *other* common deployment pitfall: the schema
    was created by ``postgres`` (superuser) but ``DATABASE_URL`` points to a
    non-owner user that lacks ``SELECT``. Without the probe the backend looks
    healthy at boot and only fails 10 seconds later inside ``PendingOrderWorker``.
    """
    from app.utils.logger import get_logger
    logger = get_logger(__name__)

    if not is_postgres_available():
        raise RuntimeError("Cannot connect to PostgreSQL. Check DATABASE_URL.")
    logger.info("PostgreSQL connection verified")

    if os.getenv('SKIP_AUTO_MIGRATE', '').lower() not in ('1', 'true', 'yes'):
        _apply_init_sql(logger, strict=strict_migrations)
    else:
        logger.info("SKIP_AUTO_MIGRATE is set; not running init.sql on boot")

    _verify_table_access(logger)


def _resolve_init_sql_path() -> Path:
    """Locate ``migrations/init.sql`` relative to this file.

    Extracted as its own function so tests can monkeypatch the path without
    needing to mock the whole ``Path(...).resolve().parent.parent.parent``
    chain (which is brittle and obscures intent).
    """
    return Path(__file__).resolve().parent.parent.parent / 'migrations' / 'init.sql'


def _resolve_market_symbols_sql_path() -> Path:
    return Path(__file__).resolve().parent.parent.parent / 'migrations' / 'market_symbols_master.sql'


def _resolve_strategy_templates_sql_path() -> Path:
    return Path(__file__).resolve().parent.parent.parent / 'migrations' / 'strategy_v2_templates.sql'


_BOOTSTRAP_LEDGER_SQL = """
CREATE TABLE IF NOT EXISTS qd_bootstrap_migrations (
    name VARCHAR(120) PRIMARY KEY,
    checksum VARCHAR(64) NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""


def _migration_timeout_seconds(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def _migration_checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _migration_progress(message: str) -> None:
    # Deployment panels normally show container stdout but the application
    # logger may be configured to write to /app/logs.  Keep migration progress
    # visible so an operator can tell schema work from a lock wait.
    print(f"[migration] {message}", flush=True)


def _fetch_applied_checksum(cur, name: str) -> str:
    cur.execute(
        "SELECT checksum FROM qd_bootstrap_migrations WHERE name = %s",
        (name,),
    )
    row = cur.fetchone()
    if not row:
        return ""
    if isinstance(row, dict):
        return str(row.get("checksum") or "")
    return str(row[0] or "")


def _record_applied_checksum(cur, name: str, checksum: str) -> None:
    # Explicit RETURNING avoids the compatibility cursor speculatively adding
    # `RETURNING id` to this text-primary-key table.
    cur.execute(
        """
        INSERT INTO qd_bootstrap_migrations (name, checksum, applied_at)
        VALUES (%s, %s, NOW())
        ON CONFLICT (name) DO UPDATE
        SET checksum = EXCLUDED.checksum, applied_at = NOW()
        RETURNING name
        """,
        (name, checksum),
    )


def _table_row_count(cur, table: str) -> int:
    # `table` is only supplied by the static component definitions below.
    cur.execute(f"SELECT COUNT(*) AS row_count FROM {table}")
    row = cur.fetchone()
    if not row:
        return 0
    if isinstance(row, dict):
        return int(row.get("row_count") or 0)
    return int(row[0] or 0)


def _apply_migration_component(
    conn,
    logger,
    *,
    name: str,
    path: Path,
    baseline_table: str = "",
    baseline_min_rows: int = 1,
) -> str:
    """Apply one bootstrap component once per content checksum.

    Older installations predate the ledger.  Large seed components can be
    baselined when their target is already populated; otherwise every Docker
    restart needlessly upserts tens of thousands of rows.  Schema SQL is never
    baselined and therefore still receives one upgrade pass.
    """
    if not path.exists():
        return "missing"

    checksum = _migration_checksum(path)
    cur = conn.cursor()
    try:
        applied_checksum = _fetch_applied_checksum(cur, name)
        if applied_checksum == checksum:
            logger.info("Migration component %s unchanged; skipping", name)
            _migration_progress(f"{name}: unchanged, skipped")
            return "skipped"

        if not applied_checksum and baseline_table:
            existing_rows = _table_row_count(cur, baseline_table)
            if existing_rows >= baseline_min_rows:
                _record_applied_checksum(cur, name, checksum)
                conn.commit()
                logger.info(
                    "Baselined migration component %s from %d existing rows",
                    name,
                    existing_rows,
                )
                _migration_progress(
                    f"{name}: existing data detected ({existing_rows} rows), baselined"
                )
                return "baselined"

        lock_timeout = _migration_timeout_seconds("MIGRATION_LOCK_TIMEOUT_SECONDS", 15)
        statement_timeout = _migration_timeout_seconds("MIGRATION_STATEMENT_TIMEOUT_SECONDS", 180)
        _migration_progress(f"{name}: applying {path.stat().st_size} bytes")
        cur.execute(f"SET LOCAL lock_timeout = '{lock_timeout}s'")
        cur.execute(f"SET LOCAL statement_timeout = '{statement_timeout}s'")
        cur.execute(path.read_text(encoding="utf-8"))
        _record_applied_checksum(cur, name, checksum)
        conn.commit()
        logger.info("Applied migration component %s (%d bytes)", name, path.stat().st_size)
        _migration_progress(f"{name}: complete")
        return "applied"
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        cur.close()


def _apply_init_sql(logger, *, strict: bool = False):
    """Run ``migrations/init.sql`` idempotently.

    Failures are downgraded to a warning rather than aborting startup — the
    most common cause of failure here is *also* the most common cause of the
    permission probe failing below (non-owner DB user). We want both signals
    visible in the log, not a hard crash that hides them.
    """
    init_sql = _resolve_init_sql_path()
    if not init_sql.exists():
        if strict:
            raise FileNotFoundError(f"Required migration file is missing: {init_sql}")
        logger.warning(
            "init.sql not found at %s — skipping auto-migrate. "
            "If you're on a fresh local PG, run it manually before starting the backend.",
            init_sql,
        )
        return

    try:
        symbols_sql = _resolve_market_symbols_sql_path()
        templates_sql = _resolve_strategy_templates_sql_path()
        with get_db_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(_BOOTSTRAP_LEDGER_SQL)
                conn.commit()
            finally:
                cur.close()

            _apply_migration_component(
                conn,
                logger,
                name="schema-init",
                path=init_sql,
            )
            _apply_migration_component(
                conn,
                logger,
                name="execution-fill-accounting-20260916",
                path=Path(__file__).resolve().parent.parent.parent / "migrations" / "20260916_fill_accounting.sql",
            )
            _apply_migration_component(
                conn,
                logger,
                name="exchange-order-pnl-20260916",
                path=Path(__file__).resolve().parent.parent.parent / "migrations" / "20260916_exchange_order_pnl.sql",
            )
            _apply_migration_component(
                conn,
                logger,
                name="ai-decision-billing-20260920",
                path=Path(__file__).resolve().parent.parent.parent / "migrations" / "20260920_ai_decision_billing.sql",
            )
            _apply_migration_component(
                conn,
                logger,
                name="quick-trade-client-order-id-20260926",
                path=Path(__file__).resolve().parent.parent.parent / "migrations" / "20260926_quick_trade_client_order_id.sql",
            )
            _apply_migration_component(
                conn,
                logger,
                name="signal-virtual-account-20260926",
                path=Path(__file__).resolve().parent.parent.parent / "migrations" / "20260926_signal_virtual_account.sql",
            )
            _apply_migration_component(
                conn,
                logger,
                name="signal-virtual-limit-orders-20260926",
                path=Path(__file__).resolve().parent.parent.parent / "migrations" / "20260926_signal_virtual_limit_orders.sql",
            )
            _apply_migration_component(
                conn,
                logger,
                name="position-alert-ownership-20261023",
                path=Path(__file__).resolve().parent.parent.parent / "migrations" / "20261023_position_alert_ownership.sql",
            )
            _apply_migration_component(
                conn,
                logger,
                name="public-universe-expansion-20261024",
                path=Path(__file__).resolve().parent.parent.parent / "migrations" / "20261024_public_universe_expansion.sql",
            )
            _apply_migration_component(
                conn,
                logger,
                name="default-cn-hk-universe-members-20261025",
                path=Path(__file__).resolve().parent.parent.parent / "migrations" / "20261025_default_cn_hk_universe_members.sql",
            )
            _apply_migration_component(
                conn,
                logger,
                name="default-global-universe-members-20261025",
                path=Path(__file__).resolve().parent.parent.parent / "migrations" / "20261025_default_global_universe_members.sql",
            )
            _apply_migration_component(
                conn,
                logger,
                name="market-symbols-master",
                path=symbols_sql,
                baseline_table="qd_market_symbols",
                # init.sql contains a small starter universe.  A production
                # master catalogue is much larger, so do not baseline a fresh
                # database before the full catalogue has been installed.
                baseline_min_rows=1000,
            )
            _apply_migration_component(
                conn,
                logger,
                name="strategy-v2-templates",
                path=templates_sql,
                baseline_table="qd_script_templates",
                baseline_min_rows=1,
            )
        logger.info("Database bootstrap components are current")
        _migration_progress("all components complete")
    except Exception as exc:
        if strict:
            raise
        logger.warning(
            "Auto-migrate failed (continuing with existing schema): %s. "
            "If this is a permission error, run 'ALTER TABLE ... OWNER TO <db_user>' "
            "or set SKIP_AUTO_MIGRATE=true to silence this on boot.",
            exc,
        )


def _verify_table_access(logger):
    """Probe ``SELECT 1`` on every critical table.

    Each probe runs in its own transaction so that one ``InsufficientPrivilege``
    doesn't abort the rest of the checks (Postgres puts the whole tx into a
    failed state after the first error). We collect all failures, then emit a
    single high-visibility banner so the operator sees the full list and the
    fix recipe at once.
    """
    failures = []
    try:
        with get_db_connection() as conn:
            for table in _CRITICAL_TABLES:
                cur = conn.cursor()
                try:
                    cur.execute(f"SELECT 1 FROM {table} LIMIT 0")
                except Exception as exc:
                    err_line = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
                    failures.append((table, err_line))
                    conn.rollback()
                finally:
                    cur.close()
    except Exception as exc:
        logger.warning("Permission probe could not run: %s", exc)
        return

    if not failures:
        logger.info("DB permission probe OK (%d critical tables readable)", len(_CRITICAL_TABLES))
        return

    bar = "=" * 72
    logger.error(bar)
    logger.error("DATABASE PERMISSION CHECK FAILED")
    logger.error("Backend connected, but the configured DB user cannot read:")
    for table, err in failures:
        logger.error("  - %s -> %s", table, err)
    logger.error("")
    logger.error("Most likely cause: tables were created by a DIFFERENT Postgres user")
    logger.error("(commonly `postgres` superuser) and the user in DATABASE_URL is")
    logger.error("neither the table owner nor has been granted access.")
    logger.error("")
    logger.error("Fix — connect as postgres superuser and run:")
    logger.error("  ALTER SCHEMA public OWNER TO <backend_user>;")
    logger.error("  DO $$ DECLARE r RECORD; BEGIN")
    logger.error("    FOR r IN SELECT tablename FROM pg_tables WHERE schemaname='public' LOOP")
    logger.error("      EXECUTE format('ALTER TABLE public.%I OWNER TO <backend_user>', r.tablename);")
    logger.error("    END LOOP;")
    logger.error("  END $$;")
    logger.error("  GRANT ALL ON ALL TABLES IN SCHEMA public TO <backend_user>;")
    logger.error("  GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO <backend_user>;")
    logger.error(bar)


# Legacy alias
def close_db_connection():
    """Legacy alias for close_db"""
    pass


__all__ = [
    'get_db_connection',
    'get_db_connection_sync',
    'close_db_connection',
    'init_database',
    'close_db',
    'get_db_type',
    'is_postgres',
]
