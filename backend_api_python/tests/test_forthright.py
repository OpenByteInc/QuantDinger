import base64
from dataclasses import replace
from types import SimpleNamespace

import pytest
import requests
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.services.openapi_brokers.forthright import ForthrightClient, ForthrightConfig
from app.services.openapi_brokers.clients import BrokerApiError
from app.services.broker_market_policy import validate_strategy_config


@pytest.fixture
def config():
    key = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    return ForthrightConfig("OPA_example", "test-account", pem, "test-password")


@pytest.fixture
def client(config, monkeypatch):
    monkeypatch.setattr('app.services.market.hk_board_lots.board_lot_rule', lambda symbol: {"lot_size": 100})
    return ForthrightClient(config)


def test_official_signature_vector(client):
    client.base_url = "https://openapi.example"
    headers = client._headers("/account/list", b"{}", created=1700000000,
                              nonce="000102030405060708090a0b0c0d0e0f")
    assert headers["Content-Digest"] == "sha-256=:RBNvo1WzZ4oRRq0W9+hknpT7T8If536DEMBg9hyq/4o=:"
    assert headers["Signature"] == "sig1=:dFM2OJ53I7bwmP74+s2wBRTRu89RwzOqJW+ActP/9Jhdu65t1abUzAjWlg0GFXh/rUHPZCDIEziciVhEXjAgCA==:"


@pytest.mark.parametrize('key_type', ['ed25519', 'rsa'])
def test_unlock_signs_exact_body_digest_without_changing_password(config, monkeypatch, key_type):
    if key_type == 'rsa':
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode()
        config = replace(config, private_key=pem)
    config = replace(config, gateway_environment='sandbox')
    client = ForthrightClient(config)
    captured = {}

    def post(url, **kwargs):
        captured.update(url=url, **kwargs)
        return SimpleNamespace(ok=True, json=lambda: {'status': 0, 'data': {}})

    monkeypatch.setattr(client.session, 'post', post)
    assert client.unlock() is True
    assert captured['url'] == 'https://test-openapi-gw.techfx88.com/account/unlock'
    assert captured['allow_redirects'] is False
    assert client.session.verify is True
    assert captured['data'] == (b'{"trade_account":"test-account","trade_account_type":"securities",'
                                b'"password":"test-password"}')
    headers = captured['headers']
    digest = 'sha-256=:YDsHwX8GH8zImyjtMQcue/SEOV4efeN1xG5AO6wo+xA=:'
    assert headers['Content-Digest'] == digest
    message = '\n'.join([
        '"@method": POST', '"@authority": test-openapi-gw.techfx88.com',
        '"@path": /account/unlock', '"@query": ?', '"content-type": application/json',
        f'"content-digest": {digest}',
        f'"@signature-params": {headers["Signature-Input"].removeprefix("sig1=")}',
    ]).encode()
    signature = base64.b64decode(headers['Signature'].removeprefix('sig1=:').removesuffix(':'))
    public_key = client._key.public_key()

    def verify(body):
        if key_type == 'rsa':
            public_key.verify(signature, body, padding.PKCS1v15(), hashes.SHA256())
        else:
            public_key.verify(signature, body)

    verify(message)
    with pytest.raises(InvalidSignature):
        verify(message.replace(digest.encode(), b'sha-256=:tampered:'))


@pytest.mark.parametrize('exchange,currency,symbol', [('US','USD','AAPL'),('HK','HKD','00700')])
@pytest.mark.parametrize('fee', [0,64.38])
def test_terminal_stock_fill_keeps_broker_total_fees_in_instrument_currency(client,exchange,currency,symbol,fee):
    row={'order_id':'F1','exchange_code':exchange,'ccy':currency,'product_code':symbol,
         'order_status':'8','order_qty':100,'last_qty':100,'last_amt':34540,'side':'B','fare_amt':fee}
    result=client._result(client._order(row))
    assert result.filled == 100 and result.avg_price == 345.4
    assert result.raw['commission'] == fee
    assert result.raw['commission_ccy'] == currency
    assert result.raw['commission_quote'] == fee
    assert result.raw['fees_by_ccy'] == {currency:fee}
    assert result.raw['fee_status'] == ('actual' if fee else 'actual_zero')


@pytest.mark.parametrize('status,filled', [('0',0),('7',50)])
def test_unsettled_stock_order_does_not_treat_estimated_fees_as_final(client,status,filled):
    result=client._result(client._order({'order_status':status,'last_qty':filled,'last_amt':filled*100,
        'exchange_code':'US','product_code':'AAPL','fare_amt':64.38}))
    assert result.raw['fee_status'] == 'pending'
    assert 'commission' not in result.raw


def test_signed_bytes_are_exact_and_redirects_disabled(client, monkeypatch):
    captured = {}
    def post(url, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(ok=True, json=lambda: {"status": 0, "data": {}})
    monkeypatch.setattr(client.session, "post", post)
    client._post("/account/list", {})
    assert captured["data"] == b"{}"
    assert captured["allow_redirects"] is False
    assert captured["timeout"] <= 60


def test_private_credentials_are_not_in_config_repr(config):
    assert config.trade_password not in repr(config)
    assert config.private_key not in repr(config)


def test_legacy_gateway_stays_unchanged_when_switch_is_restored(config, monkeypatch):
    monkeypatch.delenv("FORTHRIGHT_GATEWAY_URL", raising=False)
    for environment in ("live", "sandbox"):
        client = ForthrightClient(replace(config, environment=environment))
        assert client.base_url == "https://test-openapi-gw.techfx88.com"
        assert "paper" not in client.get_connection_status()
    monkeypatch.setenv("FORTHRIGHT_GATEWAY_URL", "https://assigned.example")
    assert ForthrightClient(config).base_url == "https://assigned.example"


@pytest.mark.parametrize("environment,host", [
    ("sandbox", "https://test-openapi-gw.techfx88.com"),
    ("live", "https://openapi-gw.forthright-sec.com.hk"),
])
def test_explicit_gateway_selection_is_fixed_and_signed(config, monkeypatch, environment, host):
    from app.routes.openapi_brokers import _request_config
    monkeypatch.setenv("FORTHRIGHT_GATEWAY_URL", "https://legacy.example")
    payload = _request_config("forthright", {"environment": environment})
    assert payload["gateway_environment"] == environment
    client = ForthrightClient(replace(config, gateway_environment=payload["gateway_environment"]))
    assert client.base_url == host
    assert client.get_connection_status()["environment"] == environment
    assert client.get_connection_status()["gateway"] == host
    with pytest.raises(ValueError, match="invalidEnvironment"):
        ForthrightClient(replace(config, gateway_environment="https://other.example"))


def test_strategy_cannot_override_saved_gateway(monkeypatch):
    from app.services import exchange_execution
    monkeypatch.setattr(exchange_execution, "_load_credential_config", lambda *a, **kw: {
        "exchange_id": "forthright", "gateway_environment": "sandbox", "environment": "sandbox"})
    merged = exchange_execution.resolve_exchange_config({"credential_id": 19, "gateway_environment": "live", "environment": "live"}, user_id=1)
    assert merged["gateway_environment"] == "sandbox"
    assert merged["environment"] == "sandbox"


@pytest.mark.parametrize("environment,expected_id", [("sandbox", 19), ("live", 20)])
def test_saving_same_app_account_keeps_gateways_separate(app, monkeypatch, environment, expected_id):
    import json
    from app.routes import openapi_brokers as routes
    monkeypatch.delenv("FORTHRIGHT_GATEWAY_URL", raising=False)
    base = {"app_id": "OPA_example", "account_id": "F1", "account_type": "futures", "environment": "live"}
    statements = []
    cursor = SimpleNamespace(
        execute=lambda sql, params: statements.append((sql, params)),
        fetchall=lambda: [{"id": 20, "encrypted_config": json.dumps({**base, "gateway_environment": "live"})},
                         {"id": 19, "encrypted_config": json.dumps(base)}],
        close=lambda: None)
    db = SimpleNamespace(cursor=lambda: cursor)
    monkeypatch.setattr(routes, "decrypt_credential_blob", lambda value: value)
    monkeypatch.setattr(routes, "encrypt_credential_blob", lambda value: value)
    assert routes._save(1, "forthright", {**base, "gateway_environment": environment}, "account", connection=db) == expected_id
    assert statements[-1][1][-2:] == (expected_id, 1)


def test_account_selection_is_category_scoped(config, monkeypatch):
    c = ForthrightClient(replace(config, account_id="", account_type="futures"))
    monkeypatch.setattr(c, "_post", lambda *args: {"securities_accounts": ["S1"], "futures_accounts": ["F1"]})
    assert c.connect()
    assert c.account_id == "F1"
    c.account_id = "S1"
    with pytest.raises(BrokerApiError, match="brokerAccountUnavailable"):
        c.connect()


def test_multiple_accounts_require_explicit_selection(config, monkeypatch):
    c = ForthrightClient(replace(config, account_id=""))
    monkeypatch.setattr(c, "_post", lambda *args: {"securities_accounts": ["S1", "S2"]})
    with pytest.raises(BrokerApiError, match="brokerAccountUnavailable"):
        c.connect()


def test_empty_stock_snapshots_match_test_gateway(client, monkeypatch):
    monkeypatch.setattr(client, "_query", lambda *args, **kwargs: None)
    assert client.get_positions() == []
    assert client.get_orders() == []


def test_malformed_positions_do_not_imply_flat(client, monkeypatch):
    monkeypatch.setattr(client, "_query", lambda *args, **kwargs: [{"product_code": "AAPL", "exchange_code": "US"}])
    with pytest.raises(BrokerApiError, match="invalidSnapshot"):
        client.get_positions()


@pytest.mark.parametrize("market,symbol,qty", [("HKStock", "00700.HK", 1.5), ("USStock", "AAPL", float("nan")),
    ("USStock", "AAPL", 0.00001), ("Options", "AAPL", 1), ("USStock", "AAPL261218C00100000", 1),
    ("USStock", "HKStock:00700.HK", 1)])
def test_invalid_products_or_quantities_never_submit(client, monkeypatch, market, symbol, qty):
    monkeypatch.setattr(client, "_trade", lambda *a, **k: pytest.fail("must not submit"))
    with pytest.raises(ValueError):
        client.place_limit_order(symbol=symbol, market_type=market, side="buy", quantity=qty, price=100)


def test_hk_identity_and_client_order_id(client, monkeypatch):
    calls = []
    monkeypatch.setattr(client, "_trade", lambda method, **body: calls.append(body) or {"order_id": "order-1"})
    result = client.place_limit_order(symbol="700.HK", market_type="HKStock", side="buy", quantity=100, price=250,
                                     client_order_id="stable-1")
    assert result.filled == 0
    assert calls[0]["exchange_code"] == "HK"
    assert calls[0]["product_code"] == "00700"
    assert calls[0]["external_serial_no"] == "stable-1"


def test_market_stock_requires_reference_price(client):
    with pytest.raises(ValueError, match="referencePriceRequired"):
        client.place_market_order(symbol="AAPL", side="buy", quantity=1)


def test_strategy_symbol_aliases_cannot_fragment_position_ownership(client):
    client.strategy_units = True
    client.validate_instrument("00700.HK", "HKStock")
    client.validate_instrument("AAPL", "USStock")
    for symbol, market in (("700.HK", "HKStock"), ("00700", "HKStock"), ("AAPL.US", "USStock")):
        with pytest.raises(ValueError, match="canonicalSymbolRequired"):
            client.validate_instrument(symbol, market)


def test_timeout_is_not_retried(client, monkeypatch):
    calls = []
    def query(*args, **kwargs):
        calls.append(args)
        raise requests.Timeout("read timed out")
    monkeypatch.setattr(client, "_query", query)
    with pytest.raises(requests.Timeout):
        client.place_limit_order(symbol="AAPL", side="buy", quantity=1, price=10)
    assert len(calls) == 1


def test_unlock_retries_only_proven_nonacceptance(client, monkeypatch):
    calls = []
    def query(method, **body):
        calls.append(body)
        if len(calls) == 1:
            raise BrokerApiError("brokerAccounts.forthright.unlockRequired")
        return {"order_id": "O1"}
    monkeypatch.setattr(client, "_query", query)
    monkeypatch.setattr(client, "unlock", lambda: True)
    client.place_limit_order(symbol="AAPL", side="buy", quantity=1, price=10, client_order_id="stable")
    assert len(calls) == 2
    assert calls[0] == calls[1]


@pytest.fixture
def futures(config, monkeypatch):
    c = ForthrightClient(replace(config, account_type="futures"))
    c._contracts = [{"prodCode": "MESZ99", "instmntCode": "MES", "expiryYmd": "20991231", "lotSize": "1"}]
    return c


def test_futures_uses_exact_contract_and_integer_lots(futures, monkeypatch):
    calls = []
    monkeypatch.setattr(futures, "_trade", lambda method, **body: calls.append(body) or {"extOrderId": "E1", "accOrderNo": 12})
    with pytest.raises(ValueError, match="contractRequired"):
        futures.place_market_order(symbol="MES", market_type="Futures", side="buy", quantity=1)
    result = futures.place_market_order(symbol="MESZ99", market_type="Futures", side="sell", quantity=2,
                                        client_order_id="a-long-stable-pending-order-id")
    assert result.order_id == "E1"
    assert calls[0]["order_qty"] == 2
    assert isinstance(calls[0]["order_qty"], int)
    assert len(calls[0]["clOrderId"]) == 16
    assert calls[0]["order_type"] == 6


def test_futures_lot_size_is_not_point_value(futures, monkeypatch):
    monkeypatch.delenv("FORTHRIGHT_FUTURES_CONTRACT_SPECS", raising=False)
    with pytest.raises(BrokerApiError, match="contractMetadataRequired"):
        futures.contract_size("MESZ99")
    monkeypatch.setenv("FORTHRIGHT_FUTURES_CONTRACT_SPECS", '{"MES":{"point_value":5,"currency":"USD"}}')
    assert futures.contract_size("MESZ99") == 5
    assert futures.normalize_strategy_quantity("MESZ99", 14) == 10


def test_futures_strategy_sizing_preserves_base_unit_ledger(futures, monkeypatch):
    futures.strategy_units = True
    monkeypatch.setenv("FORTHRIGHT_FUTURES_CONTRACT_SPECS", '{"MES":{"point_value":5,"currency":"USD"}}')
    calls = []
    monkeypatch.setattr(futures, "_trade", lambda method, **body: calls.append(body) or {"extOrderId": "E1"})
    futures.place_market_order(symbol="MESZ99", market_type="Futures", side="buy", quantity=10)
    assert calls[0]["order_qty"] == 2
    monkeypatch.setattr(futures, "_pages", lambda *a, **k: [
        {"extOrderId": "E1", "tradeQty": 1, "avgTradedPrc": 5999.5, "tradeNo": 1},
        {"extOrderId": "E1", "tradeQty": 1, "avgTradedPrc": 6000.5, "tradeNo": 2},
    ])
    order = futures._order({"prodCode": "MESZ99", "extOrderId": "E1", "accOrderNo": 12, "totalQty": 2,
                            "tradedQty": 2, "totalTrdPrc": 12000, "status": 9})
    assert order["filled_qty"] == 10
    assert order["filled_avg_price"] == 6000


def test_cancel_uses_all_broker_identifiers_after_restart(futures, monkeypatch):
    raw = {"extOrderId": "E1", "accOrderNo": 12, "prodCode": "MESZ99", "buySell": "S"}
    monkeypatch.setattr(futures, "_find_order", lambda **kwargs: raw)
    calls = []
    monkeypatch.setattr(futures, "_trade", lambda method, **body: calls.append((method, body)))
    assert futures.cancel_order("E1")
    assert calls == [("cancel_order", {"external_order_id": "E1", "accOrderNo": 12, "product_code": "MESZ99", "side": "S"})]


@pytest.mark.parametrize("code,status", [(5, "submitted"), (8, "partially_filled"), (9, "filled"),
    (10, "cancelled"), (28, "cancelled"), (21, "rejected"), (99, "submitted")])
def test_futures_status_mapping(futures, code, status):
    assert futures._order({"status": code})["status"] == status


def test_futures_policy_does_not_enable_other_brokers():
    with pytest.raises(ValueError):
        validate_strategy_config(exchange_id="forthright", market_category="Futures", market_type="spot", trade_direction="both")
    with pytest.raises(ValueError):
        validate_strategy_config(exchange_id="tiger", market_category="Futures", market_type="spot")
    with pytest.raises(ValueError):
        validate_strategy_config(exchange_id="forthright", market_category="USStock", trade_direction="short")


def test_futures_margin_uses_detail_base_currency_not_cash_bucket(futures, monkeypatch):
    def query(method, **kwargs):
        if method == "fund_total":
            return [{"ccy": "USD", "cash": 100}, {"ccy": "HK-HKD", "cash": 2000000}]
        assert method == "fund_detail"
        return {"baseCcy": "USD", "avFund": 252542.52, "cashBal": 255301.32,
                "netEquity": 255301.32, "imargin": 2758.8, "mmargin": 2510.508, "orderLockup": 0}
    monkeypatch.setattr(futures, "_query", query)
    data = futures.get_account_summary("HKD")
    assert data["currency"] == "USD"
    assert data["available_funds"] == data["buying_power"] == 252542.52
    assert data["cash"] == data["equity"] == 255301.32
    assert data["balances"][0]["cash"] == 100
    assert data["initial_margin"] == 2758.8
    assert data["maintenance_margin"] == 2510.508
    assert data["frozen_funds"] == 0


def test_missing_margin_never_becomes_cash_or_zero(futures, monkeypatch):
    monkeypatch.setattr(futures, "_query", lambda method, **kw: [{"ccy": "USD", "cash": 100}] if method == "fund_total" else {"baseCcy": "USD"})
    data = futures.get_account_summary()
    assert data["available_funds"] is None
    assert data["buying_power"] is None
    assert data["initial_margin"] is None


def test_futures_runtime_factory_is_disabled_before_connect(monkeypatch):
    from app.services.live_trading.factory import create_client
    monkeypatch.setattr(ForthrightClient, "connect", lambda self: pytest.fail("Futures strategy must not connect"))
    with pytest.raises(Exception, match="liveMarketUnsupported"):
        create_client({"exchange_id": "forthright", "account_type": "futures"})


def test_saved_futures_deployment_cannot_be_started(monkeypatch):
    import inspect
    from flask import Flask
    from app.routes import strategy
    monkeypatch.setattr(strategy, "_strategy", lambda _: {"execution_mode": "live", "market_category": "Futures"})
    monkeypatch.setattr(strategy, "is_live_trading_enabled", lambda: True)
    monkeypatch.setattr(strategy, "get_strategy_service", lambda: pytest.fail("Must reject before changing runtime state"))
    with Flask(__name__).test_request_context():
        response, status = inspect.unwrap(strategy.start_strategy)(19)
        assert status == 400
        assert "liveMarketUnsupported" in response.get_data(as_text=True)


def test_logging_redacts_entire_private_key_and_password(config):
    from app.services.exchange_execution import safe_exchange_config_for_log
    result = safe_exchange_config_for_log({"private_key": config.private_key, "trade_password": config.trade_password})
    assert result == {"private_key": "[redacted]", "trade_password": "[redacted]"}


def test_malformed_order_snapshot_is_not_empty(client, monkeypatch):
    monkeypatch.setattr(client, "_query", lambda *a, **k: {})
    with pytest.raises(BrokerApiError, match="invalidSnapshot"):
        client.get_orders(status="open")


@pytest.mark.parametrize("http_status,business_status", [(502, None), (200, 10000)])
def test_ambiguous_gateway_response_enters_reconciliation_without_retry(client, monkeypatch, http_status, business_status):
    from app.services.pending_orders.broker_support import submit_broker_order
    from app.services.pending_orders.submission_recovery import SubmissionRecoveryMixin
    calls = []
    def post(*args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(status_code=http_status, ok=http_status == 200,
                               json=lambda: {"status": business_status, "data": None})
    monkeypatch.setattr(client.session, "post", post)
    with pytest.raises(ConnectionError) as caught:
        submit_broker_order(client, symbol="AAPL", side="buy", quantity=1,
                            order_type="limit", limit_price=10, session_market="Futures")
    assert len(calls) == 1
    assert SubmissionRecoveryMixin._is_ambiguous_submit_error(caught.value)
    from app.routes.openapi_brokers import _public_error
    assert _public_error(caught.value, "fallback") == "brokerAccounts.forthright.executionUnknown"


def test_market_order_outside_session_is_localized_and_not_retried(client, monkeypatch):
    calls = []
    def post(*args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(status_code=200, ok=True, json=lambda: {"status": 10007, "data": None})
    monkeypatch.setattr(client.session, "post", post)
    with pytest.raises(BrokerApiError, match="marketSessionClosed"):
        client.place_market_order(symbol="AAPL", side="buy", quantity=1, reference_price=200)
    assert len(calls) == 1


def test_partial_fill_waits_for_matching_execution_quantity(futures, monkeypatch):
    monkeypatch.setattr(futures, "_pages", lambda *a, **k: [
        {"extOrderId": "E1", "tradeQty": 1, "avgTradedPrc": 6000, "tradeNo": 1},
    ])
    with pytest.raises(BrokerApiError, match="invalidSnapshot"):
        futures._order({"extOrderId": "E1", "accOrderNo": 12, "tradedQty": 2})


def test_futures_history_uses_metadata_without_requiring_an_unexpired_contract(futures, monkeypatch):
    monkeypatch.setenv("FORTHRIGHT_FUTURES_CONTRACT_SPECS", '{"MES":{"point_value":5,"currency":"USD"}}')
    assert futures.contract_size("MESZ20", "MES") == 5


def test_order_lookup_and_cancel_do_not_depend_on_unrelated_fill_data(futures, monkeypatch):
    monkeypatch.setattr(futures, "_pages", lambda *a, **k: [
        {"extOrderId": "old", "prodCode": "expired", "tradedQty": 99},
        {"extOrderId": "new", "prodCode": "MESZ99", "accOrderNo": 12, "buySell": "B"},
    ])
    calls = []
    monkeypatch.setattr(futures, "_trade", lambda *a, **k: calls.append(k))
    assert futures.cancel_order("new")
    assert calls[0]["external_order_id"] == "new"


def test_dated_futures_data_uses_exact_configured_symbol(monkeypatch):
    from app.data_sources.futures import FuturesDataSource
    data = object.__new__(FuturesDataSource)
    data.exchange = SimpleNamespace(fetch_ticker=lambda *a: pytest.fail("must not route to crypto"))
    monkeypatch.delenv("FORTHRIGHT_FUTURES_CONTRACT_SPECS", raising=False)
    with pytest.raises(ValueError, match="contractMetadataRequired"):
        data.get_ticker("HSIZ99")
    monkeypatch.setenv("FORTHRIGHT_FUTURES_CONTRACT_SPECS", '{"MESZ99":{"data_provider":"yfinance","data_symbol":"MESZ99.CME"}}')
    calls = []
    monkeypatch.setattr(data, "_get_ticker_yfinance", lambda symbol, **kw: calls.append((symbol, kw)) or {"last": 6000})
    assert data.get_ticker("MESZ99") == {"symbol": "MESZ99", "last": 6000}
    assert calls == [("MESZ99.CME", {"exact": True})]
    monkeypatch.setenv("FORTHRIGHT_FUTURES_CONTRACT_SPECS", '{"MESZ99":{"data_provider":"yfinance","data_symbol":"MES=F"}}')
    with pytest.raises(ValueError, match="contractMetadataRequired"):
        data.get_ticker("MESZ99")


def test_futures_short_signals_keep_stock_and_crypto_spot_restrictions():
    from app.services.strategy_v2.live_execution import LiveOrderRequest, StrategyV2OrderGateway
    from app.services.strategy_runtime.signals import StrategySignal
    from app.services.strategy_live_guard import resolve_strategy_direction_mode
    request = LiveOrderRequest(strategy_id=1, strategy_run_id=2, user_id=3, symbol="MESZ99",
                               action="open_short", quantity=5, reference_price=6000,
                               signal_timestamp=1, market_type="spot", execution_mode="live",
                               exchange_id="forthright", market_category="Futures")
    assert StrategyV2OrderGateway._validate(request) == request
    StrategySignal(timestamp=1, symbol="MESZ99", action="open_short", market_type="spot",
                   metadata={"market_category": "Futures"}).validate()
    assert resolve_strategy_direction_mode({"market_category": "Futures", "market_type": "spot",
                                            "direction_mode": "short_only"}) == "short_only"
    for market in ("USStock", "Crypto", ""):
        with pytest.raises(ValueError, match="spotShortUnsupported"):
            StrategyV2OrderGateway._validate(replace(request, market_category=market))
        with pytest.raises(ValueError, match="short"):
            StrategySignal(timestamp=1, symbol="AAPL", action="open_short", market_type="spot",
                           metadata={"market_category": market}).validate()


def test_saved_credential_selection_is_tenant_scoped(app, monkeypatch):
    import json
    from app.routes import openapi_brokers as routes
    calls = []
    monkeypatch.setattr(routes, "_rows", lambda user_id, broker: calls.append((user_id, broker)) or [
        {"id": 7, "encrypted_config": json.dumps({"account_type": "securities", "account_id": "S"})},
        {"id": 8, "encrypted_config": json.dumps({"account_type": "futures", "account_id": "F"})},
    ])
    monkeypatch.setattr(routes, "decrypt_credential_blob", lambda value: value)
    with app.test_request_context('/api/forthright/contracts?credential_id=8'):
        assert routes._load_config(101, "forthright")["account_id"] == "F"
    with app.test_request_context('/api/forthright/contracts?credential_id=99'):
        with pytest.raises(ValueError, match="accountUnavailable"):
            routes._load_config(101, "forthright")
    assert calls == [(101, "forthright"), (101, "forthright")]


def test_forthright_endpoints_require_login(app):
    client = app.test_client()
    for method, path in (("post", "unlock"), ("get", "contracts"), ("post", "order"), ("post", "close-position")):
        assert getattr(client, method)(f"/api/forthright/{path}").status_code in {401, 403}


def test_one_app_discovers_supported_subaccounts_only(client, monkeypatch):
    monkeypatch.setattr(client, "_post", lambda *args: {
        "securities_accounts": ["S1", "S1"], "futures_accounts": ["F1", "F2"], "usop_accounts": ["O1"]})
    assert client.discover_accounts() == [
        {"account_type": "securities", "account_id": "S1"},
        {"account_type": "futures", "account_id": "F1"},
        {"account_type": "futures", "account_id": "F2"}]


@pytest.mark.parametrize("encoding", ["utf-8", "gb18030"])
def test_broker_error_decodes_bytes_without_silent_replacement(client, monkeypatch, encoding):
    import json
    result = {"status": 10000, "info": "service response failed: status=1 info=价格错误"}
    response = requests.Response()
    response.status_code = 200
    response._content = json.dumps(result, ensure_ascii=False).encode(encoding)
    response.encoding = "utf-8"
    calls = []
    monkeypatch.setattr(client.session, "post", lambda *a, **k: calls.append(a) or response)
    with pytest.raises(ConnectionError) as error:
        client._post("/future/trade/place_order", {})
    assert len(calls) == 1
    assert error.value.broker_details["message"] == result["info"]
    assert error.value.broker_details["encoding"] == encoding
    assert error.value.broker_details["replacement_on_wire"] is False


def test_irrecoverable_upstream_replacement_is_reported_not_guessed(client):
    response = requests.Response()
    response._content = '{"status":10000,"info":"\uFFFD"}'.encode()
    result, decoding = client._decode_response(response)
    assert result["info"] == "\uFFFD"
    assert decoding["replacement_on_wire"] is True


def test_error_details_redact_request_secrets(client):
    result = {"status": 10000, "info": f"{client.config.private_key} {client.config.trade_password} {client.config.app_id} 4321"}
    details = client._error_details(result, SimpleNamespace(status_code=200), {}, {"sms_code": "4321"})
    assert all(value not in details["message"] for value in [client.config.private_key, client.config.trade_password, client.config.app_id, "4321"])


def test_success_snapshot_never_uses_guessed_encoding(client):
    response = requests.Response()
    response._content = '{"status":0,"data":{"name":"港币"}}'.encode("gb18030")
    with pytest.raises(ValueError):
        client._decode_response(response)


def test_stock_balances_request_the_order_currency_without_mutating_config(client, monkeypatch):
    calls = []
    monkeypatch.setattr(client, "_query", lambda method, **kwargs: (calls.append(kwargs) or {"asset": 780, "ccy": "HKD", "net_cash_amt": 780, "enable_buy_power": 780, "market_value": 0}) if method == "fund_total" else [{"ccy": "HKD", "cash_buy_power": 780, "t0": 780, "desirable_amt": 780}])
    assert client.get_account_summary("HKD")["currency"] == "HKD"
    assert calls == [{"to_ccy": "HKD"}]
    assert client.config.currency == "USD"


def test_hk_order_filter_accepts_chart_ticker(client, monkeypatch):
    monkeypatch.setattr(client, "_query", lambda *a, **k: [{"order_id": "O1", "exchange_code": "HK", "product_code": "03690", "order_status": "1", "side": "B", "order_qty": 100}])
    assert client.get_orders(status="open", symbol="03690")[0]["symbol"] == "03690.HK"


@pytest.mark.parametrize("fail_second", [False, True])
def test_discovered_accounts_save_atomically_and_publish_sessions_after_commit(app, monkeypatch, fail_second):
    from contextlib import contextmanager
    from flask import g
    from app.routes import openapi_brokers as routes
    events = []
    db = SimpleNamespace(commit=lambda: events.append("commit"))
    @contextmanager
    def connection():
        try:
            yield db
        except Exception:
            events.append("rollback")
            raise
    class Client:
        connected = False
        def __init__(self, cfg): self.cfg = cfg
        def discover_accounts(self): return [{"account_type": "securities", "account_id": "S"}, {"account_type": "futures", "account_id": "F"}]
        def get_connection_status(self): return {"account_id": self.cfg["account_id"], "account_type": self.cfg["account_type"]}
    def save(user_id, broker, cfg, name, *, connection):
        assert user_id == 101 and connection is db
        events.append(cfg["account_id"])
        if fail_second and cfg["account_id"] == "F": raise RuntimeError("save failed")
        return 1 if cfg["account_id"] == "S" else 2
    monkeypatch.setattr(routes, "get_db_connection", connection)
    monkeypatch.setattr(routes, "_client", lambda broker, cfg: Client(cfg))
    monkeypatch.setattr(routes, "_save", save)
    monkeypatch.setitem(routes._sessions, "forthright", SimpleNamespace(set=lambda client, credential_id: events.append(f"session:{credential_id}")))
    with app.test_request_context('/api/forthright/connect'):
        g.user_id = 101
        if fail_second:
            with pytest.raises(RuntimeError): routes._connect_forthright_accounts({}, "App")
            assert events == ["S", "F", "rollback"]
        else:
            response = routes._connect_forthright_accounts({}, "App").get_json()
            assert len(response["data"]["accounts"]) == 2
            assert events == ["S", "F", "commit", "session:1", "session:2"]


def test_fills_route_preserves_broker_normalized_hk_symbol(app, monkeypatch):
    import inspect
    from app.routes import openapi_brokers as routes
    calls = []
    def orders(**kwargs):
        calls.append(kwargs)
        return [{"symbol": "03690.HK", "status": "filled", "order_id": "F1", "filled_qty": 100}]
    monkeypatch.setattr(routes, "_connected_client", lambda broker: (SimpleNamespace(get_orders=orders), None))
    with app.test_request_context('/api/forthright/fills?symbol=03690'):
        response = inspect.unwrap(routes.fills)("forthright").get_json()
    assert response["data"][0]["symbol"] == "03690.HK"
    assert len(response["data"]) == 1
    assert response["data"][0]["filled_qty"] == 100
    assert calls[0]["symbol"] == "03690"


def test_reporting_cash_does_not_become_native_usd_cash(client, monkeypatch):
    def query(method, **kwargs):
        if method == "fund_total":
            return {"asset": 1269971.21, "ccy": "USD", "net_cash_amt": 1269971.21,
                    "enable_buy_power": 1269967.80, "market_value": 0}
        return [{"ccy": "HKD", "cash_buy_power": 9998863.93, "t0": 9999212.67, "desirable_amt": 9998863.93},
                {"ccy": "USD", "cash_buy_power": -4.01, "t0": 0, "desirable_amt": -4.01}]
    monkeypatch.setattr(client, "_query", query)
    data = client.get_account_summary("USD")
    assert data["cash"] == 1269971.21
    assert data["cash_is_converted"] is True
    assert data["available_cash"] == -4.01
    assert data["balances"][0]["currency"] == "HKD"
    assert data["balances"][0]["settled_cash"] == 9999212.67


def test_closed_stock_rows_are_not_current_positions(client, monkeypatch):
    monkeypatch.setattr(client, "_query", lambda *a, **k: [
        {"exchange_code": "HK", "product_code": "00700", "quality": 0, "avg_price": 0, "price": 344.8},
        {"exchange_code": "HK", "product_code": "03690", "quality": 10, "avg_price": 0, "avg_buying_price": 64.1, "price": 64.1}])
    rows = client.get_positions()
    assert len(rows) == 1 and rows[0]["quantity"] == 10
    assert rows[0]["currency"] == "HKD"
    assert rows[0]["avg_entry_price"] == 64.1


def test_stock_position_preserves_zero_sellable_quantity(client, monkeypatch):
    monkeypatch.setattr(client, "_query", lambda *a, **k: [{
        "exchange_code": "HK", "product_code": "00700", "quality": 100,
        "enable_qty": 0, "avg_price": 345.4, "entrust_sell_qty": 100}])
    position = client.get_positions()[0]
    assert position["quantity"] == 100
    assert position["salable_qty"] == position["available_qty"] == 0


def test_stock_order_history_uses_calendar_time_latest_order_and_order_fee(client, monkeypatch):
    base = {"exchange_code": "HK", "product_code": "00700", "side": "B",
            "order_qty": 100, "last_qty": 40, "last_amt": 13800, "fare_amt": 12.5,
            "curr_date": 20261126, "curr_time": 90501, "order_status": "6"}
    monkeypatch.setattr(client, "_query", lambda method, **kw: [dict(base, order_id="new", curr_time=100000)] if method == "today_order" else [dict(base, order_id="old")])
    orders = client.get_orders(limit=1)
    assert orders[0]["id"] == "new"
    assert orders[0]["submitted_at"] == "2026-11-26T10:00:00"
    assert orders[0]["qty"] == 100
    assert orders[0]["filled_avg_price"] == 345
    assert orders[0]["fee_amount"] == 12.5 and orders[0]["fee_currency"] == "HKD"
    assert orders[0]["fee_status"] == "actual"


def test_true_stock_fills_include_partial_cancelled_executions_once(client, monkeypatch):
    base = {"exchange_code": "HK", "product_code": "00700", "side": "B", "ccy": "HKD",
            "order_id": "partial-cancelled", "trade_date": 20261127, "curr_date": 20261126,
            "last_qty": 10, "last_px": 345.4, "serial_no": 1, "business_time": 90501}
    second = dict(base, serial_no=2, last_qty=20, last_px=345.5, business_time=90502)
    monkeypatch.setattr(client, "_query", lambda method, **kw: [base, second] if method == "history_match" else [second])
    rows = client.get_transactions(symbol="700")
    assert len(rows) == 2 and sum(r["filled_qty"] for r in rows) == 30
    assert rows[0]["amount"] == 6910
    assert rows[0]["filled_at"] == "2026-11-26T09:05:02"
    assert all(r["symbol"] == "00700.HK" and r["fee_status"] == "pending" and "commission" not in r for r in rows)
    assert len(client.get_transactions(limit=1)) == 1
    assert client.get_transactions(symbol="AAPL") == []


@pytest.mark.parametrize("field,value", [("last_qty", -1), ("last_px", float("nan")), ("serial_no", None), ("side", "unknown")])
def test_invalid_fill_never_becomes_a_successful_history(client, monkeypatch, field, value):
    row = {"exchange_code": "US", "product_code": "AAPL", "side": "B", "ccy": "USD",
           "order_id": "F1", "trade_date": 20261126, "last_qty": 1, "last_px": 100, "serial_no": 1}
    row[field] = value
    monkeypatch.setattr(client, "_query", lambda *a, **k: [row])
    with pytest.raises(BrokerApiError, match="invalidSnapshot"):
        client.get_transactions()


def test_stock_fills_api_failure_does_not_silently_fall_back_to_orders(app, client, monkeypatch):
    import inspect
    from app.routes import openapi_brokers as routes
    monkeypatch.setattr(client, "_query", lambda *a, **k: (_ for _ in ()).throw(ConnectionError("unavailable")))
    monkeypatch.setattr(client, "get_orders", lambda **k: pytest.fail("Must not hide incomplete history"))
    monkeypatch.setattr(routes, "_connected_client", lambda broker: (client, None))
    with app.test_request_context('/api/forthright/fills'):
        response, status = inspect.unwrap(routes.fills)("forthright")
    assert status == 500 and response.get_json()["success"] is False


@pytest.mark.parametrize("currency", ["CNY", "EUR"])
def test_new_account_only_accepts_supported_display_currencies(currency):
    from app.routes.openapi_brokers import _request_config
    with pytest.raises(ValueError):
        _request_config("forthright", {"currency": currency})


def test_recorded_authorization_is_tenant_scoped_and_survives_reload(app, client, monkeypatch):
    import inspect
    import json
    from dataclasses import asdict
    from contextlib import contextmanager
    from flask import g
    from app.routes import openapi_brokers as routes
    stored = {"encrypted_config": json.dumps(asdict(client.config))}
    statements = []
    class Cursor:
        def execute(self, sql, params):
            statements.append((sql, params))
            assert "user_id = %s" in sql and "exchange_id = %s" in sql
            if sql.startswith("UPDATE"):
                assert params[1:] == (18, 101, "forthright")
                stored["encrypted_config"] = params[0]
            else:
                assert params == (18, 101, "forthright")
        def fetchone(self): return stored
        def close(self): pass
    @contextmanager
    def connection():
        yield SimpleNamespace(cursor=Cursor, commit=lambda: None)
    monkeypatch.setattr(routes, "get_db_connection", connection)
    monkeypatch.setattr(routes, "encrypt_credential_blob", lambda text: text)
    monkeypatch.setattr(routes, "decrypt_credential_blob", lambda text: text)
    monkeypatch.setattr(routes, "_rows", lambda user, broker: [{"id": 18, **stored}] if user == 101 else [])
    monkeypatch.setattr(routes, "_broker_id", lambda value: value)
    with app.test_request_context('/api/forthright/unlock', method='POST', json={"credential_id": 18}):
        g.user_id = 101
        routes._record_forthright_authorization(client, "verified")
    for _ in range(2):
        with app.test_request_context('/api/forthright/authorization?credential_id=18'):
            g.user_id = 101
            response = inspect.unwrap(routes.forthright_authorization)().get_json()
            assert response["data"]["state"] == "verified"
            assert response["data"]["checked_at"]
            assert "private_key" not in response["data"]
    with app.test_request_context('/api/forthright/authorization?credential_id=18'):
        g.user_id = 202
        assert inspect.unwrap(routes.forthright_authorization)()[1] == 400
    assert len(statements) == 2


def test_verified_micro_contract_chart_defaults_do_not_enable_strategy_mapping(monkeypatch):
    from app.services.market.dated_futures import dated_future_feed, future_display_metadata, is_dated_future
    monkeypatch.delenv("FORTHRIGHT_FUTURES_CONTRACT_SPECS", raising=False)
    assert dated_future_feed("MESZ26", required=True, allow_default=True) == ("yfinance", "MESZ26.CME")
    assert dated_future_feed("MNQH27", required=True, allow_default=True) == ("yfinance", "MNQH27.CME")
    with pytest.raises(ValueError):
        dated_future_feed("MESZ26", required=True)
    for symbol in ("HSIZ6", "MES=F", "MES", "6BH7"):
        with pytest.raises(ValueError):
            dated_future_feed(symbol, required=True, allow_default=True)
    assert is_dated_future("6BH7")
    assert is_dated_future("M2KZ26")
    assert not is_dated_future("BTC/USDT")
    assert future_display_metadata("MESZ26")["price_tick"] == .25
    assert future_display_metadata("HSIZ6")["quote_currency"] is None


def test_explicit_futures_mapping_takes_precedence(monkeypatch):
    from app.services.market.dated_futures import dated_future_feed
    monkeypatch.setenv("FORTHRIGHT_FUTURES_CONTRACT_SPECS", '{"MESZ26":{"data_provider":"twelvedata","data_symbol":"MESZ26"}}')
    assert dated_future_feed("MESZ26", required=True, allow_default=True) == ("twelvedata", "MESZ26")
    monkeypatch.setenv("FORTHRIGHT_FUTURES_CONTRACT_SPECS", '{"MESZ26":{"data_provider":"yfinance","data_symbol":"MES=F"}}')
    with pytest.raises(ValueError):
        dated_future_feed("MESZ26", required=True, allow_default=True)


@pytest.mark.parametrize("symbol,suffix", [("ZCU27", "CBT"), ("YMZ26", "CBT"), ("ZLH27", "CBT"),
    ("GCG27", "CMX"), ("CLF27", "NYM"), ("NGF27", "NYM"), ("6EZ26", "CME"), ("M2KZ26", "CME")])
def test_exchange_specific_dated_feeds_preserve_expiry(monkeypatch, symbol, suffix):
    from app.services.market.dated_futures import dated_future_feed
    monkeypatch.delenv("FORTHRIGHT_FUTURES_CONTRACT_SPECS", raising=False)
    assert dated_future_feed(symbol, required=True, allow_default=True) == ("yfinance", f"{symbol}.{suffix}")
    with pytest.raises(ValueError):
        dated_future_feed(symbol, required=True)


@pytest.mark.parametrize("timeframe,interval,expected", [("3m", "1m", 2), ("4H", "1h", 2)])
def test_dated_futures_resample_ohlcv_without_daily_fallback(monkeypatch, timeframe, interval, expected):
    import pandas as pd
    from app.data_sources import futures
    source = futures.FuturesDataSource.__new__(futures.FuturesDataSource)
    index = pd.date_range("2026-01-01", periods=6, freq="min" if interval == "1m" else "h", tz="UTC")
    frame = pd.DataFrame({"Open":range(1,7),"High":range(2,8),"Low":range(0,6),"Close":range(1,7),"Volume":[10]*6},index=index)
    calls = []
    ticker = SimpleNamespace(history=lambda **kwargs: calls.append(kwargs) or frame,
                             history_metadata={"symbol":"MESZ26.CME","instrumentType":"FUTURE"})
    monkeypatch.setattr(futures.yf,"Ticker",lambda symbol:ticker)
    bars = source._get_traditional_futures_yf("MESZ26.CME",timeframe,30,exact=True)
    assert calls[0]["interval"] == interval
    assert calls[0]["auto_adjust"] is False
    assert calls[0]["start"].utcoffset().total_seconds() == 0
    assert calls[0]["end"].utcoffset().total_seconds() == 0
    assert len(bars) == expected
    assert sum(bar["volume"] for bar in bars) == 60
    assert bars[0]["open"] == 1
    assert bars[-1]["close"] == 6


def test_dated_feed_wrong_contract_metadata_is_never_rendered(monkeypatch):
    import pandas as pd
    from app.data_sources import futures
    source = futures.FuturesDataSource.__new__(futures.FuturesDataSource)
    frame=pd.DataFrame({"Open":[1],"High":[2],"Low":[1],"Close":[2],"Volume":[1]},index=pd.date_range("2026-01-01",periods=1,tz="UTC"))
    monkeypatch.setattr(futures.yf,"Ticker",lambda symbol:SimpleNamespace(history=lambda **kwargs:frame,history_metadata={"symbol":"MES=F","instrumentType":"FUTURE"}))
    assert source._get_traditional_futures_yf("MESZ26.CME","1D",30,exact=True) == []


def test_micro_futures_invalid_tick_never_reaches_broker(config,monkeypatch):
    c=ForthrightClient(replace(config,account_type="futures"))
    monkeypatch.setattr(c,"_instrument",lambda *args:("MESZ26","",{}))
    monkeypatch.setattr(c,"_trade",lambda *args,**kwargs:pytest.fail("invalid price must not submit"))
    with pytest.raises(ValueError,match="invalidLimitPrice"):
        c.place_limit_order(symbol="MESZ26",market_type="Futures",side="buy",quantity=1,price=7250.1)


def test_futures_catalog_only_registers_public_unexpired_contracts(monkeypatch):
    from app.services.market.dated_futures import register_contract_catalog
    from app.services import symbol_master_sync
    captured = []
    monkeypatch.setattr(symbol_master_sync, 'upsert_symbol_master', lambda rows: captured.extend(rows) or len(rows))
    assert register_contract_catalog([
        {'prodCode': 'MESZ99', 'prodName': 'Micro S&P', 'expiryYmd': '20991218', 'marketCode': 'CME', 'account': 'must-not-copy'},
        {'prodCode': 'MESZ20', 'expiryYmd': '20201218'},
        {'prodCode': 'MES=F', 'expiryYmd': '20991218'},
        {'prodCode': 'MESM99', 'expiryYmd': '20991218', 'lastTradeDate': '2020-12-18'},
    ]) == 1
    assert captured[0].market == 'Futures'
    assert captured[0].instrument_id == 'MESZ99'
    assert captured[0].product_type == 'futures'
    assert {key: captured[0].product_meta[key] for key in ('exchange', 'expiry', 'last_trade_date')} == {'exchange': 'CME', 'expiry': '20991218', 'last_trade_date': ''}
    assert captured[0].product_meta['quantity_step'] == 1
    assert captured[0].product_meta['price_tick'] == 0.25

@pytest.mark.parametrize('quantity,side', [(2,'sell'),(-3,'buy')])
def test_close_position_rechecks_snapshot_and_offsets_exact_lots(futures, monkeypatch, quantity, side):
    monkeypatch.setattr(futures, 'get_orders', lambda **kw: [])
    monkeypatch.setattr(futures, 'get_positions', lambda: [{'symbol':'MESZ99','quantity':quantity}])
    sent=[]
    monkeypatch.setattr(futures, 'place_market_order', lambda **kw: sent.append(kw) or 'accepted')
    assert futures.close_futures_position('MESZ99', quantity) == 'accepted'
    assert sent == [{'symbol':'MESZ99','market_type':'Futures','side':side,'quantity':abs(quantity)}]

@pytest.mark.parametrize('quantity,positions,orders,error', [
    (0,[],[],'invalidOrder'), (1.5,[],[],'invalidOrder'),
    (1,[],[],'positionChanged'), (1,[{'symbol':'MESZ99','quantity':-1}],[],'positionChanged'),
    (1,[{'symbol':'MESZ99','quantity':2}],[],'positionChanged'),
    (1,[{'symbol':'MESZ99','quantity':1}],[{'id':'pending'}],'closeOrdersPending'),
])
def test_close_position_rejects_stale_or_pending_snapshot(futures,monkeypatch,quantity,positions,orders,error):
    monkeypatch.setattr(futures,'get_positions',lambda:positions)
    monkeypatch.setattr(futures,'get_orders',lambda **kw:orders)
    monkeypatch.setattr(futures,'place_market_order',lambda **kw:pytest.fail('must not submit'))
    with pytest.raises(ValueError,match=error): futures.close_futures_position('MESZ99',quantity)

def test_close_position_unknown_outcome_never_retries(futures,monkeypatch):
    monkeypatch.setattr(futures,'get_positions',lambda:[{'symbol':'MESZ99','quantity':1}])
    monkeypatch.setattr(futures,'get_orders',lambda **kw:[])
    sent=[]
    def submit(**kw):
        sent.append(kw)
        raise requests.Timeout()
    monkeypatch.setattr(futures,'place_market_order',submit)
    with pytest.raises(requests.Timeout): futures.close_futures_position('MESZ99',1)
    assert len(sent)==1

def test_close_position_rejects_strategy_units_and_securities(client,futures):
    with pytest.raises(ValueError,match='unsupportedMarket'): client.close_futures_position('MESZ99',1)
    futures.strategy_units=True
    with pytest.raises(ValueError,match='unsupportedMarket'): futures.close_futures_position('MESZ99',1)


@pytest.mark.parametrize("locked", [True, False])
def test_close_route_serializes_account_and_preserves_errors(app,monkeypatch,locked):
    import inspect
    from contextlib import nullcontext
    from app.routes import openapi_brokers as routes
    calls=[]
    cur=SimpleNamespace(execute=lambda sql,args:calls.append(args),fetchone=lambda:{"locked":locked},close=lambda:None)
    db=SimpleNamespace(cursor=lambda:cur,commit=lambda:calls.append("commit"))
    def close(symbol,quantity):
        calls.append((symbol,quantity))
        return SimpleNamespace(success=True,message="accepted",order_id="E1",status="new")
    client=SimpleNamespace(base_url="https://test.example",account_id="F1",close_futures_position=close)
    monkeypatch.setattr(routes,"_connected_client",lambda broker:(client,None))
    monkeypatch.setattr(routes,"get_db_connection",lambda:nullcontext(db))
    with app.test_request_context('/api/forthright/close-position',method='POST',json={"credential_id":19,"symbol":"MESZ99","expected_quantity":-2}):
        result=inspect.unwrap(routes.close_forthright_position)()
    assert calls[0]==("forthright-close:https://test.example","F1")
    if locked:
        assert result.get_json()["data"]["orderId"]=="E1"
        assert calls[1:]==[("MESZ99",-2),"commit"]
    else:
        assert result[0].get_json()["error"]=="brokerAccounts.forthright.closeOrdersPending"
        assert len(calls)==1

@pytest.mark.parametrize('signal,side', [('open_long','B'),('add_long','B'),('close_short','B'),('reduce_short','B'),('open_short','S'),('add_short','S'),('close_long','S'),('reduce_long','S')])
def test_strategy_signal_adapter_submits_futures_lots_with_stable_id(futures,monkeypatch,signal,side):
    from app.services.live_trading.execution import place_order_from_signal
    futures.strategy_units=True
    monkeypatch.setenv('FORTHRIGHT_FUTURES_CONTRACT_SPECS','{"MES":{"point_value":5,"currency":"USD"}}')
    calls=[]
    monkeypatch.setattr(futures,'_trade',lambda method,**body:calls.append(body) or {'extOrderId':'E1'})
    result=place_order_from_signal(futures,signal_type=signal,symbol='MESZ99',amount=10,market_type='spot',exchange_config={'market_category':'Futures'},client_order_id='stable-request')
    assert result.exchange_id=='forthright'
    assert result.exchange_order_id=='E1'
    assert calls[0]['order_qty']==2
    assert calls[0]['side']==side
    assert calls[0]['clOrderId']==futures._client_id('stable-request')
