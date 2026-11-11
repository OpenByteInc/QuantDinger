from decimal import Decimal

import pytest

from app.services.live_trading.base import LiveTradingError
from app.services.live_trading.native_protection import (
    NativeProtectionRequest,
    place_native_protection_orders,
    protection_prices_from_payload,
)


def _request(**overrides):
    values = {
        "symbol": "BTC/USDT",
        "pos_side": "long",
        "quantity": 0.01,
        "entry_price": 100.0,
        "stop_loss_price": 90.0,
        "take_profit_price": 120.0,
        "margin_mode": "cross",
        "client_order_id": "qdprot1",
    }
    values.update(overrides)
    return NativeProtectionRequest(**values)


def test_resolves_strategy_protection_percentages():
    stop, take, trailing, activation = protection_prices_from_payload(
        {
            "protection": {
                "stop_loss_pct": 0.1,
                "take_profit_pct": 0.2,
                "trailing_stop_pct": 0.03,
                "trailing_activation_pct": 0.04,
            }
        },
        entry_price=100,
        pos_side="long",
    )
    assert stop == pytest.approx(90)
    assert take == pytest.approx(120)
    assert trailing == pytest.approx(0.03)
    assert activation == pytest.approx(0.04)


def test_binance_uses_current_algo_order_endpoint():
    from app.services.live_trading.binance import BinanceFuturesClient

    client = BinanceFuturesClient.__new__(BinanceFuturesClient)
    client.broker_id = BinanceFuturesClient._BROKER_ID
    client.get_dual_side_position = lambda: False
    calls = []
    client._signed_request = lambda method, path, params: calls.append((method, path, params)) or {"algoId": 1}

    result = place_native_protection_orders(client, _request())

    assert len(result) == 2
    assert {call[2]["type"] for call in calls} == {"STOP_MARKET", "TAKE_PROFIT_MARKET"}
    assert all(call[1] == "/fapi/v1/algoOrder" for call in calls)
    assert all(call[2]["reduceOnly"] == "true" for call in calls)
    assert all(
        call[2]["clientAlgoId"].startswith(f"x-{BinanceFuturesClient._BROKER_ID}")
        for call in calls
    )
    assert len({call[2]["clientAlgoId"] for call in calls}) == 2


def test_binance_trailing_protection_keeps_partner_attribution():
    from app.services.live_trading.binance import BinanceFuturesClient

    client = BinanceFuturesClient.__new__(BinanceFuturesClient)
    client.broker_id = BinanceFuturesClient._BROKER_ID
    client.get_dual_side_position = lambda: False
    calls = []
    client._signed_request = (
        lambda method, path, params: calls.append((method, path, params)) or {"algoId": 1}
    )

    result = place_native_protection_orders(
        client,
        _request(
            stop_loss_price=0,
            take_profit_price=0,
            trailing_stop_pct=0.02,
            trailing_activation_pct=0.01,
        ),
    )

    assert len(result) == 1
    assert calls[0][2]["type"] == "TRAILING_STOP_MARKET"
    assert calls[0][2]["clientAlgoId"].startswith(
        f"x-{BinanceFuturesClient._BROKER_ID}"
    )


def test_binance_normalizes_native_protection_to_exchange_filters():
    from app.services.live_trading.binance import BinanceFuturesClient

    client = BinanceFuturesClient.__new__(BinanceFuturesClient)
    client.broker_id = BinanceFuturesClient._BROKER_ID
    client.get_dual_side_position = lambda: False
    client.get_symbol_filters = lambda **_kwargs: {
        "PRICE_FILTER": {"tickSize": "0.1", "minPrice": "0.1"},
        "LOT_SIZE": {"stepSize": "0.001", "minQty": "0.001"},
        "MARKET_LOT_SIZE": {"stepSize": "0.001", "minQty": "0.001"},
        "_meta": {"pricePrecision": 1, "quantityPrecision": 3},
    }
    calls = []
    client._signed_request = (
        lambda method, path, params: calls.append((method, path, params))
        or {"algoId": len(calls)}
    )

    result = place_native_protection_orders(
        client,
        _request(
            quantity=0.001234,
            entry_price=76049.9,
            stop_loss_price=74148.6525,
            take_profit_price=77951.14749999999,
            trailing_stop_pct=0.02,
            trailing_activation_pct=0.05,
        ),
    )

    assert len(result) == 3
    assert all(call[2]["quantity"] == "0.001" for call in calls)
    by_type = {call[2]["type"]: call[2] for call in calls}
    assert by_type["STOP_MARKET"]["triggerPrice"] == "74148.6"
    assert by_type["TAKE_PROFIT_MARKET"]["triggerPrice"] == "77951.1"
    assert by_type["TRAILING_STOP_MARKET"]["activatePrice"] == "79852.3"


def test_binance_rejects_native_protection_quantity_below_exchange_minimum():
    from app.services.live_trading.binance import BinanceFuturesClient

    client = BinanceFuturesClient.__new__(BinanceFuturesClient)
    client.broker_id = BinanceFuturesClient._BROKER_ID
    client.get_dual_side_position = lambda: False
    client.get_symbol_filters = lambda **_kwargs: {
        "PRICE_FILTER": {"tickSize": "0.1", "minPrice": "0.1"},
        "LOT_SIZE": {"stepSize": "0.001", "minQty": "0.001"},
        "MARKET_LOT_SIZE": {"stepSize": "0.001", "minQty": "0.001"},
    }
    calls = []
    client._signed_request = lambda *_args, **_kwargs: calls.append(True)

    with pytest.raises(
        LiveTradingError,
        match="binance_native_protection_quantity_below_minimum",
    ):
        place_native_protection_orders(client, _request(quantity=0.0009))

    assert calls == []


def test_okx_hedge_protection_uses_position_side_without_net_only_reduce_flag():
    from app.services.live_trading.okx import OkxClient

    client = OkxClient.__new__(OkxClient)
    client.broker_code = "broker"
    client._normalize_order_size = lambda **_kwargs: (Decimal("2"), 0)
    client._resolve_pos_side = lambda **_kwargs: "long"
    calls = []
    client._signed_request = lambda method, path, json_body=None, params=None: calls.append(json_body) or {"data": [{"algoId": "1"}]}

    result = place_native_protection_orders(client, _request())

    assert len(result) == 2
    assert all("reduceOnly" not in body for body in calls)
    assert calls[0]["tag"] == "broker"
    assert any("slTriggerPx" in body for body in calls)
    assert any("tpTriggerPx" in body for body in calls)


def test_okx_net_protection_sets_reduce_only():
    from app.services.live_trading.okx import OkxClient

    client = OkxClient.__new__(OkxClient)
    client.broker_code = ""
    client._normalize_order_size = lambda **_kwargs: (Decimal("2"), 0)
    client._resolve_pos_side = lambda **_kwargs: "net"
    calls = []
    client._signed_request = (
        lambda method, path, json_body=None, params=None: calls.append(json_body)
        or {"data": [{"algoId": "1"}]}
    )

    place_native_protection_orders(client, _request())

    assert calls
    assert all(body["reduceOnly"] == "true" for body in calls)


def test_okx_snaps_trigger_and_activation_prices_to_tick():
    from app.services.live_trading.okx import OkxClient

    client = OkxClient.__new__(OkxClient)
    client.broker_code = ""
    client._normalize_order_size = lambda **_kwargs: (Decimal("2"), 0)
    client._resolve_pos_side = lambda **_kwargs: "net"
    client.get_instrument = lambda **_kwargs: {"tickSz": "0.1"}
    calls = []
    client._signed_request = (
        lambda method, path, json_body=None, params=None: calls.append(json_body)
        or {"data": [{"algoId": "1"}]}
    )

    place_native_protection_orders(
        client,
        _request(
            entry_price=76049.9,
            stop_loss_price=74148.6525,
            take_profit_price=77951.14749999999,
            trailing_stop_pct=0.05,
            trailing_activation_pct=0.05,
        ),
    )

    assert calls[0]["slTriggerPx"] == "74148.6"
    assert calls[1]["tpTriggerPx"] == "77951.1"
    assert calls[2]["activePx"] == "79852.3"


def test_okx_rejects_native_protection_quantity_below_exchange_minimum():
    from app.services.live_trading.okx import OkxClient

    client = OkxClient.__new__(OkxClient)
    client.broker_code = ""
    client._normalize_order_size = lambda **_kwargs: (Decimal("0"), 3)
    client._resolve_pos_side = lambda **_kwargs: "net"

    with pytest.raises(
        LiveTradingError,
        match="okx_native_protection_quantity_below_minimum",
    ):
        place_native_protection_orders(client, _request(quantity=0.0001))


def test_bitget_keeps_channel_header_path_and_position_side():
    from app.services.live_trading.bitget import BitgetMixClient

    client = BitgetMixClient.__new__(BitgetMixClient)
    client._normalize_size = lambda **_kwargs: (Decimal("0.01"), 2)
    calls = []
    client._signed_request = lambda method, path, json_body=None, params=None: calls.append((path, json_body)) or {"code": "00000"}

    place_native_protection_orders(client, _request())

    assert len(calls) == 1
    path, body = calls[0]
    assert path == "/api/v3/trade/place-strategy-order"
    assert body["posSide"] == "long"
    assert body["reduceOnly"] == "yes"
    assert body["stopLoss"] == "90"
    assert body["takeProfit"] == "120"


def test_bitget_snaps_trigger_and_activation_prices_to_contract_tick():
    from app.services.live_trading.bitget import BitgetMixClient

    client = BitgetMixClient.__new__(BitgetMixClient)
    client._normalize_size = lambda **_kwargs: (Decimal("0.01"), 2)
    client.get_contract = lambda **_kwargs: {"pricePrecision": "1", "priceMultiplier": "0.1"}
    calls = []
    client._signed_request = (
        lambda method, path, json_body=None, params=None: calls.append(json_body)
        or {"code": "00000"}
    )

    place_native_protection_orders(
        client,
        _request(
            entry_price=76049.9,
            stop_loss_price=74148.6525,
            take_profit_price=79852.395,
            trailing_stop_pct=0.05,
            trailing_activation_pct=0.05,
        ),
    )

    assert calls[0]["stopLoss"] == "74148.6"
    assert calls[0]["takeProfit"] == "79852.3"
    assert calls[1]["trailingStopParams"]["activationPrice"] == "79852.3"


def test_bitget_rejects_native_protection_quantity_below_exchange_minimum():
    from app.services.live_trading.bitget import BitgetMixClient

    client = BitgetMixClient.__new__(BitgetMixClient)
    client._normalize_size = lambda **_kwargs: (Decimal("0"), 3)

    with pytest.raises(
        LiveTradingError,
        match="bitget_native_protection_quantity_below_minimum",
    ):
        place_native_protection_orders(client, _request(quantity=0.0001))


def test_bitget_classic_account_uses_v2_native_protection_orders():
    from app.services.live_trading.bitget_compat import BitgetCompatibleMixClient

    client = BitgetCompatibleMixClient(
        api_key="classic-native-protection",
        secret_key="secret",
        passphrase="passphrase",
        account_mode="classic",
    )
    routed = client._classic_client
    routed._normalize_size = lambda **_kwargs: (Decimal("0.01"), 2)
    routed.get_contract = lambda **_kwargs: {"pricePlace": "1", "priceEndStep": "5"}
    calls = []
    routed._signed_request = (
        lambda method, path, json_body=None, params=None: calls.append((path, json_body))
        or {"code": "00000"}
    )

    place_native_protection_orders(client, _request())

    assert len(calls) == 2
    assert all(path == "/api/v2/mix/order/place-tpsl-order" for path, _body in calls)
    assert {body["planType"] for _path, body in calls} == {"loss_plan", "profit_plan"}
    assert all(body["holdSide"] == "long" for _path, body in calls)


def test_bitget_classic_snaps_trigger_prices_to_price_end_step():
    from app.services.live_trading.bitget_compat import BitgetCompatibleMixClient

    client = BitgetCompatibleMixClient(
        api_key="classic-native-protection",
        secret_key="secret",
        passphrase="passphrase",
        account_mode="classic",
    )
    routed = client._classic_client
    routed._normalize_size = lambda **_kwargs: (Decimal("0.01"), 2)
    routed.get_contract = lambda **_kwargs: {"pricePlace": "1", "priceEndStep": "5"}
    calls = []
    routed._signed_request = (
        lambda method, path, json_body=None, params=None: calls.append(json_body)
        or {"code": "00000"}
    )

    place_native_protection_orders(
        client,
        _request(
            entry_price=76049.9,
            stop_loss_price=74148.6525,
            take_profit_price=79852.395,
            trailing_stop_pct=0.05,
            trailing_activation_pct=0.05,
        ),
    )

    assert [body["triggerPrice"] for body in calls] == ["74148.5", "79852", "79852"]


def test_bitget_classic_rejects_native_protection_quantity_below_exchange_minimum():
    from app.services.live_trading.bitget_compat import BitgetCompatibleMixClient

    client = BitgetCompatibleMixClient(
        api_key="classic-native-protection",
        secret_key="secret",
        passphrase="passphrase",
        account_mode="classic",
    )
    client._classic_client._normalize_size = lambda **_kwargs: (Decimal("0"), 2)

    with pytest.raises(
        LiveTradingError,
        match="bitget_native_protection_quantity_below_minimum",
    ):
        place_native_protection_orders(client, _request(quantity=0.0001))


def test_bybit_conditional_orders_are_reduce_only():
    from app.services.live_trading.bybit import BybitClient

    client = BybitClient.__new__(BybitClient)
    client._normalize_qty = lambda **_kwargs: (Decimal("0.01"), 2)
    client._resolve_position_idx = lambda *_args, **_kwargs: 1
    calls = []
    client._signed_request = lambda method, path, json_body=None, params=None: calls.append(json_body) or {"retCode": 0}

    place_native_protection_orders(client, _request())

    assert len(calls) == 2
    assert all(body["reduceOnly"] is True for body in calls)
    assert all(body["closeOnTrigger"] is True for body in calls)
    assert {body["triggerDirection"] for body in calls} == {1, 2}


def test_bybit_snaps_trigger_and_trailing_prices_to_tick():
    from app.services.live_trading.bybit import BybitClient

    client = BybitClient.__new__(BybitClient)
    client.category = "linear"
    client.get_instrument_info = lambda **_kwargs: {"priceFilter": {"tickSize": "0.1"}}
    client._normalize_qty = lambda **_kwargs: (Decimal("0.01"), 2)
    client._resolve_position_idx = lambda *_args, **_kwargs: 2
    calls = []
    client._signed_request = (
        lambda method, path, json_body=None, params=None: calls.append(json_body)
        or {"retCode": 0}
    )

    place_native_protection_orders(
        client,
        _request(
            pos_side="short",
            entry_price=76049.9,
            stop_loss_price=77951.14749999999,
            take_profit_price=0,
            trailing_stop_pct=0.05,
            trailing_activation_pct=0.05,
        ),
    )

    assert calls[0]["triggerPrice"] == "77951.1"
    assert calls[1]["trailingStop"] == "3802.4"
    assert calls[1]["activePrice"] == "72247.4"


def test_bybit_rejects_native_protection_quantity_below_exchange_minimum():
    from app.services.live_trading.bybit import BybitClient

    client = BybitClient.__new__(BybitClient)
    client._normalize_qty = lambda **_kwargs: (Decimal("0"), 3)

    with pytest.raises(
        LiveTradingError,
        match="bybit_native_protection_quantity_below_minimum",
    ):
        place_native_protection_orders(client, _request(quantity=0.0001))


def test_gate_price_orders_use_reduce_only_signed_contract_size():
    from app.services.live_trading.gate import GateUsdtFuturesClient

    client = GateUsdtFuturesClient.__new__(GateUsdtFuturesClient)
    client._resolve_order_size = lambda **_kwargs: ("-2", None)
    calls = []
    client._signed_request = lambda method, path, **kwargs: calls.append((path, kwargs)) or {"id": 1}

    place_native_protection_orders(client, _request())

    assert len(calls) == 2
    assert all(kwargs["json_body"]["initial"]["reduce_only"] is True for _, kwargs in calls)
    assert all(kwargs["json_body"]["initial"]["size"] == -2 for _, kwargs in calls)


def test_gate_decimal_price_orders_use_string_amount():
    from app.services.live_trading.gate import GateUsdtFuturesClient

    client = GateUsdtFuturesClient.__new__(GateUsdtFuturesClient)
    client._resolve_order_size = lambda **_kwargs: (
        "-0.2",
        {"X-Gate-Size-Decimal": "1"},
    )
    calls = []
    client._signed_request = lambda method, path, **kwargs: calls.append((path, kwargs)) or {"id": 1}

    place_native_protection_orders(client, _request())

    assert len(calls) == 2
    assert all(kwargs["json_body"]["initial"]["amount"] == "-0.2" for _, kwargs in calls)
    assert all("size" not in kwargs["json_body"]["initial"] for _, kwargs in calls)


def test_gate_snaps_trigger_prices_to_contract_tick():
    from app.services.live_trading.gate import GateUsdtFuturesClient

    client = GateUsdtFuturesClient.__new__(GateUsdtFuturesClient)
    client._resolve_order_size = lambda **_kwargs: ("2", None)
    client.get_contract = lambda **_kwargs: {"order_price_round": "0.1"}
    calls = []
    client._signed_request = (
        lambda method, path, **kwargs: calls.append(kwargs["json_body"])
        or {"id": 1}
    )

    place_native_protection_orders(
        client,
        _request(
            entry_price=76049.9,
            stop_loss_price=74148.6525,
            take_profit_price=77951.14749999999,
        ),
    )

    assert [body["trigger"]["price"] for body in calls] == ["74148.6", "77951.1"]


def test_gate_rejects_native_protection_quantity_below_exchange_minimum():
    from app.services.live_trading.gate import GateUsdtFuturesClient

    client = GateUsdtFuturesClient.__new__(GateUsdtFuturesClient)
    client._resolve_order_size = lambda **_kwargs: ("0", None)

    with pytest.raises(
        LiveTradingError,
        match="gate_native_protection_quantity_below_minimum",
    ):
        place_native_protection_orders(client, _request(quantity=0.0001))


def test_htx_uses_margin_mode_specific_position_tpsl_endpoint():
    from app.services.live_trading.htx import HtxClient

    client = HtxClient.__new__(HtxClient)
    client._base_to_contracts = lambda **_kwargs: 3
    calls = []
    client._swap_private_request_raw = lambda method, path, **kwargs: calls.append((path, kwargs["json_body"])) or {"status": "ok"}

    result = place_native_protection_orders(client, _request(margin_mode="isolated"))

    assert len(result) == 1
    assert calls[0][0] == "/linear-swap-api/v1/swap_tpsl_order"
    assert calls[0][1]["volume"] == 3
    assert calls[0][1]["direction"] == "sell"


def test_htx_snaps_native_protection_prices_to_contract_tick():
    from app.services.live_trading.htx import HtxClient

    client = HtxClient.__new__(HtxClient)
    client.market_type = "swap"
    client._base_to_contracts = lambda **_kwargs: 3
    client.get_contract_info = lambda **_kwargs: {"price_tick": "0.1"}
    calls = []
    client._swap_private_request_raw = (
        lambda method, path, **kwargs: calls.append(kwargs["json_body"])
        or {"status": "ok"}
    )

    place_native_protection_orders(
        client,
        _request(
            entry_price=76049.9,
            stop_loss_price=74148.6525,
            take_profit_price=77951.14749999999,
        ),
    )

    assert calls[0]["sl_trigger_price"] == 74148.6
    assert calls[0]["tp_trigger_price"] == 77951.1


def test_htx_rejects_native_protection_quantity_below_exchange_minimum():
    from app.services.live_trading.htx import HtxClient

    client = HtxClient.__new__(HtxClient)
    client._base_to_contracts = lambda **_kwargs: 0

    with pytest.raises(
        LiveTradingError,
        match="htx_native_protection_quantity_below_minimum",
    ):
        place_native_protection_orders(client, _request(quantity=0.0001))
