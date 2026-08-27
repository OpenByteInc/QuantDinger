from app.services.futu_trading.mappers import (
    classify_futu_error,
    is_terminal_status,
    normalize_order_status,
    order_row_to_raw,
    position_row_to_dict,
    side_from_futu,
    side_to_futu,
)


def test_normalize_order_status():
    assert normalize_order_status("SUBMITTED") == "submitted"
    assert normalize_order_status("FILLED_PART") == "partially_filled"
    assert normalize_order_status("FILLED_ALL") == "filled"
    assert normalize_order_status("CANCELLED_ALL") == "cancelled"
    assert normalize_order_status("FAILED") == "rejected"
    assert is_terminal_status("filled")
    assert is_terminal_status("cancelled")
    assert not is_terminal_status("submitted")


def test_side_mapping():
    assert side_to_futu("buy") == "BUY"
    assert side_to_futu("open_long") == "BUY"
    assert side_to_futu("sell") == "SELL"
    assert side_from_futu("BUY") == "buy"
    assert side_from_futu("TrdSide.SELL") == "sell"


def test_order_row_to_raw():
    raw = order_row_to_raw({
        "order_id": "12345",
        "code": "HK.00700",
        "order_status": "FILLED_PART",
        "dealt_qty": 100,
        "dealt_avg_price": 350.5,
        "qty": 200,
        "price": 351.0,
        "trd_side": "BUY",
        "remark": "futu-1-2",
        "currency": "HKD",
        "commission": 1.2,
    })
    assert raw["order_id"] == "12345"
    assert raw["status"] == "partially_filled"
    assert raw["filled"] == 100
    assert raw["avg_price"] == 350.5
    assert raw["side"] == "buy"
    assert raw["client_order_id"] == "futu-1-2"
    assert raw["commission_ccy"] == "HKD"


def test_position_row_to_dict():
    pos = position_row_to_dict({
        "code": "HK.00700",
        "qty": 500,
        "cost_price": 320.0,
        "market_val": 180000,
        "currency": "HKD",
    })
    assert pos["symbol"] == "00700.HK"
    assert pos["quantity"] == 500
    assert pos["side"] == "long"
    assert pos["avgCost"] == 320.0


def test_classify_futu_error():
    code, _ = classify_futu_error("no right to get the quote")
    assert code == "FUTU_QUOTE_PERMISSION_DENIED"
    code, _ = classify_futu_error("unlock trade first")
    assert code == "FUTU_TRADE_LOCKED"
    code, _ = classify_futu_error("invalid lot size qty")
    assert code == "FUTU_INVALID_LOT_SIZE"
