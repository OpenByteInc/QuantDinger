"""Tests for local position snapshot helpers used by live sync and UI."""

from app.services.live_trading.records import (
    lookup_exchange_side_qty,
    normalize_strategy_symbol,
    strategy_allowed_symbols,
)


def test_strategy_allowed_symbols_includes_trading_config_symbol():
    sc = {
        "symbol": "",
        "trading_config": {"symbol": "SOL/USDT", "symbol_list": []},
    }
    assert strategy_allowed_symbols(sc) == {"SOL/USDT"}


def test_strategy_allowed_symbols_includes_row_and_list():
    sc = {
        "symbol": "BTC/USDT",
        "trading_config": {
            "symbol": "ETH/USDT",
            "symbol_list": ["Crypto:SOL/USDT", "BNBUSDT"],
        },
    }
    allowed = strategy_allowed_symbols(sc)
    assert "BTC/USDT" in allowed
    assert "ETH/USDT" in allowed
    assert "SOL/USDT" in allowed
    assert normalize_strategy_symbol("BNBUSDT").upper() in allowed


def test_lookup_exchange_side_qty_symbol_aliases():
    exch = {"SOL/USDT": {"long": 1.5, "short": 0.0}}
    assert lookup_exchange_side_qty(exch, "SOLUSDT", "long") == 1.5
    assert lookup_exchange_side_qty(exch, "SOL/USDT", "short") == 0.0


def test_filter_strategy_positions_by_allowed_symbols():
    """Regression: positions API must not surface unrelated wallet legs."""
    sc = {"symbol": "", "trading_config": {"symbol": "ETH/USDT"}}
    allowed = strategy_allowed_symbols(sc)
    allowed_upper = {
        normalize_strategy_symbol(str(s or "")).upper()
        for s in allowed
        if normalize_strategy_symbol(str(s or ""))
    }
    rows = [
        {"symbol": "ETH/USDT", "side": "short", "size": 0.41},
        {"symbol": "USDT", "side": "long", "size": 362.0},
        {"symbol": "OKB/USDT", "side": "long", "size": 0.6},
    ]
    filtered = [
        r
        for r in rows
        if normalize_strategy_symbol(str(r.get("symbol") or "")).upper() in allowed_upper
    ]
    assert len(filtered) == 1
    assert filtered[0]["symbol"] == "ETH/USDT"


def test_strategy_has_trades_for_symbol_candidates():
    from app.services.live_trading.records import _position_symbol_candidates

    cands = _position_symbol_candidates("ETHUSDT")
    assert "ETH/USDT" in cands
    assert "ETHUSDT" in cands


def test_apply_exchange_snapshot_upserts_allowed_symbol(monkeypatch):
    from app.services.live_trading import strategy_position_sync as sps

    upserts = []
    deletes = []

    def fake_upsert(**kwargs):
        upserts.append(kwargs)

    def fake_delete(strategy_id, symbol, side):
        deletes.append((strategy_id, symbol, side))

    monkeypatch.setattr(sps, "upsert_position", fake_upsert)
    monkeypatch.setattr(sps, "_delete_position", fake_delete)

    written = sps.apply_exchange_snapshot_to_strategy_ledger(
        strategy_id=42,
        strategy_config={"symbol": "", "trading_config": {"symbol": "ETH/USDT"}},
        exch_size={"ETH/USDT": {"long": 0.5, "short": 0.0}},
        exch_entry_price={"ETH/USDT": {"long": 3200.0, "short": 0.0}},
        market_type="swap",
        exchange_config={"credential_id": 7},
    )
    assert written == 1
    assert len(upserts) == 1
    assert upserts[0]["strategy_id"] == 42
    assert upserts[0]["symbol"] == "ETH/USDT"
    assert upserts[0]["side"] == "long"
    assert upserts[0]["size"] == 0.5
    assert deletes == [ (42, "ETH/USDT", "short") ]


def test_apply_exchange_snapshot_skips_unrelated_symbols(monkeypatch):
    from app.services.live_trading import strategy_position_sync as sps

    upserts = []

    monkeypatch.setattr(sps, "upsert_position", lambda **kw: upserts.append(kw))
    monkeypatch.setattr(sps, "_delete_position", lambda *a: None)

    written = sps.apply_exchange_snapshot_to_strategy_ledger(
        strategy_id=1,
        strategy_config={"trading_config": {"symbol": "ETH/USDT"}},
        exch_size={
            "ETH/USDT": {"long": 1.0, "short": 0.0},
            "BTC/USDT": {"long": 2.0, "short": 0.0},
        },
        exch_entry_price={},
        market_type="swap",
    )
    assert written == 1
    assert len(upserts) == 1
    assert upserts[0]["symbol"] == "ETH/USDT"
