"""Point-in-time multi-asset data portal for Strategy API V2."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Callable

import pandas as pd

from .frequencies import frequency_seconds, normalize_frequency
from .instruments import parse_instrument


class StrategyDataError(ValueError):
    pass


class MultiAssetDataPortal:
    REQUIRED_COLUMNS = ("open", "high", "low", "close")

    def __init__(
        self,
        frames: Mapping[str, pd.DataFrame],
        *,
        frequency_frames: Mapping[str, Mapping[str, pd.DataFrame]] | None = None,
        driving_frequency: str = "1d",
        universe_resolver: Callable[[str, pd.Timestamp], Iterable[str]] | None = None,
    ) -> None:
        self.driving_frequency = normalize_frequency(driving_frequency)
        self.frames_by_frequency: dict[str, dict[str, pd.DataFrame]] = {}
        self.aliases: dict[str, str] = {}
        self.current_dt: pd.Timestamp | None = None
        self._include_current = False
        # A grid can inspect dozens of resting orders for the same instrument
        # on every bar.  Keep the current timestamp's normalized OHLC rows in
        # memory so those checks do not repeatedly perform pandas index lookup
        # and Series construction.  The cache is deliberately scoped to one
        # timestamp: it cannot expose future data and remains bounded by the
        # number of instruments in the universe.
        self._bar_cache_timestamp: pd.Timestamp | None = None
        self._bar_cache: dict[str, dict[str, Any] | None] = {}
        self.universe_resolver = universe_resolver
        bundles = frequency_frames or {self.driving_frequency: frames}
        for raw_frequency, raw_frames in bundles.items():
            frequency = normalize_frequency(raw_frequency)
            normalized_frames: dict[str, pd.DataFrame] = {}
            for raw_key, raw_frame in raw_frames.items():
                instrument = parse_instrument(raw_key)
                frame = self._normalize_frame(raw_frame, instrument.key)
                normalized_frames[instrument.key] = frame
                self.aliases[instrument.symbol] = instrument.key
                self.aliases[str(raw_key)] = instrument.key
            if normalized_frames:
                self.frames_by_frequency[frequency] = normalized_frames
        if self.driving_frequency not in self.frames_by_frequency:
            raise StrategyDataError(
                f"strategyV2.drivingFrequencyUnavailable:{self.driving_frequency}"
            )
        self.frames = self.frames_by_frequency[self.driving_frequency]
        values: set[pd.Timestamp] = set()
        for frame in self.frames.values():
            values.update(pd.Timestamp(item) for item in frame.index)
        self._timestamps = pd.DatetimeIndex(sorted(values))

    @property
    def timestamps(self) -> pd.DatetimeIndex:
        return self._timestamps

    def set_clock(self, current_dt: Any, *, include_current: bool) -> None:
        self.current_dt = pd.Timestamp(current_dt)
        self._include_current = bool(include_current)

    def resolve_key(self, symbol: object, *, frequency: object = None) -> str:
        frames = self.frames_for_frequency(frequency)
        raw = str(symbol or "").strip()
        if raw in frames:
            return raw
        if raw in self.aliases and self.aliases[raw] in frames:
            return self.aliases[raw]
        parsed = parse_instrument(raw)
        if parsed.key in frames:
            return parsed.key
        matching = [key for key in frames if key.split(":", 1)[-1].split("@", 1)[0] == parsed.symbol]
        if len(matching) == 1:
            return matching[0]
        raise StrategyDataError(f"strategyV2.dataUnavailable:{raw}")

    def frames_for_frequency(self, frequency: object = None) -> dict[str, pd.DataFrame]:
        normalized = normalize_frequency(frequency, self.driving_frequency)
        frames = self.frames_by_frequency.get(normalized)
        if frames is None:
            raise StrategyDataError(f"strategyV2.frequencyNotSubscribed:{normalized}")
        return frames

    def visible_frame(
        self,
        symbol: object,
        count: int | None = None,
        *,
        frequency: object = None,
    ) -> pd.DataFrame:
        normalized = normalize_frequency(frequency, self.driving_frequency)
        frames = self.frames_for_frequency(normalized)
        key = self.resolve_key(symbol, frequency=normalized)
        frame = frames[key]
        cutoff = self._visible_cutoff(normalized)
        if cutoff is None:
            return frame.iloc[0:0].copy()
        end_index = int(frame.index.searchsorted(cutoff, side="right"))
        visible = frame.iloc[:end_index]
        if count is not None and int(count) > 0:
            visible = visible.tail(int(count))
        return visible.copy()

    def history(
        self,
        symbols: object,
        *,
        count: int,
        fields: object = None,
        frequency: object = None,
    ) -> pd.DataFrame | dict[str, pd.DataFrame]:
        normalized = normalize_frequency(frequency, self.driving_frequency)
        requested = _as_list(symbols)
        selected_fields = [str(item).strip().lower() for item in _as_list(fields)] if fields else []
        output: dict[str, pd.DataFrame] = {}
        for symbol in requested:
            key = self.resolve_key(symbol, frequency=normalized)
            frame = self.visible_frame(key, count=count, frequency=normalized)
            if selected_fields:
                available = [field for field in selected_fields if field in frame.columns]
                frame = frame.loc[:, available]
            output[key] = frame
        if len(output) == 1:
            return next(iter(output.values()))
        return output

    def current(
        self,
        symbol: object,
        field: str = "close",
        default: float = 0.0,
        *,
        frequency: object = None,
    ) -> float:
        frame = self.visible_frame(symbol, count=1, frequency=frequency)
        if frame.empty or field not in frame.columns:
            return float(default)
        try:
            value = float(frame.iloc[-1][field])
            return value if value == value else float(default)
        except Exception:
            return float(default)

    def open_at(self, symbol: object, timestamp: Any) -> float | None:
        bar = self.bar_at(symbol, timestamp)
        value = float((bar or {}).get("open") or 0.0)
        return value if value > 0 else None

    def close_at(self, symbol: object, timestamp: Any) -> float | None:
        bar = self.bar_at(symbol, timestamp)
        value = float((bar or {}).get("close") or 0.0)
        return value if value > 0 else None

    def bar_at(self, symbol: object, timestamp: Any) -> dict[str, Any] | None:
        key = self.resolve_key(symbol, frequency=self.driving_frequency)
        ts = pd.Timestamp(timestamp)
        if self._bar_cache_timestamp != ts:
            self._bar_cache_timestamp = ts
            self._bar_cache.clear()
        if key in self._bar_cache:
            return self._bar_cache[key]

        frame = self.frames[key]
        if ts not in frame.index:
            self._bar_cache[key] = None
            return None
        row = frame.loc[ts]
        try:
            bar: dict[str, Any] = {
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row.get("volume") or 0.0),
            }
            for name in (
                "suspended",
                "is_suspended",
                "limit_up",
                "is_limit_up",
                "limit_down",
                "is_limit_down",
                "lot_size",
                "industry",
            ):
                if name in row.index:
                    bar[name] = row.get(name)
            self._bar_cache[key] = bar
            return bar
        except (KeyError, TypeError, ValueError):
            self._bar_cache[key] = None
            return None

    def panel(
        self,
        symbols: Iterable[object] | None = None,
        *,
        count: int | None = None,
        frequency: object = None,
    ) -> dict[str, pd.DataFrame]:
        normalized = normalize_frequency(frequency, self.driving_frequency)
        frames = self.frames_for_frequency(normalized)
        requested = list(symbols or frames.keys())
        return {
            self.resolve_key(symbol, frequency=normalized): self.visible_frame(
                symbol,
                count=count,
                frequency=normalized,
            )
            for symbol in requested
        }

    def universe(self, reference: str) -> list[str]:
        if not self.universe_resolver:
            return []
        when = self.current_dt or pd.Timestamp.utcnow()
        return [parse_instrument(value).key for value in self.universe_resolver(reference, when)]

    def _visible_cutoff(self, frequency: str) -> pd.Timestamp | None:
        if self.current_dt is None:
            return None
        event_time = self.current_dt
        if self._include_current:
            event_time += pd.Timedelta(seconds=frequency_seconds(self.driving_frequency))
        return event_time - pd.Timedelta(seconds=frequency_seconds(frequency))

    @classmethod
    def _normalize_frame(cls, raw: pd.DataFrame, key: str) -> pd.DataFrame:
        if raw is None or raw.empty:
            raise StrategyDataError(f"strategyV2.emptyData:{key}")
        frame = raw.copy()
        if not isinstance(frame.index, pd.DatetimeIndex):
            time_column = next((name for name in ("time", "datetime", "date", "timestamp") if name in frame.columns), "")
            if not time_column:
                raise StrategyDataError(f"strategyV2.timeIndexRequired:{key}")
            frame.index = pd.to_datetime(frame.pop(time_column), errors="coerce")
        else:
            frame.index = pd.to_datetime(frame.index, errors="coerce")
        frame = frame[~frame.index.isna()].sort_index()
        frame = frame[~frame.index.duplicated(keep="last")]
        frame.columns = [str(column).strip().lower() for column in frame.columns]
        missing = [column for column in cls.REQUIRED_COLUMNS if column not in frame.columns]
        if missing:
            raise StrategyDataError(f"strategyV2.ohlcRequired:{key}:{','.join(missing)}")
        if "volume" not in frame.columns:
            frame["volume"] = 0.0
        return frame


def _as_list(value: object) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        return list(value.keys())
    try:
        return list(value)
    except TypeError:
        return [value]
