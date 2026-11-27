from decimal import Decimal

import pytest

from app.services.openapi_brokers.forthright import ForthrightClient


@pytest.fixture
def snapshots(monkeypatch):
    client = object.__new__(ForthrightClient)
    client.is_futures = False
    base = dict(exchange_code='HK', product_code='00700', side='B', ccy='HKD',
                order_id='F1', trade_date=20261127, curr_date=20261127,
                last_qty=40, last_px=353.2, serial_no=1, business_time=93001)
    fills = [base, dict(base, last_qty=60, serial_no=2, business_time=93002)]
    order = dict(base, order_status='8', order_qty=100, last_qty=100,
                 last_amt=35320, fare_amt='65.48')
    responses = dict(history_match=fills, today_match=[fills[1]],
                     history_order=[order], today_order=[order])
    monkeypatch.setattr(client, '_query', lambda method, **kw: responses[method])
    return client, responses


@pytest.mark.parametrize('exchange,currency,symbol', [('HK', 'HKD', '00700'), ('US', 'USD', 'AAPL')])
@pytest.mark.parametrize('status', ['8', '6'])
@pytest.mark.parametrize('fee', ['65.48', '0'])
def test_complete_executions_allocate_terminal_order_fee_once(snapshots, exchange, currency, symbol, status, fee):
    client, responses = snapshots
    for rows in responses.values():
        for row in rows:
            row.update(exchange_code=exchange, ccy=currency, product_code=symbol)
    responses['today_order'][0].update(order_status=status, fare_amt=fee)
    rows = client.get_transactions()
    assert len(rows) == 2
    assert sum(Decimal(str(row['fee_amount'])) for row in rows) == Decimal(fee)
    for row in rows:
        assert row['fee_currency'] == row['commission_ccy'] == currency
        assert row['fee_amount'] == row['commission'] == row['commission_quote']
        assert row['fee_scope'] == 'execution' and row['fee_source'] == 'order_allocation'
        assert row['fee_status'] == ('actual' if Decimal(fee) else 'actual_zero')
    assert client.get_transactions(limit=1) == rows[:1]
    assert client.get_transactions() == rows


@pytest.mark.parametrize('change', ['missing_fee', 'nonterminal', 'quantity', 'notional', 'symbol', 'currency', 'side', 'missing_order', 'lookup_error'])
def test_incomplete_or_mismatched_fee_evidence_stays_pending(snapshots, change, monkeypatch):
    client, responses = snapshots
    order = responses['today_order'][0]
    if change == 'missing_fee':
        order.pop('fare_amt')
    elif change == 'nonterminal':
        order['order_status'] = '7'
    elif change == 'quantity':
        order['last_qty'] = 200
    elif change == 'notional':
        order['last_amt'] = 40000
    elif change == 'symbol':
        order['product_code'] = '00388'
    elif change == 'currency':
        order['ccy'] = 'USD'
    elif change == 'side':
        order['side'] = 'S'
    elif change == 'missing_order':
        responses.update(today_order=[], history_order=[])
    else:
        def unavailable(**kwargs):
            raise TimeoutError('offline fixture')
        monkeypatch.setattr(client, '_order_snapshots', unavailable)
    rows = client.get_transactions()
    assert len(rows) == 2
    assert all(row['fee_status'] == 'pending' and 'commission' not in row for row in rows)


def test_fee_rounding_retains_exact_total(snapshots):
    client, responses = snapshots
    first = responses['history_match'][0]
    responses['history_match'] = [dict(first, last_qty=1, last_px=1, serial_no=i) for i in range(3)]
    responses['today_match'] = []
    responses['today_order'][0].update(last_qty=3, last_amt=3, fare_amt='.01')
    assert sum(Decimal(str(row['commission'])) for row in client.get_transactions()) == Decimal('.01')
