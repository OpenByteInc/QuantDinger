from __future__ import annotations

from contextlib import AbstractContextManager

import pytest

from app.services.execution_streams import processor as processor_module


class FakeCursor:
    def __init__(self, calls, pending):
        self.calls = calls
        self.pending = pending

    def execute(self, sql, params=()):
        self.calls.append((sql, params))

    def fetchone(self):
        return self.pending

    def close(self):
        return None


class FakeConnection(AbstractContextManager):
    def __init__(self, calls, pending):
        self.calls = calls
        self.pending = pending

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def cursor(self):
        return FakeCursor(self.calls, self.pending)

    def commit(self):
        return None


@pytest.mark.parametrize(
    ("order_status", "queue_status", "intent_status"),
    [("filled", "filled", "filled"), ("open", "sent", "partially_filled")],
)
def test_late_fill_recovers_failed_queue_and_intent(
    monkeypatch,
    order_status,
    queue_status,
    intent_status,
):
    calls = []
    pending = {
        "id": 7,
        "strategy_id": 0,
        "order_intent_id": 9,
        "filled": 0,
        "avg_price": 0,
        "payload_json": "{}",
    }
    monkeypatch.setattr(
        processor_module,
        "get_db_connection",
        lambda: FakeConnection(calls, pending),
    )
    monkeypatch.setattr(
        processor_module.ExecutionEventProcessor,
        "_already_projected",
        staticmethod(lambda _event_id: False),
    )
    monkeypatch.setattr(
        processor_module,
        "posted_totals",
        lambda *_args: {"quantity": 0.0, "average": 0.0},
    )
    monkeypatch.setattr(
        processor_module,
        "combine_pending_snapshot",
        lambda event, _pending: (event, {}),
    )
    event = {
        "id": 101,
        "quantity": 1.0,
        "cumulative_quantity": 1.0,
        "is_cumulative": True,
        "price": 100.0,
        "order_status": order_status,
        "fee_status": "actual",
        "exchange_order_id": "exchange-101",
    }
    binding = {"id": 8, "pending_order_id": 7, "strategy_id": 0}

    processor_module.ExecutionEventProcessor()._project_pending_order(event, binding)

    pending_update = next(item for item in calls if "UPDATE pending_orders" in item[0])
    assert "status = 'failed' AND %s > 0" in pending_update[0]
    assert pending_update[1][3:5] == (queue_status, 1.0)
    intent_update = next(item for item in calls if "UPDATE strategy_order_intents" in item[0])
    assert intent_update[1] == (queue_status, 1.0, queue_status, 9)
    assert intent_status in intent_update[0]


def test_native_protection_fill_is_projected_as_independent_exit(monkeypatch):
    calls = []
    persisted = []
    pending = {
        "id": 7,
        "strategy_id": 11,
        "strategy_run_id": 13,
        "user_id": 2,
        "symbol": "BTC/USDT",
        "signal_type": "open_long",
        "market_type": "swap",
    }
    monkeypatch.setattr(
        processor_module,
        "get_db_connection",
        lambda: FakeConnection(calls, pending),
    )
    monkeypatch.setattr(
        processor_module.ExecutionEventProcessor,
        "_already_projected",
        staticmethod(lambda _event_id: False),
    )
    monkeypatch.setattr(processor_module, "load_strategy_configs", lambda _strategy_id: {"user_id": 2})
    monkeypatch.setattr(processor_module, "persist_strategy_fill", lambda **kwargs: persisted.append(kwargs))
    monkeypatch.setattr(processor_module, "append_strategy_log", lambda *_args, **_kwargs: None)

    processor = object.__new__(processor_module.ExecutionEventProcessor)
    processor._fees = lambda *_args, **_kwargs: ({"USDT": 0.1}, 0.1)
    event = {
        "id": 501,
        "exchange_id": "okx",
        "market_type": "swap",
        "symbol": "BTC/USDT",
        "side": "sell",
        "quantity": 0.01,
        "price": 62000,
        "exchange_order_id": "child-501",
        "exchange_fill_id": "fill-501",
        "fee_status": "actual",
        "raw_json": {},
        "_client": object(),
        "_exchange_config": {},
    }
    binding = {
        "id": 8,
        "pending_order_id": 7,
        "strategy_id": 11,
        "strategy_run_id": 13,
        "signal_type": "open_long",
        "matched_identifier_role": "stop_loss",
    }

    processor._project_native_protection_fill(event, binding)

    assert len(persisted) == 1
    fill = persisted[0]
    assert fill["signal_type"] == "close_long"
    assert fill["close_reason"] == "native_stop_loss"
    assert fill["order_id"] == 0
    assert fill["execution_event_id"] == 501
