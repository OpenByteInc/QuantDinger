import inspect
from contextlib import contextmanager

from flask import Flask, g

from app.routes import quick_trade
from app.services.live_trading import fee_quote
from app.services.pending_orders import live_order_phases


class _Cursor:
    def __init__(self, row=None):
        self.row = row
        self.query = ""
        self.params = ()

    def execute(self, query, params):
        self.query = query
        self.params = params

    def fetchone(self):
        return self.row

    def close(self):
        return None


class _Db:
    def __init__(self, cursor):
        self.cursor_value = cursor
        self.committed = False

    def cursor(self):
        return self.cursor_value

    def commit(self):
        self.committed = True


def test_cancel_limit_order_uses_owned_record_and_reconciles_fill(monkeypatch):
    select_cursor = _Cursor({
        "id": 41,
        "user_id": 99,
        "credential_id": 305,
        "exchange_id": "okx",
        "symbol": "BTC/USDT",
        "order_type": "limit",
        "market_type": "swap",
        "status": "submitted",
        "exchange_order_id": "order-41",
        "filled_amount": 0,
        "avg_fill_price": 0,
        "commission": 0,
        "commission_ccy": "",
        "commission_quote": None,
        "raw_result": {"_quick_trade": {"requested_base_qty": 0.01, "client_order_id": "client-41"}},
    })
    update_cursor = _Cursor()
    databases = [_Db(select_cursor), _Db(update_cursor)]

    @contextmanager
    def fake_connection():
        yield databases.pop(0)

    client = object()
    captured = {}

    def fake_cancel(**kwargs):
        captured.update(kwargs)
        return {"status": "cancelled"}

    monkeypatch.setattr(quick_trade, "get_db_connection", fake_connection)
    monkeypatch.setattr(
        quick_trade,
        "_build_product_aware_config",
        lambda *args, **kwargs: (
            {"exchange_id": "okx", "market_type": "swap"},
            {"product_type": "crypto"},
        ),
    )
    monkeypatch.setattr(quick_trade, "create_exchange_client", lambda *args, **kwargs: client)
    monkeypatch.setattr(live_order_phases, "cancel_live_limit_order", fake_cancel)
    monkeypatch.setattr(
        quick_trade,
        "enrich_fill",
        lambda *args, **kwargs: {
            "filled": 0.002,
            "avg_price": 75000,
            "fee": 0.03,
            "fee_ccy": "USDT",
            "status": "cancelled",
        },
    )
    monkeypatch.setattr(fee_quote, "fee_to_quote", lambda *args, **kwargs: 0.03)

    app = Flask(__name__)
    handler = inspect.unwrap(quick_trade.cancel_order)
    with app.test_request_context("/api/quick-trade/cancel-order", method="POST"):
        g.user_id = 99
        response = handler({"trade_id": 41})

    payload = response.get_json()
    assert payload["code"] == 1
    assert payload["data"]["status"] == "cancelled"
    assert payload["data"]["filled_amount"] == 0.002
    assert captured["client"] is client
    assert captured["order_id"] == "order-41"
    assert captured["client_order_id"] == "client-41"
    assert select_cursor.params == (41, 99)
    assert "status = %s" in update_cursor.query
    assert update_cursor.params[0] == "cancelled"
    assert update_cursor.params[-2:] == (41, 99)
