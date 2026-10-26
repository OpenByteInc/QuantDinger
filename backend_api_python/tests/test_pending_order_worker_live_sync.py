from __future__ import annotations

import ast
import inspect
import json
import textwrap
from types import SimpleNamespace

import pytest

from app.services import pending_order_worker as worker_module
from app.services.live_trading.adapters import LiveOrderPhaseAdapter
from app.services.pending_orders import live_sync_recovery
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


def test_alpaca_filled_order_backfills_authoritative_zero_fee(monkeypatch):
    class Broker:
        def get_order_status(self, order_id):
            assert order_id == "alpaca-order-41"
            return SimpleNamespace(
                status="filled",
                filled=1.0,
                avg_price=210.5,
                raw={
                    "fee_status": "actual_zero",
                    "fees_by_ccy": {"USD": 0.0},
                    "commission": 0.0,
                    "commission_ccy": "USD",
                    "commission_quote": 0.0,
                },
            )

    row = {
        "id": 41,
        "user_id": 7,
        "strategy_id": 9,
        "symbol": "AAPL",
        "signal_type": "open_long",
        "market_type": "USStock",
        "exchange_id": "alpaca",
        "exchange_order_id": "alpaca-order-41",
        "amount": 1.0,
        "filled": 1.0,
        "avg_price": 210.5,
        "payload_json": '{"strategy_id":9,"strategy_run_id":3,"order_intent_id":5,"symbol":"AAPL","signal_type":"open_long"}',
    }
    worker = object.__new__(worker_module.PendingOrderWorker)
    worker._claim_alpaca_sent_order = lambda order_id: dict(row)
    snapshots = []
    worker._update_alpaca_sent_order_snapshot = lambda **kwargs: snapshots.append(kwargs)
    persisted = []
    monkeypatch.setattr(worker_module, "AlpacaClient", Broker)
    monkeypatch.setattr(worker_module, "load_strategy_configs", lambda strategy_id: {
        "user_id": 7,
        "exchange_config": {"exchange_id": "alpaca"},
        "market_category": "USStock",
    })
    monkeypatch.setattr(worker_module, "resolve_exchange_config", lambda cfg, user_id: dict(cfg))
    monkeypatch.setattr(worker_module, "create_client", lambda cfg: Broker())
    monkeypatch.setattr(worker_module, "dispatch_requested_cancel", lambda *args, **kwargs: None)
    monkeypatch.setattr(worker_module, "persist_strategy_fill", lambda **kwargs: persisted.append(kwargs) or (None, None))
    monkeypatch.setattr(worker_module, "append_strategy_log", lambda *args, **kwargs: None)

    worker._sync_one_alpaca_sent_order(row)

    assert persisted[0]["filled"] == 0.0
    assert persisted[0]["cumulative_fees"] == {"USD": 0.0}
    assert persisted[0]["cumulative_commission_quote"] == 0.0
    assert persisted[0]["fee_status"] == "actual_zero"
    assert snapshots[0]["fee_status"] == "actual_zero"
    assert snapshots[0]["fee_source"] == "rest"


def test_claim_live_sent_order_transitions_the_claimed_row(monkeypatch):
    class Cursor:
        def execute(self, sql, params):
            assert "status = 'syncing'" in sql
            assert "COALESCE(filled, 0) <= 0" in sql
            assert "live_fee_sync:retry" in sql
            assert "live_fill_sync:retry_error" in sql
            assert params == (41, 90, 300)

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
    worker._stale_processing_sec = 90

    assert worker._claim_live_sent_order(41) == {"id": 41, "status": "syncing"}


def test_fetch_live_sent_orders_defers_recent_retry_errors(monkeypatch):
    executed = []

    class Cursor:
        def execute(self, sql, params):
            executed.append((sql, params))

        def fetchall(self):
            return []

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
    worker._stale_processing_sec = 90
    worker._fee_sync_retry_sec = 300

    assert worker._fetch_live_sent_orders(limit=50) == []

    select_sql, params = executed[1]
    assert "live_fill_sync:retry_error" in select_sql
    assert params == (90, 300, 50)


def test_fee_reconciliation_is_not_throttled_by_a_healthy_execution_stream():
    worker = object.__new__(worker_module.PendingOrderWorker)

    assert worker._should_rest_reconcile({"fee_reconciliation_needed": True}) is True


def test_fee_reconciliation_limits_each_exchange_account_batch(monkeypatch):
    worker = object.__new__(worker_module.PendingOrderWorker)
    worker._fee_sync_batch_per_account = 2
    rows = [
        {
            "id": order_id,
            "exchange_id": "binance",
            "credential_id": 7,
            "market_type": "spot",
            "fee_reconciliation_needed": True,
        }
        for order_id in range(1, 5)
    ]
    monkeypatch.setattr(worker, "_fetch_live_sent_orders", lambda limit: rows)
    synced = []
    monkeypatch.setattr(worker, "_sync_one_live_sent_order", lambda row: synced.append(row["id"]))

    worker._sync_live_sent_orders()

    assert synced == [1, 2]


def test_live_sent_sync_restores_claim_after_reconciliation_error(monkeypatch):
    worker = object.__new__(worker_module.PendingOrderWorker)
    worker._fee_sync_batch_per_account = 5
    row = {"id": 41, "exchange_id": "gate", "credential_id": 7, "market_type": "swap"}
    monkeypatch.setattr(worker, "_fetch_live_sent_orders", lambda limit: [row])
    monkeypatch.setattr(worker, "_should_rest_reconcile", lambda _row: True)
    monkeypatch.setattr(
        worker,
        "_sync_one_live_sent_order",
        lambda _row: (_ for _ in ()).throw(RuntimeError("snapshot mismatch")),
    )
    restored = []
    monkeypatch.setattr(
        worker_module,
        "restore_live_sent_order_after_sync_error",
        lambda restored_row, exc: restored.append((restored_row["id"], str(exc))),
    )

    worker._sync_live_sent_orders()

    assert restored == [(41, "snapshot mismatch")]


def test_restore_live_sent_order_after_sync_error_releases_syncing_claim(monkeypatch):
    executed = []

    class Cursor:
        def execute(self, sql, params):
            executed.append((sql, params))

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
            executed.append(("commit", None))

    monkeypatch.setattr(live_sync_recovery, "get_db_connection", lambda: Database())

    live_sync_recovery.restore_live_sent_order_after_sync_error(
        {"id": 41},
        RuntimeError("snapshot mismatch"),
    )

    sql, params = executed[0]
    assert "SET status = 'sent'" in sql
    assert "status = 'syncing'" in sql
    assert "live_fill_sync:retry_error" in sql
    assert params == ("snapshot mismatch", 41)
    assert executed[1] == ("commit", None)


@pytest.mark.parametrize("exchange_id", ["gate", "okx", "bitget", "bybit", "binance", "htx"])
def test_live_sent_sync_ignores_float_tail_across_crypto_exchanges(monkeypatch, exchange_id):
    row = _row(filled=0.0048, avg_price=84_590.1)
    row.update(
        {
            "exchange_id": exchange_id,
            "amount": 0.005174558160819331,
            "signal_type": "add_long",
            "payload_json": (
                '{"strategy_id":9,"strategy_run_id":3,"order_intent_id":5,'
                '"signal_type":"add_long","amount":0.005174558160819331}'
            ),
        }
    )
    worker, snapshots, persisted = _worker(
        monkeypatch,
        row,
        exchange_fill=(0.004800000000000001, 84_590.09999999999, "cancelled"),
    )

    worker._sync_one_live_sent_order(row)

    assert persisted[0]["filled"] == 0
    assert persisted[0]["cumulative_filled"] == pytest.approx(0.0048)
    assert persisted[0]["cumulative_average_price"] == pytest.approx(84_590.1)
    assert snapshots[0]["status"] == "cancelled"
    assert snapshots[0]["filled"] == pytest.approx(0.0048)


def test_live_sent_sync_finalizes_after_restart_without_duplicate_fill(monkeypatch):
    row = _row(filled=1.0, avg_price=101.0)
    worker, snapshots, persisted = _worker(
        monkeypatch,
        row,
        exchange_fill=(1.0, 101.0, "filled"),
    )

    worker._sync_one_live_sent_order(row)

    assert len(persisted) == 1
    assert persisted[0]["filled"] == 0
    assert persisted[0]["cumulative_filled"] == pytest.approx(row["filled"])
    assert snapshots[0]["status"] == "filled"
    assert snapshots[0]["exchange_status"] == "filled"


def test_live_sent_sync_marks_pending_order_fee_as_actual(monkeypatch):
    row = _row(filled=1.0, avg_price=101.0)
    worker, snapshots, persisted = _worker(
        monkeypatch,
        row,
        exchange_fill=(1.0, 101.0, "filled"),
    )
    monkeypatch.setattr(
        worker_module,
        "wait_live_order_fill",
        lambda **kwargs: {
            "filled": 1.0,
            "avg_price": 101.0,
            "fees_by_ccy": {"USDT": 0.05},
            "fee_status": "actual",
        },
    )

    worker._sync_one_live_sent_order(row)

    assert len(persisted) == 1
    assert snapshots[0]["fee_status"] == "actual"
    assert snapshots[0]["fee_source"] == "rest"


def test_stale_zero_sync_marker_cannot_hide_executor_fill_in_row():
    row = {
        "exchange_response_json": json.dumps(
            {"live_fill_sync": {"tracked_filled": 0.0, "tracked_avg_price": 0.0}}
        )
    }

    filled, avg = tracked_fill_baseline(
        row,
        exchange_order_id="exchange-41",
        previous_filled=0.1,
        previous_avg=685.48,
    )

    assert filled == pytest.approx(0.1)
    assert avg == pytest.approx(685.48)


def test_live_sent_sync_does_not_rebook_fill_hidden_by_stale_marker(monkeypatch):
    row = _row(filled=0.1, avg_price=685.48)
    row["exchange_response_json"] = json.dumps(
        {"live_fill_sync": {"tracked_filled": 0.0, "tracked_avg_price": 0.0}}
    )
    worker, snapshots, persisted = _worker(
        monkeypatch,
        row,
        exchange_fill=(0.1, 685.48, "filled"),
    )

    worker._sync_one_live_sent_order(row)

    assert len(persisted) == 1
    assert persisted[0]["filled"] == 0
    assert persisted[0]["cumulative_filled"] == pytest.approx(row["filled"])
    assert snapshots[0]["status"] == "filled"
    assert snapshots[0]["filled"] == pytest.approx(0.1)


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


def test_close_retry_leg_baseline_does_not_double_book_fill(monkeypatch):
    row = _row(filled=4.76, avg_price=100.0)
    row["signal_type"] = "close_short"
    row["amount"] = 4.76
    row["exchange_order_id"] = "retry-1"
    row["exchange_response_json"] = json.dumps({"phases": {
        "close_reconciliation": {
            "status": "closed",
            "target_quantity": 4.76,
            "remaining_quantity": 0.0,
            "retry_orders": [{
                "exchange_order_id": "retry-1",
                "filled_quantity": 1.9,
                "avg_price": 100.0,
            }],
        }
    }})
    worker, snapshots, persisted = _worker(
        monkeypatch,
        row,
        exchange_fill=(1.9, 100.0, "filled"),
    )

    worker._sync_one_live_sent_order(row)

    assert len(persisted) == 1
    assert persisted[0]["filled"] == 0
    assert snapshots[0]["filled"] == pytest.approx(4.76)
    assert snapshots[0]["status"] == "filled"


def test_unresolved_close_reconciliation_cannot_be_promoted_by_child_status(monkeypatch):
    row = _row(filled=2.86, avg_price=100.0)
    row["signal_type"] = "close_short"
    row["amount"] = 4.76
    row["exchange_response_json"] = json.dumps({"phases": {
        "close_reconciliation": {
            "status": "partial",
            "target_quantity": 4.76,
            "remaining_quantity": 1.9,
            "retry_orders": [],
        }
    }})
    worker, snapshots, _persisted = _worker(
        monkeypatch,
        row,
        exchange_fill=(2.86, 100.0, "filled"),
    )

    worker._sync_one_live_sent_order(row)

    assert snapshots[0]["status"] == "sent"
    response = json.loads(snapshots[0]["exchange_response_json"])
    assert response["phases"]["close_reconciliation"]["status"] == "partial"


def test_restart_after_market_poll_timeout_books_only_the_market_tail(monkeypatch):
    row = _row(filled=2.0, avg_price=100.0)
    row["amount"] = 3.0
    row["exchange_response_json"] = json.dumps({"phases": {"executor": {
        "limit_summary": {"exchange_order_id": "limit-1", "filled_qty": 2.0, "avg_price": 100.0},
        "market_summary": {"exchange_order_id": "exchange-41", "filled_qty": 0.0, "avg_price": 0.0},
    }}})
    worker, snapshots, persisted = _worker(monkeypatch, row, exchange_fill=(1.0, 101.0, "filled"))
    worker._sync_one_live_sent_order(row)
    assert persisted[0]["filled"] == 1.0
    assert persisted[0]["avg_price"] == 101.0
    assert snapshots[0]["filled"] == 3.0
    assert snapshots[0]["avg_price"] == pytest.approx(100 + 1 / 3)
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


def test_ibkr_accepted_order_with_local_persistence_failure_is_reconciled(monkeypatch):
    sequence = []

    class Result:
        success = True
        order_id = "ibkr-accepted"
        filled = 0.0
        avg_price = 0.0
        status = "Submitted"
        message = "Order submitted"
        raw = {"status": "Submitted", "orderRef": "qd_9_52"}

    class Client:
        def place_market_order(self, **kwargs):
            assert sequence == ["prepared"]
            assert kwargs["client_order_id"] == "qd_9_52"
            sequence.append("submitted")
            return Result()

    worker = object.__new__(worker_module.PendingOrderWorker)
    worker._mark_sent = lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("database unavailable"))
    unknown = []
    worker._mark_submit_unknown = lambda **kwargs: unknown.append(kwargs)
    worker._mark_failed = lambda **kwargs: pytest.fail(str(kwargs))
    monkeypatch.setattr(worker_module, "persist_strategy_fill", lambda **_kwargs: None)
    monkeypatch.setattr(worker_module, "append_strategy_log", lambda *args, **kwargs: None)

    worker._execute_ibkr_order(
        order_id=52,
        order_row={},
        payload={"signal_type": "open_long", "symbol": "AAPL", "amount": 10, "ref_price": 200},
        client=Client(),
        strategy_id=9,
        exchange_config={"exchange_id": "ibkr", "market_type": "USStock"},
        client_order_id="qd_9_52",
        prepare_submission=lambda: sequence.append("prepared"),
        _notify_live_best_effort=lambda **kwargs: None,
        _console_print=lambda *args, **kwargs: None,
    )

    assert unknown == [{"order_id": 52, "error": "ibkr_submit_unknown:database unavailable"}]
    assert sequence == ["prepared", "submitted"]


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
