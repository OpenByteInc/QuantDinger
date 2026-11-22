from types import SimpleNamespace

import pytest

from app.services.pending_orders.order_retry import (
    is_safe_explicit_order_retry,
    submit_with_safe_retry,
)
from app.services.pending_orders.error_classification import classify_exchange_order_error


@pytest.mark.parametrize(
    "error",
    [
        'Bybit error: {"retCode":10006,"retMsg":"Too many visits"}',
        "Bybit retCode=10019: new requests are not accepted",
    ],
)
def test_safe_retry_accepts_only_explicit_no_accept_responses(error):
    assert is_safe_explicit_order_retry(error) is True


@pytest.mark.parametrize(
    "error",
    [
        "HTTP 503 service unavailable",
        "HTTP 504 gateway timeout",
        "connection reset by peer",
        "read timed out",
        "insufficient balance",
        "invalid api key",
        "market is closed",
        'Bybit retCode=10016: Service is restarting',
        'Bybit retCode=10000: Server Timeout',
        'Binance code=-1007: Timeout waiting for response',
        'Binance code=-1006: An unexpected response was received',
        'HTTP 408 timeout',
        'HTTP 503 rate limit; execution status unknown',
        TimeoutError(),
        "service is restarting",
    ],
)
def test_safe_retry_rejects_ambiguous_or_business_failures(error):
    assert is_safe_explicit_order_retry(error) is False


def test_broker_result_retries_once_with_backoff_and_jitter(monkeypatch):
    monkeypatch.setenv("LIVE_TRADING_ORDER_SAFE_RETRIES", "1")
    monkeypatch.setenv("LIVE_TRADING_ORDER_RETRY_BACKOFF_SEC", "0.5")
    monkeypatch.setenv("LIVE_TRADING_ORDER_RETRY_JITTER_RATIO", "0.2")
    results = [
        SimpleNamespace(success=False, message="rate limit exceeded"),
        SimpleNamespace(success=True, message=""),
    ]
    delays = []
    retries = []

    result = submit_with_safe_retry(
        lambda: results.pop(0),
        result_error=lambda value: "" if value.success else value.message,
        sleep_fn=delays.append,
        uniform_fn=lambda low, high: high,
        on_retry=lambda attempt, delay, category: retries.append((attempt, delay, category)),
    )

    assert result.success is True
    assert delays == pytest.approx([0.6])
    assert retries == [(1, pytest.approx(0.6), "rate_limit")]


def test_ambiguous_exception_is_never_retried(monkeypatch):
    monkeypatch.setenv("LIVE_TRADING_ORDER_SAFE_RETRIES", "3")
    attempts = []

    def submit():
        attempts.append("submit")
        raise TimeoutError("connection timed out")

    with pytest.raises(TimeoutError):
        submit_with_safe_retry(submit, sleep_fn=lambda _delay: None)

    assert attempts == ["submit"]


def test_retry_observer_failure_does_not_abort_safe_retry(monkeypatch):
    monkeypatch.setenv("LIVE_TRADING_ORDER_SAFE_RETRIES", "1")
    monkeypatch.setenv("LIVE_TRADING_ORDER_RETRY_BACKOFF_SEC", "0")
    attempts = []

    def submit():
        attempts.append("submit")
        if len(attempts) == 1:
            raise RuntimeError("rate limit exceeded")
        return "accepted"

    def broken_observer(_attempt, _delay, _category):
        raise RuntimeError("log storage unavailable")

    assert submit_with_safe_retry(submit, on_retry=broken_observer) == "accepted"
    assert attempts == ["submit", "submit"]


@pytest.mark.parametrize(
    "error",
    [
        "HTTPSConnectionPool: Read timed out. (read timeout=15.0)",
        "Connection aborted: remote end closed connection without response",
    ],
)
def test_common_request_failures_remain_ambiguous_transport(error):
    assert classify_exchange_order_error(error)["category"] == "transport"


@pytest.mark.parametrize("order_type,bracket", [
    ("market", False), ("limit", False), ("market", True), ("limit", True),
])
def test_broker_routing_preserves_client_id_and_protection_on_safe_retry(monkeypatch, order_type, bracket):
    from unittest.mock import Mock
    from app.services.pending_orders.broker_support import submit_broker_order

    monkeypatch.setenv("LIVE_TRADING_ORDER_SAFE_RETRIES", "1")
    monkeypatch.setenv("LIVE_TRADING_ORDER_RETRY_BACKOFF_SEC", "0")
    result = SimpleNamespace(success=True, message="")
    client = Mock()
    method = client.place_bracket_order if bracket else (
        client.place_limit_order if order_type == "limit" else client.place_market_order
    )
    method.side_effect = [SimpleNamespace(success=False, message="rate limit exceeded"), result]
    protection = {"stop_loss_price": 90, "take_profit_price": 110}
    assert submit_broker_order(
        client, order_type=order_type, limit_price=100, bracket=bracket,
        protection=protection, symbol="AAPL", quantity=2, side="buy",
        client_order_id="stable-id", market_type="USStock",
    ) is result
    assert method.call_count == 2
    assert method.call_args_list[0] == method.call_args_list[1]
    kwargs = method.call_args.kwargs
    assert kwargs["client_order_id"] == "stable-id"
    assert kwargs["stop_loss_price"] == 90
    assert kwargs["take_profit_price"] == 110
    if bracket:
        assert kwargs["limit_price"] == (100 if order_type == "limit" else 0)
    elif order_type == "limit":
        assert kwargs["price"] == 100
    else:
        assert "price" not in kwargs and "limit_price" not in kwargs
