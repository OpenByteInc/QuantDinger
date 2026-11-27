import sqlite3
from contextlib import contextmanager

import pytest

from app.services.pending_orders import alpaca_sync_persistence as persistence


@pytest.mark.parametrize('broker', ['alpaca', 'tiger', 'webull', 'forthright'])
def test_settled_partial_cancel_can_be_claimed_for_late_fee_without_reopening_other_orders(monkeypatch, broker):
    db = sqlite3.connect(':memory:')
    db.row_factory = lambda cursor, row: dict(zip([column[0] for column in cursor.description], row))
    db.executescript('''
        CREATE TABLE pending_orders (id INTEGER PRIMARY KEY, status TEXT, filled REAL,
            exchange_id TEXT, exchange_order_id TEXT, client_order_id TEXT,
            sent_at TEXT, updated_at TEXT, dispatch_note TEXT);
        CREATE TABLE qd_strategy_trades (pending_order_id INTEGER, amount REAL, fee_status TEXT);
    ''')
    for identity, status, quantity, fee_status, updated in [
        (1, 'cancelled', 40, 'pending', '2020-01-01'),
        (2, 'filled', 100, 'pending', '2020-01-01'),
        (3, 'cancelled', 40, 'actual', '2020-01-01'),
        (4, 'cancelled', 0, 'pending', '2020-01-01'),
        (5, 'cancelled', 40, 'pending', '2999-01-01'),
    ]:
        db.execute('INSERT INTO pending_orders VALUES (?,?,?,?,?,?,?,?,?)',
                   (identity, status, quantity, broker, str(identity), '', '2020-01-01', updated, ''))
        db.execute('INSERT INTO qd_strategy_trades VALUES (?,?,?)', (identity, quantity, fee_status))

    class Cursor:
        def __init__(self):
            self.cursor = db.cursor()

        def execute(self, sql, params=()):
            sql = sql.replace("NOW() - (%s * INTERVAL '1 second')", "datetime('now', '-' || ? || ' seconds')")
            self.cursor.execute(sql.replace('NOW()', "datetime('now')").replace('%s', '?'), params)

        def fetchall(self):
            return self.cursor.fetchall()

        def fetchone(self):
            return self.cursor.fetchone()

        def close(self):
            self.cursor.close()

    class Connection:
        cursor = staticmethod(Cursor)
        commit = staticmethod(db.commit)

    @contextmanager
    def connection():
        yield Connection()

    monkeypatch.setattr(persistence, 'get_db_connection', connection)
    worker = object.__new__(persistence.AlpacaOrderSyncPersistenceMixin)
    worker._stale_processing_sec = 0
    worker._fee_sync_retry_sec = 60
    assert [row['id'] for row in worker._fetch_alpaca_sent_orders()] == [1, 2]
    assert worker._claim_alpaca_sent_order(1)['status'] == 'syncing'
    assert worker._claim_alpaca_sent_order(1) is None
    for identity in [3, 4, 5]:
        assert worker._claim_alpaca_sent_order(identity) is None
    db.close()
