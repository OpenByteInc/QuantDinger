import inspect
from types import SimpleNamespace
import pytest
from flask import Flask, g

from app.routes import quick_trade
from app.services.quick_trade.sizing import (
    PriceConversionError,
    convert_usdt_to_base_qty,
    resolve_order_notional_usdt,
)


class DummyClientWithoutPrice:
    pass


class DummyClientWithTicker:
    def __init__(self, price: float):
        self._price = price

    def get_ticker(self, symbol=None):
        return {"last": str(self._price)}


class DummyClientWithFailingTicker:
    def get_ticker(self, symbol=None):
        raise ConnectionError("Timeout connecting to exchange")


def test_convert_usdt_to_base_qty_success():
    client = DummyClientWithTicker(50000.0)
    qty = convert_usdt_to_base_qty(client, "BTC/USDT", 5000.0, "swap")
    assert qty == pytest.approx(0.1)


def test_convert_usdt_to_base_qty_uses_reference_price():
    client = DummyClientWithoutPrice()
    qty = convert_usdt_to_base_qty(
        client, "BTC/USDT", 6000.0, "swap", reference_price=60000.0
    )
    assert qty == pytest.approx(0.1)


def test_convert_usdt_to_base_qty_raises_price_conversion_error_on_missing_price():
    client = DummyClientWithoutPrice()
    with pytest.raises(PriceConversionError) as exc_info:
        convert_usdt_to_base_qty(client, "BTC/USDT", 5000.0, "swap")
    assert "Could not retrieve current market price" in str(exc_info.value)
    assert "BTC/USDT" in str(exc_info.value)


def test_convert_usdt_to_base_qty_raises_price_conversion_error_on_ticker_exception():
    client = DummyClientWithFailingTicker()
    with pytest.raises(PriceConversionError) as exc_info:
        convert_usdt_to_base_qty(client, "ETH/USDT", 1000.0, "swap")
    assert "ETH/USDT" in str(exc_info.value)


def test_convert_usdt_to_base_qty_zero_or_negative_amount():
    client = DummyClientWithoutPrice()
    assert convert_usdt_to_base_qty(client, "BTC/USDT", 0.0, "swap") == 0.0
    assert convert_usdt_to_base_qty(client, "BTC/USDT", -10.0, "swap") == -10.0


def test_place_order_rejects_when_price_conversion_fails(monkeypatch):
    """Verify that place_order returns 400 and does NOT submit an order when price fetch fails."""
    monkeypatch.setattr(
        quick_trade,
        "_build_product_aware_config",
        lambda *a, **k: ({"exchange_id": "binance"}, {}),
    )
    monkeypatch.setattr(quick_trade, "configure_quick_trade_derivatives", lambda *a, **k: None)
    # Client has no get_ticker and cannot return a price
    client = DummyClientWithoutPrice()
    monkeypatch.setattr(quick_trade, "create_exchange_client", lambda *a, **k: client)
    monkeypatch.setattr(quick_trade, "_recent_strategy_conflict", lambda *a, **k: None)

    submitted = []
    from app.services.live_trading import execution
    monkeypatch.setattr(
        execution,
        "place_order_from_signal",
        lambda *a, **k: submitted.append(k),
    )

    app = Flask(__name__)
    with app.test_request_context():
        g.user_id = 101
        payload = {
            "credential_id": 1,
            "symbol": "BTC/USDT",
            "side": "buy",
            "order_type": "market",
            "amount": 5000.0,
            "leverage": 1,
            "market_type": "swap",
        }
        response, status_code = inspect.unwrap(quick_trade.place_order)(payload)
        data = response.get_json()

        assert status_code == 400
        assert data["code"] == 0
        assert "Failed to retrieve current price for BTC/USDT" in data["msg"]
        assert data["error_hint"] == "quickTrade.errorHints.invalidPrice"
        assert len(submitted) == 0  # Crucial: no order was sent!
