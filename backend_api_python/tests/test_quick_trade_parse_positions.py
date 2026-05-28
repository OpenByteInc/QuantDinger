"""Tests for Quick Trade position side parsing (long vs short display)."""

from unittest.mock import MagicMock, patch

from app.routes.quick_trade import _infer_position_side_from_row, _parse_positions


def test_binance_hedge_short():
    row = {"positionSide": "SHORT", "positionAmt": "109", "symbol": "APTUSDT"}
    assert _infer_position_side_from_row(row) == "short"


def test_binance_one_way_short_signed_amt():
    row = {"positionSide": "BOTH", "positionAmt": "-109", "symbol": "APTUSDT"}
    assert _infer_position_side_from_row(row) == "short"


def test_bybit_sell_side():
    row = {"side": "Sell", "size": "109", "symbol": "APTUSDT"}
    assert _infer_position_side_from_row(row) == "short"


def test_bybit_position_idx_short():
    row = {"positionIdx": 2, "size": "109", "symbol": "APTUSDT"}
    assert _infer_position_side_from_row(row) == "short"


def test_gate_negative_contract_size():
    row = {"size": -50, "positionAmt": 109.0, "symbol": "APT_USDT"}
    assert _infer_position_side_from_row(row) == "short"


def test_gate_position_side_short():
    row = {"size": -50, "positionAmt": 109.0, "positionSide": "SHORT", "symbol": "APT_USDT"}
    assert _infer_position_side_from_row(row) == "short"


def test_htx_direction_sell():
    row = {"volume": 10, "direction": "sell", "contract_code": "APT-USDT"}
    assert _infer_position_side_from_row(row) == "short"


def test_parse_positions_gate_short_row():
    raw = [{"size": -50, "positionAmt": 109.0, "positionSide": "SHORT", "contract": "APT_USDT"}]
    out = _parse_positions(raw)
    assert len(out) == 1
    assert out[0]["side"] == "short"
    assert out[0]["size"] == 109.0


def test_okx_net_mode_short_negative_pos():
    """OKX 买卖模式: posSide=net + pos<0 必须识别为空仓."""
    row = {
        "instId": "APT-USDT-SWAP",
        "posSide": "net",
        "pos": "-109",
        "avgPx": "0.9129",
        "upl": "0.1526",
    }
    assert _infer_position_side_from_row(row) == "short"


def test_okx_net_mode_long_positive_pos():
    row = {"posSide": "net", "pos": "50", "instId": "APT-USDT-SWAP"}
    assert _infer_position_side_from_row(row) == "long"


def test_okx_long_short_mode_short():
    row = {"posSide": "short", "pos": "109", "instId": "APT-USDT-SWAP"}
    assert _infer_position_side_from_row(row) == "short"


def test_parse_positions_okx_net_mode_wrapper():
    raw = {
        "code": "0",
        "data": [
            {
                "instId": "APT-USDT-SWAP",
                "posSide": "net",
                "pos": "-109",
                "avgPx": "0.9129",
                "upl": "0.1526",
                "markPx": "0.9115",
            }
        ],
    }
    out = _parse_positions(raw)
    assert len(out) == 1
    assert out[0]["side"] == "short"
    assert out[0]["size"] == 109.0


def test_normalize_okx_positions_raw_net_short():
    from app.routes.quick_trade import _normalize_okx_positions_raw

    raw = {"data": [{"posSide": "net", "pos": "-109"}]}
    norm = _normalize_okx_positions_raw(raw)
    assert norm["data"][0]["positionSide"] == "SHORT"


def test_parse_positions_bybit_list_wrapper():
    raw = {
        "result": {
            "list": [
                {
                    "symbol": "APTUSDT",
                    "side": "Sell",
                    "size": "109",
                    "entryPrice": "0.9129",
                    "unrealisedPnl": "0.15",
                }
            ]
        }
    }
    out = _parse_positions(raw)
    assert len(out) == 1
    assert out[0]["side"] == "short"


def test_parse_positions_spot_bal_row():
    """Spot wallet rows use bal/availBal instead of pos/positionAmt."""
    raw = {
        "data": [
            {
                "symbol": "APT/USDT",
                "bal": 120.5,
                "availBal": 118.0,
            }
        ]
    }
    out = _parse_positions(raw)
    assert len(out) == 1
    assert out[0]["side"] == "long"
    assert out[0]["size"] == 120.5


def test_fetch_spot_holdings_raw_empty():
    from app.routes.quick_trade import _fetch_spot_holdings_raw

    client = MagicMock()
    with patch(
        "app.services.live_trading.spot_sizing.get_spot_base_holding",
        return_value={"total": 0.0, "available": 0.0},
    ):
        raw = _fetch_spot_holdings_raw(client, symbol="APT/USDT")
    assert raw == {"data": []}


def test_fetch_spot_holdings_raw_with_balance():
    from app.routes.quick_trade import _fetch_spot_holdings_raw

    client = MagicMock()
    with patch(
        "app.services.live_trading.spot_sizing.get_spot_base_holding",
        return_value={"total": 50.0, "available": 48.5, "avg_cost": 1.25},
    ):
        raw = _fetch_spot_holdings_raw(client, symbol="APT/USDT")
    out = _parse_positions(raw)
    assert len(out) == 1
    assert out[0]["symbol"] == "APT/USDT"
    assert out[0]["size"] == 50.0
    assert out[0]["entry_price"] == 1.25


def test_enrich_spot_positions_computes_pnl():
    from app.routes.quick_trade import _enrich_spot_positions

    client = MagicMock()
    with patch(
        "app.routes.quick_trade._quick_trade_spot_avg_entry_price",
        return_value=2.0,
    ), patch(
        "app.services.live_trading.spot_sizing.fetch_spot_last_price",
        return_value=2.5,
    ):
        out = _enrich_spot_positions(
            [{"symbol": "APT/USDT", "side": "long", "size": 10.0, "entry_price": 0, "mark_price": 0}],
            client=client,
            symbol="APT/USDT",
            user_id=1,
            credential_id=1,
            market_type="spot",
        )
    assert len(out) == 1
    assert out[0]["entry_price"] == 2.0
    assert out[0]["mark_price"] == 2.5
    assert abs(out[0]["unrealized_pnl"] - 5.0) < 1e-9
