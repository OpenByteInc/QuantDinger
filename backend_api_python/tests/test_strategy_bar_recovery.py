from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.events.bar_clock import BarStreamKey
from app.events.bar_recovery import BarRecoveryRoute, StrategyBarRecoveryPublisher
from app.events.protocol import MarketBarClosedV1


class FakeRepository:
    def __init__(self, routes):
        self.routes = list(routes)

    def active_bar_routes(self):
        return list(self.routes)


class FakeProducer:
    def __init__(self, results=None):
        self.results = list(results or [True])
        self.batches = []

    def publish_batch(self, events, *, timeout):
        self.batches.append((list(events), timeout))
        return self.results.pop(0) if self.results else True

    def close(self):
        return None


def _publisher(partition_key, *, producer=None):
    return StrategyBarRecoveryPublisher(
        repository=FakeRepository([
            {"partition_key": partition_key, "timeframe": "1m"},
        ]),
        producer=producer or FakeProducer(),
        grace_seconds=0,
    )


@pytest.mark.parametrize("venue", ["binance", "bitget", "bybit", "okx", "gate", "htx"])
def test_recovery_publishes_same_contract_for_every_crypto_venue(venue):
    partition_key = MarketBarClosedV1.partition_key(
        venue=venue,
        market="Crypto",
        market_type="swap",
        instrument_id="BTCUSDT",
        timeframe="1m",
    )
    producer = FakeProducer()
    recovery = _publisher(partition_key, producer=producer)

    assert recovery.publish_due(datetime(2026, 9, 29, 12, 1, 6, tzinfo=timezone.utc)) == 1

    event = producer.batches[0][0][0]
    assert event.partition_key == partition_key
    assert event.payload["venue"] == venue
    assert event.producer == "strategy-bar-recovery"


def test_recovery_parser_preserves_colons_inside_canonical_instrument():
    partition_key = "bar:crypto:crypto:swap:crypto:btc/usdt@swap:1m"

    route = BarRecoveryRoute.parse(partition_key, "1m")
    event = route.event(100)

    assert route.instrument_id == "crypto:btc/usdt@swap"
    assert event.partition_key == partition_key


def test_recovery_retries_same_token_after_delivery_failure():
    partition_key = "bar:gate:crypto:swap:btc_usdt:1m"
    producer = FakeProducer([False, True])
    recovery = _publisher(partition_key, producer=producer)
    now = datetime(2026, 9, 29, 12, 1, 6, tzinfo=timezone.utc)

    assert recovery.publish_due(now) == 0
    assert recovery.publish_due(now) == 1
    assert len(producer.batches) == 2
    assert producer.batches[0][0][0].event_id == producer.batches[1][0][0].event_id


def test_recovery_catches_up_each_missed_boundary_after_transport_outage():
    partition_key = "bar:gate:crypto:swap:btc_usdt:1m"
    producer = FakeProducer([False, True])
    recovery = _publisher(partition_key, producer=producer)
    first = datetime(2026, 9, 29, 12, 1, 6, tzinfo=timezone.utc)
    restored = datetime(2026, 9, 29, 12, 3, 6, tzinfo=timezone.utc)

    assert recovery.publish_due(first) == 0
    assert recovery.publish_due(restored) == 3

    tokens = [event.payload["closed_bar_token"] for event in producer.batches[1][0]]
    assert tokens == list(range(tokens[0], tokens[0] + 3))


def test_primary_bar_event_suppresses_recovery_duplicate():
    event = MarketBarClosedV1.create(
        venue="okx",
        market="Crypto",
        market_type="swap",
        instrument_id="BTCUSDT",
        symbol="BTC/USDT",
        timeframe="1m",
        closed_bar_token=100,
        closed_at=datetime.fromtimestamp(101 * 60, tz=timezone.utc),
    )
    producer = FakeProducer()
    recovery = _publisher(event.partition_key, producer=producer)
    recovery.observe(event)

    assert recovery.publish_due(datetime.fromtimestamp(101 * 60 + 5, tz=timezone.utc)) == 0
    assert producer.batches == []


@pytest.mark.parametrize(
    ("market", "outside_session", "inside_session"),
    [
        (
            "USStock",
            datetime(2026, 9, 29, 12, 1, 6, tzinfo=timezone.utc),
            datetime(2026, 9, 29, 13, 31, 6, tzinfo=timezone.utc),
        ),
        (
            "HKStock",
            datetime(2026, 9, 29, 4, 31, 6, tzinfo=timezone.utc),
            datetime(2026, 9, 29, 5, 1, 6, tzinfo=timezone.utc),
        ),
    ],
)
def test_direct_equity_recovery_obeys_market_sessions(
    market,
    outside_session,
    inside_session,
):
    stream = BarStreamKey.from_member(
        {
            "market": "Crypto",
            "underlying_market": market,
            "api_family": "stock",
            "product_type": "direct_equity",
            "market_type": "spot",
            "instrument_id": "AAPL" if market == "USStock" else "0700",
            "symbol": "AAPL/USD" if market == "USStock" else "0700/HKD",
            "exchange_id": "gate",
        },
        "1m",
    )

    outside = _publisher(stream.partition_key, producer=FakeProducer())
    inside_producer = FakeProducer()
    inside = _publisher(stream.partition_key, producer=inside_producer)

    assert outside.publish_due(outside_session) == 0
    assert inside.publish_due(inside_session) == 1
    assert inside_producer.batches[0][0][0].payload["market"] == market


@pytest.mark.parametrize("product_type,api_family,market_type", [
    ("stock_perpetual", "swap", "swap"),
    ("tokenized_equity", "spot", "spot"),
    ("tokenized_equity", "reality", "spot"),
])
def test_exchange_equity_products_use_continuous_crypto_boundaries(
    product_type,
    api_family,
    market_type,
):
    stream = BarStreamKey.from_member(
        {
            "market": "Crypto",
            "underlying_market": "USStock",
            "api_family": api_family,
            "product_type": product_type,
            "market_type": market_type,
            "instrument_id": "AAPLUSDT",
            "symbol": "AAPL/USDT",
            "exchange_id": "gate",
        },
        "1m",
    )
    producer = FakeProducer()
    recovery = _publisher(stream.partition_key, producer=producer)

    assert stream.market == "Crypto"
    assert recovery.publish_due(
        datetime(2026, 10, 3, 12, 1, 6, tzinfo=timezone.utc)
    ) == 1
    assert producer.batches[0][0][0].payload["market"] == "Crypto"
