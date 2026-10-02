"""Derivative account configuration safety checks."""

import pytest

from app.services.live_trading.account_configuration import (
    configure_derivatives_account,
    requires_derivatives_account_configuration,
)
from app.services.live_trading.base import LiveTradingError
from app.services.live_trading.bybit import BybitClient
from app.services.live_trading.binance import BinanceFuturesClient
from app.services.live_trading.gate import GateUsdtFuturesClient
from app.services.live_trading.okx import OkxClient


def test_okx_spot_account_mode_is_rejected_before_leverage_change():
    client = OkxClient.__new__(OkxClient)
    leverage_calls = []
    client.get_account_config = lambda: {"acctLv": "1", "posMode": "net_mode"}
    client.set_leverage = lambda **kwargs: leverage_calls.append(kwargs) or True

    with pytest.raises(LiveTradingError, match="OKX_SWAP_ACCOUNT_MODE_REQUIRED"):
        configure_derivatives_account(
            client,
            exchange_id="okx",
            symbol="BTC/USDT",
            leverage=5,
            margin_mode="cross",
        )

    assert leverage_calls == []


def test_bybit_unchanged_leverage_is_success():
    client = BybitClient.__new__(BybitClient)
    client.category = "linear"

    def unchanged(*_args, **_kwargs):
        raise LiveTradingError("Bybit error: {'retCode': 110043, 'retMsg': 'leverage not modified'}")

    client._signed_request = unchanged

    assert client.set_leverage(symbol="BTC/USDT", leverage=1) is True


def test_bybit_unchanged_margin_mode_is_success():
    client = BybitClient.__new__(BybitClient)
    client.category = "linear"

    def unchanged(*_args, **_kwargs):
        raise LiveTradingError("Bybit error: {'retCode': 110026, 'retMsg': 'margin mode not modified'}")

    client._signed_request = unchanged

    assert client.set_margin_mode("cross") is True


def test_reduce_only_swap_close_skips_account_configuration():
    from app.services.live_trading.account_configuration import (
        requires_derivatives_account_configuration,
    )

    assert requires_derivatives_account_configuration(
        market_type="swap", reduce_only=True
    ) is False


def test_swap_open_still_requires_account_configuration():
    from app.services.live_trading.account_configuration import (
        requires_derivatives_account_configuration,
    )

    assert requires_derivatives_account_configuration(
        market_type="swap", reduce_only=False
    ) is True


def test_binance_rejects_leverage_above_api_limit_instead_of_clamping():
    client = BinanceFuturesClient.__new__(BinanceFuturesClient)
    client._signed_request = lambda *_args, **_kwargs: pytest.fail("request must not be sent")

    with pytest.raises(LiveTradingError, match="exceeds"):
        client.set_leverage(symbol="BTC/USDT", leverage=126)


def test_binance_rejects_effective_leverage_mismatch():
    client = BinanceFuturesClient.__new__(BinanceFuturesClient)
    client._signed_request = lambda *_args, **_kwargs: {"leverage": 10}

    with pytest.raises(LiveTradingError, match="applied 10x"):
        client.set_leverage(symbol="BTC/USDT", leverage=20)


def test_reduce_only_swap_skips_derivatives_configuration():
    assert requires_derivatives_account_configuration(market_type="swap", reduce_only=True) is False
    assert requires_derivatives_account_configuration(market_type="swap", reduce_only=False) is True
    assert requires_derivatives_account_configuration(market_type="spot", reduce_only=False) is False


def test_binance_margin_timeout_continues_after_configuration_readback():
    client = BinanceFuturesClient.__new__(BinanceFuturesClient)
    client.set_margin_type = lambda **_kwargs: (_ for _ in ()).throw(
        LiveTradingError(
            'Binance HTTP 408: {"code":-1007,"msg":"Timeout waiting for response; execution status unknown."}'
        )
    )
    leverage_calls = []
    client.set_leverage = lambda **kwargs: leverage_calls.append(kwargs) or {"leverage": 5}
    client.get_symbol_configuration = lambda **_kwargs: {
        "margin_mode": "cross",
        "leverage": 5,
    }

    result = configure_derivatives_account(
        client,
        exchange_id="binance",
        symbol="BTC/USDT",
        leverage=5,
        margin_mode="cross",
    )

    assert result["margin_mode_confirmed_after_timeout"] is True
    assert leverage_calls == [{"symbol": "BTC/USDT", "leverage": 5}]


def test_binance_margin_timeout_fails_when_readback_differs():
    client = BinanceFuturesClient.__new__(BinanceFuturesClient)
    client.set_margin_type = lambda **_kwargs: (_ for _ in ()).throw(
        LiveTradingError("Binance HTTP 408: code=-1007 execution status unknown")
    )
    client.set_leverage = lambda **_kwargs: pytest.fail("leverage must not be changed")
    client.get_symbol_configuration = lambda **_kwargs: {
        "margin_mode": "isolated",
        "leverage": 5,
    }

    with pytest.raises(LiveTradingError, match="could not be confirmed"):
        configure_derivatives_account(
            client,
            exchange_id="binance",
            symbol="BTC/USDT",
            leverage=5,
            margin_mode="cross",
        )


def test_gate_lists_dual_mode_positions_through_standard_collection_endpoint():
    client = GateUsdtFuturesClient.__new__(GateUsdtFuturesClient)
    calls = []
    positions = [
        {"contract": "BTC_USDT", "mode": "dual_long", "size": "2"},
        {"contract": "BTC_USDT", "mode": "dual_short", "size": "-1"},
    ]

    def signed(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return positions

    client._signed_request = signed

    assert client.get_positions() == positions
    assert calls == [(
        "GET",
        "/api/v4/futures/usdt/positions",
        {"extra_headers": {"X-Gate-Size-Decimal": "1"}},
    )]


def test_gate_rejects_leverage_above_dynamic_maximum():
    client = GateUsdtFuturesClient.__new__(GateUsdtFuturesClient)
    client.get_contract = lambda **_kwargs: {"leverage_max": "10"}
    client._signed_request = lambda *_args, **_kwargs: pytest.fail("request must not be sent")

    with pytest.raises(LiveTradingError, match="maximum 10x"):
        client.set_leverage(contract="BTC_USDT", leverage=11, margin_mode="cross")


@pytest.mark.parametrize(
    ("position_mode", "expected_path"),
    [
        ("single", "/api/v4/futures/usdt/positions/BTC_USDT/leverage"),
        ("dual", "/api/v4/futures/usdt/dual_comp/positions/BTC_USDT/leverage"),
    ],
)
def test_gate_uses_documented_leverage_endpoint_for_position_mode(
    position_mode, expected_path
):
    client = GateUsdtFuturesClient.__new__(GateUsdtFuturesClient)
    calls = []
    client.get_position_mode = lambda: position_mode
    client.get_contract = lambda **_kwargs: {"leverage_max": "100"}
    client._signed_request = lambda method, path, **kwargs: (
        calls.append((method, path, kwargs)) or {"cross_leverage_limit": "5"}
    )

    assert client.set_leverage(
        contract="BTC_USDT", leverage=5, margin_mode="cross"
    ) is True
    assert calls == [(
        "POST",
        expected_path,
        {
            "params": {"leverage": "0", "cross_leverage_limit": "5"},
            "json_body": None,
        },
    )]


def test_gate_rejects_effective_leverage_mismatch():
    client = GateUsdtFuturesClient.__new__(GateUsdtFuturesClient)
    client._position_mode_cache = (1e20, "single")
    client._position_mode_cache_ttl_sec = 30.0
    client.get_contract = lambda **_kwargs: {"leverage_max": "100"}
    client._signed_request = lambda *_args, **_kwargs: {"cross_leverage_limit": "10"}

    with pytest.raises(LiveTradingError, match="applied 10x"):
        client.set_leverage(contract="BTC_USDT", leverage=20, margin_mode="cross")


def test_gate_split_position_mode_is_rejected_before_leverage_change():
    client = GateUsdtFuturesClient.__new__(GateUsdtFuturesClient)
    client.get_position_mode = lambda: "dual_plus"
    client.set_leverage = lambda **_kwargs: pytest.fail("leverage must not be changed")

    with pytest.raises(LiveTradingError, match="dual_plus split-position"):
        configure_derivatives_account(
            client,
            exchange_id="gate",
            symbol="BTC/USDT",
            leverage=5,
            margin_mode="cross",
        )


def test_okx_hedge_mode_sets_both_leg_leverages():
    client = OkxClient.__new__(OkxClient)
    calls = []
    client.get_account_config = lambda: {
        "acctLv": "2",
        "posMode": "long_short_mode",
    }
    client.set_leverage = lambda **kwargs: calls.append(kwargs) or True

    result = configure_derivatives_account(
        client,
        exchange_id="okx",
        symbol="BTC/USDT",
        leverage=5,
        margin_mode="cross",
    )

    assert result["position_mode"] == "hedge"
    assert {call["pos_side"] for call in calls} == {"long", "short"}
