from datetime import datetime, timezone
import json
import threading

from app.data_sources.us_stock import USStockDataSource
from app.services.strategy_runtime import state
from app.services.strategy_runtime.timeframes import (
    completed_bar_token,
    seconds_until_next_completed_bar,
)
from app.services.trading_executor import TradingExecutor
from app.services.trading_executor import _apply_market_data_defaults
from app.services.trading_executor import _latest_frame_timestamp
from app.services.trading_executor import _runtime_wait_seconds
from app.services.trading_executor import _signal_cycle_due


def test_completed_bar_token_changes_only_after_next_bar_boundary():
    first = completed_bar_token(
        "1m", datetime(2026, 8, 31, 12, 0, 5, tzinfo=timezone.utc)
    )
    same = completed_bar_token(
        "1m", datetime(2026, 8, 31, 12, 0, 59, tzinfo=timezone.utc)
    )
    following = completed_bar_token(
        "1m", datetime(2026, 8, 31, 12, 1, 0, tzinfo=timezone.utc)
    )

    assert first == same
    assert following == first + 1


def test_next_completed_bar_delay_targets_the_next_boundary():
    delay = seconds_until_next_completed_bar(
        "1m",
        datetime(2026, 8, 31, 12, 0, 30, tzinfo=timezone.utc),
        grace_seconds=0.25,
    )

    assert delay == 30.25


def test_bar_event_bypasses_poll_throttle_at_boundary():
    assert _signal_cycle_due(
        cycle_started=100.0,
        next_signal_poll=104.0,
        bar_event_triggered=True,
    )
    assert not _signal_cycle_due(
        cycle_started=100.0,
        next_signal_poll=104.0,
        bar_event_triggered=False,
    )


def test_flat_crypto_bar_runtime_uses_idle_wait(monkeypatch):
    monkeypatch.setenv("BAR_IDLE_SCHEDULER_ENABLED", "1")
    monkeypatch.setenv("BAR_IDLE_WAKE_INTERVAL_SEC", "10")
    monkeypatch.setattr(
        "app.services.trading_executor.seconds_until_next_completed_bar",
        lambda _frequency: 30.0,
    )

    wait_seconds = _runtime_wait_seconds(
        risk_tick=1.0,
        frequency="1m",
        candidates=[{"market": "Crypto"}],
        bot_type="",
        positions={},
        order_statuses={},
        protected=set(),
        pending_count=0,
        market_ready=True,
    )

    assert wait_seconds == 10.0


def test_active_runtime_keeps_risk_tick(monkeypatch):
    monkeypatch.setenv("BAR_IDLE_SCHEDULER_ENABLED", "1")
    common = {
        "risk_tick": 1.0,
        "frequency": "1m",
        "candidates": [{"market": "Crypto"}],
        "bot_type": "",
        "protected": set(),
        "pending_count": 0,
        "market_ready": True,
    }

    assert _runtime_wait_seconds(
        **common,
        positions={"BTC": {"amount": 0.01}},
        order_statuses={},
    ) == 1.0
    assert _runtime_wait_seconds(
        **common,
        positions={},
        order_statuses={"order-1": {"status": "submitted"}},
    ) == 1.0


def test_idle_stock_runtime_uses_event_fallback_wake(monkeypatch):
    monkeypatch.setenv("BAR_IDLE_SCHEDULER_ENABLED", "1")
    monkeypatch.setenv("BAR_EVENT_FALLBACK_WAKE_SEC", "300")
    monkeypatch.setenv("STRATEGY_IDLE_HEARTBEAT_SEC", "30")

    wait_seconds = _runtime_wait_seconds(
        risk_tick=1.0,
        frequency="1m",
        candidates=[{"market": "USStock"}],
        bot_type="",
        positions={},
        order_statuses={},
        protected=set(),
        pending_count=0,
        market_ready=True,
        event_driven=True,
    )

    assert wait_seconds == 30.0


def test_event_fallback_can_wake_faster_than_idle_heartbeat(monkeypatch):
    monkeypatch.setenv("BAR_IDLE_SCHEDULER_ENABLED", "1")
    monkeypatch.setenv("BAR_EVENT_FALLBACK_WAKE_SEC", "8")
    monkeypatch.setenv("STRATEGY_IDLE_HEARTBEAT_SEC", "30")

    wait_seconds = _runtime_wait_seconds(
        risk_tick=1.0,
        frequency="1h",
        candidates=[{"market": "Crypto"}],
        bot_type="",
        positions={},
        order_statuses={},
        protected=set(),
        pending_count=0,
        market_ready=True,
        event_driven=True,
    )

    assert wait_seconds == 8.0


def test_event_driven_runtime_with_position_keeps_risk_tick(monkeypatch):
    monkeypatch.setenv("BAR_IDLE_SCHEDULER_ENABLED", "1")

    assert _runtime_wait_seconds(
        risk_tick=1.0,
        frequency="1m",
        candidates=[{"market": "USStock"}],
        bot_type="",
        positions={"MSFT": {"amount": 1}},
        order_statuses={},
        protected=set(),
        pending_count=0,
        market_ready=True,
        event_driven=True,
    ) == 1.0


def test_runtime_liveness_uses_command_state_without_database_poll(monkeypatch):
    executor = TradingExecutor()
    executor.running_strategies[9] = object()
    monkeypatch.setattr(
        "app.services.trading_executor.get_db_connection",
        lambda: (_ for _ in ()).throw(AssertionError("database poll not expected")),
    )

    assert executor._is_strategy_running(9, threading.Event()) is True


def test_runtime_state_coalesces_snapshots_and_flushes_latest(monkeypatch):
    executions = []

    class Cursor:
        def execute(self, _sql, params):
            executions.append(params)

        def close(self):
            pass

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def cursor(self):
            return Cursor()

        def commit(self):
            pass

    clock = iter([100.0, 101.0, 102.0])
    monkeypatch.setattr(state, "get_db_connection", Connection)
    monkeypatch.setattr(state.time, "monotonic", lambda: next(clock))
    store = state.RuntimeStateStore(strategy_id=9, strategy_run_id=2)

    assert store.save({"step": 1}, min_interval_seconds=5) is True
    assert store.save({"step": 2}, min_interval_seconds=5) is False
    assert store.flush() is True

    assert len(executions) == 2
    assert json.loads(executions[-1][-1]) == {"step": 2}


def test_us_stock_quote_batch_deduplicates_and_caches(monkeypatch):
    source = USStockDataSource.__new__(USStockDataSource)
    source.finnhub_client = None
    source.clear_quote_cache()
    calls = []

    def fetch(symbol):
        calls.append(symbol)
        return {"symbol": symbol, "last": 100.0}

    monkeypatch.setattr(source, "_fetch_ticker", fetch)
    monkeypatch.setattr(source, "_fetch_yfinance_batch_quotes", lambda _symbols: {})
    monkeypatch.setenv("US_STOCK_QUOTE_BATCH_WINDOW_MS", "0")
    first = source.get_tickers(["msft", "AAPL", "MSFT"])
    second = source.get_tickers(["AAPL", "MSFT"])

    assert set(first) == {"AAPL", "MSFT"}
    assert set(second) == {"AAPL", "MSFT"}
    assert calls == ["AAPL", "MSFT"]


def test_us_stock_quote_batch_prefers_one_bulk_request(monkeypatch):
    source = USStockDataSource.__new__(USStockDataSource)
    source.finnhub_client = None
    source.clear_quote_cache()
    batches = []

    def fetch_batch(symbols):
        batches.append(symbols)
        return {symbol: {"last": 100.0} for symbol in symbols}

    monkeypatch.setattr(source, "_fetch_yfinance_batch_quotes", fetch_batch)
    monkeypatch.setenv("US_STOCK_QUOTE_BATCH_WINDOW_MS", "0")
    monkeypatch.setattr(
        source,
        "_fetch_ticker",
        lambda _symbol: (_ for _ in ()).throw(AssertionError("fallback not expected")),
    )

    quotes = source.get_tickers(["MSFT", "AAPL", "NVDA"])

    assert batches == [["AAPL", "MSFT", "NVDA"]]
    assert set(quotes) == {"MSFT", "AAPL", "NVDA"}


def test_concurrent_us_stock_quotes_join_one_batch(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    source = USStockDataSource.__new__(USStockDataSource)
    source.finnhub_client = None
    source.clear_quote_cache()
    batches = []

    def fetch_batch(symbols):
        batches.append(symbols)
        return {symbol: {"last": 100.0} for symbol in symbols}

    monkeypatch.setattr(source, "_fetch_yfinance_batch_quotes", fetch_batch)
    monkeypatch.setenv("US_STOCK_QUOTE_BATCH_WINDOW_MS", "50")
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(source.get_ticker, ["MSFT", "AAPL", "NVDA"]))

    assert batches == [["AAPL", "MSFT", "NVDA"]]
    assert all(result["last"] == 100.0 for result in results)


def test_latest_frame_timestamp_detects_candle_advancement():
    import pandas as pd

    first = pd.DataFrame(
        {"close": [1.0]},
        index=pd.DatetimeIndex(["2026-08-31T12:00:00Z"]),
    )
    second = pd.DataFrame(
        {"close": [1.0, 2.0]},
        index=pd.DatetimeIndex([
            "2026-08-31T12:00:00Z",
            "2026-08-31T12:01:00Z",
        ]),
    )

    assert _latest_frame_timestamp(second) > _latest_frame_timestamp(first)


def test_latest_frame_timestamp_reads_strategy_instrument_panel():
    import pandas as pd

    panel = {
        "Crypto:BTC/USDT@swap": pd.DataFrame(
            {"close": [1.0, 2.0]},
            index=pd.DatetimeIndex([
                "2026-08-31T12:00:00Z",
                "2026-08-31T12:01:00Z",
            ]),
        ),
        "Crypto:ETH/USDT@swap": pd.DataFrame(
            {"close": [3.0]},
            index=pd.DatetimeIndex(["2026-08-31T12:02:00Z"]),
        ),
    }

    assert _latest_frame_timestamp(panel) == pd.Timestamp("2026-08-31T12:02:00Z")
    assert _latest_frame_timestamp({}) is None


def test_finnhub_429_blocks_followup_requests_across_symbols(monkeypatch):
    class Client:
        def __init__(self):
            self.calls = []

        def quote(self, symbol):
            self.calls.append(symbol)
            raise RuntimeError("429 rate limit")

    source = USStockDataSource.__new__(USStockDataSource)
    source.finnhub_client = Client()
    source.clear_quote_cache()
    monkeypatch.setenv("FINNHUB_QUOTE_MIN_INTERVAL_SEC", "0")

    assert source._fetch_finnhub_quote("MSFT") == {}
    assert source._fetch_finnhub_quote("AAPL") == {}
    assert source.finnhub_client.calls == ["MSFT"]


def test_live_prices_fetches_each_market_group_as_one_batch(monkeypatch):
    batches = []

    def get_tickers(market, symbols, exchange_id=None, market_type=None):
        batches.append((market, symbols, exchange_id, market_type))
        return {symbol: {"last": index + 100.0} for index, symbol in enumerate(symbols)}

    from app.services import trading_executor

    monkeypatch.setattr(trading_executor.DataSourceFactory, "get_tickers", get_tickers)
    prices = TradingExecutor._live_prices([
        {"key": "USStock:MSFT", "market": "USStock", "symbol": "MSFT"},
        {"key": "USStock:AAPL", "market": "USStock", "symbol": "AAPL"},
    ])

    assert len(batches) == 1
    assert batches[0][1] == ["MSFT", "AAPL"]
    assert prices == {"USStock:MSFT": 100.0, "USStock:AAPL": 101.0}


def test_crypto_market_data_defaults_to_system_exchange_and_preserves_product(monkeypatch):
    from app.services import trading_executor

    batches = []

    def get_tickers(market, symbols, exchange_id=None, market_type=None):
        batches.append((market, symbols, exchange_id, market_type))
        return {symbol: {"last": 84_000.0} for symbol in symbols}

    monkeypatch.setattr(trading_executor, "default_crypto_exchange_id", lambda: "bybit")
    monkeypatch.setattr(trading_executor.DataSourceFactory, "get_tickers", get_tickers)
    candidates = [
        {
            "key": "Crypto:BTC/USDT@swap",
            "market": "Crypto",
            "symbol": "BTC/USDT",
            "exchange_id": "",
            "market_type": "swap",
        },
        {
            "key": "Crypto:ETH/USDT@gate:spot",
            "market": "Crypto",
            "symbol": "ETH/USDT",
            "exchange_id": "gate",
            "market_type": "spot",
        },
        {
            "key": "Crypto:BTC/USDT@bybit:swap",
            "market": "Crypto",
            "symbol": "BTC/USDT",
            "exchange_id": "bybit",
            "market_type": "swap",
        },
        {"key": "USStock:MSFT", "market": "USStock", "symbol": "MSFT"},
    ]

    _apply_market_data_defaults(candidates)
    prices = TradingExecutor._live_prices(candidates)

    assert candidates[0]["exchange_id"] == "bybit"
    assert candidates[0]["market_type"] == "swap"
    assert candidates[0]["key"] == "Crypto:BTC/USDT@bybit:swap"
    assert candidates[1]["key"] == "Crypto:ETH/USDT@gate:spot"
    assert candidates[2] == {
        "key": "USStock:MSFT",
        "market": "USStock",
        "symbol": "MSFT",
    }
    assert len(candidates) == 3
    assert ("Crypto", ["BTC/USDT"], "bybit", "swap") in batches
    assert prices["Crypto:BTC/USDT@bybit:swap"] == 84_000.0


def test_market_data_defaults_repair_legacy_equity_keys_without_losing_metadata():
    candidates = [
        {
            "key": "USStock:DXCM@NASDAQ:spot",
            "market": "USStock",
            "symbol": "DXCM",
            "exchange_id": "NASDAQ",
            "market_type": "spot",
        },
        {
            "key": "HKStock:00700.HK@HKEX:spot",
            "market": "HKStock",
            "symbol": "00700.HK",
            "exchange_id": "HKEX",
            "market_type": "spot",
        },
    ]

    _apply_market_data_defaults(candidates)

    assert [item["key"] for item in candidates] == ["USStock:DXCM", "HKStock:00700.HK"]
    assert candidates[0]["exchange_id"] == "NASDAQ"
    assert candidates[0]["market_type"] == "spot"
