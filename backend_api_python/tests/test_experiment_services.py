"""Tests for experiment orchestration services."""

from datetime import datetime

import pandas as pd

from app.services.experiment.evolution import StrategyEvolutionService
from app.services.experiment.regime import MarketRegimeService
from app.services.experiment.runner import ExperimentRunnerService
from app.services.experiment.scoring import StrategyScoringService


def test_market_regime_detects_with_short_tail_segment():
    """Regression: segment tail with 20-29 bars must not crash iloc[-30]."""
    service = MarketRegimeService()
    df = pd.DataFrame({
        'time': pd.date_range('2024-01-01', periods=50, freq='D').astype(str),
        'open': [100 + i * 0.5 for i in range(50)],
        'high': [101 + i * 0.5 for i in range(50)],
        'low': [99 + i * 0.5 for i in range(50)],
        'close': [100 + i * 0.5 for i in range(50)],
        'volume': [1000 for _ in range(50)],
    })

    regime = service.detect(df, symbol='BTC/USDT', market='Crypto', timeframe='1D')

    assert regime['regime']
    assert isinstance(regime.get('segments'), list)


def test_market_regime_detects_bull_trend():
    service = MarketRegimeService()
    df = pd.DataFrame({
        'time': pd.date_range('2024-01-01', periods=60, freq='D').astype(str),
        'open': [100 + i * 1.0 for i in range(60)],
        'high': [101 + i * 1.05 for i in range(60)],
        'low': [99 + i * 0.95 for i in range(60)],
        'close': [100 + i * 1.1 for i in range(60)],
        'volume': [1000 + i * 5 for i in range(60)],
    })

    regime = service.detect(df, symbol='BTC/USDT', market='Crypto', timeframe='1D')

    assert regime['regime'] == 'bull_trend'
    assert regime['confidence'] > 0.5
    assert 'trend_following' in regime['strategyFamilies']


def test_strategy_scoring_outputs_rankable_score():
    service = StrategyScoringService()
    result = {
        'totalReturn': 25,
        'annualReturn': 35,
        'maxDrawdown': 8,
        'sharpeRatio': 1.8,
        'winRate': 56,
        'profitFactor': 1.9,
        'totalTrades': 24,
        'equityCurve': [
            {'time': '2024-01-01', 'value': 10000},
            {'time': '2024-01-02', 'value': 10200},
            {'time': '2024-01-03', 'value': 10400},
        ],
    }

    score = service.score_result(result)

    assert score['overallScore'] > 60
    assert score['grade'] in {'A', 'B', 'C'}
    assert 'drawdownScore' in score['components']


def test_evolution_builds_variants_from_parameter_space():
    service = StrategyEvolutionService()
    variants = service.build_variants(
        base_snapshot={'strategy_config': {'risk': {'stopLossPct': 2}}},
        parameter_space={
            'strategyConfig.risk.stopLossPct': [1, 2],
            'strategyConfig.risk.takeProfitPct': [4, 6],
        },
        max_variants=4,
        method='grid',
    )

    assert len(variants) == 4
    assert variants[0]['snapshot']['strategy_config']['risk']['stopLossPct'] in {1, 2}
    assert 'takeProfitPct' in variants[0]['snapshot']['strategy_config']['risk']


def test_evolution_sweeps_indicator_level_params():
    """Regression: `indicator_params.atr_period` style keys must reach
    `snapshot['indicator_params']['atr_period']`, otherwise the structured
    tuner cannot drive multi-param indicators (RSI/MACD/EMA/ATR etc.) — only
    risk/leverage. Covers the P1 frontend auto-inference path that emits
    `indicator_params.<name>` for every @param the user writes."""
    service = StrategyEvolutionService()
    variants = service.build_variants(
        base_snapshot={
            'strategy_config': {'risk': {'stopLossPct': 2}},
            'indicator_params': {'atr_period': 10, 'multiplier': 3.0},
        },
        parameter_space={
            'indicator_params.atr_period': [7, 14, 21],
            'indicator_params.multiplier': [1.5, 3.0, 4.5],
            'leverage': [1, 2],
        },
        max_variants=6,
        method='grid',
    )

    assert len(variants) == 6
    for v in variants:
        ind = v['snapshot']['indicator_params']
        assert ind['atr_period'] in {7, 14, 21}
        assert ind['multiplier'] in {1.5, 3.0, 4.5}
        assert v['snapshot']['leverage'] in {1, 2}
        # The override map should expose the indicator keys for the UI.
        assert 'indicator_params.atr_period' in v['overrides']
        assert 'indicator_params.multiplier' in v['overrides']

    # Distinct (atr_period, multiplier, leverage) combos must appear — verifies
    # the Cartesian product is shuffled rather than collapsed onto one corner.
    combos = {(v['overrides']['indicator_params.atr_period'],
               v['overrides']['indicator_params.multiplier'],
               v['overrides']['leverage']) for v in variants}
    assert len(combos) == 6


class _FakeBacktestService:
    def _fetch_kline_data(self, market, symbol, timeframe, start_date, end_date):
        return pd.DataFrame({
            'time': pd.date_range('2024-01-01', periods=60, freq='D').astype(str),
            'open': [100 + i for i in range(60)],
            'high': [101 + i for i in range(60)],
            'low': [99 + i for i in range(60)],
            'close': [100 + i * 1.2 for i in range(60)],
            'volume': [1000 for _ in range(60)],
        })

    def run_strategy_snapshot(self, snapshot, start_date, end_date):
        stop_loss = (((snapshot.get('strategy_config') or {}).get('risk') or {}).get('stopLossPct') or 2)
        score_boost = max(0, 3 - float(stop_loss))
        return {
            'totalReturn': 10 + score_boost * 10,
            'annualReturn': 12 + score_boost * 10,
            'maxDrawdown': 14 - score_boost * 3,
            'sharpeRatio': 0.8 + score_boost * 0.6,
            'winRate': 45 + score_boost * 6,
            'profitFactor': 1.1 + score_boost * 0.3,
            'totalTrades': 18,
            'equityCurve': [
                {'time': '2024-01-01', 'value': 10000},
                {'time': '2024-02-01', 'value': 10500 + score_boost * 800},
                {'time': '2024-03-01', 'value': 11000 + score_boost * 1400},
            ],
            'trades': [],
        }


def test_experiment_runner_returns_best_strategy_output():
    runner = ExperimentRunnerService(backtest_service=_FakeBacktestService())
    data = runner.run_pipeline(
        user_id=1,
        payload={
            'base': {
                'indicatorCode': 'output = {}',
                'market': 'Crypto',
                'symbol': 'BTC/USDT',
                'timeframe': '1D',
                'startDate': '2024-01-01',
                'endDate': '2024-03-31',
                'strategyConfig': {'risk': {'stopLossPct': 2}},
            },
            'parameterSpace': {
                'strategyConfig.risk.stopLossPct': [1, 2, 3],
            },
            'evolution': {
                'method': 'grid',
                'maxVariants': 3,
            },
        },
    )

    assert data['bestStrategyOutput'] is not None
    assert data['rankedStrategies'][0]['rank'] == 1
    assert data['regime']['regime'] == 'bull_trend'
    best_stop_loss = data['bestStrategyOutput']['snapshot']['strategy_config']['risk']['stopLossPct']
    assert best_stop_loss == 1
