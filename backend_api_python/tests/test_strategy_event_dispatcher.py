from __future__ import annotations

from datetime import datetime, timezone

from app.events.dispatcher import StrategyDueDispatcher
from app.events.protocol import MarketBarClosedV1
from app.services.strategy_event_subscriptions import strategy_shard


def _bar_event():
    return MarketBarClosedV1.create(
        venue="gateio",
        market="Crypto",
        market_type="swap",
        instrument_id="BTC_USDT",
        symbol="BTC/USDT",
        timeframe="1m",
        closed_bar_token=456,
        closed_at=datetime(2026, 9, 29, 12, 1, tzinfo=timezone.utc),
    )


class FakeRepository:
    def __init__(self, strategy_ids):
        self.strategy_ids = strategy_ids
        self.lookups = []

    def strategy_ids_for_event(self, event_type, partition_key):
        self.lookups.append((event_type, partition_key))
        return list(self.strategy_ids)

    def unready_strategy_ids(self):
        return list(self.strategy_ids)


class FakeProducer:
    def __init__(self, succeeds=True):
        self.succeeds = succeeds
        self.batches = []

    def publish_batch(self, events, *, timeout):
        self.batches.append((list(events), timeout))
        return self.succeeds

    def snapshot(self):
        return {"kafka_events_queued": sum(len(events) for events, _ in self.batches)}

    def close(self):
        return None


def test_strategy_shard_is_stable_and_bounded():
    assert strategy_shard(257, 128) == 1
    assert strategy_shard(257, 64) == 1
    assert strategy_shard(128, 128) == 0


def test_dispatcher_groups_and_chunks_strategies_by_stable_shard():
    strategy_ids = [1, 129, 257, 385, 513]
    repository = FakeRepository(strategy_ids)
    producer = FakeProducer()
    dispatcher = StrategyDueDispatcher(
        repository=repository,
        producer=producer,
        batch_size=2,
    )
    event = _bar_event()

    assert dispatcher.dispatch(event)

    published = producer.batches[0][0]
    assert [item.payload["strategy_ids"] for item in published] == [
        [1, 129],
        [257, 385],
        [513],
    ]
    assert {item.partition_key for item in published} == {"strategy-shard:1"}
    assert all(item.causation_id == event.event_id for item in published)
    assert dispatcher.snapshot()["strategies_dispatched"] == 5
    assert dispatcher.snapshot()["batches_dispatched"] == 3


def test_dispatcher_retries_source_event_when_batch_flush_fails():
    producer = FakeProducer(succeeds=False)
    dispatcher = StrategyDueDispatcher(
        repository=FakeRepository([7]),
        producer=producer,
    )

    assert dispatcher.dispatch(_bar_event()) is False
    assert dispatcher.snapshot()["source_events"] == 0


def test_unready_daily_strategies_are_prepared_without_market_events():
    from app.events.protocol import StrategyRuntimePrepareV1

    producer = FakeProducer()
    dispatcher = StrategyDueDispatcher(repository=FakeRepository([1, 129, 2]), producer=producer)
    assert dispatcher.prepare_due_runtimes()
    events = producer.batches[0][0]
    assert [event.payload["strategy_ids"] for event in events] == [[1, 129], [2]]
    assert all(event.event_type == StrategyRuntimePrepareV1.event_type for event in events)
    assert all("closed_bar_token" not in event.payload for event in events)
    producer.succeeds = False
    assert not dispatcher.prepare_due_runtimes()
