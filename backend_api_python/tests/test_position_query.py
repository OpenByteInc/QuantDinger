"""Tests for close-quantity resolution (DB + exchange fallback)."""
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
