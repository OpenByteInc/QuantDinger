"""OKX order size precision inference from lotSz.

Regression test: lotSz values like "0.00000001" normalize to scientific
notation ("1E-8") under Decimal.normalize(), which the old string-based
parser misread as precision 0 — collapsing any sub-1-unit spot order to
sz="0" and triggering OKX error 51000 (Parameter sz error).
"""

import time

from app.services.live_trading.okx import OkxClient


def _client_with_instrument(inst_id: str, inst_type: str, lot_sz: str, min_sz: str) -> OkxClient:
    c = OkxClient(api_key="k", secret_key="s", passphrase="p")
    c._inst_cache[f"{inst_type}:{inst_id}"] = (
        time.time(),
        {"instId": inst_id, "lotSz": lot_sz, "minSz": min_sz},
    )
    return c


def test_spot_small_lot_sz_keeps_fractional_size():
    # BTC-USDT spot: lotSz=0.00000001 (1E-8 after Decimal.normalize()).
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
    # Floored to lot step, then rendered without exceeding precision.
    assert c._dec_str(sz, strict_precision=precision) == "0.023456"


def test_swap_integer_lot_sz_precision_zero():
    # Swap contracts: lotSz=1 must keep precision 0 (whole contracts).
    c = _client_with_instrument("BTC-USDT-SWAP", "SWAP", "1", "1")
    c._inst_cache["SWAP:BTC-USDT-SWAP"][1]["ctVal"] = "0.01"
    sz, precision = c._normalize_order_size(
        inst_id="BTC-USDT-SWAP", market_type="swap", size=0.05
    )
    assert precision == 0
    assert c._dec_str(sz, strict_precision=precision) == "5"
