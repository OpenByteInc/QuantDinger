"""Grid config sanitization and Bitget cancel wiring."""

from __future__ import annotations

from app.services.grid.config import sanitize_grid_bot_params
from app.services.grid.exchange_orders import cancel_grid_order
from app.services.live_trading.bitget import BitgetMixClient


def test_sanitize_neutral_clears_initial_pct():
    out = sanitize_grid_bot_params({"gridDirection": "neutral", "initialPositionPct": 15})
    assert out["initialPositionPct"] == 0


def test_sanitize_long_keeps_initial_pct():
    out = sanitize_grid_bot_params({"gridDirection": "long", "initialPositionPct": 15})
    assert out["initialPositionPct"] == 15


def test_cancel_grid_order_bitget_passes_product_type():
    class FakeBitget(BitgetMixClient):
        def __init__(self):
            self.last_cancel = None

        def cancel_order(self, **kwargs):
            self.last_cancel = kwargs
            return {"code": "00000"}

    client = FakeBitget()
    cancel_grid_order(
        client,
        symbol="BTC/USDT",
        market_type="swap",
        exchange_order_id="1445428223646216193",
        client_order_id="g001c005e12345",
        exchange_config={"product_type": "USDT-FUTURES", "margin_coin": "USDT"},
    )
    assert client.last_cancel is not None
    assert client.last_cancel["product_type"] == "USDT-FUTURES"
    assert client.last_cancel["margin_coin"] == "USDT"
    assert client.last_cancel["order_id"] == "1445428223646216193"
    assert client.last_cancel["client_oid"] == "g001c005e12345"
