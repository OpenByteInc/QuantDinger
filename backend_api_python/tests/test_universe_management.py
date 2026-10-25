from contextlib import contextmanager
from datetime import date

import pytest

from app.services import universe as universe_module
from app.services.universe import UniverseError, UniverseService, normalize_manual_members


def test_manual_universe_rejects_mixed_market_members():
    with pytest.raises(UniverseError, match="universe.memberMarketMismatch"):
        normalize_manual_members("USStock", [
            {"market": "USStock", "symbol": "AAPL"},
            {"market": "Crypto", "symbol": "BTC/USDT"},
        ])


def test_stock_universe_rejects_crypto_pair_symbols():
    with pytest.raises(UniverseError, match="universe.memberMarketMismatch"):
        normalize_manual_members("USStock", [{"symbol": "BTC/USDT"}])


def test_manual_universe_rejects_mixed_market_type():
    with pytest.raises(UniverseError, match="universe.invalidMarket"):
        normalize_manual_members("Mixed", [{"market": "USStock", "symbol": "AAPL"}])


def test_manual_universe_normalizes_members_for_its_market():
    members = normalize_manual_members("Crypto", [{"symbol": "btc/usdt"}])

    assert [(item["market"], item["symbol"]) for item in members] == [("Crypto", "BTC/USDT")]


def test_delete_manual_removes_only_an_owned_editable_universe(monkeypatch):
    statements = []

    class Cursor:
        def execute(self, sql, params):
            statements.append((" ".join(sql.split()), params))

        def fetchone(self):
            return {
                "id": 42,
                "user_id": 7,
                "code": "personal-42",
                "is_system": False,
                "universe_type": "manual",
            }

        def close(self):
            return None

    class Connection:
        committed = False

        def cursor(self):
            return Cursor()

        def commit(self):
            self.committed = True

    connection = Connection()

    @contextmanager
    def fake_connection():
        yield connection

    monkeypatch.setattr(universe_module, "get_db_connection", fake_connection)

    result = UniverseService().delete_manual(7, 42)

    assert result == {"id": 42, "code": "personal-42"}
    assert any(sql == "DELETE FROM qd_universes WHERE id = ?" for sql, _ in statements)
    assert connection.committed is True


def test_system_member_edit_preserves_history_before_effective_date():
    statements = []

    class Cursor:
        def execute(self, sql, params):
            statements.append((" ".join(sql.split()), params))

    members = normalize_manual_members("USStock", [
        {"symbol": "AAPL"},
        {"symbol": "MSFT"},
    ])
    effective_from = date(2023, 1, 1)

    UniverseService._replace_system_members(Cursor(), 77, members, effective_from)

    assert statements[0] == (
        "DELETE FROM qd_universe_members WHERE universe_id = ? AND valid_from >= ?",
        (77, effective_from),
    )
    assert "SET valid_to = ?" in statements[1][0]
    assert statements[1][1] == (effective_from, 77, effective_from, effective_from)
    inserts = [item for item in statements if item[0].startswith("INSERT INTO qd_universe_members")]
    assert len(inserts) == 2
    assert all(item[1][8] == effective_from for item in inserts)
    assert all(item[1][11] == "admin:2023-01-01" for item in inserts)
