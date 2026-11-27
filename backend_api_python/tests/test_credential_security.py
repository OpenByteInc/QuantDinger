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


@pytest.mark.parametrize('code', sorted(credentials._SAFE_CREDENTIAL_ERRORS))
def test_credential_errors_return_the_canonical_allowlisted_object(code):
    supplied = code.encode().decode()
    assert supplied is not code
    assert credentials._credential_error(ValueError(supplied)) is code


@pytest.mark.parametrize('operation', ['test', 'create'])
@pytest.mark.parametrize('message,expected', [
    ('INVALID_CREDENTIAL_MARKET_SCOPE', 'INVALID_CREDENTIAL_MARKET_SCOPE'),
    ('api_secret=sensitive-marker at /srv/private/config.py', 'CREDENTIAL_CONNECTION_FAILED'),
    ('INVALID_CREDENTIAL_MARKET_SCOPE: sensitive-marker', 'CREDENTIAL_CONNECTION_FAILED'),
    ('CREDENTIAL_CONNECTION_FAILED\nsensitive-marker', 'CREDENTIAL_CONNECTION_FAILED'),
])
def test_credential_route_errors_do_not_reflect_exception_text(app, monkeypatch, operation, message, expected):
    def reject(*args, **kwargs):
        raise ValueError(message)

    monkeypatch.setattr(credentials, '_crypto_credential_config', reject)
    data = {'exchange_id': 'okx', 'api_key': 'test-key', 'secret_key': 'test-secret'}
    with app.test_request_context(f'/api/credentials/{operation}', method='POST', json=data):
        g.user_id = 7
        response, status = inspect.unwrap(getattr(credentials, f'{operation}_credential'))(data)
    assert status == 400
    assert response.get_json() == {'code': 0, 'msg': expected, 'data': None}


def test_credential_probe_failure_does_not_echo_provider_exception(app, monkeypatch):
    def reject(*args, **kwargs):
        raise RuntimeError('broker response contains api_secret=sensitive-marker')

    monkeypatch.setattr(credentials, '_crypto_credential_config', lambda *args: {})
    monkeypatch.setattr(credentials, '_probe_crypto_credential', reject)
    with app.test_request_context('/api/credentials/test', method='POST'):
        response, status = inspect.unwrap(credentials.test_credential)({'exchange_id': 'okx'})
    assert status == 400
    assert response.get_json() == {'code': 0, 'msg': 'CREDENTIAL_CONNECTION_FAILED', 'data': None}


def test_broker_config_repr_does_not_print_secrets():
    from app.services.openapi_brokers import TigerConfig, WebullConfig
    assert "secret-marker" not in repr(TigerConfig("tiger", "account", "secret-marker"))
    assert "secret-marker" not in repr(WebullConfig("app", "secret-marker", "secret-marker"))
