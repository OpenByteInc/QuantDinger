from app.data_sources.crypto import resolve_ccxt_for_live_trading, resolve_crypto_venue


def test_resolve_crypto_venue_swap_from_trading_config():
    ex, mt = resolve_crypto_venue(
        exchange_config={"exchange_id": "binance"},
        trading_config={"market_type": "swap"},
    )
    assert ex == "binance"
    assert mt == "swap"


def test_resolve_crypto_venue_defaults_to_settings_exchange():
    ex, mt = resolve_crypto_venue(
        exchange_config={},
        trading_config={"market_type": "spot"},
    )
    assert ex
    assert mt == "spot"


def test_binance_swap_maps_to_usdm_ccxt():
    ccxt_id, _opts = resolve_ccxt_for_live_trading("binance", "swap")
    assert ccxt_id == "binanceusdm"


def test_binance_spot_maps_to_spot_ccxt():
    ccxt_id, _opts = resolve_ccxt_for_live_trading("binance", "spot")
    assert ccxt_id == "binance"
