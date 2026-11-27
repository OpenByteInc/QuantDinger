from copy import deepcopy
import sqlite3

import pytest

from app.services.live_trading import fill_accounting as accounting


@pytest.mark.parametrize('currency', ['HKD', 'USD'])
@pytest.mark.parametrize('fee', [65.48, 0.0])
def test_fee_only_correction_updates_callback_total_without_duplicate_fill(monkeypatch, currency, fee):
    rows = [dict(id=1, quantity=40, fee=0, commission_quote=0, fee_status='pending'),
            dict(id=2, quantity=60, fee=0, commission_quote=0, fee_status='pending')]
    calls = []

    class Cursor:
        def execute(self, sql, params):
            calls.append((sql, params))
            if 'SET fee = ' in sql:
                amount, ccy, quote, status, identity = params
                row = next(row for row in rows if row['id'] == identity)
                row.update(fee=amount, fee_ccy=ccy, commission_quote=quote, fee_status=status)
            elif 'SET fee_status = ' in sql:
                next(row for row in rows if row['id'] == params[1])['fee_status'] = params[0]

        def fetchall(self):
            return deepcopy(rows)

    def posted(owner, identity):
        assert (owner, identity) == ('pending_order_id', 42)
        return dict(quantity=100, fees={currency: fee}, quote=fee)

    monkeypatch.setattr(accounting, 'posted_totals', posted)
    trade = dict(strategy_id=3, order_intent_id=7, credential_id=18, exchange_order_id='F1')
    status = 'actual' if fee else 'actual_zero'
    for _ in range(3):
        accounting._sync_runtime_order_fee(Cursor(), 42, trade, fee, status)
    assert sum(row['fee'] for row in rows) == fee
    assert sum(row['commission_quote'] for row in rows) == fee
    assert sum(row['quantity'] for row in rows) == 100
    assert rows[-1]['fee_ccy'] == currency
    assert all(row['fee_status'] == status for row in rows)
    assert all(params == (3, 7, 18, 42, 'F1') for sql, params in calls if 'SELECT' in sql)
    assert not any('INSERT' in sql for sql, _ in calls)


def test_missing_or_incomplete_runtime_projection_is_not_given_another_orders_fees(monkeypatch):
    calls = []

    class Cursor:
        def execute(self, sql, params):
            calls.append(sql)

        def fetchall(self):
            return [dict(id=1, quantity=40)]

    monkeypatch.setattr(accounting, 'posted_totals', lambda *a: dict(quantity=100, fees={'HKD': 65.48}, quote=65.48))
    accounting._sync_runtime_order_fee(Cursor(), 42, dict(strategy_id=3, order_intent_id=7,
        credential_id=18, exchange_id='forthright', exchange_order_id='F1'), 65.48, 'actual')
    assert len(calls) == 1 and 'SELECT' in calls[0]
    accounting._sync_runtime_order_fee(Cursor(), 42, {}, 65.48, 'actual')
    assert len(calls) == 1


def test_runtime_fee_sql_scopes_updates_to_exact_order_account_and_strategy(monkeypatch):
    db = sqlite3.connect(':memory:')
    db.row_factory = lambda cursor, row: dict(zip([column[0] for column in cursor.description], row))
    db.executescript('''
        CREATE TABLE pending_orders (id INTEGER, exchange_id TEXT);
        INSERT INTO pending_orders VALUES (42, 'forthright');
        CREATE TABLE strategy_order_fills (id INTEGER PRIMARY KEY, strategy_id INTEGER,
            order_intent_id INTEGER, credential_id INTEGER, exchange_id TEXT, exchange_order_id TEXT,
            quantity REAL, fee REAL, fee_ccy TEXT, commission_quote REAL, fee_status TEXT);
    ''')
    for identity, strategy, intent, credential, broker, order in [
        (1, 3, 7, 18, 'forthright', 'F1'), (2, 3, 7, 18, 'forthright', 'F1'),
        (3, 4, 7, 18, 'forthright', 'F1'), (4, 3, 8, 18, 'forthright', 'F1'),
        (5, 3, 7, 19, 'forthright', 'F1'), (6, 3, 7, 18, 'tiger', 'F1'),
        (7, 3, 7, 18, 'forthright', 'F2'),
    ]:
        db.execute('INSERT INTO strategy_order_fills VALUES (?,?,?,?,?,?,50,0,NULL,NULL,?)',
                   (identity, strategy, intent, credential, broker, order, 'pending'))

    class Cursor:
        def execute(self, sql, params):
            self.result = db.execute(sql.replace('%s', '?').replace(' FOR UPDATE', ''), params)

        def fetchall(self):
            return self.result.fetchall()

    monkeypatch.setattr(accounting, 'posted_totals', lambda *a: dict(quantity=100, fees={'HKD': 65.48}, quote=65.48))
    trade = dict(strategy_id=3, order_intent_id=7, credential_id=18, exchange_order_id='F1')
    for _ in range(2):
        accounting._sync_runtime_order_fee(Cursor(), 42, trade, 65.48, 'actual')
    rows = db.execute('SELECT * FROM strategy_order_fills ORDER BY id').fetchall()
    assert sum(row['fee'] for row in rows[:2]) == 65.48
    assert all(row['fee_status'] == 'actual' for row in rows[:2])
    assert all(row['fee_status'] == 'pending' and row['fee'] == 0 for row in rows[2:])
    db.close()
