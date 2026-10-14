"""Regression tests for JWT forgery and authorization bypasses."""

import datetime
from contextlib import contextmanager

import jwt
from flask import Flask, jsonify

from app.config.settings import Config
from app.services import user_service as user_service_module
from app.services.user_service import UserService
from app.utils import db as db_module
from app.utils import auth


def _encode(claims: dict) -> str:
    return jwt.encode(claims, Config.SECRET_KEY, algorithm="HS256")


def _claims(**overrides) -> dict:
    now = datetime.datetime.now(datetime.timezone.utc)
    claims = {
        "exp": now + datetime.timedelta(minutes=5),
        "iat": now,
        "sub": "attacker",
        "user_id": 1,
        "role": "admin",
        "token_version": 1,
    }
    claims.update(overrides)
    return claims


def test_secret_key_legacy_floor(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "1234567890")
    assert Config.SECRET_KEY == "1234567890"

    monkeypatch.setenv("SECRET_KEY", "123456789")
    try:
        Config.SECRET_KEY
    except RuntimeError:
        pass
    else:
        raise AssertionError("SECRET_KEY values shorter than 10 bytes must be rejected")

    monkeypatch.setenv("SECRET_KEY", "quantdinger-secret-key-change-me")
    try:
        Config.SECRET_KEY
    except RuntimeError:
        pass
    else:
        raise AssertionError("the public legacy SECRET_KEY must remain rejected")


def test_settings_reload_keeps_existing_jwt_valid(monkeypatch):
    from app.services.settings import runtime

    active_secret = "stable-jwt-secret-shared-by-every-worker"
    monkeypatch.setenv("SECRET_KEY", active_secret)
    token = auth.generate_token(
        user_id=1,
        username="admin",
        role="admin",
        token_version=1,
    )

    def fake_load(_path, override=True):
        assert override is True
        monkeypatch.setenv("SECRET_KEY", "different-secret-from-settings-file")
        monkeypatch.setenv("LLM_PROVIDER", "deepseek")

    monkeypatch.setattr(runtime, "load_dotenv", fake_load)
    runtime.reload_runtime_env()
    monkeypatch.setattr(auth, "_verify_token_version", lambda *_: True)
    monkeypatch.setattr(
        auth,
        "_get_user_auth_state",
        lambda _: {
            "username": "admin",
            "role": "admin",
            "status": "active",
            "token_version": 1,
        },
    )

    payload = auth.verify_token(token)

    assert Config.SECRET_KEY == active_secret
    assert payload is not None
    assert payload["_verified_username"] == "admin"


def test_missing_token_version_is_rejected_before_database_check(monkeypatch):
    calls = []
    monkeypatch.setattr(
        auth,
        "_verify_token_version",
        lambda user_id, token_version: calls.append((user_id, token_version)) or True,
    )
    claims = _claims()
    claims.pop("token_version")

    assert auth.verify_token(_encode(claims)) is None
    assert calls == []


def test_database_role_overrides_forged_admin_claim(monkeypatch):
    monkeypatch.setattr(auth, "_verify_token_version", lambda *_: True)
    monkeypatch.setattr(
        auth,
        "_get_user_auth_state",
        lambda _: {
            "username": "victim",
            "role": "user",
            "status": "active",
            "token_version": 1,
        },
    )

    payload = auth.verify_token(_encode(_claims(role="admin")))

    assert payload is not None
    assert payload["_verified_username"] == "victim"
    assert payload["_verified_user_role"] == "user"


def test_token_version_change_during_verification_is_rejected(monkeypatch):
    monkeypatch.setattr(auth, "_verify_token_version", lambda *_: True)
    monkeypatch.setattr(
        auth,
        "_get_user_auth_state",
        lambda _: {
            "username": "victim",
            "role": "user",
            "status": "active",
            "token_version": 2,
        },
    )

    assert auth.verify_token(_encode(_claims(token_version=1))) is None


def test_password_reset_invalidates_existing_token(monkeypatch):
    state = {
        "username": "victim",
        "role": "user",
        "status": "active",
        "password_hash": "old-hash",
        "token_version": 7,
    }

    class FakeCursor:
        result = None

        def execute(self, sql, params=()):
            normalized = " ".join(sql.lower().split())
            if normalized.startswith("update qd_users"):
                state["password_hash"] = params[0]
                if "token_version = coalesce(token_version, 1) + 1" in normalized:
                    state["token_version"] = (state["token_version"] or 1) + 1
                return
            if "select username, role, status, token_version" in normalized:
                self.result = dict(state)
                return
            raise AssertionError(f"Unexpected SQL: {normalized}")

        def fetchone(self):
            return self.result

        def close(self):
            pass

    class FakeConnection:
        def cursor(self):
            return FakeCursor()

        def commit(self):
            pass

    @contextmanager
    def fake_connection():
        yield FakeConnection()

    monkeypatch.setattr(user_service_module, "get_db_connection", fake_connection)
    monkeypatch.setattr(db_module, "get_db_connection", fake_connection)
    monkeypatch.setattr(UserService, "_password_changed_column_ready", True)

    service = UserService()
    monkeypatch.setattr(service, "hash_password", lambda _: "new-hash")
    token = auth.generate_token(1, "victim", "user", token_version=7)

    assert service.reset_password(1, "new-password") is True
    assert state["password_hash"] == "new-hash"
    assert state["token_version"] == 8
    assert auth.verify_token(token) is None


def test_admin_required_uses_only_verified_database_role(monkeypatch):
    app = Flask(__name__)

    @app.get("/admin")
    @auth.login_required
    @auth.admin_required
    def admin_route():
        return jsonify({"ok": True})

    monkeypatch.setattr(
        auth,
        "verify_token",
        lambda _: {
            "sub": "attacker",
            "user_id": 1,
            "role": "admin",
            "token_version": 1,
            "_verified_username": "victim",
            "_verified_user_role": "user",
        },
    )

    response = app.test_client().get(
        "/admin",
        headers={"Authorization": "Bearer forged"},
    )
    assert response.status_code == 403
