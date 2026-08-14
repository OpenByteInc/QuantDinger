from types import SimpleNamespace
from unittest.mock import MagicMock

import app.services.pending_order_worker as worker_module
from app.services.pending_order_worker import PendingOrderWorker


class _FakeFutuClient:
    def __init__(self, result):
        self.result = result
        self.disconnected = False

    def get_order_status(self, _order_id):
        return self.result

    def disconnect(self):
        self.disconnected = True


def _worker_with_claim(row):
    worker = PendingOrderWorker.__new__(PendingOrderWorker)
    worker._claim_futu_sent_order = MagicMock(return_value=dict(row))
    worker._release_futu_sync_claim = MagicMock()
    worker._update_futu_sent_order_snapshot = MagicMock()
    worker._unrecorded_pending_fill = MagicMock(return_value=0.0)
    return worker


def _configure_futu_strategy(monkeypatch, client):
    monkeypatch.setattr(
        worker_module,
        "load_strategy_configs",
        lambda _strategy_id: {
            "user_id": 1,
            "exchange_config": {"exchange_id": "futu"},
        },
    )
    monkeypatch.setattr(
        worker_module,
        "resolve_exchange_config",
        lambda config, user_id: config,
    )
    monkeypatch.setattr(worker_module, "create_client", lambda *_args, **_kwargs: client)
    monkeypatch.setattr(worker_module, "FutuClient", _FakeFutuClient)


def test_failed_status_query_requeues_without_overwriting_fill(monkeypatch):
    row = {
        "id": 17,
        "exchange_order_id": "OID-17",
        "strategy_id": 9,
        "filled": 5,
        "avg_price": 100,
    }
    result = SimpleNamespace(
        success=False,
        status="",
        filled=0,
        avg_price=0,
        raw={},
        message="OpenD unavailable",
    )
    client = _FakeFutuClient(result)
    worker = _worker_with_claim(row)
    _configure_futu_strategy(monkeypatch, client)

    worker._sync_one_futu_sent_order(row)

    worker._release_futu_sync_claim.assert_called_once_with(17, "not_finalized")
    worker._update_futu_sent_order_snapshot.assert_not_called()
    assert client.disconnected


def test_invalid_claimed_order_is_requeued_immediately():
    row = {
        "id": 18,
        "exchange_order_id": "OID-18",
        "strategy_id": 0,
    }
    worker = _worker_with_claim(row)

    worker._sync_one_futu_sent_order(row)

    worker._release_futu_sync_claim.assert_called_once_with(18, "not_finalized")
    worker._update_futu_sent_order_snapshot.assert_not_called()


def test_successful_regressive_snapshot_preserves_recorded_fill(monkeypatch):
    row = {
        "id": 19,
        "exchange_order_id": "OID-19",
        "strategy_id": 9,
        "filled": 5,
        "avg_price": 100,
    }
    result = SimpleNamespace(
        success=True,
        status="submitted",
        filled=0,
        avg_price=0,
        raw={},
        message="OK",
    )
    client = _FakeFutuClient(result)
    worker = _worker_with_claim(row)
    _configure_futu_strategy(monkeypatch, client)

    worker._sync_one_futu_sent_order(row)

    worker._release_futu_sync_claim.assert_not_called()
    update = worker._update_futu_sent_order_snapshot.call_args.kwargs
    assert update["filled"] == 5
    assert update["avg_price"] == 100
    assert update["status"] == "sent"
    assert client.disconnected


def test_terminal_snapshot_discards_cached_client(monkeypatch):
    row = {
        "id": 21,
        "exchange_order_id": "OID-21",
        "strategy_id": 9,
        "filled": 0,
        "avg_price": 0,
    }
    result = SimpleNamespace(
        success=True,
        status="filled",
        filled=0,
        avg_price=0,
        raw={},
        message="OK",
    )
    client = _FakeFutuClient(result)
    worker = _worker_with_claim(row)
    _configure_futu_strategy(monkeypatch, client)

    worker._sync_one_futu_sent_order(row)

    assert client.disconnected


def test_retry_uses_durable_trade_ledger_to_avoid_duplicate_fill(monkeypatch):
    row = {
        "id": 20,
        "exchange_order_id": "OID-20",
        "strategy_id": 9,
        "filled": 0,
        "avg_price": 0,
    }
    result = SimpleNamespace(
        success=True,
        status="partially_filled",
        filled=5,
        avg_price=101,
        raw={},
        message="OK",
    )
    client = _FakeFutuClient(result)
    worker = _worker_with_claim(row)
    _configure_futu_strategy(monkeypatch, client)
    persist = MagicMock()
    monkeypatch.setattr(worker_module, "persist_strategy_fill", persist)

    worker._sync_one_futu_sent_order(row)

    worker._unrecorded_pending_fill.assert_called_once_with(
        20,
        5.0,
        fail_closed=True,
        include_stream_events=True,
    )
    persist.assert_not_called()
    update = worker._update_futu_sent_order_snapshot.call_args.kwargs
    assert update["filled"] == 5


def test_futu_rest_sync_accounts_for_ingested_stream_events(monkeypatch):
    cursor = MagicMock()
    cursor.fetchone.side_effect = [
        {"recorded": 2},
        {"cumulative": 5, "incremental": 3},
    ]
    connection = MagicMock()
    connection.cursor.return_value = cursor
    context = MagicMock()
    context.__enter__.return_value = connection
    context.__exit__.return_value = False
    monkeypatch.setattr(worker_module, "get_db_connection", lambda: context)

    delta = PendingOrderWorker._unrecorded_pending_fill(
        20,
        6,
        fail_closed=True,
        include_stream_events=True,
    )

    assert delta == 1
    assert cursor.execute.call_count == 2
