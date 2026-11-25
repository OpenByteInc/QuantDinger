from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

from app.data_sources.errors import (
    MarketDataFailure,
    MarketDataUnavailableError,
    classify_market_data_failure,
)
from app.services.strategy_runtime.timeframes import (
    LiveFrameWarningState,
    live_ready_universe_symbols,
    load_live_frequency_frames,
)
from app.services.strategy_v2 import StrategyV2LiveSession, compile_strategy_v2
from app.services.strategy_v2.service import StrategyV2BacktestService
from app.utils.strategy_runtime_logs import format_market_data_log, parse_market_data_log


@pytest.mark.parametrize(
    ("detail", "expected"),
    [
        ("HTTP 451 Service unavailable from a restricted location", "region_restricted"),
        ("403 Forbidden: CloudFront is configured to block access from your country", "region_restricted"),
        ("ProxyError: tunnel connection failed", "proxy_failure"),
        ("binance does not have market symbol AVAX/USDT:USDT", "symbol_not_found"),
        ("HTTP 429 too many requests", "rate_limited"),
        ("Incomplete K-line coverage", "incomplete_market_data"),
        ("request timed out", "exchange_unavailable"),
    ],
)
def test_market_data_failure_classification(detail, expected):
    failure = classify_market_data_failure(
        detail,
        exchange_id="binance",
        market_type="spot",
        symbol="AVAX/USDT",
        timeframe="1m",
    )

    assert failure.code == expected
    assert failure.technical_detail == detail
    assert failure.exchange_id == "binance"


def test_market_data_strategy_log_round_trip():
    failure = MarketDataFailure(
        code="region_restricted",
        message="Region unavailable",
        technical_detail="HTTP 451",
        exchange_id="binance",
        market_type="spot",
        symbol="AVAX/USDT",
        timeframe="1m",
        retryable=False,
    )

    parsed = parse_market_data_log(format_market_data_log(failure))

    assert parsed == failure.as_dict()


def test_live_timeframe_loader_raises_structured_failure_when_all_frames_fail():
    failure = classify_market_data_failure(
        "HTTP 451 restricted location",
        exchange_id="binance",
        market_type="spot",
        symbol="AVAX/USDT",
        timeframe="1m",
    )
    service = SimpleNamespace(
        fetch_frequency_frames=lambda *_args: (
            {"1m": {}},
            [{
                "symbol": "Crypto:AVAX/USDT@binance:spot",
                "frequency": "1m",
                "reason": "marketData.region_restricted",
                "market_data_error": failure.as_dict(),
            }],
        )
    )
    manifest = SimpleNamespace(
        warmup_bars=30,
        frequencies=("1m",),
        driving_frequency="1m",
        fundamental_dependencies=(),
    )

    with pytest.raises(MarketDataUnavailableError) as raised:
        load_live_frequency_frames(
            service=service,
            candidates=[{"symbol": "AVAX/USDT"}],
            manifest=manifest,
            end_date=datetime.now(timezone.utc),
        )

    assert raised.value.failure.code == "region_restricted"
    assert raised.value.failure.technical_detail == "HTTP 451 restricted location"


def test_partial_data_warnings_change_only_with_failures_and_reset_on_recovery():
    from unittest.mock import Mock

    skipped = [{"symbol": f"USStock:TEST{i}", "frequency": "1d", "reason": "marketData.no_market_data"}
               for i in range(6)]
    fetch = Mock(return_value=({"1d": {"USStock:AAPL": pd.DataFrame({"close": [100.]})}}, skipped))
    warn = Mock()
    state = LiveFrameWarningState()
    args = dict(
        service=SimpleNamespace(fetch_frequency_frames=fetch), candidates=[],
        manifest=SimpleNamespace(warmup_bars=30, frequencies=("1d",), driving_frequency="1d",
                                 fundamental_dependencies=()),
        end_date=datetime.now(timezone.utc), warn=warn, warning_state=state,
    )
    load_live_frequency_frames(**args)
    skipped.reverse()
    load_live_frequency_frames(**args)
    assert warn.call_count == 1
    # A changed sixth failure matters even though the log previews only five.
    skipped[0]["reason"] = "marketData.rate_limited"
    load_live_frequency_frames(**args)
    assert warn.call_count == 2
    fetch.return_value = ({"1d": {"USStock:AAPL": pd.DataFrame({"close": [100.]})}}, [])
    load_live_frequency_frames(**args)
    fetch.return_value = ({"1d": {"USStock:AAPL": pd.DataFrame({"close": [100.]})}}, skipped)
    load_live_frequency_frames(**args)
    assert warn.call_count == 3
    # Total loss remains an error, never suppressed by the warning cache.
    fetch.return_value = ({"1d": {}}, skipped)
    with pytest.raises(MarketDataUnavailableError):
        load_live_frequency_frames(**args)
    assert LiveFrameWarningState().changed(skipped)


def test_strategy_frame_batch_keeps_symbol_on_structured_fetch_failure():
    failure = classify_market_data_failure(
        "invalid symbol",
        exchange_id="binance",
        market_type="spot",
        symbol="MISSING/USDT",
        timeframe="1m",
    )

    def fail_fetch(*_args, **_kwargs):
        raise MarketDataUnavailableError(failure)

    service = StrategyV2BacktestService(
        repository=object(),
        universe_service=object(),
        frame_fetcher=fail_fetch,
        snapshot_store=object(),
        instrument_rules_provider=object(),
    )
    _frames, skipped = service.fetch_frames(
        [{"key": "Crypto:MISSING/USDT@binance:spot", "market": "Crypto", "symbol": "MISSING/USDT"}],
        "1m",
        datetime.now(timezone.utc),
        datetime.now(timezone.utc),
    )

    assert skipped[0]["symbol"] == "Crypto:MISSING/USDT@binance:spot"
    assert skipped[0]["market_data_error"]["code"] == "symbol_not_found"


def test_live_dynamic_universe_excludes_unloaded_and_underwarmed_members():
    source = """
def initialize(context):
    context.set_universe(index="INDEX:NASDAQ100")
    context.subscribe(frequency="1d")
    context.set_warmup(260)
    g.members = []

def on_rebalance(context, data):
    g.members = list(get_index_stocks("INDEX:NASDAQ100"))
    for symbol in g.members:
        get_history(60, frequency="1d", field="close", security_list=[symbol])
"""
    index = pd.bdate_range(end="2026-10-22", periods=270)
    healthy = pd.DataFrame(
        {
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "volume": 1000.0,
        },
        index=index,
    )
    underwarmed = healthy.tail(1)
    frequency_frames = {
        "1d": {
            "USStock:AAPL": healthy,
            "USStock:EA": underwarmed,
        }
    }
    candidates = [
        {"key": "USStock:AAPL", "market": "USStock", "symbol": "AAPL"},
        {"key": "USStock:EA", "market": "USStock", "symbol": "EA"},
        {"key": "USStock:WRD", "market": "USStock", "symbol": "WRD"},
    ]
    manifest = compile_strategy_v2(source).manifest

    ready, shortfalls = live_ready_universe_symbols(
        frequency_frames,
        manifest,
        datetime(2026, 10, 23, tzinfo=timezone.utc),
        candidates,
    )

    assert ready == {"USStock:AAPL"}
    assert [item["symbol"] for item in shortfalls] == ["USStock:EA"]

    members = [item["key"] for item in candidates]
    session = StrategyV2LiveSession(
        code=source,
        frames=frequency_frames["1d"],
        frequency_frames=frequency_frames,
        initial_capital=10000,
        universe_resolver=lambda _reference, _timestamp: [
            symbol for symbol in members if symbol in ready
        ],
    )
    session.process(frequency_frames["1d"])

    assert session.program.state.members == ["USStock:AAPL"]
