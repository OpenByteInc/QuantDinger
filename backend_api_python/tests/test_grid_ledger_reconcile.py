"""Grid phantom ledger reconciliation helpers."""

from __future__ import annotations

from app.services.grid.ledger_reconcile import (
    exchange_is_flat,
    is_legacy_initial_recovery_trade,
    ledger_exceeds_exchange,
    ledger_has_open_legs,
    should_clear_phantom_ledger,
)


def test_exchange_is_flat():
    assert exchange_is_flat({"long_size": 0, "short_size": 0}) is True
    assert exchange_is_flat({"long_size": 0.01, "short_size": 0}) is False
    assert exchange_is_flat(None) is False
    assert exchange_is_flat({}) is False


def test_ledger_excess_detects_partial_phantom_position():
    assert ledger_exceeds_exchange(
        {"long_size": 0.773408, "short_size": 0},
        [{"side": "long", "size": 96.924408}],
    ) is True
    assert ledger_exceeds_exchange(
        {"long_size": 0.773408, "short_size": 0},
        [{"side": "long", "size": 0.773408}],
    ) is False


def test_legacy_initial_recovery_requires_bug_signature():
    assert is_legacy_initial_recovery_trade({
        "strategy_run_id": 0,
        "binding_client_order_id": "ginit01106l",
        "binding_exchange_order_id": "",
        "exchange_order_id": "",
    }) is True
    assert is_legacy_initial_recovery_trade({
        "strategy_run_id": 9001,
        "binding_client_order_id": "ginit01106r00000009001l",
        "exchange_order_id": "4038722687882424321",
    }) is False


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


def test_should_not_delete_closed_history_when_both_ledgers_are_flat():
    ok, reason = should_clear_phantom_ledger(
        exchange_snapshot={"long_size": 0, "short_size": 0},
        ledger_positions=[],
        initial_trades=[{"id": 10, "close_reason": "grid_initial_long"}],
    )

    assert ok is False
    assert "historical trades are preserved" in reason


def test_should_not_clear_without_verified_exchange_snapshot():
    ok, reason = should_clear_phantom_ledger(
        exchange_snapshot=None,
        ledger_positions=[{"id": 1, "side": "long", "size": 100}],
        initial_trades=[{"id": 10}],
    )

    assert ok is False
    assert "snapshot unavailable" in reason


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
