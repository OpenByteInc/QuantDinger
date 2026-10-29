import pytest


@pytest.fixture(autouse=True)
def saved_settings(monkeypatch):
    """Capture database writes from the settings save route."""
    import app.routes.settings as settings_route

    saved = {}
    monkeypatch.setattr(
        settings_route,
        "save_values",
        lambda updates, secret_keys=(), operator_id=None: saved.update(updates),
    )
    return saved


def test_settings_values_masks_password_fields(client, monkeypatch):
    import app.routes.settings as settings_route
    import app.utils.auth as auth_mod

    monkeypatch.setattr(auth_mod, "verify_token", lambda token: {
        "sub": "admin",
        "user_id": 1,
        "role": "admin",
        "_verified_username": "admin",
        "_verified_user_role": "admin",
    })
    monkeypatch.setattr(settings_route, "read_settings", lambda: {
        "CUSTOM_API_KEY": "real-secret",
        "LLM_PROVIDER": "custom",
    })
    monkeypatch.setattr(settings_route, "CONFIG_SCHEMA", {
        "ai": {
            "items": [
                {"key": "CUSTOM_API_KEY", "type": "password"},
                {"key": "LLM_PROVIDER", "type": "select"},
            ]
        }
    })

    resp = client.get("/api/settings/values", headers={"Authorization": "Bearer token"})
    assert resp.status_code == 200
    data = resp.get_json()["data"]["ai"]
    assert data["CUSTOM_API_KEY"] == ""
    assert data["CUSTOM_API_KEY_configured"] is True
    assert data["LLM_PROVIDER"] == "custom"


def test_settings_values_does_not_treat_password_default_as_configured(client, monkeypatch):
    import app.routes.settings as settings_route
    import app.utils.auth as auth_mod

    monkeypatch.setattr(auth_mod, "verify_token", lambda token: {
        "sub": "admin",
        "user_id": 1,
        "role": "admin",
        "_verified_username": "admin",
        "_verified_user_role": "admin",
    })
    monkeypatch.setattr(settings_route, "read_settings", lambda: {})
    monkeypatch.setattr(settings_route, "CONFIG_SCHEMA", {
        "security": {
            "items": [
                {"key": "ADMIN_PASSWORD", "type": "password", "default": "123456"},
            ]
        }
    })

    resp = client.get("/api/settings/values", headers={"Authorization": "Bearer token"})
    assert resp.status_code == 200
    data = resp.get_json()["data"]["security"]
    assert data["ADMIN_PASSWORD"] == ""
    assert data["ADMIN_PASSWORD_configured"] is False


def test_secret_key_save_requires_restart_without_hot_reload(client, monkeypatch):
    import app.routes.settings as settings_route
    import app.utils.auth as auth_mod

    monkeypatch.setattr(auth_mod, "verify_token", lambda token: {
        "sub": "admin",
        "user_id": 1,
        "role": "admin",
        "_verified_username": "admin",
        "_verified_user_role": "admin",
    })
    monkeypatch.setattr(settings_route, "CONFIG_SCHEMA", {
        "auth": {
            "items": [
                {"key": "SECRET_KEY", "type": "password"},
            ]
        }
    })
    monkeypatch.setattr(
        settings_route,
        "read_settings",
        lambda: {"SECRET_KEY": "current-persisted-secret"},
    )

    written = {}
    runtime_calls = []
    monkeypatch.setattr(
        settings_route,
        "write_env_file",
        lambda values: written.update(values) is None,
    )
    monkeypatch.setattr(
        settings_route,
        "clear_config_cache",
        lambda: runtime_calls.append("clear"),
    )
    monkeypatch.setattr(
        settings_route,
        "reload_runtime_env",
        lambda: runtime_calls.append("reload"),
    )
    monkeypatch.setattr(
        settings_route,
        "refresh_runtime_services",
        lambda: runtime_calls.append("refresh"),
    )

    resp = client.post(
        "/api/settings/save",
        headers={"Authorization": "Bearer token"},
        json={"auth": {"SECRET_KEY": "next-persisted-secret"}},
    )

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["code"] == 1
    assert written["SECRET_KEY"] == "next-persisted-secret"
    assert payload["data"]["requires_restart"] is True
    assert payload["data"]["restart_required_keys"] == ["SECRET_KEY"]
    assert payload["data"]["hot_reloaded"] is False
    assert payload["data"]["services_refreshed"] is False
    assert runtime_calls == []


def test_ai_setting_save_hot_reloads_without_restarting(client, monkeypatch):
    import app.routes.settings as settings_route
    import app.utils.auth as auth_mod

    monkeypatch.setattr(auth_mod, "verify_token", lambda token: {
        "sub": "admin",
        "user_id": 1,
        "role": "admin",
        "_verified_username": "admin",
        "_verified_user_role": "admin",
    })
    monkeypatch.setattr(settings_route, "CONFIG_SCHEMA", {
        "ai": {
            "items": [
                {"key": "LLM_PROVIDER", "type": "select"},
            ]
        }
    })
    monkeypatch.setattr(
        settings_route,
        "read_settings",
        lambda: {"LLM_PROVIDER": "openrouter"},
    )
    monkeypatch.setattr(settings_route, "write_env_file", lambda values: True)

    runtime_calls = []
    monkeypatch.setattr(
        settings_route,
        "clear_config_cache",
        lambda: runtime_calls.append("clear"),
    )
    monkeypatch.setattr(
        settings_route,
        "reload_runtime_env",
        lambda: runtime_calls.append("reload"),
    )
    monkeypatch.setattr(
        settings_route,
        "refresh_runtime_services",
        lambda: runtime_calls.append("refresh"),
    )

    resp = client.post(
        "/api/settings/save",
        headers={"Authorization": "Bearer token"},
        json={"ai": {"LLM_PROVIDER": "deepseek"}},
    )

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["code"] == 1
    assert payload["data"]["requires_restart"] is False
    assert payload["data"]["restart_required_keys"] == []
    assert payload["data"]["hot_reloaded_keys"] == ["LLM_PROVIDER"]
    assert payload["data"]["hot_reloaded"] is True
    assert payload["data"]["services_refreshed"] is True
    assert runtime_calls == ["clear", "reload", "refresh"]


def test_deployment_environment_keys_are_not_exposed(client, monkeypatch):
    import app.routes.settings as settings_route
    import app.utils.auth as auth_mod

    monkeypatch.setattr(auth_mod, "verify_token", lambda token: {
        "sub": "admin", "user_id": 1, "role": "admin",
        "_verified_username": "admin", "_verified_user_role": "admin",
    })
    monkeypatch.setattr(settings_route, "CONFIG_SCHEMA", {})
    monkeypatch.setattr(settings_route, "read_settings", lambda: {
        "POSTGRES_PASSWORD": "database-secret",
        "DATABASE_URL": "postgresql://user:secret@database/app",
        "KAFKA_MARKET_PARTITIONS": "48",
        "CUSTOM_FEATURE_FLAG": "true",
    })

    schema_response = client.get(
        "/api/settings/schema", headers={"Authorization": "Bearer token"}
    )
    values_response = client.get(
        "/api/settings/values", headers={"Authorization": "Bearer token"}
    )

    assert schema_response.get_json()["data"] == {}
    assert values_response.get_json()["data"] == {}


def test_deployment_environment_key_cannot_be_saved_from_settings(client, monkeypatch):
    import app.routes.settings as settings_route
    import app.utils.auth as auth_mod

    monkeypatch.setattr(auth_mod, "verify_token", lambda token: {
        "sub": "admin", "user_id": 1, "role": "admin",
        "_verified_username": "admin", "_verified_user_role": "admin",
    })
    monkeypatch.setattr(settings_route, "CONFIG_SCHEMA", {})
    monkeypatch.setattr(
        settings_route, "read_settings", lambda: {"KAFKA_MARKET_PARTITIONS": "48"}
    )
    written = {}
    monkeypatch.setattr(
        settings_route, "write_env_file", lambda values: not written.update(values)
    )

    response = client.post(
        "/api/settings/save",
        headers={"Authorization": "Bearer token"},
        json={"scalability": {"KAFKA_MARKET_PARTITIONS": "64"}},
    )

    payload = response.get_json()
    assert payload["code"] == 1
    assert written == {}
    assert payload["data"]["updated_keys"] == []
    assert payload["data"]["restart_required_keys"] == []
    assert payload["data"]["hot_reloaded"] is False


def test_saving_curated_setting_preserves_hidden_deployment_values(client, monkeypatch, saved_settings):
    import app.routes.settings as settings_route
    import app.utils.auth as auth_mod

    monkeypatch.setattr(auth_mod, "verify_token", lambda token: {
        "sub": "admin", "user_id": 1, "role": "admin",
        "_verified_username": "admin", "_verified_user_role": "admin",
    })
    monkeypatch.setattr(settings_route, "CONFIG_SCHEMA", {
        "runtime": {
            "items": [{"key": "VISIBLE_SETTING", "type": "text", "default": "old"}]
        }
    })
    monkeypatch.setattr(settings_route, "read_settings", lambda: {
        "VISIBLE_SETTING": "old",
        "POSTGRES_PASSWORD": "database-secret",
        "GRAFANA_ADMIN_PASSWORD": "dashboard-secret",
    })
    written = {}
    monkeypatch.setattr(
        settings_route, "write_env_file", lambda values: not written.update(values)
    )
    monkeypatch.setattr(settings_route, "clear_config_cache", lambda: None)
    monkeypatch.setattr(settings_route, "reload_runtime_env", lambda: None)
    monkeypatch.setattr(settings_route, "refresh_runtime_services", lambda: None)

    response = client.post(
        "/api/settings/save",
        headers={"Authorization": "Bearer token"},
        json={"runtime": {"VISIBLE_SETTING": "new"}},
    )

    assert response.get_json()["code"] == 1
    # The curated value goes to the database; the deployment file is untouched.
    assert saved_settings == {"VISIBLE_SETTING": "new"}
    assert written == {}


def test_unchanged_environment_value_does_not_rewrite_or_require_restart(client, monkeypatch):
    import app.routes.settings as settings_route
    import app.utils.auth as auth_mod

    monkeypatch.setattr(auth_mod, "verify_token", lambda token: {
        "sub": "admin", "user_id": 1, "role": "admin",
        "_verified_username": "admin", "_verified_user_role": "admin",
    })
    monkeypatch.setattr(settings_route, "CONFIG_SCHEMA", {})
    monkeypatch.setattr(
        settings_route, "read_settings", lambda: {"KAFKA_MARKET_PARTITIONS": "48"}
    )
    writes = []
    monkeypatch.setattr(settings_route, "write_env_file", lambda values: writes.append(values))

    response = client.post(
        "/api/settings/save",
        headers={"Authorization": "Bearer token"},
        json={"scalability": {"KAFKA_MARKET_PARTITIONS": "48"}},
    )

    payload = response.get_json()
    assert payload["code"] == 1
    assert payload["data"]["updated_keys"] == []
    assert payload["data"]["requires_restart"] is False
    assert writes == []


def test_secret_setting_is_saved_to_database_as_secret(client, monkeypatch):
    import app.routes.settings as settings_route
    import app.utils.auth as auth_mod

    monkeypatch.setattr(auth_mod, "verify_token", lambda token: {
        "sub": "admin", "user_id": 7, "role": "admin",
        "_verified_username": "admin", "_verified_user_role": "admin",
    })
    monkeypatch.setattr(settings_route, "CONFIG_SCHEMA", {
        "ai": {"items": [{"key": "OPENAI_API_KEY", "type": "password"}]}
    })
    monkeypatch.setattr(settings_route, "read_settings", lambda: {})
    calls = []
    monkeypatch.setattr(
        settings_route,
        "save_values",
        lambda updates, secret_keys=(), operator_id=None: calls.append((updates, set(secret_keys), operator_id)),
    )
    monkeypatch.setattr(settings_route, "write_env_file", lambda values: pytest.fail("file written"))
    monkeypatch.setattr(settings_route, "clear_config_cache", lambda: None)
    monkeypatch.setattr(settings_route, "reload_runtime_env", lambda: None)
    monkeypatch.setattr(settings_route, "refresh_runtime_services", lambda: None)

    response = client.post(
        "/api/settings/save",
        headers={"Authorization": "Bearer token"},
        json={"ai": {"OPENAI_API_KEY": "sk-new"}},
    )

    assert response.get_json()["code"] == 1
    assert calls == [({"OPENAI_API_KEY": "sk-new"}, {"OPENAI_API_KEY"}, 7)]
