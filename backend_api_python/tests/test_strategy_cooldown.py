import threading
from unittest.mock import Mock

import pytest

from app.data_sources.errors import MarketDataUnavailableError, classify_market_data_failure
from app.services import trading_executor as runtime
from app.services.strategy_runtime.cooldown import exchange_cooldown_seconds
from app.utils.binance_request_guard import BinanceRateLimitError


@pytest.mark.parametrize("error,expected", [
    (BinanceRateLimitError(1084), 1084),
    (MarketDataUnavailableError(classify_market_data_failure("Binance rate limit: retry after 1075s")), 1075),
    ("marketData.rate_limited", 60),
    ("Binance HTTP 418: IP banned", 60),
    ("Binance HTTP 401: invalid api key", 0),
    ("strategyRuntime.startFailed", 0),
    ("strategyV2.hedgeModeUnknown:binance_unknown", 0),
])
def test_cooldown_is_distinct_from_fatal_or_unknown_failures(error, expected):
    assert exchange_cooldown_seconds(error) == expected


@pytest.mark.parametrize("limited", [True, False])
def test_startup_rate_limit_preserves_desired_state_but_fatal_failure_stops(monkeypatch, limited):
    executor = runtime.TradingExecutor(external_bar_triggers=False)
    strategy = {"id": 1, "desired_runtime_epoch": 7}
    monkeypatch.setattr(executor, "_load_strategy", Mock(return_value=strategy))
    error = MarketDataUnavailableError(classify_market_data_failure("Binance rate limit: retry after 1084s")) if limited else RuntimeError("invalid source")
    monkeypatch.setattr(executor, "_load_source", Mock(side_effect=error))
    stopped, deferred = Mock(), Mock()
    monkeypatch.setattr(executor, "_mark_stopped", stopped)
    monkeypatch.setattr(executor, "_defer_exchange_start", deferred)
    monkeypatch.setattr(runtime, "append_strategy_log", Mock())
    try:
        assert list(executor._run_strategy_loop(1, threading.Event())) == []
        if limited:
            stopped.assert_not_called()
            deferred.assert_called_once_with(1, error, strategy)
        else:
            deferred.assert_not_called()
            stopped.assert_called_once_with(1, expected_epoch=7)
    finally:
        executor.close()


def test_binance_position_mode_does_not_disguise_cooldown_as_unknown():
    from app.services.live_trading.binance import BinanceFuturesClient

    client = BinanceFuturesClient.__new__(BinanceFuturesClient)
    client._dual_side_cache = None
    client._signed_request = Mock(side_effect=BinanceRateLimitError(1084))
    with pytest.raises(BinanceRateLimitError):
        client.get_dual_side_position()
    client._signed_request.assert_called_once()


def test_preflight_cooldown_is_deferred_without_scheduling_an_unchecked_runtime(monkeypatch):
    executor = runtime.TradingExecutor(external_bar_triggers=False)
    error = BinanceRateLimitError(1084)
    monkeypatch.setattr(executor, "_preflight_live_strategy", Mock(side_effect=error))
    deferred = Mock()
    monkeypatch.setattr(executor, "_defer_exchange_start", deferred)
    try:
        assert not executor.start_strategy(1)
        assert not executor.running_strategies
        deferred.assert_called_once_with(1, error)
    finally:
        executor.close()


@pytest.mark.parametrize("reason", ["marketData.rate_limited", "Binance HTTP 418: IP banned", "OKX rate limit: retry after 2s"])
def test_cooldown_never_exhausts_auto_stop_threshold(monkeypatch, reason):
    from app.services import strategy_lifecycle

    stopped = Mock()
    monkeypatch.setattr(strategy_lifecycle, "auto_stop_live_strategy", stopped)
    assert not strategy_lifecycle.maybe_auto_stop_on_exchange_error(1, reason, consecutive_failures=100)
    stopped.assert_not_called()
