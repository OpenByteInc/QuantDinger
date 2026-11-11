from app.services import strategy as strategy_service_module
from app.services.strategy import StrategyService


class _Cursor:
    def __init__(self, *, update_rowcount=0, selected_status=None, selected_row=None):
        self.rowcount = update_rowcount
        self.selected_status = selected_status
        self.selected_row = selected_row
        self.statements = []

    def execute(self, sql, params=None):
        self.statements.append((sql, params))

    def fetchone(self):
        if self.selected_row is not None:
            return dict(self.selected_row)
        if self.selected_status is None:
            return None
        return {"status": self.selected_status}

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


def test_update_strategy_status_returns_true_when_update_affects_row(monkeypatch):
    cursor = _Cursor(update_rowcount=1)
    conn = _Connection(cursor)
    monkeypatch.setattr(strategy_service_module, "get_db_connection", lambda: conn)

    assert StrategyService().update_strategy_status(42, "stopped", user_id=7) is True
    assert conn.committed is True
    assert len(cursor.statements) == 1


def test_update_strategy_status_accepts_already_stopped_row(monkeypatch):
    cursor = _Cursor(update_rowcount=0, selected_status="stopped")
    conn = _Connection(cursor)
    monkeypatch.setattr(strategy_service_module, "get_db_connection", lambda: conn)

    assert StrategyService().update_strategy_status(42, "stopped", user_id=7) is True
    assert len(cursor.statements) == 2


def test_update_strategy_status_returns_false_when_row_not_found(monkeypatch):
    cursor = _Cursor(update_rowcount=0, selected_status=None)
    conn = _Connection(cursor)
    monkeypatch.setattr(strategy_service_module, "get_db_connection", lambda: conn)

    assert StrategyService().update_strategy_status(42, "stopped", user_id=7) is False


def test_start_advances_desired_runtime_epoch_only_on_stopped_to_running(monkeypatch):
    cursor = _Cursor(
        update_rowcount=1,
        selected_row={
            "id": 42,
            "user_id": 7,
            "status": "stopped",
            "desired_runtime_epoch": 3,
        },
    )
    conn = _Connection(cursor)
    monkeypatch.setattr(strategy_service_module, "get_db_connection", lambda: conn)

    assert StrategyService().update_strategy_status(42, "running", user_id=7) is True

    update_sql = cursor.statements[-1][0]
    assert "desired_runtime_epoch" in update_sql
    assert "runtime_requested_at=NOW()" in update_sql


def test_start_does_not_advance_epoch_when_strategy_is_already_running(monkeypatch):
    cursor = _Cursor(
        update_rowcount=1,
        selected_row={
            "id": 42,
            "user_id": 7,
            "status": "running",
            "desired_runtime_epoch": 3,
        },
    )
    conn = _Connection(cursor)
    monkeypatch.setattr(strategy_service_module, "get_db_connection", lambda: conn)

    assert StrategyService().update_strategy_status(42, "running", user_id=7) is True

    update_sql = cursor.statements[-1][0]
    assert "desired_runtime_epoch" not in update_sql
    assert "runtime_requested_at" not in update_sql


def test_explicit_restart_advances_runtime_epoch_without_stopping_desired_state(monkeypatch):
    cursor = _Cursor(update_rowcount=1, selected_row={"status": "running"})
    conn = _Connection(cursor)
    monkeypatch.setattr(strategy_service_module, "get_db_connection", lambda: conn)

    assert StrategyService().request_strategy_runtime_restart(42, user_id=7) is True

    update_sql = cursor.statements[-1][0]
    assert "desired_runtime_epoch" in update_sql
    assert "runtime_requested_at=NOW()" in update_sql


def test_batch_stop_reports_status_persistence_failures(monkeypatch):
    calls = []

    def fake_update(self, strategy_id, status, user_id=None):
        calls.append((strategy_id, status, user_id))
        return strategy_id == 1

    monkeypatch.setattr(StrategyService, "update_strategy_status", fake_update)

    result = StrategyService().batch_stop_strategies([1, 2], user_id=7)

    assert result["success"] is True
    assert result["success_ids"] == [1]
    assert result["failed_ids"] == [{"id": 2, "error": "status update affected 0 rows"}]
    assert calls == [(1, "stopped", 7), (2, "stopped", 7)]
