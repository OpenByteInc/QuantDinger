from datetime import datetime, timezone
from types import SimpleNamespace
import json

import pytest

from app.services import pending_order_worker as worker_module
from app.services.openapi_brokers import ForthrightClient, ForthrightConfig


@pytest.fixture(autouse=True)
def board_lot_metadata(monkeypatch):
    monkeypatch.setattr('app.services.market.hk_board_lots.board_lot_rule', lambda symbol: {"lot_size": 100})


def test_broker_module_exports_preserve_existing_class_identity():
    from app.services.openapi_brokers import clients, tiger, webull, base
    from app.services.openapi_brokers import TigerClient, WebullClient

    assert TigerClient is clients.TigerClient is tiger.TigerClient
    assert WebullClient is clients.WebullClient is webull.WebullClient
    assert clients.BrokerOrderResult is base.BrokerOrderResult
    assert clients.BrokerApiError is base.BrokerApiError
    assert ForthrightClient.__module__.endswith('.forthright')
    assert TigerClient.__module__.endswith('.tiger')
    assert WebullClient.__module__.endswith('.webull')


@pytest.mark.parametrize('market,symbol,exchange,hour,quantity', [
    ('USStock', 'AAPL', 'US', 15, 1.2345),
    ('HKStock', '00700.HK', 'HK', 3, 100),
])
@pytest.mark.parametrize('signal,side', [('open_long', 'B'), ('close_long', 'S')])
@pytest.mark.parametrize('order_type,wire_type', [('limit', 'LO'), ('market', 'MO')])
def test_worker_submits_forthright_stock_wire_order_without_changing_other_brokers(
    monkeypatch, market, symbol, exchange, hour, quantity, signal, side, order_type, wire_type,
):
    from app.services import market_schedule
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 25, hour, tzinfo=timezone.utc).astimezone(tz or timezone.utc)

    monkeypatch.setattr(market_schedule, 'datetime', Clock)
    key = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    client = ForthrightClient(ForthrightConfig('OPA_test', 'test-account', pem, 'test-password'), strategy_units=True)
    calls = []
    monkeypatch.setattr(client, '_trade', lambda method, **body: calls.append((method, body)) or {'order_id': 'F1'})
    worker = object.__new__(worker_module.PendingOrderWorker)
    sent, failed = [], []
    worker._mark_sent = lambda **kwargs: sent.append(kwargs)
    worker._mark_failed = lambda **kwargs: failed.append(kwargs)
    monkeypatch.setattr(worker_module, 'append_strategy_log', lambda *args, **kwargs: None)
    worker._execute_alpaca_order_locked(
        order_id=42, order_row={}, payload={'signal_type': signal, 'symbol': symbol,
            'amount': quantity, 'ref_price': 150, 'order_type': order_type, 'limit_price': 149,
            'protection': {'stop_loss_pct': .05, 'take_profit_pct': .1}},
        client=client, strategy_id=3, exchange_config={'exchange_id': 'forthright', 'credential_id': 18},
        market_category=market, exchange_id='forthright', client_order_id='stable-forthright-intent',
        _notify_live_best_effort=lambda **kwargs: None, _console_print=lambda *args: None,
    )
    assert not failed
    assert len(calls) == 1
    method, body = calls[0]
    assert method == 'place_order'
    assert body['exchange_code'] == exchange
    assert body['product_code'] == symbol.removesuffix('.HK')
    assert body['order_qty'] == quantity
    assert body['side'] == side
    assert body['order_type'] == wire_type
    assert body['order_price'] == (149 if order_type == 'limit' else 150)
    assert body['external_serial_no'] == 'stable-forthright-intent'
    assert 'stop_loss_price' not in body and 'take_profit_price' not in body
    assert sent[0]['exchange_id'] == 'forthright'


@pytest.mark.parametrize('market,symbol', [('USStock', 'AAPL'), ('HKStock', '00700.HK')])
def test_worker_rejects_closed_session_before_connecting_to_any_broker(monkeypatch, market, symbol):
    worker = object.__new__(worker_module.PendingOrderWorker)
    failed = []
    worker._mark_failed = lambda **kwargs: failed.append(kwargs)
    monkeypatch.setattr(worker_module, 'append_strategy_log', lambda *args: None)
    monkeypatch.setattr(worker_module, 'equity_order_session_error', lambda *a, **kw: 'equity_market_closed')
    monkeypatch.setattr(worker_module, 'build_live_order_context', lambda **kw: SimpleNamespace(
        strategy_id=3, signal_type='open_long', symbol=symbol, amount=1, cfg={},
        exchange_config={'exchange_id':'forthright','credential_id':18}, safe_exchange_config={},
        exchange_id='forthright', market_category=market, market_type='spot'))
    monkeypatch.setattr(worker_module, 'create_client', lambda *a, **kw: pytest.fail('No broker connection outside session'))
    worker._execute_live_order(order_id=42, order_row={}, payload={})
    assert failed == [{'order_id':42, 'error':'equity_market_closed'}]


@pytest.mark.parametrize('market,exchange,currency,symbol', [
    ('USStock', 'US', 'USD', 'AAPL'), ('HKStock', 'HK', 'HKD', '00700.HK'),
])
@pytest.mark.parametrize('status', ['8', '6'])
@pytest.mark.parametrize('delayed_fee', [False, True])
def test_stock_fill_sync_preserves_cumulative_fee_currency_on_repeated_snapshot(
    monkeypatch, market, exchange, currency, symbol, status, delayed_fee,
):
    client = object.__new__(ForthrightClient)
    client.is_futures = False
    wire = {'order_id': 'F1', 'exchange_code': exchange, 'product_code': symbol.removesuffix('.HK'),
            'order_status': status, 'order_qty': 200 if status == '6' else 100, 'last_qty': 100, 'last_amt': 34540,
            'side': 'B', 'fare_amt': 64.38, 'ccy': currency}
    result = client._result(client._order(wire))
    pending = client._result(client._order({k: v for k, v in wire.items() if k != 'fare_amt'}))
    snapshots = iter([pending, result, result] if delayed_fee else [result, result])
    monkeypatch.setattr(client, 'get_order_status', lambda _: next(snapshots))
    config = {'exchange_id': 'forthright', 'credential_id': 18}
    monkeypatch.setattr(worker_module, 'load_strategy_configs', lambda _: {
        'user_id': 1, 'market_category': market, 'exchange_config': config})
    monkeypatch.setattr(worker_module, 'resolve_exchange_config', lambda *a, **kw: config)
    monkeypatch.setattr(worker_module, 'create_client', lambda _: client)
    monkeypatch.setattr(worker_module, 'dispatch_requested_cancel', lambda *a: None)
    monkeypatch.setattr(worker_module, 'append_strategy_log', lambda *a: None)
    recorded = []
    monkeypatch.setattr(worker_module, 'persist_strategy_fill', lambda **kw: recorded.append(kw) or (None, None))
    row = {'id': 42, 'strategy_id': 3, 'exchange_id': 'forthright', 'exchange_order_id': 'F1',
           'payload_json': json.dumps({'signal_type': 'open_long', 'symbol': symbol})}
    worker = object.__new__(worker_module.PendingOrderWorker)
    worker._claim_alpaca_sent_order = lambda _: row.copy()
    worker._update_alpaca_sent_order_snapshot = lambda **kw: row.update(kw)
    worker._sync_one_alpaca_sent_order(row)
    worker._sync_one_alpaca_sent_order(row)
    if delayed_fee:
        worker._sync_one_alpaca_sent_order(row)
    assert [r['filled'] for r in recorded] == ([100, 0, 0] if delayed_fee else [100, 0])
    assert [r['commission'] for r in recorded] == ([0, 64.38, 0] if delayed_fee else [64.38, 0])
    for snapshot in recorded[1:] if delayed_fee else recorded:
        assert snapshot['cumulative_filled'] == 100
        assert snapshot['cumulative_fees'] == {currency: 64.38}
        assert snapshot['cumulative_commission_quote'] == 64.38
        assert snapshot['commission_ccy'] == currency
        assert snapshot['market_type'] == market
        assert snapshot['fee_status'] == 'actual'
        assert snapshot['exchange_order_id'] == 'F1'
