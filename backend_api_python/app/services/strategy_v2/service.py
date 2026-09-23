"""Strategy API V2 orchestration for compilation and backtests."""

from __future__ import annotations

import hashlib
import math
import os
from time import perf_counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any, Callable

import pandas as pd

from app.data_sources.errors import MarketDataUnavailableError
from app.services.backtest.metrics import calculate_information_ratio
from app.services.backtest_limits import (
    BacktestRangeLimitError,
    backtest_warmup_calendar_days,
    validate_backtest_range,
)
from app.services.fundamental_data import get_fundamental_data_service
from app.services.instrument_rules import InstrumentRulesProvider, get_instrument_rules_provider
from app.services.market.instrument_products import PRODUCT_CRYPTO
from app.services.market.product_catalog import get_catalog_product
from app.services.universe import UniverseService, get_universe_service
from app.utils.logger import get_logger

from .contract import StrategyV2ContractError, compile_strategy_v2
from .factor_research import FactorResearchEngine
from .models import InstrumentSpec, StrategyManifest
from .market_data import load_strategy_frame
from .runtime import StrategyV2BacktestRunner
from .readiness import validate_universe_history, validate_warmup, validate_fundamentals
from .snapshot import MarketDataSnapshotStore, canonical_frame_bytes
from .storage import StrategyBacktestRepository


logger = get_logger(__name__)


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
        instrument_rules_provider: InstrumentRulesProvider | None = None,
    ) -> None:
        self.repository = repository or StrategyBacktestRepository()
        self.universe_service = universe_service or get_universe_service()
        self.frame_fetcher = frame_fetcher or load_strategy_frame
        self.fundamental_enricher = fundamental_enricher
        self.data_kind = str(data_kind or "market")
        self.data_source = str(data_source or "system_market_data_router")
        self.snapshot_store = snapshot_store or MarketDataSnapshotStore()
        self.instrument_rules_provider = instrument_rules_provider or get_instrument_rules_provider()

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
        _attach_catalog_products(candidates)
        minimum_symbols = max(3, int(groups or 5))
        if len(candidates) < minimum_symbols:
            raise StrategyV2ContractError(
                f"strategyV2.factorResearchUniverseTooSmall:{minimum_symbols}"
            )
        frequency = manifest.driving_frequency
        warmup_bars = max(40, manifest.warmup_bars)
        fetch_start = start_date - timedelta(days=_warmup_calendar_days(frequency, warmup_bars, candidates))
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
        instrument_rules_snapshot_id: str = "",
    ) -> tuple[int | None, dict[str, Any]]:
        started_at = perf_counter()
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

        instrument_products = _attach_catalog_products(candidates)

        frequency = manifest.driving_frequency
        _validate_product_frequencies(instrument_products, manifest.frequencies)
        fetch_starts = {
            item: start_date
            - timedelta(days=_warmup_calendar_days(item, manifest.warmup_bars, candidates))
            for item in manifest.frequencies
        }
        for item in manifest.frequencies:
            _enforce_backtest_range(
                candidates=candidates,
                timeframe=item,
                start_date=start_date,
                end_date=end_date,
                warmup_bars=manifest.warmup_bars,
                fetch_start=fetch_starts[item],
            )
        data_at = perf_counter()
        frequency_frames, skipped = self.fetch_frequency_frames(
            candidates,
            manifest.frequencies,
            fetch_starts,
            end_date,
        )
        frames = frequency_frames.get(frequency, {})
        if not frames:
            raise StrategyV2ContractError("strategyV2.noMarketData")
        validate_warmup(frequency_frames, manifest.warmup_bars, start_date, candidates)
        if manifest.fundamental_dependencies:
            enricher = self.fundamental_enricher or get_fundamental_data_service().enrich_panel
            frames = enricher(frames, candidates)
            frequency_frames[frequency] = frames
            self.validate_fundamental_dependencies(frames, manifest)

        def resolve_universe(reference: str, timestamp: pd.Timestamp) -> list[str]:
            del reference
            if not universe_id:
                return [item["key"] for item in candidates]
            members = self.universe_service.resolve_members(user_id, universe_id, as_of=timestamp.date())
            return [_member_key(item) for item in members]

        rules_snapshot = None
        if any(str(item.get("market") or "") == "Crypto" for item in candidates):
            rules_snapshot = self.instrument_rules_provider.historical_snapshot(
                candidates,
                snapshot_id=instrument_rules_snapshot_id,
                as_of=_instrument_rules_as_of(frames, end_date),
                persist=self.data_kind == "market" and persist,
            )

        replay_at = perf_counter()
        runner = StrategyV2BacktestRunner(
            code=code,
            frames=frames,
            frequency_frames=frequency_frames,
            initial_capital=initial_capital,
            params=params,
            leverage_enabled=leverage_enabled,
            leverage=leverage,
            commission=commission,
            slippage=slippage,
            universe_resolver=resolve_universe,
            instrument_rules=rules_snapshot,
        )
        result = runner.run(start_date=start_date, end_date=end_date)
        report_at = perf_counter()
        result["reviewCandles"] = _build_review_candle_snapshots(
            frames,
            result.get("closedTrades") or [],
            source_frequency=frequency,
            start_date=start_date,
            end_date=end_date,
        )
        benchmark_spec = _benchmark_for_manifest(manifest)
        benchmark_frame = None
        benchmark_error = ""
        if benchmark_spec is not None:
            benchmark_frame = frames.get(benchmark_spec.key)
            if benchmark_frame is None:
                try:
                    benchmark_frequency = _review_frequency_for_window(
                        frequency,
                        int((end_date - start_date).total_seconds()),
                        max_bars=3000,
                    )[0]
                    benchmark_frame = self.frame_fetcher(
                        benchmark_spec.market,
                        benchmark_spec.symbol,
                        benchmark_frequency,
                        start_date,
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
        result["benchmarkRelativeMetrics"] = calculate_information_ratio(
            result.get("equityCurve") or [],
            result.get("benchmarkCurve") or [],
            benchmark=benchmark_spec.key if benchmark_spec is not None else None,
            frequency=manifest.driving_frequency,
            annualization_factor=float(result.get("periodsPerYear") or 1.0),
        )
        timeframe_provenance = {
            item: [
                _frame_provenance(
                    key,
                    frame,
                    snapshot_store=(
                        self.snapshot_store
                        if self.data_kind == "market" and persist
                        else None
                    ),
                )
                for key, frame in item_frames.items()
            ]
            for item, item_frames in frequency_frames.items()
        }
        result["dataProvenance"] = {
            "kind": self.data_kind,
            "source": self.data_source,
            "requestedStart": start_date.isoformat(),
            "requestedEnd": end_date.isoformat(),
            "frequency": frequency,
            "frequencies": list(manifest.frequencies),
            "symbols": timeframe_provenance.get(frequency, []),
            "timeframes": timeframe_provenance,
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
            "preFillValuationRule": StrategyV2BacktestRunner.PREFILL_VALUATION_POLICY,
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
            "cryptoRebalanceToleranceQuote": 10.0,
            "subLotClosePolicy": "reconcile_up_to_10_quote_with_cash_and_fees",
            "fundingMode": "not_modeled",
            "exchangeEquityProducts": instrument_products,
            "corporateActionsMode": (
                "underlying_us_equity_series"
                if any(
                    str(item.get("product_type") or "").strip().lower() == "direct_equity"
                    for item in instrument_products
                )
                else "venue_price_series"
            ),
            "drivingFrequency": frequency,
            "frequencies": list(manifest.frequencies),
            "higherTimeframePolicy": "completed_before_driving_bar_close",
        }

        persistence_at = perf_counter()
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
        completed_at = perf_counter()
        logger.info("Strategy V2 backtest timing run_id=%s symbols=%s frequency=%s seconds=%s", run_id, len(frames), frequency, {
            "prepare": round(data_at - started_at, 6),
            "market_data_and_rules": round(replay_at - data_at, 6),
            "simulation": round(report_at - replay_at, 6),
            "report_and_snapshots": round(persistence_at - report_at, 6),
            "persistence": round(completed_at - persistence_at, 6),
            "total": round(completed_at - started_at, 6),
        })
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
        validate_universe_history(universe, start_date)
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
    ) -> tuple[dict[str, pd.DataFrame], list[dict[str, Any]]]:
        frames: dict[str, pd.DataFrame] = {}
        skipped: list[dict[str, Any]] = []

        def fetch(member: dict[str, Any]):
            frame = self.frame_fetcher(
                member["market"],
                member["symbol"],
                frequency,
                start_date,
                end_date,
                market_type=member.get("market_type") or "",
                exchange_id=member.get("exchange_id") or "",
                instrument_id=member.get("instrument_id") or "",
                api_family=member.get("api_family") or "",
            )
            return member, frame

        workers = min(8, max(1, len(candidates)))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="strategy-v2-data") as executor:
            futures = {
                executor.submit(fetch, member): member for member in candidates
            }
            for future in as_completed(futures):
                member = futures[future]
                try:
                    member, frame = future.result()
                    if frame is None or frame.empty:
                        skipped.append({"symbol": member.get("key") or "", "reason": "strategyV2.noMarketData"})
                        continue
                    frames[member["key"]] = frame
                except Exception as exc:
                    if isinstance(exc, MarketDataUnavailableError):
                        skipped.append({
                            "symbol": member.get("key") or "",
                            "reason": str(exc),
                            "market_data_error": exc.failure.as_dict(),
                        })
                    else:
                        skipped.append({"symbol": member.get("key") or "", "reason": str(exc)[:240]})
        return dict(sorted(frames.items())), skipped

    def fetch_frequency_frames(
        self,
        candidates: list[dict[str, Any]],
        frequencies: tuple[str, ...],
        start_dates: dict[str, datetime],
        end_date: datetime,
    ) -> tuple[dict[str, dict[str, pd.DataFrame]], list[dict[str, Any]]]:
        """Load every declared timeframe and retain only complete symbol bundles."""
        bundles: dict[str, dict[str, pd.DataFrame]] = {
            frequency: {} for frequency in frequencies
        }
        skipped: list[dict[str, Any]] = []

        def fetch(member: dict[str, Any], frequency: str):
            frame = self.frame_fetcher(
                member["market"],
                member["symbol"],
                frequency,
                start_dates[frequency],
                end_date,
                market_type=member.get("market_type") or "",
                exchange_id=member.get("exchange_id") or "",
                instrument_id=member.get("instrument_id") or "",
                api_family=member.get("api_family") or "",
            )
            return member, frequency, frame

        requests = [
            (member, frequency)
            for frequency in frequencies
            for member in candidates
        ]
        workers = min(8, max(1, len(requests)))
        with ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix="strategy-v2-timeframes",
        ) as executor:
            futures = {
                executor.submit(fetch, member, frequency): (member, frequency)
                for member, frequency in requests
            }
            for future in as_completed(futures):
                member, frequency = futures[future]
                try:
                    _, _, frame = future.result()
                    if frame is None or frame.empty:
                        skipped.append({
                            "symbol": member.get("key") or "",
                            "frequency": frequency,
                            "reason": "strategyV2.noMarketData",
                        })
                        continue
                    bundles[frequency][member["key"]] = frame
                except Exception as exc:
                    item: dict[str, Any] = {
                        "symbol": member.get("key") or "",
                        "frequency": frequency,
                        "reason": str(exc)[:240],
                    }
                    if isinstance(exc, MarketDataUnavailableError):
                        item["market_data_error"] = exc.failure.as_dict()
                    skipped.append(item)

        complete_symbols = set.intersection(
            *(set(frames) for frames in bundles.values())
        ) if bundles else set()
        for frequency, frames in bundles.items():
            bundles[frequency] = {
                key: frames[key]
                for key in sorted(frames)
                if key in complete_symbols
            }
        return bundles, skipped

    @staticmethod
    def validate_fundamental_dependencies(frames: dict[str, pd.DataFrame], manifest: StrategyManifest) -> None:
        required = {_normalize_field(item) for item in manifest.fundamental_dependencies}
        validate_fundamentals(frames, required)


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
        validation_market = (
            str(candidate.get("underlying_market") or "USStock")
            if str(candidate.get("product_type") or "").strip().lower() == "direct_equity"
            else market
        )
        if validation_market in checked_markets:
            continue
        checked_markets.add(validation_market)
        error = validate_backtest_range(
            market=validation_market,
            symbol=str(candidate.get("underlying_symbol") or candidate.get("symbol") or ""),
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


def _instrument_rules_as_of(
    frames: dict[str, pd.DataFrame],
    requested_end: datetime,
) -> datetime:
    """Use the last common market-data timestamp for historical rule lookup."""
    timestamps = []
    for frame in frames.values():
        if frame is None or frame.empty:
            continue
        timestamp = pd.Timestamp(frame.index.max())
        if timestamp.tzinfo is not None:
            timestamp = timestamp.tz_convert("UTC").tz_localize(None)
        timestamps.append(timestamp)
    if not timestamps:
        return requested_end

    end_timestamp = pd.Timestamp(requested_end)
    if end_timestamp.tzinfo is not None:
        end_timestamp = end_timestamp.tz_convert("UTC").tz_localize(None)
    return min(end_timestamp, *timestamps).to_pydatetime()


def _attach_catalog_products(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach exact venue product metadata used by data routing and reporting."""
    products: list[dict[str, Any]] = []
    for member in candidates:
        if (
            str(member.get("market") or "").strip() != "Crypto"
            or not str(member.get("exchange_id") or "").strip()
        ):
            continue
        try:
            product = get_catalog_product(
                market="Crypto",
                symbol=str(member.get("symbol") or ""),
                exchange_id=str(member.get("exchange_id") or ""),
                market_type=str(member.get("market_type") or "spot"),
            )
        except Exception as exc:
            logger.debug("Product catalog lookup unavailable during backtest preparation: %s", exc)
            continue
        if not product:
            continue
        member["instrument_id"] = str(product.get("instrument_id") or member.get("instrument_id") or "")
        member["product_type"] = str(product.get("product_type") or PRODUCT_CRYPTO)
        member["api_family"] = str(product.get("api_family") or member.get("market_type") or "spot")
        member["underlying_market"] = str(product.get("underlying_market") or "")
        member["underlying_symbol"] = str(product.get("underlying_symbol") or "")
        if member["product_type"] != PRODUCT_CRYPTO:
            products.append({
                "symbol": str(member.get("symbol") or ""),
                "exchange_id": str(member.get("exchange_id") or ""),
                "market_type": str(member.get("market_type") or "spot"),
                "instrument_id": member["instrument_id"],
                "product_type": member["product_type"],
                "api_family": member["api_family"],
                "underlying_market": str(product.get("underlying_market") or ""),
                "underlying_symbol": str(product.get("underlying_symbol") or ""),
            })
    return products


def _validate_product_frequencies(
    products: list[dict[str, Any]],
    frequencies: tuple[str, ...],
) -> None:
    if not any(str(item.get("api_family") or "").lower() == "reality" for item in products):
        return
    allowed = {"1m", "5m", "15m", "1h", "4h", "1d"}
    unsupported = sorted({str(value or "").strip().lower() for value in frequencies} - allowed)
    if unsupported:
        raise StrategyV2ContractError(
            f"strategyV2.bitgetRealityTimeframeUnsupported:{','.join(unsupported)}"
        )


def _validate_warmup_history(frequency_frames, warmup_bars: int, start_date: datetime) -> None:
    validate_warmup(frequency_frames, warmup_bars, start_date)


def _warmup_calendar_days(frequency: str, warmup_bars: int, candidates=()) -> int:
    """Keep the legacy helper available for internal callers and tests."""
    days = backtest_warmup_calendar_days(frequency, warmup_bars)
    normalized = str(frequency).lower()
    if warmup_bars > 0 and normalized.endswith(("m", "h")) and any(
        item.get("market") in {"USStock", "HKStock", "AStock"}
        or str(item.get("underlying_market") or "") in {"USStock", "HKStock", "AStock"}
        or str(item.get("product_type") or "").strip().lower() == "direct_equity"
        for item in candidates
    ):
        hours = float(normalized[:-1]) / (60 if normalized.endswith("m") else 1)
        # Four trading hours per session also covers the shortest stock market
        # in a mixed universe; allow weekends and a holiday safety margin.
        days = max(days, 7, math.ceil(warmup_bars * hours / 4 * 7 / 5 * 1.5))
    return days


def _benchmark_for_manifest(manifest: StrategyManifest) -> InstrumentSpec | None:
    if manifest.benchmark is not None:
        return manifest.benchmark

    instruments: list[InstrumentSpec] = list(manifest.universe.instruments)
    instrument_keys = {item.key for item in instruments}
    for subscription in manifest.subscriptions:
        for instrument in subscription.instruments:
            if instrument.key not in instrument_keys:
                instruments.append(instrument)
                instrument_keys.add(instrument.key)

    if manifest.strategy_type == "portfolio":
        markets = {item.market for item in instruments}
        # Only infer a benchmark when the market mapping is unambiguous.  A
        # silent SPY fallback for crypto, HK or mixed portfolios produces a
        # plausible-looking but semantically wrong excess-return series.
        if markets == {"USStock"}:
            return InstrumentSpec(market="USStock", symbol="SPY", market_type="spot")
        if markets == {"Crypto"}:
            first = instruments[0] if instruments else None
            return InstrumentSpec(
                market="Crypto",
                symbol="BTC/USDT",
                exchange_id=first.exchange_id if first else "",
                market_type="spot",
            )
        return None

    if not instruments:
        return None
    instrument = instruments[0]
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
    coverage_start = pd.Timestamp(close.index.min())
    last_bar_open = pd.Timestamp(close.index.max())
    # Market-data indexes represent the *opening* instant of each bar.  Treating
    # the final open as the coverage boundary incorrectly marks a complete 15m
    # benchmark as partial when the strategy equity curve contains observations
    # from the remaining minutes of that same bar.  Extend coverage by exactly
    # one inferred bar -- never farther -- so stale prices still cannot leak into
    # later missing periods.
    unique_index = pd.DatetimeIndex(close.index).drop_duplicates().sort_values()
    deltas = unique_index.to_series().diff().dropna()
    positive_deltas = deltas[deltas > pd.Timedelta(0)]
    bar_interval = positive_deltas.median() if not positive_deltas.empty else pd.Timedelta(0)
    coverage_end = (
        last_bar_open + bar_interval - pd.Timedelta(microseconds=1)
        if bar_interval > pd.Timedelta(0)
        else last_bar_open
    )
    covered_timestamps = timestamps[(timestamps >= coverage_start) & (timestamps <= coverage_end)]
    aligned = close.reindex(close.index.union(covered_timestamps)).sort_index().ffill().reindex(covered_timestamps)
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
    coverage_ratio = float(len(aligned)) / float(len(timestamps)) if len(timestamps) else 0.0
    status = "available" if len(aligned) == len(timestamps) else "partial"
    return {
        "benchmark": metadata,
        "benchmarkStatus": status,
        "benchmarkError": "" if status == "available" else "strategyV2.benchmarkPartialCoverage",
        "benchmarkCurve": curve,
        "benchmarkTotalReturn": total_return,
        "benchmarkCoverageStart": coverage_start.tz_localize("UTC").isoformat().replace("+00:00", "Z"),
        "benchmarkCoverageEnd": coverage_end.tz_localize("UTC").isoformat().replace("+00:00", "Z"),
        "benchmarkCoverageRatio": coverage_ratio,
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


_REVIEW_FREQUENCIES: tuple[tuple[str, str, int], ...] = (
    ("1m", "1min", 60),
    ("5m", "5min", 300),
    ("15m", "15min", 900),
    ("30m", "30min", 1800),
    ("1H", "1h", 3600),
    ("4H", "4h", 14400),
    ("1D", "1D", 86400),
    ("1W", "1W", 604800),
)


def _build_review_candle_snapshots(
    frames: dict[str, pd.DataFrame],
    trades: list[dict[str, Any]],
    *,
    source_frequency: str,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    max_bars: int = 3000,
    max_symbols: int = 12,
) -> dict[str, dict[str, Any]]:
    """Persist bounded OHLCV-only snapshots for fast, reproducible trade review."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for trade in trades or []:
        symbol = _review_instrument_key((trade or {}).get("symbol"))
        if symbol and symbol in frames:
            grouped.setdefault(symbol, []).append(trade)
    snapshots: dict[str, dict[str, Any]] = {}
    for symbol in list(grouped)[:max(1, int(max_symbols or 1))]:
        frame = frames.get(symbol)
        if frame is None or frame.empty:
            continue
        timestamps = [
            pd.to_datetime(item.get(field), errors="coerce", utc=True)
            for item in grouped[symbol]
            for field in ("entry_time", "exit_time")
        ]
        timestamps = [item for item in timestamps if not pd.isna(item)]
        if not timestamps:
            continue
        trade_entry = min(timestamps).tz_convert(None)
        trade_exit = max(timestamps).tz_convert(None)
        requested_start = (
            pd.to_datetime(start_date, errors="coerce", utc=True).tz_convert(None)
            if start_date is not None
            else pd.NaT
        )
        requested_end = (
            pd.to_datetime(end_date, errors="coerce", utc=True).tz_convert(None)
            if end_date is not None
            else pd.NaT
        )
        entry = requested_start if not pd.isna(requested_start) else trade_entry
        exit_at = requested_end if not pd.isna(requested_end) else trade_exit
        source_seconds = _review_frequency_seconds(source_frequency)
        span_seconds = max(source_seconds, int((exit_at - entry).total_seconds()))
        if not pd.isna(requested_start) and not pd.isna(requested_end):
            window_start = entry
            window_end = exit_at
        else:
            padding_seconds = max(source_seconds * 8, int(span_seconds * 0.05))
            window_start = entry - pd.Timedelta(seconds=padding_seconds)
            window_end = exit_at + pd.Timedelta(seconds=padding_seconds)
        timeframe, rule, _ = _review_frequency_for_window(
            source_frequency,
            int((window_end - window_start).total_seconds()),
            max_bars=max_bars,
        )
        candle_frame = _resample_review_frame(frame, rule, window_start, window_end)
        if candle_frame.empty:
            continue
        if len(candle_frame.index) > max_bars:
            candle_frame = candle_frame.iloc[-max_bars:]
        snapshots[symbol] = {
            "timeframe": timeframe,
            "candles": [
                {
                    "time": int(pd.Timestamp(index).tz_localize("UTC").timestamp()),
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row["volume"]),
                }
                for index, row in candle_frame.iterrows()
            ],
        }
    return snapshots


def _review_instrument_key(value: object) -> str:
    """Map a hedged position key back to the market-data instrument key."""
    symbol = str(value or "").strip()
    base, separator, suffix = symbol.rpartition("::")
    if separator and suffix.strip().lower() in {"long", "short"}:
        return base
    return symbol


def _review_frequency_seconds(value: str) -> int:
    normalized = str(value or "").strip().lower()
    return next((seconds for key, _, seconds in _REVIEW_FREQUENCIES if key.lower() == normalized), 86400)


def _review_frequency_for_window(
    source_frequency: str,
    span_seconds: int,
    *,
    max_bars: int,
) -> tuple[str, str, int]:
    source_seconds = _review_frequency_seconds(source_frequency)
    choices = [item for item in _REVIEW_FREQUENCIES if item[2] >= source_seconds]
    for item in choices:
        if span_seconds // item[2] + 2 <= max(1, max_bars):
            return item
    return choices[-1] if choices else _REVIEW_FREQUENCIES[-1]


def _resample_review_frame(
    frame: pd.DataFrame,
    rule: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DataFrame:
    columns = {str(column).lower(): column for column in frame.columns}
    required = {name: columns.get(name) for name in ("open", "high", "low", "close", "volume")}
    if any(value is None for value in required.values()):
        return pd.DataFrame()
    source = frame[[required[name] for name in ("open", "high", "low", "close", "volume")]].copy()
    source.columns = ["open", "high", "low", "close", "volume"]
    index = pd.DatetimeIndex(pd.to_datetime(source.index, errors="coerce", utc=True)).tz_convert(None)
    source.index = index
    source = source[~source.index.isna()].sort_index()
    source = source.loc[(source.index >= start) & (source.index <= end)]
    if source.empty:
        return source
    for column in source.columns:
        source[column] = pd.to_numeric(source[column], errors="coerce")
    return source.resample(rule, label="right", closed="right").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna(subset=["open", "high", "low", "close"])


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
