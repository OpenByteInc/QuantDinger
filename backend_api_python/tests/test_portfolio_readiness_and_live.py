from decimal import Decimal
from types import SimpleNamespace

import pandas as pd
import pytest

from app.services.strategy_v2.readiness import validate_warmup, validate_fundamentals, validate_universe_history
from app.services.strategy_v2 import StrategyV2LiveSession
from app.services.strategy_runtime.live_portfolio import available_strategy_cash, positions_by_symbol, pricing_members
from app.services.strategy_runtime.cancellations import dispatch_requested_cancel
from app.services.fundamental_data import FundamentalDataService


def frame(days=4):
    return pd.DataFrame(dict(open=100., high=101., low=99., close=100., volume=1000.),
                        index=pd.date_range('2026-01-01', periods=days, tz='UTC'))


def test_snapshot_cannot_be_used_before_publication():
    universe = dict(code='sp500', metadata=dict(snapshot_only=True, snapshot_as_of='2026-07-18'))
    with pytest.raises(ValueError, match='universeHistoryUnavailable:sp500:2026-07-18'):
        validate_universe_history(universe, '2025-09-11')
    validate_universe_history(universe, '2026-07-18')


def test_warmup_names_stock_frequency_and_counts_and_uses_join_date():
    bundles = {'1d': {'USStock:AAA': frame()}}
    with pytest.raises(ValueError, match='USStock:AAA@1d=1/3'):
        validate_warmup(bundles, 3, '2026-01-02')
    validate_warmup(bundles, 3, '2026-01-02', [dict(key='USStock:AAA', valid_from='2026-01-04')])


def test_fundamentals_require_values_for_every_member_not_union_of_columns():
    good = frame().assign(net_income=3)
    bad = frame().assign(net_income=float('nan'))
    with pytest.raises(ValueError, match='USStock:BBB/net_income'):
        validate_fundamentals({'USStock:AAA': good, 'USStock:BBB': bad}, {'net_income'})


def test_timezone_aware_panel_uses_daily_market_cap_without_future_financials(monkeypatch):
    svc = FundamentalDataService()
    monkeypatch.setattr(svc, '_load_rows', lambda *args: [dict(
        period_end='2025-09-30', available_at='2026-01-02', shares_outstanding=10,
        market_cap=999, net_income=2)])
    values = frame()
    values.loc[values.index[-1], 'close'] = 120
    actual = svc.enrich_frame(market='USStock', symbol='AAA', frame=values)
    assert pd.isna(actual.iloc[0].net_income)
    assert actual.iloc[-1].market_cap == 1200


def test_live_cash_changes_with_position_value_and_equity():
    candidates = [dict(key='USStock:AAA', market_type='spot')]
    positions = {'USStock:AAA': dict(amount=10, avg_cost=100, last_price=120)}
    assert available_strategy_cash(10200, positions, candidates, 1) == 9000
    assert available_strategy_cash(9500, positions, candidates, 1) == 8300


def test_five_hundred_stock_portfolio_reads_positions_once():
    calls = []
    def fetch(sid, symbol):
        calls.append((sid, symbol))
        return [dict(symbol='S001', side='long', size=2, entry_price=10)]
    candidates = [dict(key=f'USStock:S{i:03}', symbol=f'S{i:03}') for i in range(503)]
    positions = positions_by_symbol(SimpleNamespace(_get_current_positions=fetch), 7, candidates, {})
    assert calls == [(7, None)]
    assert list(positions) == ['USStock:S001']
    assert pricing_members(candidates, positions) == [candidates[1]]
    assert pricing_members(candidates, positions, require_complete=True) == candidates


def test_live_position_snapshot_normalizes_database_decimals():
    candidates = [dict(key='USStock:AAA', symbol='AAA', market_type='spot')]
    executor = SimpleNamespace(_get_current_positions=lambda *_args: [dict(
        symbol='AAA',
        side='long',
        size=Decimal('1.25000000'),
        entry_price=Decimal('123.45670000'),
        current_price=Decimal('124.50000000'),
    )])

    positions = positions_by_symbol(executor, 7, candidates, {})

    assert positions == {
        'USStock:AAA': {
            'amount': 1.25,
            'side': 'long',
            'position_side': '',
            'avg_cost': 123.4567,
            'last_price': 124.5,
        },
    }
    assert all(
        isinstance(positions['USStock:AAA'][field], float)
        for field in ('amount', 'avg_cost', 'last_price')
    )


def test_live_cancel_waits_for_broker_and_survives_restart():
    code = '''
PERSIST_RUNTIME_STATE = True
def initialize(context):
    context.set_universe(["USStock:AAA"])
    context.subscribe(frequency="1d")
def handle_data(context, data):
    pass
'''
    session = StrategyV2LiveSession(code=code, frames={'USStock:AAA': frame()}, initial_capital=10000)
    session.context.update_order_statuses({'entry': dict(status='submitted')})
    assert session.context.cancel_order('entry')
    assert session.context.get_order_status('entry')['status'] == 'cancel_pending'
    restored = StrategyV2LiveSession(code=code, frames={'USStock:AAA': frame()}, initial_capital=10000)
    restored.restore_session_snapshot(session.session_snapshot())
    assert restored.context._cancelled_order_ids == {'entry'}
    restored.context.update_order_statuses({'entry': dict(status='filled')})
    assert not restored.context.cancel_order('entry')
    session.context.order('USStock:AAA', 1, client_order_id='same-cycle')
    assert session.context.cancel_order('same-cycle')
    assert session.context.get_order_status('same-cycle')['status'] == 'cancelled'
    assert session.context.flush_orders() == []


def test_cancel_dispatch_does_not_invent_confirmation_on_failure():
    calls = []
    def cancel(oid):
        calls.append(oid)
        raise RuntimeError('already filled')
    row = dict(id=1, exchange_order_id='broker-1', status='syncing')
    dispatch_requested_cancel(SimpleNamespace(cancel_order=cancel), row,
                              dict(strategy_cancel_requested=True), dict(exchange_id='alpaca'))
    assert calls == ['broker-1']
    assert row['status'] == 'syncing'
