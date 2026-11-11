from app.services.strategy_runtime import identity


class _Cursor:
    def __init__(self, existing):
        self.existing = dict(existing)
        self.statements = []
        self.lastrowid = 8

    def execute(self, sql, params=None):
        self.statements.append((sql, params))

    def fetchone(self):
        return dict(self.existing)

    def close(self):
        pass


class _Connection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.committed = True


def test_handoff_reuses_active_run_for_same_runtime_epoch(monkeypatch):
    active = identity.StrategyRunSnapshot(
        strategy_run_id=7,
        strategy_id=20,
        user_id=3,
        code_hash="hash",
        runtime_epoch=4,
        runtime_status="running",
    )
    monkeypatch.setattr(identity, "_active_run_for_strategy", lambda *_args: active)
    monkeypatch.setattr(
        identity,
        "get_db_connection",
        lambda: (_ for _ in ()).throw(AssertionError("database should not be used")),
    )

    result = identity.ensure_strategy_run(strategy_id=20, runtime_epoch=4)

    assert result is active


def test_new_runtime_epoch_supersedes_old_active_run_before_insert(monkeypatch):
    cursor = _Cursor(
        {
            "id": 7,
            "strategy_id": 20,
            "user_id": 3,
            "code_hash": "old-hash",
            "runtime_epoch": 4,
            "runtime_status": "running",
        }
    )
    connection = _Connection(cursor)
    monkeypatch.setattr(identity, "_active_run_for_strategy", lambda *_args: None)
    monkeypatch.setattr(identity, "get_db_connection", lambda: connection)
    monkeypatch.setattr(identity, "append_runtime_event", lambda **_kwargs: None)

    result = identity.ensure_strategy_run(
        strategy_id=20,
        user_id=3,
        code="new code",
        runtime_epoch=5,
    )

    assert connection.committed is True
    assert result.strategy_run_id == 8
    assert result.runtime_epoch == 5
    statements = [sql for sql, _params in cursor.statements]
    assert any("superseded_by_runtime_epoch" in sql for sql in statements)
    insert_index = next(
        index for index, sql in enumerate(statements) if "INSERT INTO strategy_runs" in sql
    )
    assert cursor.statements[insert_index][1][-1] == 5
