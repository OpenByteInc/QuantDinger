from app.services.market.instrument_products import (
    PRODUCT_CRYPTO,
    PRODUCT_STOCK_PERPETUAL,
    PRODUCT_TOKENIZED_EQUITY,
    classify_instrument_product,
)


def test_bybit_xstock_spot_product_keeps_multiplier():
    profile = classify_instrument_product(
        {
            "base": "AAPLX",
            "info": {
                "symbolType": "xstocks",
                "underlyingTicker": "AAPL",
                "xstockMultiplier": "0.01",
            },
        },
        exchange_id="bybit",
        market_type="spot",
        symbol="AAPLX/USDT",
        instrument_id="AAPLXUSDT",
    )

    assert profile.asset_class == "equity"
    assert profile.product_type == PRODUCT_TOKENIZED_EQUITY
    assert profile.api_family == "spot"
    assert profile.underlying_market == "USStock"
    assert profile.underlying_symbol == "AAPL"
    assert profile.product_meta["multiplier"] == "0.01"


def test_bybit_equity_swap_is_stock_perpetual():
    profile = classify_instrument_product(
        {
            "base": "AAPL",
            "info": {
                "symbolType": "stock",
                "underlyingTicker": "AAPL",
                "marketRegion": "US",
            },
        },
        exchange_id="bybit",
        market_type="swap",
    )

    assert profile.product_type == PRODUCT_STOCK_PERPETUAL
    assert profile.underlying_symbol == "AAPL"


def test_binance_tradfi_equity_is_stock_perpetual():
    profile = classify_instrument_product(
        {
            "base": "AAPL",
            "info": {
                "contractType": "TRADIFI_PERPETUAL",
                "underlyingType": "EQUITY",
            },
        },
        exchange_id="binance",
        market_type="swap",
    )

    assert profile.product_type == PRODUCT_STOCK_PERPETUAL
    assert profile.underlying_symbol == "AAPL"


def test_binance_tradfi_commodity_is_not_stock_perpetual():
    profile = classify_instrument_product(
        {
            "base": "XAU",
            "info": {
                "contractType": "TRADIFI_PERPETUAL",
                "underlyingType": "COMMODITY",
            },
        },
        exchange_id="binance",
        market_type="swap",
    )

    assert profile.product_type == PRODUCT_CRYPTO


def test_binance_hong_kong_equity_swap_maps_numeric_ticker_to_hk_stock():
    profile = classify_instrument_product(
        {
            "base": "HK0700",
            "info": {
                "category": "stock",
                "marketRegion": "HK",
                "underlyingTicker": "0700",
            },
        },
        exchange_id="binance",
        market_type="swap",
        symbol="HK0700/USDT",
        instrument_id="HK0700USDT",
    )

    assert profile.product_type == PRODUCT_STOCK_PERPETUAL
    assert profile.underlying_market == "HKStock"
    assert profile.underlying_symbol == "00700"


def test_binance_bstock_spot_uses_reference_equity_catalog():
    profile = classify_instrument_product(
        {"base": "NVDAB", "info": {"symbol": "NVDABUSDT"}},
        exchange_id="binance",
        market_type="spot",
        symbol="NVDAB/USDT",
        instrument_id="NVDABUSDT",
        known_equity_symbols={"NVDA"},
    )

    assert profile.product_type == PRODUCT_TOKENIZED_EQUITY
    assert profile.api_family == "spot"
    assert profile.underlying_market == "USStock"
    assert profile.underlying_symbol == "NVDA"


def test_binance_unknown_b_suffix_remains_crypto():
    profile = classify_instrument_product(
        {"base": "UNKNOWNB", "info": {"symbol": "UNKNOWNBUSDT"}},
        exchange_id="binance",
        market_type="spot",
        known_equity_symbols={"NVDA"},
    )

    assert profile.product_type == PRODUCT_CRYPTO


def test_named_hong_kong_equity_uses_explicit_market_region():
    profile = classify_instrument_product(
        {
            "base": "TENCENT",
            "info": {
                "category": "stock",
                "marketRegion": "Hong Kong",
                "underlyingTicker": "TENCENT",
            },
        },
        exchange_id="binance",
        market_type="swap",
    )

    assert profile.underlying_market == "HKStock"
    assert profile.underlying_symbol == "TENCENT"


def test_unsupported_equity_region_is_not_mapped_to_us_stock():
    profile = classify_instrument_product(
        {
            "base": "005930",
            "info": {
                "category": "stock",
                "marketRegion": "KR",
                "underlyingTicker": "005930",
            },
        },
        exchange_id="binance",
        market_type="swap",
    )

    assert profile.underlying_market == ""
    assert profile.underlying_symbol == "005930"


def test_bitget_reality_fallback_requires_area_symbol_and_strict_base():
    reality = classify_instrument_product(
        {"base": "RAAPL", "info": {"areaSymbol": "yes"}},
        exchange_id="bitget",
        market_type="spot",
    )
    ordinary = classify_instrument_product(
        {"base": "RACA", "info": {"areaSymbol": "no"}},
        exchange_id="bitget",
        market_type="spot",
    )

    assert reality.product_type == PRODUCT_TOKENIZED_EQUITY
    assert reality.api_family == "reality"
    assert reality.underlying_symbol == "AAPL"
    assert ordinary.product_type == PRODUCT_CRYPTO


def test_bitget_rwa_swap_is_stock_perpetual():
    profile = classify_instrument_product(
        {
            "base": "AAPL",
            "info": {"isRwa": "YES", "symbolType": "stock", "sizeMultiplier": "0.001"},
        },
        exchange_id="bitget",
        market_type="swap",
    )

    assert profile.product_type == PRODUCT_STOCK_PERPETUAL
    assert profile.product_meta["multiplier"] == "0.001"


def test_rwa_flag_without_equity_marker_is_not_assumed_to_be_stock():
    profile = classify_instrument_product(
        {"base": "XAUT", "info": {"isRwa": "YES", "symbolType": "metal"}},
        exchange_id="bitget",
        market_type="swap",
    )

    assert profile.product_type == PRODUCT_CRYPTO


def test_gate_stock_contract_is_stock_perpetual():
    profile = classify_instrument_product(
        {"base": "AAPL", "info": {"contract_type": "stocks", "quanto_multiplier": "0.01"}},
        exchange_id="gate",
        market_type="swap",
    )

    assert profile.product_type == PRODUCT_STOCK_PERPETUAL
    assert profile.underlying_symbol == "AAPL"
    assert profile.product_meta["multiplier"] == "0.01"


def test_unverified_htx_symbol_fails_closed_as_crypto():
    profile = classify_instrument_product(
        {"base": "AAPL", "info": {"business_type": "swap", "contract_type": "swap"}},
        exchange_id="htx",
        market_type="swap",
    )

    assert profile.product_type == PRODUCT_CRYPTO
    assert profile.underlying_symbol == ""


def test_htx_official_stock_label_is_stock_perpetual():
    profile = classify_instrument_product(
        {
            "base": "AAPL",
            "info": {
                "business_type": "swap",
                "contract_type": "swap",
                "tradfi_labels": ["Stocks"],
            },
        },
        exchange_id="htx",
        market_type="swap",
    )

    assert profile.product_type == PRODUCT_STOCK_PERPETUAL
    assert profile.underlying_symbol == "AAPL"


def test_htx_official_metal_label_is_not_stock_perpetual():
    profile = classify_instrument_product(
        {
            "base": "XAU",
            "info": {
                "business_type": "swap",
                "contract_type": "swap",
                "tradfi_labels": ["Metals"],
            },
        },
        exchange_id="htx",
        market_type="swap",
    )

    assert profile.product_type == PRODUCT_CRYPTO


def test_ordinary_crypto_remains_crypto():
    profile = classify_instrument_product(
        {"base": "BTC", "info": {"contractType": "LinearPerpetual"}},
        exchange_id="bybit",
        market_type="swap",
    )

    assert profile.asset_class == "crypto"
    assert profile.product_type == PRODUCT_CRYPTO
    assert profile.api_family == "swap"
