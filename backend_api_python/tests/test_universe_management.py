from contextlib import contextmanager
from datetime import date, timedelta
from inspect import unwrap

import pytest
from flask import Flask

from app.routes import universe as universe_routes
from app.services import universe as universe_module
from app.services.universe import UniverseError, UniverseService, normalize_manual_members


class _ScriptedCursor:
    def __init__(self, *, one=(), many=()):
        self.one = list(one)
        self.many = list(many)
        self.statements = []
        self.closed = False

    def execute(self, sql, params=()):
        self.statements.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self.one.pop(0) if self.one else None

    def fetchall(self):
        return self.many.pop(0) if self.many else []

    def close(self):
        self.closed = True


class _Connection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.committed = False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.committed = True


def _install_connection(monkeypatch, cursor):
    connection = _Connection(cursor)

    @contextmanager
    def fake_connection():
        yield connection

    monkeypatch.setattr(universe_module, "get_db_connection", fake_connection)
    return connection


def _system_row(**overrides):
    return {
        "id": 77,
        "user_id": None,
        "code": "managed-tech",
        "name": "Managed Tech",
        "name_i18n_key": "",
        "market": "USStock",
        "universe_type": "manual",
        "source": "manual",
        "source_ref": "",
        "is_system": True,
        "status": "active",
        "metadata_json": {"seed": True},
        **overrides,
    }


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


def test_create_system_universe_persists_members_and_metadata(monkeypatch):
    cursor = _ScriptedCursor(one=[None, {"id": 77}])
    connection = _install_connection(monkeypatch, cursor)
    service = UniverseService()
    replaced = {}
    monkeypatch.setattr(
        service,
        "_replace_system_members",
        lambda cur, universe_id, members, effective_from: replaced.update(
            universe_id=universe_id,
            members=members,
            effective_from=effective_from,
        ),
    )
    monkeypatch.setattr(service, "get_system", lambda universe_id: {"id": universe_id})

    result = service.create_system({
        "name": "Managed Tech",
        "code": "managed-tech",
        "market": "USStock",
        "historyFrom": "2023-01-01",
        "members": [{"symbol": "AAPL"}, {"symbol": "MSFT"}],
        "metadata": {"owner": "platform"},
    })

    assert result == {"id": 77}
    assert connection.committed is True
    assert replaced["universe_id"] == 77
    assert replaced["effective_from"] == date(2023, 1, 1)
    assert [member["symbol"] for member in replaced["members"]] == ["AAPL", "MSFT"]
    insert = next(item for item in cursor.statements if item[0].startswith("INSERT INTO qd_universes"))
    assert '"admin_managed": true' in insert[1][-1]
    assert '"owner": "platform"' in insert[1][-1]


def test_create_system_universe_rejects_duplicate_code(monkeypatch):
    cursor = _ScriptedCursor(one=[{"id": 9}])
    _install_connection(monkeypatch, cursor)

    with pytest.raises(UniverseError) as exc_info:
        UniverseService().create_system({
            "name": "Managed Tech",
            "code": "managed-tech",
            "market": "USStock",
            "members": [],
        })

    assert exc_info.value.code == "universe.codeExists"
    assert exc_info.value.status_code == 409
    assert cursor.closed is True


def test_create_system_universe_enforces_member_limit(monkeypatch):
    monkeypatch.setattr(universe_module, "MAX_SYSTEM_MEMBERS", 0)

    with pytest.raises(UniverseError) as exc_info:
        UniverseService().create_system({
            "name": "Managed Tech",
            "market": "USStock",
            "members": [{"symbol": "AAPL"}],
        })

    assert exc_info.value.code == "universe.tooManyMembers"


@pytest.mark.parametrize(
    ("payload", "error_code"),
    [
        ({"name": "", "market": "USStock"}, "universe.invalidName"),
        ({"name": "Pool", "market": "Unknown"}, "universe.invalidMarket"),
        ({"name": "Pool", "market": "USStock", "universeType": "watchlist"}, "universe.createTypeUnsupported"),
        ({
            "name": "Pool",
            "market": "USStock",
            "historyFrom": (date.today() + timedelta(days=1)).isoformat(),
        }, "universe.invalidHistoryDate"),
    ],
)
def test_create_system_universe_validates_payload(payload, error_code):
    with pytest.raises(UniverseError) as exc_info:
        UniverseService().create_system(payload)

    assert exc_info.value.code == error_code


def test_get_system_universe_serializes_history_and_member_count(monkeypatch):
    cursor = _ScriptedCursor(one=[{"history_from": date(2023, 1, 1)}])
    _install_connection(monkeypatch, cursor)
    service = UniverseService()
    monkeypatch.setattr(service, "_get_system_universe", lambda db, universe_id: _system_row(id=universe_id))
    monkeypatch.setattr(service, "_resolve_system_members", lambda universe_id: [{"symbol": "AAPL"}])

    result = service.get_system(77)

    assert result["id"] == 77
    assert result["history_from"] == "2023-01-01"
    assert result["member_count"] == 1


@pytest.mark.parametrize("with_members", [False, True])
def test_update_system_universe_handles_metadata_and_optional_members(monkeypatch, with_members):
    cursor = _ScriptedCursor()
    connection = _install_connection(monkeypatch, cursor)
    service = UniverseService()
    monkeypatch.setattr(service, "_get_system_universe", lambda db, universe_id: _system_row(id=universe_id))
    monkeypatch.setattr(service, "get_system", lambda universe_id: {"id": universe_id, "updated": True})
    replacements = []
    monkeypatch.setattr(
        service,
        "_replace_system_members",
        lambda cur, universe_id, members, effective_from: replacements.append(
            (universe_id, members, effective_from)
        ),
    )
    payload = {"name": "Updated Tech"}
    if with_members:
        payload.update({
            "market": "USStock",
            "universeType": "manual",
            "historyFrom": "2024-01-01",
            "members": [{"symbol": "NVDA"}],
        })

    result = service.update_system(77, payload)

    assert result == {"id": 77, "updated": True}
    assert connection.committed is True
    update_sql = next(sql for sql, _ in cursor.statements if sql.startswith("UPDATE qd_universes"))
    if with_members:
        assert "source = 'manual'" in update_sql
        assert replacements[0][0] == 77
        assert replacements[0][1][0]["symbol"] == "NVDA"
        assert replacements[0][2] == date(2024, 1, 1)
    else:
        assert "source = 'manual'" not in update_sql
        assert replacements == []


@pytest.mark.parametrize(
    ("payload", "error_code"),
    [
        ({"name": ""}, "universe.invalidName"),
        ({"market": "Unknown"}, "universe.invalidMarket"),
        ({"universeType": "watchlist"}, "universe.createTypeUnsupported"),
        ({
            "members": [{"symbol": "AAPL"}],
            "historyFrom": (date.today() + timedelta(days=1)).isoformat(),
        }, "universe.invalidHistoryDate"),
    ],
)
def test_update_system_universe_validates_payload(monkeypatch, payload, error_code):
    cursor = _ScriptedCursor()
    _install_connection(monkeypatch, cursor)
    service = UniverseService()
    monkeypatch.setattr(service, "_get_system_universe", lambda db, universe_id: _system_row(id=universe_id))

    with pytest.raises(UniverseError) as exc_info:
        service.update_system(77, payload)

    assert exc_info.value.code == error_code


def test_update_system_universe_enforces_member_limit(monkeypatch):
    cursor = _ScriptedCursor()
    _install_connection(monkeypatch, cursor)
    service = UniverseService()
    monkeypatch.setattr(service, "_get_system_universe", lambda db, universe_id: _system_row(id=universe_id))
    monkeypatch.setattr(universe_module, "MAX_SYSTEM_MEMBERS", 0)

    with pytest.raises(UniverseError) as exc_info:
        service.update_system(77, {"members": [{"symbol": "AAPL"}]})

    assert exc_info.value.code == "universe.tooManyMembers"


def test_system_visibility_and_delete_commit_expected_changes(monkeypatch):
    cursor = _ScriptedCursor()
    connection = _install_connection(monkeypatch, cursor)
    service = UniverseService()
    monkeypatch.setattr(service, "_get_system_universe", lambda db, universe_id: _system_row(id=universe_id))

    hidden = service.set_system_visibility(77, False)
    deleted = service.delete_system(77)

    assert hidden == {"id": 77, "code": "managed-tech", "status": "hidden"}
    assert deleted == {"id": 77, "code": "managed-tech"}
    assert connection.committed is True
    assert any(params == ("hidden", 77) for _, params in cursor.statements)
    assert any(sql.startswith("DELETE FROM qd_universes") for sql, _ in cursor.statements)


def test_resolve_system_members_serializes_rows(monkeypatch):
    cursor = _ScriptedCursor(many=[[{
        "market": "USStock",
        "symbol": "AAPL",
        "name": "Apple",
        "weight": "0.5",
        "rank": "1",
        "valid_from": date(2023, 1, 1),
        "metadata": '{"sector":"Technology"}',
    }]])
    _install_connection(monkeypatch, cursor)
    service = UniverseService()
    monkeypatch.setattr(service, "_get_system_universe", lambda db, universe_id: _system_row(id=universe_id))

    result = service._resolve_system_members(77, as_of="2024-01-02")

    assert result[0]["symbol"] == "AAPL"
    assert result[0]["weight"] == 0.5
    assert result[0]["rank"] == 1
    assert result[0]["metadata"] == {"sector": "Technology"}
    assert cursor.statements[-1][1] == (77, date(2024, 1, 2), date(2024, 1, 2))


def test_system_universe_lookup_rejects_missing_row():
    cursor = _ScriptedCursor(one=[None])

    with pytest.raises(UniverseError) as exc_info:
        UniverseService._get_system_universe(_Connection(cursor), 404)

    assert exc_info.value.code == "universe.notFound"
    assert exc_info.value.status_code == 404


def _route_logic(route):
    return unwrap(route)


def test_admin_system_routes_delegate_successfully(monkeypatch):
    app = Flask(__name__)

    class Service:
        def create_system(self, payload):
            return {"action": "create", "payload": payload}

        def update_system(self, universe_id, payload):
            return {"action": "update", "id": universe_id, "payload": payload}

        def set_system_visibility(self, universe_id, visible):
            return {"action": "visibility", "id": universe_id, "visible": visible}

        def delete_system(self, universe_id):
            return {"action": "delete", "id": universe_id}

    monkeypatch.setattr(universe_routes, "get_universe_service", lambda: Service())

    with app.test_request_context(json={"name": "Pool"}):
        response, status = _route_logic(universe_routes.create_system_universe)()
        assert status == 201
        assert response.get_json()["data"]["action"] == "create"
    with app.test_request_context(json={"name": "Updated"}):
        response, status = _route_logic(universe_routes.update_system_universe)(77)
        assert status == 200
        assert response.get_json()["data"]["id"] == 77
    with app.test_request_context(json={"visible": False}):
        response, status = _route_logic(universe_routes.set_system_universe_visibility)(77)
        assert status == 200
        assert response.get_json()["data"]["visible"] is False
    with app.test_request_context():
        response, status = _route_logic(universe_routes.delete_system_universe)(77)
        assert status == 200
        assert response.get_json()["data"]["action"] == "delete"


def test_admin_system_routes_return_domain_and_unexpected_errors(monkeypatch):
    app = Flask(__name__)

    class Service:
        def create_system(self, payload):
            if payload.get("unexpected"):
                raise RuntimeError("database unavailable")
            raise UniverseError("universe.invalidName", status_code=422)

        def update_system(self, universe_id, payload):
            if payload.get("domain"):
                raise UniverseError("universe.invalidMarket", status_code=422)
            raise RuntimeError("database unavailable")

        def set_system_visibility(self, universe_id, visible):
            raise RuntimeError("database unavailable")

        def delete_system(self, universe_id):
            if universe_id == 77:
                raise UniverseError("universe.notFound", status_code=404)
            raise RuntimeError("database unavailable")

    monkeypatch.setattr(universe_routes, "get_universe_service", lambda: Service())

    with app.test_request_context(json={}):
        response, status = _route_logic(universe_routes.create_system_universe)()
        assert status == 422
        assert response.get_json()["msg"] == "universe.invalidName"
    with app.test_request_context(json={"unexpected": True}):
        response, status = _route_logic(universe_routes.create_system_universe)()
        assert status == 500
        assert response.get_json()["msg"] == "universe.createFailed"
    with app.test_request_context(json={"domain": True}):
        response, status = _route_logic(universe_routes.update_system_universe)(77)
        assert status == 422
        assert response.get_json()["msg"] == "universe.invalidMarket"
    with app.test_request_context(json={}):
        response, status = _route_logic(universe_routes.update_system_universe)(77)
        assert status == 500
        assert response.get_json()["msg"] == "universe.updateFailed"
    with app.test_request_context(json={"visible": "yes"}):
        response, status = _route_logic(universe_routes.set_system_universe_visibility)(77)
        assert status == 400
        assert response.get_json()["msg"] == "universe.invalidVisibility"
    with app.test_request_context(json={"visible": True}):
        response, status = _route_logic(universe_routes.set_system_universe_visibility)(77)
        assert status == 500
        assert response.get_json()["msg"] == "universe.updateFailed"
    with app.test_request_context():
        response, status = _route_logic(universe_routes.delete_system_universe)(77)
        assert status == 404
        assert response.get_json()["msg"] == "universe.notFound"
    with app.test_request_context():
        response, status = _route_logic(universe_routes.delete_system_universe)(78)
        assert status == 500
        assert response.get_json()["msg"] == "universe.deleteFailed"
