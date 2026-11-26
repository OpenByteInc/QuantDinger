from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest

from app.services.market import assets, watchlist
from app.services.market.dated_futures import contract_catalog_rows, futures_products, preset_contracts


def test_preset_catalog_contains_only_public_identity_fields():
    rows = preset_contracts()
    assert len(rows) > 100
    assert len({row['prodCode'] for row in rows}) == len(rows)
    for row in rows:
        assert set(row) == {'prodCode', 'prodName', 'marketCode', 'expiryYmd', 'lastTradeDate', 'instmntCode'}
    roots = {row['symbol'] for row in futures_products()}
    assert {'MES', 'MNQ', 'ZC', 'GC', 'CL'} <= roots


def test_catalog_excludes_expired_and_last_trade_passed():
    today = datetime.now(timezone.utc)
    future = (today + timedelta(days=100)).strftime('%Y%m%d')
    past = (today - timedelta(days=1)).strftime('%Y%m%d')
    base = dict(prodCode='MESZ99', prodName='Micro S&P 500', marketCode='CME', instmntCode='MES', expiryYmd=future)
    assert len(contract_catalog_rows([base])) == 1
    assert contract_catalog_rows([{**base, 'expiryYmd': past}]) == []
    assert contract_catalog_rows([{**base, 'lastTradeDate': past}]) == []
    assert contract_catalog_rows([{**base, 'prodCode': 'MES'}]) == []
    row = contract_catalog_rows([base])[0]
    assert row.product_meta['root'] == 'MES'
    assert row.instrument_id == 'MESZ99'


def test_futures_empty_search_browses_only_unexpired_catalog(monkeypatch):
    calls = []
    class Cursor:
        def execute(self, sql, params): calls.append((sql, params))
        def fetchall(self): return []
        def close(self): pass
    class Connection:
        def cursor(self): return Cursor()
    @contextmanager
    def connection(): yield Connection()
    monkeypatch.setattr(assets, 'get_db_connection', connection)
    assert assets.search_market_assets(market='Futures', keyword='') == []
    assert len(calls) == 1
    assets.search_market_assets(market='Futures', keyword='AUD')
    assert calls[1][1][1:5] == ('%%', '%%', '%%', 'AUD')
    assert 'last_trade_date' in calls[0][0] and "AT TIME ZONE 'UTC'" in calls[0][0]
    assert calls[0][1][1] == '%%'
    assert assets.search_market_assets(market='USStock', keyword='') == []
    assert len(calls) == 2


@pytest.mark.parametrize('meta', [{}, {'expiry': '20000101'}, {'expiry': '20990101', 'last_trade_date': '2000-01-01'}])
def test_stale_or_root_only_futures_cannot_be_added(monkeypatch, meta):
    monkeypatch.setattr(watchlist, 'get_market_asset', lambda **kw: {'asset_id': 123, 'market': 'Futures'})
    monkeypatch.setattr(watchlist, 'get_asset_capabilities', lambda _: {'contexts': [{'product_meta': meta}]})
    assert watchlist.add_watchlist_item(1, asset_id=123) == (False, 'market.assetNotFound')


def test_futures_browse_route_allows_empty_keyword(monkeypatch):
    from app.routes import market
    from flask import Flask
    monkeypatch.setattr(market, 'search_market_assets', lambda **kw: [{'asset_id': 12, 'symbol': 'MESZ99'}])
    with Flask(__name__).test_request_context('/api/market/assets/search?market=Futures'):
        assert market.search_assets().get_json()['data'][0]['asset_id'] == 12
