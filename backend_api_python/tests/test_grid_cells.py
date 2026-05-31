"""GridCellState + GridCell dataclass invariants (P2 scaffolding).

The repository layer touches the DB and is exercised by integration tests;
here we only assert the in-memory contract that other parts of the codebase
will rely on (state parsing + ``from_row`` round-trip).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from app.services.live_trading.grid_cells import GridCell, GridCellState


def test_state_parse_accepts_enum_string_and_unknown():
    assert GridCellState.parse(GridCellState.LONG_HELD) is GridCellState.LONG_HELD
    assert GridCellState.parse('buy_open') is GridCellState.BUY_OPEN
    assert GridCellState.parse('BUY_OPEN') is GridCellState.BUY_OPEN
    # Unknown strings fall back to IDLE so a corrupted DB row never crashes.
    assert GridCellState.parse('totally-bogus') is GridCellState.IDLE
    assert GridCellState.parse(None) is GridCellState.IDLE


def test_from_row_round_trip_with_dict_extra():
    row = {
        'id': 1,
        'strategy_id': 42,
        'symbol': 'BTC/USDT',
        'cell_index': 3,
        'lower_price': 100.0,
        'upper_price': 110.0,
        'state': 'long_held',
        'leg_size': 0.5,
        'leg_entry_price': 105.0,
        'working_order_id': 'abc',
        'last_event_ts': datetime(2026, 5, 28, 12, 0, tzinfo=timezone.utc),
        'extra': {'note': 'paired'},
    }
    cell = GridCell.from_row(row)
    assert cell.id == 1
    assert cell.strategy_id == 42
    assert cell.cell_index == 3
    assert cell.state is GridCellState.LONG_HELD
    assert cell.extra == {'note': 'paired'}


def test_from_row_handles_json_string_extra():
    row = {
        'id': 2,
        'strategy_id': 1,
        'symbol': 'ETH/USDT',
        'cell_index': 0,
        'lower_price': 1500.0,
        'upper_price': 1550.0,
        'state': 'idle',
        'extra': json.dumps({'paired_id': 99}),
    }
    cell = GridCell.from_row(row)
    assert cell.extra == {'paired_id': 99}
    assert cell.state is GridCellState.IDLE


def test_from_row_recovers_from_malformed_json():
    row = {
        'id': 3,
        'strategy_id': 1,
        'symbol': 'ETH/USDT',
        'cell_index': 0,
        'lower_price': 1500.0,
        'upper_price': 1550.0,
        'state': 'idle',
        'extra': '{not json',
    }
    cell = GridCell.from_row(row)
    assert cell.extra == {}
