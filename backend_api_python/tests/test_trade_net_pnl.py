"""Tests for FIFO open-commission allocation and net P&L enrichment."""

from __future__ import annotations

from app.utils.trade_net_pnl import (
    allocate_open_commissions_fifo,
    enrich_trades_net_pnl,
    net_pnl_for_equity_step,
    net_realized_pnl,
)


def test_allocate_open_commission_single_round_trip():
    trades = [
        {"id": 1, "symbol": "BTC/USDT", "type": "open_long", "amount": 0.1, "commission": 5.0, "profit": None, "created_at": 100},
        {"id": 2, "symbol": "BTC/USDT", "type": "close_long", "amount": 0.1, "commission": 3.0, "profit": 100.0, "created_at": 200},
    ]
    alloc = allocate_open_commissions_fifo(trades)
    assert alloc[2] == 5.0


def test_enrich_trades_net_pnl_full_round_trip():
    trades = [
        {"id": 1, "symbol": "BTC/USDT", "type": "open_long", "amount": 0.1, "commission": 5.0, "profit": None, "created_at": 100},
        {"id": 2, "symbol": "BTC/USDT", "type": "close_long", "amount": 0.1, "commission": 3.0, "profit": 100.0, "created_at": 200},
    ]
    enrich_trades_net_pnl(trades)
    close = trades[1]
    assert close["profit_gross"] == 100.0
    assert close["open_commission_allocated"] == 5.0
    assert close["profit"] == 92.0
    assert close["net_pnl"] == 92.0
    assert close["total_commission"] == 8.0


def test_partial_close_fifo_across_two_opens():
    trades = [
        {"id": 1, "symbol": "ETH/USDT", "type": "open_long", "amount": 1.0, "commission": 10.0, "profit": None, "created_at": 1},
        {"id": 2, "symbol": "ETH/USDT", "type": "add_long", "amount": 1.0, "commission": 8.0, "profit": None, "created_at": 2},
        {"id": 3, "symbol": "ETH/USDT", "type": "reduce_long", "amount": 1.5, "commission": 2.0, "profit": 30.0, "created_at": 3},
    ]
    enrich_trades_net_pnl(trades)
    close = trades[2]
    # 1.0 @ 10/1 + 0.5 @ 8/1 = 10 + 4 = 14 open fee
    assert close["open_commission_allocated"] == 14.0
    assert close["profit"] == 30.0 - 2.0 - 14.0


def test_net_pnl_for_equity_step_open_and_close():
    open_row = {"type": "open_long", "profit": None, "commission": 4.0}
    assert net_pnl_for_equity_step(open_row) == -4.0

    close_row = {
        "type": "close_long",
        "profit": 90.0,
        "profit_gross": 100.0,
        "net_pnl": 90.0,
        "commission": 3.0,
        "open_commission_allocated": 7.0,
    }
    assert net_pnl_for_equity_step(close_row) == 90.0
    assert net_realized_pnl(close_row, open_commission=7.0) == 90.0


def test_short_leg_does_not_cross_with_long():
    trades = [
        {"id": 1, "symbol": "BTC/USDT", "type": "open_long", "amount": 1.0, "commission": 5.0, "profit": None, "created_at": 1},
        {"id": 2, "symbol": "BTC/USDT", "type": "open_short", "amount": 1.0, "commission": 6.0, "profit": None, "created_at": 2},
        {"id": 3, "symbol": "BTC/USDT", "type": "close_long", "amount": 1.0, "commission": 1.0, "profit": 20.0, "created_at": 3},
    ]
    enrich_trades_net_pnl(trades)
    assert trades[2]["open_commission_allocated"] == 5.0
    assert trades[2]["profit"] == 20.0 - 1.0 - 5.0
