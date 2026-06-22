"""Bootstrap admin password reminder policy."""

from app.services.user_service import UserService


def test_builtin_default_password_still_requires_change(monkeypatch):
    svc = UserService()
    password_hash = svc.hash_password(UserService.BOOTSTRAP_DEFAULT_PASSWORD)

    monkeypatch.setenv("ADMIN_PASSWORD", UserService.BOOTSTRAP_DEFAULT_PASSWORD)

    assert svc._initial_password_state(password_hash, None) == "must_change"


def test_env_admin_password_change_syncs_db_instead_of_prompting(monkeypatch):
    svc = UserService()
    password_hash = svc.hash_password(UserService.BOOTSTRAP_DEFAULT_PASSWORD)

    monkeypatch.setenv("ADMIN_PASSWORD", "not-the-default")

    assert svc._initial_password_state(password_hash, None) == "sync_env_password"


def test_non_default_db_password_is_marked_changed(monkeypatch):
    svc = UserService()
    password_hash = svc.hash_password("already-custom-password")

    monkeypatch.setenv("ADMIN_PASSWORD", UserService.BOOTSTRAP_DEFAULT_PASSWORD)

    assert svc._initial_password_state(password_hash, None) == "mark_changed"


def test_existing_password_changed_timestamp_disables_prompt(monkeypatch):
    svc = UserService()
    password_hash = svc.hash_password(UserService.BOOTSTRAP_DEFAULT_PASSWORD)

    monkeypatch.setenv("ADMIN_PASSWORD", UserService.BOOTSTRAP_DEFAULT_PASSWORD)

    assert svc._initial_password_state(password_hash, "2026-06-06 00:00:00") == "ok"
