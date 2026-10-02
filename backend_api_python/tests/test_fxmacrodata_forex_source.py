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

    def fake_get(url, params, headers, timeout):
        captured["url"] = url
        captured["params"] = params
        captured["headers"] = headers
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
        "url": "https://api.fxmacrodata.com/v1/forex/eur/usd",
        "params": {
            "start_date": "2024-01-22",
            "end_date": "2024-02-01",
            "limit": 100,
            "offset": 0,
        },
        "headers": {"X-API-Key": "test-key"},
        "timeout": 12,
    }


def test_fxmacrodata_daily_kline_follows_pagination(monkeypatch):
    pages = {
        0: {
            "data": [{"date": "2024-01-03", "val": 1.3}, {"date": "2024-01-02", "val": 1.2}],
            "pagination": {"has_more": True, "next_offset": 2},
        },
        2: {
            "data": [{"date": "2024-01-01", "val": 1.1}],
            "pagination": {"has_more": False, "next_offset": None},
        },
    }
    offsets = []

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    def fake_get(url, params, headers, timeout):
        offsets.append(params["offset"])
        return FakeResponse(pages[params["offset"]])

    monkeypatch.setattr("app.data_sources.forex.requests.get", fake_get)
    rows = ForexDataSource()._get_kline_fxmacrodata("EURUSD", "1D", 5, before_time=1704326400)

    assert offsets == [0, 2]
    assert [row["close"] for row in rows] == [1.1, 1.2, 1.3]


def test_fxmacrodata_skips_intraday_timeframes():
    source = ForexDataSource()
    assert source._get_kline_fxmacrodata("EURUSD", "1m", 5) == []
