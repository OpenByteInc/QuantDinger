from app.services.backtest_presets import (
    PRESET_LIVE_ALIGNED,
    PRESET_EXPLORATION,
    apply_backtest_preset,
)


def test_preset_does_not_override_explicit_enable_mtf():
    data = apply_backtest_preset({
        'backtestPreset': PRESET_LIVE_ALIGNED,
        'enableMtf': False,
        'commission': 0.0005,
        'slippage': 0.0005,
    })
    assert data['enableMtf'] is False
    assert data['backtestPreset'] == PRESET_LIVE_ALIGNED


def test_preset_fills_missing_defaults():
    data = apply_backtest_preset({
        'backtestPreset': PRESET_EXPLORATION,
    })
    assert 'enableMtf' not in data
    assert data['slippage'] == 0.0
    assert data['commission'] == 0.001
