from __future__ import annotations

from app.services.live_trading.binance import BinanceFuturesClient
from app.services.live_trading.binance_spot import BinanceSpotClient
from app.services.live_trading.bitget import BitgetMixClient
from app.services.live_trading.bitget_spot import BitgetSpotClient
from app.services.live_trading.bybit import BybitClient
from app.services.live_trading.gate import GateSpotClient, GateUsdtFuturesClient
from app.services.live_trading.okx import OkxClient


def _fake_client(cls, response):
    client = object.__new__(cls)
    client.seen = []

    def fake_signed_request(method, path, params=None, **kwargs):
        client.seen.append({"method": method, "path": path, "params": dict(params or {})})
        return response

    client._signed_request = fake_signed_request
    return client


def test_binance_futures_fee_rate_uses_concatenated_symbol():
    client = _fake_client(
        BinanceFuturesClient,
        {"makerCommissionRate": "0.0002", "takerCommissionRate": "0.0005"},
    )

    result = client.get_fee_rate("BTC/USDT:USDT")

    assert result == {"maker": 0.0002, "taker": 0.0005}
    assert client.seen[-1]["path"] == "/fapi/v1/commissionRate"
    assert client.seen[-1]["params"] == {"symbol": "BTCUSDT"}


def test_binance_spot_fee_rate_uses_concatenated_symbol():
    client = _fake_client(
        BinanceSpotClient,
        {"standardCommission": {"maker": "0.001", "taker": "0.001"}},
    )

    result = client.get_fee_rate("ETH/USDT:USDT")

    assert result == {"maker": 0.001, "taker": 0.001}
    assert client.seen[-1]["path"] == "/api/v3/account/commission"
    assert client.seen[-1]["params"] == {"symbol": "ETHUSDT"}


def test_bitget_mix_fee_rate_uses_official_business_type_mix():
    client = _fake_client(
        BitgetMixClient,
        {"data": {"makerFeeRate": "0.0002", "takerFeeRate": "0.0006"}},
    )

    result = client.get_fee_rate("SOL/USDT:USDT", market_type="swap")

    assert result == {"maker": 0.0002, "taker": 0.0006}
    assert client.seen[-1]["path"] == "/api/v2/common/trade-rate"
    assert client.seen[-1]["params"] == {"symbol": "SOLUSDT", "businessType": "mix"}


def test_bitget_spot_fee_rate_uses_official_business_type_spot():
    client = _fake_client(
        BitgetSpotClient,
        {"data": {"makerFeeRate": "0.001", "takerFeeRate": "0.001"}},
    )

    result = client.get_fee_rate("SOL/USDT:USDT", market_type="spot")

    assert result == {"maker": 0.001, "taker": 0.001}
    assert client.seen[-1]["path"] == "/api/v2/common/trade-rate"
    assert client.seen[-1]["params"] == {"symbol": "SOLUSDT", "businessType": "spot"}


def test_bybit_fee_rate_uses_category_and_concatenated_symbol():
    client = _fake_client(
        BybitClient,
        {"result": {"list": [{"makerFeeRate": "0.0001", "takerFeeRate": "0.00055"}]}},
    )

    result = client.get_fee_rate("BTC/USDT:USDT", market_type="swap")

    assert result == {"maker": 0.0001, "taker": 0.00055}
    assert client.seen[-1]["path"] == "/v5/account/fee-rate"
    assert client.seen[-1]["params"] == {"category": "linear", "symbol": "BTCUSDT"}


def test_gate_spot_fee_rate_uses_currency_pair():
    client = _fake_client(
        GateSpotClient,
        {"maker_fee_rate": "0.001", "taker_fee_rate": "0.001"},
    )

    result = client.get_fee_rate("BTC/USDT:USDT", market_type="spot")

    assert result == {"maker": 0.001, "taker": 0.001}
    assert client.seen[-1]["path"] == "/api/v4/wallet/fee"
    assert client.seen[-1]["params"] == {"currency_pair": "BTC_USDT"}


def test_gate_futures_fee_rate_uses_contract_path():
    client = _fake_client(
        GateUsdtFuturesClient,
        {"maker_fee_rate": "0.0002", "taker_fee_rate": "0.0005"},
    )

    result = client.get_fee_rate("BTC/USDT:USDT", market_type="swap")

    assert result == {"maker": 0.0002, "taker": 0.0005}
    assert client.seen[-1]["path"] == "/api/v4/futures/usdt/contracts/BTC_USDT"
    assert client.seen[-1]["params"] == {}


def test_okx_fee_rate_uses_inst_type_and_inst_id():
    client = _fake_client(
        OkxClient,
        {"data": [{"maker": "-0.0002", "taker": "-0.0005"}]},
    )

    result = client.get_fee_rate("SOL/USDT:USDT", market_type="swap")

    assert result == {"maker": 0.0002, "taker": 0.0005}
    assert client.seen[-1]["path"] == "/api/v5/account/trade-fee"
    assert client.seen[-1]["params"] == {"instType": "SWAP", "instId": "SOL-USDT-SWAP"}
