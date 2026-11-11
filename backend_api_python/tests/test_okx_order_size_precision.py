"""OKX order size precision inference from lotSz."""

import time
from unittest.mock import patch

from app.services.live_trading.okx import OkxClient


def _client_with_instrument(inst_id: str, inst_type: str, lot_sz: str, min_sz: str) -> OkxClient:
    c = OkxClient(api_key="k", secret_key="s", passphrase="p")
    c._inst_cache[f"{inst_type}:{inst_id}"] = (
        time.time(),
        {"instId": inst_id, "lotSz": lot_sz, "minSz": min_sz},
    )
    return c


def test_spot_small_lot_sz_keeps_fractional_size():
    c = _client_with_instrument("BTC-USDT", "SPOT", "0.00000001", "0.00001")
    sz, precision = c._normalize_order_size(
        inst_id="BTC-USDT", market_type="spot", size=0.00081471
    )
    assert precision == 8
    assert c._dec_str(sz, strict_precision=precision) == "0.00081471"


def test_spot_regular_lot_sz_precision():
    c = _client_with_instrument("ETH-USDT", "SPOT", "0.000001", "0.001")
    sz, precision = c._normalize_order_size(
        inst_id="ETH-USDT", market_type="spot", size=0.0234567891
    )
    assert precision == 6
    assert c._dec_str(sz, strict_precision=precision) == "0.023456"


def test_swap_integer_lot_sz_precision_zero():
    c = _client_with_instrument("BTC-USDT-SWAP", "SWAP", "1", "1")
    c._inst_cache["SWAP:BTC-USDT-SWAP"][1]["ctVal"] = "0.01"
    sz, precision = c._normalize_order_size(
        inst_id="BTC-USDT-SWAP", market_type="swap", size=0.05
    )
    assert precision == 0
    assert c._dec_str(sz, strict_precision=precision) == "5"


def test_positive_exponent_lot_sz_clamps_to_zero_precision():
    c = _client_with_instrument("X-USDT", "SPOT", "10", "10")
    sz, precision = c._normalize_order_size(
        inst_id="X-USDT", market_type="spot", size=25.0
    )
    assert precision == 0
    assert c._dec_str(sz, strict_precision=precision) == "20"


def test_spot_limit_order_serializes_small_price_without_exponent():
    c = _client_with_instrument("SHIB-USDT", "SPOT", "1", "1")
    with patch.object(c, "_signed_request", return_value={"data": [{"ordId": "123"}]}) as request:
        c.place_limit_order(
            market_type="spot",
            symbol="SHIB/USDT",
            side="buy",
            size=10,
            price=1e-05,
        )

    body = request.call_args.kwargs["json_body"]
    assert body["px"] == "0.00001"
    assert "e" not in body["px"].lower()


def test_swap_limit_order_serializes_small_price_without_exponent():
    c = _client_with_instrument("SHIB-USDT-SWAP", "SWAP", "1", "1")
    c._inst_cache["SWAP:SHIB-USDT-SWAP"][1]["ctVal"] = "1"
    with (
        patch.object(c, "_resolve_pos_side", return_value="net"),
        patch.object(c, "_signed_request", return_value={"data": [{"ordId": "123"}]}) as request,
    ):
        c.place_limit_order(
            market_type="swap",
            symbol="SHIB/USDT",
            side="buy",
            size=10,
            price=1e-05,
        )

    body = request.call_args.kwargs["json_body"]
    assert body["px"] == "0.00001"
    assert "e" not in body["px"].lower()


def test_swap_limit_order_snaps_price_to_tick_size():
    c = _client_with_instrument("BTC-USDT-SWAP", "SWAP", "1", "1")
    c._inst_cache["SWAP:BTC-USDT-SWAP"][1].update({"ctVal": "0.01", "tickSz": "0.1"})
    with (
        patch.object(c, "_resolve_pos_side", return_value="net"),
        patch.object(c, "_signed_request", return_value={"data": [{"ordId": "123"}]}) as request,
    ):
        c.place_limit_order(
            market_type="swap",
            symbol="BTC/USDT",
            side="buy",
            size=0.05,
            price=77951.14749999999,
        )

    body = request.call_args.kwargs["json_body"]
    assert body["px"] == "77951.1"
