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
    monkeypatch.setattr(settings_route, "read_env_file", lambda: {
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
    monkeypatch.setattr(settings_route, "read_env_file", lambda: {})
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


def test_secret_key_is_not_exposed_in_settings_schema():
    import app.routes.settings as settings_route

    exposed_keys = {
        item["key"]
        for group in settings_route.CONFIG_SCHEMA.values()
        for item in group["items"]
    }

    assert "SECRET_KEY" not in exposed_keys


def test_admin_account_settings_are_all_basic_fields():
    import app.routes.settings as settings_route

    auth_group = settings_route._effective_schema({})["auth"]
    items = {item["key"]: item for item in auth_group["items"]}

    assert auth_group["title"] == "settings.group.auth"
    assert auth_group["icon"] == "user"
    assert set(items) == {"ADMIN_USER", "ADMIN_PASSWORD", "ADMIN_EMAIL"}
    assert all(not item["is_advanced"] for item in items.values())


def test_ai_settings_expose_only_effective_controls_in_the_right_groups():
    import app.routes.settings as settings_route

    schema = settings_route._effective_schema({})
    ai_keys = {item["key"] for item in schema["ai"]["items"]}
    network_keys = {item["key"] for item in schema["network"]["items"]}

    assert "JEV_ENABLED" in ai_keys
    assert "SEC_EDGAR_USER_AGENT" not in ai_keys
    assert "LLM_PROXY_URL" not in ai_keys
    assert "LLM_USE_SYSTEM_PROXY" not in ai_keys
    assert {"LLM_PROXY_URL", "LLM_USE_SYSTEM_PROXY"}.issubset(network_keys)


def test_live_trading_settings_expose_only_order_execution_defaults():
    import app.routes.settings as settings_route

    trading_keys = {
        item["key"]
        for item in settings_route._effective_schema({})["trading"]["items"]
    }

    assert trading_keys == {
        "AGENT_LIVE_TRADING_ENABLED",
        "MAKER_WAIT_SEC",
        "MAKER_OFFSET_BPS",
    }
    assert "ALLOW_LOCAL_DESKTOP_BROKERS" not in trading_keys
    assert "ENABLE_PENDING_ORDER_WORKER" not in trading_keys
    assert "DISABLE_RESTORE_RUNNING_STRATEGIES" not in trading_keys


def test_fixed_token_identifiers_are_read_only_deployment_settings():
    import app.routes.settings as settings_route

    billing_items = {
        item["key"]: item
        for item in settings_route._effective_schema(
            {"BILLING_ENABLED": "true"}
        )["billing"]["items"]
    }

    for key in settings_route.FIXED_TOKEN_IDENTIFIERS:
        assert billing_items[key]["readonly"] is True
        assert billing_items[key]["source"] == "deployment"
        assert key in settings_route.DEPLOYMENT_ONLY_SETTINGS

    assert billing_items["USDT_TRC20_ADDRESS"].get("readonly") is not True
    assert billing_items["USDC_ERC20_ADDRESS"].get("readonly") is not True


def test_fixed_token_identifier_cannot_be_saved_from_settings(client, monkeypatch):
    import app.routes.settings as settings_route
    import app.utils.auth as auth_mod

    monkeypatch.setattr(auth_mod, "verify_token", lambda token: {
        "sub": "admin",
        "user_id": 1,
        "role": "admin",
        "_verified_username": "admin",
        "_verified_user_role": "admin",
    })
    monkeypatch.setattr(
        settings_route,
        "read_env_file",
        lambda: {"USDT_ERC20_CONTRACT": "official-contract"},
    )
    writes = []
    monkeypatch.setattr(
        settings_route,
        "write_env_file",
        lambda values: writes.append(values),
    )

    response = client.post(
        "/api/settings/save",
        headers={"Authorization": "Bearer token"},
        json={"billing": {"USDT_ERC20_CONTRACT": "attacker-contract"}},
    )

    assert response.status_code == 400
    assert response.get_json() == {
        "code": 0,
        "msg": "settings.deploymentOnlySetting",
        "data": {"keys": ["USDT_ERC20_CONTRACT"]},
    }
    assert writes == []


def test_secret_key_cannot_be_saved_from_settings(client, monkeypatch):
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
        "read_env_file",
        lambda: {"SECRET_KEY": "current-persisted-secret"},
    )

    writes = []
    monkeypatch.setattr(
        settings_route,
        "write_env_file",
        lambda values: writes.append(values),
    )

    resp = client.post(
        "/api/settings/save",
        headers={"Authorization": "Bearer token"},
        json={"auth": {"SECRET_KEY": "next-persisted-secret"}},
    )

    assert resp.status_code == 400
    payload = resp.get_json()
    assert payload == {
        "code": 0,
        "msg": "settings.deploymentOnlySetting",
        "data": {"keys": ["SECRET_KEY"]},
    }
    assert writes == []


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
        "read_env_file",
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
    monkeypatch.setattr(settings_route, "read_env_file", lambda: {
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
        settings_route, "read_env_file", lambda: {"KAFKA_MARKET_PARTITIONS": "48"}
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


def test_saving_curated_setting_preserves_hidden_deployment_values(client, monkeypatch):
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
    monkeypatch.setattr(settings_route, "read_env_file", lambda: {
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
    assert written == {
        "VISIBLE_SETTING": "new",
        "POSTGRES_PASSWORD": "database-secret",
        "GRAFANA_ADMIN_PASSWORD": "dashboard-secret",
    }


def test_unchanged_environment_value_does_not_rewrite_or_require_restart(client, monkeypatch):
    import app.routes.settings as settings_route
    import app.utils.auth as auth_mod

    monkeypatch.setattr(auth_mod, "verify_token", lambda token: {
        "sub": "admin", "user_id": 1, "role": "admin",
        "_verified_username": "admin", "_verified_user_role": "admin",
    })
    monkeypatch.setattr(settings_route, "CONFIG_SCHEMA", {})
    monkeypatch.setattr(
        settings_route, "read_env_file", lambda: {"KAFKA_MARKET_PARTITIONS": "48"}
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
