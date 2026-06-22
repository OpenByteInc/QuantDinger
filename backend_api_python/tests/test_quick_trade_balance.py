"""Quick-trade balance parsing for Bybit / Gate / HTX."""

from app.routes.quick_trade import _parse_balance
from app.services.live_trading import htx_v5


def test_parse_bybit_unified_wallet():
    raw = {
        "retCode": 0,
        "result": {
            "list": [{
                "accountType": "UNIFIED",
                "totalAvailableBalance": "1234.56",
                "totalEquity": "1500",
                "coin": [{"coin": "USDT", "walletBalance": "0", "availableToWithdraw": "0", "availableBalance": "0"}],
            }]
        },
    }
    out = _parse_balance(raw, "bybit", "swap")
    assert out["available"] == 1234.56
    assert out["total"] == 1500.0


def test_parse_bybit_usdt_coin_fallback():
    raw = {
        "retCode": 0,
        "result": {
            "list": [{
                "accountType": "CONTRACT",
                "totalAvailableBalance": "",
                "totalEquity": "",
                "coin": [{
                    "coin": "USDT",
                    "walletBalance": "88.5",
                    "availableBalance": "88.5",
                    "availableToWithdraw": "",
                }],
            }]
        },
    }
    out = _parse_balance(raw, "bybit", "swap")
    assert out["available"] == 88.5
    assert out["total"] == 88.5


def test_parse_gate_futures_account():
    raw = {
        "total": "1000",
        "available": "750.25",
        "unrealised_pnl": "0",
        "position_margin": "100",
        "order_margin": "50",
    }
    out = _parse_balance(raw, "gate", "swap")
    assert out["available"] == 750.25
    assert out["total"] == 1000.0


def test_parse_gate_spot_accounts_list():
    raw = [
        {"currency": "BTC", "available": "0.1", "locked": "0"},
        {"currency": "USDT", "available": "321", "locked": "9"},
    ]
    out = _parse_balance(raw, "gate", "spot")
    assert out["available"] == 321.0
    assert out["total"] == 330.0


def test_parse_htx_swap_normalized_list():
    raw = htx_v5.normalize_balance({
        "code": 200,
        "data": [
            {"currency": "USDT", "available": "456.78", "equity": "500"},
        ],
    })
    out = _parse_balance(raw, "htx", "swap")
    assert out["available"] == 456.78
    assert out["total"] == 500.0


def test_parse_htx_spot_balance():
    raw = {
        "status": "ok",
        "data": {
            "list": [
                {"currency": "usdt", "type": "trade", "balance": "200"},
            ],
        },
    }
    out = _parse_balance(raw, "htx", "spot")
    assert out["available"] == 200.0
    assert out["total"] == 200.0


def test_htx_v5_normalize_balance_data_list():
    out = htx_v5.normalize_balance({
        "code": 200,
        "data": [{"currency": "USDT", "available": "99", "equity": "100"}],
    })
    assert len(out["data"]) == 1
    assert out["data"][0]["margin_available"] == 99.0
