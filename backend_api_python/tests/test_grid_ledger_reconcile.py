"""Grid phantom ledger reconciliation helpers."""

from __future__ import annotations

from app.services.grid.ledger_reconcile import (
    exchange_is_flat,
    ledger_has_open_legs,
    should_clear_phantom_ledger,
)


def test_exchange_is_flat():
    assert exchange_is_flat({"long_size": 0, "short_size": 0}) is True
    assert exchange_is_flat({"long_size": 0.01, "short_size": 0}) is False


def test_should_clear_when_exchange_flat_and_ledger_open():
    ok, reason = should_clear_phantom_ledger(
        exchange_snapshot={"long_size": 0, "short_size": 0},
        ledger_positions=[{"id": 1, "side": "long", "size": 0.01}],
        initial_trades=[{"id": 10, "close_reason": "grid_initial_long"}],
    )
    assert ok is True
    assert "flat" in reason.lower()


def test_should_not_clear_when_exchange_has_position():
    ok, reason = should_clear_phantom_ledger(
        exchange_snapshot={"long_size": 0.01, "short_size": 0},
        ledger_positions=[{"id": 1, "side": "long", "size": 0.01}],
        initial_trades=[{"id": 10}],
        force=False,
    )
    assert ok is False


def test_force_clears_despite_exchange_position():
    ok, reason = should_clear_phantom_ledger(
        exchange_snapshot={"long_size": 0.01, "short_size": 0},
        ledger_positions=[{"id": 1, "side": "long", "size": 0.01}],
        initial_trades=[{"id": 10}],
        force=True,
    )
    assert ok is True
    assert "forced" in reason.lower()


def test_ledger_has_open_legs():
    assert ledger_has_open_legs([{"size": 0}]) is False
    assert ledger_has_open_legs([{"size": 0.001}]) is True
