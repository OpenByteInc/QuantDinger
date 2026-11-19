from datetime import datetime, timedelta, timezone

import pytest

from app.services.instrument_rules import (
    InstrumentRulesProvider,
    InstrumentRulesSnapshot,
    InstrumentRulesSnapshotStore,
)


@pytest.mark.parametrize(
    ("exchange", "market_type", "raw", "expected"),
    [
        (
            "binance",
            "spot",
            {
                "filters": [
                    {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                    {"filterType": "LOT_SIZE", "stepSize": "0.0001", "minQty": "0.001"},
                    {"filterType": "MIN_NOTIONAL", "minNotional": "5"},
                ]
            },
            (0.0001, 0.001, 5.0, 0.01, 1.0),
        ),
        (
            "bybit",
            "swap",
            {
                "lotSizeFilter": {
                    "qtyStep": "0.001",
                    "minOrderQty": "0.002",
                    "minNotionalValue": "5",
                },
                "priceFilter": {"tickSize": "0.1"},
            },
            (0.001, 0.002, 5.0, 0.1, 1.0),
        ),
        (
            "bybit",
            "spot",
            {
                "lotSizeFilter": {
                    "basePrecision": "0.000001",
                    "minOrderAmt": "5",
                },
                "priceFilter": {"tickSize": "0.01"},
            },
            (0.000001, 0.0, 5.0, 0.01, 1.0),
        ),
        (
            "okx",
            "swap",
            {"lotSz": "1", "minSz": "2", "tickSz": "0.1", "ctVal": "0.01"},
            (0.01, 0.02, 0.0, 0.1, 0.01),
        ),
        (
            "bitget",
            "swap",
            {
                "sizeMultiplier": "1",
                "minTradeNum": "2",
                "contractSize": "0.001",
                "pricePlace": "1",
                "priceEndStep": "5",
                "minTradeUSDT": "5",
            },
            (0.001, 0.002, 5.0, 0.5, 0.001),
        ),
        (
            "bitget",
            "swap",
            {
                "quantityMultiplier": "0.0001",
                "quantityPrecision": "4",
                "minOrderQty": "0.0002",
                "minOrderAmount": "5",
                "priceMultiplier": "0.1",
                "pricePrecision": "1",
            },
            (0.0001, 0.0002, 5.0, 0.1, 1.0),
        ),
        (
            "gate",
            "swap",
            {
                "quanto_multiplier": "0.0001",
                "order_size_min": "0.1",
                "order_price_round": "0.01",
            },
            (0.00001, 0.00001, 0.0, 0.01, 0.0001),
        ),
        (
            "htx",
            "swap",
            {"contract_size": "0.001", "price_tick": "0.1"},
            (0.001, 0.001, 0.0, 0.1, 0.001),
        ),
        (
            "bitget",
            "spot",
            {
                "quantityPrecision": "6",
                "pricePrecision": "2",
                "minTradeAmount": "0.0001",
                "minTradeUSDT": "1",
            },
            (0.000001, 0.0001, 1.0, 0.01, 1.0),
        ),
        (
            "bitget",
            "spot",
            {
                "quantityMultiplier": "0.00001",
                "quantityPrecision": "5",
                "minOrderQty": "0.0001",
                "minOrderAmount": "5",
                "priceMultiplier": "0.01",
                "pricePrecision": "2",
            },
            (0.00001, 0.0001, 5.0, 0.01, 1.0),
        ),
        (
            "gate",
            "spot",
            {
                "amount_precision": 6,
                "precision": 2,
                "min_base_amount": "0.0001",
                "min_quote_amount": "1",
            },
            (0.000001, 0.0001, 1.0, 0.01, 1.0),
        ),
        (
            "htx",
            "spot",
            {
                "amount-precision": 6,
                "price-precision": 2,
                "min-order-amt": "0.0001",
                "min-order-value": "1",
            },
            (0.000001, 0.0001, 1.0, 0.01, 1.0),
        ),
    ],
)
def test_native_exchange_rules_normalize_to_base_asset_units(
    exchange, market_type, raw, expected
):
    rules = InstrumentRulesProvider.normalize(
        exchange,
        market_type,
        "BTC/USDT",
        raw,
        captured_at="2026-08-27T00:00:00Z",
    )

    assert (
        rules.amount_step,
        rules.min_amount,
        rules.min_notional,
        rules.price_tick,
        rules.contract_size,
    ) == pytest.approx(expected)


def test_provider_uses_native_endpoint_and_ttl_cache(tmp_path):
    calls = []

    def fetch(exchange, market_type, path, params, headers):
        calls.append((exchange, market_type, path, params, headers))
        return {
            "symbols": [{
                "symbol": "BTCUSDT",
                "filters": [
                    {"filterType": "PRICE_FILTER", "tickSize": "0.1"},
                    {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
                ],
            }]
        }

    provider = InstrumentRulesProvider(
        raw_fetcher=fetch,
        snapshot_store=InstrumentRulesSnapshotStore(tmp_path),
    )

    first = provider.get_rules("BTC/USDT", exchange_id="binance", market_type="swap")
    second = provider.get_rules("BTC/USDT", exchange_id="binance", market_type="swap")

    assert first is second
    assert first.amount_step == 0.001
    assert len(calls) == 1
    assert calls[0][2] == "/fapi/v1/exchangeInfo"


def test_provider_falls_back_when_a_real_live_clients_public_host_is_blocked(tmp_path):
    class BinanceLikeClient:
        def get_symbol_filters(self, *, symbol):
            raise RuntimeError(f"primary host blocked for {symbol}")

    calls = []

    def fetch(exchange, market_type, path, params, headers):
        calls.append((exchange, market_type, path, params, headers))
        return {
            "symbols": [{
                "symbol": "BTCUSDT",
                "filters": [
                    {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                    {"filterType": "LOT_SIZE", "stepSize": "0.00001", "minQty": "0.00001"},
                ],
            }]
        }

    provider = InstrumentRulesProvider(
        raw_fetcher=fetch,
        snapshot_store=InstrumentRulesSnapshotStore(tmp_path),
    )
    rules = provider.get_rules(
        "BTC/USDT",
        exchange_id="binance",
        market_type="spot",
        client=BinanceLikeClient(),
    )

    assert rules.amount_step == 0.00001
    assert calls[0][2] == "/api/v3/exchangeInfo"


def test_htx_spot_provider_uses_v1_endpoint_with_minimum_order_fields(tmp_path):
    calls = []

    def fetch(exchange, market_type, path, params, headers):
        calls.append(path)
        return {
            "data": [{
                "symbol": "btcusdt",
                "amount-precision": 6,
                "price-precision": 2,
                "min-order-amt": "0.00001",
                "min-order-value": "1",
            }]
        }

    rules = InstrumentRulesProvider(
        raw_fetcher=fetch,
        snapshot_store=InstrumentRulesSnapshotStore(tmp_path),
    ).get_rules("BTC/USDT", exchange_id="htx", market_type="spot")

    assert calls == ["/v1/common/symbols"]
    assert rules.min_amount == 0.00001
    assert rules.min_notional == 1.0


def test_snapshot_is_content_addressed_and_replayable(tmp_path):
    store = InstrumentRulesSnapshotStore(tmp_path)
    provider = InstrumentRulesProvider(
        raw_fetcher=lambda *_args: {
            "symbols": [{
                "symbol": "BTCUSDT",
                "filters": [{
                    "filterType": "LOT_SIZE",
                    "stepSize": "0.001",
                    "minQty": "0.001",
                }],
            }]
        },
        snapshot_store=store,
    )
    instruments = [{
        "market": "Crypto",
        "symbol": "BTC/USDT",
        "exchange_id": "binance",
        "market_type": "swap",
    }]

    snapshot = provider.snapshot(instruments)
    restored = store.load(snapshot.snapshot_id)

    assert restored.snapshot_id == snapshot.snapshot_id
    assert restored.get("Crypto:BTC/USDT@binance:swap").amount_step == 0.001


def test_snapshot_resolves_generic_crypto_key_without_runtime_config(monkeypatch):
    rules = InstrumentRulesProvider.normalize(
        "binance",
        "swap",
        "BTC/USDT",
        {
            "symbol": "BTCUSDT",
            "filters": [{
                "filterType": "LOT_SIZE",
                "stepSize": "0.001",
                "minQty": "0.001",
            }],
        },
        captured_at="2025-01-01T00:00:00Z",
    )
    snapshot = InstrumentRulesSnapshot.build([rules])
    monkeypatch.setattr(
        "app.services.instrument_rules.default_crypto_exchange_id",
        lambda: (_ for _ in ()).throw(AssertionError("runtime config must not be read")),
    )

    resolved = snapshot.get("Crypto:BTC/USDT@swap")

    assert resolved is not None
    assert resolved.key == "Crypto:BTC/USDT@binance:swap"
    assert resolved.amount_step == 0.001


def test_historical_backtest_without_snapshot_never_fetches_todays_rules(tmp_path):
    def unexpected_fetch(*_args):
        raise AssertionError("historical backtest must not fetch current exchange rules")

    provider = InstrumentRulesProvider(
        raw_fetcher=unexpected_fetch,
        snapshot_store=InstrumentRulesSnapshotStore(tmp_path),
    )
    snapshot = provider.historical_snapshot(
        [{
            "market": "Crypto",
            "symbol": "BTC/USDT",
            "exchange_id": "okx",
            "market_type": "swap",
        }],
        as_of=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )

    rules = snapshot.get("Crypto:BTC/USDT@okx:swap")
    assert rules.source == "historical_fallback_no_snapshot"
    assert rules.amount_step == 1e-8


def test_same_day_completed_bar_uses_historical_rules_without_network(tmp_path):
    def unexpected_fetch(*_args):
        raise AssertionError("completed market data must not fetch newer exchange rules")

    provider = InstrumentRulesProvider(
        raw_fetcher=unexpected_fetch,
        snapshot_store=InstrumentRulesSnapshotStore(tmp_path),
    )
    snapshot = provider.historical_snapshot(
        [{
            "market": "Crypto",
            "symbol": "BTC/USDT",
            "exchange_id": "okx",
            "market_type": "swap",
        }],
        as_of=datetime.now(timezone.utc) - timedelta(minutes=1),
    )

    rules = snapshot.get("Crypto:BTC/USDT@okx:swap")
    assert rules.source == "historical_fallback_no_snapshot"


def test_historical_backtest_automatically_reuses_latest_eligible_snapshot(tmp_path):
    store = InstrumentRulesSnapshotStore(tmp_path)
    rules = InstrumentRulesProvider.normalize(
        "okx",
        "swap",
        "BTC/USDT",
        {"lotSz": "1", "minSz": "1", "tickSz": "0.1", "ctVal": "0.01"},
        captured_at="2025-01-01T00:00:00Z",
    )
    saved = InstrumentRulesSnapshot.build(
        [rules], captured_at="2025-01-01T00:00:00Z"
    )
    store.save(saved)
    provider = InstrumentRulesProvider(
        raw_fetcher=lambda *_args: (_ for _ in ()).throw(
            AssertionError("stored historical rules should be reused")
        ),
        snapshot_store=store,
    )

    restored = provider.historical_snapshot(
        [{
            "market": "Crypto",
            "symbol": "BTC/USDT",
            "exchange_id": "okx",
            "market_type": "swap",
        }],
        as_of=datetime(2025, 6, 1, tzinfo=timezone.utc),
    )

    assert restored.snapshot_id == saved.snapshot_id
    assert restored.get("Crypto:BTC/USDT@okx:swap").amount_step == 0.01
