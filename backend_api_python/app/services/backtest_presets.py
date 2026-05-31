"""Shared backtest preset defaults for API routes and strategy snapshots."""

from __future__ import annotations

from typing import Any, Dict

# Ratio units (0.0005 = 0.05%).
DEFAULT_SLIPPAGE = 0.0005
DEFAULT_COMMISSION = 0.001

LIVE_ALIGNED_SLIPPAGE = 0.0005
LIVE_ALIGNED_COMMISSION = 0.0005

PRESET_LIVE_ALIGNED = 'live_aligned'
PRESET_EXPLORATION = 'exploration'


def normalize_preset(value: Any) -> str:
    raw = str(value or '').strip().lower()
    if raw in (PRESET_LIVE_ALIGNED, 'live', 'live-aligned', 'aligned'):
        return PRESET_LIVE_ALIGNED
    if raw in (PRESET_EXPLORATION, 'explore', 'ideal', 'standard'):
        return PRESET_EXPLORATION
    return ''


def preset_defaults(preset: str) -> Dict[str, Any]:
    if preset == PRESET_LIVE_ALIGNED:
        return {
            'backtestPreset': PRESET_LIVE_ALIGNED,
            'slippage': LIVE_ALIGNED_SLIPPAGE,
            'commission': LIVE_ALIGNED_COMMISSION,
        }
    if preset == PRESET_EXPLORATION:
        return {
            'backtestPreset': PRESET_EXPLORATION,
            'slippage': 0.0,
            'commission': DEFAULT_COMMISSION,
        }
    return {}


def apply_backtest_preset(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Merge preset defaults into a backtest request payload (mutates copy).

    Preset values are *defaults only*: explicit request fields (enableMtf,
    commission, slippage) from the UI always win so toggles are not ignored.
    """
    data = dict(payload or {})
    preset = normalize_preset(data.get('backtestPreset'))
    if preset:
        data['backtestPreset'] = preset
        defaults = preset_defaults(preset)
        for key, value in defaults.items():
            if key == 'backtestPreset':
                continue
            if key not in data or data.get(key) in (None, ''):
                data[key] = value
        return data

    if 'slippage' not in data or data.get('slippage') in (None, ''):
        data['slippage'] = DEFAULT_SLIPPAGE
    return data
