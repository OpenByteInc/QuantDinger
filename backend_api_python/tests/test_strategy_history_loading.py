"""History reads stay bounded at the API and never wait for venue REST calls."""
from inspect import unwrap
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from flask import Flask, g

from app.routes import strategy_ledger_routes as trades
from app.routes import strategy_logs_routes as logs
from app.services.execution_streams import pnl_reconciliation as pnl
from app.tasks import reported_pnl as backfill


def _row(id=1, **extra):
    return dict(id=id, credential_id=1, exchange_id='bitget', market_type='swap',
                symbol='BTC/USDT', exchange_order_id='order', type='close_long',
                amount=.5, created_at=1, **extra)


@pytest.mark.parametrize('query,page,size,count', [
    ('page=1&page_size=10', 1, 10, 10), ('page=2&page_size=10', 2, 10, 10),
    ('page=999', 999, 10, 0), ('page=-3&page_size=0', 1, 1, 1),
    ('page=x&page_size=bad', 1, 10, 10), ('page_size=5000', 1, 100, 25),
])
def test_trade_response_pagination_bounds(query, page, size, count):
    with Flask(__name__).test_request_context('/?' + query):
        rows, metadata = trades._trade_page([{'id': i} for i in range(25)])
    assert metadata == dict(total=25, page=page, page_size=size)
    assert len(rows) == count
    if rows:
        assert rows[0]['id'] == (page - 1) * size


@pytest.mark.parametrize('exchange', ['okx', 'bitget', 'binance', 'gate', 'bybit', 'htx', 'tiger'])
def test_http_report_read_never_creates_client_or_claim(monkeypatch, exchange):
    rows = [_row()]
    rows[0]['exchange_id'] = exchange
    if exchange == 'tiger':
        rows[0]['market_type'] = 'hkstock'
    monkeypatch.setattr(pnl, '_load', lambda keys: ({}, {}))
    monkeypatch.setattr(pnl, '_client', lambda *a: pytest.fail('No venue work during HTTP read'))
    monkeypatch.setattr(pnl, '_claim', lambda *a: pytest.fail('Read must not claim a REST request'))
    pnl.enrich_reported_order_pnl(rows, user_id=1, trading_config={}, fetch_missing=False)
    assert rows[0]['exchange_pnl']['status'] == 'pending'


def test_background_queue_is_bounded_and_only_includes_missing_reports(monkeypatch):
    rows = [_row(i, exchange_pnl={'status': 'pending'}) for i in range(1, 7)]
    for row in rows:
        row['exchange_order_id'] = str(row['id'])
    rows[0]['exchange_pnl']['status'] = 'reported'
    rows[1]['exchange_pnl']['status'] = 'unsupported'
    claim, queued = MagicMock(return_value=True), MagicMock()
    monkeypatch.setattr(backfill, '_claim', claim)
    monkeypatch.setattr(backfill.reconcile_reported_pnl, 'apply_async', queued)
    backfill.schedule_reported_pnl(rows, strategy_id=7, user_id=9)
    assert claim.call_count == queued.call_count == 2
    assert [call.kwargs['args'][2][-1] for call in queued.call_args_list] == ['3', '4']
    assert all(call.kwargs['queue'] == 'maintenance' for call in queued.call_args_list)


def test_queue_outage_does_not_fail_history_or_keep_retrying(monkeypatch):
    rows = [_row(i, exchange_pnl={'status': 'pending'}) for i in range(1, 4)]
    for row in rows:
        row['exchange_order_id'] = str(row['id'])
    monkeypatch.setattr(backfill, '_claim', lambda *a: True)
    queued = MagicMock(side_effect=ConnectionError('offline'))
    monkeypatch.setattr(backfill.reconcile_reported_pnl, 'apply_async', queued)
    backfill.schedule_reported_pnl(rows, strategy_id=7, user_id=9)
    assert queued.call_count == 1


def test_backfill_rechecks_strategy_owner(monkeypatch):
    service = SimpleNamespace(get_strategy=MagicMock(return_value=None))
    monkeypatch.setattr(backfill, 'get_strategy_service', lambda: service)
    monkeypatch.setattr(backfill, 'get_db_connection', lambda: pytest.fail('No access to another owner'))
    backfill.reconcile_reported_pnl.run(7, 9, [1, 'okx', 'swap', 'BTC/USDT', 'order'])
    service.get_strategy.assert_called_once_with(7, user_id=9)


@pytest.mark.parametrize('after,limit', [(0, 200), (100, 10), (100, 5000), (-1, -3)])
def test_incremental_logs_bound_query_and_advance_past_filtered_ticks(monkeypatch, after, limit):
    connection = MagicMock()
    cursor = connection.__enter__.return_value.cursor.return_value
    cursor.fetchall.return_value = [dict(id=103, message='tick price=1', level='info'),
                                   dict(id=102, message='ready', level='info')]
    service = SimpleNamespace(get_strategy=MagicMock(return_value={'id': 7}))
    monkeypatch.setattr(logs, 'get_db_connection', lambda: connection)
    monkeypatch.setattr(logs, 'get_strategy_service', lambda: service)
    with Flask(__name__).test_request_context(f'/?id=7&after_id={after}&limit={limit}'):
        g.user_id = 9
        result = unwrap(logs.get_strategy_logs)().get_json()
    service.get_strategy.assert_called_once_with(7, user_id=9)
    assert cursor.execute.call_args.args[1] == (7, max(0, after), max(1, min(500, limit)))
    assert result['last_id'] == 103
    assert [row['id'] for row in result['data']] == [102]


@pytest.mark.parametrize('route', [trades, logs])
def test_history_ownership_is_checked_before_any_history_query(monkeypatch, route):
    monkeypatch.setattr(route, 'get_strategy_service', lambda: SimpleNamespace(get_strategy=lambda *a, **k: None))
    monkeypatch.setattr(route, 'get_db_connection', lambda: pytest.fail('No history query before authorization'))
    handler = trades.get_trades if route is trades else logs.get_strategy_logs
    with Flask(__name__).test_request_context('/?id=7&page=1&after_id=10'):
        g.user_id = 9
        _, status = unwrap(handler)()
    assert status == 404
