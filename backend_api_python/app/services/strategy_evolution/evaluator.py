"""Prepared Strategy V2 evaluator for one evolution study."""

from __future__ import annotations

from datetime import datetime, timedelta
import math
from typing import Any

import pandas as pd

from app.services.strategy_v2 import StrategyV2BacktestService
from app.services.strategy_v2.contract import compile_strategy_v2
from app.services.strategy_v2.frequencies import periods_per_year
from app.services.strategy_v2.service import (
    _attach_catalog_products,
    _enforce_backtest_range,
    _warmup_calendar_days,
)


class PreparedEvolutionEvaluator:
    """Load a study's market data once and reuse in-memory slices."""

    def __init__(
        self,
        *,
        backtest_service: StrategyV2BacktestService,
        user_id: int,
        code: str,
        start_date: datetime,
        end_date: datetime,
        initial_capital: float,
        leverage_enabled: bool,
        leverage: float,
        source_id: int,
        strategy_name: str,
        candidate_limit: int | None = None,
    ) -> None:
        self.user_id = user_id
        self.code = code
        self.initial_capital = initial_capital
        self.leverage_enabled = leverage_enabled
        self.leverage = leverage
        self.source_id = source_id
        self.strategy_name = strategy_name
        self._frames: dict[tuple[str, str, str, str, str, str, str], pd.DataFrame] = {}
        self._results: dict[tuple[Any, ...], dict[str, Any]] = {}
        self.stats = {"backtestRuns": 0, "cacheHits": 0}

        program = compile_strategy_v2(code)
        manifest = program.manifest
        self.strategy_type = manifest.strategy_type
        self.universe_kind = manifest.universe.kind
        self.driving_frequency = manifest.driving_frequency
        self.warmup_bars = manifest.warmup_bars
        candidates, universe_id = backtest_service.resolve_candidates(
            user_id=user_id,
            manifest=manifest,
            start_date=start_date,
            end_date=end_date,
        )
        self.universe_id = universe_id
        self.universe_size = len(candidates)
        self.symbol_coverage_estimated = bool(
            candidate_limit and self.universe_size > int(candidate_limit)
        )
        if self.symbol_coverage_estimated:
            indices = _spread_indices(self.universe_size, int(candidate_limit))
            candidates = [candidates[index] for index in indices]
        self.symbols_checked = len(candidates)
        _attach_catalog_products(candidates)
        fetch_starts = {
            frequency: start_date
            - timedelta(
                days=_warmup_calendar_days(
                    frequency,
                    manifest.warmup_bars,
                    candidates,
                )
            )
            for frequency in manifest.frequencies
        }
        for frequency in manifest.frequencies:
            _enforce_backtest_range(
                candidates=candidates,
                timeframe=frequency,
                start_date=start_date,
                end_date=end_date,
                warmup_bars=manifest.warmup_bars,
                fetch_start=fetch_starts[frequency],
            )
        frequency_frames, skipped = backtest_service.fetch_frequency_frames(
            candidates,
            manifest.frequencies,
            fetch_starts,
            end_date,
        )
        driving_frames = frequency_frames.get(self.driving_frequency, {})
        self.symbols_used = len(driving_frames)
        self.skipped_symbols = len({
            str(item.get("symbol") or "")
            for item in skipped
            if str(item.get("symbol") or "")
        })
        for frequency, frames in frequency_frames.items():
            for member in candidates:
                frame = frames.get(str(member.get("key") or ""))
                if frame is None or frame.empty:
                    continue
                self._frames[self._identity(member, frequency)] = frame

        self.backtest_service = StrategyV2BacktestService(
            repository=backtest_service.repository,
            universe_service=backtest_service.universe_service,
            frame_fetcher=self._fetch_frame,
            fundamental_enricher=backtest_service.fundamental_enricher,
            data_kind=backtest_service.data_kind,
            data_source=f"{backtest_service.data_source}:evolution_memory",
            snapshot_store=backtest_service.snapshot_store,
            instrument_rules_provider=_CachedInstrumentRulesProvider(backtest_service.instrument_rules_provider),
        )

    def walk_forward_context(self, start_date: datetime, end_date: datetime) -> dict[str, Any]:
        merged = pd.DatetimeIndex([])
        start = _naive_timestamp(start_date)
        end = _naive_timestamp(end_date)
        for identity, frame in self._frames.items():
            if identity[2] != self.driving_frequency or frame.empty:
                continue
            index = pd.DatetimeIndex(frame.index)
            index = index[(index >= start) & (index <= end)]
            merged = merged.union(index, sort=False)
        merged = merged.sort_values().unique()
        return {
            "observations": tuple(item.to_pydatetime() for item in merged),
            "warmupBars": self.warmup_bars,
            "frequency": self.driving_frequency,
            "strategyType": self.strategy_type,
            "universeKind": self.universe_kind,
            "universeId": self.universe_id,
            "universeSize": self.universe_size,
            "symbolsChecked": self.symbols_checked,
            "symbolsUsed": self.symbols_used,
            "skippedSymbols": self.skipped_symbols,
            "symbolCoveragePercent": round(
                self.symbols_used / self.symbols_checked * 100.0,
                2,
            ) if self.symbols_checked else 0.0,
            "symbolCoverageEstimated": self.symbol_coverage_estimated,
        }

    def __call__(
        self,
        params: dict[str, Any],
        start_date: datetime,
        end_date: datetime,
        commission: float,
        slippage: float,
    ) -> dict[str, Any]:
        cache_key = (
            tuple(sorted((str(key), repr(value)) for key, value in params.items())),
            start_date.isoformat(),
            end_date.isoformat(),
            float(commission),
            float(slippage),
        )
        cached = self._results.get(cache_key)
        if cached is not None:
            self.stats["cacheHits"] += 1
            return cached
        self.stats["backtestRuns"] += 1
        _run_id, result = self.backtest_service.run(
            user_id=self.user_id,
            code=self.code,
            start_date=start_date,
            end_date=end_date,
            initial_capital=self.initial_capital,
            leverage_enabled=self.leverage_enabled,
            leverage=self.leverage,
            commission=commission,
            slippage=slippage,
            params=params,
            persist=False,
            source_id=self.source_id,
            strategy_name=self.strategy_name,
            analysis_only=True,
        )
        self._results[cache_key] = result
        return result

    def evaluate_plan(
        self,
        params: dict[str, Any],
        folds: tuple[Any, ...],
        commission: float,
        slippage: float,
    ) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        if not folds:
            return []
        result = self(
            params,
            folds[0].train_start,
            folds[-1].validation_end,
            commission,
            slippage,
        )
        return [
            (
                _slice_result(result, fold.train_start, fold.train_end),
                _slice_result(result, fold.validation_start, fold.validation_end),
            )
            for fold in folds
        ]

    def _fetch_frame(
        self,
        market: str,
        symbol: str,
        timeframe: str,
        start_date: datetime,
        end_date: datetime,
        *,
        market_type: str = "",
        exchange_id: str = "",
        instrument_id: str = "",
        api_family: str = "",
    ) -> pd.DataFrame:
        identity = (
            str(market or ""),
            str(symbol or ""),
            str(timeframe or "").lower(),
            str(market_type or ""),
            str(exchange_id or ""),
            str(instrument_id or ""),
            str(api_family or ""),
        )
        frame = self._frames.get(identity)
        if frame is None or frame.empty:
            return pd.DataFrame()
        start = _naive_timestamp(start_date)
        end = _naive_timestamp(end_date)
        return frame.loc[(frame.index >= start) & (frame.index <= end)].copy(deep=False)

    @staticmethod
    def _identity(member: dict[str, Any], frequency: str) -> tuple[str, str, str, str, str, str, str]:
        return (
            str(member.get("market") or ""),
            str(member.get("symbol") or ""),
            str(frequency or "").lower(),
            str(member.get("market_type") or ""),
            str(member.get("exchange_id") or ""),
            str(member.get("instrument_id") or ""),
            str(member.get("api_family") or ""),
        )


def _naive_timestamp(value: datetime) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_convert("UTC").tz_localize(None)
    return timestamp


def _spread_indices(total: int, count: int) -> tuple[int, ...]:
    if count >= total:
        return tuple(range(total))
    if count <= 1:
        return (0,)
    return tuple(round(index * (total - 1) / (count - 1)) for index in range(count))


class _CachedInstrumentRulesProvider:
    def __init__(self, provider: Any) -> None:
        self.provider = provider
        self.cache: dict[tuple[Any, ...], Any] = {}

    def historical_snapshot(self, candidates, **kwargs):
        identity = tuple(
            sorted(
                (
                    str(item.get("market") or ""),
                    str(item.get("symbol") or ""),
                    str(item.get("exchange_id") or ""),
                    str(item.get("instrument_id") or ""),
                )
                for item in candidates
            )
        )
        key = (
            identity,
            str(kwargs.get("snapshot_id") or ""),
            str(kwargs.get("as_of") or ""),
            bool(kwargs.get("persist")),
        )
        if key not in self.cache:
            self.cache[key] = self.provider.historical_snapshot(candidates, **kwargs)
        return self.cache[key]


def _slice_result(result: dict[str, Any], start_date: datetime, end_date: datetime) -> dict[str, Any]:
    start = _naive_timestamp(start_date)
    end = _naive_timestamp(end_date)
    curve = sorted(
        (
            (_naive_timestamp(item.get("time") or item.get("date")), item)
            for item in (result.get("equityCurve") or [])
            if item.get("time") or item.get("date")
        ),
        key=lambda row: row[0],
    )
    before = [row for row in curve if row[0] < start]
    selected = [row for row in curve if start <= row[0] <= end]
    if not selected:
        return {
            "totalReturn": 0.0,
            "sharpeRatio": 0.0,
            "maxDrawdown": 0.0,
            "winRate": 0.0,
            "totalTrades": 0,
            "totalExecutions": 0,
            "sampleCount": 0,
            "equityCurve": [],
        }

    baseline = float((before[-1][1] if before else selected[0][1]).get("value") or 0.0)
    values = [float(item.get("value") or 0.0) for _, item in selected]
    returns = pd.Series(([baseline] if baseline > 0 else []) + values, dtype="float64").pct_change().dropna()
    frequency = str((result.get("manifest") or {}).get("drivingFrequency") or "1d")
    markets = (result.get("manifest") or {}).get("markets") or []
    volatility = float(returns.std(ddof=0)) if not returns.empty else 0.0
    sharpe = (
        float(returns.mean() / volatility * math.sqrt(periods_per_year(frequency, markets)))
        if volatility > 1e-12
        else 0.0
    )
    peak = baseline if baseline > 0 else values[0]
    worst_drawdown = 0.0
    sliced_curve = []
    for timestamp, item in selected:
        value = float(item.get("value") or 0.0)
        peak = max(peak, value)
        drawdown = (value / peak - 1.0) * 100.0 if peak > 0 else 0.0
        worst_drawdown = min(worst_drawdown, drawdown)
        sliced_curve.append({**item, "time": str(item.get("time") or timestamp.isoformat()), "drawdown": drawdown})

    closed_trades = [
        item
        for item in (result.get("closedTrades") or result.get("trades") or [])
        if _in_range(item.get("exit_time") or item.get("time"), start, end)
    ]
    executions = [
        item
        for item in (result.get("executions") or result.get("rawTrades") or [])
        if _in_range(item.get("time") or item.get("eventTime"), start, end)
    ]
    wins = sum(1 for item in closed_trades if float(item.get("profit") or 0.0) > 0)
    return {
        "totalReturn": (values[-1] / baseline - 1.0) * 100.0 if baseline > 0 else 0.0,
        "sharpeRatio": sharpe,
        "maxDrawdown": worst_drawdown,
        "winRate": wins / len(closed_trades) * 100.0 if closed_trades else 0.0,
        "totalTrades": len(closed_trades),
        "totalExecutions": len(executions),
        "sampleCount": len(sliced_curve),
        "equityCurve": sliced_curve,
        "closedTrades": closed_trades,
        "executions": executions,
    }


def _in_range(value: Any, start: pd.Timestamp, end: pd.Timestamp) -> bool:
    if not value:
        return False
    try:
        timestamp = _naive_timestamp(value)
    except (TypeError, ValueError):
        return False
    return start <= timestamp <= end
