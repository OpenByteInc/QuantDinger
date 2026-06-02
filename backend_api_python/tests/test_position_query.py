"""Tests for close-quantity resolution (DB + exchange fallback)."""
import pytest
from unittest.mock import MagicMock

from app.services.live_trading.position_query import (
    resolve_reduce_only_quantity,
    symbols_equivalent,
)


def test_symbols_equivalent_compact_and_slash():
    assert symbols_equivalent("DOGEUSDT", "DOGE/USDT")
    assert symbols_equivalent("btc/usdt", "BTCUSDT")
    assert not symbols_equivalent("ETH/USDT", "DOGE/USDT")


def test_resolve_uses_exchange_when_db_missing(monkeypatch):
    monkeypatch.setattr(
        "app.services.live_trading.position_query.fetch_position_size_for_side",
        lambda *_a, **_k: 0.0,
    )
    monkeypatch.setattr(
        "app.services.live_trading.position_query.query_exchange_position_size",
        lambda **_k: 99.0,
    )
    amount, meta = resolve_reduce_only_quantity(
        strategy_id=1,
        symbol="DOGE/USDT",
        pos_side="short",
        requested_amount=0.0,
        client=MagicMock(),
        market_type="swap",
        exchange_config={},
    )
    assert amount == 99.0
    assert meta.get("filled_from") == "exchange"
    assert meta.get("db_missing") is True


def test_resolve_caps_to_db_when_smaller(monkeypatch):
    monkeypatch.setattr(
        "app.services.live_trading.position_query.fetch_position_size_for_side",
        lambda *_a, **_k: 50.0,
    )
    monkeypatch.setattr(
        "app.services.live_trading.position_query.query_exchange_position_size",
        lambda **_k: 99.0,
    )
    amount, meta = resolve_reduce_only_quantity(
        strategy_id=1,
        symbol="DOGE/USDT",
        pos_side="short",
        requested_amount=80.0,
        client=MagicMock(),
        market_type="swap",
        exchange_config={},
    )
    assert amount == 50.0
    assert meta.get("capped_by") == "db"


def test_okx_net_mode_long_position(monkeypatch):
    from app.services.live_trading.okx import OkxClient
    from app.services.live_trading.position_query import query_exchange_position_size

    class FakeOkx(OkxClient):
        def __init__(self):
            pass

        def get_positions(self, *, inst_id: str = "", inst_type: str = "SWAP"):
            return {
                "data": [
                    {
                        "instId": inst_id,
                        "posSide": "net",
                        "pos": "10",
                        "ctVal": "0.01",
                    }
                ]
            }

    qty = query_exchange_position_size(
        client=FakeOkx(),
        symbol="BNB/USDT",
        pos_side="long",
        market_type="swap",
    )
    assert qty == pytest.approx(0.1)


def test_okx_net_mode_short_ignored_for_long_query(monkeypatch):
    from app.services.live_trading.okx import OkxClient
    from app.services.live_trading.position_query import query_exchange_position_size

    class FakeOkx(OkxClient):
        def __init__(self):
            pass

        def get_positions(self, *, inst_id: str = "", inst_type: str = "SWAP"):
            return {
                "data": [
                    {
                        "instId": inst_id,
                        "posSide": "net",
                        "pos": "-10",
                        "ctVal": "0.01",
                    }
                ]
            }

    qty = query_exchange_position_size(
        client=FakeOkx(),
        symbol="BNB/USDT",
        pos_side="long",
        market_type="swap",
    )
    assert qty == 0.0


def test_binance_one_way_long_query(monkeypatch):
    from app.services.live_trading.binance import BinanceFuturesClient
    from app.services.live_trading.position_query import query_exchange_position_size

    class FakeBinance(BinanceFuturesClient):
        def __init__(self):
            pass

        def get_positions(self):
            return [
                {"symbol": "BNBUSDT", "positionSide": "BOTH", "positionAmt": "2.5"},
            ]

    qty = query_exchange_position_size(
        client=FakeBinance(),
        symbol="BNB/USDT",
        pos_side="long",
        market_type="swap",
    )
    assert qty == pytest.approx(2.5)


def test_binance_one_way_short_not_returned_as_long():
    from app.services.live_trading.binance import BinanceFuturesClient
    from app.services.live_trading.position_query import query_exchange_position_size

    class FakeBinance(BinanceFuturesClient):
        def __init__(self):
            pass

        def get_positions(self):
            return [
                {"symbol": "BNBUSDT", "positionSide": "BOTH", "positionAmt": "-2.5"},
            ]

    assert query_exchange_position_size(
        client=FakeBinance(),
        symbol="BNB/USDT",
        pos_side="long",
        market_type="swap",
    ) == 0.0
    assert query_exchange_position_size(
        client=FakeBinance(),
        symbol="BNB/USDT",
        pos_side="short",
        market_type="swap",
    ) == pytest.approx(2.5)


def test_bitget_one_way_total_without_hold_side():
    from app.services.live_trading.bitget import BitgetMixClient
    from app.services.live_trading.position_query import query_exchange_position_size

    class FakeBitget(BitgetMixClient):
        def __init__(self):
            pass

        def get_positions(self, *, product_type: str = "USDT-FUTURES", symbol: str = ""):
            return {
                "data": [
                    {"symbol": "BNBUSDT", "side": "buy", "total": "1.8"},
                ]
            }

    qty = query_exchange_position_size(
        client=FakeBitget(),
        symbol="BNB/USDT",
        pos_side="long",
        market_type="swap",
    )
    assert qty == pytest.approx(1.8)
