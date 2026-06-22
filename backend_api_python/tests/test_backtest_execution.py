from app.services.backtest_execution import (
    default_slippage_if_missing,
    merge_strict_mode_into_strategy_config,
    parse_strict_mode,
    precision_info_for_run,
)


def test_parse_strict_mode_defaults_true():
    assert parse_strict_mode(None) is True
    assert parse_strict_mode('true') is True
    assert parse_strict_mode('0') is False
    assert parse_strict_mode(False) is False


def test_merge_strict_mode_maps_signal_timing():
    strict_cfg = merge_strict_mode_into_strategy_config({}, True)
    assert strict_cfg['execution']['signalTiming'] == 'next_bar_open'
    assert strict_cfg['strictMode'] is True

    aggressive_cfg = merge_strict_mode_into_strategy_config({'execution': {'signalTiming': 'next_bar_open'}}, False)
    assert aggressive_cfg['execution']['signalTiming'] == 'same_bar_close'
    assert aggressive_cfg['strictMode'] is False


def test_default_slippage_if_missing():
    assert default_slippage_if_missing(None) == 0.0005
    assert default_slippage_if_missing('') == 0.0005
    assert default_slippage_if_missing(0.001) == 0.001


def test_precision_info_for_run_modes():
    strict = precision_info_for_run(strict_mode=True, strategy_timeframe='1H')
    assert strict['mode'] == 'strict'
    assert strict['enabled'] is False

    aggressive_1m = precision_info_for_run(
        strict_mode=False,
        strategy_timeframe='1H',
        mtf_active=True,
        exec_timeframe='1m',
    )
    assert aggressive_1m['mode'] == 'aggressive_1m'
    assert aggressive_1m['enabled'] is True

    aggressive_bar = precision_info_for_run(
        strict_mode=False,
        strategy_timeframe='1D',
        mtf_active=False,
        fallback_reason='non_crypto',
    )
    assert aggressive_bar['mode'] == 'aggressive_bar'
