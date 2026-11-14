"""
PostgreSQL Database Connection Utility

Supports multi-user mode with connection pooling.
Provides placeholder conversion for backward compatibility with legacy code.

Pool tuning (all via env, safe defaults):
    DB_POOL_MIN               minconn                       default 5
    DB_POOL_MAX               maxconn or "auto"             default auto
    DB_POOL_ACQUIRE_TIMEOUT   seconds to wait on exhaustion default 10
    DB_POOL_HEALTH_CHECK      "true" / "false"              default "true"
    DB_IDLE_IN_TRANSACTION_TIMEOUT_MS server-side timeout    default 60000
    DB_LOCK_TIMEOUT_MS        server-side lock wait timeout  default 10000
"""
import os
import time
import threading
from typing import Optional, Any, List, Dict
from contextlib import contextmanager
from contextvars import ContextVar

from app.observability.db_metrics import (
    DB_POOL_ACQUIRE_FAILURES,
    DB_POOL_ACQUIRE_SECONDS,
    DB_POOL_CONNECTIONS,
    DB_POOL_UTILIZATION_RATIO,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Try to import psycopg2
try:
    import psycopg2
    from psycopg2 import pool
    from psycopg2 import OperationalError, InterfaceError
    from psycopg2.extras import RealDictCursor, execute_batch
    HAS_PSYCOPG2 = True
except ImportError:
    HAS_PSYCOPG2 = False
    logger.warning("psycopg2 not installed. PostgreSQL support disabled.")

# Connection pool (global singleton)
_connection_pool: Optional[Any] = None
_pool_lock = threading.Lock()


def _env_int(key: str, default: int) -> int:
    try:
        v = int(os.getenv(key, str(default)))
        return v if v > 0 else default
    except Exception:
        return default


def _env_optional_int(key: str) -> Optional[int]:
    raw = os.getenv(key)
    if raw is None:
        return None
    value = raw.strip().lower()
    if not value or value in ("auto", "default"):
        return None
    try:
        parsed = int(value)
        return parsed if parsed > 0 else None
    except Exception:
        logger.warning("Invalid %s=%r; using auto", key, raw)
        return None


def _env_bool(key: str, default: bool) -> bool:
    v = os.getenv(key)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


DB_POOL_MIN = _env_int("DB_POOL_MIN", 5)
DB_POOL_MAX_CONFIGURED = _env_optional_int("DB_POOL_MAX")
DB_POOL_AUTO_DEFAULT_MAX = _env_int("DB_POOL_AUTO_DEFAULT_MAX", 50)
DB_POOL_MAX = DB_POOL_MAX_CONFIGURED or DB_POOL_AUTO_DEFAULT_MAX
DB_POOL_ACQUIRE_TIMEOUT = _env_int("DB_POOL_ACQUIRE_TIMEOUT", 10)
DB_POOL_HEALTH_CHECK = _env_bool("DB_POOL_HEALTH_CHECK", True)
DB_POOL_AUTO_CAP = _env_bool("DB_POOL_AUTO_CAP", True)
DB_POOL_RESERVE_FOR_OTHER_CLIENTS = _env_int("DB_POOL_RESERVE_FOR_OTHER_CLIENTS", 20)
DB_IDLE_IN_TRANSACTION_TIMEOUT_MS = _env_int("DB_IDLE_IN_TRANSACTION_TIMEOUT_MS", 60_000)
DB_LOCK_TIMEOUT_MS = _env_int("DB_LOCK_TIMEOUT_MS", 10_000)
DB_APPLICATION_NAME = os.getenv("DB_APPLICATION_NAME", "quantdinger_api").strip() or "quantdinger_api"


def _get_database_url() -> str:
    """Get database connection URL from environment"""
    return os.getenv('DATABASE_URL', '').strip()


def _parse_database_url(url: str) -> Dict[str, Any]:
    """
    Parse DATABASE_URL format: postgresql://user:password@host:port/dbname
    """
    if not url:
        return {}
    
    # Remove protocol prefix
    if url.startswith('postgresql://'):
        url = url[13:]
    elif url.startswith('postgres://'):
        url = url[11:]
    else:
        return {}
    
    result = {}
    
    # Split user:password@host:port/dbname
    if '@' in url:
        auth, hostpart = url.rsplit('@', 1)
        if ':' in auth:
            result['user'], result['password'] = auth.split(':', 1)
        else:
            result['user'] = auth
    else:
        hostpart = url
    
    # Split host:port/dbname
    if '/' in hostpart:
        hostport, result['dbname'] = hostpart.split('/', 1)
    else:
        hostport = hostpart
    
    if ':' in hostport:
        result['host'], port_str = hostport.split(':', 1)
        result['port'] = int(port_str)
    else:
        result['host'] = hostport
        result['port'] = 5432
    
    return result


def _get_connection_pool():
    """Get or create connection pool"""
    global _connection_pool
    
    if _connection_pool is not None:
        return _connection_pool
    
    with _pool_lock:
        if _connection_pool is not None:
            return _connection_pool
        
        if not HAS_PSYCOPG2:
            raise RuntimeError("psycopg2 is not installed. Cannot use PostgreSQL.")
        
        db_url = _get_database_url()
        if not db_url:
            raise RuntimeError("DATABASE_URL environment variable is not set.")
        
        params = _parse_database_url(db_url)
        if not params:
            raise RuntimeError(f"Invalid DATABASE_URL format: {db_url}")
        
        effective_min, effective_max = _resolve_effective_pool_limits(params)

        try:
            _connection_pool = pool.ThreadedConnectionPool(
                minconn=effective_min,
                maxconn=effective_max,
                host=params.get('host', 'localhost'),
                port=params.get('port', 5432),
                user=params.get('user', 'quantdinger'),
                password=params.get('password', ''),
                dbname=params.get('dbname', 'quantdinger'),
                connect_timeout=10,
                application_name=DB_APPLICATION_NAME,
                # Apply timezone at connection establishment so we don't need
                # per-checkout SET TIME ZONE (which left connections in an
                # "idle in transaction" state when no explicit commit/rollback
                # followed).  keepalives keep dead sockets from lingering in
                # the pool when the PG side or a NAT drops them.
                options=(
                    "-c timezone=UTC "
                    f"-c idle_in_transaction_session_timeout={DB_IDLE_IN_TRANSACTION_TIMEOUT_MS} "
                    f"-c lock_timeout={DB_LOCK_TIMEOUT_MS}"
                ),
                keepalives=1,
                keepalives_idle=30,
                keepalives_interval=10,
                keepalives_count=3,
            )
            logger.info(
                f"PostgreSQL connection pool created: "
                f"{params.get('host')}:{params.get('port')}/{params.get('dbname')} "
                f"(min={effective_min}, max={effective_max}, "
                f"configured_min={DB_POOL_MIN}, configured_max={_pool_max_config_label()}, "
                f"acquire_timeout={DB_POOL_ACQUIRE_TIMEOUT}s, "
                f"health_check={DB_POOL_HEALTH_CHECK})"
            )
            _observe_pool_stats(_connection_pool)
        except Exception as e:
            logger.error(f"Failed to create PostgreSQL connection pool: {e}")
            raise
        
        return _connection_pool


def _show_pg_int(conn, setting: str, default: int = 0) -> int:
    cur = conn.cursor()
    try:
        cur.execute(f"SHOW {setting}")
        row = cur.fetchone()
        return int(row[0]) if row else default
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return default
    finally:
        try:
            cur.close()
        except Exception:
            pass


def _probe_pg_connection_limit(params: Dict[str, Any]) -> Optional[Dict[str, int]]:
    """Read PostgreSQL connection limits using a short-lived probe connection."""
    if not DB_POOL_AUTO_CAP:
        return None
    probe = None
    try:
        probe = psycopg2.connect(
            host=params.get('host', 'localhost'),
            port=params.get('port', 5432),
            user=params.get('user', 'quantdinger'),
            password=params.get('password', ''),
            dbname=params.get('dbname', 'quantdinger'),
            connect_timeout=5,
            application_name=f"{DB_APPLICATION_NAME}_pool_probe",
            options="-c timezone=UTC",
        )
        max_connections = _show_pg_int(probe, "max_connections", 0)
        superuser_reserved = _show_pg_int(probe, "superuser_reserved_connections", 0)
        reserved = _show_pg_int(probe, "reserved_connections", 0)
        if max_connections <= 0:
            return None
        return {
            "max_connections": max_connections,
            "superuser_reserved_connections": superuser_reserved,
            "reserved_connections": reserved,
        }
    except Exception as exc:
        logger.warning("Could not probe PostgreSQL max_connections; using configured DB pool limits: %s", exc)
        return None
    finally:
        if probe is not None:
            try:
                probe.close()
            except Exception:
                pass


def _resolve_effective_pool_limits(params: Dict[str, Any]) -> tuple[int, int]:
    """Cap per-process pool size so app pools cannot exceed PostgreSQL capacity.

    psycopg2's pool max is per Python process. With Gunicorn, total possible
    DB connections is roughly GUNICORN_WORKERS * DB_POOL_MAX, plus pgAdmin,
    psql, migrations, and Postgres reserved slots. If DB_POOL_MAX is larger
    than server max_connections, PostgreSQL rejects new sockets with
    "sorry, too many clients already" before the application pool can queue.
    """
    configured_min = max(1, DB_POOL_MIN)
    explicit_max = DB_POOL_MAX_CONFIGURED
    default_auto_max = max(configured_min, DB_POOL_AUTO_DEFAULT_MAX)
    limits = _probe_pg_connection_limit(params)
    if not limits:
        configured_max = max(configured_min, explicit_max or default_auto_max)
        if explicit_max is None:
            logger.info(
                "DB_POOL_MAX=auto selected fallback max=%s because PostgreSQL limits could not be probed.",
                configured_max,
            )
        return configured_min, configured_max

    pg_max = int(limits.get("max_connections") or 0)
    pg_reserved = int(limits.get("superuser_reserved_connections") or 0)
    pg_reserved += int(limits.get("reserved_connections") or 0)
    workers = _env_int("GUNICORN_WORKERS", 1)
    usable_total = max(1, pg_max - pg_reserved - DB_POOL_RESERVE_FOR_OTHER_CLIENTS)
    per_process_cap = max(1, usable_total // max(1, workers))

    if explicit_max is None:
        configured_max = default_auto_max
        effective_max = min(configured_max, per_process_cap)
        logger.info(
            "DB_POOL_MAX=auto selected max=%s "
            "(postgres max_connections=%s, reserved=%s, reserve_for_other_clients=%s, "
            "gunicorn_workers=%s, auto_default_max=%s).",
            effective_max,
            pg_max,
            pg_reserved,
            DB_POOL_RESERVE_FOR_OTHER_CLIENTS,
            workers,
            default_auto_max,
        )
    else:
        configured_max = max(configured_min, explicit_max)
        effective_max = min(configured_max, per_process_cap)
    effective_min = min(configured_min, effective_max)
    if explicit_max is not None and effective_max < configured_max:
        logger.warning(
            "DB_POOL_MAX=%s exceeds safe PostgreSQL capacity; using effective max=%s "
            "(postgres max_connections=%s, reserved=%s, reserve_for_other_clients=%s, "
            "gunicorn_workers=%s). Use DB_POOL_MAX=auto, lower DB_POOL_MAX/DB_POOL_RESERVE_FOR_OTHER_CLIENTS, "
            "or raise PostgreSQL max_connections if needed.",
            configured_max,
            effective_max,
            pg_max,
            pg_reserved,
            DB_POOL_RESERVE_FOR_OTHER_CLIENTS,
            workers,
        )
    return effective_min, effective_max


def _pool_max_config_label() -> str:
    return str(DB_POOL_MAX_CONFIGURED) if DB_POOL_MAX_CONFIGURED is not None else "auto"


def _rollback_open_transaction(conn) -> bool:
    """Return a pooled connection to PostgreSQL's idle transaction state."""
    if conn is None or getattr(conn, "closed", 0):
        return False

    try:
        status_reader = getattr(conn, "get_transaction_status", None)
        if callable(status_reader):
            idle_status = psycopg2.extensions.TRANSACTION_STATUS_IDLE
            if status_reader() == idle_status:
                return True
        conn.rollback()
        return True
    except Exception:
        return False


def _is_connection_healthy(conn) -> bool:
    """Quick health check: make sure the connection is not closed and can
    actually round-trip a trivial query.  Used only when DB_POOL_HEALTH_CHECK
    is on, since SELECT 1 adds a small latency.
    """
    if conn is None:
        return False
    # psycopg2 sets .closed to nonzero when the connection is closed.
    if getattr(conn, "closed", 0):
        return False
    if not _rollback_open_transaction(conn):
        return False

    probe_ok = False
    cur = None
    try:
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.fetchone()
        probe_ok = True
    except Exception:
        probe_ok = False
    finally:
        if cur is not None:
            try:
                cur.close()
            except Exception:
                probe_ok = False

    # psycopg2 starts a transaction even for SELECT 1. Never let the health
    # probe itself put a pooled connection into idle-in-transaction state.
    return _rollback_open_transaction(conn) and probe_ok


def _acquire_conn_with_wait(pg_pool):
    """Wrapper around pg_pool.getconn() that waits up to
    DB_POOL_ACQUIRE_TIMEOUT seconds instead of failing immediately when the
    pool is exhausted (psycopg2's default behaviour).  Also performs a
    lightweight health check on the returned connection and discards dead
    connections back to the pool to let PG reopen fresh ones.
    """
    if not HAS_PSYCOPG2:
        raise RuntimeError("psycopg2 is not installed. Cannot use PostgreSQL.")

    started_at = time.monotonic()
    deadline = started_at + max(1, DB_POOL_ACQUIRE_TIMEOUT)
    backoff = 0.05  # start at 50ms
    last_err: Optional[Exception] = None
    warned = False
    while True:
        try:
            conn = pg_pool.getconn()
        except pool.PoolError as e:
            last_err = e
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                DB_POOL_ACQUIRE_FAILURES.labels(
                    application=DB_APPLICATION_NAME,
                    reason="pool_exhausted",
                ).inc()
                logger.error(
                    "PostgreSQL pool exhausted: all %s connections are in use and waiting %ss "
                    "did not free any. stats=%s. Consider lowering request concurrency or "
                    "investigating long-running DB sections.",
                    getattr(pg_pool, "maxconn", DB_POOL_MAX),
                    DB_POOL_ACQUIRE_TIMEOUT,
                    _pool_stats(pg_pool),
                )
                raise
            if not warned:
                logger.warning(
                    "PostgreSQL pool exhausted (%s in use); waiting up to %ss for a slot. stats=%s",
                    getattr(pg_pool, "maxconn", DB_POOL_MAX),
                    DB_POOL_ACQUIRE_TIMEOUT,
                    _pool_stats(pg_pool),
                )
                warned = True
            time.sleep(min(backoff, max(0.0, remaining)))
            backoff = min(backoff * 2, 0.5)
            continue
        except OperationalError as e:
            last_err = e
            if "too many clients already" not in str(e).lower():
                raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                DB_POOL_ACQUIRE_FAILURES.labels(
                    application=DB_APPLICATION_NAME,
                    reason="server_capacity",
                ).inc()
                logger.error(
                    "PostgreSQL server refused connections for %ss: too many clients already. "
                    "pool_stats=%s. Lower DB_POOL_MAX/request concurrency or raise PostgreSQL "
                    "max_connections.",
                    DB_POOL_ACQUIRE_TIMEOUT,
                    _pool_stats(pg_pool),
                )
                raise
            if not warned:
                logger.warning(
                    "PostgreSQL server is at max_connections; waiting up to %ss before failing. "
                    "pool_stats=%s",
                    DB_POOL_ACQUIRE_TIMEOUT,
                    _pool_stats(pg_pool),
                )
                warned = True
            time.sleep(min(backoff, max(0.0, remaining)))
            backoff = min(backoff * 2, 0.5)
            continue

        connection_ready = _rollback_open_transaction(conn)
        if connection_ready and DB_POOL_HEALTH_CHECK:
            connection_ready = _is_connection_healthy(conn)

        if not connection_ready:
            # Drop the dead connection and let the pool create a new one on
            # next attempt.  putconn(close=True) asks the pool to discard it.
            try:
                pg_pool.putconn(conn, close=True)
            except Exception:
                pass
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                DB_POOL_ACQUIRE_FAILURES.labels(
                    application=DB_APPLICATION_NAME,
                    reason="unhealthy_connection",
                ).inc()
                raise last_err or RuntimeError("DB pool returned only dead connections")
            time.sleep(min(backoff, max(0.0, remaining)))
            continue

        DB_POOL_ACQUIRE_SECONDS.labels(application=DB_APPLICATION_NAME).observe(
            max(0.0, time.monotonic() - started_at)
        )
        _observe_pool_stats(pg_pool)
        return conn


def _pool_stats(pg_pool) -> Dict[str, int]:
    try:
        idle = len(getattr(pg_pool, "_pool", []) or [])
    except Exception:
        idle = -1
    try:
        used = len(getattr(pg_pool, "_used", {}) or {})
    except Exception:
        used = -1
    opened = idle + used if idle >= 0 and used >= 0 else -1
    return {
        "min": int(getattr(pg_pool, "minconn", -1) or -1),
        "max": int(getattr(pg_pool, "maxconn", -1) or -1),
        "idle": idle,
        "used": used,
        "opened": opened,
    }


def _observe_pool_stats(pg_pool) -> None:
    stats = _pool_stats(pg_pool)
    application = DB_APPLICATION_NAME
    for state in ("used", "idle", "opened", "max"):
        value = stats.get(state, -1)
        if value >= 0:
            DB_POOL_CONNECTIONS.labels(application=application, state=state).set(value)
    used = stats.get("used", -1)
    capacity = stats.get("max", -1)
    if used >= 0 and capacity > 0:
        DB_POOL_UTILIZATION_RATIO.labels(application=application).set(used / capacity)


class PostgresCursor:
    """PostgreSQL cursor wrapper with placeholder conversion for backward compatibility"""
    
    def __init__(self, cursor):
        self._cursor = cursor
        self._last_insert_id = None
        self._rowcount = None
        # INSERT ... RETURNING: execute() peeks the first row for lastrowid; callers
        # that also cur.fetchone() must see the same row (not a second fetch from PG).
        self._buffered_row: Optional[Dict[str, Any]] = None
    
    def _convert_placeholders(self, query: str) -> str:
        """
        Convert ? placeholders to PostgreSQL %s for backward compatibility.
        Also handle some SQL syntax differences.
        """
        # Replace ? -> %s
        query = query.replace('?', '%s')
        
        # INSERT OR IGNORE -> PostgreSQL: INSERT ... ON CONFLICT DO NOTHING
        query = query.replace('INSERT OR IGNORE', 'INSERT')
        
        return query
    
    def execute(self, query: str, args: Any = None):
        """Execute SQL statement.

        For INSERT statements without an explicit RETURNING clause, we try to
        append ``RETURNING id`` so legacy callers can read ``cursor.lastrowid``.
        But not every table has an ``id`` column (e.g. ``qd_oauth_states``
        uses ``state`` as PK).  In that case psycopg2 raises
        ``UndefinedColumn`` and aborts the whole transaction, which can
        cascade into "column \"id\" does not exist" errors across the app.

        To stay safe, wrap the RETURNING-id variant in a SAVEPOINT.  If it
        fails with UndefinedColumn, roll back to the savepoint and retry the
        plain INSERT without RETURNING.  The outer transaction is preserved.
        """
        query = self._convert_placeholders(query)
        if args is not None and not isinstance(args, (tuple, list)):
            args = (args,)

        self._buffered_row = None
        self._last_insert_id = None
        self._rowcount = None

        is_insert = query.strip().upper().startswith('INSERT')
        has_returning = 'RETURNING' in query.upper()

        if is_insert and not has_returning:
            q_with_id = query.rstrip(';').rstrip() + ' RETURNING id'
            savepoint = '_pg_ins_ret_id'
            try:
                # Constant savepoint name; avoid SQL formatting.
                self._cursor.execute("SAVEPOINT _pg_ins_ret_id")
            except Exception:
                savepoint = None

            try:
                if args:
                    result = self._cursor.execute(q_with_id, args)
                else:
                    result = self._cursor.execute(q_with_id)
                # RELEASE SAVEPOINT replaces the native rowcount with -1.
                self._rowcount = self._cursor.rowcount
                try:
                    row = self._cursor.fetchone()
                    if row and 'id' in row:
                        self._last_insert_id = row['id']
                except Exception:
                    pass
                if savepoint:
                    try:
                        self._cursor.execute("RELEASE SAVEPOINT _pg_ins_ret_id")
                    except Exception:
                        pass
                return result
            except Exception as e:
                # If the error is about missing id column, fall back.  Other
                # errors (unique violation, NOT NULL, FK, ...) must propagate.
                msg = str(e).lower()
                is_missing_id = (
                    'column "id" does not exist' in msg
                    or 'undefinedcolumn' in e.__class__.__name__.lower()
                    and '"id"' in msg
                )
                if not is_missing_id:
                    raise
                if savepoint:
                    try:
                        self._cursor.execute("ROLLBACK TO SAVEPOINT _pg_ins_ret_id")
                    except Exception:
                        pass
                # Retry without RETURNING id.  Leaves _last_insert_id as None.
                if args:
                    result = self._cursor.execute(query, args)
                else:
                    result = self._cursor.execute(query)
                self._rowcount = self._cursor.rowcount
                return result

        # Non-INSERT, or INSERT with caller-supplied RETURNING
        if args:
            result = self._cursor.execute(query, args)
        else:
            result = self._cursor.execute(query)
        self._rowcount = self._cursor.rowcount

        if is_insert and has_returning:
            try:
                row = self._cursor.fetchone()
                if row is not None:
                    self._buffered_row = row if isinstance(row, dict) else dict(row)
                    if "id" in self._buffered_row:
                        self._last_insert_id = self._buffered_row["id"]
            except Exception:
                self._buffered_row = None
                pass

        return result

    def executemany(self, query: str, args_list: Any):
        """Execute a statement in bulk without per-row RETURNING/savepoints."""
        query = self._convert_placeholders(query)
        self._buffered_row = None
        self._last_insert_id = None
        self._rowcount = None
        if HAS_PSYCOPG2:
            return execute_batch(self._cursor, query, args_list, page_size=1000)
        return self._cursor.executemany(query, args_list)
    
    def fetchone(self) -> Optional[Dict[str, Any]]:
        """Fetch single row"""
        if self._buffered_row is not None:
            row = self._buffered_row
            self._buffered_row = None
            return row
        row = self._cursor.fetchone()
        if row is None:
            return None
        # RealDictCursor already returns a dict, so return as-is
        return row if isinstance(row, dict) else dict(row) if row else None
    
    def fetchall(self) -> List[Dict[str, Any]]:
        """Fetch all rows"""
        rows = self._cursor.fetchall()
        if not rows:
            return []
        # RealDictCursor already returns dicts, so return as-is
        return [row if isinstance(row, dict) else dict(row) for row in rows]
    
    def close(self):
        """Close cursor"""
        self._cursor.close()
    
    @property
    def lastrowid(self) -> Optional[int]:
        """Get last inserted row ID"""
        return self._last_insert_id
    
    @property
    def rowcount(self) -> int:
        """Get affected row count"""
        return self._cursor.rowcount if self._rowcount is None else self._rowcount


class PostgresConnection:
    """PostgreSQL connection wrapper"""
    
    def __init__(self, conn):
        self._conn = conn
        self._pool = _get_connection_pool()
    
    def cursor(self) -> PostgresCursor:
        """Create cursor"""
        return PostgresCursor(self._conn.cursor(cursor_factory=RealDictCursor))
    
    def commit(self):
        """Commit transaction"""
        self._conn.commit()
    
    def rollback(self):
        """Rollback transaction"""
        self._conn.rollback()
    
    def close(self):
        """Return connection to pool.  Broken connections are discarded so
        we don't poison the pool with closed sockets.
        """
        conn = self._conn
        self._conn = None
        if self._pool and conn:
            try:
                broken = bool(getattr(conn, "closed", 0))
                if not broken and not _rollback_open_transaction(conn):
                    broken = True
                self._pool.putconn(conn, close=broken)
                _observe_pool_stats(self._pool)
            except Exception as e:
                logger.warning(f"Failed to return connection to pool: {e}")


class _TransactionConnection:
    """Keep legacy helper commits inside the owning transaction."""

    def __init__(self, connection):
        self.connection = connection
        self.rollback_only = False
        self.after_commit = []

    def cursor(self):
        return self.connection.cursor()

    def commit(self):
        pass

    def rollback(self):
        self.rollback_only = True

    def close(self):
        pass


_active_transaction = ContextVar("postgres_active_transaction", default=None)


def run_after_commit(callback, *args, **kwargs):
    """Defer external effects until local accounting is durably committed."""
    active = _active_transaction.get()
    if active is None:
        return callback(*args, **kwargs)
    active.after_commit.append((callback, args, kwargs))
    return True


@contextmanager
def get_pg_transaction():
    """Atomically commit nested synchronous database helpers, or roll back all."""
    active = _active_transaction.get()
    if active is not None:
        try:
            yield active
        except BaseException:
            active.rollback_only = True
            raise
        return
    with get_pg_connection() as connection:
        transaction = _TransactionConnection(connection)
        token = _active_transaction.set(transaction)
        try:
            yield transaction
            if transaction.rollback_only:
                raise RuntimeError("Database transaction marked for rollback")
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            _active_transaction.reset(token)
    for callback, args, kwargs in transaction.after_commit:
        try:
            callback(*args, **kwargs)
        except Exception:
            logger.exception("Post-commit action failed; periodic order reconciliation will retry")


@contextmanager
def get_pg_connection():
    """
    Get PostgreSQL database connection (Context Manager).

    Uses _acquire_conn_with_wait so a momentary pool exhaustion does not
    immediately fail the request; we wait up to DB_POOL_ACQUIRE_TIMEOUT
    seconds for a connection to be released.
    """
    active = _active_transaction.get()
    if active is not None:
        try:
            yield active
        except BaseException:
            active.rollback_only = True
            raise
        return
    pg_pool = _get_connection_pool()
    conn = None
    broken = False
    try:
        conn = _acquire_conn_with_wait(pg_pool)
        pg_conn = PostgresConnection(conn)
        yield pg_conn
    except Exception as e:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
            # If the connection itself died mid-request, discard it instead
            # of returning it to the pool.
            if isinstance(e, (OperationalError, InterfaceError)) or getattr(conn, "closed", 0):
                broken = True
        error_msg = str(e) if e else repr(e)
        error_type = type(e).__name__
        logger.error(f"PostgreSQL operation error ({error_type}): {error_msg}", exc_info=True)
        raise
    finally:
        if conn is not None:
            try:
                if not broken and not _rollback_open_transaction(conn):
                    broken = True
                pg_pool.putconn(conn, close=broken)
                _observe_pool_stats(pg_pool)
            except Exception:
                pass


def get_pg_connection_sync() -> PostgresConnection:
    """
    Get connection synchronously (caller must close).

    NOTE: this function leaks its connection if the caller forgets to call
    `.close()`.  Prefer `get_pg_connection()` (context manager) whenever
    possible.
    """
    pg_pool = _get_connection_pool()
    conn = _acquire_conn_with_wait(pg_pool)
    return PostgresConnection(conn)


def execute_sql(sql: str, params: tuple = None) -> List[Dict[str, Any]]:
    """
    Execute SQL and return results (convenience function)
    """
    with get_pg_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        if sql.strip().upper().startswith('SELECT'):
            return cursor.fetchall()
        conn.commit()
        return []


def is_postgres_available() -> bool:
    """Check if PostgreSQL is available"""
    if not HAS_PSYCOPG2:
        return False
    
    db_url = _get_database_url()
    if not db_url:
        return False
    
    try:
        with get_pg_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            return True
    except Exception as e:
        logger.debug(f"PostgreSQL not available: {e}")
        return False


def close_pool():
    """Close connection pool (call on app shutdown)"""
    global _connection_pool
    if _connection_pool:
        try:
            _connection_pool.closeall()
            _connection_pool = None
            logger.info("PostgreSQL connection pool closed")
        except Exception as e:
            logger.warning(f"Error closing connection pool: {e}")
