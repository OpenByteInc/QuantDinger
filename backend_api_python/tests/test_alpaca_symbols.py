from app.services.alpaca_trading.symbols import infer_asset_class, normalize_symbol, parse_symbol


def test_btc_usdt_maps_to_usd_for_crypto():
    sym, ac = parse_symbol("BTC/USDT", market_hint="Crypto")
    assert ac == "crypto"
    assert sym == "BTC/USD"


def test_brk_b_is_us_equity():
    sym, ac = parse_symbol("BRK/B", market_hint="USStock")
    assert ac == "us_equity"
    assert sym == "BRK.B"


def test_aapl_stays_equity_with_slash_quote_misdetect():
    sym, ac = parse_symbol("AAPL", market_hint="USStock")
    assert ac == "us_equity"
    assert sym == "AAPL"


def test_exchange_prefix_stripped():
    sym, ac = parse_symbol("NASDAQ:AAPL", market_hint="USStock")
    assert sym == "AAPL"
    assert ac == "us_equity"


def test_infer_crypto_without_hint():
    assert infer_asset_class("ETH/USD") == "crypto"
    assert infer_asset_class("BRK/B") == "us_equity"
