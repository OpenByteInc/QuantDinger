from flask import Flask
from app.routes import strategy_ledger_routes
from app.services import virtual_trading


def test_virtual_ledger_allocates_open_and_close_fees_to_exit_row(monkeypatch):
    monkeypatch.setattr(
        virtual_trading,
        "list_virtual_trades",
        lambda _strategy_id: [
            {
                "id": 2,
                "symbol": "BTC/USDT",
                "symbol_canonical": "BTC/USDT",
                "type": "close_long",
                "side": "long",
                "price": 110,
                "amount": 1,
                "value": 110,
                "commission": 1,
                "commission_quote": 1,
                "profit": 10,
                "reference_price": 110,
                "slippage_quote": 0,
                "created_at": 2,
            },
            {
                "id": 1,
                "symbol": "BTC/USDT",
                "symbol_canonical": "BTC/USDT",
                "type": "open_long",
                "side": "long",
                "price": 100,
                "amount": 1,
                "value": 100,
                "commission": 1,
                "commission_quote": 1,
                "profit": 0,
                "reference_price": 100,
                "slippage_quote": 0,
                "created_at": 1,
            },
        ],
    )

    with Flask(__name__).test_request_context('/strategies/trades?id=7'):
        payload = strategy_ledger_routes._virtual_trades_payload(7, leverage=1, market_type="swap")
    rows = {row["id"]: row for row in payload["trades"]}

    assert rows[1]["profit"] is None
    assert rows[1]["net_pnl"] is None
    assert rows[2]["profit_gross"] == 10
    assert rows[2]["open_commission_allocated"] == 1
    assert rows[2]["close_commission"] == 1
    assert rows[2]["net_pnl"] == 8
    assert rows[2]["profit"] == 8
    assert payload["cost_summary"]["net_realized_pnl"] == 8
    with Flask(__name__).test_request_context('/strategies/trades?id=7&page=1&page_size=1'):
        paged = strategy_ledger_routes._virtual_trades_payload(7, leverage=1, market_type="swap")
    assert paged['items'] == [rows[2]]
    assert paged['total'] == 2
    assert paged['cost_summary'] == payload['cost_summary']
    assert 'trades' not in paged
