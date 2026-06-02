"""OKX account snapshot spot wallet parsing."""

from app.services.live_trading.account_snapshot import (
    _fetch_okx_snapshot,
    _parse_okx_spot_balances,
)


def test_parse_okx_spot_balances_from_wallet():
    raw = {
        "code": "0",
        "data": [
            {
                "details": [
                    {"ccy": "USDT", "eq": "1000", "availBal": "900", "openAvgPx": "1"},
                    {"ccy": "ETH", "eq": "0.5", "availBal": "0.48", "openAvgPx": "2000.5"},
                    {"ccy": "BTC", "eq": "0", "availBal": "0", "frozenBal": "0"},
                ]
            }
        ],
    }
    rows = _parse_okx_spot_balances(raw)
    syms = {r["symbol"] for r in rows}
    assert "USDT" in syms
    assert "ETH/USDT" in syms
    assert "BTC/USDT" not in syms
    eth = next(r for r in rows if r["symbol"] == "ETH/USDT")
    assert eth["size"] == 0.5
    assert eth["entry_price"] == 2000.5
    assert eth["market_type"] == "spot"


def test_fetch_okx_snapshot_uses_balance_for_spot():
    from app.services.live_trading.okx import OkxClient

    class FakeOkx(OkxClient):
        def __init__(self):
            pass

        def get_positions(self, *, inst_type: str = "SWAP", inst_id: str = ""):
            assert inst_type == "SWAP"
            return {"data": [{"instId": "ETH-USDT-SWAP", "posSide": "net", "pos": "1", "avgPx": "2000"}]}

        def get_balance(self):
            return {
                "data": [
                    {
                        "details": [
                            {"ccy": "ETH", "eq": "0.12", "availBal": "0.12", "openAvgPx": "2100"},
                        ]
                    }
                ]
            }

        def _signed_request(self, method, path, params=None):
            return {"data": []}

    errors = []
    swap_pos, spot_pos, orders = _fetch_okx_snapshot(FakeOkx(), "okx", errors)
    assert not errors
    assert len(swap_pos) == 1
    assert len(spot_pos) == 1
    assert spot_pos[0]["symbol"] == "ETH/USDT"
    assert spot_pos[0]["size"] == 0.12
