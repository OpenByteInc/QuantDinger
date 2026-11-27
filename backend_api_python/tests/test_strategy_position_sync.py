"""Tests for local position snapshot helpers used by live sync and UI."""

from contextlib import contextmanager
import sqlite3

import pytest

from app.services.live_trading.records import (
    lookup_exchange_side_qty,
    mark_live_positions,
    normalize_strategy_symbol,
    strategy_allowed_symbols,
)


def test_strategy_allowed_symbols_includes_trading_config_symbol():
    sc = {
        "symbol": "",
        "trading_config": {"symbol": "SOL/USDT"},
    }
    assert strategy_allowed_symbols(sc) == {"SOL/USDT"}


def test_strategy_allowed_symbols_includes_row_and_trading_config_symbol():
    sc = {
        "symbol": "BTC/USDT",
        "trading_config": {"symbol": "ETH/USDT"},
    }
    allowed = strategy_allowed_symbols(sc)
    assert "BTC/USDT" in allowed
    assert "ETH/USDT" in allowed


def test_strategy_allowed_symbols_uses_portfolio_manifest_universe():
    sc = {
        "symbol": "basket:2",
        "trading_config": {
            "strategy_manifest": {
                "universe": {
                    "instruments": [
                        {"market": "USStock", "symbol": "AAPL"},
                        {"market": "USStock", "symbol": "MSFT"},
                    ]
                }
            }
        },
    }

    assert strategy_allowed_symbols(sc) == {"AAPL", "MSFT"}


def test_strategy_allowed_symbols_uses_owned_dynamic_universe_members(monkeypatch):
    from app.services.live_trading import records

    class Cursor:
        def execute(self, _query, params=()):
            assert params == (42, 42)

        def fetchall(self):
            return [{"symbol": "AAPL"}, {"symbol": "MSFT"}]

        def close(self):
            return None

    class Connection:
        def cursor(self):
            return Cursor()

    @contextmanager
    def connection():
        yield Connection()

    monkeypatch.setattr(records, "get_db_connection", connection)

    allowed = strategy_allowed_symbols(
        {
            "strategy_id": 42,
            "symbol": "universe:INDEX:NASDAQ100",
            "trading_config": {"strategy_manifest": {"universe": {"reference": "INDEX:NASDAQ100"}}},
        }
    )

    assert allowed == {"AAPL", "MSFT"}


def test_lookup_exchange_side_qty_symbol_aliases():
    exch = {"SOL/USDT": {"long": 1.5, "short": 0.0}}
    assert lookup_exchange_side_qty(exch, "SOLUSDT", "long") == 1.5
    assert lookup_exchange_side_qty(exch, "SOL/USDT", "short") == 0.0


def test_filter_strategy_positions_by_allowed_symbols():
    """Regression: positions API must not surface unrelated wallet legs."""
    sc = {"symbol": "", "trading_config": {"symbol": "ETH/USDT"}}
    allowed = strategy_allowed_symbols(sc)
    allowed_upper = {
        normalize_strategy_symbol(str(s or "")).upper() for s in allowed if normalize_strategy_symbol(str(s or ""))
    }
    rows = [
        {"symbol": "ETH/USDT", "side": "short", "size": 0.41},
        {"symbol": "USDT", "side": "long", "size": 362.0},
        {"symbol": "OKB/USDT", "side": "long", "size": 0.6},
    ]
    filtered = [r for r in rows if normalize_strategy_symbol(str(r.get("symbol") or "")).upper() in allowed_upper]
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
    assert deletes == [(42, "ETH/USDT", "short")]


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


def test_mark_live_positions_updates_price_and_unrealized_pnl(monkeypatch):
    from app.services.live_trading import records

    updates = []

    class Cursor:
        rowcount = 0

        def execute(self, query, params=()):
            if "SELECT id, symbol_canonical" in query:
                self.rowcount = 0
            elif "UPDATE qd_strategy_positions" in query:
                updates.append(params)
                self.rowcount = 1

        def fetchall(self):
            return [
                {
                    "id": 7,
                    "symbol_canonical": "BTC/USDT",
                    "symbol": "BTC/USDT",
                    "side": "short",
                    "size": 0.0044,
                    "entry_price": 84135.9,
                }
            ]

        def close(self):
            return None

    class Connection:
        def cursor(self):
            return Cursor()

        def commit(self):
            return None

    @contextmanager
    def connection():
        yield Connection()

    monkeypatch.setattr(records, "get_db_connection", connection)

    count = mark_live_positions(
        21,
        {"Crypto:BTC/USDT@gate:swap": 84000.0},
    )

    assert count == 1
    assert updates[0][0] == pytest.approx(84000.0)
    assert updates[0][4] == pytest.approx((84135.9 - 84000.0) * 0.0044)
    assert updates[0][5] > 0
    assert updates[0][7] == 21


def test_mark_live_positions_rejects_ambiguous_cross_venue_prices(monkeypatch):
    from app.services.live_trading import records

    monkeypatch.setattr(
        records,
        "get_db_connection",
        lambda: pytest.fail("ambiguous venue prices must not reach the live ledger"),
    )

    count = mark_live_positions(
        21,
        {
            "Crypto:BTC/USDT@binance:swap": 84000.0,
            "Crypto:BTC/USDT@gate:swap": 84020.0,
        },
    )

    assert count == 0


@pytest.fixture
def position_ledger(monkeypatch):
    from app.services.live_trading import records

    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.create_function("NOW", 0, lambda: "2026-01-01")
    db.create_function("GREATEST", -1, max)
    db.create_function("LEAST", -1, min)
    db.execute("""CREATE TABLE qd_strategy_positions (
        id INTEGER PRIMARY KEY, user_id INTEGER, strategy_id INTEGER,
        symbol TEXT, symbol_canonical TEXT, side TEXT, size REAL, entry_price REAL,
        current_price REAL, highest_price REAL, lowest_price REAL,
        market_type TEXT, credential_id INTEGER, inst_id TEXT, strategy_run_id INTEGER,
        unrealized_pnl REAL DEFAULT 0, pnl_percent REAL DEFAULT 0, updated_at TEXT,
        position_state TEXT, tradable_size REAL, residual_reason TEXT,
        residual_evidence_json TEXT, residual_classified_at TEXT,
        UNIQUE(strategy_id, symbol, side)
    )""")

    class Cursor:
        def __init__(self):
            self.inner = db.cursor()

        def execute(self, query, params=()):
            self.inner.execute(query.replace("%s", "?").replace("::jsonb", "").replace("FOR UPDATE", ""), params)

        @property
        def rowcount(self):
            return self.inner.rowcount

        def fetchall(self):
            return [dict(row) for row in self.inner.fetchall()]

        def close(self):
            self.inner.close()

    class Connection:
        def cursor(self):
            return Cursor()

        def commit(self):
            db.commit()

    @contextmanager
    def connection():
        yield Connection()

    def fetch(strategy_id, symbol, side):
        row = db.execute(
            "SELECT * FROM qd_strategy_positions WHERE strategy_id = ? AND symbol = ? AND side = ?",
            (strategy_id, symbol, side),
        ).fetchone()
        return dict(row) if row else {}

    monkeypatch.setattr(records, "get_db_connection", connection)
    monkeypatch.setattr(records, "_get_user_id_from_strategy", lambda _sid: 2)
    monkeypatch.setattr(records, "_fetch_position", fetch)
    yield db
    db.close()


@pytest.mark.parametrize("side", ["long", "short"])
def test_split_full_close_removes_position_and_floating_profit(position_ledger, side):
    from app.services.live_trading import records

    args = dict(strategy_id=7, symbol="BTC/USDT", side=side)
    records.upsert_position(**args, size=.0152, entry_price=58529.55302631579, current_price=60313.3)
    fill_args = dict(strategy_id=7, symbol="BTC/USDT", signal_type=f"close_{side}")
    records.apply_fill_to_local_position(**fill_args, filled=.0068, avg_price=60313.4)
    remaining = records._fetch_position(7, "BTC/USDT", side)
    sign = 1 if side == "long" else -1
    assert remaining["size"] == .0084
    assert remaining["unrealized_pnl"] == pytest.approx(sign * (60313.4 - 58529.55302631579) * .0084)
    records.apply_fill_to_local_position(**fill_args, filled=.0084, avg_price=60313.3)
    assert records._fetch_position(7, "BTC/USDT", side) == {}


def test_full_close_absorbs_preexisting_binary_rounding_tail(position_ledger):
    from app.services.live_trading import records

    records.upsert_position(strategy_id=7, symbol="BTC/USDT", side="long",
                            size=.008400000000000001, entry_price=58529, current_price=60313)
    records.apply_fill_to_local_position(strategy_id=7, symbol="BTC/USDT", signal_type="close_long",
                                         filled=.0084, avg_price=60313)
    assert records._fetch_position(7, "BTC/USDT", "long") == {}


@pytest.mark.parametrize("symbol,quantity,closed", [
    ("BTC/USDT", .00002, .00001), ("00700", .02, .01), ("META", .002, .001),
    ("TINY/USDT", 2e-15, 1e-15),
])
def test_partial_close_preserves_real_small_holdings(position_ledger, symbol, quantity, closed):
    from app.services.live_trading import records

    records.upsert_position(strategy_id=7, symbol=symbol, side="long", size=quantity,
                            entry_price=100, current_price=200)
    records.apply_fill_to_local_position(strategy_id=7, symbol=symbol, signal_type="reduce_long",
                                         filled=closed, avg_price=200)
    row, _ = records._fetch_position_fuzzy(7, symbol, "long")
    assert row["size"] == pytest.approx(quantity - closed, rel=1e-12, abs=0)
    assert row["unrealized_pnl"] == pytest.approx(100 * (quantity - closed), rel=1e-12, abs=0)


@pytest.mark.parametrize("legacy_symbol", ["00700", "700", "700.HK", "HK.00700", "HKStock:00700"])
def test_hk_close_resolves_legacy_ledger_and_keeps_one_canonical_position(position_ledger, legacy_symbol):
    from app.services.live_trading import records

    records.upsert_position(strategy_id=7, symbol="00700.HK", side="long", size=200,
                            entry_price=350, current_price=353)
    position_ledger.execute("UPDATE qd_strategy_positions SET symbol = ?, symbol_canonical = ?",
                            (legacy_symbol, legacy_symbol))
    records.apply_fill_to_local_position(strategy_id=7, symbol="00700.HK", signal_type="reduce_long",
                                         filled=100, avg_price=353)
    rows = position_ledger.execute("SELECT symbol, size, entry_price FROM qd_strategy_positions").fetchall()
    assert [tuple(row) for row in rows] == [("00700.HK", 100, 350)]
    assert records.fetch_position_size_for_side(7, legacy_symbol, "long") == 100
    records.apply_fill_to_local_position(strategy_id=7, symbol="700", signal_type="close_long",
                                         filled=100, avg_price=354)
    assert position_ledger.execute("SELECT COUNT(*) FROM qd_strategy_positions").fetchone()[0] == 0


def test_mark_revalues_historical_precision_tail_without_deleting_inventory(position_ledger):
    from app.services.live_trading import records

    records.upsert_position(strategy_id=7, symbol="BTC/USDT", side="long", size=2e-18,
                            entry_price=58529.55302631579, current_price=60313.3)
    position_ledger.execute("UPDATE qd_strategy_positions SET unrealized_pnl = 27.114474")
    assert records.mark_live_positions(7, {"BTC/USDT": 60313.3}) == 1
    row = records._fetch_position(7, "BTC/USDT", "long")
    assert row["size"] == 2e-18
    assert row["unrealized_pnl"] == pytest.approx(0, abs=1e-12)


def test_delayed_mark_cannot_restore_profit_from_pre_close_size(position_ledger, monkeypatch):
    from app.services.live_trading import records

    records.upsert_position(strategy_id=7, symbol="BTC/USDT", side="long", size=.0152,
                            entry_price=58529, current_price=60313)
    stale = records._fetch_position(7, "BTC/USDT", "long")
    records.upsert_position(strategy_id=7, symbol="BTC/USDT", side="long", size=.0084,
                            entry_price=58529, current_price=60313)
    monkeypatch.setattr(records, "_fetch_position_fuzzy", lambda *_args: (stale, "BTC/USDT"))
    assert not records.patch_position_markers(strategy_id=7, symbol="BTC/USDT", side="long", current_price=60314)
    row = records._fetch_position(7, "BTC/USDT", "long")
    assert row["unrealized_pnl"] == pytest.approx((60313 - 58529) * .0084)
