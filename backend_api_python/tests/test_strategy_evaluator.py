from __future__ import annotations

from app.events.evaluator import StrategyEvaluationBatchHandler
from app.events.protocol import StrategyEvaluationBatchV1, StrategyRuntimePrepareV1
from app.services.event_inbox import InboxClaim


class FakeInbox:
    def __init__(self, claim=InboxClaim("acquired", 1)):
        self.next_claim = claim
        self.claims = []
        self.completions = []
        self.failures = []

    def claim(self, **kwargs):
        self.claims.append(kwargs)
        return self.next_claim

    def complete(self, **kwargs):
        self.completions.append(kwargs)
        return True

    def fail(self, **kwargs):
        self.failures.append(kwargs)
        return True


class FakeShardLeases:
    def __init__(self, token=7):
        self.token = token
        self.acquisitions = []
        self.releases = []
        self.released = []
    def acquire(self, **kwargs):
        self.acquisitions.append(kwargs)
        return self.token

    def release(self, **kwargs):
        self.releases.append(kwargs)
        return True

    def release_all(self, **kwargs):
        self.released.append(kwargs)
        return len(self.acquisitions)


class FakeSubscriptions:
    def __init__(self, running_ids):
        self.running_ids = running_ids

    def running_strategy_ids(self, strategy_ids):
        assert list(strategy_ids) == [1, 129]
        return list(self.running_ids)


class FakeRuntimeHost:
    def __init__(self):
        self.evaluations = []
        self.closed = False
        self.released = []

    def evaluate(self, strategy_id, event, *, timeout):
        self.evaluations.append((strategy_id, event.event_id, timeout))
        return True

    def snapshot(self):
        return {"running_strategies": len(self.evaluations)}

    def release_strategies(self, strategy_ids):
        released = set(strategy_ids)
        self.released.append(released)
        return len(released)

    def close(self):
        self.closed = True


def _batch():
    return StrategyEvaluationBatchV1.create(
        strategy_shard=1,
        strategy_ids=[129, 1],
        source_event_id="source-bar-event",
        closed_bar_token=123,
        timeframe="1m",
    )


def test_shadow_evaluator_fences_and_completes_inbox(monkeypatch):
    monkeypatch.setenv("STRATEGY_SHARD_COUNT", "128")
    inbox = FakeInbox()
    leases = FakeShardLeases()
    handler = StrategyEvaluationBatchHandler(
        owner_id="worker-a",
        consumer_group="evaluator-test",
        inbox=inbox,
        shard_leases=leases,
        subscriptions=FakeSubscriptions([1]),
        mode="shadow",
    )

    assert handler.handle(_batch()) is True

    assert leases.acquisitions[0]["strategy_shard"] == 1
    result = inbox.completions[0]["result"]
    assert result["fencing_token"] == 7
    assert result["running_strategy_count"] == 1
    assert result["inactive_strategy_count"] == 1
    assert leases.releases == [{
        "strategy_shard": 1,
        "owner_id": "worker-a",
        "fencing_token": 7,
    }]
    assert handler.snapshot()["evaluation_owned_shards"] == 0
    assert handler.snapshot()["evaluation_batches_completed"] == 1
    handler.close()
    assert leases.released == [{"owner_id": "worker-a"}]


def test_shadow_evaluator_commits_completed_duplicate_without_reprocessing():
    inbox = FakeInbox(InboxClaim("completed", 1))
    leases = FakeShardLeases()
    handler = StrategyEvaluationBatchHandler(
        owner_id="worker-a",
        consumer_group="evaluator-test",
        inbox=inbox,
        shard_leases=leases,
        subscriptions=FakeSubscriptions([]),
        mode="shadow",
    )

    assert handler.handle(_batch()) is True
    assert leases.acquisitions == []
    assert leases.releases == []
    assert inbox.completions == []
    assert handler.snapshot()["evaluation_batches_duplicate"] == 1


def test_shadow_evaluator_retries_when_shard_lease_is_busy():
    inbox = FakeInbox()
    leases = FakeShardLeases(token=None)
    handler = StrategyEvaluationBatchHandler(
        owner_id="worker-b",
        consumer_group="evaluator-test",
        inbox=inbox,
        shard_leases=leases,
        subscriptions=FakeSubscriptions([]),
        mode="shadow",
    )

    assert handler.handle(_batch()) is False
    assert inbox.failures[0]["terminal"] is False
    assert handler.snapshot()["evaluation_batches_busy"] == 1
    assert leases.releases == []


def test_shadow_evaluator_marks_poison_batch_dead_after_max_attempts():
    inbox = FakeInbox(InboxClaim("acquired", 5))
    event = _batch()
    invalid = type(event)(
        **{**event.to_dict(), "partition_key": "strategy-shard:2"},
    )
    handler = StrategyEvaluationBatchHandler(
        owner_id="worker-a",
        consumer_group="evaluator-test",
        inbox=inbox,
        shard_leases=FakeShardLeases(),
        subscriptions=FakeSubscriptions([]),
        max_attempts=5,
        mode="shadow",
    )

    assert handler.handle(invalid) is True
    assert inbox.failures[0]["terminal"] is True
    assert handler.snapshot()["evaluation_batches_dead"] == 1


def test_active_evaluator_waits_for_runtime_completion(monkeypatch):
    monkeypatch.setenv("STRATEGY_SHARD_COUNT", "128")
    inbox = FakeInbox()
    runtime_host = FakeRuntimeHost()
    leases = FakeShardLeases()
    handler = StrategyEvaluationBatchHandler(
        owner_id="worker-active",
        consumer_group="evaluator-active",
        inbox=inbox,
        shard_leases=leases,
        subscriptions=FakeSubscriptions([1, 129]),
        mode="active",
        runtime_host=runtime_host,
    )

    assert handler.handle(_batch()) is True
    assert {item[0] for item in runtime_host.evaluations} == {1, 129}
    assert inbox.completions[0]["result"]["mode"] == "active"
    assert leases.releases[0]["strategy_shard"] == 1
    assert handler.release_partition_keys({"strategy-shard:1"}) == 2
    assert runtime_host.released == [{1, 129}]
    assert handler.snapshot()["evaluation_rebalance_released_strategies"] == 2
    handler.close()
    assert runtime_host.closed is True


def test_prepare_uses_same_partition_without_evaluating_a_candle():
    from app.events.topics import topic_for_event

    host, inbox = FakeRuntimeHost(), FakeInbox()
    # Preparation has no candle token; it cannot be mistaken for a signal event.
    event = StrategyRuntimePrepareV1.create(strategy_shard=1, strategy_ids=[1, 129])
    host.preparations = []
    host.prepare = lambda sid, event, **kwargs: host.preparations.append(sid) or True
    handler = StrategyEvaluationBatchHandler(
        owner_id="worker", consumer_group="test", inbox=inbox,
        shard_leases=FakeShardLeases(), subscriptions=FakeSubscriptions([1, 129]),
        mode="active", runtime_host=host,
    )
    try:
        assert topic_for_event(event.event_type) == topic_for_event(_batch().event_type)
        assert handler.handle(event)
        assert sorted(host.preparations) == [1, 129]
        assert host.evaluations == []
        assert handler.release_partition_keys({"strategy-shard:1"}) == 2
    finally:
        handler.close()
