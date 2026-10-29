import base64
import hashlib
import hmac
from types import SimpleNamespace
from urllib.parse import quote

from app.services.openapi_brokers import TigerClient, TigerConfig, WebullClient, WebullConfig
from app.services.live_trading.execution import place_order_from_signal
from app.routes.openapi_brokers import _public_error


class FakeTigerTradeClient:
    def __init__(self):
        self.calls = []

    def get_assets(self, **kwargs):
        self.calls.append(("get_assets", kwargs))
        return [SimpleNamespace(summary=SimpleNamespace(
            currency="USD", net_liquidation=1200, cash=300,
            buying_power=600, available_funds=500,
        ))]

    def get_positions(self, **kwargs):
        self.calls.append(("get_positions", kwargs))
        contract = SimpleNamespace(symbol="AAPL", currency="USD", market="US", exchange="NASDAQ")
        return [SimpleNamespace(
            contract=contract, position_qty=2, quantity=2, average_cost=100,
            market_price=110, market_value=220, realized_pnl=0, unrealized_pnl=20,
            salable_qty=2,
        )]

    def place_order(self, order):
        self.calls.append(("place_order", order))
        order.id = 401001
        return order.id

    def cancel_order(self, **kwargs):
        self.calls.append(("cancel_order", kwargs))
        return int(kwargs["id"])


def fake_tiger_sdk(trade_client):
    class FakeConfig:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    def contract(symbol, currency):
        return SimpleNamespace(symbol=symbol, currency=currency, market="HK" if currency == "HKD" else "US", exchange=None)

    def order(account, security, action, order_type, quantity, limit_price=None, **kwargs):
        return SimpleNamespace(
            account=account, contract=security, action=action, order_type=order_type,
            quantity=quantity, limit_price=limit_price, filled=0, avg_fill_price=0,
            commission=None, gst=None, charges=None, status="NEW", remaining=quantity,
            id=None, order_id=None, order_time=None, update_time=None, trade_time=None,
            reason=None, user_mark="",
        )

    return {
        "Currency": SimpleNamespace(ALL="ALL"),
        "Market": SimpleNamespace(ALL="ALL"),
        "SecurityType": SimpleNamespace(STK="STK"),
        "stock_contract": contract,
        "market_order": lambda account, security, action, quantity, **kwargs: order(account, security, action, "MKT", quantity, **kwargs),
        "limit_order": lambda account, security, action, quantity, price, **kwargs: order(account, security, action, "LMT", quantity, price, **kwargs),
        "TigerOpenClientConfig": FakeConfig,
        "TradeClient": lambda config: trade_client,
    }


def test_tiger_connects_directly_and_normalizes_positions(monkeypatch):
    trade_client = FakeTigerTradeClient()
    client = TigerClient(TigerConfig(tiger_id="tiger-1", account_id="paper-1", private_key="pem", environment="paper"))
    monkeypatch.setattr(client, "_sdk", lambda: fake_tiger_sdk(trade_client))
    assert client.connect() is True
    assert client.account_id == "paper-1"
    positions = client.get_positions()
    assert positions[0]["symbol"] == "AAPL"
    assert positions[0]["unrealized_pnl"] == 20
    assert client.get_connection_status()["paper"] is True


def test_tiger_order_uses_official_sdk_order_and_user_mark(monkeypatch):
    trade_client = FakeTigerTradeClient()
    client = TigerClient(TigerConfig(tiger_id="tiger-1", account_id="account-1", private_key="pem"))
    monkeypatch.setattr(client, "_sdk", lambda: fake_tiger_sdk(trade_client))
    result = client.place_limit_order(
        symbol="3690.HK", side="buy", quantity=2, price=70.5,
        market_type="HKStock", client_order_id="qd-order-1",
    )
    assert result.order_id == "401001"
    order = trade_client.calls[-1][1]
    assert order.contract.symbol == "03690"
    assert order.contract.currency == "HKD"
    assert order.limit_price == 70.5
    assert order.user_mark == "qd-order-1"


def test_tiger_strategy_execution_keeps_stock_symbol(monkeypatch):
    trade_client = FakeTigerTradeClient()
    client = TigerClient(TigerConfig(tiger_id="tiger-1", account_id="account-1", private_key="pem"))
    monkeypatch.setattr(client, "_sdk", lambda: fake_tiger_sdk(trade_client))
    result = place_order_from_signal(
        client,
        signal_type="open_long",
        symbol="USStock:AAPL",
        amount=3,
        market_type="spot",
        exchange_config={"market_category": "USStock"},
        client_order_id="strategy-order-1",
    )
    assert result.exchange_order_id == "401001"
    assert result.exchange_id == "tiger"
    order = trade_client.calls[-1][1]
    assert order.contract.symbol == "AAPL"
    assert order.user_mark == "strategy-order-1"


def test_webull_strategy_execution_keeps_stock_symbol(monkeypatch):
    client = WebullClient(WebullConfig(app_key="key", app_secret="secret", account_id="A-1"))
    captured = {}

    def fake_request(method, path, **kwargs):
        captured.update(method=method, path=path, **kwargs)
        return {
            "orders": [{
                "client_order_id": "strategy-order-2",
                "order_id": "server-2",
                "status": "SUBMITTED",
            }]
        }

    monkeypatch.setattr(client, "_request", fake_request)
    result = place_order_from_signal(
        client,
        signal_type="open_long",
        symbol="USStock:AAPL",
        amount=3,
        market_type="spot",
        exchange_config={"market_category": "USStock"},
        client_order_id="strategy-order-2",
    )
    assert result.exchange_id == "webull"
    assert result.exchange_order_id == "strategy-order-2"
    assert captured["body"]["new_orders"][0]["symbol"] == "AAPL"


def test_webull_signature_matches_hmac_sha1_contract():
    client = WebullClient(WebullConfig(app_key="app-key", app_secret="secret", environment="sandbox"))
    params = {"account_id": "123", "page_size": 20}
    timestamp = "2026-10-29T01:02:03Z"
    nonce = "nonce-1"
    body = '{"hello":"world"}'
    actual = client.build_signature("POST", "/trading/orders/place", params, body, timestamp, nonce)
    signing = {
        "account_id": "123", "page_size": "20", "host": "api.sandbox.webull.com",
        "x-app-key": "app-key", "x-signature-algorithm": "HMAC-SHA1",
        "x-signature-nonce": nonce, "x-signature-version": "1.0", "x-timestamp": timestamp,
    }
    canonical = "&".join(f"{key}={signing[key]}" for key in sorted(signing))
    source = f"/trading/orders/place&{canonical}&{hashlib.md5(body.encode()).hexdigest().upper()}"
    expected = base64.b64encode(
        hmac.new(b"secret&", quote(source, safe="").encode("utf-8"), hashlib.sha1).digest()
    ).decode("ascii")
    assert actual == expected


def test_public_broker_error_only_returns_allowlisted_keys():
    assert _public_error(
        ValueError("brokerAccounts.invalidOrder"), "brokerAccounts.orderFailed"
    ) == "brokerAccounts.invalidOrder"
    assert _public_error(
        RuntimeError("upstream secret: abc123"), "brokerAccounts.orderFailed"
    ) == "brokerAccounts.orderFailed"


def test_webull_sandbox_connect_and_current_balance_fields(monkeypatch):
    client = WebullClient(WebullConfig(app_key="key", app_secret="secret", environment="sandbox"))

    def fake_request(method, path, **kwargs):
        if path == "/trading/accounts/list":
            return {"accounts": [{"account_id": "A-1"}]}
        return {"total_net_liquidation_value": "1200.50", "total_cash_balance": "300.25", "margin_excess": "600.00"}

    monkeypatch.setattr(client, "_request", fake_request)
    assert client.connect() is True
    summary = client.get_account_summary()
    assert summary["equity"] == "1200.50"
    assert summary["cash"] == "300.25"
    assert summary["buying_power"] == "600.00"


def test_webull_order_detail_exposes_actual_commission(monkeypatch):
    client = WebullClient(WebullConfig(app_key="key", app_secret="secret", account_id="A-1"))
    monkeypatch.setattr(client, "_request", lambda *args, **kwargs: {
        "client_order_id": "client-1", "order_id": "server-1", "status": "FILLED",
        "filled_quantity": "2", "filled_price": "101.5",
        "commission": {"actual_commission": "0.35"},
        "fees": [{"currency": "USD", "actual_value": "0.35"}],
    })
    result = client.get_order_status_by_client_id("client-1")
    assert result.status == "filled"
    assert result.filled == 2
    assert result.avg_price == 101.5
    assert result.raw["fee_breakdown"] == {"USD": 0.35}
    assert result.raw["fee_status"] == "actual"


def test_webull_filled_orders_filter_symbol_and_status(monkeypatch):
    client = WebullClient(WebullConfig(app_key="key", app_secret="secret", account_id="A-1"))
    monkeypatch.setattr(client, "_request", lambda *args, **kwargs: {
        "orders": [
            {"client_order_id": "1", "symbol": "AAPL", "status": "FILLED", "filled_price": "101"},
            {"client_order_id": "2", "symbol": "MSFT", "status": "FILLED", "filled_price": "400"},
            {"client_order_id": "3", "symbol": "AAPL", "status": "SUBMITTED"},
        ]
    })
    rows = client.get_orders(status="filled", symbol="AAPL")
    assert [row["id"] for row in rows] == ["1"]
    assert rows[0]["filled_avg_price"] == "101"


def test_webull_place_persists_client_id_for_status_and_cancel(monkeypatch):
    client = WebullClient(WebullConfig(app_key="key", app_secret="secret", account_id="A-1"))
    captured = {}

    def fake_request(method, path, **kwargs):
        captured.update(method=method, path=path, **kwargs)
        return {
            "orders": [{
                "client_order_id": "client-1",
                "order_id": "server-1",
                "status": "SUBMITTED",
            }]
        }

    monkeypatch.setattr(client, "_request", fake_request)
    result = client.place_limit_order(
        symbol="AAPL", side="BUY", quantity=2, price=101.5,
        market_type="USStock", client_order_id="client-1",
    )
    assert result.order_id == "client-1"
    assert result.raw["broker_order_id"] == "server-1"
    assert captured["body"]["new_orders"][0]["client_order_id"] == "client-1"


def test_webull_cancel_uses_client_order_id(monkeypatch):
    client = WebullClient(WebullConfig(app_key="key", app_secret="secret", account_id="A-1"))
    captured = {}
    monkeypatch.setattr(client, "_request", lambda method, path, **kwargs: captured.update(method=method, path=path, **kwargs) or {})
    assert client.cancel_order("client-order-123") is True
    assert captured["body"] == {"account_id": "A-1", "client_order_id": "client-order-123"}


def test_openapi_broker_routes_are_registered(app):
    rules = {rule.rule for rule in app.url_map.iter_rules()}
    assert "/api/<broker>/connect" in rules
    assert "/api/<broker>/positions" in rules
    assert "/api/<broker>/order/<order_id>" in rules
