import inspect
import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from flask import g, jsonify

from app.routes import credentials, openapi_brokers
from app.utils.credential_crypto import encrypt_credential_blob
from app.utils.structured_redaction import redact_structured_secrets, strip_structured_secrets


@pytest.mark.parametrize("key", ["privateKey", "trade_password", "appSecret", "api_secret", "access_token", "encrypted_config", "smsCode"])
def test_nested_broker_secrets_are_masked_and_stripped(key):
    value = {"raw": [{key: "sensitive-marker", "symbol": "AAPL"}]}
    assert "sensitive-marker" not in json.dumps(redact_structured_secrets(value))
    assert strip_structured_secrets(value) == {"raw": [{"symbol": "AAPL"}]}


@pytest.mark.parametrize("module", [credentials, openapi_brokers])
def test_response_boundary_masks_provider_raw_fields_and_disables_cache(app, module):
    with app.test_request_context():
        response = module.protect_credential_response(jsonify({"data": {"raw": {"tradePassword": "secret-marker"}, "quantity": 100}}))
        assert "secret-marker" not in response.get_data(as_text=True)
        assert response.get_json()["data"]["quantity"] == 100
        assert response.headers["Cache-Control"] == "no-store"


def test_saved_credentials_list_exposes_only_metadata_for_current_user(app, monkeypatch):
    secret = "private-key-marker"
    blob = encrypt_credential_blob(json.dumps({"app_id":"test-app", "account_id":"account-A", "private_key":secret, "trade_password":"password-marker", "account_type":"securities", "environment":"sandbox"}))
    queries = []
    class Cursor:
        def execute(self, sql, params): queries.append((sql, params))
        def fetchall(self): return [{"id":18, "user_id":7, "exchange_id":"forthright", "encrypted_config":blob}]
        def close(self): pass
    @contextmanager
    def connection(): yield SimpleNamespace(cursor=Cursor)
    monkeypatch.setattr(credentials, "get_db_connection", connection)
    monkeypatch.setattr(credentials, "is_trading_venue_enabled", lambda _:True)
    with app.test_request_context():
        g.user_id = 7
        response = inspect.unwrap(credentials.list_credentials)()
        data = response.get_data(as_text=True)
        assert all(value not in data for value in (secret, "password-marker", blob, "encrypted_config"))
        assert response.get_json()["data"]["items"][0]["account_id"] == "account-A"
    assert "WHERE user_id = %s" in queries[0][0] and queries[0][1] == (7,)


def test_foreign_credential_selection_is_rejected_before_decryption(app, monkeypatch):
    seen=[]
    monkeypatch.setattr(openapi_brokers, "_rows", lambda user, broker: seen.append(user) or [{"id":18}])
    monkeypatch.setattr(openapi_brokers, "decrypt_credential_blob", lambda _:pytest.fail("Foreign credential reached decryption"))
    with app.test_request_context('/?credential_id=19'):
        with pytest.raises(ValueError, match="accountUnavailable"):
            openapi_brokers._load_config(7, "forthright")
    assert seen == [7]


@pytest.mark.parametrize("error_type", [RuntimeError, Exception])
def test_connect_exception_never_echoes_or_logs_credentials(app, monkeypatch, caplog, error_type):
    marker = "private-key-error-marker"
    monkeypatch.setattr(openapi_brokers, "_broker_id", lambda broker:broker)
    def reject(*args): raise error_type(marker)
    monkeypatch.setattr(openapi_brokers, "_request_config", reject)
    with app.test_request_context('/api/forthright/connect', method='POST', json={"privateKey":marker}):
        g.user_id=7
        response, status = inspect.unwrap(openapi_brokers.connect)("forthright")
    assert status in (400,500)
    assert marker not in response.get_data(as_text=True)
    assert marker not in caplog.text


def test_raw_credential_probe_exception_is_not_a_public_message():
    assert credentials._credential_error(RuntimeError("api-secret-marker")) == "CREDENTIAL_CONNECTION_FAILED"
    assert credentials._credential_error(ValueError("INVALID_CREDENTIAL_MARKET_SCOPE")) == "INVALID_CREDENTIAL_MARKET_SCOPE"


def test_broker_config_repr_does_not_print_secrets():
    from app.services.openapi_brokers import TigerConfig, WebullConfig
    assert "secret-marker" not in repr(TigerConfig("tiger", "account", "secret-marker"))
    assert "secret-marker" not in repr(WebullConfig("app", "secret-marker", "secret-marker"))
