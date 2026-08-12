from datetime import datetime

import pandas as pd
import pytest

from app.services.strategy_v2 import StrategyV2ContractError
from app.services.strategy_v2.service import (
    StrategyV2BacktestService,
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
    assert all(point["time"].endswith("Z") for point in result["benchmarkCurve"])
    assert result["dataProvenance"]["kind"] == "market"
    assert result["audit"]["passed"] is True
    assert result["dataProvenance"]["symbols"][0]["snapshotId"]
    assert repository.persisted["initial_capital"] == 10000
    assert repository.persisted["leverage"] == 1.0
    assert repository.persisted["manifest"]["apiVersion"] == 2


def test_dynamic_universe_reference_matches_canonical_universe_code():
    assert _universe_matches(
        {"code": "nasdaq100", "source_ref": "NDX"},
        "INDEX:NASDAQ100",
    )


def test_fetch_frames_forwards_futu_config_in_strict_mode():
    captured = {}

    def frame_fetcher(*_args, **kwargs):
        captured.update(kwargs)
        return _frame()

    config = {"exchange_id": "futu", "futu_host": "10.0.0.8"}
    service = StrategyV2BacktestService(
        repository=_Repository(),
        frame_fetcher=frame_fetcher,
    )
    frames, skipped = service.fetch_frames(
        [{
            "key": "HKStock:00700.HK",
            "market": "HKStock",
            "symbol": "00700.HK",
            "market_type": "spot",
            "exchange_id": "futu",
        }],
        "1d",
        datetime(2026, 1, 1),
        datetime(2026, 1, 5),
        exchange_config=config,
        strict_data_source=True,
    )

    assert frames
    assert not skipped
    assert captured["exchange_config"] is config
    assert captured["strict_data_source"] is True


def test_fetch_frames_fails_whole_futu_batch_in_strict_mode():
    def failing_fetcher(*_args, **_kwargs):
        raise RuntimeError("FUTU_OPEND_UNREACHABLE")

    service = StrategyV2BacktestService(
        repository=_Repository(),
        frame_fetcher=failing_fetcher,
    )

    with pytest.raises(RuntimeError, match="executionMarketDataUnavailable"):
        service.fetch_frames(
            [{
                "key": "HKStock:00700.HK",
                "market": "HKStock",
                "symbol": "00700.HK",
                "market_type": "spot",
                "exchange_id": "futu",
            }],
            "1d",
            datetime(2026, 1, 1),
            datetime(2026, 1, 5),
            exchange_config={"exchange_id": "futu"},
            strict_data_source=True,
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
