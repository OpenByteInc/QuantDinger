from app.services import pending_order_position_sync as sync_mod


def _no_recent_open_fills(monkeypatch):
    monkeypatch.setattr(
        sync_mod,
        "_recent_open_fill_symbols",
        lambda strategy_id, grace_sec: set(),
    )


def test_purge_flat_strategy_positions_deletes_exchange_flat_legs(monkeypatch):
    deleted = []

    _no_recent_open_fills(monkeypatch)
    monkeypatch.setattr(
        sync_mod,
        "_delete_position",
        lambda strategy_id, symbol, side: deleted.append((strategy_id, symbol, side)),
    )
    monkeypatch.setattr(
        sync_mod,
        "_local_strategy_position_legs",
        lambda strategy_id, allowed_symbols: [("BTC/USDT", "long"), ("BTC/USDT", "short")],
    )

    count = sync_mod._purge_flat_strategy_positions_from_exchange(
        strategy_id=7,
        strategy_config={"symbol": "BTC/USDT"},
        exch_size={},
    )

    assert count == 2
    assert (7, "BTC/USDT", "long") in deleted
    assert (7, "BTC/USDT", "short") in deleted


def test_purge_flat_strategy_positions_keeps_exchange_live_leg(monkeypatch):
    deleted = []

    _no_recent_open_fills(monkeypatch)
    monkeypatch.setattr(
        sync_mod,
        "_delete_position",
        lambda strategy_id, symbol, side: deleted.append((strategy_id, symbol, side)),
    )
    monkeypatch.setattr(
        sync_mod,
        "_local_strategy_position_legs",
        lambda strategy_id, allowed_symbols: [("BTC/USDT", "long"), ("BTC/USDT", "short")],
    )

    count = sync_mod._purge_flat_strategy_positions_from_exchange(
        strategy_id=8,
        strategy_config={"symbol": "BTC/USDT"},
        exch_size={"BTC/USDT": {"long": 0.01, "short": 0.0}},
    )

    assert count == 1
    assert (8, "BTC/USDT", "long") not in deleted
    assert (8, "BTC/USDT", "short") in deleted


def test_purge_flat_strategy_positions_can_be_disabled(monkeypatch):
    deleted = []
    _no_recent_open_fills(monkeypatch)
    monkeypatch.setenv("POSITION_SYNC_PURGE_FLAT_LEDGER", "false")
    monkeypatch.setattr(
        sync_mod,
        "_delete_position",
        lambda strategy_id, symbol, side: deleted.append((strategy_id, symbol, side)),
    )
    monkeypatch.setattr(
        sync_mod,
        "_local_strategy_position_legs",
        lambda strategy_id, allowed_symbols: [("ETH/USDT", "long")],
    )

    count = sync_mod._purge_flat_strategy_positions_from_exchange(
        strategy_id=9,
        strategy_config={"symbol": "ETH/USDT"},
        exch_size={},
    )

    assert count == 0
    assert deleted == []


def test_purge_flat_strategy_positions_noops_when_local_ledger_empty(monkeypatch):
    deleted = []
    _no_recent_open_fills(monkeypatch)
    monkeypatch.setattr(
        sync_mod,
        "_delete_position",
        lambda strategy_id, symbol, side: deleted.append((strategy_id, symbol, side)),
    )
    monkeypatch.setattr(
        sync_mod,
        "_local_strategy_position_legs",
        lambda strategy_id, allowed_symbols: [],
    )

    count = sync_mod._purge_flat_strategy_positions_from_exchange(
        strategy_id=10,
        strategy_config={"symbol": "ETH/USDT"},
        exch_size={},
    )

    assert count == 0
    assert deleted == []


def test_purge_flat_strategy_positions_keeps_leg_within_grace_window(monkeypatch):
    deleted = []
    monkeypatch.setenv("POSITION_SYNC_PURGE_GRACE_SEC", "120")
    monkeypatch.setattr(
        sync_mod,
        "_delete_position",
        lambda strategy_id, symbol, side: deleted.append((strategy_id, symbol, side)),
    )
    monkeypatch.setattr(
        sync_mod,
        "_local_strategy_position_legs",
        lambda strategy_id, allowed_symbols: [("BTC/USDT", "long")],
    )
    monkeypatch.setattr(
        sync_mod,
        "_recent_open_fill_symbols",
        lambda strategy_id, grace_sec: {("BTC/USDT", "long")},
    )

    count = sync_mod._purge_flat_strategy_positions_from_exchange(
        strategy_id=11,
        strategy_config={"symbol": "BTC/USDT"},
        exch_size={},
    )

    assert count == 0
    assert deleted == []
