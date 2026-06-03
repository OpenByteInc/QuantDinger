"""Tests for shared exchange position row parsing."""

from app.services.live_trading.position_row_parse import (
    infer_position_side_from_row,
    position_base_qty_for_side,
)


def test_binance_one_way_long():
    row = {"positionSide": "BOTH", "positionAmt": "1.5", "symbol": "BNBUSDT"}
    assert infer_position_side_from_row(row) == "long"
    assert position_base_qty_for_side(row, "long") == 1.5
    assert position_base_qty_for_side(row, "short") == 0.0


def test_binance_one_way_short():
    row = {"positionSide": "BOTH", "positionAmt": "-2.25", "symbol": "BNBUSDT"}
    assert infer_position_side_from_row(row) == "short"
    assert position_base_qty_for_side(row, "short") == 2.25
    assert position_base_qty_for_side(row, "long") == 0.0


def test_okx_net_mode_long():
    row = {"posSide": "net", "pos": "10", "instId": "BNB-USDT-SWAP"}
    assert infer_position_side_from_row(row) == "long"
    assert position_base_qty_for_side(row, "long", contracts_to_base=0.01) == 0.1


def test_net_mode_short():
    row = {"posSide": "net", "pos": "-8", "instId": "BNB-USDT-SWAP"}
    assert infer_position_side_from_row(row) == "short"
    assert position_base_qty_for_side(row, "short", contracts_to_base=0.1) == 0.8


def test_bybit_one_way_buy_side():
    row = {"symbol": "BNBUSDT", "side": "Buy", "size": "3.5", "positionIdx": 0}
    assert infer_position_side_from_row(row) == "long"
    assert position_base_qty_for_side(row, "long") == 3.5


def test_bitget_hedge_hold_side():
    row = {"symbol": "BNBUSDT", "holdSide": "short", "total": "1.2"}
    assert infer_position_side_from_row(row) == "short"
    assert position_base_qty_for_side(row, "short") == 1.2


def test_gate_signed_contracts():
    row = {"contract": "BNB_USDT", "size": -15}
    assert infer_position_side_from_row(row) == "short"
    assert position_base_qty_for_side(row, "short", contracts_to_base=0.01) == 0.15
