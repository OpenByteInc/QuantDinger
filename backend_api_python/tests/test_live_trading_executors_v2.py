from __future__ import annotations

import pytest

from app.services.live_trading.base import LiveOrderResult, LiveTradingError
from app.services.live_trading.contracts import FillSnapshot, OrderIntent
from app.services.live_trading.executors import (
    LimitThenMarketExecutor,
    MarketOrderExecutor,
    RestingLimitExecutor,
)
from app.services.live_trading.ws_order_transport import WebSocketOrderUnknown


class FakeAdapter:
    exchange_id = "fake"

    def __init__(self, *, fill_status: str = "filled", fill_qty: float = 1.0):
        self.calls = []
        self.fill_status = fill_status
        self.fill_qty = fill_qty

    def place_market_order(self, intent):
        self.calls.append(("market", intent.symbol))
        return LiveOrderResult(exchange_id=self.exchange_id, exchange_order_id="m1", filled=1.0, avg_price=101, raw={})

    def place_limit_order(self, intent):
        self.calls.append(("limit", intent.symbol))
        return LiveOrderResult(exchange_id=self.exchange_id, exchange_order_id="l1", filled=0.0, avg_price=0, raw={})

    def cancel_order(self, intent, *, order_id: str = ""):
        self.calls.append(("cancel", order_id))
        self.fill_status = "canceled"
        return {"ok": True}

    def wait_for_fill(self, intent, *, order_id: str = "", max_wait_sec: float = 15.0):
        self.calls.append(("wait", order_id, max_wait_sec))
        if order_id == "m1":
            return FillSnapshot(
                filled_qty=float(intent.quantity or 0.0),
                avg_price=101,
                status="filled",
                raw={},
                fees_by_ccy={"USDT": 0.2},
            )
        return FillSnapshot(
            filled_qty=self.fill_qty,
            avg_price=100,
            status=self.fill_status,
            raw={},
            fees_by_ccy={"USDT": 0.1} if self.fill_qty > 0 else {},
        )

    def query_position(self, intent):
        raise NotImplementedError


def test_market_order_executor_submits_normalized_intent():
    adapter = FakeAdapter()
    intent = OrderIntent(symbol="BTC/USDT", side="buy", quantity=1)

    result = MarketOrderExecutor(adapter).execute(intent)

    assert result.success is True
    assert result.status == "filled"
    assert result.exchange_order_id == "m1"
    assert result.fees_by_ccy == {"USDT": 0.2}
    assert adapter.calls == [("market", "BTC/USDT"), ("wait", "m1", 12.0)]


def test_market_order_transport_failure_is_left_for_reconciliation():
    adapter = FakeAdapter()
    attempts = []

    def timeout(_intent):
        attempts.append("submit")
        raise LiveTradingError("HTTP 504 gateway timeout")

    adapter.place_market_order = timeout

    result = MarketOrderExecutor(adapter).execute(
        OrderIntent(
            symbol="BTC/USDT",
            side="buy",
            quantity=1,
            client_order_id="qd_7_11_mkt",
        )
    )

    assert result.success is False
    assert result.status == "unknown"
    assert result.raw == {
        "submit_outcome": "unknown",
        "client_order_id": "qd_7_11_mkt",
    }
    assert attempts == ["submit"]


@pytest.mark.parametrize(
    ("executor", "place_method", "price"),
    [
        (MarketOrderExecutor, "place_market_order", 0),
        (RestingLimitExecutor, "place_limit_order", 90000),
        (LimitThenMarketExecutor, "place_limit_order", 90000),
    ],
)
def test_explicit_rate_limit_retries_once_with_same_intent(
    monkeypatch,
    executor,
    place_method,
    price,
):
    monkeypatch.setenv("LIVE_TRADING_ORDER_SAFE_RETRIES", "1")
    monkeypatch.setenv("LIVE_TRADING_ORDER_RETRY_BACKOFF_SEC", "0")
    adapter = FakeAdapter()
    original = getattr(adapter, place_method)
    submitted = []

    def rate_limited_once(intent):
        submitted.append(intent)
        if len(submitted) == 1:
            raise LiveTradingError('Bybit error: {"retCode":10006,"retMsg":"Too many visits"}')
        return original(intent)

    setattr(adapter, place_method, rate_limited_once)
    intent = OrderIntent(
        symbol="BTC/USDT",
        side="buy",
        quantity=1,
        price=price,
        client_order_id="qd_safe_retry",
    )

    result = executor(adapter).execute(intent)

    assert result.success is True
    assert submitted == [intent, intent]


def test_explicit_business_rejection_is_not_retried(monkeypatch):
    monkeypatch.setenv("LIVE_TRADING_ORDER_SAFE_RETRIES", "3")
    monkeypatch.setenv("LIVE_TRADING_ORDER_RETRY_BACKOFF_SEC", "0")
    adapter = FakeAdapter()
    attempts = []

    def rejected(_intent):
        attempts.append("submit")
        raise LiveTradingError("insufficient balance")

    adapter.place_market_order = rejected
    result = MarketOrderExecutor(adapter).execute(
        OrderIntent(symbol="BTC/USDT", side="buy", quantity=1)
    )

    assert result.success is False
    assert result.status == "rejected"
    assert attempts == ["submit"]


@pytest.mark.parametrize(
    ("executor", "place_method", "price"),
    [
        (MarketOrderExecutor, "place_market_order", 0),
        (RestingLimitExecutor, "place_limit_order", 90000),
        (LimitThenMarketExecutor, "place_limit_order", 90000),
    ],
)
def test_websocket_sent_without_ack_is_left_for_reconciliation(
    executor,
    place_method,
    price,
):
    adapter = FakeAdapter()

    def unknown(_intent):
        raise WebSocketOrderUnknown("order frame sent without an acknowledgement")

    setattr(adapter, place_method, unknown)
    result = executor(adapter).execute(
        OrderIntent(
            symbol="BTC/USDT",
            side="buy",
            quantity=1,
            price=price,
            client_order_id="qd_ws_unknown",
        )
    )

    assert result.success is False
    assert result.status == "unknown"
    assert result.raw == {
        "submit_outcome": "unknown",
        "client_order_id": "qd_ws_unknown",
    }


def test_limit_then_market_transport_failure_is_left_for_reconciliation():
    adapter = FakeAdapter()

    def timeout(_intent):
        raise LiveTradingError("HTTP 504 gateway timeout")

    adapter.place_limit_order = timeout

    result = LimitThenMarketExecutor(adapter).execute(
        OrderIntent(
            symbol="BTC/USDT",
            side="buy",
            quantity=1,
            price=90000,
            client_order_id="qd_7_12_lmt",
        )
    )

    assert result.success is False
    assert result.status == "unknown"
    assert result.raw["client_order_id"] == "qd_7_12_lmt"


def test_limit_then_market_persists_each_client_id_before_submit():
    prepared = []
    adapter = FakeAdapter(fill_status="open", fill_qty=0)
    original_limit = adapter.place_limit_order
    original_market = adapter.place_market_order

    def prepare_then_limit(intent):
        prepared.append(intent.client_order_id)
        return original_limit(intent)

    def prepare_then_market(intent):
        prepared.append(intent.client_order_id)
        return original_market(intent)

    adapter.place_limit_order = prepare_then_limit
    adapter.place_market_order = prepare_then_market
    result = LimitThenMarketExecutor(adapter, max_wait_sec=1).execute(
        OrderIntent(
            symbol="BTC/USDT",
            side="buy",
            quantity=1,
            price=90000,
            client_order_id="qd_7_13_lmt",
            fallback_client_order_id="qd_7_13_mkt",
        )
    )

    assert result.success is True
    assert prepared == ["qd_7_13_lmt", "qd_7_13_mkt"]


def test_resting_limit_executor_submits_without_waiting_or_cancelling():
    adapter = FakeAdapter(fill_status="open", fill_qty=0)
    intent = OrderIntent(symbol="BTC/USDT", side="buy", quantity=1, price=90000)

    result = RestingLimitExecutor(adapter).execute(intent)

    assert result.success is True
    assert result.status == "submitted"
    assert result.exchange_order_id == "l1"
    assert result.fees_by_ccy == {}
    assert adapter.calls == [("limit", "BTC/USDT")]


def test_limit_then_market_returns_limit_fill_when_complete():
    adapter = FakeAdapter(fill_status="filled", fill_qty=2)
    intent = OrderIntent(symbol="ETH/USDT", side="buy", quantity=2, price=100)

    result = LimitThenMarketExecutor(adapter, max_wait_sec=3).execute(intent)

    assert result.success is True
    assert result.status == "filled"
    assert result.exchange_order_id == "l1"
    assert adapter.calls == [("limit", "ETH/USDT"), ("wait", "l1", 3.0)]


def test_limit_then_market_cancels_and_falls_back_when_unfilled():
    adapter = FakeAdapter(fill_status="open", fill_qty=0)
    intent = OrderIntent(symbol="SOL/USDT", side="sell", quantity=3, price=100)

    result = LimitThenMarketExecutor(adapter, max_wait_sec=1, fallback_to_market=True).execute(intent)

    assert result.success is True
    assert result.exchange_order_id == "m1"
    assert result.fees_by_ccy == {"USDT": 0.2}
    assert adapter.calls == [
        ("limit", "SOL/USDT"),
        ("wait", "l1", 1.0),
        ("cancel", "l1"),
        ("wait", "l1", 1.0),
        ("market", "SOL/USDT"),
        ("wait", "m1", 12.0),
    ]


def test_limit_then_market_preserves_partial_limit_fill_and_markets_remaining():
    adapter = FakeAdapter(fill_status="open", fill_qty=1)
    intent = OrderIntent(symbol="SOL/USDT", side="sell", quantity=3, price=100)

    result = LimitThenMarketExecutor(adapter, max_wait_sec=1, fallback_to_market=True).execute(intent)

    assert result.success is True
    assert result.filled_qty == 3
    assert result.avg_price == 100.66666666666667
    assert result.fees_by_ccy == pytest.approx({"USDT": 0.3})
    assert adapter.calls == [
        ("limit", "SOL/USDT"),
        ("wait", "l1", 1.0),
        ("cancel", "l1"),
        ("wait", "l1", 1.0),
        ("market", "SOL/USDT"),
        ("wait", "m1", 12.0),
    ]


def test_limit_then_market_falls_back_after_price_band_rejection():
    adapter = FakeAdapter()

    def reject_limit(intent):
        adapter.calls.append(("limit", intent.symbol))
        raise LiveTradingError(
            "Bybit error: {'retCode': 110003, 'retMsg': 'Order price exceeds the allowable range.'}"
        )

    adapter.place_limit_order = reject_limit
    intent = OrderIntent(
        symbol="ETH/USDT",
        side="buy",
        quantity=2,
        price=2800,
        client_order_id="limit-id",
        fallback_client_order_id="market-id",
    )

    result = LimitThenMarketExecutor(adapter, fallback_to_market=True).execute(intent)

    assert result.success is True
    assert result.exchange_order_id == "m1"
    assert "110003" in result.raw["limit_error"]
    assert adapter.calls == [
        ("limit", "ETH/USDT"),
        ("market", "ETH/USDT"),
        ("wait", "m1", 12.0),
    ]
