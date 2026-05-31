from decimal import Decimal
from unittest.mock import MagicMock

from app.services.live_trading.binance_spot import BinanceSpotClient
from app.services.live_trading.bitget_spot import BitgetSpotClient
from app.services.live_trading.spot_sizing import (
    clamp_spot_close_quantity,
    prepare_spot_live_order_sizes,
    scale_spot_open_notional,
)


def test_scale_spot_open_notional_default_buffer():
    assert scale_spot_open_notional(1000.0) == 995.0


def test_clamp_spot_close_uses_free_and_normalize():
    client = MagicMock(spec=BinanceSpotClient)
    client.get_account.return_value = {"balances": [{"asset": "BTC", "free": "0.99"}]}
    client._normalize_quantity.return_value = (Decimal("0.98"), 2)
    final, meta = clamp_spot_close_quantity(client, symbol="BTC/USDT", requested_qty=1.0)
    assert final == 0.98
    assert meta.get("adjusted") is True
    assert meta.get("exchange_free") == 0.99


def test_prepare_spot_live_order_bitget_market_buy_uses_quote():
    client = MagicMock(spec=BitgetSpotClient)
    client._normalize_quote_size.return_value = (Decimal("50"), 2)
    client._normalize_base_size.return_value = (Decimal("100"), 0)
    base, quote, uses_quote = prepare_spot_live_order_sizes(
        client,
        symbol="DOGE/USDT",
        side="buy",
        reduce_only=False,
        base_qty=1000.0,
        ref_price=0.05,
    )
    assert uses_quote is True
    assert quote == 50.0
    assert base == 1000.0


def test_clamp_spot_close_no_change_when_within_free():
    client = MagicMock(spec=BinanceSpotClient)
    client.get_account.return_value = {"balances": [{"asset": "ETH", "free": "10"}]}
    client._normalize_quantity.return_value = (Decimal("0.5"), 1)
    final, meta = clamp_spot_close_quantity(client, symbol="ETH/USDT", requested_qty=0.5, safety_ratio=1.0)
    assert final == 0.5
    assert "adjusted" not in meta or meta.get("adjusted") is not True
