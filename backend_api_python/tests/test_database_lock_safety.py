from contextlib import contextmanager
from pathlib import Path

from app.services import user_service as user_service_module


def test_admin_bootstrap_releases_count_transaction_before_nested_helpers(monkeypatch):
    connection_open = False

    class Cursor:
        @staticmethod
        def execute(_query):
            return None

        @staticmethod
        def fetchone():
            return {"count": 1}

        @staticmethod
        def close():
            return None

    class Connection:
        @staticmethod
        def cursor():
            return Cursor()

    @contextmanager
    def connection():
        nonlocal connection_open
        connection_open = True
        try:
            yield Connection()
        finally:
            connection_open = False

    service = user_service_module.UserService()
    monkeypatch.setattr(user_service_module, "get_db_connection", connection)
    monkeypatch.setattr(service, "ensure_password_changed_column", lambda: None)

    calls = []

    def sync_credentials():
        assert connection_open is False
        calls.append("credentials")

    def sync_email(*, overwrite_existing):
        assert connection_open is False
        assert overwrite_existing is False
        calls.append("email")

    monkeypatch.setattr(service, "sync_bootstrap_admin_credentials_from_env", sync_credentials)
    monkeypatch.setattr(service, "sync_admin_email_from_config", sync_email)

    service.ensure_admin_exists()

    assert calls == ["credentials", "email"]


def test_central_user_table_has_no_runtime_ddl():
    app_root = Path(__file__).resolve().parents[1] / "app"
    offenders = []
    for path in app_root.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        if "ALTER TABLE qd_users" in source:
            offenders.append(str(path.relative_to(app_root)))

    assert offenders == []


def test_billing_schema_is_migration_owned():
    service_path = Path(__file__).resolve().parents[1] / "app" / "services" / "billing_service.py"
    source = service_path.read_text(encoding="utf-8")

    assert "ALTER TABLE" not in source
    assert "CREATE TABLE" not in source
