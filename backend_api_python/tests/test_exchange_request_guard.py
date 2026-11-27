import json
import os
import subprocess
import sys
import uuid
from unittest.mock import Mock

import pytest

from app.utils import exchange_request_guard as module
from app.utils.exchange_request_budget import ExchangeRateLimitError, _EXTEND


@pytest.fixture
def guard(isolated_exchange_request_guard):
    return isolated_exchange_request_guard


def test_account_scope_is_opaque_and_stable_across_worker_processes(guard):
    url = "https://www.okx.com/api/v5/account/balance"
    credential = "test-only-account-key"
    scope = guard.scope(url, headers={"OK-ACCESS-KEY": credential})
    assert credential not in scope.endpoint
    assert len(scope.endpoint.split(":")[2]) == 64
    assert scope.endpoint != guard.scope(url, headers={"OK-ACCESS-KEY": "other-test-key"}).endpoint
    assert scope.endpoint != guard.scope(url).endpoint
    code = (
        "from app.utils.exchange_request_guard import ExchangeRequestGuard; "
        f"print(ExchangeRequestGuard().scope({url!r}, headers={{'OK-ACCESS-KEY': {credential!r}}}).endpoint)"
    )
    for _ in range(2):
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True, timeout=30)
        assert result.stdout.strip() == scope.endpoint


@pytest.mark.parametrize("host", ["hbdm.com", "api.hbdm.com", "API.HBDM.COM:443"])
def test_htx_derivatives_scope_accepts_only_domain_boundaries(guard, host):
    params = {"AccessKeyId": "test-only-account-key"}
    scope = guard.scope(f"https://{host}/linear-swap-api/v1/swap_position_info", "POST", params=params)
    assert scope.venue == "htx"
    assert scope.endpoint.endswith(":linear:read")
    assert scope == guard.scope(
        f"https://{host}/linear-swap-api/v1/swap_position_info?AccessKeyId=test-only-account-key", "POST",
    )
    assert guard.scope(f"https://{host}/v5/trade/position_all", "POST", params=params).endpoint.endswith(":POST:/v5/trade/position_all")


@pytest.mark.parametrize("host", [
    "evilhbdm.com", "hbdm.com.evil.example", "api.hbdm.com.evil.example", "hbdm.com@evil.example",
])
def test_htx_lookalike_hosts_are_not_exchange_scopes(guard, host):
    assert module.venue_for_url(f"https://{host}/") == ""
    assert guard.scope(f"https://{host}/linear-swap-api/v1/swap_position_info", "POST",
                       params={"AccessKeyId": "test-only-account-key"}) is None


@pytest.mark.parametrize("url,payload,auth", [
    ("https://www.okx.com/api/v5/account/balance", {"code": "50011"}, {"OK-ACCESS-KEY": "secret"}),
    ("https://api.bybit.com/v5/order/realtime", {"retCode": 10006}, {"X-BAPI-API-KEY": "secret"}),
    ("https://api.bitget.com/api/v2/spot/account/assets", {"code": "429"}, {"ACCESS-KEY": "secret"}),
    ("https://api.gateio.ws/api/v4/spot/accounts", {"label": "TOO_MANY_REQUESTS"}, {"KEY": "secret"}),
    ("https://api.htx.com/v1/account/accounts", {"status": "error", "err-code": "frequent-invoke"}, {}),
    ("https://api.hbdm.com/linear-swap-api/v1/swap_position_info", {"err_code": 1032}, {}),
])
def test_http_200_business_limits_stop_followups_without_leaking_credentials(guard, url, payload, auth):
    scope = guard.before_request(url, headers=auth)
    with pytest.raises(ExchangeRateLimitError):
        guard.observe(scope, 200, {"Retry-After": "30"}, json.dumps(payload))
    with pytest.raises(ExchangeRateLimitError):
        guard.before_request(url, headers=auth)
    assert "secret" not in scope.endpoint
    if auth:
        guard.before_request(url, headers={key: "another" for key in auth})
    guard.before_request(url + "/other", headers=auth)
    guard.before_request("https://paper-api.alpaca.markets/v2/orders", "POST")


def test_read_cooldown_does_not_block_cancel_or_other_venues(guard):
    url = "https://api.gateio.ws/api/v4/spot/orders/123"
    scope = guard.before_request(url, headers={"KEY": "a"})
    with pytest.raises(ExchangeRateLimitError):
        guard.observe(scope, 429, {})
    with pytest.raises(ExchangeRateLimitError):
        guard.before_request(url.replace("123", "456"), headers={"KEY": "a"})
    guard.before_request(url, "DELETE", headers={"KEY": "a"})
    guard.before_request("https://www.okx.com/api/v5/trade/cancel-order", "POST")


def test_okx_account_order_limit_spans_new_and_amend_but_not_cancel(guard):
    prefix = "https://www.okx.com/api/v5/trade/"
    auth = {"OK-ACCESS-KEY": "a"}
    scope = guard.before_request(prefix + "order", "POST", headers=auth)
    with pytest.raises(ExchangeRateLimitError):
        guard.observe(scope, 200, {}, '{"code":"50061"}')
    for path in ("order", "batch-orders", "amend-order", "amend-batch-orders"):
        with pytest.raises(ExchangeRateLimitError):
            guard.before_request(prefix + path, "POST", headers=auth)
    guard.before_request(prefix + "cancel-order", "POST", headers=auth)
    guard.before_request(prefix + "order", "GET", headers=auth)
    guard.before_request(prefix + "order", "POST", headers={"OK-ACCESS-KEY": "b"})


def test_bybit_ip_ban_is_shared_across_hosts_accounts_and_writes(guard):
    url = "https://api.bybit.com/v5/order/create"
    scope = guard.before_request(url, "POST", headers={"X-BAPI-API-KEY": "a"})
    with pytest.raises(ExchangeRateLimitError) as exc:
        guard.observe(scope, 403, {}, "access too frequent")
    assert exc.value.retry_after >= 600
    for host in ("api.bybit.com", "api.bybit.kz", "api.bybick.com"):
        with pytest.raises(ExchangeRateLimitError):
            guard.before_request(f"https://{host}/v5/order/cancel", "POST", headers={"X-BAPI-API-KEY": "b"})
    guard.before_request("https://api-testnet.bybit.com/v5/order/create", "POST")
    guard.before_request("https://api.bybit.com.evil.example/v5/order/create", "POST")


@pytest.mark.parametrize("venue,url,payload", [
    ("bybit", "https://api.bybit.com/v5/order/create", {"retCode": 10003}),
    ("bitget", "https://api.bitget.com/api/v2/mix/order/place-order", {"code": "40010"}),
    ("okx", "https://www.okx.com/api/v5/trade/order", {"code": "51001"}),
    ("okx", "https://www.okx.com/api/v5/trade/batch-orders", {"code": "2", "data": [{"sCode": "0"}, {"sCode": "50011"}]}),
])
def test_auth_symbol_timeout_and_partial_batch_responses_remain_unchanged(guard, venue, url, payload):
    scope = guard.before_request(url, "POST")
    guard.observe(scope, 200, {}, json.dumps(payload))
    guard.observe(scope, 403, {}, "Forbidden: restricted jurisdiction")
    guard.before_request(url, "POST")
    assert not guard.budgets[venue]._deadlines


@pytest.mark.parametrize("url,headers", [
    ("https://api.bybit.com/v5/order/create", {"X-Bapi-Limit-Status": "0", "X-Bapi-Limit-Reset-Timestamp": "1800000003000"}),
    ("https://api.gateio.ws/api/v4/spot/orders", {"X-Gate-RateLimit-Requests-Remain": "0", "X-Gate-RateLimit-Reset-Timestamp": "1800000003"}),
    ("https://api.htx.com/v1/order/orders/place", {"X-HB-RateLimit-Requests-Remain": "0", "X-HB-RateLimit-Requests-Expire": "1800000003000"}),
    ("https://api.hbdm.com/v5/trade/order", {"ratelimit-remaining": "0", "ratelimit-reset": "1800000003000"}),
])
def test_last_quota_success_is_preserved_and_next_request_waits_until_reset(guard, monkeypatch, url, headers):
    monkeypatch.setattr(module.time, "time", lambda: 1800000000)
    scope = guard.before_request(url, "POST")
    guard.observe(scope, 200, headers, '{"code":0,"orderId":"accepted"}')
    with pytest.raises(ExchangeRateLimitError) as exc:
        guard.before_request(url, "POST")
    assert 2.9 < exc.value.retry_after <= 3.1


def test_successful_order_is_not_failed_when_shared_cooldown_publication_fails(guard, monkeypatch):
    budget = guard.budgets["bybit"]

    def evaluate(script, *_args):
        if script == _EXTEND:
            raise ExchangeRateLimitError(5)
        return [0, 0]

    monkeypatch.setattr(budget, "_eval", evaluate)
    request = Mock(return_value=Mock(status_code=200, text='{"retCode":0,"result":{"orderId":"123"}}',
                                    headers={"X-Bapi-Limit-Status": "0"}))
    response = module.guarded_exchange_request(request, "https://api.bybit.com/v5/order/create", method="POST")
    assert json.loads(response.text)["result"]["orderId"] == "123"
    request.assert_called_once()
    response.close.assert_not_called()


def test_htx_signed_post_reads_are_limited_and_separate_from_trading(guard):
    params = {"AccessKeyId": "private-key"}
    read = guard.scope("https://api.hbdm.com/linear-swap-api/v1/swap_cross_account_position_info", "POST", params=params)
    write = guard.scope("https://api.hbdm.com/linear-swap-api/v1/swap_cross_order", "POST", params=params)
    assert read.lane == "account"
    assert write.lane == "write"
    assert read.endpoint != write.endpoint
    assert "private-key" not in read.endpoint
    assert guard.scope("https://api.hbdm.com/v5/trade/position_all", "POST", params=params).lane == "account"


def test_read_budget_is_shared_across_clients_but_not_spent_by_writes(guard, monkeypatch):
    monkeypatch.setenv("OKX_REST_READS_PER_SECOND", "2")
    budget = guard.budgets["okx"]
    assert budget._reserve("okx:live", "public", 2) == (0.0, False)
    assert budget._reserve("okx:live", "public", 2) == (0.0, False)
    assert budget._reserve("okx:live", "public", 2)[0] > 0
    guard.before_request("https://www.okx.com/api/v5/trade/cancel-order", "POST")


def test_base_and_native_clients_share_cooldown_and_do_not_repeat_http(guard):
    from app.services.live_trading.base import BaseRestClient
    from app.data_sources.native_crypto import NativeCryptoPublicClient

    client = BaseRestClient("https://www.okx.com")
    response = Mock(status_code=200, headers={}, text='{"code":"50011","msg":"Rate limit"}')
    response.json.return_value = {"code": "50011"}
    client._request_session = Mock()
    client._request_session.request.return_value = Mock(__enter__=Mock(return_value=response), __exit__=Mock(return_value=False))
    with pytest.raises(ExchangeRateLimitError):
        client._request("GET", "/api/v5/market/ticker")
    native = NativeCryptoPublicClient("okx", "spot")
    session = Mock()
    native._session_local.value = session
    with pytest.raises(ExchangeRateLimitError):
        native._get("https://www.okx.com/api/v5/market/ticker")
    session.get.assert_not_called()
    client._request_session.request.assert_called_once()


def test_real_redis_cooldown_crosses_instances_but_not_account_or_endpoint(monkeypatch):
    url = os.getenv("BINANCE_GUARD_TEST_REDIS_URL", "")
    if not url:
        pytest.skip("isolated Redis not supplied")
    import redis

    namespace = "qd-test-exchange-guard:" + uuid.uuid4().hex
    monkeypatch.setenv("EXCHANGE_REST_REDIS_URL", url)
    monkeypatch.setenv("EXCHANGE_REST_NAMESPACE", namespace)
    one, two = module.ExchangeRequestGuard(), module.ExchangeRequestGuard()
    target = "https://www.okx.com/api/v5/account/balance"
    scope = one.before_request(target, headers={"OK-ACCESS-KEY": "a"})
    connection = redis.Redis.from_url(url)
    try:
        with pytest.raises(ExchangeRateLimitError):
            one.observe(scope, 200, {"Retry-After": "30"}, '{"code":"50011"}')
        with pytest.raises(ExchangeRateLimitError):
            two.before_request(target, headers={"OK-ACCESS-KEY": "a"})
        two.before_request(target, headers={"OK-ACCESS-KEY": "b"})
        two.before_request("https://www.okx.com/api/v5/trade/cancel-order", "POST", headers={"OK-ACCESS-KEY": "a"})
    finally:
        keys = list(connection.scan_iter(match=namespace + ":*"))
        if keys:
            connection.delete(*keys)
        connection.close()


@pytest.mark.parametrize("venue", ["bybit", "htx", "gate", "okx", "bitget"])
def test_fee_limits_return_known_fill_without_polling_or_fabricating_fees(venue, monkeypatch):
    from app.services.live_trading.bybit import BybitClient
    from app.services.live_trading.htx import HtxClient
    from app.services.live_trading.gate import GateSpotClient
    from app.services.live_trading.okx import OkxClient
    from app.services.live_trading.bitget import BitgetMixClient

    auth = {"api_key": "key", "secret_key": "secret"}
    if venue == "bybit":
        client = BybitClient(**auth, category="linear")
        detail, fee_method = {"orderStatus": "Filled", "cumExecQty": "2", "avgPrice": "100"}, "get_executions"
    elif venue == "htx":
        client = HtxClient(**auth, market_type="spot")
        detail, fee_method = {"state": "filled", "field-amount": "2", "field-cash-amount": "200"}, "get_order_match_results"
    elif venue == "gate":
        client = GateSpotClient(**auth)
        detail, fee_method = {"status": "closed", "amount": "2", "filled_amount": "2", "left": "0", "avg_deal_price": "100"}, "_order_trade_rows"
    elif venue == "okx":
        client = OkxClient(**auth, passphrase="pass")
        detail, fee_method = {"state": "filled", "accFillSz": "2", "avgPx": "100"}, "get_order_fills"
    else:
        client = BitgetMixClient(**auth, passphrase="pass")
        detail, fee_method = {"data": {"state": "filled", "baseVolume": "2", "priceAvg": "100"}}, "get_order_fills"
    query = Mock(return_value=detail)
    monkeypatch.setattr(client, "get_order_detail" if venue == "bitget" else "get_order", query)
    fee_query = Mock(side_effect=ExchangeRateLimitError(30))
    monkeypatch.setattr(client, fee_method, fee_query)
    sleep = Mock(side_effect=AssertionError("must defer fee queries rather than poll during cooldown"))
    monkeypatch.setattr(module.time, "sleep", sleep)
    kwargs = {"symbol": "BTC/USDT", "max_wait_sec": 12}
    if venue == "okx":
        kwargs.update(ord_id="1", market_type="spot")
    else:
        kwargs["order_id"] = "1"
    if venue == "bitget":
        kwargs["product_type"] = "USDT-FUTURES"
    result = client.wait_for_fill(**kwargs)
    assert result["filled"] == 2
    assert result["avg_price"] == 100
    assert not result.get("fees_by_ccy")
    assert result.get("fee_status") not in {"actual", "actual_zero"}
    fee_query.assert_called_once()
    sleep.assert_not_called()
