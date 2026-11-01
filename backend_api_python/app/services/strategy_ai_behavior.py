"""Bounded runtime smoke tests for AI-generated Strategy API V2 candidates.

Compilation and AST checks prove that source follows the public contract, but
they cannot prove that a requested signal path is executable.  This module
runs only high-value, deterministic scenarios for fixed single-instrument CTA
candidates where the requested direction can be observed safely.
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

from app.services.strategy_ai_capabilities import StrategyAIGenerationIntent
from app.services.strategy_v2 import StrategyV2BacktestRunner
from app.services.strategy_v2.contract import StrategyV2ContractError


_OBSERVABLE_CAPABILITIES = {"bidirectional", "supertrend", "technical_factors"}
_PANDAS_FREQUENCIES = {
    "1m": "min",
    "5m": "5min",
    "15m": "15min",
    "30m": "30min",
    "1h": "h",
    "4h": "4h",
    "1d": "D",
    "1w": "W-MON",
}


def _eligible_manifest(manifest: Any, intent: StrategyAIGenerationIntent) -> bool:
    if not _OBSERVABLE_CAPABILITIES.intersection(intent.capabilities):
        return False
    if getattr(manifest, "strategy_type", "") != "cta":
        return False
    universe = getattr(manifest, "universe", None)
    instruments = tuple(getattr(universe, "instruments", ()) or ())
    if len(instruments) != 1:
        return False
    frequencies = tuple(getattr(manifest, "frequencies", ()) or ())
    if len(frequencies) != 1 or frequencies[0] not in _PANDAS_FREQUENCIES:
        return False
    if tuple(getattr(manifest, "fundamental_dependencies", ()) or ()):
        return False
    return True


def _scenario_frame(frequency: str, warmup_bars: int) -> pd.DataFrame:
    count = min(900, max(360, int(warmup_bars or 0) + 240))
    index = pd.date_range(
        "2025-01-01",
        periods=count,
        freq=_PANDAS_FREQUENCIES[frequency],
        tz="UTC",
    )
    closes = []
    for offset in range(count):
        # Alternating long regimes plus a shorter cycle exercise common trend,
        # crossover, momentum, overbought, and oversold paths without randomness.
        primary = 320.0 * math.sin(offset / 18.0)
        secondary = 95.0 * math.sin(offset / 5.5)
        regime = 90.0 if (offset // 72) % 2 == 0 else -90.0
        closes.append(max(100.0, 2500.0 + primary + secondary + regime))
    opens = [closes[0], *closes[:-1]]
    spreads = [24.0 + 8.0 * (1.0 + math.sin(offset / 8.0)) for offset in range(count)]
    # Constant volume would zero every relative-volume and volume z-score path.
    # Volume oscillates and expands with the bar's move, so regime breaks spike.
    volumes = [
        1_000_000.0 * (1.0 + 0.5 * math.sin(offset / 3.7)) + 20_000.0 * abs(close - open_)
        for offset, (open_, close) in enumerate(zip(opens, closes))
    ]
    return pd.DataFrame(
        {
            "open": opens,
            "high": [max(open_, close) + spread for open_, close, spread in zip(opens, closes, spreads)],
            "low": [min(open_, close) - spread for open_, close, spread in zip(opens, closes, spreads)],
            "close": closes,
            "volume": volumes,
        },
        index=index,
    )


def _required_open_sides(manifest: Any) -> set[str]:
    direction = str(getattr(manifest, "direction_mode", "") or "")
    if direction == "long_only":
        return {"long"}
    if direction == "short_only":
        return {"short"}
    if direction in {"both", "neutral"}:
        return {"long", "short"}
    return set()


def validate_strategy_ai_behavior(
    code: str,
    manifest: Any,
    intent: StrategyAIGenerationIntent,
) -> dict[str, Any]:
    """Run deterministic execution checks when an intent has observable legs."""
    if not _eligible_manifest(manifest, intent):
        return {"executed": False, "reason": "not_applicable"}

    instrument = manifest.universe.instruments[0]
    frequency = manifest.frequencies[0]
    frame = _scenario_frame(frequency, getattr(manifest, "warmup_bars", 0))
    try:
        runner = StrategyV2BacktestRunner(
            code=code,
            frames={instrument.key: frame},
            initial_capital=100_000.0,
            commission=0.0,
            slippage=0.0,
        )
        result = runner.run()
    except Exception as exc:
        detail = str(exc).strip() or type(exc).__name__
        raise StrategyV2ContractError(
            f"strategyV2.aiBehaviorRuntimeFailed:{detail[:240]}"
        ) from exc

    opened_sides = {
        str(item.get("position_side") or "")
        for item in result.get("rawTrades", ())
        if str(item.get("type") or "") in {"open_long", "open_short"}
    }
    required_sides = _required_open_sides(manifest)
    missing_sides = required_sides - opened_sides
    if missing_sides:
        detail = ",".join(sorted(missing_sides))
        raise StrategyV2ContractError(
            f"strategyV2.aiBehaviorOpenLegMissing:{detail}"
        )

    return {
        "executed": True,
        "sample_count": int(result.get("sampleCount") or 0),
        "total_executions": int(result.get("totalExecutions") or 0),
        "opened_sides": sorted(opened_sides),
    }
