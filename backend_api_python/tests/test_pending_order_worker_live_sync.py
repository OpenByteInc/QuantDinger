from __future__ import annotations

import ast
import inspect
import json
import textwrap

import pytest

from app.services import pending_order_worker as worker_module
from app.services.live_trading.adapters import LiveOrderPhaseAdapter
from app.services.pending_orders.sent_order_recovery import (
    is_final_fill,
    normalize_live_order_status,
    tracked_fill_baseline,
)


def test_live_order_adapter_call_uses_only_supported_constructor_keywords():
    """Keep the worker and adapter constructor contract in sync."""
    source = textwrap.dedent(inspect.getsource(worker_module.PendingOrderWorker._execute_live_order))
    tree = ast.parse(source)
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "LiveOrderPhaseAdapter"
    ]

    assert len(calls) == 1
    passed_keywords = {keyword.arg for keyword in calls[0].keywords if keyword.arg}
    supported_keywords = set(inspect.signature(LiveOrderPhaseAdapter).parameters)
    assert passed_keywords <= supported_keywords


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
        "amount": 1.0,
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


def test_bitget_precision_normalized_fill_releases_residual_sent_order(monkeypatch):
    class Client:
        def normalize_base_order_size(self, *, symbol, product_type, base_size):
            assert symbol == "BTC/USDT"
            assert product_type == "USDT-FUTURES"
            assert base_size == pytest.approx(0.037993191620)
            return 0.0379

    row = _row(filled=0.0379, avg_price=62_519.1)
    row.update({
        "exchange_id": "bitget",
        "amount": 0.037993191620,
        "payload_json": (
            '{"strategy_id":9,"strategy_run_id":3,"order_intent_id":5,'
            '"amount":0.037993191620}'
        ),
    })
    worker = object.__new__(worker_module.PendingOrderWorker)
    worker._claim_live_sent_order = lambda order_id: dict(row)
    snapshots = []
    worker._update_live_sent_order_snapshot = lambda **kwargs: snapshots.append(kwargs)
    monkeypatch.setattr(
        worker_module,
        "load_strategy_configs",
        lambda strategy_id: {"user_id": 7, "exchange_config": {"exchange_id": "bitget"}},
    )
    monkeypatch.setattr(worker_module, "resolve_exchange_config", lambda cfg, user_id: dict(cfg))
    monkeypatch.setattr(worker_module, "create_client", lambda cfg, market_type: Client())
    monkeypatch.setattr(
        worker_module,
        "query_grid_order_fill",
        lambda *args, **kwargs: (0.0379, 62_519.1, "partial"),
    )
    monkeypatch.setattr(worker_module, "persist_strategy_fill", lambda **kwargs: None)
    monkeypatch.setattr(worker_module, "append_strategy_log", lambda *args, **kwargs: None)

    worker._sync_one_live_sent_order(row)

    assert snapshots[0]["status"] == "filled"
    assert snapshots[0]["filled"] == pytest.approx(0.0379)


def test_bitget_executable_quantity_uses_base_unit_after_contract_rounding():
    class Client:
        def normalize_base_order_size(self, **kwargs):
            assert kwargs["base_size"] == pytest.approx(0.037993191620)
            return 0.0379

    from app.services.pending_orders.order_quantities import exchange_executable_base_quantity

    assert exchange_executable_base_quantity(
        Client(),
        exchange_id="bitget",
        symbol="BTC/USDT",
        market_type="swap",
        requested=0.037993191620,
        exchange_config={"product_type": "USDT-FUTURES"},
    ) == pytest.approx(0.0379)


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


def test_tracked_fill_baseline_takes_the_largest_known_source():
    """A stale live_fill_sync marker (written before the fill landed) must
    not blank the baseline: the executor fill in the row and the persisted
    ledger both know about the fill, so the largest source wins."""
    row = {
        "exchange_response_json": json.dumps(
            {"live_fill_sync": {"tracked_filled": 0.0, "tracked_avg_price": 0.0}}
        )
    }
    filled, avg = tracked_fill_baseline(
        row,
        exchange_order_id="o1",
        previous_filled=0.1,
        previous_avg=685.48,
        persisted_filled=0.1,
        persisted_avg=685.48,
    )
    assert filled == pytest.approx(0.1)
    assert avg == pytest.approx(685.48)


def test_tracked_fill_baseline_ledger_beats_partial_stale_marker():
    row = {
        "exchange_response_json": json.dumps(
            {"live_fill_sync": {"tracked_filled": 0.05, "tracked_avg_price": 100.0}}
        )
    }
    filled, avg = tracked_fill_baseline(
        row,
        exchange_order_id="o1",
        previous_filled=0.0,
        previous_avg=0.0,
        persisted_filled=0.1,
        persisted_avg=101.0,
    )
    assert filled == pytest.approx(0.1)
    assert avg == pytest.approx(101.0)


def test_live_sent_sync_ignores_stale_zero_fill_sync_marker(monkeypatch):
    """Regression for a live double-booked fill: the row carried a
    live_fill_sync marker with tracked_filled=0 from a pre-fill sync, the
    executor then recorded the real 0.1 fill, and the next reconciliation
    re-booked the full 0.1 (position 0.1 -> 0.2 in the ledger while the
    exchange held 0.1). The ledger baseline must clamp the delta to zero."""
    row = _row(filled=0.1, avg_price=685.48)
    row["exchange_response_json"] = json.dumps(
        {"live_fill_sync": {"tracked_filled": 0.0, "tracked_avg_price": 0.0}}
    )
    worker, snapshots, persisted = _worker(
        monkeypatch,
        row,
        exchange_fill=(0.1, 685.48, "filled"),
    )
    monkeypatch.setattr(
        worker_module,
        "persisted_order_fill_baseline",
        lambda **kwargs: (0.1, 685.48),
    )

    worker._sync_one_live_sent_order(row)

    assert persisted == []
    assert snapshots[0]["status"] == "filled"
    assert snapshots[0]["filled"] == pytest.approx(0.1)
