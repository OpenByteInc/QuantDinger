"""
Market regime detection service.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class RegimeProfile:
    key: str
    label: str
    strategy_families: List[str]


class MarketRegimeService:
    """Rule-based market regime detection for the first orchestration version."""

    REGIME_VERSION = 'market-regime-v1'

    REGIME_PROFILES: Dict[str, RegimeProfile] = {
        'bull_trend': RegimeProfile(
            key='bull_trend',
            label='Bull Trend',
            strategy_families=['trend_following', 'breakout', 'pullback_continuation'],
        ),
        'bear_trend': RegimeProfile(
            key='bear_trend',
            label='Bear Trend',
            strategy_families=['trend_following', 'breakdown', 'short_pullback'],
        ),
        'range_compression': RegimeProfile(
            key='range_compression',
            label='Range Compression',
            strategy_families=['mean_reversion', 'bollinger_reversion', 'range_breakout_watch'],
        ),
        'high_volatility': RegimeProfile(
            key='high_volatility',
            label='High Volatility',
            strategy_families=['volatility_breakout', 'reduced_risk_trend', 'event_drive'],
        ),
        'transition': RegimeProfile(
            key='transition',
            label='Transition',
            strategy_families=['hybrid', 'wait_and_see', 'confirmation_breakout'],
        ),
    }

    def detect(self, df: pd.DataFrame, *, symbol: str = '', market: str = '', timeframe: str = '') -> Dict[str, Any]:
        frame = self._normalize_frame(df)
        if len(frame) < 30:
            raise ValueError('At least 30 candles are required for regime detection')

        features = self._extract_features(frame)
        regime_key, confidence = self._classify(features)
        profile = self.REGIME_PROFILES[regime_key]
        segments = self._build_segments(frame, max_segments=4)

        return {
            'version': self.REGIME_VERSION,
            'symbol': symbol,
            'market': market,
            'timeframe': timeframe,
            'regime': profile.key,
            'label': profile.label,
            'confidence': round(confidence, 2),
            'features': features,
            'strategyFamilies': profile.strategy_families,
            'segments': segments,
        }

    def _normalize_frame(self, df: pd.DataFrame) -> pd.DataFrame:
        frame = df.copy()
        frame.columns = [str(col).lower() for col in frame.columns]
        required = {'open', 'high', 'low', 'close'}
        missing = required.difference(frame.columns)
        if missing:
            raise ValueError(f'Missing OHLC columns: {sorted(missing)}')
        frame = frame.dropna(subset=['open', 'high', 'low', 'close']).reset_index(drop=True)
        return frame

    def _extract_features(self, df: pd.DataFrame) -> Dict[str, float]:
        close = df['close'].astype(float)
        high = df['high'].astype(float)
        low = df['low'].astype(float)
        volume = df['volume'].astype(float) if 'volume' in df.columns else pd.Series(dtype=float)

        n = len(close)
        vol_window = min(30, n)
        lookback_bars = min(30, n)

        pct = close.pct_change().replace([np.inf, -np.inf], np.nan).fillna(0.0)
        ema_fast = close.ewm(span=10, adjust=False).mean()
        ema_slow = close.ewm(span=30, adjust=False).mean()
        ema_gap_pct = float(abs((ema_fast.iloc[-1] - ema_slow.iloc[-1]) / max(close.iloc[-1], 1e-9)) * 100.0)
        price_change_pct = float(((close.iloc[-1] / max(close.iloc[0], 1e-9)) - 1.0) * 100.0)
        realized_vol_pct = float(pct.tail(vol_window).std(ddof=0) * np.sqrt(vol_window) * 100.0)

        tr = pd.concat([
            (high - low),
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ], axis=1).max(axis=1).fillna(0.0)
        atr_pct = float((tr.tail(min(14, n)).mean() / max(close.iloc[-1], 1e-9)) * 100.0)

        ref_idx = max(0, n - lookback_bars)
        directional_efficiency = float(
            abs(close.iloc[-1] - close.iloc[ref_idx])
            / max(close.diff().abs().tail(vol_window).sum(), 1e-9)
        )

        if not volume.empty and len(volume.dropna()) >= 20:
            volume_base = max(volume.tail(20).mean(), 1e-9)
            volume_ratio = float(volume.iloc[-1] / volume_base)
        else:
            volume_ratio = 1.0

        return {
            'priceChangePct': round(price_change_pct, 4),
            'emaGapPct': round(ema_gap_pct, 4),
            'realizedVolPct': round(realized_vol_pct, 4),
            'atrPct': round(atr_pct, 4),
            'directionalEfficiency': round(directional_efficiency, 4),
            'volumeRatio': round(volume_ratio, 4),
        }

    def _classify(self, features: Dict[str, float]) -> tuple[str, float]:
        change = features['priceChangePct']
        gap = features['emaGapPct']
        vol = features['realizedVolPct']
        atr = features['atrPct']
        efficiency = features['directionalEfficiency']

        if gap >= 1.0 and efficiency >= 0.55 and change > 1.0:
            return 'bull_trend', min(0.99, 0.55 + gap * 0.12 + efficiency * 0.3)
        if gap >= 1.0 and efficiency >= 0.55 and change < -1.0:
            return 'bear_trend', min(0.99, 0.55 + gap * 0.12 + efficiency * 0.3)
        if vol >= 4.5 or atr >= 3.5:
            return 'high_volatility', min(0.99, 0.5 + max(vol / 10.0, atr / 7.0))
        if gap <= 0.45 and efficiency <= 0.38 and atr <= 2.0:
            return 'range_compression', min(0.99, 0.52 + (0.45 - gap) * 0.35 + (0.38 - efficiency) * 0.25)
        return 'transition', 0.55

    def _build_segments(self, df: pd.DataFrame, *, max_segments: int = 4) -> List[Dict[str, Any]]:
        segment_size = max(30, len(df) // max_segments)
        segments: List[Dict[str, Any]] = []
        for start in range(0, len(df), segment_size):
            subset = df.iloc[start:start + segment_size]
            if len(subset) < 30:
                continue
            features = self._extract_features(subset)
            regime_key, confidence = self._classify(features)
            profile = self.REGIME_PROFILES[regime_key]
            start_time = self._safe_time(subset.iloc[0])
            end_time = self._safe_time(subset.iloc[-1])
            segments.append({
                'regime': regime_key,
                'label': profile.label,
                'confidence': round(confidence, 2),
                'startTime': start_time,
                'endTime': end_time,
            })
        return segments

    @staticmethod
    def _safe_time(row: pd.Series) -> Any:
        for key in ('time', 'timestamp', 'datetime', 'date'):
            if key in row:
                return row[key]
        return None
