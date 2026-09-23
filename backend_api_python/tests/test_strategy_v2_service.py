from datetime import datetime

import pandas as pd
import pytest

from app.services.strategy_v2 import (
    InstrumentSpec,
    StrategyManifest,
    StrategyV2ContractError,
    SubscriptionSpec,
    UniverseSpec,
)
from app.services.strategy_v2.service import (
    StrategyV2BacktestService,
    _benchmark_for_manifest,
    _build_benchmark_result,
    _build_review_candle_snapshots,
    _instrument_rules_as_of,
    _review_frequency_for_window,
    _universe_matches,
    _warmup_calendar_days,
)
from app.services.strategy_v2.snapshot import MarketDataSnapshotStore


class _Repository:
    def persist_run(self, **kwargs):
        self.persisted = kwargs
        return 81


def test_warmup_days_follow_strategy_frequency():
    assert _warmup_calendar_days("1m", 2) == 1
    assert _warmup_calendar_days("4h", 120) == 30
    assert _warmup_calendar_days("1d", 10) == 19
    assert _warmup_calendar_days("1w", 10) == 80


def test_instrument_rules_use_last_common_market_timestamp():
    frames = {
        "Crypto:BTC/USDT@swap": pd.DataFrame(
            {"close": [100.0, 101.0]},
            index=pd.DatetimeIndex(["2026-09-16 16:08", "2026-09-16 16:09"]),
        ),
        "Crypto:ETH/USDT@swap": pd.DataFrame(
            {"close": [50.0, 51.0]},
            index=pd.DatetimeIndex(["2026-09-16 16:07", "2026-09-16 16:08"]),
        ),
    }

    resolved = _instrument_rules_as_of(
        frames,
        datetime(2026, 9, 16, 23, 59, 59),
    )

    assert resolved == datetime(2026, 9, 16, 16, 8)


def test_benchmark_alignment_does_not_extend_stale_prices_past_real_coverage():
    benchmark_index = pd.date_range("2026-08-23", periods=3, freq="D")
    benchmark_frame = pd.DataFrame({"close": [100.0, 101.0, 102.0]}, index=benchmark_index)
    equity_curve = [
        {"time": f"2026-08-{day:02d}T00:00:00Z", "value": 10000.0}
        for day in range(23, 28)
    ]

    result = _build_benchmark_result(
        InstrumentSpec(market="Crypto", symbol="ETH/USDT", market_type="spot"),
        benchmark_frame,
        equity_curve,
        10000.0,
    )

    assert result["benchmarkStatus"] == "partial"
    assert len(result["benchmarkCurve"]) == 3
    assert result["benchmarkCurve"][-1]["time"] == "2026-08-25T00:00:00Z"
    assert result["benchmarkCurve"][-1]["value"] == pytest.approx(10200.0)
    assert result["benchmarkCoverageRatio"] == pytest.approx(0.6)


def _portfolio_manifest(*instruments: InstrumentSpec, benchmark=None) -> StrategyManifest:
    return StrategyManifest(
        api_version=2,
        code_hash="test",
        strategy_type="portfolio",
        universe=UniverseSpec(kind="static", instruments=tuple(instruments)),
        subscriptions=(SubscriptionSpec(instruments=tuple(instruments), frequency="1d"),),
        schedules=(),
        benchmark=benchmark,
    )


def test_portfolio_benchmark_inference_is_market_aware():
    us = _benchmark_for_manifest(_portfolio_manifest(
        InstrumentSpec(market="USStock", symbol="AAPL", market_type="spot"),
        InstrumentSpec(market="USStock", symbol="MSFT", market_type="spot"),
    ))
    crypto = _benchmark_for_manifest(_portfolio_manifest(
        InstrumentSpec(market="Crypto", symbol="ETH/USDT", exchange_id="okx", market_type="swap"),
        InstrumentSpec(market="Crypto", symbol="SOL/USDT", exchange_id="okx", market_type="swap"),
    ))
    mixed = _benchmark_for_manifest(_portfolio_manifest(
        InstrumentSpec(market="USStock", symbol="AAPL", market_type="spot"),
        InstrumentSpec(market="Crypto", symbol="BTC/USDT", market_type="spot"),
    ))

    assert us == InstrumentSpec(market="USStock", symbol="SPY", market_type="spot")
    assert crypto == InstrumentSpec(
        market="Crypto", symbol="BTC/USDT", exchange_id="okx", market_type="spot"
    )
    assert mixed is None


def test_explicit_portfolio_benchmark_is_never_overridden():
    explicit = InstrumentSpec(market="USStock", symbol="QQQ", market_type="spot")
    manifest = _portfolio_manifest(
        InstrumentSpec(market="USStock", symbol="AAPL", market_type="spot"),
        benchmark=explicit,
    )

    assert _benchmark_for_manifest(manifest) == explicit


def test_benchmark_alignment_covers_the_full_final_intraday_bar():
    benchmark_index = pd.date_range("2026-08-29T23:00:00", periods=4, freq="15min")
    benchmark_frame = pd.DataFrame({"close": [100.0, 101.0, 102.0, 103.0]}, index=benchmark_index)
    equity_curve = [
        {"time": "2026-08-29T23:00:00Z", "value": 10000.0},
        {"time": "2026-08-29T23:45:00Z", "value": 10010.0},
        {"time": "2026-08-29T23:59:00Z", "value": 10020.0},
    ]

    result = _build_benchmark_result(
        InstrumentSpec(market="Crypto", symbol="ETH/USDT", market_type="spot"),
        benchmark_frame,
        equity_curve,
        10000.0,
    )

    assert result["benchmarkStatus"] == "available"
    assert result["benchmarkCoverageRatio"] == pytest.approx(1.0)
    assert len(result["benchmarkCurve"]) == len(equity_curve)
    assert result["benchmarkCurve"][-1]["value"] == pytest.approx(10300.0)
    assert result["benchmarkCoverageEnd"] == "2026-08-29T23:59:59.999999Z"


def test_trade_review_snapshot_uses_the_finest_bounded_timeframe_and_keeps_only_ohlcv():
    index = pd.date_range("2026-07-31", periods=30 * 24 * 60, freq="min")
    frame = pd.DataFrame({
        "open": range(len(index)),
        "high": [value + 2 for value in range(len(index))],
        "low": [value - 2 for value in range(len(index))],
        "close": [value + 1 for value in range(len(index))],
        "volume": [3] * len(index),
        "private_rule": ["must-not-leak"] * len(index),
    }, index=index)
    symbol = "Crypto:ETH/USDT@swap"

    snapshots = _build_review_candle_snapshots(
        {symbol: frame},
        [{
            "symbol": symbol,
            "entry_time": "2026-07-31T00:10:00Z",
            "exit_time": "2026-08-29T23:40:00Z",
        }],
        source_frequency="1m",
        start_date=datetime(2026, 7, 31),
        end_date=datetime(2026, 8, 29, 23, 59),
    )

    snapshot = snapshots[symbol]
    assert snapshot["timeframe"] == "15m"
    assert 2000 < len(snapshot["candles"]) <= 3000
    assert set(snapshot["candles"][0]) == {"time", "open", "high", "low", "close", "volume"}
    assert max(item["volume"] for item in snapshot["candles"]) > 3
    first_time = pd.to_datetime(snapshot["candles"][0]["time"], unit="s", utc=True)
    assert first_time < pd.Timestamp("2026-08-01T00:00:00Z")


def test_trade_review_snapshot_maps_hedged_position_leg_to_market_data_symbol():
    index = pd.date_range("2026-08-20", periods=3 * 24 * 60, freq="min")
    frame = pd.DataFrame({
        "open": [100.0] * len(index),
        "high": [101.0] * len(index),
        "low": [99.0] * len(index),
        "close": [100.5] * len(index),
        "volume": [10.0] * len(index),
    }, index=index)
    symbol = "Crypto:SOL/USDT@swap"

    snapshots = _build_review_candle_snapshots(
        {symbol: frame},
        [{
            "symbol": f"{symbol}::long",
            "entry_time": "2026-08-20T08:01:00Z",
            "exit_time": "2026-08-22T12:34:00Z",
        }],
        source_frequency="1m",
        start_date=datetime(2026, 8, 20),
        end_date=datetime(2026, 8, 22, 23, 59),
    )

    assert set(snapshots) == {symbol}
    assert snapshots[symbol]["candles"]


def test_month_of_minutes_uses_a_complete_coarser_benchmark_frequency():
    timeframe, _rule, _seconds = _review_frequency_for_window(
        "1m",
        30 * 24 * 60 * 60,
        max_bars=3000,
    )

    assert timeframe == "15m"


def test_year_of_hourly_review_uses_four_hours_instead_of_daily_candles():
    timeframe, _rule, _seconds = _review_frequency_for_window(
        "1H",
        365 * 24 * 60 * 60,
        max_bars=3000,
    )

    assert timeframe == "4H"


def test_short_hourly_review_keeps_the_strategy_timeframe():
    timeframe, _rule, _seconds = _review_frequency_for_window(
        "1H",
        90 * 24 * 60 * 60,
        max_bars=3000,
    )

    assert timeframe == "1H"


def _frame(*_args, **_kwargs):
    index = pd.date_range("2026-01-01", periods=5, freq="D")
    return pd.DataFrame({
        "open": [100, 101, 102, 103, 104],
        "high": [101, 102, 103, 104, 105],
        "low": [99, 100, 101, 102, 103],
        "close": [100, 101, 102, 103, 104],
        "volume": [1000] * 5,
    }, index=index)


def test_v2_service_request_needs_only_runtime_parameters(tmp_path):
    code = """
def initialize(context):
    context.set_universe(["USStock:AAPL"])
    context.subscribe(frequency="1d")

def handle_data(context, data):
    if not context.portfolio.positions:
        order_target_percent("AAPL", 1.0)
"""
    repository = _Repository()
    service = StrategyV2BacktestService(
        repository=repository,
        frame_fetcher=_frame,
        snapshot_store=MarketDataSnapshotStore(tmp_path),
    )

    run_id, result = service.run(
        user_id=1,
        code=code,
        start_date=datetime(2026, 1, 1),
        end_date=datetime(2026, 1, 5, 23, 59),
        initial_capital=10000,
        persist=True,
        source_id=104,
    )

    assert run_id == 81
    assert result["manifest"]["universe"]["instruments"][0]["symbol"] == "AAPL"
    assert result["diagnostics"]["sourceControlled"] is True
    assert result["benchmarkStatus"] == "available"
    assert len(result["benchmarkCurve"]) == len(result["equityCurve"])
    assert result["benchmarkRelativeMetrics"]["status"] == "available"
    assert result["benchmarkRelativeMetrics"]["benchmark"] == "USStock:AAPL"
    assert result["benchmarkRelativeMetrics"]["frequency"] == "1d"
    assert result["benchmarkRelativeMetrics"]["annualizationFactor"] == result["periodsPerYear"]
    assert result["benchmarkRelativeMetrics"]["observations"] == len(result["equityCurve"]) - 1
    assert all(point["time"].endswith("Z") for point in result["benchmarkCurve"])
    assert result["dataProvenance"]["kind"] == "market"
    assert result["audit"]["passed"] is True
    assert result["dataProvenance"]["symbols"][0]["snapshotId"]
    assert repository.persisted["initial_capital"] == 10000
    assert repository.persisted["leverage"] == 1.0
    assert repository.persisted["manifest"]["apiVersion"] == 2


def test_v2_service_applies_changed_runtime_params_to_each_run(tmp_path):
    code = """
# @param target_pct float 0.25 Target allocation
def initialize(context):
    context.set_universe(["USStock:AAPL"])
    context.subscribe(frequency="1d")

def handle_data(context, data):
    if not context.portfolio.positions:
        target_pct = float(context.params.get("target_pct", 0.25))
        order_target_percent("AAPL", target_pct)
"""
    service = StrategyV2BacktestService(
        repository=_Repository(),
        frame_fetcher=_frame,
        snapshot_store=MarketDataSnapshotStore(tmp_path),
    )
    common = {
        "user_id": 1,
        "code": code,
        "start_date": datetime(2026, 1, 1),
        "end_date": datetime(2026, 1, 5, 23, 59),
        "initial_capital": 10000,
        "persist": False,
    }

    _, smaller = service.run(**common, params={"target_pct": 0.25})
    _, larger = service.run(**common, params={"target_pct": 0.75})

    assert larger["totalReturn"] > smaller["totalReturn"]
    assert larger["totalExecutions"] == smaller["totalExecutions"] == 1


def test_v2_service_runs_a_multi_symbol_portfolio_and_preserves_symbol_attribution(tmp_path):
    code = """
def initialize(context):
    context.set_universe(["USStock:AAPL", "USStock:MSFT"])
    context.subscribe(frequency="1d")
    run_daily(rebalance, time="09:35")

def rebalance(context, data):
    order_target_percent("AAPL", 0.5)
    order_target_percent("MSFT", 0.5)
"""
    service = StrategyV2BacktestService(
        repository=_Repository(),
        frame_fetcher=_frame,
        snapshot_store=MarketDataSnapshotStore(tmp_path),
    )

    _, result = service.run(
        user_id=1,
        code=code,
        start_date=datetime(2026, 1, 1),
        end_date=datetime(2026, 1, 5, 23, 59),
        initial_capital=10000,
        persist=False,
    )

    assert result["manifest"]["strategyType"] == "portfolio"
    assert result["diagnostics"]["symbolsUsed"] == 2
    assert {row["symbol"] for row in result["attribution"]["symbols"]} == {
        "USStock:AAPL",
        "USStock:MSFT",
    }
    assert result["benchmark"]["symbol"] == "SPY"


def test_dynamic_universe_reference_matches_canonical_universe_code():
    assert _universe_matches(
        {"code": "nasdaq100", "source_ref": "NDX"},
        "INDEX:NASDAQ100",
    )


def test_v2_service_accepts_a_controlled_fundamental_enricher():
    code = """
def initialize(context):
    context.set_universe(["USStock:AAPL"])
    context.subscribe(frequency="1d")

def handle_data(context, data):
    values = get_fundamentals(["ROE"], ["AAPL"])
    if not values.empty:
        order_target_percent("AAPL", 0.5)
"""
    calls = []

    def enrich(frames, members):
        calls.append((list(frames), list(members)))
        return {
            symbol: frame.assign(return_on_equity=0.18)
            for symbol, frame in frames.items()
        }

    service = StrategyV2BacktestService(
        repository=_Repository(),
        frame_fetcher=_frame,
        fundamental_enricher=enrich,
    )
    _, result = service.run(
        user_id=1,
        code=code,
        start_date=datetime(2026, 1, 1),
        end_date=datetime(2026, 1, 5, 23, 59),
        initial_capital=10000,
        persist=False,
    )

    assert calls
    assert result["diagnostics"]["symbolsUsed"] == 1


def test_factor_research_rejects_single_symbol_cta_sources():
    code = """
def initialize(context):
    context.set_universe(["USStock:AAPL"])
    context.subscribe(frequency="1d")

def handle_data(context, data):
    pass
"""
    service = StrategyV2BacktestService(repository=_Repository(), frame_fetcher=_frame)

    with pytest.raises(StrategyV2ContractError, match="factorResearchPortfolioOnly"):
        service.research_factor(
            user_id=1,
            code=code,
            start_date=datetime(2026, 1, 1),
            end_date=datetime(2026, 1, 5, 23, 59),
            factor_id="momentum_20",
            groups=3,
        )


def test_factor_research_rejects_portfolios_smaller_than_group_count():
    code = """
def initialize(context):
    context.set_universe(["USStock:AAPL", "USStock:MSFT"])
    context.subscribe(frequency="1d")

def on_rebalance(context, panel):
    pass
"""
    service = StrategyV2BacktestService(repository=_Repository(), frame_fetcher=_frame)

    with pytest.raises(StrategyV2ContractError, match="factorResearchUniverseTooSmall:3"):
        service.research_factor(
            user_id=1,
            code=code,
            start_date=datetime(2026, 1, 1),
            end_date=datetime(2026, 1, 5, 23, 59),
            factor_id="momentum_20",
            groups=3,
        )
