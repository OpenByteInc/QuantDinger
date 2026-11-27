from datetime import timedelta

import pandas as pd
import pytest

from app.data_sources.errors import MarketDataUnavailableError
from app.services.strategy_v2 import market_data as md
from app.services.strategy_runtime.candle_readiness import frame_covers_bar


@pytest.fixture(autouse=True)
def cache(monkeypatch):
    md.clear_shared_strategy_frame_cache()
    cached = {}
    monkeypatch.setattr(md._cache, "get", cached.get)
    monkeypatch.setattr(md._cache, "put", lambda key, value, *args: cached.update({key: value}))
    yield cached
    md.clear_shared_strategy_frame_cache()


def prices(end, periods=260):
    return pd.DataFrame(
        {"open": 100., "high": 101., "low": 99., "close": 100., "volume": 10.},
        index=pd.date_range(end=end, periods=periods, freq="B"),
    )


@pytest.mark.parametrize("market", ["HKStock", "USStock"])
def test_live_warmup_reuses_newly_listed_history_across_startup_clocks(monkeypatch, cache, market):
    cutoff = pd.Timestamp("2026-11-25T15:59:59")
    monkeypatch.setattr(md, "_market_bar_cutoff", lambda *a, **kw: cutoff)
    frame = prices("2026-11-25T01:30")
    rows = frame.assign(time=[int(stamp.timestamp()) for stamp in frame.index]).to_dict("records")
    calls = []
    monkeypatch.setattr(md.DataSourceFactory, "get_kline_with_diagnostics",
                        lambda **kw: (calls.append(kw) or rows, None))
    monkeypatch.setattr(md.DataSourceFactory, "get_kline", lambda **kw: calls.append(kw) or rows)
    end = pd.Timestamp("2026-11-27T14:35:00Z").to_pydatetime()
    start = end - timedelta(days=780)
    first = md.load_strategy_frame(market, "TEST", "1d", start, end, required_warmup_bars=260)
    assert len(first) == 260
    # A different worker has only the Redis cache, and a different startup clock.
    md.clear_shared_strategy_frame_cache()
    again = md.load_strategy_frame(market, "TEST", "1d", start + timedelta(minutes=2),
                                   end + timedelta(minutes=2), required_warmup_bars=260)
    pd.testing.assert_frame_equal(first, again)
    assert len(calls) == 1
    # A backtest requesting the full interval must not inherit live warmup coverage.
    with pytest.raises(MarketDataUnavailableError):
        md.load_strategy_frame(market, "TEST", "1d", start, end)
    assert len(calls) == 2


@pytest.mark.parametrize("damage", ["short", "duplicates", "nan", "zero", "infinite", "old"])
def test_warmup_cannot_count_invalid_duplicate_or_stale_history(damage):
    end = pd.Timestamp("2026-11-25")
    frame = prices(end)
    if damage == "short":
        frame = frame.iloc[1:]
    elif damage == "duplicates":
        frame = pd.concat([frame.iloc[1:], frame.iloc[[-1]]])
    elif damage in {"nan", "zero", "infinite"}:
        frame.iloc[0, 0] = {"nan": float("nan"), "zero": 0, "infinite": float("inf")}[damage]
    elif damage == "old":
        frame.index -= pd.Timedelta(days=30)
    assert not md._covers_equity_window(frame, end - pd.Timedelta(days=780), end, 86400,
                                       required_warmup_bars=260)


def test_yesterdays_data_does_not_become_current_with_enough_warmup():
    frame = prices("2026-11-24T14:30")
    assert not frame_covers_bar(frame, {"market": "USStock"}, "1d", pd.Timestamp("2026-11-25T14:30"))


def test_large_stock_pool_fits_count_limit_but_obeys_byte_budget(monkeypatch):
    frame = prices("2026-11-25T14:30")
    cutoff = frame.index[-1]
    monkeypatch.setattr(md, "_market_bar_cutoff", lambda *a, **kw: cutoff)
    calls = []
    monkeypatch.setattr(md, "_load_strategy_frame_uncached", lambda *a, **kw: calls.append(a) or frame)
    for _ in range(2):
        for symbol in range(697):
            md.load_strategy_frame("USStock", str(symbol), "1d", frame.index[0], cutoff)
    assert len(calls) == 697
    monkeypatch.setattr(md, "_SHARED_FRAME_CACHE_MAX_BYTES", md._frame_bytes(frame) * 2)
    md._evict_shared_frame_if_needed()
    assert len(md._shared_frames) == 2
    assert md._shared_frames_bytes <= md._SHARED_FRAME_CACHE_MAX_BYTES


@pytest.mark.parametrize("market,frequency,end", [
    ("Crypto", "1d", "2026-11-27"),
    ("USStock", "1h", "2026-11-27"),
    ("USStock", "1d", "2026-01-01"),
])
def test_warmup_policy_does_not_change_crypto_intraday_or_backtests(monkeypatch, market, frequency, end):
    monkeypatch.setattr(md, "_market_bar_cutoff", lambda *a, **kw: pd.Timestamp("2026-11-27"))
    received = []
    monkeypatch.setattr(md, "_load_strategy_frame_uncached", lambda *a, **kw: received.append(kw) or pd.DataFrame())
    md.load_strategy_frame(market, "TEST", frequency, pd.Timestamp(end) - pd.Timedelta(days=780),
                           pd.Timestamp(end), required_warmup_bars=260)
    assert "required_warmup_bars" not in received[0]
