"""Shared multi-timeframe loading for live Strategy API V2 sessions."""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Callable

import pandas as pd

from app.services.fundamental_data import get_fundamental_data_service
from app.services.strategy_v2.frequencies import frequency_seconds
from app.services.strategy_v2.models import StrategyManifest
from app.services.strategy_v2.service import StrategyV2BacktestService


def live_history_days(frequency: str, warmup_bars: int) -> int:
    """Return a frequency-aware live lookback with a startup buffer."""
    bars = max(10, max(1, int(warmup_bars or 0)) * 3)
    seconds = frequency_seconds(frequency) * bars
    return max(1, int(math.ceil(seconds / 86_400)))


def load_live_frequency_frames(
    *,
    service: StrategyV2BacktestService,
    candidates: list[dict[str, object]],
    manifest: StrategyManifest,
    end_date: datetime,
    exchange_config: dict[str, object] | None = None,
    strict_data_source: bool = False,
    warn: Callable[[str], None] | None = None,
) -> dict[str, dict[str, pd.DataFrame]]:
    """Load a complete live frame bundle for all declared strategy timeframes."""
    start_dates = {
        frequency: end_date
        - timedelta(days=live_history_days(frequency, manifest.warmup_bars))
        for frequency in manifest.frequencies
    }
    bundles, skipped = service.fetch_frequency_frames(
        candidates,
        manifest.frequencies,
        start_dates,
        end_date,
        exchange_config=exchange_config,
        strict_data_source=strict_data_source,
    )
    if skipped and warn:
        details = ", ".join(
            f"{item.get('symbol') or '?'}@{item.get('frequency') or '?'}:"
            f"{item.get('reason') or 'unavailable'}"
            for item in skipped[:5]
        )
        suffix = f" ({details})" if details else ""
        warn(
            f"Skipped {len(skipped)} instrument/timeframe data source(s)"
            f" without usable market data{suffix}"
        )

    driving_frequency = manifest.driving_frequency
    driving_frames = bundles.get(driving_frequency, {})
    if manifest.fundamental_dependencies:
        driving_frames = get_fundamental_data_service().enrich_panel(
            driving_frames,
            candidates,
        )
        bundles[driving_frequency] = driving_frames
        service.validate_fundamental_dependencies(driving_frames, manifest)
    if not driving_frames:
        raise RuntimeError("strategyV2.noMarketData")
    return bundles


__all__ = ["live_history_days", "load_live_frequency_frames"]
