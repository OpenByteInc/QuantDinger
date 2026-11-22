"""Fault injection for durable grid submits; no exchange or database is contacted."""

from contextlib import nullcontext
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.services.grid.engine import GridEngine
from app.services.grid.levels import GridCellSpec
from app.services.grid.resting_orders_repo import GridOrderLedgerUnavailable, GridRestingOrderRepository
from app.services.live_trading.base import LiveOrderResult, LiveTradingError


@pytest.fixture
def harness(monkeypatch):
    state = SimpleNamespace(rows=[], submits=[], cells=[], logs=[], cancels=[], insert_ok=True,
                            persist_ok=True, error=None, order_id="venue-1", during_submit=None)
    repo = GridRestingOrderRepository.__new__(GridRestingOrderRepository)
    repo.list_open = lambda *a: deepcopy([row for row in state.rows if row.status in {"pending", "open", "partial"}])

    def insert(row):
        if not state.insert_ok:
            return None
        row.id = len(state.rows) + 1
        state.rows.append(deepcopy(row))
        return row.id

    def update(identifier, *, status, **kwargs):
        state.rows[identifier - 1].status = status
        return True

    def ack(identifier, exchange_order_id):
        if not state.persist_ok:
            raise RuntimeError("database disconnected after submit")
        row = state.rows[identifier - 1]
        if row.status not in {"pending", "open"} or row.filled_quantity or row.processed_fill_qty:
            return False
        row.status = "open"
        row.exchange_order_id = exchange_order_id
        return True

    repo.insert = insert
    repo.update_status = update
    repo.acknowledge_submission = ack
    monkeypatch.setattr("app.services.grid.engine.GridRestingOrderRepository", lambda: repo)
    monkeypatch.setattr("app.services.grid.engine.GridCellRepository", lambda: SimpleNamespace(
        update_state=lambda *a, **k: state.cells.append(k) or True))
    monkeypatch.setattr("app.utils.db.get_db_transaction", nullcontext)
    monkeypatch.setattr("app.services.grid.resting_orders_repo.get_db_transaction", nullcontext)
    monkeypatch.setattr("app.services.live_trading.fill_accounting.lock_strategy_fills", lambda *a: None)
    monkeypatch.setattr("app.services.grid.engine.append_strategy_log", lambda *a: state.logs.append(a))
    monkeypatch.setattr("app.services.execution_streams.repository.ExecutionEventRepository.register_binding", lambda *a, **k: None)
    monkeypatch.setattr("app.services.grid.engine.cancel_grid_order", lambda *a, **k: state.cancels.append(k))

    def submit(*args, **kwargs):
        assert state.rows[-1].status == "pending"
        state.submits.append(kwargs)
        if state.during_submit:
            state.during_submit()
        if state.error:
            raise state.error
        return LiveOrderResult("okx", state.order_id, 0, 0, {})

    monkeypatch.setattr("app.services.grid.engine.place_grid_limit_order", submit)

    def new_engine(exchange="okx"):
        engine = GridEngine(9, "BTC/USDT", {"market_type": "swap", "bot_params": {"gridCount": 5}},
            {"exchange_id": exchange, "credential_id": 7}, create_client_fn=lambda: object(),
            enqueue_market=lambda *a: pytest.fail("No emergency flatten may be enqueued"))
        engine._normalize_grid_base_qty = lambda qty, price: qty
        engine._grid_entry_ownership_allowed = lambda *a, **k: (True, {})
        engine._resolve_grid_exit_quantity = lambda *a, requested_qty, **k: requested_qty
        engine._cell_record = lambda *a: SimpleNamespace(leg_size=0.05, extra={})
        engine._cell_cost_basis = lambda *a, **k: 100
        return engine

    return new_engine, repo, state


def submit(engine, *, cell=1, exit_order=False):
    return engine._place_limit(GridCellSpec(cell, 99, 100),
        "long_exit" if exit_order else "long_entry", "sell" if exit_order else "buy", 100,
        pos_side="long", reduce_only=exit_order, quantity=0.01)


@pytest.mark.parametrize("exchange", ["binance", "okx", "bybit", "bitget", "gate", "htx"])
@pytest.mark.parametrize("exit_order", [False, True])
def test_timeout_intent_blocks_next_cycle_and_restart(harness, exchange, exit_order):
    new_engine, repo, state = harness
    state.error = TimeoutError("response lost after exchange acceptance")
    engine = new_engine(exchange)
    assert not submit(engine, exit_order=exit_order)
    assert not submit(engine, exit_order=exit_order)
    assert not submit(new_engine(exchange), cell=2, exit_order=exit_order)
    assert len(state.submits) == len(state.rows) == 1
    assert state.rows[0].status == "pending"
    assert state.rows[0].client_order_id == state.submits[0]["client_order_id"]
    assert not state.cancels and not engine.stop_requested


@pytest.mark.parametrize("failure", ["insert", "read", "missing_id", "ack"])
def test_storage_and_acknowledgement_failures_do_not_duplicate(harness, failure):
    new_engine, repo, state = harness
    if failure == "insert":
        state.insert_ok = False
    elif failure == "read":
        def broken_read(*args):
            raise GridOrderLedgerUnavailable("database unavailable")
        repo.list_open = broken_read
    elif failure == "missing_id":
        state.order_id = ""
    else:
        state.persist_ok = False
    engine = new_engine()
    assert not submit(engine)
    assert not submit(engine)
    assert len(state.submits) == (0 if failure in {"insert", "read"} else 1)
    assert not state.cancels


def test_explicit_rejection_releases_pending_intent(harness):
    new_engine, repo, state = harness
    state.error = LiveTradingError("Bybit retCode=10006: Too many visits")
    engine = new_engine()
    assert not submit(engine)
    assert state.rows[0].status == "cancelled"
    state.error = None
    assert submit(engine)
    assert len(state.submits) == 2
    assert state.rows[1].status == "open"


def test_recovered_open_order_blocks_duplicate_entry(harness):
    new_engine, repo, state = harness
    state.error = TimeoutError("read timed out")
    assert not submit(new_engine())
    state.rows[0].status = "open"
    state.rows[0].exchange_order_id = "reconciled-1"
    state.error = None
    assert not submit(new_engine())
    assert len(state.submits) == 1


def test_ack_does_not_overwrite_racing_fill(harness):
    new_engine, repo, state = harness
    def fill():
        state.rows[-1].status = "filled"
        state.rows[-1].filled_quantity = state.rows[-1].processed_fill_qty = 0.01
    state.during_submit = fill
    assert submit(new_engine())
    assert state.rows[0].status == "filled"
    assert not state.cells


def test_exit_topup_does_not_overwrite_total_cell_inventory(harness):
    new_engine, repo, state = harness
    assert submit(new_engine(), exit_order=True)
    assert state.cells[0]["leg_size"] == 0.05


def test_reservation_detects_concurrent_order_change(harness):
    new_engine, repo, state = harness
    engine = new_engine()
    assert submit(engine)
    stale = repo.list_open(9)
    state.rows[0].processed_fill_qty = 0.005
    candidate = deepcopy(state.rows[0])
    candidate.cell_index = 2
    assert repo.reserve_pending(candidate, stale) is None
    assert len(state.rows) == 1


def test_terminal_order_allows_new_cycle_after_reconciliation(harness):
    new_engine, repo, state = harness
    state.error = TimeoutError("read timed out")
    assert not submit(new_engine())
    state.rows[0].status = "cancelled"
    state.error = None
    assert submit(new_engine())
    assert len(state.submits) == 2


def test_stale_topup_observes_already_submitted_coverage(harness):
    new_engine, repo, state = harness
    engine = new_engine()
    cell = GridCellSpec(1, 99, 100)
    kwargs = dict(reduce_only=True, pos_side="long", quantity=0.01, coverage_target=0.01)
    assert engine._place_limit(cell, "long_exit", "sell", 100, **kwargs)
    assert not new_engine()._place_limit(cell, "long_exit", "sell", 100, **kwargs)
    assert len(state.submits) == 1


def test_ledger_outage_degrades_runner_without_entering_shutdown():
    from app.services.grid.runner import GridRestingRunner

    runner = GridRestingRunner.__new__(GridRestingRunner)
    runner.strategy_id = 9
    runner._last_operational_snapshot = {"open_orders": 3, "healthy": True, "error": ""}
    runner._last_operational_check_ts = 0
    def unavailable(*a, **k):
        raise GridOrderLedgerUnavailable("disconnected")
    runner._tick = unavailable
    runner._engine = SimpleNamespace(_orders=SimpleNamespace(list_open=unavailable))
    for _ in range(6):
        runner.tick(100)
    snapshot = runner.operational_snapshot()
    assert snapshot["healthy"] is False
    assert snapshot["error"] == "grid_order_ledger_unavailable"
    assert snapshot["open_orders"] == 3
