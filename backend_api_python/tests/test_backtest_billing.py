from contextlib import contextmanager
from decimal import Decimal

from app.routes import backtest_center
from app.services import billing_service
from app.services.billing_config import load_billing_config


def test_backtest_cost_defaults_to_30_and_can_be_overridden(monkeypatch):
    monkeypatch.delenv("BILLING_COST_BACKTEST", raising=False)
    assert load_billing_config()["cost_backtest"] == 30

    monkeypatch.setenv("BILLING_COST_BACKTEST", "45")
    assert load_billing_config()["cost_backtest"] == 45


def test_backtest_submission_delegates_billing_to_job_boundary(monkeypatch):
    calls = []

    def submit_job(**kwargs):
        calls.append(kwargs)
        return {"job_id": "job-1", "status": "queued"}

    monkeypatch.setattr(backtest_center.agent_jobs, "submit_job", submit_job)
    receipt = backtest_center._submit_backtest_job({
        "code": "def initialize(context):\n    pass",
        "startDate": "2026-01-01",
        "endDate": "2026-01-31",
    }, 7)

    assert receipt == {"job_id": "job-1", "status": "queued"}
    assert calls[0]["user_id"] == 7
    assert calls[0]["kind"] == "backtest"
    assert calls[0]["request_payload"]["__user_id"] == 7


def test_credit_deduction_is_atomic(monkeypatch):
    statements = []

    class FakeCursor:
        def __init__(self):
            self._next_row = None

        def execute(self, sql, params=()):
            statements.append((" ".join(sql.split()), params))
            if "UPDATE qd_users" in sql:
                self._next_row = {"credits": Decimal("70")}

        def fetchone(self):
            row = self._next_row
            self._next_row = None
            return row

        def close(self):
            pass

    class FakeDb:
        def __init__(self):
            self.committed = False

        def cursor(self):
            return FakeCursor()

        def commit(self):
            self.committed = True

        def rollback(self):
            raise AssertionError("successful deduction must not roll back")

    db = FakeDb()

    @contextmanager
    def fake_connection():
        yield db

    monkeypatch.setattr(billing_service, "get_db_connection", fake_connection)
    service = billing_service.BillingService()
    monkeypatch.setattr(service, "is_billing_enabled", lambda: True)
    monkeypatch.setattr(service, "get_feature_cost", lambda feature: 30)

    success, message = service.check_and_consume(5, "backtest", "backtest:test")

    assert (success, message) == (True, "consumed")
    assert db.committed is True
    update_sql, update_params = statements[0]
    assert "credits = credits - ?" in update_sql
    assert "credits >= ?" in update_sql
    assert "RETURNING credits" in update_sql
    assert update_params == (30, 5, 30)
    assert any("INSERT INTO qd_credits_log" in sql for sql, _params in statements)
