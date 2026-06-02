"""Tests for professional grid engine."""

from __future__ import annotations

import pytest

from app.services.grid.config import GridBotConfig
from app.services.grid.levels import generate_cells, generate_levels
from app.services.grid.validator import validate_grid_config


def test_generate_levels_arithmetic():
    levels = generate_levels(90000, 100000, 10, "arithmetic")
    assert len(levels) == 10
    assert levels[0] == 90000
    assert abs(levels[-1] - 100000) < 1e-6


def test_generate_cells_count():
    levels = generate_levels(100, 200, 5, "arithmetic")
    cells = generate_cells(levels)
    assert len(cells) == 4


def test_validate_long_grid_ok():
    cfg = GridBotConfig(
        upper_price=100000,
        lower_price=90000,
        grid_count=10,
        amount_per_grid=100,
        grid_mode="arithmetic",
        grid_direction="long",
        initial_position_pct=0.3,
        order_mode="maker",
        boundary_action="pause",
        leverage=5,
        market_type="swap",
        margin_mode="cross",
    )
    ok, msg, _ = validate_grid_config(cfg, initial_capital=10000)
    assert ok is True
    assert msg == ""


def test_validate_rejects_bad_bounds():
    cfg = GridBotConfig(
        upper_price=100,
        lower_price=200,
        grid_count=10,
        amount_per_grid=50,
        grid_mode="arithmetic",
        grid_direction="long",
        initial_position_pct=0,
        order_mode="maker",
        boundary_action="pause",
        leverage=1,
        market_type="swap",
        margin_mode="cross",
    )
    ok, msg, _ = validate_grid_config(cfg)
    assert ok is False
    assert "upperPrice" in msg


def test_config_from_trading_config_initial_pct():
    tc = {
        "leverage": 5,
        "market_type": "swap",
        "bot_params": {
            "upperPrice": 100000,
            "lowerPrice": 90000,
            "gridCount": 10,
            "amountPerGrid": 100,
            "gridDirection": "long",
            "initialPositionPct": 30,
        },
    }
    cfg = GridBotConfig.from_trading_config(tc)
    assert cfg.initial_position_pct == 0.3
    assert cfg.grid_direction == "long"


def test_initial_market_target_qty_100u_20pct_20x():
    """100 USDT * 20% margin * 20x leverage ≈ 400 USDT notional at 72710."""
    from app.services.grid.engine import GridEngine
    from app.services.grid.exchange_orders import make_grid_initial_client_order_id

    tc = {
        "initial_capital": 100,
        "leverage": 20,
        "market_type": "swap",
        "bot_params": {
            "upperPrice": 80200,
            "lowerPrice": 69800,
            "gridCount": 24,
            "amountPerGrid": 4,
            "gridDirection": "long",
            "initialPositionPct": 20,
        },
    }
    engine = GridEngine(
        42,
        "BTC/USDT",
        tc,
        {},
        create_client_fn=lambda: None,
        enqueue_market=lambda *a, **k: False,
    )
    qty = engine._target_initial_base_qty(72710.0)
    assert qty == pytest.approx(400.0 / 72710.0, rel=1e-4)
    assert make_grid_initial_client_order_id(42, leg="long") == make_grid_initial_client_order_id(42, leg="long")
    assert make_grid_initial_client_order_id(42, leg="long") != make_grid_initial_client_order_id(42, leg="short")


def test_initial_market_recovers_from_exchange_without_new_order(monkeypatch):
    from app.services.grid.engine import GridEngine

    tc = {
        "initial_capital": 100,
        "leverage": 20,
        "market_type": "swap",
        "bot_params": {
            "upperPrice": 80200,
            "lowerPrice": 69800,
            "gridCount": 24,
            "amountPerGrid": 4,
            "gridDirection": "long",
            "initialPositionPct": 20,
        },
    }
    recorded = {"calls": 0}

    def fake_record(*args, **kwargs):
        recorded["calls"] += 1

    monkeypatch.setattr("app.services.grid.engine.record_grid_market_fill", fake_record)
    monkeypatch.setattr("app.services.grid.engine.append_strategy_log", lambda *a, **k: None)
    monkeypatch.setattr("app.services.grid.engine.persist_grid_resting_state", lambda *a, **k: None)
    monkeypatch.setattr("app.services.grid.engine.GridEngine._has_initial_market_trade", lambda self: False)

    engine = GridEngine(
        7,
        "BTC/USDT",
        tc,
        {},
        create_client_fn=lambda: object(),
        enqueue_market=lambda *a, **k: False,
    )
    target = engine._target_initial_base_qty(72710.0)
    monkeypatch.setattr("app.services.grid.engine.GridEngine._leg_position_qty", lambda self, side: target)

    ok = engine.run_initial_market_position(72710.0)
    assert ok is True
    assert engine._initial_done is True
    assert recorded["calls"] == 1


def test_sync_exit_coverage_places_long_exit_for_uncovered_position(monkeypatch):
    from app.services.grid.engine import GridEngine
    from app.services.grid.levels import generate_cells, generate_levels

    tc = {
        "initial_capital": 1000,
        "leverage": 2,
        "market_type": "swap",
        "bot_params": {
            "upperPrice": 758,
            "lowerPrice": 588,
            "gridCount": 23,
            "amountPerGrid": 20,
            "gridDirection": "long",
            "initialPositionPct": 20,
        },
    }
    placed = []

    def fake_place(self, cell, purpose, side, price, *, reduce_only, pos_side, quantity=None):
        placed.append(
            {
                "purpose": purpose,
                "side": side,
                "price": price,
                "reduce_only": reduce_only,
                "quantity": quantity,
                "cell": cell.index,
            }
        )
        return True

    monkeypatch.setattr("app.services.grid.engine.append_strategy_log", lambda *a, **k: None)
    monkeypatch.setattr("app.services.grid.engine.GridEngine._place_limit", fake_place)
    monkeypatch.setattr("app.services.grid.engine.GridEngine._leg_position_qty", lambda self, side: 4.08)
    monkeypatch.setattr("app.services.grid.engine.GridEngine._dedupe_open_exit_orders", lambda self, p: None)
    monkeypatch.setattr("app.services.grid.engine.GridEngine.sync_held_cell_exits", lambda self, px: 0)
    monkeypatch.setattr(
        "app.services.grid.engine.GridEngine._grid_base_qty",
        lambda self, px: 0.059111,
    )
    monkeypatch.setattr(
        "app.services.grid.engine.GridEngine._levels_and_cells",
        lambda self: (
            generate_levels(588, 758, 23, "arithmetic"),
            generate_cells(generate_levels(588, 758, 23, "arithmetic")),
        ),
    )

    class FakeOrders:
        def list_open(self, strategy_id):
            return []

        def has_open_for_cell(self, strategy_id, cell_index, purpose):
            return False

    engine = GridEngine(
        9,
        "BNB/USDT",
        tc,
        {},
        create_client_fn=lambda: object(),
        enqueue_market=lambda *a, **k: False,
    )
    engine._bootstrapped = True
    engine._orders = FakeOrders()

    n = engine.sync_exit_coverage(676.8)
    assert n == 1
    assert len(placed) == 1
    assert placed[0]["purpose"] == "long_exit"
    assert placed[0]["side"] == "sell"
    assert placed[0]["reduce_only"] is True
    assert placed[0]["quantity"] == pytest.approx(0.059111)
    assert placed[0]["price"] > 676.8 - 20  # active cell upper near current price


def test_sync_exit_coverage_skips_when_exits_already_cover_position(monkeypatch):
    from app.services.grid.engine import GridEngine
    from app.services.grid.resting_orders_repo import GridRestingOrder

    tc = {
        "initial_capital": 1000,
        "leverage": 2,
        "market_type": "swap",
        "bot_params": {
            "upperPrice": 758,
            "lowerPrice": 588,
            "gridCount": 23,
            "amountPerGrid": 20,
            "gridDirection": "long",
            "initialPositionPct": 20,
        },
    }
    monkeypatch.setattr("app.services.grid.engine.GridEngine._leg_position_qty", lambda self, side: 4.08)
    monkeypatch.setattr(
        "app.services.grid.engine.GridEngine._levels_and_cells",
        lambda self: ([], []),
    )

    open_exit = GridRestingOrder(
        id=1,
        strategy_id=9,
        symbol="BNB/USDT",
        cell_index=11,
        purpose="long_exit",
        side="sell",
        pos_side="long",
        reduce_only=True,
        price=676.7,
        quantity=4.08,
        quote_amount=20,
        client_order_id="x",
        exchange_order_id="y",
        status="open",
        filled_quantity=0,
        processed_fill_qty=0,
    )

    class FakeOrders:
        def list_open(self, strategy_id):
            return [open_exit]

        def has_open_for_cell(self, strategy_id, cell_index, purpose):
            return True

    engine = GridEngine(
        9,
        "BNB/USDT",
        tc,
        {},
        create_client_fn=lambda: object(),
        enqueue_market=lambda *a, **k: False,
    )
    engine._bootstrapped = True
    engine._orders = FakeOrders()

    assert engine.sync_exit_coverage(676.8) == 0


def test_sync_exit_coverage_skips_when_target_cell_already_has_open_exit(monkeypatch):
    from app.services.grid.engine import GridEngine
    from app.services.grid.levels import generate_cells, generate_levels
    from app.services.grid.resting_orders_repo import GridRestingOrder

    tc = {
        "initial_capital": 1000,
        "leverage": 10,
        "market_type": "swap",
        "bot_params": {
            "upperPrice": 758,
            "lowerPrice": 588,
            "gridCount": 24,
            "amountPerGrid": 4,
            "gridDirection": "long",
            "initialPositionPct": 35,
        },
    }
    placed = []

    def fake_place(self, cell, purpose, side, price, *, reduce_only, pos_side, quantity=None):
        placed.append({"cell": cell.index, "quantity": quantity})
        return True

    levels = generate_levels(588, 758, 24, "arithmetic")
    cells = generate_cells(levels)

    open_exit = GridRestingOrder(
        id=1,
        strategy_id=9,
        symbol="BNB/USDT",
        cell_index=13,
        purpose="long_exit",
        side="sell",
        pos_side="long",
        reduce_only=True,
        price=691.47,
        quantity=0.51,
        quote_amount=4,
        client_order_id="x",
        exchange_order_id="y",
        status="open",
        filled_quantity=0,
        processed_fill_qty=0,
    )

    class FakeOrders:
        def list_open(self, strategy_id):
            return [open_exit]

        def has_open_for_cell(self, strategy_id, cell_index, purpose):
            return int(cell_index) == 13 and purpose == "long_exit"

    target = next(c for c in cells if c.index == 13)

    monkeypatch.setattr("app.services.grid.engine.append_strategy_log", lambda *a, **k: None)
    monkeypatch.setattr("app.services.grid.engine.GridEngine._place_limit", fake_place)
    monkeypatch.setattr("app.services.grid.engine.GridEngine._leg_position_qty", lambda self, side: 0.62)
    monkeypatch.setattr("app.services.grid.engine.GridEngine._dedupe_open_exit_orders", lambda self, p: None)
    monkeypatch.setattr("app.services.grid.engine.GridEngine.sync_held_cell_exits", lambda self, px: 0)
    monkeypatch.setattr(
        "app.services.grid.engine.GridEngine._active_cell_for_price",
        lambda self, _cells, _price, _direction: target,
    )
    monkeypatch.setattr(
        "app.services.grid.engine.GridEngine._levels_and_cells",
        lambda self: (levels, cells),
    )
    monkeypatch.setattr(
        "app.services.grid.engine.GridEngine._grid_base_qty",
        lambda self, px: 0.059111,
    )

    engine = GridEngine(
        9,
        "BNB/USDT",
        tc,
        {},
        create_client_fn=lambda: object(),
        enqueue_market=lambda *a, **k: False,
    )
    engine._bootstrapped = True
    engine._orders = FakeOrders()

    assert engine.sync_exit_coverage(684.0) == 0
    assert placed == []


def test_sync_exit_coverage_skips_when_position_below_one_grid(monkeypatch):
    from app.services.grid.engine import GridEngine
    from app.services.grid.levels import generate_cells, generate_levels

    tc = {
        "initial_capital": 100,
        "leverage": 10,
        "market_type": "swap",
        "bot_params": {
            "upperPrice": 758,
            "lowerPrice": 588,
            "gridCount": 24,
            "amountPerGrid": 4,
            "gridDirection": "long",
            "initialPositionPct": 35,
        },
    }
    placed = []

    def fake_place(self, *args, **kwargs):
        placed.append(1)
        return True

    levels = generate_levels(588, 758, 24, "arithmetic")
    cells = generate_cells(levels)

    class FakeOrders:
        def list_open(self, strategy_id):
            return []

        def has_open_for_cell(self, strategy_id, cell_index, purpose):
            return False

    monkeypatch.setattr("app.services.grid.engine.append_strategy_log", lambda *a, **k: None)
    monkeypatch.setattr("app.services.grid.engine.GridEngine._place_limit", fake_place)
    monkeypatch.setattr("app.services.grid.engine.GridEngine._leg_position_qty", lambda self, side: 0.005)
    monkeypatch.setattr("app.services.grid.engine.GridEngine._dedupe_open_exit_orders", lambda self, p: None)
    monkeypatch.setattr("app.services.grid.engine.GridEngine.sync_held_cell_exits", lambda self, px: 0)
    monkeypatch.setattr(
        "app.services.grid.engine.GridEngine._grid_base_qty",
        lambda self, px: 0.059111,
    )
    monkeypatch.setattr(
        "app.services.grid.engine.GridEngine._levels_and_cells",
        lambda self: (levels, cells),
    )

    engine = GridEngine(
        9,
        "BNB/USDT",
        tc,
        {},
        create_client_fn=lambda: object(),
        enqueue_market=lambda *a, **k: False,
    )
    engine._bootstrapped = True
    engine._orders = FakeOrders()

    assert engine.sync_exit_coverage(684.0) == 0
    assert placed == []


def test_run_initial_market_stops_when_okx_net_position_exists(monkeypatch):
    from app.services.grid.engine import GridEngine

    tc = {
        "initial_capital": 1000,
        "leverage": 2,
        "market_type": "swap",
        "bot_params": {
            "upperPrice": 758,
            "lowerPrice": 588,
            "gridCount": 23,
            "amountPerGrid": 20,
            "gridDirection": "long",
            "initialPositionPct": 20,
        },
    }
    recorded = {"calls": 0, "market": 0}

    def fake_record(*args, **kwargs):
        recorded["calls"] += 1

    def fake_market(*args, **kwargs):
        recorded["market"] += 1
        return False, 0.0, 0.0

    monkeypatch.setattr("app.services.grid.engine.record_grid_market_fill", fake_record)
    monkeypatch.setattr("app.services.grid.engine.append_strategy_log", lambda *a, **k: None)
    monkeypatch.setattr("app.services.grid.engine.persist_grid_resting_state", lambda *a, **k: None)
    monkeypatch.setattr("app.services.grid.engine.GridEngine._has_initial_market_trade", lambda self: False)
    monkeypatch.setattr("app.services.grid.engine.execute_grid_market_order", fake_market)

    engine = GridEngine(
        11,
        "BNB/USDT",
        tc,
        {},
        create_client_fn=lambda: object(),
        enqueue_market=lambda *a, **k: False,
    )
    target = engine._target_initial_base_qty(679.0)
    monkeypatch.setattr("app.services.grid.engine.GridEngine._leg_position_qty", lambda self, side: target)

    ok = engine.run_initial_market_position(679.0)
    assert ok is True
    assert engine._initial_done is True
    assert recorded["calls"] == 1
    assert recorded["market"] == 0


def test_sync_grid_orders_skips_non_idle_cell(monkeypatch):
    from app.services.grid.engine import GridEngine
    from app.services.grid.levels import generate_cells, generate_levels
    from app.services.live_trading.grid_cells import GridCellState

    tc = {
        "initial_capital": 100,
        "leverage": 10,
        "market_type": "swap",
        "bot_params": {
            "upperPrice": 700,
            "lowerPrice": 680,
            "gridCount": 5,
            "amountPerGrid": 4,
            "gridDirection": "long",
        },
    }
    placed = []

    def fake_place(self, *args, **kwargs):
        placed.append(args)
        return True

    levels = generate_levels(680, 700, 5, "arithmetic")
    cells = generate_cells(levels)

    class FakeOrders:
        def list_open(self, strategy_id):
            return []

        def has_open_for_cell(self, strategy_id, cell_index, purpose):
            return False

        def update_status(self, *a, **k):
            return True

    monkeypatch.setattr("app.services.grid.engine.append_strategy_log", lambda *a, **k: None)
    monkeypatch.setattr("app.services.grid.engine.GridEngine._place_limit", fake_place)
    monkeypatch.setattr("app.services.grid.engine.GridEngine._dedupe_open_entry_orders", lambda self, p: None)
    monkeypatch.setattr(
        "app.services.grid.engine.GridEngine._levels_and_cells",
        lambda self: (levels, cells),
    )
    monkeypatch.setattr(
        "app.services.grid.engine.GridEngine._cell_state_by_index",
        lambda self: {3: GridCellState.LONG_HELD},
    )

    engine = GridEngine(
        9,
        "BNB/USDT",
        tc,
        {},
        create_client_fn=lambda: object(),
        enqueue_market=lambda *a, **k: False,
    )
    engine._bootstrapped = True
    engine._orders = FakeOrders()

    n = engine.sync_grid_orders(691.5)
    assert n == len(cells) - 1
    assert all(int(p[0].index) != 3 for p in placed)


def test_sync_grid_orders_skips_when_exit_open(monkeypatch):
    from app.services.grid.engine import GridEngine
    from app.services.grid.levels import generate_cells, generate_levels
    from app.services.live_trading.grid_cells import GridCellState

    tc = {
        "initial_capital": 100,
        "leverage": 10,
        "market_type": "swap",
        "bot_params": {
            "upperPrice": 700,
            "lowerPrice": 680,
            "gridCount": 5,
            "amountPerGrid": 4,
            "gridDirection": "long",
        },
    }
    placed = []

    def fake_place(self, *args, **kwargs):
        placed.append(args)
        return True

    levels = generate_levels(680, 700, 5, "arithmetic")
    cells = generate_cells(levels)
    target_idx = 2

    class FakeOrders:
        def list_open(self, strategy_id):
            return []

        def has_open_for_cell(self, strategy_id, cell_index, purpose):
            return int(cell_index) == target_idx and purpose == "long_exit"

        def update_status(self, *a, **k):
            return True

    monkeypatch.setattr("app.services.grid.engine.append_strategy_log", lambda *a, **k: None)
    monkeypatch.setattr("app.services.grid.engine.GridEngine._place_limit", fake_place)
    monkeypatch.setattr("app.services.grid.engine.GridEngine._dedupe_open_entry_orders", lambda self, p: None)
    monkeypatch.setattr(
        "app.services.grid.engine.GridEngine._levels_and_cells",
        lambda self: (levels, cells),
    )
    monkeypatch.setattr(
        "app.services.grid.engine.GridEngine._cell_state_by_index",
        lambda self: {target_idx: GridCellState.IDLE},
    )

    engine = GridEngine(
        9,
        "BNB/USDT",
        tc,
        {},
        create_client_fn=lambda: object(),
        enqueue_market=lambda *a, **k: False,
    )
    engine._bootstrapped = True
    engine._orders = FakeOrders()

    engine.sync_grid_orders(691.5)
    assert all(int(p[0].index) != target_idx for p in placed)


def test_on_order_filled_long_entry_marks_held_even_if_exit_hangs(monkeypatch):
    from app.services.grid.engine import GridEngine
    from app.services.grid.levels import GridCellSpec
    from app.services.grid.resting_orders_repo import GridRestingOrder
    from app.services.live_trading.grid_cells import GridCellState

    tc = {"market_type": "swap", "bot_params": {"gridDirection": "long", "gridCount": 5}}
    updates = []

    class FakeOrders:
        def has_open_for_cell(self, strategy_id, cell_index, purpose):
            return False

    class FakeCells:
        def update_state(self, *args, **kwargs):
            updates.append(kwargs)

    cell = GridCellSpec(index=1, lower_price=691.4, upper_price=691.5)
    order = GridRestingOrder(
        strategy_id=1,
        symbol="BNB/USDT",
        cell_index=1,
        purpose="long_entry",
        side="buy",
        pos_side="long",
        price=691.4,
        quantity=0.05,
    )

    monkeypatch.setattr("app.services.grid.engine.append_strategy_log", lambda *a, **k: None)
    monkeypatch.setattr(
        "app.services.grid.fill_handler.apply_grid_fill_to_local_state",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "app.services.grid.engine.GridEngine._levels_and_cells",
        lambda self: ([], [cell]),
    )
    monkeypatch.setattr("app.services.grid.engine.GridEngine._place_limit", lambda *a, **k: False)

    engine = GridEngine(
        1,
        "BNB/USDT",
        tc,
        {},
        create_client_fn=lambda: object(),
        enqueue_market=lambda *a, **k: False,
    )
    engine._orders = FakeOrders()
    engine._cells = FakeCells()

    engine.on_order_filled(order, 0.05, 691.4)
    assert len(updates) == 1
    assert updates[0]["state"] == GridCellState.LONG_HELD

