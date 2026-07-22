from __future__ import annotations

import pytest

from app.services import pending_order_worker as worker_module
from app.services.pending_orders.sent_order_recovery import is_final_fill, normalize_live_order_status


def _row(*, filled: float, avg_price: float):
    return {
        "id": 41,
        "user_id": 7,
        "strategy_id": 9,
        "symbol": "BTC/USDT",
        "signal_type": "open_long",
        "market_type": "swap",
        "exchange_id": "binance",
        "exchange_order_id": "exchange-41",
        "filled": filled,
        "avg_price": avg_price,
        "payload_json": '{"strategy_id":9,"strategy_run_id":3,"order_intent_id":5}',
    }


def _worker(monkeypatch, row, *, exchange_fill):
    worker = object.__new__(worker_module.PendingOrderWorker)
    worker._claim_live_sent_order = lambda order_id: dict(row)
    snapshots = []
    worker._update_live_sent_order_snapshot = lambda **kwargs: snapshots.append(kwargs)
    persisted = []
    monkeypatch.setattr(
        worker_module,
        "load_strategy_configs",
        lambda strategy_id: {"user_id": 7, "exchange_config": {"exchange_id": "binance"}},
    )
    monkeypatch.setattr(worker_module, "resolve_exchange_config", lambda cfg, user_id: dict(cfg))
    monkeypatch.setattr(worker_module, "create_client", lambda cfg, market_type: object())
    monkeypatch.setattr(worker_module, "query_grid_order_fill", lambda *args, **kwargs: exchange_fill)
    monkeypatch.setattr(worker_module, "persist_strategy_fill", lambda **kwargs: persisted.append(kwargs))
    monkeypatch.setattr(worker_module, "append_strategy_log", lambda *args, **kwargs: None)
    return worker, snapshots, persisted


def test_live_sent_sync_persists_only_incremental_partial_fill(monkeypatch):
    row = _row(filled=0.25, avg_price=100.0)
    worker, snapshots, persisted = _worker(
        monkeypatch,
        row,
        exchange_fill=(0.75, 102.0, "partial"),
    )

    worker._sync_one_live_sent_order(row)

    assert len(persisted) == 1
    assert persisted[0]["filled"] == pytest.approx(0.5)
    assert persisted[0]["avg_price"] == pytest.approx(103.0)
    assert snapshots[0]["status"] == "sent"
    assert snapshots[0]["filled"] == pytest.approx(0.75)


def test_claim_live_sent_order_transitions_the_claimed_row(monkeypatch):
    class Cursor:
        def execute(self, sql, params):
            assert "status = 'syncing'" in sql
            assert "COALESCE(filled, 0) <= 0" in sql
            assert "live_fee_sync:retry" in sql
            assert params == (41, 300)

        def fetchone(self):
            return {"id": 41, "status": "syncing"}

        def close(self):
            return None

    class Database:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def cursor(self):
            return Cursor()

        def commit(self):
            return None

    monkeypatch.setattr(worker_module, "get_db_connection", lambda: Database())
    worker = object.__new__(worker_module.PendingOrderWorker)
    worker._fee_sync_retry_sec = 300

    assert worker._claim_live_sent_order(41) == {"id": 41, "status": "syncing"}


def test_live_sent_sync_finalizes_after_restart_without_duplicate_fill(monkeypatch):
    row = _row(filled=1.0, avg_price=101.0)
    worker, snapshots, persisted = _worker(
        monkeypatch,
        row,
        exchange_fill=(1.0, 101.0, "filled"),
    )

    worker._sync_one_live_sent_order(row)

    assert persisted == []
    assert snapshots[0]["status"] == "filled"
    assert snapshots[0]["exchange_status"] == "filled"


def test_live_sent_sync_tracks_market_leg_without_overwriting_limit_fill(monkeypatch):
    row = _row(filled=0.75, avg_price=100.0)
    row["exchange_response_json"] = (
        '{"phases":{"executor":{"market_summary":'
        '{"exchange_order_id":"exchange-41","filled_qty":0.5,"avg_price":101.0}}}}'
    )
    worker, snapshots, persisted = _worker(
        monkeypatch,
        row,
        exchange_fill=(0.75, 102.0, "filled"),
    )

    worker._sync_one_live_sent_order(row)

    assert persisted[0]["filled"] == pytest.approx(0.25)
    assert persisted[0]["avg_price"] == pytest.approx(104.0)
    assert snapshots[0]["filled"] == pytest.approx(1.0)
    assert snapshots[0]["avg_price"] == pytest.approx(101.0)
    assert snapshots[0]["status"] == "filled"


def test_live_sent_sync_keeps_terminal_fill_open_when_average_price_is_missing(monkeypatch):
    row = _row(filled=0.0, avg_price=0.0)
    worker, snapshots, persisted = _worker(
        monkeypatch,
        row,
        exchange_fill=(1.0, 0.0, "filled"),
    )

    worker._sync_one_live_sent_order(row)

    assert persisted == []
    assert snapshots[0]["status"] == "sent"
    assert snapshots[0]["exchange_status"] == "fill_price_missing"


def test_exchange_filled_status_accepts_quantity_precision_remainder():
    assert is_final_fill(0.1617456789, 0.161745, 58_705.0, "FILLED") is True


def test_non_terminal_partial_fill_remains_open():
    assert is_final_fill(1.0, 0.75, 58_705.0, "PARTIALLY_FILLED") is False


def test_terminal_fill_without_average_price_remains_open():
    assert is_final_fill(1.0, 1.0, 0.0, "FILLED") is False


def test_ibkr_submission_never_fabricates_a_fill_from_requested_amount(monkeypatch):
    class Result:
        success = True
        order_id = "ibkr-1"
        filled = 0.0
        avg_price = 0.0
        status = "Submitted"
        message = "Order submitted"
        raw = {"status": "Submitted"}

    class Client:
        def place_market_order(self, **kwargs):
            return Result()

    worker = object.__new__(worker_module.PendingOrderWorker)
    sent = []
    worker._mark_sent = lambda **kwargs: sent.append(kwargs)
    worker._mark_failed = lambda **kwargs: pytest.fail(str(kwargs))
    persisted = []
    monkeypatch.setattr(worker_module, "persist_strategy_fill", lambda **kwargs: persisted.append(kwargs))
    monkeypatch.setattr(worker_module, "append_strategy_log", lambda *args, **kwargs: None)

    worker._execute_ibkr_order(
        order_id=51,
        order_row={},
        payload={"signal_type": "open_long", "symbol": "AAPL", "amount": 10, "ref_price": 200},
        client=Client(),
        strategy_id=9,
        exchange_config={"exchange_id": "ibkr", "market_type": "USStock"},
        _notify_live_best_effort=lambda **kwargs: None,
        _console_print=lambda *args, **kwargs: None,
    )

    assert sent[0]["filled"] == 0.0
    assert sent[0]["avg_price"] == 0.0
    assert sent[0]["final_filled"] is False
    assert persisted == []


def test_live_sent_sync_reconciles_ibkr_submitted_order(monkeypatch):
    class Result:
        filled = 10.0
        avg_price = 201.5
        status = "Filled"

    class Client:
        def get_order_status(self, order_id):
            assert order_id == "exchange-41"
            return Result()

    row = _row(filled=0.0, avg_price=0.0)
    row["exchange_id"] = "ibkr"
    row["symbol"] = "AAPL"
    row["market_type"] = "USStock"
    row["payload_json"] = '{"strategy_id":9,"signal_type":"open_long","symbol":"AAPL"}'
    worker = object.__new__(worker_module.PendingOrderWorker)
    worker._claim_live_sent_order = lambda order_id: dict(row)
    snapshots = []
    worker._update_live_sent_order_snapshot = lambda **kwargs: snapshots.append(kwargs)
    persisted = []
    monkeypatch.setattr(
        worker_module,
        "load_strategy_configs",
        lambda strategy_id: {"user_id": 7, "exchange_config": {"exchange_id": "ibkr"}},
    )
    monkeypatch.setattr(worker_module, "resolve_exchange_config", lambda cfg, user_id: dict(cfg))
    monkeypatch.setattr(worker_module, "create_client", lambda cfg, market_type: Client())
    monkeypatch.setattr(
        worker_module,
        "query_grid_order_fill",
        lambda *args, **kwargs: pytest.fail("IBKR should use get_order_status"),
    )
    monkeypatch.setattr(worker_module, "persist_strategy_fill", lambda **kwargs: persisted.append(kwargs))
    monkeypatch.setattr(worker_module, "append_strategy_log", lambda *args, **kwargs: None)

    worker._sync_one_live_sent_order(row)

    assert persisted[0]["filled"] == 10.0
    assert persisted[0]["avg_price"] == 201.5
    assert snapshots[0]["status"] == "filled"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Filled", "filled"),
        ("PartiallyFilled", "partial"),
        ("PreSubmitted", "open"),
        ("ApiCancelled", "cancelled"),
        ("", "unknown"),
    ],
)
def test_live_order_status_normalization(raw, expected):
    assert normalize_live_order_status(raw) == expected
