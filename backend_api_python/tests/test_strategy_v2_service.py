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
    strategy_universe_symbol_limit,
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


def test_universe_symbol_limit_is_larger_for_daily_data(monkeypatch):
    monkeypatch.delenv("STRATEGY_V2_MAX_SYMBOLS", raising=False)
    monkeypatch.delenv("STRATEGY_V2_MAX_DAILY_SYMBOLS", raising=False)

    assert strategy_universe_symbol_limit("30m") == 600
    assert strategy_universe_symbol_limit("1d") == 1200
    assert strategy_universe_symbol_limit("1w") == 1200


def test_universe_symbol_limit_honors_capacity_overrides(monkeypatch):
    monkeypatch.setenv("STRATEGY_V2_MAX_SYMBOLS", "700")
    monkeypatch.setenv("STRATEGY_V2_MAX_DAILY_SYMBOLS", "1000")

    assert strategy_universe_symbol_limit("1h") == 700
    assert strategy_universe_symbol_limit("1d") == 1000


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


@pytest.mark.parametrize(
    ("instrument", "expected"),
    [
        (InstrumentSpec(market="USStock", symbol="DXCM", exchange_id="NASDAQ", market_type="spot"), "USStock:DXCM"),
        (InstrumentSpec(market="HKStock", symbol="00700.HK", exchange_id="HKEX", market_type="spot"), "HKStock:00700.HK"),
        (InstrumentSpec(market="CNStock", symbol="600519.SH", exchange_id="SSE", market_type="spot"), "CNStock:600519.SH"),
        (InstrumentSpec(market="Forex", symbol="EUR/USD", exchange_id="broker", market_type="spot"), "Forex:EUR/USD"),
        (InstrumentSpec(market="Futures", symbol="ES", exchange_id="CME", market_type="future"), "Futures:ES"),
        (InstrumentSpec(market="Crypto", symbol="BTC/USDT", exchange_id="okx", market_type="swap"), "Crypto:BTC/USDT@okx:swap"),
    ],
)
def test_instrument_key_keeps_venue_only_for_exchange_routed_crypto(instrument, expected):
    assert instrument.key == expected


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
    progress = []
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
        on_progress=progress.append,
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
    assert [item["phase"] for item in progress] == [
        "preparing",
        "loading_market_data",
        "preparing_simulation",
        "running_simulation",
        "building_report",
        "persisting_result",
        "completed",
    ]
    assert progress[-1]["percent"] == 100


def test_v2_service_calculates_relative_metrics_at_native_benchmark_frequency(monkeypatch, tmp_path):
    minute_index = pd.date_range("2026-01-05T14:30:00", periods=61, freq="min")
    benchmark_index = minute_index[::15]

    def frame_fetcher(_market, symbol, timeframe, *_args, **_kwargs):
        if symbol == "SPY":
            assert timeframe == "15m"
            return pd.DataFrame({"close": [100.0, 101.0, 100.5, 102.0, 101.0]}, index=benchmark_index)
        assert timeframe == "1m"
        return pd.DataFrame({
            "open": [100.0] * len(minute_index),
            "high": [100.0] * len(minute_index),
            "low": [100.0] * len(minute_index),
            "close": [100.0] * len(minute_index),
            "volume": [1000.0] * len(minute_index),
        }, index=minute_index)

    monkeypatch.setattr(
        "app.services.strategy_v2.service._review_frequency_for_window",
        lambda *_args, **_kwargs: ("15m", "15min", 900),
    )
    code = """
def initialize(context):
    context.set_universe(["USStock:AAPL"])
    context.subscribe(frequency="1m")
    context.set_benchmark("USStock:SPY")

def handle_data(context, data):
    pass
"""
    service = StrategyV2BacktestService(
        repository=_Repository(),
        frame_fetcher=frame_fetcher,
        snapshot_store=MarketDataSnapshotStore(tmp_path),
    )

    _, result = service.run(
        user_id=1,
        code=code,
        start_date=datetime(2026, 1, 5, 14, 30),
        end_date=datetime(2026, 1, 5, 15, 30),
        initial_capital=10000,
        persist=False,
    )

    metrics = result["benchmarkRelativeMetrics"]
    assert metrics["status"] == "available"
    assert metrics["frequency"] == "15m"
    assert metrics["annualizationFactor"] == pytest.approx(252 * 390 / 15)
    assert metrics["observations"] == 4


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


def test_dynamic_universe_excludes_members_without_loaded_market_data(tmp_path):
    class DynamicUniverse:
        @staticmethod
        def list_universes(_user_id):
            return [{"id": 7, "code": "sp500", "source_ref": "SP500"}]

        @staticmethod
        def candidate_members(_user_id, _universe_id, *, start, end):
            del start, end
            return [
                {"market": "USStock", "symbol": "AAPL"},
                {"market": "USStock", "symbol": "EA"},
            ]

        @staticmethod
        def resolve_members(_user_id, _universe_id, *, as_of):
            del as_of
            return [
                {"market": "USStock", "symbol": "AAPL"},
                {"market": "USStock", "symbol": "EA"},
            ]

    def frame_fetcher(_market, symbol, *_args, **_kwargs):
        return pd.DataFrame() if symbol == "EA" else _frame()

    code = """
def initialize(context):
    context.set_universe(pool="sp500")
    context.subscribe(frequency="1d")
    run_daily(rebalance, time="09:35")

def rebalance(context, data):
    for symbol in get_universe_stocks():
        get_history(2, "1d", "close", symbol)
"""
    service = StrategyV2BacktestService(
        repository=_Repository(),
        universe_service=DynamicUniverse(),
        frame_fetcher=frame_fetcher,
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

    assert result["diagnostics"]["symbolsUsed"] == 1
    assert result["diagnostics"]["symbolsSkipped"] == [
        {
            "symbol": "USStock:EA",
            "frequency": "1d",
            "reason": "strategyV2.noMarketData",
        }
    ]


def test_dynamic_universe_excludes_members_without_required_warmup(tmp_path):
    class DynamicUniverse:
        @staticmethod
        def list_universes(_user_id):
            return [{"id": 7, "code": "nasdaq100", "source_ref": "NDX"}]

        @staticmethod
        def candidate_members(_user_id, _universe_id, *, start, end):
            del start, end
            return [
                {"market": "USStock", "symbol": "AAPL"},
                {"market": "USStock", "symbol": "EA"},
            ]

        @staticmethod
        def resolve_members(_user_id, _universe_id, *, as_of):
            del as_of
            return [
                {"market": "USStock", "symbol": "AAPL"},
                {"market": "USStock", "symbol": "EA"},
            ]

    start_date = datetime(2026, 1, 5)

    def frame_fetcher(_market, symbol, *_args, **_kwargs):
        index = (
            pd.bdate_range(end="2026-01-09", periods=275)
            if symbol == "AAPL"
            else pd.DatetimeIndex(["2026-01-02"])
        )
        return pd.DataFrame(
            {
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "volume": 1000.0,
            },
            index=index,
        )

    code = """
def initialize(context):
    context.set_universe(index="INDEX:NASDAQ100")
    context.subscribe(frequency="1d")
    context.set_warmup(260)

def on_rebalance(context, data):
    pass
"""
    service = StrategyV2BacktestService(
        repository=_Repository(),
        universe_service=DynamicUniverse(),
        frame_fetcher=frame_fetcher,
        snapshot_store=MarketDataSnapshotStore(tmp_path),
    )

    _, result = service.run(
        user_id=1,
        code=code,
        start_date=start_date,
        end_date=datetime(2026, 1, 9, 23, 59),
        initial_capital=10000,
        persist=False,
    )

    assert result["diagnostics"]["symbolsUsed"] == 1
    assert result["diagnostics"]["symbolsSkipped"] == [{
        "symbol": "USStock:EA",
        "frequency": "1d",
        "reason": "strategyV2.insufficientWarmupData",
        "availableBars": 1,
        "requiredBars": 260,
    }]


def test_dynamic_universe_excludes_members_without_required_fundamentals(tmp_path):
    class DynamicUniverse:
        @staticmethod
        def list_universes(_user_id):
            return [{"id": 7, "code": "sp500", "source_ref": "SP500"}]

        @staticmethod
        def candidate_members(_user_id, _universe_id, *, start, end):
            del start, end
            return [
                {"market": "USStock", "symbol": "AAPL"},
                {"market": "USStock", "symbol": "BE"},
            ]

        @staticmethod
        def resolve_members(_user_id, _universe_id, *, as_of):
            del as_of
            return [
                {"market": "USStock", "symbol": "AAPL"},
                {"market": "USStock", "symbol": "BE"},
            ]

    def enrich(frames, _members):
        return {
            symbol: frame.assign(
                market_cap=1_000_000_000.0 if symbol == "USStock:AAPL" else None
            )
            for symbol, frame in frames.items()
        }

    code = """
def initialize(context):
    context.set_universe(pool="sp500")
    context.subscribe(frequency="1d")

def on_rebalance(context, data):
    members = list(get_universe_stocks())
    get_fundamentals(["market_cap"], members)
"""
    service = StrategyV2BacktestService(
        repository=_Repository(),
        universe_service=DynamicUniverse(),
        frame_fetcher=_frame,
        fundamental_enricher=enrich,
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

    assert result["diagnostics"]["symbolsUsed"] == 1
    assert result["diagnostics"]["symbolsSkipped"] == [{
        "symbol": "USStock:BE",
        "reason": "strategyV2.fundamentalDataMissing",
        "missingFields": ["market_cap"],
    }]


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


def test_universe_factor_research_does_not_compile_or_execute_strategy_code():
    symbols = ["A", "B", "C", "D", "E", "F"]

    class ResearchUniverse:
        @staticmethod
        def get_universe(_user_id, universe_id):
            assert universe_id == 17
            return {
                "id": 17,
                "code": "research-pool",
                "name": "Research Pool",
                "market": "USStock",
                "member_count": len(symbols),
                "history_from": "2020-01-01",
            }

        @staticmethod
        def candidate_members(_user_id, universe_id, *, start, end):
            assert universe_id == 17
            assert start <= end
            return [{"market": "USStock", "symbol": symbol} for symbol in symbols]

    index = pd.bdate_range("2025-01-01", periods=120)

    def frame_fetcher(_market, symbol, *_args, **_kwargs):
        offset = symbols.index(symbol)
        prices = [100 + offset + day * (0.15 + offset * 0.03) for day in range(len(index))]
        return pd.DataFrame({"open": prices, "close": prices}, index=index)

    service = StrategyV2BacktestService(
        repository=_Repository(),
        universe_service=ResearchUniverse(),
        frame_fetcher=frame_fetcher,
    )
    result = service.research_universe_factor(
        user_id=1,
        universe_id=17,
        frequency="1d",
        start_date=datetime(2025, 2, 10),
        end_date=datetime(2025, 5, 30, 23, 59),
        factor_id="momentum_20",
        groups=3,
        holding_period=5,
    )

    assert result["researchMode"] == "universe"
    assert result["universeId"] == 17
    assert result["frequency"] == "1d"
    assert result["symbolsRequested"] == len(symbols)
    assert result["manifest"]["strategyType"] == "factor_research"


def test_factor_benchmark_reuses_one_market_panel_for_multiple_factors():
    symbols = ["A", "B", "C", "D", "E", "F"]
    fetches = []

    class ResearchUniverse:
        @staticmethod
        def get_universe(_user_id, universe_id):
            return {
                "id": universe_id,
                "code": "research-pool",
                "name": "Research Pool",
                "market": "USStock",
                "member_count": len(symbols),
                "history_from": "2020-01-01",
            }

        @staticmethod
        def candidate_members(_user_id, _universe_id, *, start, end):
            assert start <= end
            return [{"market": "USStock", "symbol": symbol} for symbol in symbols]

    index = pd.bdate_range("2025-01-01", periods=180)

    def frame_fetcher(_market, symbol, *_args, **_kwargs):
        fetches.append(symbol)
        offset = symbols.index(symbol)
        prices = [
            100 + offset * 2 + day * (0.08 + offset * 0.025) + (day % 7) * offset * 0.02
            for day in range(len(index))
        ]
        return pd.DataFrame({"open": prices, "close": prices}, index=index)

    service = StrategyV2BacktestService(
        repository=_Repository(),
        universe_service=ResearchUniverse(),
        frame_fetcher=frame_fetcher,
    )
    result = service.benchmark_universe_factors(
        user_id=1,
        universe_id=17,
        frequency="1d",
        start_date=datetime(2025, 3, 1),
        end_date=datetime(2025, 8, 29, 23, 59),
        factors=[
            {"factorId": "momentum_20"},
            {"factorId": "reversal_5"},
        ],
        groups=3,
        holding_period=5,
        minimum_oos_observations=3,
    )

    assert sorted(fetches) == symbols
    assert result["researchMode"] == "benchmark"
    assert result["summary"]["factorCount"] == 2
    assert result["summary"]["requestedFactorCount"] == 2
    assert result["summary"]["failedFactorCount"] == 0
    assert len(result["rankings"]) == 2
    assert result["correlation"]["factors"] == ["momentum_20", "reversal_5"]
    assert result["errors"] == []


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


def test_factor_research_loads_selected_fundamental_without_strategy_dependency():
    symbols = ["A", "B", "C", "D", "E", "F"]
    code = f"""
def initialize(context):
    context.set_universe({[f'USStock:{symbol}' for symbol in symbols]!r})
    context.subscribe(frequency="1d")

def on_rebalance(context, panel):
    pass
"""
    index = pd.date_range("2025-01-01", periods=100, freq="B")
    calls = []

    def frame_fetcher(_market, symbol, *_args, **_kwargs):
        offset = symbols.index(symbol)
        prices = [100 + offset + day * (0.1 + offset * 0.02) for day in range(len(index))]
        return pd.DataFrame({"open": prices, "close": prices}, index=index)

    def enrich(frames, members):
        calls.append((list(frames), list(members)))
        return {
            key: frame.assign(pe_ratio=10.0 + symbols.index(key.split(":", 1)[1]))
            for key, frame in frames.items()
        }

    service = StrategyV2BacktestService(
        repository=_Repository(),
        frame_fetcher=frame_fetcher,
        fundamental_enricher=enrich,
    )
    result = service.research_factor(
        user_id=1,
        code=code,
        start_date=datetime(2025, 2, 10),
        end_date=datetime(2025, 4, 30, 23, 59),
        factor_id="value",
        groups=3,
        holding_period=5,
    )

    assert calls
    assert result["factorId"] == "value"
    assert result["icSeries"]
