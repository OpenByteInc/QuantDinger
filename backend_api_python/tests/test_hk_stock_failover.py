import pytest
import requests

from app.data_sources import hk_stock


@pytest.fixture(autouse=True)
def reset(monkeypatch):
    monkeypatch.setattr(hk_stock.HKStockDataSource, "_tencent_blocked_until", 0)
    monkeypatch.setattr(hk_stock, "fetch_twelvedata_klines", lambda **kw: [])


@pytest.mark.parametrize("status", [429, 501, 403])
def test_blocked_tencent_falls_back_and_cools_down_across_symbols(monkeypatch, status):
    calls = []
    def blocked(**kw):
        calls.append(kw)
        response = requests.Response()
        response.status_code = status
        raise requests.HTTPError(response=response)
    rows = [{"time": 1795600000, "open": 10, "high": 11, "low": 9, "close": 10, "volume": 100}]
    monkeypatch.setattr(hk_stock, "fetch_kline", blocked)
    monkeypatch.setattr(hk_stock, "fetch_yfinance_klines", lambda **kw: rows)
    source = hk_stock.HKStockDataSource()
    assert source.get_kline("00700", "1D", 10) == rows
    assert source.get_kline("00005", "1D", 10) == rows
    assert len(calls) == 1


def test_provider_timeout_falls_through_but_is_not_global_symbol_ban(monkeypatch):
    calls = []
    def timeout(**kw):
        calls.append(kw)
        raise requests.Timeout("timed out")
    monkeypatch.setattr(hk_stock, "fetch_kline", timeout)
    monkeypatch.setattr(hk_stock, "fetch_yfinance_klines", lambda **kw: [])
    for symbol in ("00700", "00005"):
        with pytest.raises(requests.Timeout):
            hk_stock.HKStockDataSource().get_kline(symbol, "1D", 10)
    assert len(calls) == 2


def test_primary_failure_can_recover_from_tencent(monkeypatch):
    def failed(**kw):
        raise requests.Timeout()
    monkeypatch.setattr(hk_stock, "fetch_twelvedata_klines", failed)
    monkeypatch.setattr(hk_stock, "fetch_kline", lambda **kw: [["2026-11-25", "10", "11", "12", "9", "100"]])
    monkeypatch.setattr(hk_stock, "fetch_yfinance_klines", lambda **kw: pytest.fail("unexpected fallback"))
    assert hk_stock.HKStockDataSource().get_kline("00700", "1D", 10)
