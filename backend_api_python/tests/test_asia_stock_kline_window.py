from datetime import datetime, timezone
import sys
import types

import pandas as pd

from app.data_sources import asia_stock_kline


def _timestamp(value: str) -> int:
    return int(datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp())


def test_yfinance_uses_requested_historical_window(monkeypatch):
    captured = {}

    class FakeTicker:
        def history(self, **kwargs):
            captured.update(kwargs)
            return pd.DataFrame()

    module = types.ModuleType("yfinance")
    module.Ticker = lambda _symbol: FakeTicker()
    monkeypatch.setitem(sys.modules, "yfinance", module)

    asia_stock_kline.fetch_yfinance_klines(
        is_hk=True,
        tencent_code="HK03690",
        timeframe="1H",
        limit=10_000,
        before_time=_timestamp("2026-11-03T00:00:00"),
        after_time=_timestamp("2026-05-18T00:00:00"),
    )

    assert captured["start"] == "2026-05-18"
    assert captured["end"] == "2026-11-04"
    assert captured["interval"] == "1h"


def test_akshare_window_uses_requested_historical_start():
    start, end = asia_stock_kline._min_bar_window(
        "1H",
        10_000,
        _timestamp("2026-11-03T00:00:00"),
        _timestamp("2026-05-18T00:00:00"),
    )

    assert start.startswith("2026-05-18 ")
    assert end.startswith("2026-11-03 ")
