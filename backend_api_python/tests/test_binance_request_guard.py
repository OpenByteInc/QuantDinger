import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest

from app.utils import binance_request_guard as module
from app.utils.exchange_request_budget import _EXTEND
from app.utils.binance_request_guard import BinanceRateLimitError, BinanceRequestGuard

LIVE = "https://fapi.binance.com/fapi/v1/premiumIndex"
SPOT = "https://api.binance.com/api/v3/order"


@pytest.fixture
def guard(monkeypatch):
    instance = BinanceRequestGuard()
    instance._redis_url = ""
    monkeypatch.setenv("BINANCE_REST_READS_PER_SECOND", "5")
    monkeypatch.setenv("BINANCE_REST_WEIGHT_SOFT_LIMIT", "1800")
    return instance


def test_ban_stops_public_private_and_writes_but_not_other_venues(guard):
    with pytest.raises(BinanceRateLimitError, match="HTTP 418"):
        guard.observe(LIVE, 418, {"Retry-After": "120"})
    for url, method in [(LIVE, "GET"), (SPOT, "GET"), (SPOT, "POST"), (SPOT, "DELETE")]:
        with pytest.raises(BinanceRateLimitError):
            guard.before_request(url, method)
    guard.before_request("https://www.okx.com/api/v5/account/positions")
    guard.before_request("https://demo-fapi.binance.com/fapi/v1/order", "POST")
    guard.before_request("https://api.binance.com.evil.example/order", "POST")


def test_longer_server_ban_wins_and_shorter_responses_never_shorten_it(guard, monkeypatch):
    monkeypatch.setattr(module.time, "time", lambda: 1800000000.0)
    monkeypatch.setattr(module.time, "monotonic", lambda: 100.0)
    with pytest.raises(BinanceRateLimitError) as exc:
        guard.observe(LIVE, 418, {"retry-after": "60"}, "IP banned until 1800003600000.")
    assert exc.value.retry_after == 3601
    with pytest.raises(BinanceRateLimitError):
        guard.observe(LIVE, 429, {"retry-after": "10"})
    assert guard._reserve("live", "write", 5) == (3601, True)
    monkeypatch.setattr(module.time, "monotonic", lambda: 3702.0)
    guard.before_request(LIVE)


def test_retry_after_http_date_and_default_cooldown(guard, monkeypatch):
    monkeypatch.setattr(module.time, "time", lambda: 0.0)
    with pytest.raises(BinanceRateLimitError) as exc:
        guard.observe(LIVE, 429, {"Retry-After": "Thu, 01 Jan 1970 00:02:00 GMT"})
    assert exc.value.retry_after == 121
    with pytest.raises(BinanceRateLimitError) as exc:
        guard.observe(LIVE, 418, {})
    assert exc.value.retry_after == 121


def test_read_budget_reserves_account_capacity_and_never_retries_writes(guard):
    for _ in range(5):
        assert guard._reserve("live", "background", 5) == (0, False)
    assert guard._reserve("live", "background", 5)[0] > 0
    assert guard._reserve("live", "account", 5) == (0, False)
    guard.before_request(SPOT, "DELETE")
    guard.before_request(SPOT, "POST")


def test_header_pressure_defers_reads_but_preserves_successful_order_response(guard, monkeypatch):
    def unavailable(*_args):
        raise BinanceRateLimitError(5, "shared coordinator unavailable")

    monkeypatch.setattr(guard, "_eval", unavailable)
    guard.observe(SPOT, 200, {"X-MBX-USED-WEIGHT-1M": "1900"})
    with pytest.raises(BinanceRateLimitError):
        guard.before_request(LIVE)
    assert guard._deadlines[("live", "pressure")] > module.time.monotonic()


def test_shared_store_failure_never_becomes_unlimited_local_fallback(guard):
    guard._redis = Mock()
    guard._redis.eval.side_effect = ConnectionError("offline")
    for _ in range(3):
        with pytest.raises(BinanceRateLimitError, match="coordinator unavailable"):
            guard.before_request(LIVE)
    assert guard._redis.eval.call_count == 1


def test_http_clients_share_cooldown_without_making_another_http_call(guard, monkeypatch, caplog):
    from app.data_sources import native_crypto
    from app.services.live_trading import base

    monkeypatch.setenv("BINANCE_REST_TRACE_ENABLED", "1")
    caplog.set_level("INFO", logger=module.__name__)

    monkeypatch.setattr(native_crypto, "binance_request_guard", guard)
    monkeypatch.setattr(base, "binance_request_guard", guard)
    response = Mock(status_code=418, headers={"Retry-After": "600"}, text="banned")
    public = native_crypto.NativeCryptoPublicClient("binance")
    session = Mock()
    session.get.return_value = response
    monkeypatch.setattr(public, "_http_session", lambda: session)
    with pytest.raises(BinanceRateLimitError):
        public._get(LIVE)
    private = base.BaseRestClient("https://api.binance.com")
    private._request_session = Mock()
    with pytest.raises(BinanceRateLimitError):
        private._request("DELETE", "/api/v3/order")
    session.get.assert_called_once()
    response.close.assert_called_once()
    private._request_session.request.assert_not_called()
    traces = [record.message for record in caplog.records if "Binance REST trace" in record.message]
    assert sum("event=start " in item for item in traces) == 1
    assert sum("event=response " in item and "status=418 " in item for item in traces) == 1
    assert sum("event=blocked " in item for item in traces) == 1


def test_trace_records_weight_without_credentials_or_query(monkeypatch, caplog):
    monkeypatch.setenv("BINANCE_REST_TRACE_ENABLED", "1")
    caplog.set_level("INFO", logger=module.__name__)
    response = Mock(status_code=200, headers={"X-MBX-USED-WEIGHT-1M": "1720", "secret": "private-header"})
    request = Mock(return_value=response)
    url = "https://private-user:private-pass@fapi.binance.com/fapi/v1/userTrades?signature=private-signature"
    assert module.traced_binance_request(request, url) is response
    request.assert_called_once_with()
    assert "event=start " in caplog.text
    assert "event=response " in caplog.text
    assert "used_weight_1m=1720" in caplog.text
    assert "private-" not in caplog.text
    assert "signature" not in caplog.text


def test_trace_never_retries_transport_failure(monkeypatch, caplog):
    monkeypatch.setenv("BINANCE_REST_TRACE_ENABLED", "1")
    caplog.set_level("INFO", logger=module.__name__)
    request = Mock(side_effect=TimeoutError("private-signature"))
    with pytest.raises(TimeoutError):
        module.traced_binance_request(request, SPOT, "POST")
    request.assert_called_once_with()
    assert "event=transport_error " in caplog.text
    assert "event=response " not in caplog.text
    assert "private-signature" not in caplog.text


def test_trace_logging_failure_preserves_success(monkeypatch):
    monkeypatch.setenv("BINANCE_REST_TRACE_ENABLED", "1")
    monkeypatch.setattr(module.logger, "info", Mock(side_effect=RuntimeError("log failure")))
    response = Mock(status_code=200, headers={})
    request = Mock(return_value=response)
    assert module.traced_binance_request(request, SPOT, "POST") is response
    request.assert_called_once_with()


@pytest.mark.parametrize("enabled,url", [("0", LIVE), ("1", "https://api.alpaca.markets/v2/orders")])
def test_trace_disabled_or_other_broker_has_no_logs(enabled, url, monkeypatch, caplog):
    monkeypatch.setenv("BINANCE_REST_TRACE_ENABLED", enabled)
    caplog.set_level("INFO", logger=module.__name__)
    request = Mock(return_value=object())
    assert module.traced_binance_request(request, url) is request.return_value
    request.assert_called_once_with()
    assert "Binance REST trace" not in caplog.text


def test_private_client_trace_records_one_http_call(guard, monkeypatch, caplog):
    import requests
    from app.services.live_trading import base

    monkeypatch.setattr(base, "binance_request_guard", guard)
    monkeypatch.setenv("BINANCE_REST_TRACE_ENABLED", "1")
    caplog.set_level("INFO", logger=module.__name__)
    response = requests.Response()
    response.status_code = 200
    response._content = b'{"orderId":123}'
    response.headers["X-MBX-USED-WEIGHT-1M"] = "15"
    client = base.BaseRestClient("https://api.binance.com")
    client._request_session = Mock()
    client._request_session.request.return_value = response
    assert client._request("POST", "/api/v3/order")[1] == {"orderId": 123}
    client._request_session.request.assert_called_once()
    assert caplog.text.count("event=start ") == 1
    assert caplog.text.count("event=response ") == 1
    assert "used_weight_1m=15" in caplog.text


def test_accepted_write_is_returned_even_if_weight_publication_fails(guard, monkeypatch):
    from app.services.live_trading import base

    def evaluate(script, *_args):
        if script == _EXTEND:
            raise BinanceRateLimitError(5, "shared coordinator unavailable")
        return [0, 0]

    monkeypatch.setattr(guard, "_eval", evaluate)
    monkeypatch.setattr(base, "binance_request_guard", guard)
    client = base.BaseRestClient("https://api.binance.com")
    client._request_session = Mock()
    response = Mock(status_code=200, text='{"orderId":123}', headers={"X-MBX-USED-WEIGHT-1M": "1900"})
    response.json.return_value = {"orderId": 123}
    context = Mock(__enter__=Mock(return_value=response), __exit__=Mock(return_value=False))
    client._request_session.request.return_value = context
    assert client._request("POST", "/api/v3/order") == (200, {"orderId": 123}, '{"orderId":123}')
    client._request_session.request.assert_called_once()


def test_ancillary_requests_use_same_guard_and_other_hosts_pass_through(guard, monkeypatch):
    monkeypatch.setattr(module, "binance_request_guard", guard)
    request = Mock(return_value=Mock(status_code=429, headers={}, text="limited"))
    with pytest.raises(BinanceRateLimitError):
        module.guarded_binance_request(request, LIVE)
    with pytest.raises(BinanceRateLimitError):
        module.guarded_binance_request(request, "https://data-api.binance.vision/api/v3/exchangeInfo")
    request.assert_called_once()
    module.guarded_binance_request(request, "https://data.alpaca.markets/v2/stocks")
    assert request.call_count == 2


@pytest.mark.parametrize("client_name,method", [("BinanceFuturesClient", "get_user_trades"), ("BinanceSpotClient", "get_my_trades")])
def test_fee_cooldown_returns_pending_without_three_attempts(client_name, method, monkeypatch):
    from app.services.live_trading.binance import BinanceFuturesClient
    from app.services.live_trading.binance_spot import BinanceSpotClient

    cls = {"BinanceFuturesClient": BinanceFuturesClient, "BinanceSpotClient": BinanceSpotClient}[client_name]
    client = cls(api_key="k", secret_key="s")
    request = Mock(side_effect=BinanceRateLimitError(600))
    monkeypatch.setattr(client, method, request)
    result = client._fetch_commission_for_order(symbol="BTC/USDT", order_id="1", filled=1, avg_price=100, max_attempts=3)
    assert result == (0.0, "", {})
    assert client._fee_status(result[2]) == "pending"
    request.assert_called_once()


@pytest.mark.parametrize("historical", [False, True])
def test_kline_rate_limit_does_not_retry_or_misclassify_symbol(historical, monkeypatch):
    from app.data_sources.crypto import CryptoDataSource

    source = object.__new__(CryptoDataSource)
    source.exchange = Mock(id="binance", enableRateLimit=True)
    source.exchange.fetch_ohlcv.side_effect = BinanceRateLimitError(600)
    fallback = Mock()
    monkeypatch.setattr(source, "_fetch_ohlcv_fallback", fallback)
    before = int(module.time.time()) - 3600 if historical else None
    result = source._fetch_ohlcv("BTC/USDT", "1m", 10, before, "1m")
    assert result == []
    source.exchange.fetch_ohlcv.assert_called_once()
    fallback.assert_not_called()


@pytest.mark.parametrize("mark_price", [123.0, 0.0])
def test_binance_execution_prices_only_load_standard_quote_if_mark_missing(mark_price, monkeypatch):
    from app.services.trading_executor import TradingExecutor
    from app.services.live_trading import factory

    candidate = {"market": "Crypto", "market_type": "swap", "symbol": "BTC/USDT", "key": "btc"}
    client = Mock()
    client.get_mark_price.return_value = mark_price
    monkeypatch.setattr(factory, "create_client", lambda *a, **k: client)
    generic = Mock(return_value={"btc": 120.0})
    monkeypatch.setattr(TradingExecutor, "_live_prices", generic)
    result = TradingExecutor._execution_account_prices([candidate], {"exchange_id": "binance"}, {})
    assert result == {"btc": mark_price or 120.0}
    assert generic.call_count == (0 if mark_price else 1)


def test_real_redis_shares_atomic_budget_and_longest_ban_between_instances(monkeypatch):
    url = os.getenv("BINANCE_GUARD_TEST_REDIS_URL", "")
    if not url:
        pytest.skip("isolated Redis not supplied")
    import redis

    namespace = "qd-test-binance-guard:" + uuid.uuid4().hex
    monkeypatch.setenv("BINANCE_REST_REDIS_URL", url)
    monkeypatch.setenv("BINANCE_REST_NAMESPACE", namespace)
    instances = [BinanceRequestGuard(), BinanceRequestGuard()]
    barrier = threading.Barrier(16)

    def reserve(index):
        barrier.wait(timeout=5)
        return instances[index % 2]._reserve("live", "background", 5)

    connection = redis.Redis.from_url(url)
    try:
        with ThreadPoolExecutor(max_workers=16) as pool:
            results = list(pool.map(reserve, range(16)))
        assert sum(delay == 0 for delay, _ in results) == 5
        instances[0]._extend("live", "ban", 600)
        with pytest.raises(BinanceRateLimitError):
            instances[1].before_request(SPOT, "POST")
        instances[1]._extend("live", "ban", 10)
        assert connection.pttl(instances[0]._key("live", "ban")) > 590000
    finally:
        keys = list(connection.scan_iter(match=namespace + ":*"))
        if keys:
            connection.delete(*keys)
        connection.close()
