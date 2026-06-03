"""Unit tests for exchange-documented grid fill unit conversion."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.services.grid.fill_units import extract_grid_fill_base_qty, parse_grid_order_fill
from app.services.live_trading.bitget import BitgetMixClient
from app.services.live_trading.gate import GateUsdtFuturesClient
from app.services.live_trading.htx import HtxClient
from app.services.live_trading.okx import OkxClient


def test_okx_swap_accfillsz_times_ctval():
    client = MagicMock()
    client.__class__ = OkxClient
    client.get_instrument.return_value = {"ctVal": "0.01"}
    qty = extract_grid_fill_base_qty(
        client,
        symbol="BTC/USDT",
        market_type="swap",
        exchange_config={},
        data={"state": "filled", "accFillSz": "55", "avgPx": "65000"},
    )
    assert qty == pytest.approx(0.55)


def test_bitget_basevolume_is_base_coin():
    client = MagicMock()
    client.__class__ = BitgetMixClient
    qty = extract_grid_fill_base_qty(
        client,
        symbol="BTC/USDT",
        market_type="swap",
        exchange_config={"product_type": "USDT-FUTURES"},
        data={"state": "filled", "baseVolume": "0.0042", "priceAvg": "73000"},
    )
    assert qty == pytest.approx(0.0042)


def test_gate_filled_size_times_quanto_multiplier():
    client = MagicMock()
    client.__class__ = GateUsdtFuturesClient
    client.get_contract.return_value = {"quanto_multiplier": "0.0001"}
    qty = extract_grid_fill_base_qty(
        client,
        symbol="BTC/USDT",
        market_type="swap",
        exchange_config={},
        data={"status": "finished", "filled_size": "100", "fill_price": "65000"},
    )
    assert qty == pytest.approx(0.01)


def test_htx_trade_volume_times_contract_size():
    client = MagicMock()
    client.__class__ = HtxClient
    client.get_contract_info.return_value = {"contract_size": "0.001"}
    qty = extract_grid_fill_base_qty(
        client,
        symbol="BTC/USDT",
        market_type="swap",
        exchange_config={},
        data={"status": 6, "trade_volume": "100", "trade_avg_price": "48555.6"},
    )
    assert qty == pytest.approx(0.1)


def test_parse_grid_order_fill_okx_integration():
    client = MagicMock()
    client.__class__ = OkxClient
    client.get_instrument.return_value = {"ctVal": "0.01"}
    filled, avg, status = parse_grid_order_fill(
        client,
        symbol="BTC/USDT",
        market_type="swap",
        exchange_config={},
        data={"state": "filled", "accFillSz": "5", "avgPx": "65000.1"},
    )
    assert status == "filled"
    assert filled == pytest.approx(0.05)
    assert avg == pytest.approx(65000.1)
