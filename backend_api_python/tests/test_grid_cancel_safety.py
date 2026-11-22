"""Cancel failures and racing fills must remain visible to reconciliation."""
from types import SimpleNamespace

import pytest

from app.services.grid.engine import GridEngine
from app.services.grid.levels import GridCellSpec
from app.services.grid.resting_orders_repo import GridRestingOrder


def make_order(identifier=1, purpose="long_exit", quantity=0.04, processed=0.0):
    return GridRestingOrder(id=identifier, strategy_id=1, symbol="BTC/USDT", cell_index=1,
        purpose=purpose, side="sell" if purpose.endswith("exit") else "buy", pos_side="long",
        price=100, quantity=quantity, exchange_order_id=str(identifier), processed_fill_qty=processed, status="open")


@pytest.fixture
def harness(monkeypatch):
    monkeypatch.setattr("app.services.grid.engine.GridRestingOrderRepository", lambda: SimpleNamespace())
    monkeypatch.setattr("app.services.grid.engine.GridCellRepository", lambda: SimpleNamespace())
    engine = GridEngine(1, "BTC/USDT",
        {"market_type": "swap", "bot_params": {"gridDirection": "long", "gridCount": 5}}, {},
        create_client_fn=lambda: object(), enqueue_market=lambda *a, **k: False)
    state = SimpleNamespace(orders=[make_order()], updates=[], placements=[], cancelled=[],
        snapshot=(0.0, 100.0, "cancelled"), write_ok=True)

    def update(*args, **kwargs):
        state.updates.append((args, kwargs))
        return state.write_ok

    engine._orders = SimpleNamespace(list_open=lambda *a: state.orders, update_status=update)
    monkeypatch.setattr(engine, "_normalize_grid_base_qty", lambda qty, price: qty)
    monkeypatch.setattr(engine, "_place_limit", lambda *a, **k: state.placements.append((a, k)) or True)
    monkeypatch.setattr(engine, "_cell_record", lambda *a: SimpleNamespace(leg_size=0.05))
    monkeypatch.setattr("app.services.live_trading.position_query.query_exchange_position_size", lambda **k: 0.05)
    monkeypatch.setattr("app.services.grid.engine.append_strategy_log", lambda *a, **k: None)
    monkeypatch.setattr("app.services.grid.engine.cancel_grid_order", lambda *a, **k: state.cancelled.append(k))
    monkeypatch.setattr("app.services.grid.engine.query_grid_order_fill", lambda *a, **k: state.snapshot)
    return engine, state


def resize(engine):
    return engine._ensure_cell_exit_coverage(GridCellSpec(index=1, lower_price=99, upper_price=100),
        purpose="long_exit", side="sell", price=100, pos_side="long", quantity=0.02)


def fail(*args, **kwargs):
    raise TimeoutError("injected failure")


@pytest.mark.parametrize("missing", [False, True])
def test_client_unavailable_keeps_existing_exit(monkeypatch, harness, missing):
    engine, state = harness
    monkeypatch.setattr(engine, "_create_client", (lambda: None) if missing else fail)
    assert resize(engine) is False
    assert not state.updates and not state.placements


@pytest.mark.parametrize("purpose,method", [
    ("long_entry", "_dedupe_open_entry_orders"), ("long_exit", "_dedupe_open_exit_orders")])
@pytest.mark.parametrize("failure", ["client", "cancel", "query"])
def test_dedupe_failure_preserves_order(monkeypatch, harness, purpose, method, failure):
    engine, state = harness
    state.orders = [make_order(1, purpose, 0.05), make_order(2, purpose)]
    if failure == "client":
        monkeypatch.setattr(engine, "_create_client", fail)
    else:
        monkeypatch.setattr("app.services.grid.engine." + ("cancel_grid_order" if failure == "cancel" else "query_grid_order_fill"), fail)
        if failure == "cancel":
            state.snapshot = (0.0, 100.0, "unknown")
    getattr(engine, method)(purpose)
    assert not state.updates


@pytest.mark.parametrize("status,filled", [
    ("open", 0.0), ("unknown", 0.0), ("partial", 0.01), ("filled", 0.04), ("cancelled", 0.01)])
def test_ack_or_racing_fill_does_not_allow_replacement(harness, status, filled):
    engine, state = harness
    state.snapshot = (filled, 100, status)
    assert resize(engine) is False
    assert not state.updates and not state.placements


def test_existing_unprocessed_fill_prevents_replacement(harness):
    engine, state = harness
    state.orders[0].filled_quantity = 0.01
    assert resize(engine) is False
    assert not state.updates and not state.placements


def test_confirmed_cancel_replaces_after_persisting(harness):
    engine, state = harness
    assert resize(engine) is True
    assert state.updates == [((1,), {"status": "cancelled"})]
    assert len(state.placements) == 1
    assert state.placements[0][1]["reduce_only"] is True
    assert state.placements[0][1]["quantity"] == 0.02


def test_processed_partial_fill_can_be_replaced(harness):
    engine, state = harness
    state.orders[0].processed_fill_qty = 0.01
    state.snapshot = (0.01, 100, "cancelled")
    assert resize(engine) is True


def test_persistence_failure_does_not_allow_replacement(harness):
    engine, state = harness
    state.write_ok = False
    assert resize(engine) is False
    assert not state.placements


@pytest.mark.parametrize("purpose,method", [
    ("long_entry", "_dedupe_open_entry_orders"), ("long_exit", "_dedupe_open_exit_orders")])
def test_confirmed_dedupe_keeps_largest_order(harness, purpose, method):
    engine, state = harness
    state.orders = [make_order(1, purpose, 0.05), make_order(2, purpose)]
    getattr(engine, method)(purpose)
    assert state.updates == [((2,), {"status": "cancelled"})]


@pytest.mark.parametrize("method", ["cancel_all_orders_on_exchange", "cancel_exit_orders_on_exchange"])
def test_bulk_cancel_failure_keeps_order(monkeypatch, harness, method):
    engine, state = harness
    monkeypatch.setattr("app.services.grid.engine.cancel_grid_order", fail)
    state.snapshot = (0.0, 100.0, "unknown")
    getattr(engine, method)()
    assert not state.updates


def test_shutdown_never_blanket_cancels_local_orders(monkeypatch, harness):
    engine, state = harness
    monkeypatch.setattr("app.services.grid.engine.cancel_grid_order", fail)
    state.snapshot = (0.0, 100.0, "unknown")
    engine._orders.cancel_all = lambda *a: pytest.fail("Blanket cancellation hides unresolved orders")
    engine._cells.release_cancelled_working_orders = lambda *a: 0
    engine.shutdown()
    assert not state.updates


def test_retry_after_fill_posting_recomputes_coverage(harness):
    engine, state = harness
    state.snapshot = (0.01, 100, "cancelled")
    assert resize(engine) is False
    state.orders[0].processed_fill_qty = 0.01
    cell = GridCellSpec(index=1, lower_price=99, upper_price=100)
    assert engine._ensure_cell_exit_coverage(cell, purpose="long_exit", side="sell", price=100,
        pos_side="long", quantity=0.02) is True
    assert state.placements[0][1]["quantity"] == 0.02


@pytest.mark.parametrize("side", ["long", "short"])
def test_periodic_pass_retries_failed_exit_resize(monkeypatch, harness, side):
    from app.services.live_trading.grid_cells import GridCellState

    engine, state = harness
    engine.cfg.grid_direction = side
    engine._bootstrapped = True
    state.orders = [make_order(purpose=side + "_exit")]
    engine._orders.has_open_for_cell = lambda *a: True
    engine._cells.list_cells = lambda *a: [SimpleNamespace(cell_index=1, leg_size=0.05,
        state=GridCellState.LONG_HELD if side == "long" else GridCellState.SHORT_HELD)]
    monkeypatch.setattr(engine, "_levels_and_cells", lambda: ([], [GridCellSpec(index=1, lower_price=99, upper_price=100)]))
    place = engine._place_limit
    monkeypatch.setattr(engine, "_place_limit", lambda *a, **k: False)
    engine.sync_held_cell_exits(100)
    assert not state.updates and not state.placements
    monkeypatch.setattr(engine, "_place_limit", place)
    engine.sync_held_cell_exits(100)
    assert len(state.placements) == 1
    assert state.placements[0][1]["quantity"] == pytest.approx(0.01)


@pytest.mark.parametrize("exchange", ["binance", "okx", "bybit", "bitget", "gate", "htx"])
def test_snapshot_outage_never_withdraws_exit_for_resize(monkeypatch, harness, exchange):
    engine, state = harness
    engine.exchange_config = {"exchange_id": exchange}
    monkeypatch.setattr("app.services.live_trading.position_query.query_exchange_position_size", fail)
    assert resize(engine) is False
    assert not state.cancelled and not state.updates and not state.placements


def test_partial_entry_topup_preserves_working_exit(harness):
    engine, state = harness
    assert engine._ensure_cell_exit_coverage(GridCellSpec(index=1, lower_price=99, upper_price=100),
        purpose="long_exit", side="sell", price=100, pos_side="long", quantity=0.05)
    assert not state.cancelled and not state.updates
    assert state.placements[0][1]["quantity"] == pytest.approx(0.01)


def test_legitimate_topups_are_not_deduped(harness):
    engine, state = harness
    state.orders = [make_order(1, quantity=0.04), make_order(2, quantity=0.01)]
    engine._dedupe_open_exit_orders("long_exit")
    assert not state.cancelled


def test_pending_topup_is_not_submitted_again(harness):
    engine, state = harness
    state.orders[0].status = "pending"
    assert resize(engine) is False
    assert not state.cancelled and not state.placements


def test_cell_release_excludes_open_orders_and_unprocessed_fills(monkeypatch):
    from contextlib import nullcontext
    from app.services.live_trading.grid_cells import GridCellRepository

    calls = []
    cursor = SimpleNamespace(execute=lambda sql, args: calls.append((sql, args)), rowcount=1, close=lambda: None)
    db = SimpleNamespace(cursor=lambda: cursor, commit=lambda: None)
    monkeypatch.setattr("app.services.live_trading.grid_cells.get_db_connection", lambda: nullcontext(db))
    repo = GridCellRepository.__new__(GridCellRepository)
    assert repo.release_cancelled_working_orders(1, "BTC/USDT") == 1
    sql, args = calls[0]
    assert "NOT EXISTS" in sql
    assert "o.cell_index = qd_grid_cells.cell_index" in sql
    assert "o.status IN ('pending', 'open', 'partial')" in sql
    assert "o.filled_quantity > o.processed_fill_qty + 1e-12" in sql
    assert "BTC/USDT" in args
