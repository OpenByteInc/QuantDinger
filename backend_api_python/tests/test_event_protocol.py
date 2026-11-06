from datetime import datetime, timezone

from app.events.bar_clock import BarStreamKey, MarketBarCloseClock, supports_bar_close_events
from app.events.bus import InMemoryEventBus
from app.events.protocol import (
    EventEnvelope,
    MarketBarClosedV1,
    OrderFillEventV1,
    OrderIntentEventV1,
)
from app.services.market_schedule import is_equity_bar_close


def test_event_envelope_round_trip_preserves_contract():
    event = MarketBarClosedV1.create(
        venue="binance",
        market="Crypto",
        market_type="swap",
        instrument_id="BTCUSDT",
        symbol="BTC/USDT",
        timeframe="1m",
        closed_bar_token=123,
        closed_at=datetime(2026, 9, 29, 12, 1, tzinfo=timezone.utc),
    )

    assert EventEnvelope.from_dict(event.to_dict()) == event
    assert EventEnvelope.from_json(event.to_json()) == event
    assert event.event_type == "market.bar.closed.v1"
    assert event.schema_version == 1


def test_bar_partition_keeps_same_symbol_isolated_by_exchange():
    common = {
        "market": "Crypto",
        "market_type": "swap",
        "instrument_id": "BTCUSDT",
        "timeframe": "1m",
    }

    binance = MarketBarClosedV1.partition_key(venue="binance", **common)
    bybit = MarketBarClosedV1.partition_key(venue="bybit", **common)

    assert binance != bybit
    assert "binance" in binance
    assert "bybit" in bybit


def test_stock_bar_partition_keeps_same_equity_isolated_by_provider():
    common = {
        "market": "Crypto",
        "underlying_market": "USStock",
        "api_family": "stock",
        "market_type": "spot",
        "instrument_id": "NVDA",
        "symbol": "NVDA/USD",
    }

    gate = BarStreamKey.from_member(
        {**common, "exchange_id": "gate"},
        "1m",
    )
    alpaca = BarStreamKey.from_member(
        {**common, "exchange_id": "alpaca"},
        "1m",
    )

    assert gate.market == alpaca.market == "USStock"
    assert gate.partition_key != alpaca.partition_key
    assert ":gate:" in gate.partition_key
    assert ":alpaca:" in alpaca.partition_key


def test_exchange_stock_perpetual_uses_continuous_crypto_clock():
    stream = BarStreamKey.from_member(
        {
            "market": "Crypto",
            "underlying_market": "USStock",
            "api_family": "swap",
            "product_type": "stock_perpetual",
            "market_type": "swap",
            "instrument_id": "AAPLUSDT",
            "symbol": "AAPL/USDT",
            "exchange_id": "gate",
        },
        "1d",
    )

    assert stream.market == "Crypto"
    assert supports_bar_close_events("1d", [{
        "market": "Crypto",
        "underlying_market": "USStock",
        "api_family": "swap",
        "product_type": "stock_perpetual",
    }])


def test_order_events_partition_by_execution_account():
    intent = OrderIntentEventV1.create(
        exchange_id="binance",
        credential_id=9,
        account_type="swap",
        strategy_id=101,
        intent={"side": "buy"},
        producer="strategy-runtime",
    )
    fill = OrderFillEventV1.create(
        exchange_id="binance",
        credential_id=9,
        account_type="swap",
        strategy_id=101,
        fill={"quantity": 0.01},
        producer="execution-gateway",
        causation_id=intent.event_id,
    )

    assert intent.partition_key == fill.partition_key == "account:binance:9:swap"
    assert fill.causation_id == intent.event_id


def test_bar_clock_publishes_distinct_venue_events_at_one_boundary():
    initial = datetime(2026, 9, 29, 12, 0, 30, tzinfo=timezone.utc)
    bus = InMemoryEventBus()
    clock = MarketBarCloseClock(bus, now=lambda: initial, autostart=False)
    received = []
    common = {
        "market": "Crypto",
        "market_type": "swap",
        "instrument_id": "BTCUSDT",
        "symbol": "BTC/USDT",
        "timeframe": "1m",
    }
    subscriptions = [
        clock.subscribe(BarStreamKey(venue=venue, **common), received.append)
        for venue in ("binance", "bybit")
    ]

    assert clock.publish_due(datetime(2026, 9, 29, 12, 1, 1, tzinfo=timezone.utc)) == 2
    assert {event.payload["venue"] for event in received} == {"binance", "bybit"}
    assert len({event.partition_key for event in received}) == 2

    for subscription in subscriptions:
        subscription.close()
    clock.close()


def test_stock_intraday_events_follow_exchange_session_boundaries():
    assert is_equity_bar_close(
        "USStock",
        datetime(2026, 9, 29, 13, 31, tzinfo=timezone.utc),
    )
    assert not is_equity_bar_close(
        "USStock",
        datetime(2026, 9, 29, 12, 1, tzinfo=timezone.utc),
    )
    assert supports_bar_close_events("1m", [{"market": "USStock"}])
    assert not supports_bar_close_events("1d", [{"market": "USStock"}])
    assert not supports_bar_close_events(
        "1m",
        [{"market": "Crypto", "api_family": "stock"}],
    )
