"""Tests for grid poller scheduling and runtime state helpers."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

from app.services.grid.poller import GridFillPoller
from app.services.grid.resting_orders_repo import GridRestingOrder
from app.services.grid.runtime_state import load_grid_resting_state


def test_load_grid_resting_state_initial_flag():
    tc = {"script_runtime_state": {"grid_resting": {"initial_market_done": True}}}
    assert load_grid_resting_state(tc).get("initial_market_done") is True


def test_poller_select_orders_respects_interval():
    poller = GridFillPoller()
    poller._min_order_interval = 100.0
    order = GridRestingOrder(id=1, strategy_id=99, symbol="BTC/USDT", status="open")
    poller._last_poll_by_order[1] = time.time()
    with patch("app.services.grid.poller.get_runner", return_value=MagicMock()):
        selected = poller._select_orders([order])
    assert selected == []


def test_poller_select_orders_prioritizes_partial():
    poller = GridFillPoller()
    poller._min_order_interval = 0.0
    partial = GridRestingOrder(id=1, strategy_id=1, symbol="BTC/USDT", status="partial")
    open_o = GridRestingOrder(id=2, strategy_id=1, symbol="BTC/USDT", status="open")
    runner = MagicMock()
    with patch("app.services.grid.poller.get_runner", return_value=runner):
        selected = poller._select_orders([open_o, partial])
    assert selected[0].status == "partial"


def test_poller_idempotent_skip_when_processed():
    poller = GridFillPoller()
    runner = MagicMock()
    order = GridRestingOrder(
        id=10,
        strategy_id=1,
        symbol="BTC/USDT",
        quantity=1.0,
        processed_fill_qty=1.0,
        filled_quantity=1.0,
        status="open",
    )
    with patch("app.services.grid.poller.query_grid_order_fill", return_value=(1.0, 100.0, "filled")):
        with patch.object(poller._repo, "update_status") as upd:
            poller._poll_order(runner, MagicMock(), order, "swap")
            upd.assert_called()
            args = upd.call_args
        assert args[0][0] == 10
