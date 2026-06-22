"""Tests for HTX wait_for_fill contract conversion and worker fill recovery."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.services.live_trading.fill_recovery import try_recover_zero_fill
from app.services.live_trading.htx import HtxClient


def _htx_client(*, market_type: str = "swap") -> HtxClient:
    client = HtxClient.__new__(HtxClient)
    client.market_type = market_type
    client._contract_cache = {}
    client._contract_cache_ttl_sec = 300.0
    return client


def test_htx_wait_for_fill_swap_multiplies_contract_size():
    client = _htx_client(market_type="swap")
    client.get_contract_info = MagicMock(return_value={"contract_size": "0.001"})
    client.get_order = MagicMock(
        return_value={"status": "6", "trade_volume": "8", "trade_avg_price": "67500"}
    )

    out = client.wait_for_fill(
        symbol="BTC/USDT",
        order_id="oid-1",
        max_wait_sec=0.01,
        poll_interval_sec=0.01,
    )
    assert out["filled"] == pytest.approx(0.008)
    assert out["avg_price"] == pytest.approx(67500.0)


def test_htx_wait_for_fill_spot_uses_field_amount():
    client = _htx_client(market_type="spot")
    client.get_contract_info = MagicMock(return_value={"contract_size": "0.001"})
    client.get_order = MagicMock(
        return_value={"status": "filled", "field-amount": "0.02", "field-cash-amount": "1300.4"}
    )

    out = client.wait_for_fill(
        symbol="BTC/USDT",
        order_id="oid-1",
        max_wait_sec=0.01,
        poll_interval_sec=0.01,
    )
    assert out["filled"] == pytest.approx(0.02)
    assert out["avg_price"] == pytest.approx(65020.0)


def test_try_recover_zero_fill_order_requery():
    client = MagicMock()
    with patch(
        "app.services.live_trading.fill_recovery.query_grid_order_fill",
        return_value=(0.005, 72000.0, "filled"),
    ):
        filled, avg, src = try_recover_zero_fill(
            client,
            symbol="BTC/USDT",
            market_type="swap",
            exchange_config={},
            exchange_order_id="ex-1",
            client_order_id="coid-1",
            requested_qty=0.005,
            signal_type="open_long",
            pos_side="long",
            pre_position_qty=0.0,
            ref_price=71000.0,
        )
    assert src == "order_requery"
    assert filled == pytest.approx(0.005)
    assert avg == pytest.approx(72000.0)


def test_try_recover_zero_fill_position_delta():
    client = MagicMock()
    with patch(
        "app.services.live_trading.fill_recovery.query_grid_order_fill",
        return_value=(0.0, 0.0, "unknown"),
    ), patch(
        "app.services.live_trading.fill_recovery.query_exchange_position_size",
        return_value=0.005,
    ):
        filled, avg, src = try_recover_zero_fill(
            client,
            symbol="BTC/USDT",
            market_type="swap",
            exchange_config={},
            exchange_order_id="ex-1",
            client_order_id="",
            requested_qty=0.005,
            signal_type="open_long",
            pos_side="long",
            pre_position_qty=0.0,
            ref_price=71000.0,
        )
    assert src == "position_delta"
    assert filled == pytest.approx(0.005)
    assert avg == pytest.approx(71000.0)


def test_try_recover_zero_fill_skips_close_signals():
    client = MagicMock()
    with patch(
        "app.services.live_trading.fill_recovery.query_grid_order_fill",
        return_value=(0.0, 0.0, "unknown"),
    ):
        filled, avg, src = try_recover_zero_fill(
            client,
            symbol="BTC/USDT",
            market_type="swap",
            exchange_config={},
            exchange_order_id="ex-1",
            client_order_id="",
            requested_qty=0.005,
            signal_type="close_long",
            pos_side="long",
            pre_position_qty=0.005,
            ref_price=71000.0,
        )
    assert filled == 0.0
    assert src == ""
