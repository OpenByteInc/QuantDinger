from app.services.futu_trading.symbols import (
    format_display_symbol,
    from_futu_code,
    infer_market_category,
    parse_symbol,
    to_futu_code,
)


def test_hk_symbol_to_futu():
    assert to_futu_code("00700.HK") == "HK.00700"
    assert to_futu_code("700.HK") == "HK.00700"
    assert to_futu_code("00700", "HKStock") == "HK.00700"
    assert to_futu_code("HK.00700") == "HK.00700"


def test_us_symbol_to_futu():
    assert to_futu_code("AAPL", "USStock") == "US.AAPL"
    assert to_futu_code("US.AAPL") == "US.AAPL"
    assert to_futu_code("AAPL.US") == "US.AAPL"


def test_from_futu_code_roundtrip():
    display, market = from_futu_code("HK.00700")
    assert display == "00700.HK"
    assert market == "HKStock"
    assert to_futu_code(display, market) == "HK.00700"

    display, market = from_futu_code("US.AAPL")
    assert display == "AAPL"
    assert market == "USStock"


def test_infer_market_and_parse():
    assert infer_market_category("00700.HK") == "HKStock"
    assert infer_market_category("AAPL") == "USStock"
    code, market = parse_symbol("0700.HK")
    assert code == "HK.00700"
    assert market == "HKStock"
    assert format_display_symbol("HK.00700") == "00700.HK"
