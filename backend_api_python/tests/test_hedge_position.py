"""Hedge-aware ScriptPosition behaviour (P0-1 contract).

Covers the subset of API that grid / DCA bot scripts and the executor's
order-to-signal translator rely on:

* Long and short legs are tracked independently.
* Net direction (``int(pos)``) and boolean truthiness remain correct.
* ``open_position`` does NOT clobber the opposite leg (this was the
  pre-P0-1 bug — opening a long would silently wipe a held short).
* Reduce returns the matched entry price so the executor can compute
  ``grid_matched_profit``.
* Legacy ``add_position`` / ``reduce_position`` route to the leg implied
  by the current net direction (back-compat for indicator scripts).
"""
from __future__ import annotations

from app.services.strategy_script_runtime import ScriptPosition


def test_starts_flat():
    p = ScriptPosition()
    assert p.is_flat()
    assert int(p) == 0
    assert bool(p) is False
    assert p.long_size == 0.0
    assert p.short_size == 0.0
    assert p['side'] == ''


def test_open_long_only_touches_long_leg():
    p = ScriptPosition()
    p.open_short(100.0, 5.0)
    p.open_long(120.0, 3.0)

    assert p.has_long()
    assert p.has_short()
    assert p.long_size == 3.0
    assert p.short_size == 5.0

    # Net = long_size - short_size = -2 → short
    assert int(p) == -1
    assert p['side'] == 'short'
    assert p['size'] == 5.0
    assert p['entry_price'] == 100.0


def test_open_long_weighted_average_entry():
    p = ScriptPosition()
    p.open_long(100.0, 2.0)
    p.open_long(110.0, 2.0)

    assert p.long_size == 4.0
    assert abs(p.long_entry - 105.0) < 1e-9


def test_reduce_long_returns_matched_entry_and_qty():
    p = ScriptPosition()
    p.open_long(100.0, 4.0)
    qty, avg = p.reduce_long(1.5)
    assert qty == 1.5
    # Avg entry of the leg before reduction.
    assert abs(avg - 100.0) < 1e-9
    assert p.long_size == 2.5
    # Remaining leg keeps the same average entry.
    assert abs(p.long_entry - 100.0) < 1e-9


def test_reduce_long_caps_to_available_size():
    p = ScriptPosition()
    p.open_long(100.0, 1.0)
    qty, avg = p.reduce_long(10.0)
    assert qty == 1.0
    assert avg == 100.0
    assert not p.has_long()
    assert p.long_entry == 0.0


def test_close_short_then_open_short_resets_entry():
    p = ScriptPosition()
    p.open_short(50.0, 2.0)
    qty, avg = p.close_short()
    assert qty == 2.0
    assert avg == 50.0
    assert not p.has_short()

    p.open_short(80.0, 1.0)
    assert p.has_short()
    assert p.short_entry == 80.0


def test_legacy_add_routes_to_current_net_direction():
    p = ScriptPosition()
    p.open_long(100.0, 1.0)
    p.add_position(120.0, 1.0)
    assert p.long_size == 2.0
    assert abs(p.long_entry - 110.0) < 1e-9
    # Short leg untouched.
    assert p.short_size == 0.0


def test_legacy_reduce_routes_to_current_net_direction():
    p = ScriptPosition()
    p.open_short(50.0, 3.0)
    p.reduce_position(1.0)
    assert p.short_size == 2.0


def test_legacy_open_position_does_not_clobber_opposite_leg():
    """Critical regression guard for the pre-P0-1 ``ctx.position`` bug.

    A neutral-grid script would hold both legs and periodically call
    ``open_position('long', ...)`` to add to its long stack. The old
    single-net ``ScriptPosition`` cleared *everything* on each open,
    silently wiping the short leg and corrupting the position view.
    """
    p = ScriptPosition()
    p.open_short(50.0, 3.0)
    p.open_position('long', 100.0, 2.0)

    assert p.has_long()
    assert p.has_short()
    assert p.long_size == 2.0
    assert p.short_size == 3.0


def test_clear_position_zeros_both_legs():
    p = ScriptPosition()
    p.open_long(100.0, 1.0)
    p.open_short(80.0, 2.0)
    p.clear_position()
    assert p.is_flat()
    assert p.long_size == 0.0
    assert p.short_size == 0.0
    assert p['side'] == ''
