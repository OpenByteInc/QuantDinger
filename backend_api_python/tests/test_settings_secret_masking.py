def test_settings_values_masks_password_fields(client, monkeypatch):
    import app.routes.settings as settings_route
    import app.utils.auth as auth_mod

    monkeypatch.setattr(auth_mod, "verify_token", lambda token: {
        "sub": "admin",
        "user_id": 1,
        "role": "admin",
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
