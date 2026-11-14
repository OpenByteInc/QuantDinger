from app.utils import db_postgres


class _ProbeCursor:
    def __init__(self, values):
        self.values = values
        self.last_setting = None

    def execute(self, sql):
        self.last_setting = sql.split()[-1]
        if self.last_setting not in self.values:
            raise RuntimeError("unknown setting")

    def fetchone(self):
        return (str(self.values[self.last_setting]),)

    def close(self):
        pass


class _ProbeConn:
    def __init__(self, values):
        self.values = values
        self.closed = False
        self.rollbacks = 0

    def cursor(self):
        return _ProbeCursor(self.values)

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


class _FakePool:
    minconn = 5
    maxconn = 77

    def __init__(self):
        self._pool = [object(), object()]
        self._used = {"a": object(), "b": object(), "c": object()}


class _MetricRecorder:
    def __init__(self):
        self.values = {}
        self.current_labels = {}

    def labels(self, **labels):
        self.current_labels = labels
        return self

    def set(self, value):
        key = tuple(sorted(self.current_labels.items()))
        self.values[key] = value


def test_effective_pool_limits_cap_config_above_postgres_capacity(monkeypatch):
    probe = _ProbeConn({
        "max_connections": 100,
        "superuser_reserved_connections": 3,
    })
    monkeypatch.setattr(db_postgres, "DB_POOL_AUTO_CAP", True)
    monkeypatch.setattr(db_postgres, "DB_POOL_MIN", 5)
    monkeypatch.setattr(db_postgres, "DB_POOL_MAX_CONFIGURED", 200)
    monkeypatch.setattr(db_postgres, "DB_POOL_MAX", 200)
    monkeypatch.setattr(db_postgres, "DB_POOL_AUTO_DEFAULT_MAX", 50)
    monkeypatch.setattr(db_postgres, "DB_POOL_RESERVE_FOR_OTHER_CLIENTS", 20)
    monkeypatch.setenv("GUNICORN_WORKERS", "1")
    monkeypatch.setattr(db_postgres.psycopg2, "connect", lambda **kwargs: probe)

    assert db_postgres._resolve_effective_pool_limits({}) == (5, 77)
    assert probe.closed is True


def test_effective_pool_limits_divide_capacity_by_gunicorn_workers(monkeypatch):
    probe = _ProbeConn({
        "max_connections": 100,
        "superuser_reserved_connections": 3,
    })
    monkeypatch.setattr(db_postgres, "DB_POOL_AUTO_CAP", True)
    monkeypatch.setattr(db_postgres, "DB_POOL_MIN", 5)
    monkeypatch.setattr(db_postgres, "DB_POOL_MAX_CONFIGURED", 200)
    monkeypatch.setattr(db_postgres, "DB_POOL_MAX", 200)
    monkeypatch.setattr(db_postgres, "DB_POOL_AUTO_DEFAULT_MAX", 50)
    monkeypatch.setattr(db_postgres, "DB_POOL_RESERVE_FOR_OTHER_CLIENTS", 20)
    monkeypatch.setenv("GUNICORN_WORKERS", "2")
    monkeypatch.setattr(db_postgres.psycopg2, "connect", lambda **kwargs: probe)

    assert db_postgres._resolve_effective_pool_limits({}) == (5, 38)


def test_effective_pool_limits_keep_safe_config(monkeypatch):
    probe = _ProbeConn({
        "max_connections": 100,
        "superuser_reserved_connections": 3,
    })
    monkeypatch.setattr(db_postgres, "DB_POOL_AUTO_CAP", True)
    monkeypatch.setattr(db_postgres, "DB_POOL_MIN", 5)
    monkeypatch.setattr(db_postgres, "DB_POOL_MAX_CONFIGURED", 30)
    monkeypatch.setattr(db_postgres, "DB_POOL_MAX", 30)
    monkeypatch.setattr(db_postgres, "DB_POOL_AUTO_DEFAULT_MAX", 50)
    monkeypatch.setattr(db_postgres, "DB_POOL_RESERVE_FOR_OTHER_CLIENTS", 20)
    monkeypatch.setenv("GUNICORN_WORKERS", "1")
    monkeypatch.setattr(db_postgres.psycopg2, "connect", lambda **kwargs: probe)

    assert db_postgres._resolve_effective_pool_limits({}) == (5, 30)


def test_effective_pool_limits_auto_uses_default_when_capacity_allows(monkeypatch):
    probe = _ProbeConn({
        "max_connections": 100,
        "superuser_reserved_connections": 3,
    })
    monkeypatch.setattr(db_postgres, "DB_POOL_AUTO_CAP", True)
    monkeypatch.setattr(db_postgres, "DB_POOL_MIN", 5)
    monkeypatch.setattr(db_postgres, "DB_POOL_MAX_CONFIGURED", None)
    monkeypatch.setattr(db_postgres, "DB_POOL_AUTO_DEFAULT_MAX", 50)
    monkeypatch.setattr(db_postgres, "DB_POOL_RESERVE_FOR_OTHER_CLIENTS", 20)
    monkeypatch.setenv("GUNICORN_WORKERS", "1")
    monkeypatch.setattr(db_postgres.psycopg2, "connect", lambda **kwargs: probe)

    assert db_postgres._resolve_effective_pool_limits({}) == (5, 50)


def test_effective_pool_limits_auto_caps_small_postgres(monkeypatch):
    probe = _ProbeConn({
        "max_connections": 30,
        "superuser_reserved_connections": 3,
    })
    monkeypatch.setattr(db_postgres, "DB_POOL_AUTO_CAP", True)
    monkeypatch.setattr(db_postgres, "DB_POOL_MIN", 5)
    monkeypatch.setattr(db_postgres, "DB_POOL_MAX_CONFIGURED", None)
    monkeypatch.setattr(db_postgres, "DB_POOL_AUTO_DEFAULT_MAX", 50)
    monkeypatch.setattr(db_postgres, "DB_POOL_RESERVE_FOR_OTHER_CLIENTS", 10)
    monkeypatch.setenv("GUNICORN_WORKERS", "1")
    monkeypatch.setattr(db_postgres.psycopg2, "connect", lambda **kwargs: probe)

    assert db_postgres._resolve_effective_pool_limits({}) == (5, 17)


def test_env_optional_int_treats_unset_and_auto_as_auto(monkeypatch):
    monkeypatch.delenv("DB_POOL_MAX", raising=False)
    assert db_postgres._env_optional_int("DB_POOL_MAX") is None
    monkeypatch.setenv("DB_POOL_MAX", "auto")
    assert db_postgres._env_optional_int("DB_POOL_MAX") is None
    monkeypatch.setenv("DB_POOL_MAX", "42")
    assert db_postgres._env_optional_int("DB_POOL_MAX") == 42


def test_pool_stats_reports_private_psycopg_pool_counts():
    assert db_postgres._pool_stats(_FakePool()) == {
        "min": 5,
        "max": 77,
        "idle": 2,
        "used": 3,
        "opened": 5,
    }


def test_pool_metrics_publish_counts_and_utilization(monkeypatch):
    connections = _MetricRecorder()
    utilization = _MetricRecorder()
    monkeypatch.setattr(db_postgres, "DB_POOL_CONNECTIONS", connections)
    monkeypatch.setattr(db_postgres, "DB_POOL_UTILIZATION_RATIO", utilization)
    monkeypatch.setattr(db_postgres, "DB_APPLICATION_NAME", "test-api")

    db_postgres._observe_pool_stats(_FakePool())

    assert connections.values[(('application', 'test-api'), ('state', 'used'))] == 3
    assert connections.values[(('application', 'test-api'), ('state', 'idle'))] == 2
    assert connections.values[(('application', 'test-api'), ('state', 'opened'))] == 5
    assert connections.values[(('application', 'test-api'), ('state', 'max'))] == 77
    assert utilization.values[(('application', 'test-api'),)] == 3 / 77


def test_pool_connections_enforce_transaction_and_lock_timeouts(monkeypatch):
    captured = {}
    fake_pool = _FakePool()

    def create_pool(**kwargs):
        captured.update(kwargs)
        return fake_pool

    monkeypatch.setattr(db_postgres, "_connection_pool", None)
    monkeypatch.setattr(db_postgres, "_resolve_effective_pool_limits", lambda _params: (2, 8))
    monkeypatch.setattr(db_postgres.pool, "ThreadedConnectionPool", create_pool)
    monkeypatch.setattr(db_postgres, "_observe_pool_stats", lambda _pool: None)
    monkeypatch.setattr(db_postgres, "DB_IDLE_IN_TRANSACTION_TIMEOUT_MS", 60_000)
    monkeypatch.setattr(db_postgres, "DB_LOCK_TIMEOUT_MS", 10_000)
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:password@localhost:5432/quantdinger")

    assert db_postgres._get_connection_pool() is fake_pool
    assert captured["options"] == (
        "-c timezone=UTC "
        "-c idle_in_transaction_session_timeout=60000 "
        "-c lock_timeout=10000"
    )
