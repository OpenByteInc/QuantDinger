"""Spot wallet snapshot parsers for all supported exchanges."""

from app.services.live_trading.spot_wallet_snapshot import (
    _from_binance_spot_account,
    _from_bitget_assets,
    _from_bybit_spot_holdings,
    _from_gate_spot_accounts,
    _from_okx_balance,
    list_spot_wallet_positions,
)


def test_okx_spot_balance_parser():
    raw = {
        "data": [
            {
                "details": [
                    {"ccy": "ETH", "eq": "0.25", "availBal": "0.24", "openAvgPx": "2100"},
                    {"ccy": "USDT", "eq": "500", "availBal": "500"},
                ]
            }
        ]
    }
    rows = _from_okx_balance(raw)
    assert len(rows) == 2
    eth = next(r for r in rows if r["symbol"] == "ETH/USDT")
    assert eth["size"] == 0.25
    assert eth["entry_price"] == 2100.0


def test_binance_spot_balances_parser():
    raw = {
        "balances": [
            {"asset": "BTC", "free": "0.01", "locked": "0.002"},
            {"asset": "USDT", "free": "100", "locked": "0"},
        ]
    }
    rows = _from_binance_spot_account(raw)
    btc = next(r for r in rows if r["symbol"] == "BTC/USDT")
    assert btc["size"] == 0.012


def test_bitget_spot_assets_parser():
    raw = {
        "data": [
            {"coin": "BNB", "available": "1.5", "frozen": "0.1", "averageOpenPrice": "600"},
        ]
    }
    rows = _from_bitget_assets(raw)
    assert rows[0]["symbol"] == "BNB/USDT"
    assert rows[0]["size"] == 1.6


def test_bybit_spot_holdings_parser():
    raw = {"result": {"list": [{"symbol": "ETH/USDT", "bal": "0.33"}]}}
    rows = _from_bybit_spot_holdings(raw)
    assert rows[0]["symbol"] == "ETH/USDT"
    assert rows[0]["size"] == 0.33


def test_gate_spot_accounts_parser():
    raw = [
        {"currency": "SOL", "available": "2", "locked": "0.5"},
    ]
    rows = _from_gate_spot_accounts(raw)
    assert rows[0]["symbol"] == "SOL/USDT"
    assert rows[0]["size"] == 2.5


def test_list_spot_wallet_unknown_client():
    assert list_spot_wallet_positions(object()) == []
