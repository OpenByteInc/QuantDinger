import pytest

from app.services import pending_order_position_sync as sync_mod


@pytest.fixture(autouse=True)
def _purge_enabled(monkeypatch):
    """The purge ships disabled; the cases below exercise it explicitly."""
    monkeypatch.setenv("POSITION_SYNC_PURGE_FLAT_LEDGER", "true")


def _no_recent_open_fills(monkeypatch):
    monkeypatch.setattr(
        sync_mod,
        "_recent_open_fill_symbols",
        lambda strategy_id, grace_sec: set(),
    )


def _attributed_close(monkeypatch, trade_id=4242):
    """A purge is only allowed once the matching close trade can be written."""
    monkeypatch.setattr(
        "app.services.live_trading.external_flat_close.trade_side_net_qty",
        lambda strategy_id, symbol, side: 0.01,
    )
    monkeypatch.setattr(
        "app.services.live_trading.external_flat_close.reconcile_external_flat_closes",
        lambda **_kwargs: trade_id,
    )


def test_purge_flat_strategy_positions_deletes_exchange_flat_legs(monkeypatch):
    deleted = []

    _no_recent_open_fills(monkeypatch)
    _attributed_close(monkeypatch)
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
    _attributed_close(monkeypatch)
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


def test_purge_flat_strategy_positions_keeps_leg_without_attributed_close(monkeypatch):
    """Non-OKX venues cannot produce a close snapshot, so their legs must survive."""
    deleted = []
    _no_recent_open_fills(monkeypatch)
    _attributed_close(monkeypatch, trade_id=0)
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

    count = sync_mod._purge_flat_strategy_positions_from_exchange(
        strategy_id=12,
        strategy_config={"symbol": "BTC/USDT"},
        exch_size={},
    )

    assert count == 0
    assert deleted == []


def test_purge_flat_strategy_positions_skips_cached_snapshot(monkeypatch):
    """A cached snapshot's age follows the sync interval, not the purge grace window."""
    deleted = []
    _no_recent_open_fills(monkeypatch)
    _attributed_close(monkeypatch)
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

    count = sync_mod._purge_flat_strategy_positions_from_exchange(
        strategy_id=13,
        strategy_config={"symbol": "BTC/USDT"},
        exch_size={},
        snapshot_from_cache=True,
    )

    assert count == 0
    assert deleted == []


def test_purge_flat_strategy_positions_drops_already_reconciled_leg(monkeypatch):
    """A failed delete must not re-reconcile: that would write a second close."""
    deleted = []
    _no_recent_open_fills(monkeypatch)
    monkeypatch.setattr(
        "app.services.live_trading.external_flat_close.trade_side_net_qty",
        lambda strategy_id, symbol, side: 0.0,
    )
    monkeypatch.setattr(
        "app.services.live_trading.external_flat_close.reconcile_external_flat_closes",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("must not re-reconcile")),
    )
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

    count = sync_mod._purge_flat_strategy_positions_from_exchange(
        strategy_id=14,
        strategy_config={"symbol": "BTC/USDT"},
        exch_size={},
    )

    assert count == 1
    assert deleted == [(14, "BTC/USDT", "long")]


def test_purge_flat_strategy_positions_is_off_unless_enabled(monkeypatch):
    """Shipping default: a flat snapshot alone must not delete a leg."""
    deleted = []
    monkeypatch.delenv("POSITION_SYNC_PURGE_FLAT_LEDGER", raising=False)
    _no_recent_open_fills(monkeypatch)
    _attributed_close(monkeypatch)
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

    count = sync_mod._purge_flat_strategy_positions_from_exchange(
        strategy_id=15,
        strategy_config={"symbol": "BTC/USDT"},
        exch_size={},
    )

    assert count == 0
    assert deleted == []
