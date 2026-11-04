from app.services.market import symbol_search


def test_crypto_search_uses_matching_cached_catalog_without_live_lookup(monkeypatch):
    monkeypatch.setattr(
        symbol_search,
        "_search_crypto_exchange",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("live lookup must not run")),
    )
    monkeypatch.setattr(
        symbol_search,
        "_search_cached_crypto_symbols",
        lambda keyword, limit, exchange_id, market_type: [
            {
                "market": "Crypto",
                "symbol": "AAPL/USDT",
                "name": "Apple",
                "exchange_id": exchange_id,
                "market_type": market_type,
                "instrument_id": "AAPL-USDT-SWAP",
                "settle_currency": "USDT",
                "asset_class": "equity",
            }
        ],
    )

    rows = symbol_search.search_market_symbols(
        "Crypto",
        "AAPL",
        exchange_id="okx",
        market_type="swap",
    )

    assert rows == [
        {
            "market": "Crypto",
            "symbol": "AAPL/USDT",
            "name": "Apple",
            "exchange_id": "okx",
            "market_type": "swap",
            "instrument_id": "AAPL-USDT-SWAP",
            "settle_currency": "USDT",
            "asset_class": "equity",
            "product_type": "crypto",
            "api_family": "swap",
            "underlying_market": "",
            "underlying_symbol": "",
            "product_meta": {},
        }
    ]


def test_crypto_search_rejects_removed_exchange():
    assert symbol_search.search_market_symbols(
        "Crypto",
        "BTC",
        exchange_id="retiredexchange",
        market_type="spot",
    ) == []


def test_crypto_search_without_market_type_returns_spot_and_swap(monkeypatch):
    calls = []

    def cached(keyword, limit, exchange_id, market_type):
        calls.append((keyword, limit, exchange_id, market_type))
        return [
            {
                "market": "Crypto",
                "symbol": "QQQB/USDT" if market_type == "spot" else "QQQ/USDT",
                "name": "QQQB" if market_type == "spot" else "QQQ",
                "exchange_id": exchange_id,
                "market_type": market_type,
                "instrument_id": "QQQBUSDT" if market_type == "spot" else "QQQUSDT",
                "settle_currency": "USDT",
                "asset_class": "equity",
                "product_type": "tokenized_equity" if market_type == "spot" else "stock_perpetual",
                "api_family": market_type,
                "underlying_market": "USStock",
                "underlying_symbol": "QQQ",
            }
        ]

    monkeypatch.setattr(symbol_search, "_search_cached_crypto_symbols", cached)

    rows = symbol_search.search_market_symbols(
        "Crypto",
        "QQQ",
        exchange_id="binance",
    )

    assert [row["instrument_id"] for row in rows] == ["QQQBUSDT", "QQQUSDT"]
    assert [row["market_type"] for row in rows] == ["spot", "swap"]
    assert calls == [
        ("QQQ", 20, "binance", "spot"),
        ("QQQ", 20, "binance", "swap"),
    ]


def test_crypto_symbol_capabilities_require_exact_same_symbol(monkeypatch):
    def cached(keyword, limit, exchange_id, market_type):
        if market_type == "spot":
            return [{
                "market": "Crypto",
                "symbol": "QQQB/USDT",
                "exchange_id": exchange_id,
                "market_type": "spot",
                "instrument_id": "QQQBUSDT",
                "product_type": "tokenized_equity",
            }]
        return [{
            "market": "Crypto",
            "symbol": "QQQ/USDT",
            "exchange_id": exchange_id,
            "market_type": "swap",
            "instrument_id": "QQQUSDT",
            "product_type": "stock_perpetual",
        }]

    monkeypatch.setattr(symbol_search, "_search_cached_crypto_symbols", cached)

    result = symbol_search.get_crypto_symbol_capabilities(
        "QQQ/USDT",
        exchange_id="binance",
    )

    assert result["market_types"] == ["swap"]
    assert result["contexts"][0]["instrument_id"] == "QQQUSDT"


def test_crypto_symbol_capabilities_allow_switch_for_same_spot_and_swap_pair(monkeypatch):
    def cached(keyword, limit, exchange_id, market_type):
        return [{
            "market": "Crypto",
            "symbol": "BTC/USDT",
            "exchange_id": exchange_id,
            "market_type": market_type,
            "instrument_id": f"BTCUSDT-{market_type.upper()}",
            "product_type": "crypto",
        }]

    monkeypatch.setattr(symbol_search, "_search_cached_crypto_symbols", cached)

    result = symbol_search.get_crypto_symbol_capabilities(
        "BTC/USDT",
        exchange_id="binance",
    )

    assert result["market_types"] == ["spot", "swap"]
    assert [item["instrument_id"] for item in result["contexts"]] == [
        "BTCUSDT-SPOT",
        "BTCUSDT-SWAP",
    ]
