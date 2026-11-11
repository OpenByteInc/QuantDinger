"""Admin system-strategies exchange column resolution."""

import pytest

from app.routes.user import (
    _admin_strategy_ledger_metrics,
    _strategy_exchange_display_name,
    _strategy_v2_admin_metadata,
)


def test_inline_exchange_id():
    assert _strategy_exchange_display_name(
        {'exchange_id': 'binance'},
        credential_map={},
    ) == 'binance'


def test_credential_id_uses_credential_map():
    assert _strategy_exchange_display_name(
        {'credential_id': 42},
        credential_map={42: {'id': 42, 'exchange_id': 'okx', 'name': 'Main OKX'}},
    ) == 'okx'


def test_credential_id_without_map_returns_empty():
    assert _strategy_exchange_display_name(
        {'credential_id': 99},
        credential_map={},
        user_id=1,
    ) == ''


def test_legacy_exchange_key():
    assert _strategy_exchange_display_name(
        {'exchange': 'gate'},
        credential_map={},
    ) == 'gate'


def test_strategy_v2_admin_metadata_classifies_cta_contract():
    metadata = _strategy_v2_admin_metadata(
        strategy_type='StrategyV2',
        trading_config={
            'api_version': 2,
            'script_source_id': 18,
            'strategy_manifest': {
                'apiVersion': 2,
                'strategyType': 'cta',
                'primaryFrequency': '4h',
                'markets': ['Crypto'],
                'universe': {
                    'kind': 'static',
                    'instruments': [
                        {'market': 'Crypto', 'symbol': 'BTC/USDT', 'market_type': 'swap'},
                    ],
                },
                'schedules': [],
                'warmupBars': 55,
                'leverageAllowed': True,
                'maxLeverage': 5,
            },
        },
        source_id=18,
        source_name='MACD and KDJ Confirmation',
        source_asset_type='script',
    )

    assert metadata['contract_ready'] is True
    assert metadata['strategy_class'] == 'cta'
    assert metadata['instrument_count'] == 1
    assert metadata['universe_symbols'] == ['BTC/USDT']
    assert metadata['market_types'] == ['swap']
    assert metadata['primary_frequency'] == '4h'
    assert metadata['max_leverage'] == 5


def test_strategy_v2_admin_metadata_classifies_portfolio_reference():
    metadata = _strategy_v2_admin_metadata(
        strategy_type='StrategyV2',
        trading_config={
            'api_version': 2,
            'script_source_id': 22,
            'strategy_manifest': {
                'apiVersion': 2,
                'strategyType': 'portfolio',
                'primaryFrequency': '1d',
                'markets': ['USStock'],
                'universe': {'kind': 'reference', 'reference': 'nasdaq100', 'instruments': []},
                'schedules': [{'frequency': 'weekly', 'weekday': 1, 'time': '09:35'}],
            },
        },
        source_id=22,
        source_asset_type='portfolio_strategy',
    )

    assert metadata['strategy_class'] == 'portfolio'
    assert metadata['universe_reference'] == 'nasdaq100'
    assert metadata['schedule_count'] == 1


def test_strategy_v2_admin_metadata_classifies_robot_from_source():
    metadata = _strategy_v2_admin_metadata(
        strategy_type='StrategyV2',
        trading_config={
            'api_version': 2,
            'script_source_id': 30,
            'strategy_manifest': {
                'apiVersion': 2,
                'strategyType': 'cta',
                'primaryFrequency': '1m',
                'markets': ['Crypto'],
                'universe': {'kind': 'static', 'instruments': []},
            },
        },
        source_id=30,
        source_template_key='robot_v2_grid',
        source_metadata={'source': 'robot_builder'},
        fallback_symbol='BTC/USDT',
        fallback_market_type='swap',
    )

    assert metadata['strategy_class'] == 'robot'
    assert metadata['universe_symbols'] == ['BTC/USDT']


def test_strategy_v2_admin_metadata_exposes_declared_grid_executor():
    metadata = _strategy_v2_admin_metadata(
        strategy_type='StrategyV2',
        trading_config={
            'api_version': 2,
            'script_source_id': 31,
            'strategy_manifest': {
                'apiVersion': 2,
                'strategyType': 'cta',
                'metadata': {
                    'strategy_family': 'grid',
                    'executor_type': 'grid',
                },
                'universe': {'kind': 'static', 'instruments': []},
            },
        },
        source_id=31,
        fallback_symbol='ETH/USDT',
        fallback_market_type='spot',
    )

    assert metadata['strategy_class'] == 'robot'
    assert metadata['bot_type'] == 'grid'


def test_signal_admin_metrics_use_strategy_capital_and_virtual_ledger():
    metrics = _admin_strategy_ledger_metrics(
        execution_mode='signal',
        strategy_initial_capital=1000,
        positions=[{'unrealized_pnl': 12.5, 'equity': 0}],
        trade_stats={
            'trade_count': 7,
            'total_realized_pnl': -2.5,
            'virtual_initial_capital': 500,
        },
    )

    assert metrics['ledger_mode'] == 'virtual'
    assert metrics['initial_capital'] == pytest.approx(1000)
    assert metrics['position_count'] == 1
    assert metrics['trade_count'] == 7
    assert metrics['total_pnl'] == pytest.approx(10)
    assert metrics['total_equity'] == pytest.approx(1010)
    assert metrics['roi'] == pytest.approx(1)


def test_live_admin_metrics_keep_live_position_equity():
    metrics = _admin_strategy_ledger_metrics(
        execution_mode='live',
        strategy_initial_capital=1000,
        positions=[{'unrealized_pnl': 8, 'equity': 1008}],
        trade_stats={
            'trade_count': 3,
            'total_realized_pnl': 4,
            'virtual_initial_capital': 500,
        },
    )

    assert metrics['ledger_mode'] == 'live'
    assert metrics['initial_capital'] == pytest.approx(1000)
    assert metrics['total_pnl'] == pytest.approx(12)
    assert metrics['total_equity'] == pytest.approx(1008)
