"""Search legacy HK catalog aliases without rewriting saved asset IDs."""

from contextlib import contextmanager
import sqlite3

import pytest

from app.services.market import assets


@pytest.fixture
def catalog(monkeypatch):
    db = sqlite3.connect(':memory:')
    db.row_factory = lambda cur, row: dict(zip([col[0] for col in cur.description], row))
    db.executescript('''
        CREATE TABLE qd_market_assets (
            id INTEGER, asset_key TEXT, market TEXT, symbol TEXT, name TEXT,
            asset_class TEXT, product_type TEXT, underlying_market TEXT,
            underlying_symbol TEXT, is_active INTEGER
        );
        CREATE TABLE qd_market_symbols (
            asset_id INTEGER, exchange TEXT, market_type TEXT, instrument_id TEXT,
            settle_currency TEXT, product_type TEXT, api_family TEXT,
            product_meta TEXT, is_active INTEGER
        );
    ''')
    rows = [(1, 'HK07709'), (2, '07709'), (3, '07710'), (4, 'HK07711')]
    for identity, symbol in rows:
        db.execute('INSERT INTO qd_market_assets VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                   (identity, str(identity), 'HKStock', symbol, 'Sample ETF', 'etf', 'hkstock', '', '', 1))
        db.execute('INSERT INTO qd_market_symbols VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                   (identity, 'HKEX', 'spot', '', 'HKD', 'hkstock', 'spot', '{}', 1))

    @contextmanager
    def connection():
        yield db
    monkeypatch.setattr(assets, 'get_db_connection', connection)
    yield db
    db.close()


@pytest.mark.parametrize('keyword', ['07709', 'HK07709', 'HK.07709', '7709.HK', 'HKStock:07709'])
def test_alias_search_returns_one_canonical_asset(catalog, keyword):
    rows = assets.search_market_assets(market='HKStock', keyword=keyword)
    assert [(row['asset_id'], row['symbol']) for row in rows] == [(2, '07709')]
    assert rows[0]['market_types'] == ['spot']


def test_deduplication_precedes_limit_and_retains_old_ids(catalog):
    rows = assets.search_market_assets(market='HKStock', keyword='Sample', limit=3)
    assert {row['symbol'] for row in rows} == {'07709', '07710', '07711'}
    assert next(row for row in rows if row['symbol'] == '07711')['asset_id'] == 4
    assert assets.get_market_asset(asset_id=1)['symbol'] == 'HK07709'


def test_partial_numeric_search_still_matches_multiple_securities(catalog):
    rows = assets.search_market_assets(market='HKStock', keyword='77')
    assert {row['symbol'] for row in rows} == {'07709', '07710', '07711'}


def test_us_tickers_are_not_rewritten_as_hk_aliases(catalog):
    catalog.execute("UPDATE qd_market_assets SET market = 'USStock' WHERE id = 1")
    rows = assets.search_market_assets(market='USStock', keyword='HK07709')
    assert rows[0]['symbol'] == 'HK07709'
