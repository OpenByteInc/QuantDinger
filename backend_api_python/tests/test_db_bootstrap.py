"""Bootstrap-time DB behaviour: auto-migrate + permission probe.

These verify the two safety nets in ``app.utils.db.init_database``:

1. ``_apply_init_sql`` is idempotent and tolerant — a missing schema file or
   a failing migration must NOT crash startup, only emit a warning. Crashing
   here would break upgrades in the field where the file might be stripped
   or the DB user temporarily lacks DDL rights.

2. ``_verify_table_access`` collects every permission failure (instead of
   aborting on the first one) so the operator sees the full list in a single
   banner, then continues startup. The previous behaviour was to start
   "successfully" and let every 10s worker tick spam ``InsufficientPrivilege``,
   making the root cause hard to spot.
"""

import logging
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from app.utils import db as db_module


class _FakeCursor:
    """Cursor that fails for a configurable set of table names."""

    def __init__(self, deny_tables=()):
        self._deny = set(deny_tables)

    def execute(self, sql, *args, **kwargs):
        for name in self._deny:
            if name in sql:
                raise RuntimeError(f"permission denied for table {name}")
        return None

    def fetchone(self):
        return None

    def close(self):
        pass


class _FakeConn:
    def __init__(self, deny_tables=()):
        self._deny_tables = deny_tables
        self.committed = False
        self.rolled_back = 0

    def cursor(self):
        return _FakeCursor(self._deny_tables)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back += 1


class _FakeConnCtx:
    """Stand-in for ``get_db_connection()`` context manager."""

    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        return self._conn

    def __exit__(self, *exc):
        return False


def test_apply_init_sql_skips_quietly_when_file_missing(tmp_path, caplog, monkeypatch):
    """Missing migrations file → warning + early return, never raise."""
    missing = tmp_path / 'does_not_exist.sql'
    monkeypatch.setattr(db_module, '_resolve_init_sql_path', lambda: missing)

    with caplog.at_level(logging.WARNING):
        db_module._apply_init_sql(logging.getLogger('test'))

    assert any('not found' in rec.message for rec in caplog.records)


def test_apply_init_sql_warns_but_does_not_raise_on_db_failure(tmp_path, caplog, monkeypatch):
    """A failing migration is downgraded to a warning — startup must continue."""
    fake_sql = tmp_path / 'init.sql'
    fake_sql.write_text("SELECT 1;", encoding='utf-8')
    monkeypatch.setattr(db_module, '_resolve_init_sql_path', lambda: fake_sql)

    # Have the cursor raise — simulates InsufficientPrivilege etc.
    bad_conn = _FakeConn()
    bad_conn.cursor = lambda: (_ for _ in ()).throw(RuntimeError("permission denied"))

    with patch.object(db_module, 'get_db_connection', return_value=_FakeConnCtx(bad_conn)):
        with caplog.at_level(logging.WARNING):
            # Must not raise — bootstrap continuation is the contract.
            db_module._apply_init_sql(logging.getLogger('test'))

    assert any('Auto-migrate failed' in rec.message for rec in caplog.records)


def test_apply_init_sql_commits_on_success(tmp_path, monkeypatch):
    """Happy path: file exists, cursor accepts every statement, conn.commit() runs.

    Without an explicit commit the schema apply would silently roll back when
    the context manager exits — the previous lack of any commit call here was
    exactly the kind of bug we want this test to catch on refactor.
    """
    fake_sql = tmp_path / 'init.sql'
    fake_sql.write_text("CREATE TABLE IF NOT EXISTS foo (id INT);", encoding='utf-8')
    monkeypatch.setattr(db_module, '_resolve_init_sql_path', lambda: fake_sql)

    conn = _FakeConn(deny_tables=())
    with patch.object(db_module, 'get_db_connection', return_value=_FakeConnCtx(conn)):
        db_module._apply_init_sql(logging.getLogger('test'))

    assert conn.committed, "_apply_init_sql must commit on success"


def test_apply_init_sql_includes_public_universe_expansion(tmp_path, monkeypatch):
    fake_sql = tmp_path / 'init.sql'
    fake_sql.write_text("SELECT 1;", encoding='utf-8')
    monkeypatch.setattr(db_module, '_resolve_init_sql_path', lambda: fake_sql)
    applied = []

    def fake_apply_component(_conn, _logger, **kwargs):
        applied.append(kwargs['name'])
        return 'applied'

    monkeypatch.setattr(db_module, '_apply_migration_component', fake_apply_component)
    conn = _FakeConn(deny_tables=())
    with patch.object(db_module, 'get_db_connection', return_value=_FakeConnCtx(conn)):
        db_module._apply_init_sql(logging.getLogger('test'))

    assert 'public-universe-expansion-20261024' in applied
    assert 'default-cn-hk-universe-members-20261025' in applied
    assert 'default-global-universe-members-20261025' in applied


def test_public_universe_expansion_has_explicit_returning_clause():
    migration_path = (
        Path(__file__).resolve().parents[1]
        / 'migrations'
        / '20261024_public_universe_expansion.sql'
    )

    assert 'RETURNING code' in migration_path.read_text(encoding='utf-8')


def test_global_universe_seed_only_populates_empty_system_pools():
    migration_path = (
        Path(__file__).resolve().parents[1]
        / 'migrations'
        / '20261025_default_global_universe_members.sql'
    )
    migration = migration_path.read_text(encoding='utf-8')

    assert 'NOT EXISTS (' in migration
    assert 'DELETE FROM qd_universe_members' not in migration


class _MigrationCursor:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.calls = []
        self.closed = False

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None

    def close(self):
        self.closed = True


class _MigrationConn:
    def __init__(self, rows=()):
        self.cursor_obj = _MigrationCursor(rows)
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def test_migration_component_skips_unchanged_checksum(tmp_path):
    sql_path = tmp_path / 'seed.sql'
    sql_path.write_text('SELECT 42;', encoding='utf-8')
    checksum = db_module._migration_checksum(sql_path)
    conn = _MigrationConn(rows=[{'checksum': checksum}])

    result = db_module._apply_migration_component(
        conn,
        logging.getLogger('test'),
        name='large-seed',
        path=sql_path,
    )

    statements = [sql for sql, _params in conn.cursor_obj.calls]
    assert result == 'skipped'
    assert 'SELECT 42;' not in statements
    assert conn.commits == 0
    assert conn.cursor_obj.closed


def test_migration_component_baselines_existing_large_catalogue(tmp_path):
    sql_path = tmp_path / 'market-seed.sql'
    sql_path.write_text('SELECT dangerous_large_seed();', encoding='utf-8')
    conn = _MigrationConn(rows=[None, {'row_count': 25000}])

    result = db_module._apply_migration_component(
        conn,
        logging.getLogger('test'),
        name='market-symbols-master',
        path=sql_path,
        baseline_table='qd_market_symbols',
        baseline_min_rows=1000,
    )

    statements = [sql for sql, _params in conn.cursor_obj.calls]
    assert result == 'baselined'
    assert 'SELECT dangerous_large_seed();' not in statements
    assert any('INSERT INTO qd_bootstrap_migrations' in sql for sql in statements)
    assert conn.commits == 1


def test_migration_component_sets_bounded_database_timeouts(tmp_path, monkeypatch):
    sql_path = tmp_path / 'schema.sql'
    sql_path.write_text('SELECT 42;', encoding='utf-8')
    conn = _MigrationConn(rows=[None])
    monkeypatch.setenv('MIGRATION_LOCK_TIMEOUT_SECONDS', '7')
    monkeypatch.setenv('MIGRATION_STATEMENT_TIMEOUT_SECONDS', '90')

    result = db_module._apply_migration_component(
        conn,
        logging.getLogger('test'),
        name='schema-init',
        path=sql_path,
    )

    statements = [sql for sql, _params in conn.cursor_obj.calls]
    assert result == 'applied'
    assert "SET LOCAL lock_timeout = '7s'" in statements
    assert "SET LOCAL statement_timeout = '90s'" in statements
    assert 'SELECT 42;' in statements
    assert conn.commits == 1


def test_verify_table_access_logs_ok_when_all_tables_readable(caplog):
    conn = _FakeConn(deny_tables=())
    with patch.object(db_module, 'get_db_connection', return_value=_FakeConnCtx(conn)):
        with caplog.at_level(logging.INFO):
            db_module._verify_table_access(logging.getLogger('test'))

    assert any('permission probe OK' in rec.message for rec in caplog.records)
    # No banner / error level entries when everything's fine.
    assert not any(rec.levelno >= logging.ERROR for rec in caplog.records)


def test_verify_table_access_collects_all_failures_and_emits_banner(caplog):
    """All failing tables must appear in a single banner, in the order we probe.

    Previous behaviour: the first ``InsufficientPrivilege`` aborted the loop
    because Postgres left the transaction in a failed state. We now rollback
    after each probe so subsequent tables still get checked.
    """
    denied = {'pending_orders', 'qd_strategy_positions'}
    conn = _FakeConn(deny_tables=denied)

    with patch.object(db_module, 'get_db_connection', return_value=_FakeConnCtx(conn)):
        with caplog.at_level(logging.ERROR):
            db_module._verify_table_access(logging.getLogger('test'))

    error_text = '\n'.join(rec.message for rec in caplog.records if rec.levelno >= logging.ERROR)
    for table in denied:
        assert table in error_text, f"banner is missing {table}"
    assert 'PERMISSION CHECK FAILED' in error_text
    assert 'ALTER TABLE' in error_text  # the fix recipe is present
    # And we expect at least one rollback per failure so the tx state stays clean.
    assert conn.rolled_back >= len(denied)


def test_init_database_respects_skip_auto_migrate(monkeypatch, caplog):
    """SKIP_AUTO_MIGRATE=true must bypass _apply_init_sql but still probe perms.

    This is the escape hatch for ops who manage schema externally (e.g. via
    Flyway/Liquibase) and don't want the backend touching DDL on every boot.
    """
    monkeypatch.setenv('SKIP_AUTO_MIGRATE', 'true')

    calls = {'apply': 0, 'verify': 0}

    def fake_apply(_logger):
        calls['apply'] += 1

    def fake_verify(_logger):
        calls['verify'] += 1

    monkeypatch.setattr(db_module, '_apply_init_sql', fake_apply)
    monkeypatch.setattr(db_module, '_verify_table_access', fake_verify)
    monkeypatch.setattr(db_module, 'is_postgres_available', lambda: True)

    with caplog.at_level(logging.INFO):
        db_module.init_database()

    assert calls['apply'] == 0, "auto-migrate should be skipped"
    assert calls['verify'] == 1, "permission probe must still run"
    assert any('SKIP_AUTO_MIGRATE' in rec.message for rec in caplog.records)


def test_bootstrap_applies_execution_accounting_after_schema(monkeypatch):
    from contextlib import contextmanager
    from unittest.mock import MagicMock
    from app.utils import db as module

    connection = MagicMock()
    @contextmanager
    def connect():
        yield connection
    calls = []
    monkeypatch.setattr(module, "get_db_connection", connect)
    monkeypatch.setattr(module, "_apply_migration_component", lambda conn, logger, **kw: calls.append(kw))
    module._apply_init_sql(MagicMock(), strict=True)
    names = [item["name"] for item in calls]
    assert names[:2] == ["schema-init", "execution-fill-accounting-20260916"]
    assert names[2] == "exchange-order-pnl-20260916"
    assert names[3] == "ai-decision-billing-20260920"
    assert names[4] == "quick-trade-client-order-id-20260926"
    assert names[5] == "signal-virtual-account-20260926"
    assert "qd_exchange_order_pnl" in calls[2]["path"].read_text(encoding="utf-8")
    assert "billing_json" in calls[3]["path"].read_text(encoding="utf-8")
    assert "qd_quick_trades" in calls[4]["path"].read_text(encoding="utf-8")
    assert "client_order_id" in calls[4]["path"].read_text(encoding="utf-8")
    virtual_sql = calls[5]["path"].read_text(encoding="utf-8")
    assert "qd_strategy_virtual_trades" in virtual_sql
    assert "slippage_quote" in virtual_sql
    assert calls[1]["path"].is_file()
    assert "commission_breakdown" in calls[1]["path"].read_text(encoding="utf-8")
