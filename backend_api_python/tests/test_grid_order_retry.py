from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.grid import exchange_orders
from app.services.live_trading.base import LiveOrderResult, LiveTradingError


def _result(order_id: str = "exchange-1") -> LiveOrderResult:
    return LiveOrderResult(
        exchange_id="bybit",
        exchange_order_id=order_id,
        filled=0.0,
        avg_price=0.0,
        raw={},
    )


def test_grid_limit_retries_explicit_rate_limit_with_same_client_id(monkeypatch):
    monkeypatch.setenv("LIVE_TRADING_ORDER_SAFE_RETRIES", "1")
    monkeypatch.setenv("LIVE_TRADING_ORDER_RETRY_BACKOFF_SEC", "0")

    class FakeBybit:
        def __init__(self):
            self.client_ids = []

        def place_limit_order(self, **kwargs):
            self.client_ids.append(kwargs.get("client_order_id"))
            if len(self.client_ids) == 1:
                raise LiveTradingError("Bybit retCode=10006: Too many visits")
            return _result()

    monkeypatch.setattr(exchange_orders, "BybitClient", FakeBybit)
    client = FakeBybit()

    result = exchange_orders.place_grid_limit_order(
        client,
        symbol="BTC/USDT",
        side="buy",
        quantity=0.001,
        price=70000.0,
        market_type="swap",
        exchange_config={"exchange_id": "bybit"},
        client_order_id="grid-stable-id",
    )

    assert result.exchange_order_id == "exchange-1"
    assert client.client_ids == ["grid-stable-id", "grid-stable-id"]


def test_grid_limit_does_not_retry_ambiguous_timeout(monkeypatch):
    monkeypatch.setenv("LIVE_TRADING_ORDER_SAFE_RETRIES", "3")

    class FakeBybit:
        def __init__(self):
            self.attempts = 0

        def place_limit_order(self, **_kwargs):
            self.attempts += 1
            raise LiveTradingError("HTTPSConnectionPool: Read timed out")

    monkeypatch.setattr(exchange_orders, "BybitClient", FakeBybit)
    client = FakeBybit()

    with pytest.raises(LiveTradingError, match="Read timed out"):
        exchange_orders.place_grid_limit_order(
            client,
            symbol="BTC/USDT",
            side="buy",
            quantity=0.001,
            price=70000.0,
            market_type="swap",
            exchange_config={"exchange_id": "bybit"},
            client_order_id="grid-stable-id",
        )

    assert client.attempts == 1


def test_grid_market_retries_explicit_rejection_with_same_client_id(monkeypatch):
    monkeypatch.setenv("LIVE_TRADING_ORDER_SAFE_RETRIES", "1")
    monkeypatch.setenv("LIVE_TRADING_ORDER_RETRY_BACKOFF_SEC", "0")
    client_ids = []

    def place_order_from_signal(_client, **kwargs):
        client_ids.append(kwargs.get("client_order_id"))
        if len(client_ids) == 1:
            raise LiveTradingError("Bybit retCode=10019: new requests are not accepted")
        return _result("exchange-market-1")

    monkeypatch.setattr(
        "app.services.live_trading.execution.place_order_from_signal",
        place_order_from_signal,
    )
    monkeypatch.setattr(
        exchange_orders,
        "wait_grid_market_fill",
        lambda *_args, **_kwargs: (0.001, 70000.0),
    )

    result = exchange_orders.execute_grid_market_order(
        SimpleNamespace(),
        symbol="BTC/USDT",
        signal_type="open_long",
        quantity=0.001,
        market_type="swap",
        exchange_config={"exchange_id": "bybit"},
        client_order_id="grid-market-stable-id",
    )

    assert result.ok is True
    assert result.exchange_order_id == "exchange-market-1"
    assert client_ids == ["grid-market-stable-id", "grid-market-stable-id"]
