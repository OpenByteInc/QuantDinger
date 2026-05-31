"""Tests for local position snapshot helpers used by live sync and UI."""

from app.services.live_trading.records import (
    lookup_exchange_side_qty,
    normalize_strategy_symbol,
    strategy_allowed_symbols,
)


def test_strategy_allowed_symbols_includes_trading_config_symbol():
    sc = {
        "symbol": "",
        "trading_config": {"symbol": "SOL/USDT", "symbol_list": []},
    }
    assert strategy_allowed_symbols(sc) == {"SOL/USDT"}


def test_strategy_allowed_symbols_includes_row_and_list():
    sc = {
        "symbol": "BTC/USDT",
        "trading_config": {
            "symbol": "ETH/USDT",
            "symbol_list": ["Crypto:SOL/USDT", "BNBUSDT"],
        },
    }
    allowed = strategy_allowed_symbols(sc)
    assert "BTC/USDT" in allowed
    assert "ETH/USDT" in allowed
    assert "SOL/USDT" in allowed
    assert normalize_strategy_symbol("BNBUSDT").upper() in allowed


def test_lookup_exchange_side_qty_symbol_aliases():
    exch = {"SOL/USDT": {"long": 1.5, "short": 0.0}}
    assert lookup_exchange_side_qty(exch, "SOLUSDT", "long") == 1.5
    assert lookup_exchange_side_qty(exch, "SOL/USDT", "short") == 0.0
