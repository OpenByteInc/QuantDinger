from datetime import date
from io import BytesIO
from types import SimpleNamespace

import openpyxl
import pytest

from app.services.market import hk_board_lots as lots
from app.services.openapi_brokers.forthright import ForthrightClient
from app.services.live_trading import alpaca_ownership as guard


@pytest.fixture
def client(monkeypatch):
    client = object.__new__(ForthrightClient)
    client.is_futures = client.strategy_units = False
    monkeypatch.setattr(lots, "board_lot_rule", lambda symbol: {"lot_size": 500 if str(symbol).startswith("00001") else 100})
    return client


@pytest.mark.parametrize("symbol,quantity,expected", [("00700.HK",111.111,100),("00700.HK",200,200),("00001.HK",999,500),("AAPL",1.123456,1.1234)])
def test_strategy_rounds_to_actual_lot_without_rounding_up(client, symbol, quantity, expected):
    assert client.normalize_strategy_quantity(symbol, quantity) == expected


def test_strategy_budget_below_lot_is_explicit(client):
    with pytest.raises(ValueError, match="belowBoardLot"):
        client.normalize_strategy_quantity("00700.HK", 1000 / 90)


@pytest.mark.parametrize("quantity", [1,99,101,150])
@pytest.mark.parametrize("side", ["buy", "sell"])
def test_manual_odd_lots_are_blocked_before_wire_submission(client, monkeypatch, quantity, side):
    monkeypatch.setattr(client, "_trade", lambda *a, **kw: pytest.fail("Odd lot reached broker"))
    with pytest.raises(ValueError, match="wholeBoardLot"):
        client.place_limit_order(symbol="00700", market_type="HKStock", side=side, quantity=quantity, price=90)


@pytest.mark.parametrize("strategy_units", [False, True])
def test_manual_lots_and_us_fractionals_preserve_quantity(client, monkeypatch, strategy_units):
    client.strategy_units = strategy_units
    calls = []
    monkeypatch.setattr(client, "_trade", lambda *a, **kw: calls.append(kw) or {"order_id":"O1"})
    client.place_limit_order(symbol="00700.HK", market_type="HKStock", side="buy", quantity=100, price=90)
    client.place_limit_order(symbol="AAPL", market_type="USStock", side="buy", quantity=0.1, price=90)
    assert [r["order_qty"] for r in calls] == [100,0.1]


@pytest.mark.parametrize("owned,sellable,expected", [(137,1000,100),(37,1000,None),(500,137,100),(500,0,None)])
def test_exit_reapplies_lot_after_ownership_cap_preserving_residual(client, monkeypatch, owned, sellable, expected):
    monkeypatch.setattr(guard, "ensure_alpaca_settled", lambda **kw: None)
    monkeypatch.setattr(client, "get_positions", lambda **kw:[{"symbol":"00700.HK","quantity":1000,"salable_qty":sellable}])
    monkeypatch.setattr(client, "get_orders", lambda **kw:[])
    monkeypatch.setattr(guard, "list_strategy_allocations_for_account", lambda **kw:[{"strategy_id":1,"side":"long","size":owned}])
    monkeypatch.setattr(guard, "evaluate_and_record_ownership", lambda **kw:SimpleNamespace(allowed=True))
    kwargs=dict(client=client,strategy_id=1,user_id=1,credential_id=18,symbol="00700.HK",signal_type="close_long",amount=1000,order_id=42,exchange_id="forthright")
    if expected is None:
        with pytest.raises(ValueError, match="belowBoardLot"):
            guard.guarded_alpaca_quantity(**kwargs)
    else:
        assert guard.guarded_alpaca_quantity(**kwargs) == expected


def workbook(updated="26/11/2026"):
    wb=openpyxl.Workbook()
    sheet=wb.active
    sheet.append(["List of Securities"])
    sheet.append(["Updated as at " + updated])
    sheet.append(["Stock Code","Category","Board Lot","Trading Currency"])
    for row in [("00700","Equity","100","HKD"),("00001","Equity","500","HKD"),("09999","Equity","1,000","HKD"),("80700","Equity","100","CNY"),("12345","Derivative Warrants","100","HKD")]:
        sheet.append(row)
    output=BytesIO(); wb.save(output); wb.close()
    return output.getvalue()


def test_official_sheet_lots_and_currency_are_not_guessed(monkeypatch):
    monkeypatch.setattr(lots,"_today",lambda:date(2026,11,26))
    data=lots._parse(workbook())
    assert data["securities"] == {"00700":100,"00001":500,"09999":1000}
    assert data["as_of"] == "2026-11-26"


@pytest.mark.parametrize("updated", ["01/11/2026","27/11/2026"])
def test_stale_or_future_source_is_rejected(monkeypatch, updated):
    monkeypatch.setattr(lots,"_today",lambda:date(2026,11,26))
    with pytest.raises(ValueError, match="lotUnavailable"):
        lots._parse(workbook(updated))


@pytest.mark.parametrize("row,expected", [
    ({"board_lot":500,"rules_as_of":"2026-11-26","rules_source":"HKEX"},500),
    ({"board_lot":100,"rules_as_of":"2026-10-01","rules_source":"HKEX"},None),
    ({"board_lot":None,"rules_as_of":"2026-11-26","rules_source":"HKEX"},None),
    (None,None),
])
def test_durable_rules_do_not_fetch_external_data(monkeypatch,row,expected):
    from contextlib import contextmanager
    from app.utils import db as db_module
    calls=[]
    class Cursor:
        def execute(self,sql,params): calls.append((sql,params))
        def fetchone(self): return row
        def close(self): pass
    @contextmanager
    def connection(): yield SimpleNamespace(cursor=lambda:Cursor())
    monkeypatch.setattr(db_module,"get_db_connection",connection)
    monkeypatch.setattr(lots,"_today",lambda:date(2026,11,26))
    monkeypatch.setattr(lots,"_download",lambda:pytest.fail("Trading must read stored rules"))
    if expected:
        assert lots.board_lot_rule("HKSTOCK:00001.HK")["lot_size"] == expected
    else:
        with pytest.raises(ValueError,match="lotUnavailable"):
            lots.board_lot_rule("00001.HK")
    assert calls[0][1] == ("00001",)


def test_tiger_uses_same_rule_before_sdk_and_normalizes_strategy_lots(monkeypatch):
    from app.services.openapi_brokers.tiger import TigerClient
    client=object.__new__(TigerClient)
    monkeypatch.setattr(lots,"board_lot_rule",lambda symbol:{"lot_size":500})
    monkeypatch.setattr(client,"_sdk",lambda:pytest.fail("Odd lot reached SDK"))
    with pytest.raises(ValueError,match="wholeBoardLot"):
        client.place_limit_order(symbol="00001.HK",quantity=100,side="buy",price=10,market_type="HKStock")
    assert client.normalize_strategy_quantity("00001.HK",1111)==1000


def test_hk_sync_persists_each_security_rule(monkeypatch):
    from app.services import symbol_master_sync as sync
    monkeypatch.setattr(lots,"_today",lambda:date(2026,11,26))
    monkeypatch.setattr(lots,"_download",lambda:lots._parse(workbook()))
    rows=sync.fetch_hk_stock_symbols_hkex()
    assert {r.symbol:r.board_lot for r in rows} == {"00700":100,"00001":500,"09999":1000,"80700":100}
    assert all(r.rules_as_of=="2026-11-26" and r.rules_source==lots.SOURCE for r in rows)


def test_rules_route_uses_tenant_connected_client(app, monkeypatch):
    import inspect
    from app.routes import openapi_brokers as routes
    monkeypatch.setattr(routes,"_connected_client",lambda broker:(SimpleNamespace(get_instrument_rules=lambda symbol:{"lot_size":100,"symbol":symbol}),None))
    with app.test_request_context('/api/forthright/instrument-rules?credential_id=18&symbol=00700.HK'):
        result=inspect.unwrap(routes.forthright_instrument_rules)().get_json()
    assert result["data"] == {"lot_size":100,"symbol":"00700.HK"}
