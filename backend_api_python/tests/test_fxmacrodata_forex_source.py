from app.data_sources.forex import ForexDataSource


def test_fxmacrodata_daily_kline_fetch(monkeypatch):
    captured = {}

    class FakeResponse:
        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {
                "data": [
                    {"date": "2024-01-03", "val": 1.0920},
                    {"date": "2024-01-01", "val": "1.1038"},
                ]
            }

    def fake_get(url, params, timeout):
        captured["url"] = url
        captured["params"] = params
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setenv("FXMACRODATA_API_KEY", "test-key")
    monkeypatch.setattr("app.data_sources.forex.requests.get", fake_get)
    source = ForexDataSource()

    rows = source._get_kline_fxmacrodata("EUR/USD", "1D", 5, before_time=1706745600)

    assert rows == [
        {"time": 1704067200, "open": 1.1038, "high": 1.1038, "low": 1.1038, "close": 1.1038, "volume": 0.0},
        {"time": 1704240000, "open": 1.092, "high": 1.092, "low": 1.092, "close": 1.092, "volume": 0.0},
    ]
    assert captured == {
        "url": "https://fxmacrodata.com/api/v1/forex/eur/usd",
        "params": {
            "start_date": "2024-01-22",
            "end_date": "2024-02-01",
            "api_key": "test-key",
        },
        "timeout": 12,
    }


def test_fxmacrodata_skips_intraday_timeframes():
    source = ForexDataSource()
    assert source._get_kline_fxmacrodata("EURUSD", "1m", 5) == []
