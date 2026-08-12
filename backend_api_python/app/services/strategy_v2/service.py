"""Strategy API V2 orchestration for compilation and backtests."""

from __future__ import annotations

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

import pandas as pd

from app.services.backtest_limits import (
    BacktestRangeLimitError,
    backtest_warmup_calendar_days,
    validate_backtest_range,
)
from app.services.fundamental_data import get_fundamental_data_service
from app.services.universe import UniverseService, get_universe_service

from .contract import StrategyV2ContractError, compile_strategy_v2
from .factor_research import FactorResearchEngine
from .models import InstrumentSpec, StrategyManifest
from .market_data import load_strategy_frame
from .runtime import StrategyV2BacktestRunner
from .snapshot import MarketDataSnapshotStore, canonical_frame_bytes
from .storage import StrategyBacktestRepository


class StrategyV2BacktestService:
    def __init__(
        self,
        *,
        repository: StrategyBacktestRepository | None = None,
        universe_service: UniverseService | None = None,
        frame_fetcher: Callable[..., pd.DataFrame] | None = None,
        fundamental_enricher: Callable[[dict[str, pd.DataFrame], list[dict[str, Any]]], dict[str, pd.DataFrame]] | None = None,
        data_kind: str = "market",
        data_source: str = "system_market_data_router",
        snapshot_store: MarketDataSnapshotStore | None = None,
    ) -> None:
        self.repository = repository or StrategyBacktestRepository()
        self.universe_service = universe_service or get_universe_service()
        self.frame_fetcher = frame_fetcher or load_strategy_frame
        self.fundamental_enricher = fundamental_enricher
        self.data_kind = str(data_kind or "market")
        self.data_source = str(data_source or "system_market_data_router")
        self.snapshot_store = snapshot_store or MarketDataSnapshotStore()

    def compile(self, code: str) -> dict[str, Any]:
        return compile_strategy_v2(code).manifest.metadata()

    def research_factor(
        self,
        *,
        user_id: int,
        code: str,
        start_date: datetime,
        end_date: datetime,
        factor_id: str,
        groups: int = 5,
        holding_period: int = 5,
        commission: float = 0.0005,
        slippage: float = 0.0005,
        neutralize_industry: bool = False,
    ) -> dict[str, Any]:
        program = compile_strategy_v2(code)
        manifest = program.manifest
        if manifest.strategy_type != "portfolio":
            raise StrategyV2ContractError("strategyV2.factorResearchPortfolioOnly")
        candidates, universe_id = self.resolve_candidates(
            user_id=user_id,
            manifest=manifest,
            start_date=start_date,
            end_date=end_date,
        )
        minimum_symbols = max(3, int(groups or 5))
        if len(candidates) < minimum_symbols:
            raise StrategyV2ContractError(
                f"strategyV2.factorResearchUniverseTooSmall:{minimum_symbols}"
            )
        frequency = manifest.primary_frequency
        warmup_bars = max(40, manifest.warmup_bars)
        fetch_start = start_date - timedelta(days=backtest_warmup_calendar_days(frequency, warmup_bars))
        _enforce_backtest_range(
            candidates=candidates,
            timeframe=frequency,
            start_date=start_date,
            end_date=end_date,
            warmup_bars=warmup_bars,
            fetch_start=fetch_start,
        )
        frames, skipped = self.fetch_frames(candidates, frequency, fetch_start, end_date)
        if not frames:
            raise StrategyV2ContractError("strategyV2.noMarketData")
        if len(frames) < minimum_symbols:
            raise StrategyV2ContractError(
                f"strategyV2.factorResearchUsableUniverseTooSmall:{minimum_symbols}"
            )
        if manifest.fundamental_dependencies:
            enricher = self.fundamental_enricher or get_fundamental_data_service().enrich_panel
            frames = enricher(frames, candidates)
        result = FactorResearchEngine().run(
            frames=frames,
            factor_id=factor_id,
            start_date=start_date,
            end_date=end_date,
            groups=groups,
            holding_period=holding_period,
            commission=commission,
            slippage=slippage,
            neutralize_industry=neutralize_industry,
        )
        result.update({
            "manifest": manifest.metadata(),
            "universeId": universe_id,
            "symbolsRequested": len(candidates),
            "symbolsUsed": len(frames),
            "symbolsSkipped": skipped,
        })
        return result

    def run(
        self,
        *,
        user_id: int,
        code: str,
        start_date: datetime,
        end_date: datetime,
        initial_capital: float,
        leverage_enabled: bool = False,
        leverage: float = 1.0,
        commission: float = 0.0005,
        slippage: float = 0.0005,
        params: dict[str, Any] | None = None,
        persist: bool = True,
        strategy_id: int | None = None,
        source_id: int | None = None,
        strategy_name: str = "",
    ) -> tuple[int | None, dict[str, Any]]:
        program = compile_strategy_v2(code)
        manifest = program.manifest
        if end_date <= start_date:
            raise StrategyV2ContractError("strategyV2.invalidDateRange")
        if initial_capital <= 0:
            raise StrategyV2ContractError("strategyV2.invalidInitialCapital")

        candidates, universe_id = self.resolve_candidates(
            user_id=user_id,
            manifest=manifest,
            start_date=start_date,
            end_date=end_date,
        )
        if not candidates:
            raise StrategyV2ContractError("strategyV2.universeHasNoData")

        frequency = manifest.primary_frequency
        fetch_start = start_date - timedelta(
            days=backtest_warmup_calendar_days(frequency, manifest.warmup_bars)
        )
        _enforce_backtest_range(
            candidates=candidates,
            timeframe=frequency,
            start_date=start_date,
            end_date=end_date,
            warmup_bars=manifest.warmup_bars,
            fetch_start=fetch_start,
        )
        frames, skipped = self.fetch_frames(candidates, frequency, fetch_start, end_date)
        if not frames:
            raise StrategyV2ContractError("strategyV2.noMarketData")
        if manifest.fundamental_dependencies:
            enricher = self.fundamental_enricher or get_fundamental_data_service().enrich_panel
            frames = enricher(frames, candidates)
            self.validate_fundamental_dependencies(frames, manifest)

        def resolve_universe(reference: str, timestamp: pd.Timestamp) -> list[str]:
            del reference
            if not universe_id:
                return [item["key"] for item in candidates]
            members = self.universe_service.resolve_members(user_id, universe_id, as_of=timestamp.date())
            return [_member_key(item) for item in members]

        runner = StrategyV2BacktestRunner(
            code=code,
            frames=frames,
            initial_capital=initial_capital,
            params=params,
            leverage_enabled=leverage_enabled,
            leverage=leverage,
            commission=commission,
            slippage=slippage,
            universe_resolver=resolve_universe,
        )
        result = runner.run(start_date=start_date, end_date=end_date)
        benchmark_spec = _benchmark_for_manifest(manifest)
        benchmark_frame = None
        benchmark_error = ""
        if benchmark_spec is not None:
            benchmark_frame = frames.get(benchmark_spec.key)
            if benchmark_frame is None:
                try:
                    benchmark_frame = self.frame_fetcher(
                        benchmark_spec.market,
                        benchmark_spec.symbol,
                        frequency,
                        fetch_start,
                        end_date,
                        market_type=benchmark_spec.market_type,
                        exchange_id=benchmark_spec.exchange_id,
                    )
                except Exception as exc:
                    benchmark_error = str(exc)[:240]
        benchmark = _build_benchmark_result(
            benchmark_spec,
            benchmark_frame,
            result.get("equityCurve") or [],
            initial_capital,
            error=benchmark_error,
        )
        result.update(benchmark)
        result["excessReturn"] = float(result.get("totalReturn") or 0.0) - float(result.get("benchmarkTotalReturn") or 0.0)
        result["dataProvenance"] = {
            "kind": self.data_kind,
            "source": self.data_source,
            "requestedStart": start_date.isoformat(),
            "requestedEnd": end_date.isoformat(),
            "frequency": frequency,
            "symbols": [
                _frame_provenance(
                    key,
                    frame,
                    snapshot_store=self.snapshot_store if self.data_kind == "market" and persist else None,
                )
                for key, frame in frames.items()
            ],
            "benchmark": _frame_provenance(
                benchmark_spec.key,
                benchmark_frame,
                snapshot_store=self.snapshot_store if self.data_kind == "market" and persist else None,
            )
            if benchmark_spec is not None and benchmark_frame is not None and not benchmark_frame.empty
            else None,
        }
        execution_count = int(result.get("totalExecutions") or 0)
        closed_count = int(result.get("totalTrades") or 0)
        result["resultStatus"] = (
            "no_signals"
            if execution_count == 0
            else "open_position_only"
            if closed_count == 0
            else "completed_trades"
        )
        result["diagnostics"] = {
            **(result.get("diagnostics") or {}),
            "symbolsRequested": len(candidates),
            "symbolsUsed": len(frames),
            "symbolsSkipped": skipped,
            "universeId": universe_id,
            "sourceControlled": True,
        }
        result["executionAssumptions"] = {
            "engineVersion": StrategyV2BacktestRunner.VERSION,
            "fillRule": "scheduled_current_open_or_signal_next_open",
            "protectionRule": "gap_open_then_intrabar_trigger",
            "intrabarMode": "conservative",
            "barClosePolicy": "closed_bars_only",
            "initialCapital": initial_capital,
            "startDate": start_date.date().isoformat(),
            "endDate": end_date.date().isoformat(),
            "leverageEnabled": bool(leverage_enabled),
            "leverage": float(leverage if leverage_enabled else 1.0),
            "commission": float(commission),
            "slippage": float(slippage),
            "fundingMode": "not_modeled",
        }

        run_id = None
        if persist:
            if self.data_kind != "market":
                raise StrategyV2ContractError("strategyV2.fixturePersistenceForbidden")
            run_id = self.repository.persist_run(
                user_id=user_id,
                strategy_id=strategy_id,
                strategy_name=strategy_name,
                source_id=source_id,
                market=",".join(manifest.markets) or "Mixed",
                symbol=_manifest_symbol(manifest),
                timeframe=frequency,
                start_date=start_date.date().isoformat(),
                end_date=end_date.date().isoformat(),
                initial_capital=initial_capital,
                commission=commission,
                slippage=slippage,
                leverage=float(leverage if leverage_enabled else 1),
                manifest=manifest.metadata(),
                params=dict(params or {}),
                result=result,
                code=code,
            )
        return run_id, result

    def resolve_candidates(
        self,
        *,
        user_id: int,
        manifest: StrategyManifest,
        start_date: datetime,
        end_date: datetime,
    ) -> tuple[list[dict[str, Any]], int | None]:
        if manifest.universe.kind == "static":
            return [_instrument_member(item) for item in manifest.universe.instruments], None

        reference = manifest.universe.reference
        universe = next((item for item in self.universe_service.list_universes(user_id) if _universe_matches(item, reference)), None)
        if not universe:
            raise StrategyV2ContractError(f"strategyV2.universeNotFound:{reference}")
        universe_id = int(universe.get("id") or 0)
        members = self.universe_service.candidate_members(
            user_id,
            universe_id,
            start=start_date.date(),
            end=end_date.date(),
        )
        limit = max(1, int(os.getenv("STRATEGY_V2_MAX_SYMBOLS", "600") or 600))
        if len(members) > limit:
            raise StrategyV2ContractError("strategyV2.universeTooLarge")
        return [{**item, "key": _member_key(item)} for item in members], universe_id

    def fetch_frames(
        self,
        candidates: list[dict[str, Any]],
        frequency: str,
        start_date: datetime,
        end_date: datetime,
        *,
        exchange_config: Optional[dict[str, Any]] = None,
        strict_data_source: bool = False,
    ) -> tuple[dict[str, pd.DataFrame], list[dict[str, str]]]:
        frames: dict[str, pd.DataFrame] = {}
        skipped: list[dict[str, str]] = []

        def fetch(member: dict[str, Any]):
            kwargs: dict[str, Any] = {
                "market_type": member.get("market_type") or "",
                "exchange_id": member.get("exchange_id") or "",
            }
            if exchange_config is not None:
                kwargs["exchange_config"] = exchange_config
            if strict_data_source:
                kwargs["strict_data_source"] = True
            frame = self.frame_fetcher(
                member["market"],
                member["symbol"],
                frequency,
                start_date,
                end_date,
                **kwargs,
            )
            return member, frame

        workers = min(8, max(1, len(candidates)))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="strategy-v2-data") as executor:
            futures = [executor.submit(fetch, member) for member in candidates]
            for future in as_completed(futures):
                try:
                    member, frame = future.result()
                    if frame is None or frame.empty:
                        skipped.append({"symbol": member.get("key") or "", "reason": "strategyV2.noMarketData"})
                        continue
                    frames[member["key"]] = frame
                except Exception as exc:
                    if strict_data_source:
                        raise RuntimeError(
                            f"strategyV2.executionMarketDataUnavailable:{exc}"
                        ) from exc
                    skipped.append({"symbol": "", "reason": str(exc)[:240]})
        return dict(sorted(frames.items())), skipped

    @staticmethod
    def validate_fundamental_dependencies(frames: dict[str, pd.DataFrame], manifest: StrategyManifest) -> None:
        required = {_normalize_field(item) for item in manifest.fundamental_dependencies}
        available = set()
        for frame in frames.values():
            available.update(str(column).strip().lower() for column in frame.columns)
        missing = sorted(required - available)
        if missing:
            raise StrategyV2ContractError(f"strategyV2.fundamentalDataMissing:{','.join(missing)}")


def _enforce_backtest_range(
    *,
    candidates: list[dict[str, Any]],
    timeframe: str,
    start_date: datetime,
    end_date: datetime,
    warmup_bars: int,
    fetch_start: datetime,
) -> None:
    errors: list[dict[str, Any]] = []
    checked_markets: set[str] = set()
    for candidate in candidates:
        market = str(candidate.get("market") or "")
        if market in checked_markets:
            continue
        checked_markets.add(market)
        error = validate_backtest_range(
            market=market,
            symbol=str(candidate.get("symbol") or ""),
            timeframe=timeframe,
            start_date=start_date,
            end_date=end_date,
            warmup_bars=warmup_bars,
            fetch_start=fetch_start,
        )
        if error:
            errors.append(error)
    if errors:
        raise BacktestRangeLimitError(min(errors, key=lambda item: int(item["max_days"])))


def _instrument_member(item: InstrumentSpec) -> dict[str, Any]:
    return {
        "key": item.key,
        "market": item.market,
        "symbol": item.symbol,
        "exchange_id": item.exchange_id,
        "market_type": item.market_type,
        "instrument_id": item.instrument_id,
    }


def _warmup_calendar_days(frequency: str, warmup_bars: int) -> int:
    """Keep the legacy helper available for internal callers and tests."""
    return backtest_warmup_calendar_days(frequency, warmup_bars)


def _benchmark_for_manifest(manifest: StrategyManifest) -> InstrumentSpec | None:
    if manifest.benchmark is not None:
        return manifest.benchmark
    if manifest.strategy_type == "portfolio" or manifest.universe.kind != "static":
        return InstrumentSpec(market="USStock", symbol="SPY", market_type="spot")
    if not manifest.universe.instruments:
        return None
    instrument = manifest.universe.instruments[0]
    if instrument.market == "Crypto" and instrument.market_type == "swap":
        return InstrumentSpec(
            market="Crypto",
            symbol=instrument.symbol,
            exchange_id=instrument.exchange_id,
            market_type="spot",
        )
    return instrument


def _build_benchmark_result(
    instrument: InstrumentSpec | None,
    frame: pd.DataFrame | None,
    equity_curve: list[dict[str, Any]],
    initial_capital: float,
    *,
    error: str = "",
) -> dict[str, Any]:
    metadata = instrument.metadata() if instrument is not None else None
    if instrument is None or frame is None or frame.empty or not equity_curve:
        return {
            "benchmark": metadata,
            "benchmarkStatus": "unavailable",
            "benchmarkError": error or "strategyV2.benchmarkDataUnavailable",
            "benchmarkCurve": [],
            "benchmarkTotalReturn": 0.0,
        }
    close = pd.to_numeric(frame["close"], errors="coerce").dropna().sort_index()
    if close.empty:
        return {
            "benchmark": metadata,
            "benchmarkStatus": "unavailable",
            "benchmarkError": "strategyV2.benchmarkDataUnavailable",
            "benchmarkCurve": [],
            "benchmarkTotalReturn": 0.0,
        }
    # Equity payloads are UTC ISO instants. Keep the internal alignment index
    # UTC-naive because market frames are normalized to UTC-naive indexes.
    timestamps = pd.DatetimeIndex(pd.to_datetime(
        [item["time"] for item in equity_curve],
        errors="coerce",
        utc=True,
    )).tz_convert(None)
    aligned = close.reindex(close.index.union(timestamps)).sort_index().ffill().reindex(timestamps)
    aligned = aligned.dropna()
    if aligned.empty or float(aligned.iloc[0]) <= 0:
        return {
            "benchmark": metadata,
            "benchmarkStatus": "unavailable",
            "benchmarkError": "strategyV2.benchmarkAlignmentUnavailable",
            "benchmarkCurve": [],
            "benchmarkTotalReturn": 0.0,
        }
    base = float(aligned.iloc[0])
    curve = [
        {
            "time": pd.Timestamp(timestamp).tz_localize("UTC").isoformat().replace("+00:00", "Z"),
            "value": round(float(initial_capital) * float(value) / base, 8),
        }
        for timestamp, value in aligned.items()
    ]
    total_return = (float(curve[-1]["value"]) / float(initial_capital) - 1.0) * 100.0
    return {
        "benchmark": metadata,
        "benchmarkStatus": "available",
        "benchmarkError": "",
        "benchmarkCurve": curve,
        "benchmarkTotalReturn": total_return,
    }


def _frame_provenance(
    key: str,
    frame: pd.DataFrame,
    *,
    snapshot_store: MarketDataSnapshotStore | None = None,
) -> dict[str, Any]:
    fingerprint = hashlib.sha256(canonical_frame_bytes(frame)).hexdigest()
    snapshot = snapshot_store.save(frame) if snapshot_store is not None else {}
    return {
        "instrument": key,
        "bars": int(len(frame.index)),
        "firstBar": str(pd.Timestamp(frame.index.min())) if not frame.empty else "",
        "lastBar": str(pd.Timestamp(frame.index.max())) if not frame.empty else "",
        "contentHash": fingerprint,
        **snapshot,
    }


def _member_key(member: dict[str, Any]) -> str:
    item = InstrumentSpec(
        market=str(member.get("market") or ""),
        symbol=str(member.get("symbol") or ""),
        exchange_id=str(member.get("exchange_id") or ""),
        market_type=str(member.get("market_type") or ""),
        instrument_id=str(member.get("instrument_id") or ""),
    )
    return item.key


def _universe_matches(item: dict[str, Any], reference: str) -> bool:
    ref = str(reference or "").strip().upper()
    if ref.startswith("POOL:"):
        ref = ref.split(":", 1)[1]
    source_ref = str(item.get("source_ref") or "").strip().upper()
    code = str(item.get("code") or "").strip().upper()
    if ref == source_ref or ref == code:
        return True
    symbol = ref.split(":", 1)[-1]
    aliases = {
        "000300.SH": "CSI300",
        "000905.SH": "CSI500",
        "SPX": "SP500",
        "NDX": "NASDAQ100",
    }
    return source_ref == symbol or code == symbol or code == aliases.get(symbol, "")


def _normalize_field(value: str) -> str:
    aliases = {
        "PE": "pe_ratio",
        "PB": "pb_ratio",
        "ROE": "return_on_equity",
        "MARKET_CAP": "market_cap",
        "REVENUE_GROWTH": "revenue_growth",
        "DEBT_TO_EQUITY": "debt_to_equity",
        "FREE_CASH_FLOW": "free_cash_flow",
    }
    raw = str(value or "").strip().upper()
    return aliases.get(raw, raw.lower())


def _manifest_symbol(manifest: StrategyManifest) -> str:
    if manifest.universe.reference:
        return f"universe:{manifest.universe.reference}"
    values = [item.symbol for item in manifest.universe.instruments]
    return values[0] if len(values) == 1 else f"basket:{len(values)}"
