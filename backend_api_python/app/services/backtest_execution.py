"""Backtest execution semantics aligned with live ``strict_mode``."""

from __future__ import annotations

from typing import Any, Dict, Optional

DEFAULT_SLIPPAGE = 0.0005


def parse_strict_mode(value: Any, *, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ('1', 'true', 'yes', 'on')
    if value is None:
        return default
    return bool(value)


def merge_strict_mode_into_strategy_config(
    strategy_config: Optional[Dict[str, Any]],
    strict_mode: bool,
) -> Dict[str, Any]:
    """Map live strict_mode → backtest signalTiming (single contract)."""
    cfg = dict(strategy_config or {})
    exec_cfg = dict(cfg.get('execution') or {})
    exec_cfg['signalTiming'] = 'next_bar_open' if strict_mode else 'same_bar_close'
    cfg['execution'] = exec_cfg
    cfg['strictMode'] = bool(strict_mode)
    return cfg


def default_slippage_if_missing(slippage: Any) -> float:
    if slippage in (None, ''):
        return DEFAULT_SLIPPAGE
    try:
        return float(slippage)
    except (TypeError, ValueError):
        return DEFAULT_SLIPPAGE


def precision_info_for_run(
    *,
    strict_mode: bool,
    strategy_timeframe: str,
    mtf_active: bool = False,
    exec_timeframe: Optional[str] = None,
    fallback_reason: Optional[str] = None,
) -> Dict[str, Any]:
    if strict_mode:
        return {
            'enabled': False,
            'mode': 'strict',
            'timeframe': strategy_timeframe,
            'precision': 'strict_bar',
            'message': '严格模式：仅用已收盘 K 线确认信号，下一根 K 线开盘价成交',
        }
    if mtf_active and exec_timeframe:
        return {
            'enabled': True,
            'mode': 'aggressive_1m',
            'timeframe': exec_timeframe,
            'strategyTimeframe': strategy_timeframe,
            'precision': 'aggressive_1m',
            'message': f'非严格模式：策略周期 {strategy_timeframe} 信号当根确认，{exec_timeframe} K 线内撮合（近似实盘 10s 轮询）',
        }
    if fallback_reason:
        return {
            'enabled': False,
            'mode': 'aggressive_bar',
            'timeframe': strategy_timeframe,
            'fallback_reason': fallback_reason,
            'precision': 'aggressive_bar',
            'message': '非严格模式：当根收盘成交（标准 K 线，未使用 1m 子周期）',
        }
    return {
        'enabled': False,
        'mode': 'aggressive_bar',
        'timeframe': strategy_timeframe,
        'precision': 'aggressive_bar',
        'message': '非严格模式：当根收盘成交（标准 K 线）',
    }
