from unittest.mock import Mock

import pytest

from app.routes import settings
from app.services.settings import runtime
from app.services.strategy_runtime.price_freshness import equity_quote_max_age_seconds


KEY = "EQUITY_QUOTE_MAX_AGE_MINUTES"


@pytest.fixture
def settings_io(monkeypatch):
    import app.utils.auth as auth

    monkeypatch.setattr(auth, "verify_token", lambda _: {
        "sub": "admin", "user_id": 1, "role": "admin",
        "_verified_username": "admin", "_verified_user_role": "admin",
    })
    monkeypatch.setattr(settings, "read_env_file", lambda: {})
    write, reload = Mock(return_value=True), Mock()
    monkeypatch.setattr(settings, "write_env_file", write)
    monkeypatch.setattr(settings, "reload_runtime_env", reload)
    return write, reload


@pytest.mark.parametrize("minutes", [1, 5, 15, 16, 60])
def test_admin_can_save_quote_age_with_restart_notice(client, settings_io, minutes):
    write, reload = settings_io
    response = client.post("/api/settings/save", headers={"Authorization": "Bearer test"},
                           json={"trading": {KEY: minutes}})
    assert response.status_code == 200
    assert response.json["data"]["restart_required_keys"] == [KEY]
    assert response.json["data"]["requires_restart"] is True
    assert write.call_args.args[0][KEY] == str(minutes)
    reload.assert_not_called()


@pytest.mark.parametrize("value", [0, -1, 61, 1.5, "nan", "inf", "bad", True])
def test_invalid_quote_age_never_reaches_settings_file(client, settings_io, value):
    write, _ = settings_io
    response = client.post("/api/settings/save", headers={"Authorization": "Bearer test"},
                           json={"trading": {KEY: value}})
    assert response.status_code == 400
    assert response.json["msg"] == "settings.invalidEquityQuoteMaxAge"
    write.assert_not_called()


def test_default_schema_and_saved_value_are_visible(client, settings_io, monkeypatch):
    field = next(item for item in settings.CONFIG_SCHEMA["trading"]["items"] if item["key"] == KEY)
    assert (field["default"], field["min"], field["max"], field["precision"]) == ("16", 1, 60, 0)
    assert KEY not in settings.ADVANCED_KEYS
    for stored, expected in (({}, "16"), ({KEY: "15"}, "15")):
        monkeypatch.setattr(settings, "read_env_file", lambda: stored)
        response = client.get("/api/settings/values", headers={"Authorization": "Bearer test"})
        assert response.json["data"]["trading"][KEY] == expected


@pytest.mark.parametrize("existing", [None, "5"])
def test_unrelated_hot_reload_cannot_change_worker_quote_policy(monkeypatch, existing):
    if existing is None:
        monkeypatch.delenv(KEY, raising=False)
    else:
        monkeypatch.setenv(KEY, existing)
    monkeypatch.setattr(runtime, "load_dotenv", lambda *a, **kw: monkeypatch.setenv(KEY, "30"))
    runtime.reload_runtime_env()
    assert equity_quote_max_age_seconds() == (int(existing) if existing else 16) * 60
